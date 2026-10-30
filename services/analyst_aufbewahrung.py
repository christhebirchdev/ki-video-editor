# services/analyst_aufbewahrung.py
"""V4: Videodateien nach N Tagen loeschen, Analysen behalten (Spec V4, Abschnitt 4).

Warum: Der VPS hat wenig Platz, und ein Video ist das personenbezogenste Stueck eines Laufs.
Die Analyse, der Chatverlauf und das Level bleiben. Was danach nicht mehr geht: Chat-Rueckfragen,
die das Video brauchen, und „mit anderem Ziel bewerten" (neu-bewerten meldet dann 410).

`tage <= 0` schaltet das Aufraeumen ab — Default lokal, damit Testvideos auf dem Mac nicht
verschwinden. Der Server setzt ANALYST_VIDEO_TAGE=30 in docker-compose.yml.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

LAUFENDE_PHASEN = ("starting", "queued", "transcribe", "quality", "describe", "evaluate")


def raeume_videos_auf(basis: Path, tage: int, jetzt: datetime | None = None) -> int:
    if tage <= 0 or not basis.is_dir():
        return 0
    jetzt = jetzt or datetime.now()
    grenze = jetzt - timedelta(days=tage)
    geloescht = 0
    for d in basis.iterdir():
        raw = d / "raw"
        if not raw.is_dir():
            continue
        try:
            meta = json.loads((d / "meta.json").read_text())
            erstellt = datetime.fromisoformat(meta.get("created_at", ""))
            phase = json.loads((d / "status.json").read_text()).get("phase")
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if erstellt >= grenze or phase in LAUFENDE_PHASEN:
            continue
        dateien = [f for f in raw.iterdir() if f.is_file()]
        if not dateien:
            continue
        for f in dateien:
            f.unlink(missing_ok=True)
        meta["video_geloescht"] = jetzt.isoformat(timespec="seconds")
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
        geloescht += 1
    return geloescht
