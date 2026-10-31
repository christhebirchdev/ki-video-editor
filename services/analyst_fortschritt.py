# services/analyst_fortschritt.py
"""Gamification (Spec V4, Abschnitt 5) — Stand 2026-11-01 (Vorgabe Chris).

Zwei verschiedene Anzeigen:
- **Kategorien** (Hook, Mittelteil, Editing, Auftreten): KEIN Level, sondern der Schnitt-Score
  (1–5) der letzten FENSTER Videos — eine ehrliche Einschaetzung, wie gut dieser Bereich gerade
  ist. Grundlage sind die Einzelwerte der Dimensionen, nicht der Umsetzungs-Score (der haengt am
  Ziel und ist zwischen Videos nicht vergleichbar).
- **Gesamtlevel** (1–10): steigt auf zwei Wegen — durch Nutzung (Anzahl Analysen) ODER durch
  Koennen (Gesamtschnitt der letzten FENSTER Videos). Der hoehere Weg zaehlt.

Warum die letzten FENSTER und nicht alle Videos (Chris, 2026-11-01): Ueber alle Videos bewegt sich
der Wert mit jeder Analyse weniger, und die schwachen ersten Videos ziehen ihn fuer immer runter.
Warum gleitend statt Einzelwert: Die Modellbewertung schwankt von Lauf zu Lauf.
Warum das Gesamtlevel nie sinkt: Ein Abstieg nach einem schwachen Video bestraft genau die, die
dranbleiben. Der Trend je Kategorie steht getrennt daneben.

Schwellen kalibriert an 98 Altlaeufen (Einzellauf je Kategorie: Median 3,3–4,0, p90 4,0–5,0).
"""
import json
from pathlib import Path

from models.analyst import KATEGORIEN, AnalystEvaluationV2

FENSTER = 5
MAX_LEVEL = 10
# Weg 1 — Nutzung: so viele Analysen braucht Level k. Dicht am Anfang, weit am Ende.
ANALYSEN_JE_LEVEL = {1: 1, 2: 3, 3: 5, 4: 8, 5: 12, 6: 17, 7: 23, 8: 30, 9: 40, 10: 50}
# Level 3 gibt es alternativ fuer die erste Neubewertung — sie ist der Lern-Kreislauf.
NEUBEWERTUNG_LEVEL = 3
# Weg 2 — Koennen: Mindest-Gesamtschnitt fuer Level k. Erst ab KOENNEN_AB_ANALYSEN, sonst entschiede
# ein einziges starkes Video ueber ein hohes Level.
SCHWELLEN = {4: 3.0, 5: 3.3, 6: 3.6, 7: 3.9, 8: 4.2, 9: 4.5, 10: 4.8}
KOENNEN_AB_ANALYSEN = 3
# Wo jede Stufe auf dem Balken sitzt (Prozent). Dicht am Anfang, weit am Ende.
POSITION = {0: 0, 1: 5, 2: 12, 3: 20, 4: 28, 5: 36, 6: 48, 7: 60, 8: 73, 9: 86, 10: 100}
FOKUS_SCORE = 3          # „3 oder schlechter" — dieselbe Grenze wie SCHWACH_SCORE in analyst_eval
TREND_SCHWELLE = 0.15


def _kategorie_wert(scores: dict, dims) -> float | None:
    werte = [scores[d] for d in dims if isinstance(scores.get(d), (int, float)) and 1 <= scores[d] <= 5]
    return sum(werte) / len(werte) if werte else None


def _schnitt(laeufe: list[dict], dims) -> float | None:
    werte = [w for w in (_kategorie_wert(l["scores"], dims) for l in laeufe[-FENSTER:]) if w is not None]
    return sum(werte) / len(werte) if werte else None


def _gesamtschnitt(laeufe: list[dict]) -> float | None:
    werte = [s for s in (_schnitt(laeufe, dims) for dims in KATEGORIEN.values()) if s is not None]
    return sum(werte) / len(werte) if werte else None


def _nutzungs_level(n: int, neu: bool) -> int:
    level = max([k for k, a in ANALYSEN_JE_LEVEL.items() if n >= a], default=0)
    return max(level, NEUBEWERTUNG_LEVEL) if neu else level


def _koennens_level(n: int, schnitt: float | None) -> int:
    if n < KOENNEN_AB_ANALYSEN or schnitt is None:
        return 0
    return max([k for k, s in SCHWELLEN.items() if schnitt >= s - 1e-9], default=0)


