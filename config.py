# config.py
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    gemini_api_key: str
    anthropic_api_key: str
    assemblyai_api_key: str
    projects_dir: str = "projects"
    memory_dir: str = "memory"
    host: str = "0.0.0.0"
    port: int = 8001

    # Claude-Modell für Sentence-Selection (V1/V2/V3).
    # Override über ENV: export CLAUDE_MODEL=claude-opus-4-8
    # Aktuelle Anthropic-Modelle: claude-sonnet-4-6, claude-opus-4-8, claude-haiku-4-5-20251001
    claude_model: str = "claude-sonnet-4-6"

    # AI Video Analyst: Beschreibung läuft über Gemini (gemini_api_key oben), kein Ollama mehr.

    # --- Server-/Deployment-Schalter (per .env steuerbar) ---
    # ANALYST_ONLY=1 → nur der Video-Analyst ist erreichbar, Editor-Router werden
    # nicht eingebunden (für öffentliches Server-Deployment). Lokal (Flag aus) = volle App.
    analyst_only: bool = False

    # Whisper-Modell für die Analyst-Transkription. Auf schwachem VPS auf "base"
    # oder "tiny" absenken (per .env WHISPER_MODEL=base), ohne Code-Änderung.
    whisper_model: str = "small"

    # Wie viele Analysen gleichzeitig wirklich laufen dürfen. Default 1 = Jobs
    # werden seriell abgearbeitet (Warteschlange), schützt den geteilten VPS.
    analyst_max_concurrent: int = 1

    # Passwort für die Admin-/Feedback-Ansicht (Kunden-Ansicht bleibt unberührt).
    # NUR über .env setzen (ADMIN_PASSWORD=...) — dieses Repo ist ÖFFENTLICH, ein Default hier
    # wäre auf GitHub für jeden lesbar. Leer = Admin-Ansicht komplett gesperrt (fail closed),
    # damit ein vergessenes .env nicht versehentlich alles freischaltet.
    admin_password: str = ""

    model_config = {"env_file": ".env"}


settings = Settings()
PROJECTS_PATH = Path(settings.projects_dir)
MEMORY_PATH = Path(settings.memory_dir)
PROJECTS_PATH.mkdir(exist_ok=True)
MEMORY_PATH.mkdir(exist_ok=True)
