FROM python:3.12-slim

# ffmpeg vem do sistema; yt-dlp do pip, sem versao fixa de proposito, porque
# ele quebra quando as plataformas mudam e a correcao vem em release nova.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# O yt-dlp passa o desafio de JavaScript do YouTube com um runtime externo, e
# so o deno vem habilitado por padrao. Sem ele, o YouTube nem chega a ser
# tentado de verdade.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

RUN pip install --no-cache-dir yt-dlp requests firebase-admin

WORKDIR /app
COPY servidor.py .

ENV PYTHONUNBUFFERED=1
CMD ["python", "servidor.py"]