def _trend(laeufe: list[dict], dims) -> str:
    if len(laeufe) <= FENSTER:
        return ""
    jetzt, davor = _schnitt(laeufe, dims), _schnitt(laeufe[:-FENSTER], dims)
    if jetzt is None or davor is None:
        return ""
    if jetzt - davor > TREND_SCHWELLE:
        return "hoch"
    if davor - jetzt > TREND_SCHWELLE:
        return "runter"
    return "gleich"


def _fokus(laeufe: list[dict]) -> dict | None:
    """Die Dimension, die in den letzten FENSTER Laeufen am haeufigsten schwach war."""
    letzte = laeufe[-FENSTER:]
    zaehler: dict[str, list[float]] = {}
    for l in letzte:
        for d, s in l["scores"].items():
            if isinstance(s, (int, float)) and 1 <= s <= FOKUS_SCORE:
                zaehler.setdefault(d, []).append(s)
    if not zaehler:
        return None
    d = max(zaehler, key=lambda k: (len(zaehler[k]), -sum(zaehler[k]) / len(zaehler[k])))
    return {"dimension": d, "anzahl": len(zaehler[d]), "von": len(letzte)}


def _anteil(level: int, n: int, schnitt: float | None) -> float:
    """Weg zum naechsten Level (0..1) — der weitere der beiden Wege zaehlt."""
    if level >= MAX_LEVEL:
        return 0.0
    vorher = ANALYSEN_JE_LEVEL.get(level, 0)
    nutzung = (n - vorher) / (ANALYSEN_JE_LEVEL[level + 1] - vorher)
    koennen = 0.0
    if level + 1 in SCHWELLEN and schnitt is not None and n >= KOENNEN_AB_ANALYSEN:
        unten = SCHWELLEN.get(level, SCHWELLEN[min(SCHWELLEN)] - 0.5)
        koennen = (schnitt - unten) / (SCHWELLEN[level + 1] - unten)
    return max(0.0, min(1.0, max(nutzung, koennen)))


def berechne_aus(laeufe: list[dict]) -> dict:
    """`laeufe`: [{created_at, scores: {dimension: 1–5}, neubewertung: bool, aus_cache: bool}]."""
    gezaehlt = sorted((l for l in laeufe if not l.get("aus_cache")), key=lambda l: l.get("created_at", ""))
    level, neu = 0, False
    for i, l in enumerate(gezaehlt):
        neu = neu or bool(l.get("neubewertung"))
        stand = gezaehlt[:i + 1]
        level = max(level, _nutzungs_level(i + 1, neu), _koennens_level(i + 1, _gesamtschnitt(stand)))
    n = len(gezaehlt)
    gesamt_schnitt = _gesamtschnitt(gezaehlt)
    position = POSITION[level]
    if level < MAX_LEVEL:
        position += _anteil(level, n, gesamt_schnitt) * (POSITION[level + 1] - POSITION[level])
    naechstes = None if level >= MAX_LEVEL else {
        "level": level + 1,
        "analysen": ANALYSEN_JE_LEVEL[level + 1],
        "schnitt": SCHWELLEN.get(level + 1),
    }
    kategorien = []
    for key, dims in KATEGORIEN.items():
        s = _schnitt(gezaehlt, dims)
        kategorien.append({
            "key": key,
            "schnitt": round(s, 2) if s is not None else None,
            "position": round(s / 5 * 100, 1) if s is not None else 0.0,
            "trend": _trend(gezaehlt, dims),
        })
    return {
        "gesamt": {"level": level, "position": round(position, 1), "naechstes": naechstes,
                   "schnitt": round(gesamt_schnitt, 2) if gesamt_schnitt is not None else None},
        "kategorien": kategorien,
        "analysen": n,
        "neubewertungen": sum(1 for l in gezaehlt if l.get("neubewertung")),
        "fokus": _fokus(gezaehlt),
        "stufen": [POSITION[k] for k in range(MAX_LEVEL + 1)],
        "max_level": MAX_LEVEL,
        "fenster": FENSTER,
    }


def berechne(run_dirs: list[Path]) -> dict:
    """Liest die Laufverzeichnisse eines Nutzers. Unfertige oder kaputte Laeufe zaehlen nicht."""
    from services.analyst_eval import dimensions_scores
    laeufe = []
    for d in run_dirs:
        try:
            meta = json.loads((d / "meta.json").read_text())
            ev = json.loads((d / "analysis.json").read_text()).get("evaluation")
            if not ev:
                continue
            scores = dimensions_scores(AnalystEvaluationV2(**ev))
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            continue
        laeufe.append({"created_at": meta.get("created_at", ""), "scores": scores,
                       "neubewertung": bool(meta.get("vorgaenger")),
                       "aus_cache": bool(meta.get("cached_from"))})
    return berechne_aus(laeufe)
