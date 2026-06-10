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

    model_config = {"env_file": ".env"}


settings = Settings()
PROJECTS_PATH = Path(settings.projects_dir)
MEMORY_PATH = Path(settings.memory_dir)
PROJECTS_PATH.mkdir(exist_ok=True)
MEMORY_PATH.mkdir(exist_ok=True)
