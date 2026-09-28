"""Decupa, backend da Fase 2.

Recebe um link, devolve um id na hora, e faz o trabalho pesado depois. Quem
pediu acompanha o andamento pelo Firestore em vez de ficar segurando uma
conexao aberta: download mais transcricao levam de 10 a 60 segundos e podem
falhar, e requisicao sincrona que espera isso estoura timeout de proxy e
deixa a pessoa olhando pra tela parada.

Cresceu a partir de prova.py, que era a Fase 1 e ja cumpriu o papel dela.
"""

import glob
import hashlib
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import requests
import firebase_admin
from firebase_admin import credentials, firestore

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODELO = "whisper-large-v3-turbo"
TRABALHOS = "trabalhos"
CACHE = "cache"
LIMITE_CORPO = 4096

PLATAFORMAS = {
    "instagram.com": "instagram",
    "tiktok.com": "tiktok",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "facebook.com": "facebook",
    "fb.watch": "facebook",
}


# ---------------------------------------------------------------- endereco

def dominio_de(url):
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def plataforma_de(url):
    host = dominio_de(url)
    for dominio, nome in PLATAFORMAS.items():
        if host == dominio or host.endswith("." + dominio):
            return nome
    return None


def normalizar(url):
    """Chave do cache. Tira o que nao muda o video: www, barra final e os
    parametros de rastreamento que cada plataforma gruda no link copiado."""
    partes = urlparse(url)
    host = dominio_de(url)
    caminho = partes.path.rstrip("/")
    if host == "youtu.be" and caminho:
        return "youtube.com/watch?v=" + caminho.strip("/")
    if host.endswith("youtube.com"):
        v = (parse_qs(partes.query).get("v") or [""])[0]
        if v:
            return "youtube.com/watch?v=" + v
    return host + caminho


def chave_cache(url_normalizada):
    return hashlib.sha1(url_normalizada.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- pipeline

class Falha(Exception):
    def __init__(self, codigo, detalhe=""):
        super().__init__(codigo)
        self.codigo = codigo
        self.detalhe = detalhe[:1500]


def rodar(cmd, pasta):
    p = subprocess.run(cmd, cwd=pasta, capture_output=True, text=True, timeout=600)
    fim = (p.stderr or p.stdout or "").strip().splitlines()
    return p.returncode == 0, "\n".join(fim[-6:])


def codigo_da_falha(plataforma, detalhe):
    """Erro com codigo, nunca mensagem generica. A frase acionavel e montada
    pela tela; aqui sai o codigo e o detalhe tecnico que ajuda a diagnosticar."""
    t = detalhe.lower()
    bloqueio = any(m in t for m in ("429", "403", "login", "sign in", "rate-limit", "cookies"))
    if plataforma == "youtube":
        return "YOUTUBE_BLOQUEADO"
    if plataforma == "instagram" and bloqueio:
        return "INSTAGRAM_BLOQUEADO"
    return "DOWNLOAD_FALHOU"


def texto_do_vtt(caminho):
    """Legenda automatica repete cada frase por causa da rolagem."""
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
    """O caminho barato: legenda que ja existe nao gasta banda nem cota da
    Groq. Medido em 2026-09-28: no YouTube ela cai junto com o bloqueio de IP,
    porque depende da mesma extracao de pagina. Continua sendo tentada porque
    e o unico caminho que pode voltar a funcionar sem proxy."""
    rodar([
        "yt-dlp", "--skip-download", "--write-auto-subs", "--write-subs",
        "--sub-langs", "pt.*", "--sub-format", "vtt", "-o", "legenda", url,
    ], pasta)
    achados = glob.glob(os.path.join(pasta, "legenda*.vtt"))
    if not achados:
        return None
    return texto_do_vtt(achados[0]) or None


def baixar_audio(url, plataforma, pasta):
    ok, detalhe = rodar([
        "yt-dlp", "-f", "bestaudio/best", "--no-playlist", "-o", "audio.%(ext)s", url,
    ], pasta)
    achados = [a for a in glob.glob(os.path.join(pasta, "audio.*")) if not a.endswith(".ogg")]
    if not ok or not achados:
        raise Falha(codigo_da_falha(plataforma, detalhe), detalhe)
    return achados[0]


def comprimir(entrada, pasta):
    saida = os.path.join(pasta, "audio.ogg")
    ok, detalhe = rodar([
        "ffmpeg", "-y", "-i", entrada, "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "16k", saida,
    ], pasta)
    if not ok or not os.path.exists(saida):
        raise Falha("CONVERSAO_FALHOU", detalhe)
    return saida


def transcrever(caminho):
    chave = os.environ.get("GROQ_API_KEY")
    if not chave:
        raise Falha("GROQ_SEM_CHAVE", "GROQ_API_KEY nao esta definida no ambiente")
    with open(caminho, "rb") as f:
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": "Bearer " + chave},
            files={"file": ("audio.ogg", f, "audio/ogg")},
            data={"model": MODELO, "language": "pt", "response_format": "verbose_json"},
            timeout=600,
        )
    if r.status_code != 200:
        raise Falha("GROQ_RECUSOU", "%s: %s" % (r.status_code, r.text))
    return r.json()


