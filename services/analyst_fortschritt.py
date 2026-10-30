# services/analyst_fortschritt.py
"""Gamification (Spec V4, Abschnitt 5): Level je Kategorie und Gesamtlevel.

Grundlage sind die Einzelwerte der Dimensionen (1–5), gemittelt je Kategorie — NICHT der
Umsetzungs-Score: Der haengt am gewaehlten Ziel und ist zwischen zwei Videos nicht vergleichbar.

Drei Entscheidungen (Chris, 2026-10-30) und ihr Grund:
- Viele Level am Anfang, wenige am Ende (POSITION): mehr erreichbare Meilensteine, solange die
  Motivation am wackeligsten ist. Level 1–3 haengen an Nutzung, nicht an Koennen — sie sind fuer
  jeden Neuling sicher erreichbar.
- Gleitender Schnitt ueber die letzten FENSTER Laeufe: Die Modellbewertung schwankt von Lauf zu
  Lauf (deshalb gibt es den Analyse-Cache). Ein Level aus einem Einzelwert wuerde zufaellig springen.
- Ein erreichtes Level sinkt nie. Ein Abstieg nach einem schwachen Video bestraft genau die
  Nutzer, die dranbleiben. Der aktuelle Trend steht getrennt daneben.

Schwellen kalibriert an 98 Altlaeufen (Einzellauf je Kategorie: Median 3,3–4,0, p90 4,0–5,0).
Unter 3,0 waeren die Skill-Stufen beim ersten Video geschenkt.
"""
import json
from pathlib import Path

from models.analyst import KATEGORIEN, AnalystEvaluationV2

FENSTER = 5
# Level 4–10: Mindest-Schnitt der Kategorie ueber die letzten FENSTER Laeufe.
SCHWELLEN = {4: 3.0, 5: 3.3, 6: 3.6, 7: 3.9, 8: 4.2, 9: 4.5, 10: 4.8}
# Untergrenze der Skala fuer den Balken zwischen Level 3 und 4.
SCHNITT_START = 2.5
# Wo jede Stufe auf dem Fortschrittsbalken sitzt (Prozent). Dicht am Anfang, weit am Ende.
POSITION = {0: 0, 1: 5, 2: 12, 3: 20, 4: 28, 5: 36, 6: 48, 7: 60, 8: 73, 9: 86, 10: 100}
MAX_LEVEL = 10
ANALYSEN_FUER_LEVEL_2 = 3
ANALYSEN_STATT_NEUBEWERTUNG = 5
FOKUS_SCORE = 3          # „3 oder schlechter" — dieselbe Grenze wie SCHWACH_SCORE in analyst_eval
TREND_SCHWELLE = 0.15


def _kategorie_wert(scores: dict, dims) -> float | None:
    werte = [scores[d] for d in dims if isinstance(scores.get(d), (int, float)) and 1 <= scores[d] <= 5]
    return sum(werte) / len(werte) if werte else None


def _schnitt(laeufe: list[dict], dims) -> float | None:
    werte = [w for w in (_kategorie_wert(l["scores"], dims) for l in laeufe[-FENSTER:]) if w is not None]
    return sum(werte) / len(werte) if werte else None


def _level(n: int, neu: bool, schnitt: float | None) -> int:
    """Hoechstes k, fuer das ALLE Bedingungen 1..k erfuellt sind."""
    if n < 1:
        return 0
    if n < ANALYSEN_FUER_LEVEL_2:
        return 1
    if not (neu or n >= ANALYSEN_STATT_NEUBEWERTUNG):
        return 2
    level = 3
    for k in range(4, MAX_LEVEL + 1):
        if schnitt is None or schnitt < SCHWELLEN[k] - 1e-9:
            break
        level = k
    return level


def _naechstes(level: int) -> dict | None:
    if level >= MAX_LEVEL:
        return None
    if level == 0:
        return {"art": "analysen", "wert": 1}
    if level == 1:
        return {"art": "analysen", "wert": ANALYSEN_FUER_LEVEL_2}
    if level == 2:
        return {"art": "neubewertung_oder_analysen", "wert": ANALYSEN_STATT_NEUBEWERTUNG}
    return {"art": "schnitt", "wert": SCHWELLEN[level + 1]}


def _anteil(level: int, n: int, neu: bool, schnitt: float | None) -> float:
    """Wie weit es vom erreichten zum naechsten Level ist (0..1) — fuer die Balkenfuellung."""
    if level >= MAX_LEVEL:
        return 0.0
    if level == 0:
        return 0.0
    if level == 1:
        return min(1.0, n / ANALYSEN_FUER_LEVEL_2)
    if level == 2:
        return 1.0 if neu else min(1.0, n / ANALYSEN_STATT_NEUBEWERTUNG)
    unten = SCHWELLEN.get(level, SCHNITT_START)
    oben = SCHWELLEN[level + 1]
    if schnitt is None:
        return 0.0
    return max(0.0, min(1.0, (schnitt - unten) / (oben - unten)))


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


def berechne_aus(laeufe: list[dict]) -> dict:
    """`laeufe`: [{created_at, scores: {dimension: 1–5}, neubewertung: bool, aus_cache: bool}]."""
    gezaehlt = sorted((l for l in laeufe if not l.get("aus_cache")), key=lambda l: l.get("created_at", ""))
    kategorien = []
    for key, dims in KATEGORIEN.items():
        hoechstes, neu = 0, False
        for i, l in enumerate(gezaehlt):
            neu = neu or bool(l.get("neubewertung"))
            hoechstes = max(hoechstes, _level(i + 1, neu, _schnitt(gezaehlt[:i + 1], dims)))
        schnitt = _schnitt(gezaehlt, dims)
        anteil = _anteil(hoechstes, len(gezaehlt), neu, schnitt)
        pos = POSITION[hoechstes]
        if hoechstes < MAX_LEVEL:
            pos += anteil * (POSITION[hoechstes + 1] - POSITION[hoechstes])
        kategorien.append({
            "key": key, "level": hoechstes, "position": round(pos, 1),
            "schnitt": round(schnitt, 2) if schnitt is not None else None,
            "trend": _trend(gezaehlt, dims), "naechstes": _naechstes(hoechstes),
        })
    gesamt = sum(k["level"] for k in kategorien) // len(kategorien)
    return {
        "gesamt": {"level": gesamt,
                   "position": round(sum(k["position"] for k in kategorien) / len(kategorien), 1)},
        "kategorien": kategorien,
        "analysen": len(gezaehlt),
        "neubewertungen": sum(1 for l in gezaehlt if l.get("neubewertung")),
        "fokus": _fokus(gezaehlt),
        "stufen": [POSITION[k] for k in range(MAX_LEVEL + 1)],
        "max_level": MAX_LEVEL,
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
