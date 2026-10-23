"""Decupa, backend da Fase 2.

Recebe um link, devolve um id na hora, e faz o trabalho pesado depois. Quem
pediu acompanha o andamento pelo Firestore em vez de ficar segurando uma
conexao aberta: download mais transcricao levam de 10 a 60 segundos e podem
falhar, e requisicao sincrona que espera isso estoura timeout de proxy e
deixa a pessoa olhando pra tela parada.

Cresceu a partir de prova.py, que era a Fase 1 e ja cumpriu o papel dela.
"""

import glob
import hmac
import hashlib
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import secrets
import math
import shutil
import wave
from contextlib import closing
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

import requests
import firebase_admin
from firebase_admin import credentials, firestore

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
SESSAO_SEGUNDOS = 24 * 60 * 60
MODELO = "whisper-large-v3-turbo"
TRABALHOS = "trabalhos"
LIMITES = "limites"
LIMITE_CORPO = 4096
LIMITE_ENVIO_BYTES = 50 * 1024 * 1024
FATIA_ENVIO_BYTES = 8 * 1024 * 1024
TRECHO_SEGUNDOS = 600
SOBREPOSICAO_SEGUNDOS = 2
EXTENSOES_AUDIO = (".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac", ".webm", ".wma", ".aif", ".aiff")

# Teto do anonimo. Cobre uso curioso e mata uso abusivo. Link que volta do
# cache nao conta, porque nao custa banda, nem cota, nem risco de bloqueio.
# Zero significa sem teto. Decisao dele em 28/09, revogando os 5 do plano.
#
# Com o teto desligado nada de IP e lido nem guardado, e a unica protecao que
# sobra e o Turnstile: bom contra robo, nenhum contra gente. Quem descobrir o
# endereco pode consumir as 8 horas de audio que a Groq da por dia.
#
# O mecanismo fica escrito e testado. Voltar a ligar e trocar este numero.
LIMITE_DIA = 0

# Transcricao que falhou nao e transcricao. Cobrar por ela faz a pessoa pagar
# por defeito nosso, que foi exatamente o que aconteceu em 28/09: dois bugs
# meus consumiram duas das cinco do dia dele sem entregar nada.
#
# As duas excecoes sao onde o download chegou a acontecer de verdade. Devolver
# tambem nesses casos abriria a porta pra alguem gastar banda a vontade
# mandando link quebrado em laco, sem nunca tocar no teto.
SEM_DEVOLUCAO = ("DOWNLOAD_FALHOU", "INSTAGRAM_BLOQUEADO")

# Nao ha limite de duracao. Decisao dele em 28/09, depois de um reel de 2
# minutos ser recusado por um filtro meu escrito errado. O unico teto que
# sobra e fisico: a Groq recusa arquivo acima de 25 MB na camada gratuita,
# o que em opus 16 kbps da cerca de tres horas e meia de audio.
LIMITE_GROQ_BYTES = 24 * 1024 * 1024

# A transcricao nao fica guardada. A tela apaga o trabalho assim que recebe o
# texto; esta janela e so o teto pra quem fechou a aba antes disso.
JANELA_LEITURA = 30  # minutos

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


# ------------------------------------------------------------------- o teto

def ip_de(handler):
    """CF-Connecting-IP, e nao X-Forwarded-For.

    Medido no servico no ar em 28/09, porque eu tinha errado duas vezes
    supondo. O Render fica atras da Cloudflare, e o que chega aqui e:

        sem forjar     XFF: 177.55.67.61, 104.23.254.80, 10.27.119.248
        forjando XFF   XFF: 198.51.100.77, 177.55.67.61, 172.71.238.190, ...

    O valor inventado entra NA FRENTE e o IP real fica no meio da fila, entao
    nem o primeiro nem o ultimo do X-Forwarded-For servem. O CF-Connecting-IP
    ficou 177.55.67.61 nos dois casos, e tentar forjar ELE faz a propria
    Cloudflare recusar o pedido com "error code: 1000" antes de chegar aqui.

    Sem CF-Connecting-IP, cai pro endereco da conexao, que atras deste proxy e
    sempre 127.0.0.1: o teto vira um so pra todo mundo. E restritivo demais de
    proposito. Se a Cloudflare sumir da frente, e melhor o teto apertar e
    aparecer no log do que abrir em silencio.
    """
    for cabecalho in ("CF-Connecting-IP", "True-Client-IP"):
        valor = (handler.headers.get(cabecalho) or "").strip()
        if valor:
            return valor
    print("sem CF-Connecting-IP: o teto vai contar todo mundo junto", flush=True)
    return handler.client_address[0]


def chave_ip(ip):
    """Guarda o resumo, nunca o endereco. O teto so precisa saber se e a mesma
    pessoa de antes, e nao precisa saber quem ela e."""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:32]


def balde_de(db, chave):
    return db.collection(LIMITES).document(time.strftime("%Y-%m-%d") + "_" + chave)


def devolver_ao_teto(db, chave, codigo):
    if LIMITE_DIA <= 0 or not chave or codigo in SEM_DEVOLUCAO:
        return
    balde_de(db, chave).set({"contagem": firestore.Increment(-1)}, merge=True)


