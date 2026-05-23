FROM python:3.11-slim

# System deps — combined in one layer, clean up cache immediately
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        espeak-ng \
        fonts-liberation \
        fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first — separate layer so code changes don't bust it
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .
RUN mkdir -p voices app/jobs

# Download neural voice models at build time (~244 MB, cached in image layer)
# Only re-runs when requirements.txt or this step changes
RUN python3 -m piper.download_voices \
        en_US-ryan-medium \
        en_US-hfc_male-medium \
        en_US-hfc_female-medium \
        en_US-lessac-medium \
        --data-dir voices

EXPOSE 8000

# Use --log-level info so startup messages are visible in Render/Fly logs
CMD ["python3", "-m", "uvicorn", "app.server:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info", \
     "--access-log"]
