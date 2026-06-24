# services/cut_engine_v52.py
"""
V5.2 Feinschliff-Engine — übersetzt Chris' menschliche Schnitt-Logik in Code.

Basiert vollständig auf V5 (gleiche Selection inkl. geteiltem Cache → identische
Satzauswahl, gleiche lokale Zonen-Quellen) und ergänzt vier Regeln, die aus dem
Hörtest-Feedback vom Testreel 5.0 abgeleitet sind:

REGEL 1 — STILLE-KOMPRESSION (Feedback: Mini-Pausen bei Cut-3-4s/5-6s/29s/35-36s)
  Whisper versteckt Pausen INNERHALB von Wort-Spannen ('und' 980ms, 'wir' 820ms,
  'so' 720ms, 'und' 1400ms). Die Wörter bleiben ganz (V4.2-Schutz war richtig!),
  aber interne Stille ≥280ms wird per RMS gefunden und auf einen natürlichen
  Rest (~160ms + Fades) komprimiert.

REGEL 2 — ONSET-GUARD (Feedback: 'mit' und 'können' vorne angeschnitten)
  Wortanfang ist heilig. Wenn Zonen-Clamping den Clip-Start HINTER den
  Wortbeginn schiebt, gewinnt das Wort: Start = Wortbeginn − 40ms. Die paar
  Millisekunden Filler-Ausklang schluckt der 20ms-Fade.

REGEL 3 — ZONEN IM WORT TRIMMEN STATT DROPPEN (Feedback: Hektik bei 22-23s)
  Ein Verbatim-Äh mitten in 'produziert' hat das ganze Wort gedroppt →
  Trümmer-Clips + kaputte Semantik. Jetzt: Liegt für das Wort ein RMS-Tail-Trim
  vor, gewinnt die Energie-Messung (präziser als Verbatim-Timestamps) und das
  Wort bleibt bis zum gemessenen Schnittpunkt. Nur Zonen, die das Wort
  großflächig abdecken, droppen es noch. Danach: Anti-Hektik-Merge (Clips mit
  ≤180ms sauberem Original-Abstand werden verbunden) + Shards <250ms fliegen.

REGEL 4 — CLIP-END-MURMUR-TRIM (Feedback: 'äh' bei Cut-22s = 'jemand,'-Äh)
  Am ENDE eines Clips: hängt nach einem kurzen Energie-Dip (≥60ms — entspannter
  als die globalen 100ms) nur noch leises Gemurmel ≤55% der Wortkern-Energie,
  wird dort geschnitten. Nur an Clip-Enden + nur bei verdächtig langen letzten
  Wörtern (Faktor ≥1.2) → risikoarm.

V1–V5 bleiben unberührt. Alle Regeln degradieren sauber: ohne RMS-Daten läuft
V5.2 wie V5.
"""
from pathlib import Path
from typing import Optional

import numpy as np

from config import PROJECTS_PATH
from models.analysis import VideoAnalysis, CutPlan, CutClip, WhisperWord
from models.memory import StyleMemory

from services.cut_engine_v2 import (
    group_into_sentences,
    assign_position_labels,
    estimated_max_word_duration,
    _is_filler,
)
from services.cut_engine_v4 import (
    _sentence_to_clips_v4,
    _find_raw_video,
    VAD_ZONE_SAFETY_SEC,
    COARSE_ZONE_SAFETY_SEC,
)
from services.cut_engine_v5 import (
    _select_sentences_cached_v5,
    VERBATIM_ZONE_SAFETY_SEC,
)
from services.outtake_detector import detect_outtake_sentences, detect_fragment_zones, detect_duplicate_word_zones
from services.audio_truth import build_audio_truth, _decode, _rms_per_frame, FRAME_SEC
from services.verbatim_whisper import get_verbatim_filler_zones

# --- Regel 1: Stille-Kompression ---
SILENCE_COMPRESS_MIN_SEC = 0.28      # interne Stille ab 280ms wird komprimiert
SILENCE_KEEP_HEAD_SEC = 0.10         # Rest-Atempause vor dem Schnitt (Teil 1 endet Stille+100ms)
SILENCE_KEEP_TAIL_SEC = 0.06         # Teil 2 beginnt 60ms vor Stimm-Wiedereinsatz
SILENCE_EDGE_PROTECT_SEC = 0.15      # erste/letzte 150ms des Clips nie als Stille werten
SILENCE_MIN_PIECE_SEC = 0.30         # beide Teilstücke müssen ≥300ms bleiben, sonst kein Split