# ------------------------------------------------------------------ worker

class Motor:
    def __init__(self, db):
        self.db = db
        self.fila = queue.Queue()
        threading.Thread(target=self._laco, daemon=True).start()
        threading.Thread(target=self._resgatar, daemon=True).start()

    def enfileirar(self, trabalho_id):
        self.fila.put(trabalho_id)

    def _laco(self):
        while True:
            trabalho_id = self.fila.get()
            try:
                self._executar(trabalho_id)
            except Falha as f:
                self._marcar_erro(trabalho_id, f.codigo, f.detalhe)
            except Exception as e:
                # Falha de um trabalho nunca derruba o worker nem os outros.
                self._marcar_erro(trabalho_id, "FALHA_INESPERADA", repr(e))

    def _resgatar(self):
        """A instancia Free dorme depois de 15 minutos parada, e quem estava no
        meio de um trabalho acorda preso. Ao subir, a fila e relida do banco.

        Releitura so no arranque, de proposito: varredura periodica gastaria
        leitura do Firestore o dia inteiro pra achar nada na maior parte das
        vezes, que e o gatilho E1 chegando pela porta dos fundos."""
        time.sleep(5)
        presos = ["na fila", "baixando", "convertendo", "transcrevendo"]
        try:
            consulta = self.db.collection(TRABALHOS).where(
                filter=firestore.FieldFilter("estado", "in", presos)
            ).limit(50).stream()
            for doc in consulta:
                print("resgatando trabalho %s" % doc.id, flush=True)
                self.enfileirar(doc.id)
        except Exception as e:
            print("nao deu pra resgatar trabalhos presos: %r" % e, flush=True)

    def _andar(self, trabalho_id, estado, extra=None):
        campos = {"estado": estado, "atualizadoEm": firestore.SERVER_TIMESTAMP}
        campos.update(extra or {})
        self.db.collection(TRABALHOS).document(trabalho_id).update(campos)

    def _marcar_erro(self, trabalho_id, codigo, detalhe):
        print("trabalho %s falhou: %s" % (trabalho_id, codigo), flush=True)
        self._andar(trabalho_id, "erro", {"codigo": codigo, "detalhe": detalhe[:1500]})

    def _executar(self, trabalho_id):
        ref = self.db.collection(TRABALHOS).document(trabalho_id)
        doc = ref.get()
        if not doc.exists:
            return
        dados = doc.to_dict()
        if dados.get("estado") in ("pronto", "erro"):
            return
        url = dados["url"]
        plataforma = dados["plataforma"]

        with tempfile.TemporaryDirectory() as pasta:
            self._andar(trabalho_id, "baixando")
            inicio = time.monotonic()

            texto = tentar_legenda(url, pasta)
            if texto:
                self._concluir(trabalho_id, dados, texto, "legenda automatica",
                               {"segundosTotal": round(time.monotonic() - inicio, 1)})
                return
            if plataforma == "youtube":
                # Medido em 2026-09-28: o YouTube recusa IP de datacenter. Sem
                # legenda nao ha caminho, e tentar o download so gasta tempo.
                raise Falha("YOUTUBE_BLOQUEADO",
                            "sem legenda automatica acessivel, e o download e recusado a partir da nuvem")

            bruto = baixar_audio(url, plataforma, pasta)
            tempos = {"segundosDownload": round(time.monotonic() - inicio, 1)}

            self._andar(trabalho_id, "convertendo", tempos)
            inicio = time.monotonic()
            comprimido = comprimir(bruto, pasta)
            tempos["segundosConversao"] = round(time.monotonic() - inicio, 1)
            tempos["bytesAudio"] = os.path.getsize(comprimido)

            self._andar(trabalho_id, "transcrevendo", tempos)
            inicio = time.monotonic()
            resposta = transcrever(comprimido)
            tempos["segundosTranscricao"] = round(time.monotonic() - inicio, 1)
            tempos["duracaoAudio"] = resposta.get("duration")

            self._concluir(trabalho_id, dados, (resposta.get("text") or "").strip(),
                           "groq " + MODELO, tempos)

    def _concluir(self, trabalho_id, dados, texto, fonte, tempos):
        campos = {"texto": texto, "fonte": fonte}
        campos.update(tempos)
        self._andar(trabalho_id, "pronto", campos)
        self.db.collection(CACHE).document(chave_cache(dados["urlNormalizada"])).set({
            "urlNormalizada": dados["urlNormalizada"],
            "plataforma": dados["plataforma"],
            "texto": texto,
            "fonte": fonte,
            "duracaoAudio": tempos.get("duracaoAudio"),
            "criadoEm": firestore.SERVER_TIMESTAMP,
        })
        print("trabalho %s pronto, %d caracteres" % (trabalho_id, len(texto)), flush=True)