def cobrar_do_teto(db, ip):
    if LIMITE_DIA <= 0:
        return
    hoje = time.strftime("%Y-%m-%d")
    ref = db.collection(LIMITES).document(hoje + "_" + chave_ip(ip))
    doc = ref.get()
    usados = (doc.to_dict() or {}).get("contagem", 0) if doc.exists else 0
    if usados >= LIMITE_DIA:
        raise Falha("LIMITE_DIARIO_ATINGIDO",
                    "%d de %d hoje" % (usados, LIMITE_DIA))
    ref.set({
        "contagem": firestore.Increment(1),
        "dia": hoje,
        "atualizadoEm": firestore.SERVER_TIMESTAMP,
    }, merge=True)


def conferir_turnstile(token):
    """So vale quando a chave existe no ambiente. Enquanto ela nao existir, o
    Turnstile fica desligado e o teto por IP segura sozinho."""
    segredo = os.environ.get("TURNSTILE_SECRET")
    if not segredo:
        return
    if not token:
        raise Falha("DESAFIO_FALTANDO", "a tela nao mandou o token do Turnstile")
    try:
        r = requests.post(TURNSTILE_URL, data={"secret": segredo, "response": token}, timeout=15)
        aceito = r.ok and r.json().get("success")
    except (requests.RequestException, ValueError):
        raise Falha("DESAFIO_INDISPONIVEL", "nao foi possivel consultar o Turnstile")
    if not aceito:
        raise Falha("DESAFIO_RECUSADO", "o Turnstile nao validou este acesso")


def criar_sessao():
    expira = int(time.time()) + SESSAO_SEGUNDOS
    corpo = "%d.%s" % (expira, secrets.token_urlsafe(24))
    segredo = os.environ["TURNSTILE_SECRET"].encode("utf-8")
    assinatura = hmac.new(segredo, corpo.encode("utf-8"), hashlib.sha256).hexdigest()
    return corpo + "." + assinatura, expira


def conferir_sessao(token):
    segredo = os.environ.get("TURNSTILE_SECRET")
    if not segredo:
        return
    try:
        expira, nonce, assinatura = token.split(".")
        corpo = expira + "." + nonce
        esperada = hmac.new(segredo.encode("utf-8"), corpo.encode("utf-8"), hashlib.sha256).hexdigest()
        if int(expira) > time.time() and hmac.compare_digest(assinatura, esperada):
            return
    except (ValueError, AttributeError):
        pass
    raise Falha("SESSAO_EXPIRADA", "verifique o acesso novamente")


# ---------------------------------------------------------------- pipeline

class Falha(Exception):
    def __init__(self, codigo, detalhe=""):
        super().__init__(codigo)
        self.codigo = codigo
        self.detalhe = detalhe[:1500]


def rodar(cmd, pasta):
    p = subprocess.run(cmd, cwd=pasta, capture_output=True, text=True, timeout=600)
    fim = (p.stderr or p.stdout or "").strip().splitlines()
    formato = [linha for linha in (p.stdout or "").splitlines() if "Downloading 1 format(s)" in linha]
    return p.returncode == 0, "\n".join(formato[-1:] + fim[-5:])


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


def escolher_com_audio(candidatos):
    """Quando nao existe trilha de audio pronta, o yt-dlp baixa video e audio
    separados e junta depois, deixando varios arquivos na pasta: um so com
    video, um so com audio, e o resultado. Pegar o primeiro da lista pega o
    que a ordem alfabetica quiser, e em 28/09 pegou o pedaco sem audio.

    Entao a escolha e por conteudo, nao por nome. Entre os que tem audio, o
    menor: se a trilha ficou separada, ela pesa menos que o arquivo com video
    junto, e converter ela custa menos.
    """
    com_audio = [c for c in candidatos if tem_audio(c)]
    if not com_audio:
        return None
    return min(com_audio, key=os.path.getsize)


def tem_audio(caminho):
    """Confere o arquivo recebido, nao o rotulo de formato do yt-dlp."""
    p = subprocess.run(
        ["ffprobe", "-protocol_whitelist", "file,pipe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", caminho],
        capture_output=True, text=True, timeout=60)
    return "audio" in (p.stdout or "")


def duracao_de(caminho):
    p = subprocess.run(
        ["ffprobe", "-protocol_whitelist", "file,pipe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", caminho],
        capture_output=True, text=True, timeout=60)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


# So audio com mais de cinco minutos passa pelo corte de silencio.
DURACAO_PARA_CORTAR_SILENCIO = 300


