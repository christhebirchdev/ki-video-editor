# services/ingest.py
"""
HDR→SDR-Normalisierung beim Upload — VOR Schnitt/Vorschau/Untertitel.

Quellen sind oft iPhone-HLG-HDR. Wenn HDR durch die ganze Pipeline läuft, kippt der in
SDR gezeichnete Untertitel je nach Player (rot/dunkel) und Vorschau≠Export. Lösung: HDR
EINMAL beim Upload nach SDR/Rec.709 umwandeln. Danach ist alles SDR-konsistent → Untertitel
überall korrekt, Vorschau == Export.

Bevorzugt libplacebo (farb-gemanagt, sieht aus wie das Original). Fallback: zscale + tonemap.
"""
import subprocess
from pathlib import Path

from services.subtitle_engine import find_ffmpeg_with_ass, _is_hdr

_HAS_PLACEBO = None


def _has_libplacebo(ffmpeg_bin: str) -> bool:
    global _HAS_PLACEBO
    if _HAS_PLACEBO is None:
        try:
            out = subprocess.run([ffmpeg_bin, "-hide_banner", "-filters"],
                                 capture_output=True, text=True).stdout
            _HAS_PLACEBO = "libplacebo" in out
        except Exception:
            _HAS_PLACEBO = False
    return _HAS_PLACEBO


def sdr_filter_chain(ffmpeg_bin: str) -> str:
    """ffmpeg-Filterkette HDR→SDR/Rec.709. libplacebo wenn vorhanden, sonst zscale+tonemap."""
    if _has_libplacebo(ffmpeg_bin):
        return "libplacebo=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv,format=yuv420p"
    return ("zscale=t=linear:npl=100,format=gbrpf32le,tonemap=hable:desat=0,"
            "zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p")


def normalize_hdr_to_sdr(video_path: Path) -> bool:
    """Wandelt eine HDR-Quelle in-place nach SDR/Rec.709 (gleicher Dateiname).
    Returns True wenn umgewandelt, False wenn schon SDR (no-op)."""
    if not _is_hdr(video_path):
        return False
    ffmpeg_bin = find_ffmpeg_with_ass()
    tmp = video_path.parent / (video_path.stem + ".sdrtmp.mp4")

    def _run(vf: str):
        return subprocess.run(
            [ffmpeg_bin, "-y", "-nostdin", "-i", str(video_path), "-vf", vf,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
             "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)],
            check=False, capture_output=True, stdin=subprocess.DEVNULL,
        )

    res = _run(sdr_filter_chain(ffmpeg_bin))
    if res.returncode != 0 and _has_libplacebo(ffmpeg_bin):
        # libplacebo-Versuch gescheitert → zscale-Fallback
        res = _run("zscale=t=linear:npl=100,format=gbrpf32le,tonemap=hable:desat=0,"
                   "zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p")
    if res.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("HDR→SDR fehlgeschlagen: " + res.stderr.decode("utf-8", "replace")[-1500:])

    tmp.replace(video_path)   # Original durch SDR ersetzen, Name bleibt
    return True
