# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from config import settings
from api import projects, pipeline, feedback, subtitles, shorts, analyst
from services.analyst_engine import markiere_abgebrochene_laeufe


@asynccontextmanager
async def lifespan(app: FastAPI):
    n = markiere_abgebrochene_laeufe()
    if n:
        print(f"  [ANALYST] {n} Lauf/Läufe nach Neustart als abgebrochen markiert")
    yield


app = FastAPI(title="KI Video Editor", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Analyst ist immer erreichbar. Die Editor-Router werden nur eingebunden, wenn
# ANALYST_ONLY aus ist (lokal). Auf dem öffentlichen Server (ANALYST_ONLY=1) sind
# die schweren Editor-Endpunkte damit gar nicht erst erreichbar.
app.include_router(analyst.router, prefix="/api/analyst", tags=["analyst"])

if not settings.analyst_only:
    app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
    app.include_router(pipeline.router, prefix="/api/pipeline", tags=["pipeline"])
    app.include_router(feedback.router, prefix="/api/feedback", tags=["feedback"])
    app.include_router(subtitles.router, prefix="/api/subtitles", tags=["subtitles"])
    app.include_router(shorts.router, prefix="/api/shorts", tags=["shorts"])


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html", {"request": request, "analyst_only": settings.analyst_only}
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