def baixar_audio(url, plataforma, pasta):
    """Tenta a trilha de audio; se o que vier nao tiver audio, baixa o video
    inteiro e deixa o ffmpeg tirar o audio dele.

    A primeira tentativa economiza banda quando funciona. A segunda e a que
    nao tem como dar errado, e existe porque a primeira deu errado tres vezes
    em 28/09: o yt-dlp entregou formato so de video quando pedimos "a melhor
    trilha de audio", e nao ha jeito de confiar nesse rotulo.
    """
    tentativas = [
        ("trilha de audio", "bestaudio*[acodec!=none]/bestaudio"),
        ("video inteiro", "bestvideo*+bestaudio/best"),
    ]

    detalhe_final = ""
    for nome, formato in tentativas:
        sala = os.path.join(pasta, nome.split()[0])
        os.makedirs(sala, exist_ok=True)

        try:
            _, detalhe = rodar([
                "yt-dlp", "-f", formato, "--no-playlist", "-o", "audio.%(ext)s", url,
            ], sala)
        except subprocess.TimeoutExpired:
            raise Falha("DOWNLOAD_FALHOU", "a plataforma nao respondeu ao download em 10 minutos")
        detalhe_final = detalhe or detalhe_final

        baixados = [a for a in glob.glob(os.path.join(sala, "audio.*")) if not a.endswith(".ogg")]

        # Um formato indisponivel pode falhar sem arquivo enquanto o video
        # completo continua acessivel. Bloqueio de acesso, sim, nao melhora.
        if not baixados:
            if codigo_da_falha(plataforma, detalhe) == "INSTAGRAM_BLOQUEADO" or nome == "video inteiro":
                raise Falha(codigo_da_falha(plataforma, detalhe), detalhe)
            continue

        escolhido = escolher_com_audio(baixados)
        if escolhido:
            print("%s: %d arquivo(s), usando %s" % (nome, len(baixados), os.path.basename(escolhido)), flush=True)
            return escolhido

        print("%s veio sem audio, tentando o proximo caminho" % nome, flush=True)

    raise Falha("SEM_AUDIO",
                "o video foi baixado inteiro e mesmo assim nao tem faixa de audio. " + detalhe_final)


