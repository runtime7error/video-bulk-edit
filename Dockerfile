# FFmpeg estatico e atualizado (o do apt do Debian e antigo)
FROM mwader/static-ffmpeg:9.0.2 AS ffmpeg

FROM python:3.12-slim
COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/tmp/autocutter

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# Render e Railway definem $PORT automaticamente
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