# -------------------------------------------------------------------- http

def criar_trabalho(db, motor, url):
    plataforma = plataforma_de(url)
    if not plataforma:
        raise Falha("PLATAFORMA_NAO_SUPORTADA", url)

    normalizada = normalizar(url)
    campos = {
        "url": url,
        "urlNormalizada": normalizada,
        "plataforma": plataforma,
        "criadoEm": firestore.SERVER_TIMESTAMP,
        "atualizadoEm": firestore.SERVER_TIMESTAMP,
    }

    # Link ja transcrito volta na hora e nao gasta banda, cota nem paciencia.
    # A marcacao de que veio de antes, e de quando, fica no proprio documento,
    # pra tela conseguir dizer isso em vez de fingir que transcreveu agora.
    guardado = db.collection(CACHE).document(chave_cache(normalizada)).get()
    if guardado.exists:
        antigo = guardado.to_dict()
        campos.update({
            "estado": "pronto",
            "texto": antigo.get("texto", ""),
            "fonte": antigo.get("fonte"),
            "duracaoAudio": antigo.get("duracaoAudio"),
            "veioDoCache": True,
            "transcritoEm": antigo.get("criadoEm"),
        })
        ref = db.collection(TRABALHOS).document()
        ref.set(campos)
        return ref.id, "pronto"

    campos["estado"] = "na fila"
    campos["veioDoCache"] = False
    ref = db.collection(TRABALHOS).document()
    ref.set(campos)
    motor.enfileirar(ref.id)
    return ref.id, "na fila"


AJUDA = (
    "Decupa, backend da Fase 2.\n\n"
    "POST /transcrever    corpo JSON com url, devolve o id do trabalho\n"
    "GET  /trabalho/<id>  estado e resultado daquele trabalho\n\n"
    "Instagram, TikTok e Facebook. YouTube recusa pedido vindo de nuvem.\n"
)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_OPTIONS(self):
        self.responder(204, "")

    def do_GET(self):
        caminho = urlparse(self.path).path
        if caminho.startswith("/trabalho/"):
            trabalho_id = caminho[len("/trabalho/"):]
            doc = self.server.db.collection(TRABALHOS).document(trabalho_id).get()
            if not doc.exists:
                self.json(404, {"codigo": "TRABALHO_NAO_ENCONTRADO"})
                return
            dados = doc.to_dict()
            dados["id"] = doc.id
            self.json(200, dados)
            return
        self.responder(200, AJUDA)

    def do_POST(self):
        if urlparse(self.path).path != "/transcrever":
            self.json(404, {"codigo": "ROTA_NAO_ENCONTRADA"})
            return
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho > LIMITE_CORPO:
            self.json(413, {"codigo": "CORPO_GRANDE_DEMAIS"})
            return
        try:
            corpo = json.loads(self.rfile.read(tamanho) or b"{}")
            url = (corpo.get("url") or "").strip()
        except Exception:
            self.json(400, {"codigo": "CORPO_INVALIDO"})
            return
        if not url:
            self.json(400, {"codigo": "URL_AUSENTE"})
            return
        try:
            trabalho_id, estado = criar_trabalho(self.server.db, self.server.motor, url)
        except Falha as f:
            self.json(400, {"codigo": f.codigo, "detalhe": f.detalhe})
            return
        self.json(202, {"id": trabalho_id, "estado": estado})

    def json(self, codigo, corpo):
        self.responder(codigo, json.dumps(corpo, ensure_ascii=False, default=str, indent=2),
                       "application/json; charset=utf-8")

    def responder(self, codigo, corpo, tipo="text/plain; charset=utf-8"):
        dados = corpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        # A tela mora no GitHub Pages e o backend aqui, entao sao origens
        # diferentes. Sem isto, a Fase 3 nao consegue falar com a Fase 2.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        if dados:
            self.wfile.write(dados)

    def log_message(self, formato, *args):
        print("%s - %s" % (self.address_string(), formato % args), flush=True)


def abrir_firestore():
    bruto = os.environ.get("FIREBASE_CREDENCIAL")
    if not bruto:
        raise RuntimeError("FIREBASE_CREDENCIAL nao esta definida no ambiente")
    firebase_admin.initialize_app(credentials.Certificate(json.loads(bruto)))
    return firestore.client()


if __name__ == "__main__":
    db = abrir_firestore()
    servidor = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), Handler)
    servidor.db = db
    servidor.motor = Motor(db)
    print("decupa ouvindo na porta %s" % servidor.server_address[1], flush=True)
    servidor.serve_forever()