def comprimir(entrada, pasta):
    saida = os.path.join(pasta, "audio.ogg")

    if not tem_audio(entrada):
        raise Falha("SEM_AUDIO", "o arquivo recebido nao tem faixa de audio legivel")

    filtros = []
    if duracao_de(entrada) > DURACAO_PARA_CORTAR_SILENCIO:
        # Corta so o silencio que passa de um segundo, e guarda um segundo
        # dele. Pausa curta de fala continua inteira, que e o que o Whisper
        # usa pra pontuar.
        filtros = ["-af", "silenceremove=stop_periods=-1:stop_duration=1:stop_threshold=-45dB"]

    ok, detalhe = rodar([
        "ffmpeg", "-y", "-protocol_whitelist", "file,pipe", "-i", entrada, "-vn", *filtros,
        "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "16k", saida,
    ], pasta)
    if not ok or not os.path.exists(saida):
        raise Falha("CONVERSAO_FALHOU", detalhe)

    # Nao e limite de tempo, e o teto de arquivo da Groq. Sem esta checagem o
    # video muito longo voltaria como "GROQ_RECUSOU 413", que nao diz nada.
    peso = os.path.getsize(saida)
    if peso > LIMITE_GROQ_BYTES:
        raise Falha("AUDIO_GRANDE_DEMAIS",
                    "%.1f MB depois de comprimido, e o motor aceita %d MB"
                    % (peso / 1048576, LIMITE_GROQ_BYTES // 1048576))
    return saida


def partes_de_audio(entrada, pasta):
    """Decodifica uma vez; corta por amostras, sem remover pausas.

    WAV mono 16 kHz: ate 604 s / 19,4 MB por requisicao. As janelas
    sobrepoem dois segundos de cada lado da divisao nominal de dez minutos.
    So uma janela fica no disco e na memoria por vez.
    """
    taxa = 16000
    passo = TRECHO_SEGUNDOS * taxa * 2
    margem = SOBREPOSICAO_SEGUNDOS * taxa * 2
    with tempfile.TemporaryFile() as erros:
        processo = subprocess.Popen([
            "ffmpeg", "-v", "error", "-protocol_whitelist", "file,pipe", "-i", entrada,
            "-map", "0:a:0", "-ac", "1", "-ar", str(taxa), "-f", "s16le", "pipe:1",
        ], stdout=subprocess.PIPE, stderr=erros)
        try:
            janela = bytearray()
            inicio = 0
            primeira = True
            while True:
                alvo = passo + margem if primeira else passo + 2 * margem
                novos = 0
                while len(janela) < alvo:
                    bloco = processo.stdout.read(min(alvo - len(janela), 64 * 1024))
                    if not bloco:
                        break
                    janela.extend(bloco)
                    novos += len(bloco)
                if not novos:
                    break
                caminho = os.path.join(pasta, "trecho.wav")
                with wave.open(caminho, "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(taxa)
                    audio.writeframes(janela)
                yield caminho, inicio / (taxa * 2), len(janela) / (taxa * 2)
                if len(janela) < alvo:
                    break
                inicio += len(janela) - 2 * margem
                janela = janela[-2 * margem:]
                primeira = False
            if processo.wait(timeout=60):
                erros.seek(0)
                raise Falha("CONVERSAO_FALHOU", erros.read()[-1500:].decode("utf-8", errors="replace"))
            if primeira and not novos:
                raise Falha("SEM_AUDIO", "o arquivo nao produziu amostras de audio")
        finally:
            if processo.poll() is None:
                processo.kill()
                processo.wait()
            processo.stdout.close()


def unir_palavras(anteriores, novas, corte):
    """Alinha so a vizinhanca do corte; repeticoes fora dela sao mantidas."""
    if not anteriores:
        return novas
    if not novas:
        return anteriores
    margem = SOBREPOSICAO_SEGUNDOS + 1
    esquerda = [(i, p) for i, p in enumerate(anteriores) if p["end"] >= corte - margem]
    direita = [(i, p) for i, p in enumerate(novas) if p["start"] <= corte + margem]
    normalizar_palavra = lambda p: re.sub(r"\W", "", p["word"].casefold())
    comparador = SequenceMatcher(None, [normalizar_palavra(p) for _, p in esquerda],
                                [normalizar_palavra(p) for _, p in direita], autojunk=False)
    ancoras = []
    for bloco in comparador.get_matching_blocks():
        for deslocamento in range(bloco.size):
            i, a = esquerda[bloco.a + deslocamento]
            j, b = direita[bloco.b + deslocamento]
            if normalizar_palavra(a) and abs(a["start"] - b["start"]) < 1:
                ancoras.append((abs((a["end"] + b["end"]) / 2 - corte), i, j))
    if ancoras:
        _, i, j = min(ancoras)
        return anteriores[:i + 1] + novas[j + 1:]
    # Se o motor reconheceu palavras diferentes, divide pela posicao temporal
    # no centro da sobreposicao; nunca elimina frases repetidas por texto so.
    return ([p for p in anteriores if (p["start"] + p["end"]) / 2 < corte]
            + [p for p in novas if (p["start"] + p["end"]) / 2 >= corte])


def transcrever(caminho, palavras=False):
    chave = os.environ.get("GROQ_API_KEY")
    if not chave:
        raise Falha("GROQ_SEM_CHAVE", "GROQ_API_KEY nao esta definida no ambiente")
    dados = {"model": MODELO, "language": "pt", "response_format": "verbose_json"}
    if palavras:
        dados["timestamp_granularities[]"] = ["word", "segment"]
    nome = os.path.basename(caminho)
    with open(caminho, "rb") as f:
        for tentativa in range(3 if palavras else 1):
            f.seek(0)
            r = requests.post(
                GROQ_URL,
                headers={"Authorization": "Bearer " + chave},
                files={"file": (nome, f, "audio/wav" if nome.endswith(".wav") else "audio/ogg")},
                data=dados,
                timeout=600,
            )
            if r.status_code != 429 or not palavras or tentativa == 2:
                break
            try:
                espera = float(r.headers.get("retry-after", "5"))
            except ValueError:
                espera = 5
            if not math.isfinite(espera) or espera > 60:
                break  # Cota longa: devolver o erro em vez de prender a fila.
            time.sleep(max(1, espera))
    if r.status_code != 200:
        raise Falha("GROQ_RECUSOU", "%s: %s" % (r.status_code, r.text))
    return r.json()


# ------------------------------------------------------------------ worker

class Motor:
    def __init__(self, db):
        self.db = db
        self.fila = queue.Queue()
        self.arquivos = {}
        self.envios = {}
        self.trava_envios = threading.Lock()
        threading.Thread(target=self._laco, daemon=True).start()
        threading.Thread(target=self._resgatar, daemon=True).start()
        threading.Thread(target=self._varrer_sempre, daemon=True).start()

    def enfileirar(self, trabalho_id):
        self.fila.put(trabalho_id)

    def enfileirar_audio(self, nome, tamanho, pasta, ip, trabalho_id=None, dono=None):
        ref = self.db.collection(TRABALHOS).document(trabalho_id) if trabalho_id else self.db.collection(TRABALHOS).document()
        self.arquivos[ref.id] = pasta
        try:
            ref.set({
                "plataforma": "arquivo", "nomeArquivo": nome[:200],
                "bytesEnviados": tamanho, "estado": "na fila",
                "envioDono": dono,
                "chaveIp": chave_ip(ip) if LIMITE_DIA > 0 else None,
                "criadoEm": firestore.SERVER_TIMESTAMP,
                "atualizadoEm": firestore.SERVER_TIMESTAMP,
            })
        except Exception:
            self.arquivos.pop(ref.id, None)
            raise
        self.enfileirar(ref.id)
        return ref.id

    def limpar_envios(self):
        with self.trava_envios:
            for chave, envio in list(self.envios.items()):
                if time.monotonic() - envio["atualizado"] > JANELA_LEITURA * 60 and envio["trava"].acquire(blocking=False):
                    try:
                        if envio["pasta"]:
                            envio["pasta"].cleanup()
                        envio["cancelado"] = True
                        del self.envios[chave]
                    finally:
                        envio["trava"].release()

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
            finally:
                pasta = self.arquivos.pop(trabalho_id, None)
                if pasta:
                    pasta.cleanup()

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
                if doc.to_dict().get("plataforma") == "arquivo":
                    if doc.id not in self.arquivos:
                        self._marcar_erro(doc.id, "ARQUIVO_INDISPONIVEL",
                                          "o servidor reiniciou; envie o arquivo novamente")
                    continue
                print("resgatando trabalho %s" % doc.id, flush=True)
                self.enfileirar(doc.id)
        except Exception as e:
            print("nao deu pra resgatar trabalhos presos: %r" % e, flush=True)

    def _varrer_sempre(self):
        """A tela apaga o trabalho assim que recebe o texto. Esta varredura e
        pra quem fechou a aba antes disso: nada pode sobreviver a janela de
        leitura, porque a promessa e que a transcricao nao fica guardada.

        A cada 30 minutos, e nao a cada 2 segundos: varredura apertada gastaria
        a cota gratuita do Firestore o dia inteiro pra achar nada (gatilho E1).
        """
        while True:
            time.sleep(1800)
            try:
                self._varrer()
            except Exception as e:
                print("varredura falhou: %r" % e, flush=True)

    def _varrer(self):
        self.limpar_envios()
        limite = datetime.now(timezone.utc) - timedelta(minutes=JANELA_LEITURA)
        velhos = self.db.collection(TRABALHOS).where(
            filter=firestore.FieldFilter("atualizadoEm", "<", limite)
        ).limit(300).stream()
        apagados = 0
        for doc in velhos:
            if doc.id in self.arquivos:
                continue  # Arquivo ainda na fila ou em processamento.
            doc.reference.delete()
            apagados += 1

        # A contagem por conexao vale um dia. Guardar ontem nao serve pra nada
        # e a politica de privacidade promete que ela some sozinha.
        ontem = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
        for doc in self.db.collection(LIMITES).where(
            filter=firestore.FieldFilter("dia", "<", ontem)
        ).limit(300).stream():
            doc.reference.delete()
            apagados += 1

        if apagados:
            print("varredura apagou %d documentos" % apagados, flush=True)

    def _andar(self, trabalho_id, estado, extra=None):
        campos = {"estado": estado, "atualizadoEm": firestore.SERVER_TIMESTAMP}
        campos.update(extra or {})
        self.db.collection(TRABALHOS).document(trabalho_id).update(campos)

    def _marcar_erro(self, trabalho_id, codigo, detalhe):
        print("trabalho %s falhou: %s" % (trabalho_id, codigo), flush=True)
        self._andar(trabalho_id, "erro", {"codigo": codigo, "detalhe": detalhe[:1500]})
        try:
            doc = self.db.collection(TRABALHOS).document(trabalho_id).get()
            if doc.exists:
                devolver_ao_teto(self.db, (doc.to_dict() or {}).get("chaveIp"), codigo)
        except Exception as e:
            print("nao deu pra devolver ao teto: %r" % e, flush=True)

    def _executar(self, trabalho_id):
        ref = self.db.collection(TRABALHOS).document(trabalho_id)
        doc = ref.get()
        if not doc.exists:
            return
        dados = doc.to_dict()
        if dados.get("estado") in ("pronto", "erro"):
            return
        url = dados.get("url")
        plataforma = dados["plataforma"]

        with tempfile.TemporaryDirectory() as pasta:
            inicio = time.monotonic()

            if plataforma != "arquivo":
                self._andar(trabalho_id, "baixando")

            if plataforma == "youtube":
                texto = tentar_legenda(url, pasta)
                if texto:
                    self._concluir(trabalho_id, dados, texto, "legenda automatica",
                                   {"segundosTotal": round(time.monotonic() - inicio, 1)})
                    return
                # Medido em 2026-09-28: o YouTube recusa IP de datacenter. Sem
                # legenda nao ha caminho, e tentar o download so gasta tempo.
                raise Falha("YOUTUBE_BLOQUEADO",
                            "sem legenda automatica acessivel, e o download e recusado a partir da nuvem")

            if plataforma == "arquivo":
                envio = self.arquivos.get(trabalho_id)
                if not envio:
                    raise Falha("ARQUIVO_INDISPONIVEL", "envie o arquivo novamente")
                bruto = os.path.join(envio.name, "entrada")
                self._transcrever_arquivo(trabalho_id, dados, bruto, pasta)
                return
            else:
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

    def _transcrever_arquivo(self, trabalho_id, dados, entrada, pasta):
        self._andar(trabalho_id, "convertendo")
        if not tem_audio(entrada):
            raise Falha("SEM_AUDIO", "o arquivo recebido nao tem faixa de audio legivel")
        duracao = duracao_de(entrada)
        total = max(1, math.ceil((duracao - SOBREPOSICAO_SEGUNDOS) / TRECHO_SEGUNDOS))
        palavras = []
        fim = 0
        partes = 0
        bytes_audio = 0
        inicio = time.monotonic()
        with closing(partes_de_audio(entrada, pasta)) as trechos:
            for indice, (caminho, deslocamento, segundos) in enumerate(trechos, 1):
                self._andar(trabalho_id, "transcrevendo", {"parteAtual": indice, "partesTotal": max(total, indice)})
                if os.path.getsize(caminho) > LIMITE_GROQ_BYTES:
                    raise Falha("AUDIO_GRANDE_DEMAIS", "um trecho excedeu o limite de transcricao")
                bytes_audio += os.path.getsize(caminho)
                try:
                    resposta = transcrever(caminho, palavras=True)
                except Falha as erro:
                    raise Falha(erro.codigo, "trecho %d: %s" % (indice, erro.detalhe))
                if resposta.get("text", "").strip() and not resposta.get("words"):
                    raise Falha("TEMPOS_AUSENTES", "o motor nao devolveu os tempos das palavras do trecho %d" % indice)
                novas = [{"word": p["word"], "start": float(p["start"]) + deslocamento,
                          "end": float(p["end"]) + deslocamento} for p in resposta.get("words", [])]
                palavras = unir_palavras(palavras, novas, (indice - 1) * TRECHO_SEGUNDOS)
                fim = deslocamento + segundos
                partes = indice
        texto = " ".join(p["word"].strip() for p in palavras).strip()
        self._concluir(trabalho_id, dados, texto, "groq " + MODELO,
                       {"duracaoAudio": round(fim, 3), "partesTotal": partes, "parteAtual": partes,
                        "bytesAudio": bytes_audio,
                        "segundosTranscricao": round(time.monotonic() - inicio, 1)})

    def _concluir(self, trabalho_id, dados, texto, fonte, tempos):
        campos = {"texto": texto, "fonte": fonte}
        campos.update(tempos)
        self._andar(trabalho_id, "pronto", campos)
        print("trabalho %s pronto, %d caracteres" % (trabalho_id, len(texto)), flush=True)


# -------------------------------------------------------------------- http

def criar_trabalho(db, motor, url, ip, sessao, desafio):
    plataforma = plataforma_de(url)
    if not plataforma:
        raise Falha("PLATAFORMA_NAO_SUPORTADA", url)

    normalizada = normalizar(url)
    campos = {
        "url": url,
        "urlNormalizada": normalizada,
        "plataforma": plataforma,
        "chaveIp": chave_ip(ip) if LIMITE_DIA > 0 else None,
        "criadoEm": firestore.SERVER_TIMESTAMP,
        "atualizadoEm": firestore.SERVER_TIMESTAMP,
    }

    # Paginas antigas ainda no cache mandam o token direto ate receberem o JS novo.
    if sessao or not desafio:
        conferir_sessao(sessao)
    else:
        conferir_turnstile(desafio)
    cobrar_do_teto(db, ip)

    campos["estado"] = "na fila"
    ref = db.collection(TRABALHOS).document()
    ref.set(campos)
    motor.enfileirar(ref.id)
    return ref.id, "na fila"


AJUDA = (
    "Decupa, backend da Fase 2.\n\n"
    "POST /transcrever    corpo JSON com url, devolve o id do trabalho\n"
    "POST /transcrever-arquivo  audio binario, X-Decupa-Nome e X-Decupa-Sessao\n"
    "GET  /trabalho/<id>  estado e resultado daquele trabalho\n"
    "GET  /config         o que a tela precisa saber antes de pedir\n"
    "DEL  /trabalho/<id>  apaga o trabalho; a tela chama ao receber o texto\n\n"
    "A transcricao nao fica guardada. Sem cache e sem historico.\n"
    "Instagram, TikTok e Facebook. YouTube recusa pedido vindo de nuvem.\n"
)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_OPTIONS(self):
        self.responder(204, "")

    def do_GET(self):
        caminho = urlparse(self.path).path
        if caminho == "/config":
            # A tela pergunta se o desafio esta ligado em vez de trazer a chave
            # escrita no codigo. Assim ligar o Turnstile e mexer no Render, nao
            # publicar versao nova do site.
            self.json(200, {
                "versao": "2026-10-23.2",
                "turnstile": os.environ.get("TURNSTILE_SITEKEY") or None,
                "limiteDia": LIMITE_DIA or None,
                "limiteEnvioBytes": LIMITE_ENVIO_BYTES,
                "fatiaEnvioBytes": FATIA_ENVIO_BYTES,
                "envioEmPartes": True,
            })
            return
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

    def do_DELETE(self):
        """A tela chama isto assim que recebe o texto. E o "ao fim do uso ela
        ja some": o servidor esquece, e a copia que sobra e a do navegador de
        quem pediu. Nao exige prova de dono porque o id sorteado ja e a prova,
        e o pior caso de apagar um id alheio e o dono perder o proprio texto,
        que e o mesmo destino que ele teria em 30 minutos."""
        caminho = urlparse(self.path).path
        if caminho.startswith("/envio/"):
            self.cancelar_envio(caminho[len("/envio/"):])
            return
        if not caminho.startswith("/trabalho/"):
            self.json(404, {"codigo": "ROTA_NAO_ENCONTRADA"})
            return
        trabalho_id = caminho[len("/trabalho/"):]
        if "/" in trabalho_id or not trabalho_id:
            self.json(400, {"codigo": "ID_INVALIDO"})
            return
        self.server.db.collection(TRABALHOS).document(trabalho_id).delete()
        self.json(200, {"apagado": True})

    def do_POST(self):
        caminho = urlparse(self.path).path
        if caminho == "/transcrever-arquivo":
            self.receber_arquivo()
            return
        if caminho not in ("/transcrever", "/sessao"):
            self.json(404, {"codigo": "ROTA_NAO_ENCONTRADA"})
            return
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho > LIMITE_CORPO:
            self.json(413, {"codigo": "CORPO_GRANDE_DEMAIS"})
            return
        try:
            corpo = json.loads(self.rfile.read(tamanho) or b"{}")
            url = (corpo.get("url") or "").strip()
            token = (corpo.get("desafio") or "").strip()
            sessao = (corpo.get("sessao") or "").strip()
        except Exception:
            self.json(400, {"codigo": "CORPO_INVALIDO"})
            return
        if caminho == "/sessao":
            if not os.environ.get("TURNSTILE_SECRET"):
                self.json(404, {"codigo": "DESAFIO_DESLIGADO"})
                return
            try:
                conferir_turnstile(token)
                credencial, expira = criar_sessao()
            except Falha as f:
                self.json(400, {"codigo": f.codigo, "detalhe": f.detalhe})
                return
            self.json(200, {"sessao": credencial, "expiraEm": expira})
            return
        if not url:
            self.json(400, {"codigo": "URL_AUSENTE"})
            return
        try:
            trabalho_id, estado = criar_trabalho(
                self.server.db, self.server.motor, url, ip_de(self), sessao, token)
        except Falha as f:
            codigo = 429 if f.codigo == "LIMITE_DIARIO_ATINGIDO" else 400
            self.json(codigo, {"codigo": f.codigo, "detalhe": f.detalhe})
            return
        self.json(202, {"id": trabalho_id, "estado": estado})

    def receber_arquivo(self):
        if self.headers.get("X-Decupa-Envio") is not None:
            self.receber_parte()
            return
        # O corpo e o proprio arquivo: le em blocos para nao carregar 50 MB
        # na memoria. Autenticacao e tamanho sao conferidos antes da leitura.
        pasta = None
        try:
            conferir_sessao(self.headers.get("X-Decupa-Sessao") or "")
            try:
                tamanho = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise Falha("CORPO_INVALIDO", "Content-Length invalido")
            if tamanho <= 0:
                raise Falha("ARQUIVO_VAZIO", "selecione um arquivo com audio")
            if tamanho > LIMITE_ENVIO_BYTES:
                raise Falha("ENVIO_GRANDE_DEMAIS", "o envio aceita ate 50 MB por arquivo")
            nome = unquote(self.headers.get("X-Decupa-Nome") or "").replace("\\", "/").rsplit("/", 1)[-1]
            if not nome.lower().endswith(EXTENSOES_AUDIO):
                raise Falha("FORMATO_NAO_SUPORTADO", "selecione um arquivo de audio")
            pasta = tempfile.TemporaryDirectory(prefix="decupa-envio-")
            with open(os.path.join(pasta.name, "entrada"), "wb") as destino:
                self.ler_arquivo(destino, tamanho)
            ip = ip_de(self) if LIMITE_DIA > 0 else ""
            cobrar_do_teto(self.server.db, ip)
            trabalho_id = self.server.motor.enfileirar_audio(nome, tamanho, pasta, ip)
            pasta = None  # O worker assume a limpeza, inclusive se falhar.
        except (Falha, OSError) as erro:
            self.close_connection = True  # Pode haver corpo ainda nao lido.
            codigo = erro.codigo if isinstance(erro, Falha) else "ENVIO_INTERROMPIDO"
            detalhe = erro.detalhe if isinstance(erro, Falha) else "a conexao caiu durante o envio"
            status = 413 if codigo == "ENVIO_GRANDE_DEMAIS" else 429 if codigo == "LIMITE_DIARIO_ATINGIDO" else 400
            self.json(status, {"codigo": codigo, "detalhe": detalhe})
            return
        finally:
            if pasta:
                pasta.cleanup()
        self.json(202, {"id": trabalho_id, "estado": "na fila"})

    def ler_arquivo(self, destino, tamanho):
        prazo = time.monotonic() + 180
        resumo = hashlib.sha256()
        restante = tamanho
        while restante:
            espera = prazo - time.monotonic()
            if espera <= 0:
                raise Falha("ENVIO_INTERROMPIDO", "o envio do bloco excedeu tres minutos")
            self.connection.settimeout(espera)
            bloco = self.rfile.read1(min(restante, 64 * 1024))
            if not bloco:
                raise Falha("ENVIO_INTERROMPIDO", "o bloco nao chegou inteiro")
            if destino:
                destino.write(bloco)
            resumo.update(bloco)
            restante -= len(bloco)
        self.connection.settimeout(None)
        return resumo.hexdigest()

    def receber_parte(self):
        try:
            sessao = self.headers.get("X-Decupa-Sessao") or ""
            conferir_sessao(sessao)
            chave = self.headers.get("X-Decupa-Envio") or ""
            nome = unquote(self.headers.get("X-Decupa-Nome") or "").replace("\\", "/").rsplit("/", 1)[-1]
            try:
                total = int(self.headers.get("X-Decupa-Total") or 0)
                posicao = int(self.headers.get("X-Decupa-Posicao") or 0)
                tamanho = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise Falha("CORPO_INVALIDO", "tamanho ou posicao invalidos")
            if not re.fullmatch(r"[a-f0-9]{32}", chave) or total <= 0 or total > 2**53 - 1 or posicao < 0 or tamanho <= 0 or posicao + tamanho > total:
                raise Falha("CORPO_INVALIDO", "identificador, tamanho ou posicao invalidos")
            if tamanho > LIMITE_ENVIO_BYTES:
                raise Falha("ENVIO_GRANDE_DEMAIS", "cada bloco aceita ate 50 MB")
            if not nome.lower().endswith(EXTENSOES_AUDIO):
                raise Falha("FORMATO_NAO_SUPORTADO", "selecione um arquivo de audio")
            motor = self.server.motor
            motor.limpar_envios()
            dono = hmac.new(sessao.encode(), chave.encode(), hashlib.sha256).hexdigest()
            with motor.trava_envios:
                envio = motor.envios.get(chave)
            if envio is None:
                existente = self.server.db.collection(TRABALHOS).document(chave).get()
                if existente.exists:
                    dados = existente.to_dict()
                    if dados.get("envioDono") != dono or dados.get("bytesEnviados") != total:
                        raise Falha("ENVIO_INVALIDO", "este identificador pertence a outro envio")
                    self.ler_arquivo(None, tamanho)
                    self.json(202, {"id": chave, "recebidos": total, "estado": dados["estado"]})
                    return
            with motor.trava_envios:
                envio = motor.envios.get(chave)
                if envio is None:
                    if posicao != 0:
                        raise Falha("ARQUIVO_INDISPONIVEL", "o envio expirou ou o servidor reiniciou; envie novamente")
                    pasta = tempfile.TemporaryDirectory(prefix="decupa-envio-")
                    open(os.path.join(pasta.name, "entrada"), "wb").close()
                    envio = {"nome": nome, "total": total, "dono": dono,
                             "pasta": pasta, "recebidos": 0, "blocos": {}, "concluido": False,
                             "trava": threading.Lock(), "atualizado": time.monotonic()}
                    motor.envios[chave] = envio
            with envio["trava"]:
                if envio.get("cancelado"):
                    raise Falha("ARQUIVO_INDISPONIVEL", "o envio foi cancelado; envie novamente")
                if (envio["dono"] != dono
                        or envio["nome"] != nome or envio["total"] != total):
                    raise Falha("ENVIO_INVALIDO", "este identificador pertence a outro envio")
                envio["atualizado"] = time.monotonic()
                repetido = envio["blocos"].get(posicao)
                if repetido:
                    if repetido != (tamanho, self.ler_arquivo(None, tamanho)):
                        raise Falha("ENVIO_INVALIDO", "o bloco reenviado tem conteudo diferente")
                else:
                    if posicao != envio["recebidos"]:
                        raise Falha("ENVIO_INVALIDO", "os blocos precisam chegar em ordem")
                    if shutil.disk_usage(tempfile.gettempdir()).free < tamanho + 2 * LIMITE_ENVIO_BYTES:
                        raise Falha("ESPACO_INSUFICIENTE", "o servidor esta sem espaco temporario; tente mais tarde")
                    with open(os.path.join(envio["pasta"].name, "entrada"), "r+b") as destino:
                        destino.seek(posicao)
                        try:
                            resumo = self.ler_arquivo(destino, tamanho)
                        except Exception:
                            destino.truncate(posicao)
                            raise
                    envio["blocos"][posicao] = (tamanho, resumo)
                    envio["recebidos"] += tamanho
                if envio["recebidos"] == total and not envio["concluido"]:
                    ip = ip_de(self) if LIMITE_DIA > 0 else ""
                    cobrar_do_teto(self.server.db, ip)
                    motor.enfileirar_audio(nome, total, envio["pasta"], ip, chave, dono)
                    envio["pasta"] = None  # A limpeza passa ao worker.
                    envio["concluido"] = True
                resposta = {"id": chave, "recebidos": envio["recebidos"],
                            "estado": "na fila" if envio["concluido"] else "enviando"}
                status = 202 if envio["concluido"] else 200
        except (Falha, OSError) as erro:
            self.close_connection = True
            codigo = erro.codigo if isinstance(erro, Falha) else "ENVIO_INTERROMPIDO"
            detalhe = erro.detalhe if isinstance(erro, Falha) else "o bloco nao chegou inteiro; tente novamente"
            self.json(413 if codigo == "ENVIO_GRANDE_DEMAIS" else 400, {"codigo": codigo, "detalhe": detalhe})
            return
        self.json(status, resposta)

    def cancelar_envio(self, chave):
        try:
            sessao = self.headers.get("X-Decupa-Sessao") or ""
            conferir_sessao(sessao)
            envio = self.server.motor.envios.get(chave)
            if envio:
                with envio["trava"]:
                    if envio["dono"] != hmac.new(sessao.encode(), chave.encode(), hashlib.sha256).hexdigest():
                        raise Falha("ENVIO_INVALIDO", "este identificador pertence a outro envio")
                    if envio["pasta"]:
                        envio["pasta"].cleanup()
                        envio["pasta"] = None
                        envio["cancelado"] = True
                        with self.server.motor.trava_envios:
                            self.server.motor.envios.pop(chave, None)
        except Falha as erro:
            self.json(400, {"codigo": erro.codigo})
            return
        self.json(200, {"apagado": True})

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
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Decupa-Nome, X-Decupa-Sessao, X-Decupa-Envio, X-Decupa-Total, X-Decupa-Posicao")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
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
