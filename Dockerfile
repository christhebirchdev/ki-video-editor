# AI Video Analyst — Container fürs Server-Deployment (hinter Traefik).
FROM python:3.12-slim

# System-Abhängigkeiten:
#  ffmpeg          – Whisper-Audio + ebur128-Messung + Keyframes
#  libglib2.0-0/gl1 – für opencv-python-headless
#  libgomp1        – OpenMP, von faster-whisper/ctranslate2 benötigt
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libglib2.0-0 libgl1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Whisper-Modell wird beim ersten Lauf hierhin geladen (per Volume persistiert,
# damit es Redeploys überlebt und nicht jedes Mal neu ~470 MB zieht).
ENV HF_HOME=/models

EXPOSE 8001

# 1 Worker: die Analyst-Warteschlange (Semaphore) ist prozess-lokal.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
