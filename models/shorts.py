# models/shorts.py
"""
MultiCut-Datenmodell: Ein langes Video → viele eigenständige Shorts.

shorts.json im Projektordner ist die Single Source of Truth für
Kandidaten, Auswahl-Status und Batch-Fortschritt. Wird atomar geschrieben
(tmp + os.replace), damit das Frontend beim Polling nie halbe JSONs liest.
"""
from pydantic import BaseModel
from typing import Literal

ShortStatus = Literal["pending", "cutting", "done", "error"]
BatchStatus = Literal["idle", "running", "done"]
PostProcessing = Literal["segment", "v52"]


class ShortCandidate(BaseModel):
    id: str                      # "short_01", "short_02", …
    index: int                   # 1-basiert, chronologisch
    title: str                   # KI-Titel der Stelle
    start_sec: float
    end_sec: float
    duration_sec: float
    transcript: str              # Transkript-Vorschau für die Kachel
    kpi_score: int               # 1–5 (5 = maximale KPI-Erwartung)
    rationale: str = ""          # 1 Satz: warum diese Stelle zum Content-Typ passt
    selected: bool = True        # default an — User kann abwählen
    status: ShortStatus = "pending"
    output_file: str = ""        # relativ zu output/, z.B. "shorts/short_01.mp4"
    error: str = ""


class ShortsPlan(BaseModel):
    project_id: str
    content_type: str            # "tofu" | "mofu"
    shorts: list[ShortCandidate] = []
    batch_status: BatchStatus = "idle"
    post_processing: PostProcessing = "segment"
    claude_reasoning: str = ""
