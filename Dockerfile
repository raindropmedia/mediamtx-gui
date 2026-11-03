FROM python:3.12-slim

# ffmpeg wird fuer die Erzeugung der Stream-Vorschaubilder (Snapshots) benoetigt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Verzeichnis fuer SQLite-Datenbank und Snapshot-Bilder (wird per Volume gemountet).
RUN mkdir -p /data/snapshots

ENV PYTHONUNBUFFERED=1 \
    MEDIAMTX_GUI_DATA_DIR=/data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