# --- Regel 2: Onset-Guard ---
ONSET_GUARD_SEC = 0.04               # Clip-Start spätestens 40ms vor Wortbeginn

# --- Regel 3: Zonen-Auflösung + Anti-Hektik ---
ZONE_COVERS_WORD_DROP = 0.60         # Zone deckt ≥60% des Wortes → Wort fliegt (echtes Filler-Wort)
ZONE_TAIL_MIN_HEAD_SEC = 0.25        # Tail-Konversion nur wenn ≥250ms Wortkopf erhalten bleiben
MERGE_MAX_GAP_SEC = 0.18             # Clips mit ≤180ms sauberem Orig-Abstand verbinden
MIN_CLIP_SEC_V52 = 0.25              # Shards unter 250ms fliegen raus

# --- Regel 4: Murmur-Trim am Clip-Ende ---
MURMUR_DIP_MIN_SEC = 0.06            # entspannter Dip (global: 100ms)
MURMUR_HEAD_PROTECT_SEC = 0.12
MURMUR_MAX_LEN_SEC = 0.70            # Gemurmel-Segment max. 700ms
MURMUR_ENERGY_RATIO = 0.55           # leiser als 55% des Wortkerns
MURMUR_WORD_MIN_SEC = 0.50           # nur bei letzten Wörtern ≥500ms…
MURMUR_WORD_MIN_FACTOR = 1.20        # …mit Dauer-Faktor ≥1.2 (verdächtig)
MURMUR_RELEASE_SEC = 0.05

# --- Regel 4b: Tal-Erkennung (RMS-Messung 'jemand'-Äh: Buckel→Tal 50%→Äh-Buckel 70-80%) ---
# Lautes drangeklebtes Äh hat KEINEN Stille-Dip — aber ein messbares Energie-Tal
# zwischen Wortkern und Äh. Nur an Clip-Enden verdächtiger Wörter.
VALLEY_MAX_RATIO = 0.60              # Tal muss unter 60% der Wortkern-Energie fallen
VALLEY_HUMP_MIN_SEC = 0.10           # Buckel nach dem Tal: 100…
VALLEY_HUMP_MAX_SEC = 0.50           # …bis 500ms bis zum Clip-Ende (= das Äh)
VALLEY_RELEASE_SEC = 0.02

# --- Regel 1b: Stille an Clip-Rändern (RMS-Messung 'wir': Whisper-Wortspanne war ~850ms Stille) ---
EDGE_SILENCE_MIN_SEC = 0.25          # führende/abschließende Stille ab 250ms trimmen
EDGE_SILENCE_PREROLL_SEC = 0.08      # Rest-Vorlauf vor dem Stimm-Einsatz
EDGE_SILENCE_POSTROLL_SEC = 0.10     # Rest-Nachlauf nach Stimm-Ende


# ===== Regel 3a: Verbatim-Zonen pro Wort auflösen =====

