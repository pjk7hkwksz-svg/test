FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg espeak-ng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .
RUN mkdir -p voices app/jobs

# Download neural voice models at build time (~244 MB, cached in image layer)
RUN python3 -m piper.download_voices \
        en_US-ryan-medium \
        en_US-hfc_male-medium \
        en_US-hfc_female-medium \
        en_US-lessac-medium \
        --data-dir voices

EXPOSE 8000

CMD ["python3", "-m", "uvicorn", "app.server:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
