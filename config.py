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

    model_config = {"env_file": ".env"}


settings = Settings()
PROJECTS_PATH = Path(settings.projects_dir)
MEMORY_PATH = Path(settings.memory_dir)
PROJECTS_PATH.mkdir(exist_ok=True)
MEMORY_PATH.mkdir(exist_ok=True)