def resolve_inword_zones(
    verbatim_zones: list[tuple[float, float]],
    words: list[WhisperWord],
    rms_tail_zones: list[tuple[float, float]],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """
    Liefert (filler_zones, tail_trim_zones_zusatz).

    - Zone überlappt kein Wort → bleibt Filler-Zone (Pause-Filler)
    - Wort hat bereits einen RMS-Tail-Trim → RMS gewinnt, Verbatim-Zone verworfen
    - Zone deckt ≥60% des Wortes → Filler-Zone (Wort ist das Filler-Wort)
    - Zone im Wort-Schwanz (≥250ms Kopf bleibt) → Tail-Trim an Zonen-Beginn
    - sonst (Zone am Wortkopf/Mitte ohne Platz) → Filler-Zone (konservativ)
    """
    filler: list[tuple[float, float]] = []
    tails: list[tuple[float, float]] = []
    for zs, ze in verbatim_zones:
        host: Optional[WhisperWord] = None
        for w in words:
            ov = min(ze, w.end) - max(zs, w.start)
            if ov > 0.04 and (w.end - w.start) > 0.05:
                host = w
                break
        if host is None:
            filler.append((zs, ze))
            continue
        if any(host.start <= ts < host.end for ts, _te in rms_tail_zones):
            continue  # RMS-Messung existiert für dieses Wort → präziser, Verbatim verwerfen
        w_dur = host.end - host.start
        ov = min(ze, host.end) - max(zs, host.start)
        if ov / w_dur >= ZONE_COVERS_WORD_DROP:
            filler.append((zs, ze))
        elif zs >= host.start + ZONE_TAIL_MIN_HEAD_SEC:
            tails.append((zs, host.end))
        else:
            filler.append((zs, ze))
    return filler, tails


# ===== Regel 2: Onset-Guard =====

def protect_onsets(clips: list[CutClip], words: list[WhisperWord]) -> tuple[list[CutClip], int]:
    """Clip-Start darf nie hinter dem Beginn seines ersten Wortes liegen."""
    fixed = 0
    out: list[CutClip] = []
    prev_end = 0.0
    for c in sorted(clips, key=lambda x: x.start):
        first_word = next(
            (w for w in words if w.end > c.start + 0.005 and w.start < c.end and w.end - w.start > 0.012),
            None,
        )
        start = c.start
        if first_word is not None and start > first_word.start - ONSET_GUARD_SEC:
            desired = max(first_word.start - ONSET_GUARD_SEC, prev_end + 0.005, 0.0)
            if desired < start:
                start = desired
                fixed += 1
        out.append(CutClip(start=start, end=c.end, reason=c.reason))
        prev_end = c.end
    return out, fixed


# ===== Regel 4: Murmur-Trim am Clip-Ende =====

def trim_trailing_murmur(
    clips: list[CutClip],
    words: list[WhisperWord],
    rms: np.ndarray,
    noise_floor: float,
    tail_trim_zones: Optional[list[tuple[float, float]]] = None,
) -> tuple[list[CutClip], int]:
    if len(rms) == 0:
        return clips, 0
    tail_trim_zones = tail_trim_zones or []
    out: list[CutClip] = []
    trimmed = 0
    dip_frames = int(MURMUR_DIP_MIN_SEC / FRAME_SEC)
    head_frames = int(MURMUR_HEAD_PROTECT_SEC / FRAME_SEC)

    for c in clips:
        # Clip-Ende stammt bereits aus einem RMS-Tail-Trim → schon energie-verifiziert,
        # NICHT weiter anfassen (sonst frisst die Tal-Regel Silbengrenzen, z. B. 'produ-ziert')
        if any(abs(ts - c.end) < 0.10 for ts, _te in tail_trim_zones):
            out.append(c)
            continue
        last_word = next(
            (w for w in reversed(words) if w.end >= c.end - 0.15 and w.start < c.end - 0.05),
            None,
        )
        new_end = c.end
        if last_word is not None:
            dur = last_word.end - last_word.start
            expected = estimated_max_word_duration(last_word.word)
            factor = dur / expected if expected > 0 else 0
            if dur >= MURMUR_WORD_MIN_SEC and factor >= MURMUR_WORD_MIN_FACTOR:
                i0 = max(0, int(round(last_word.start / FRAME_SEC)))
                i1 = min(len(rms), int(round(min(c.end, last_word.end) / FRAME_SEC)))
                seg = rms[i0:i1]
                if len(seg) > head_frames + dip_frames + 3:
                    threshold = max(noise_floor * 2.0, float(np.percentile(seg, 90)) * 0.18)
                    quiet = seg < threshold
                    runs, rs = [], None
                    for i, q in enumerate(quiet):
                        if q and rs is None:
                            rs = i
                        elif not q and rs is not None:
                            runs.append((rs, i)); rs = None
                    if rs is not None:
                        runs.append((rs, len(seg)))
                    for r0, r1 in reversed(runs):
                        if r1 - r0 < dip_frames or r0 < head_frames:
                            continue
                        after = seg[r1:]
                        if len(after) < 3 or len(after) > int(MURMUR_MAX_LEN_SEC / FRAME_SEC):
                            continue
                        core = float(np.percentile(seg[:r0], 90)) if r0 > 0 else 0.0
                        if core <= 0 or float(np.mean(after)) > core * MURMUR_ENERGY_RATIO:
                            continue
                        if float(np.mean(after)) <= threshold:
                            continue
                        new_end = last_word.start + r0 * FRAME_SEC + MURMUR_RELEASE_SEC
                        trimmed += 1
                        break

                    # Regel 4b: Tal-Erkennung — lautes drangeklebtes Äh ohne Stille-Dip.
                    # Gemessen am 'jemand'-Fall: Wortkern 100% → Tal 50% → Äh-Buckel 70-80%
                    # bis zum Clip-Ende. Schnitt am Tal-Minimum.
                    if new_end == c.end and len(seg) > head_frames + 10:
                        core = float(np.percentile(seg, 90))
                        # geglättet (50ms-Fenster) gegen Mikro-Schwankungen
                        kernel = np.ones(5) / 5.0
                        smooth = np.convolve(seg, kernel, mode="same")
                        hump_min = int(VALLEY_HUMP_MIN_SEC / FRAME_SEC)
                        hump_max = int(VALLEY_HUMP_MAX_SEC / FRAME_SEC)
                        # Tal-Kandidaten: lokales Minimum nach dem Wortkopf,
                        # Buckel danach reicht bis (fast) zum Clip-Ende
                        best = None
                        for i in range(head_frames, len(smooth) - hump_min):
                            tail_len = len(smooth) - i
                            if tail_len < hump_min or tail_len > hump_max:
                                continue
                            if smooth[i] >= core * VALLEY_MAX_RATIO:
                                continue
                            after_mean = float(np.mean(smooth[i:]))
                            if after_mean <= smooth[i] * 1.15:
                                continue  # nach dem Tal kommt kein Buckel mehr → normales Wortende
                            if best is None or smooth[i] < smooth[best]:
                                best = i
                        if best is not None:
                            new_end = last_word.start + best * FRAME_SEC + VALLEY_RELEASE_SEC
                            trimmed += 1
        if new_end - c.start >= MIN_CLIP_SEC_V52:
            out.append(CutClip(start=c.start, end=min(new_end, c.end), reason=c.reason))
        else:
            out.append(c)
    return out, trimmed


# ===== Regel 1b: Stille an Clip-Rändern trimmen =====

def trim_edge_silence(
    clips: list[CutClip],
    rms: np.ndarray,
    noise_floor: float,
) -> tuple[list[CutClip], int]:
    """
    Führende/abschließende Stille ≥250ms am Clip-Rand wird abgeschnitten.
    Messfall 'wir' (27.07-27.89): Whispers Wortspanne war zu ~85% Stille —
    der Clip begann mit 850ms toter Luft, die die interne Kompression
    (Randschutz + Mindeststück-Regel) nicht greifen konnte.
    """
    if len(rms) == 0:
        return clips, 0
    min_run = int(EDGE_SILENCE_MIN_SEC / FRAME_SEC)
    trimmed = 0
    out: list[CutClip] = []
    for c in clips:
        i0 = max(0, int(round(c.start / FRAME_SEC)))
        i1 = min(len(rms), int(round(c.end / FRAME_SEC)))
        seg = rms[i0:i1]
        start, end = c.start, c.end
        if len(seg) > min_run + 10:
            threshold = max(noise_floor * 2.5, float(np.percentile(seg, 90)) * 0.10)
            # führende Stille
            n_lead = 0
            while n_lead < len(seg) and seg[n_lead] < threshold:
                n_lead += 1
            if n_lead >= min_run:
                start = c.start + n_lead * FRAME_SEC - EDGE_SILENCE_PREROLL_SEC
                trimmed += 1
            # abschließende Stille
            n_tail = 0
            while n_tail < len(seg) and seg[len(seg) - 1 - n_tail] < threshold:
                n_tail += 1
            if n_tail >= min_run:
                end = c.end - n_tail * FRAME_SEC + EDGE_SILENCE_POSTROLL_SEC
                trimmed += 1
        if end - start >= MIN_CLIP_SEC_V52:
            out.append(CutClip(start=max(start, c.start), end=min(end, c.end), reason=c.reason))
        else:
            out.append(c)
    return out, trimmed


# ===== Regel 1: Stille-Kompression =====

def compress_silences(
    clips: list[CutClip],
    rms: np.ndarray,
    noise_floor: float,
) -> tuple[list[CutClip], int]:
    if len(rms) == 0:
        return clips, 0
    min_run = int(SILENCE_COMPRESS_MIN_SEC / FRAME_SEC)
    edge = int(SILENCE_EDGE_PROTECT_SEC / FRAME_SEC)
    splits = 0
    work = sorted(clips, key=lambda x: x.start)
    out: list[CutClip] = []

    while work:
        c = work.pop(0)
        i0 = max(0, int(round(c.start / FRAME_SEC)))
        i1 = min(len(rms), int(round(c.end / FRAME_SEC)))
        seg = rms[i0:i1]
        done = True
        if len(seg) > 2 * edge + min_run:
            threshold = max(noise_floor * 2.5, float(np.percentile(seg, 90)) * 0.10)
            quiet = seg < threshold
            rs = None
            for i in range(edge, len(seg) - edge):
                if quiet[i] and rs is None:
                    rs = i
                elif not quiet[i] and rs is not None:
                    if i - rs >= min_run:
                        sil_start = c.start + rs * FRAME_SEC
                        sil_end = c.start + i * FRAME_SEC
                        p1 = CutClip(start=c.start, end=sil_start + SILENCE_KEEP_HEAD_SEC,
                                     reason=c.reason + " [pause-compress]")
                        p2 = CutClip(start=sil_end - SILENCE_KEEP_TAIL_SEC, end=c.end, reason=c.reason)
                        if (p1.end - p1.start) >= SILENCE_MIN_PIECE_SEC and (p2.end - p2.start) >= SILENCE_MIN_PIECE_SEC:
                            out.append(p1)
                            work.insert(0, p2)   # Rest erneut prüfen (mehrere Pausen pro Clip)
                            splits += 1
                            done = False
                            break
                    rs = None
        if done:
            out.append(c)
    return sorted(out, key=lambda x: x.start), splits


# ===== Regel 3b: Anti-Hektik-Merge + Shard-Filter =====

def merge_close_clips(
    clips: list[CutClip],
    blocked_zones: list[tuple[float, float]],
) -> tuple[list[CutClip], int]:
    """Clips mit ≤180ms Original-Abstand verbinden — außer eine Zone liegt im Gap."""
    if not clips:
        return clips, 0
    ordered = sorted(clips, key=lambda x: x.start)
    merged = [ordered[0]]
    n = 0
    for c in ordered[1:]:
        prev = merged[-1]
        gap = c.start - prev.end
        gap_blocked = any(min(c.start, ze) - max(prev.end, zs) > 0.02 for zs, ze in blocked_zones)
        if 0 <= gap <= MERGE_MAX_GAP_SEC and not gap_blocked:
            merged[-1] = CutClip(start=prev.start, end=c.end, reason=prev.reason + " [merged]")
            n += 1
        else:
            merged.append(c)
    return merged, n


# ===== Hauptfunktion =====

def plan_cuts_v52(analysis: VideoAnalysis, memory: StyleMemory, platform: str) -> CutPlan:
    """V5.2: V5-Basis + Feinschliff (Stille-Kompression, Onset-Guard, Murmur-Trim, Anti-Hektik)."""

    # --- Identisch zu V5: Sätze, Flags, Selection (geteilter v5-Cache!) ---
    sentences = group_into_sentences(analysis.whisper_words)
    assign_position_labels(sentences, analysis.duration_sec)
    print(f"  [V5.2] {len(sentences)} Sätze (Gemini: nicht verwendet)")
    if not sentences:
        return CutPlan(project_id=analysis.project_id, clips=[], claude_reasoning="V5.2: Keine Sätze erkannt")

    flags = detect_outtake_sentences(sentences)
    flags_by_id = {f.sentence_id: f for f in flags}
    enforced_out = {f.sentence_id for f in flags if f.enforced}

    kept_ids, reasoning = _select_sentences_cached_v5(
        sentences, flags_by_id, analysis.project_id, platform, analysis.duration_sec, memory
    )
    kept_ids = [i for i in kept_ids if i not in enforced_out]
    kept_sentences = [s for s in sentences if s.id in kept_ids]
    print(f"  [V5.2] {len(kept_sentences)}/{len(sentences)} Sätze behalten")

    # --- Lokale Audio-Signale (geteilte Caches mit V4/V5) ---
    video_path = _find_raw_video(analysis.project_id)
    rms = np.zeros(0, dtype=np.float32)
    noise_floor = 0.0
    verbatim_zones: list[tuple[float, float]] = []
    truth = {"pause_filler_zones": [], "tail_trim_zones": [], "vad_segment_count": 0}
    if video_path is not None:
        truth = build_audio_truth(
            video_path, analysis.whisper_words, estimated_max_word_duration,
            cache_path=PROJECTS_PATH / analysis.project_id / "v4_audio_truth.json",
        )
        verbatim_zones = get_verbatim_filler_zones(
            video_path, cache_path=PROJECTS_PATH / analysis.project_id / "v5_verbatim_fillers.json",
        )
        try:
            audio = _decode(video_path)
            rms = _rms_per_frame(audio)
            noise_floor = float(np.percentile(rms, 5)) if len(rms) else 0.0
        except Exception as e:
            print(f"  [V5.2] WARN: RMS nicht verfügbar ({e}) — Feinschliff-Regeln 1/4 inaktiv")

    truth_available = truth.get("vad_segment_count", 0) > 0
    rms_tail_zones = [tuple(z) for z in truth["tail_trim_zones"]]

    # --- Regel 3a: Verbatim-Zonen auflösen (RMS gewinnt, Tail statt Drop) ---
    verbatim_filler, verbatim_tails = resolve_inword_zones(
        verbatim_zones, analysis.whisper_words, rms_tail_zones
    )
    tail_trim_zones = sorted(rms_tail_zones + verbatim_tails)

    whisper_filler_zones = [
        (w.start, w.end, COARSE_ZONE_SAFETY_SEC)
        for w in analysis.whisper_words if _is_filler(w.word)
    ]
    pause_filler_zones = [(z[0], z[1], VAD_ZONE_SAFETY_SEC) for z in truth["pause_filler_zones"]]
    verbatim_filler_zones = [(zs, ze, VERBATIM_ZONE_SAFETY_SEC) for zs, ze in verbatim_filler]
    filler_zones = sorted(whisper_filler_zones + pause_filler_zones + verbatim_filler_zones)

    outtake_zones = detect_fragment_zones(analysis.whisper_words)
    _dup_zones = detect_duplicate_word_zones(analysis.whisper_words)
    if _dup_zones:
        print(f"  [V5.2] {len(_dup_zones)} doppelte Wörter (Stotterer) entfernt: "
              + ", ".join(f"{a:.2f}-{b:.2f}" for a, b in _dup_zones))
    outtake_zones = sorted(outtake_zones + _dup_zones)

    print(
        f"  [V5.2] Zonen: {len(whisper_filler_zones)}+{len(pause_filler_zones)}+{len(verbatim_filler_zones)} Filler, "
        f"{len(tail_trim_zones)} Tail-Trims ({len(verbatim_tails)} aus Verbatim konvertiert)"
    )

    # --- Basis-Clips (V4.2-Builder) ---
    clips: list[CutClip] = []
    for s in kept_sentences:
        clips.extend(_sentence_to_clips_v4(
            s, filler_zones, outtake_zones, tail_trim_zones,
            use_text_fallback=not truth_available,
        ))

    # --- Feinschliff-Pässe ---
    clips, n_onset = protect_onsets(clips, analysis.whisper_words)
    clips, n_murmur = trim_trailing_murmur(
        clips, analysis.whisper_words, rms, noise_floor, tail_trim_zones=tail_trim_zones
    )
    clips, n_edges = trim_edge_silence(clips, rms, noise_floor)
    clips, n_splits = compress_silences(clips, rms, noise_floor)
    block = [(z[0], z[1]) for z in filler_zones] + tail_trim_zones
    clips, n_merged = merge_close_clips(clips, block)
    clips = [c for c in clips if c.end - c.start >= MIN_CLIP_SEC_V52]

    print(
        f"  [V5.2] Feinschliff: {n_onset} Onsets geschützt, {n_murmur} Murmur/Tal-Trims, "
        f"{n_edges} Rand-Stillen getrimmt, {n_splits} Pausen komprimiert, "
        f"{n_merged} Clips verbunden → {len(clips)} finale Clips"
    )

    return CutPlan(
        project_id=analysis.project_id,
        clips=clips,
        claude_reasoning=f"V5.2 (Feinschliff): {reasoning}",
    )
