"""Fase 1 do Decupa: a prova de fogo.

Existe pra responder uma pergunta so, antes de qualquer tela: da pra baixar
video do Instagram, TikTok, YouTube e Facebook a partir de um servidor de
nuvem? Baixa so o audio, comprime, e manda pra Groq transcrever em portugues.

Nao e o produto. Nao tem fila, nem banco, nem teto. Sai do ar depois de medir.
"""

import glob
import json
import os
import re
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import requests

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODELO = "whisper-large-v3-turbo"

PLATAFORMAS = {
    "instagram.com": "instagram",
    "tiktok.com": "tiktok",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "facebook.com": "facebook",
    "fb.watch": "facebook",
}


def plataforma_de(url):
    host = (urlparse(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    for dominio, nome in PLATAFORMAS.items():
        if host == dominio or host.endswith("." + dominio):
            return nome
    return None


def rodar(cmd, pasta):
    """Roda um binario e devolve (deu_certo, fim_do_stderr). O fim do stderr e
    o que diz se foi bloqueio de IP, login exigido ou outra coisa."""
    p = subprocess.run(cmd, cwd=pasta, capture_output=True, text=True, timeout=600)
    erro = (p.stderr or p.stdout or "").strip().splitlines()
    return p.returncode == 0, "\n".join(erro[-6:])


def texto_do_vtt(caminho):
    """Legenda automatica do YouTube repete cada linha varias vezes por causa
    do efeito de rolagem. Tira marcacao, tira repeticao consecutiva."""
    linhas = []
    for linha in open(caminho, encoding="utf-8"):
        linha = linha.strip()
        if not linha or "-->" in linha or linha.startswith(("WEBVTT", "Kind:", "Language:")):
            continue
        linha = re.sub(r"<[^>]+>", "", linha).strip()
        if linha and (not linhas or linhas[-1] != linha):
            linhas.append(linha)
    return " ".join(linhas)


def tentar_legenda(url, pasta):
    """O caminho barato do YouTube: se ja existe legenda automatica em
    portugues, nao baixa video, nao converte e nao gasta Groq."""
    ok, _ = rodar([
        "yt-dlp", "--skip-download", "--write-auto-subs", "--write-subs",
        "--sub-langs", "pt.*", "--sub-format", "vtt", "-o", "legenda", url,
    ], pasta)
    achados = glob.glob(os.path.join(pasta, "legenda*.vtt"))
    if not ok or not achados:
        return None
    texto = texto_do_vtt(achados[0])
    return texto or None


def baixar_audio(url, pasta):
    ok, erro = rodar([
        "yt-dlp", "-f", "bestaudio/best", "--no-playlist",
        "-o", "audio.%(ext)s", url,
    ], pasta)
    achados = [a for a in glob.glob(os.path.join(pasta, "audio.*")) if not a.endswith(".ogg")]
    if not ok or not achados:
        raise RuntimeError("DOWNLOAD_FALHOU: " + erro)
    return achados[0]


def comprimir(entrada, pasta):
    """opus 16 kbps mono a 16 kHz. Uma hora deve dar uns 7 MB. A Fase 1 mede
    se esse numero do plano esta certo."""
    saida = os.path.join(pasta, "audio.ogg")
    ok, erro = rodar([
        "ffmpeg", "-y", "-i", entrada, "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "16k", saida,
    ], pasta)
    if not ok or not os.path.exists(saida):
        raise RuntimeError("CONVERSAO_FALHOU: " + erro)
    return saida


def transcrever(caminho):
    chave = os.environ.get("GROQ_API_KEY")
    if not chave:
        raise RuntimeError("GROQ_API_KEY nao esta definida no ambiente")
    with open(caminho, "rb") as f:
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": "Bearer " + chave},
            files={"file": ("audio.ogg", f, "audio/ogg")},
            data={"model": MODELO, "language": "pt", "response_format": "verbose_json"},
            timeout=600,
        )
    if r.status_code != 200:
        raise RuntimeError("GROQ_RECUSOU %s: %s" % (r.status_code, r.text[:400]))
    return r.json()


def processar(url):
    plataforma = plataforma_de(url)
    if not plataforma:
        return {"ok": False, "erro": "PLATAFORMA_NAO_SUPORTADA", "url": url}

    medida = {"ok": True, "url": url, "plataforma": plataforma}
    with tempfile.TemporaryDirectory() as pasta:
        try:
            if plataforma == "youtube":
                inicio = time.monotonic()
                texto = tentar_legenda(url, pasta)
                if texto:
                    medida["fonte"] = "legenda automatica"
                    medida["segundos_total"] = round(time.monotonic() - inicio, 1)
                    medida["texto"] = texto
                    return medida

            inicio = time.monotonic()
            bruto = baixar_audio(url, pasta)
            medida["segundos_download"] = round(time.monotonic() - inicio, 1)
            medida["bytes_baixados"] = os.path.getsize(bruto)

            inicio = time.monotonic()
            comprimido = comprimir(bruto, pasta)
            medida["segundos_conversao"] = round(time.monotonic() - inicio, 1)
            medida["bytes_audio_opus"] = os.path.getsize(comprimido)

            inicio = time.monotonic()
            resposta = transcrever(comprimido)
            medida["segundos_transcricao"] = round(time.monotonic() - inicio, 1)
            medida["fonte"] = "groq " + MODELO
            medida["duracao_audio"] = resposta.get("duration")
            medida["texto"] = (resposta.get("text") or "").strip()
        except Exception as e:
            medida["ok"] = False
            medida["erro"] = str(e)
    return medida


AJUDA = (
    "Decupa, prova da Fase 1, com deno.\n\n"
    "GET /transcrever?url=<link do video>\n\n"
    "Instagram, TikTok, YouTube e Facebook. Devolve o texto e as medicoes.\n"
)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        partes = urlparse(self.path)
        if partes.path != "/transcrever":
            self.responder(200, AJUDA, "text/plain; charset=utf-8")
            return
        url = (parse_qs(partes.query).get("url") or [""])[0].strip()
        if not url:
            self.responder(400, AJUDA, "text/plain; charset=utf-8")
            return
        resultado = processar(url)
        corpo = json.dumps(resultado, ensure_ascii=False, indent=2)
        self.responder(200 if resultado["ok"] else 502, corpo, "application/json; charset=utf-8")

    def responder(self, codigo, corpo, tipo):
        dados = corpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def log_message(self, formato, *args):
        print("%s - %s" % (self.address_string(), formato % args), flush=True)


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "10000"))
    print("prova da Fase 1 ouvindo na porta %d" % porta, flush=True)
    ThreadingHTTPServer(("0.0.0.0", porta), Handler).serve_forever()
