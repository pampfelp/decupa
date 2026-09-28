FROM python:3.12-slim

# ffmpeg vem do sistema; yt-dlp do pip, sem versao fixa de proposito, porque
# ele quebra quando as plataformas mudam e a correcao vem em release nova.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir yt-dlp requests

WORKDIR /app
COPY prova.py .

ENV PYTHONUNBUFFERED=1
CMD ["python", "prova.py"]
