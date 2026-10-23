"""Contrato HTTP e ciclo do upload, sem Firestore ou Groq reais.

python -m unittest discover -s tests -v
ffmpeg e ffprobe precisam estar no PATH para os testes de conversao.
"""
import http.client
import os
from pathlib import Path
import queue
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote
import wave

import servidor as s


class Documento:
    def __init__(self, db, chave):
        self.db, self.id = db, chave
        self.reference = self

    @property
    def exists(self):
        return self.id in self.db.dados

    def get(self):
        return self

    def set(self, dados):
        self.db.dados[self.id] = dados.copy()

    def update(self, dados):
        self.db.dados[self.id].update(dados)

    def to_dict(self):
        return self.db.dados[self.id].copy()


class Banco:
    def __init__(self):
        self.dados = {}
        self.consulta = []

    def collection(self, _):
        return self

    def document(self, chave=None):
        return Documento(self, chave or str(len(self.dados) + 1))

    def where(self, **_):
        return self

    def limit(self, _):
        return self

    def stream(self):
        return self.consulta


class Upload(unittest.TestCase):
    def setUp(self):
        self.ambiente = patch.dict(os.environ, {"TURNSTILE_SECRET": "segredo-local-do-teste"})
        self.ambiente.start()
        self.db = Banco()
        self.motor = s.Motor.__new__(s.Motor)
        self.motor.db = self.db
        self.motor.fila = queue.Queue()
        self.motor.arquivos = {}
        self.motor.envios = {}
        self.motor.trava_envios = threading.Lock()
        self.http = s.ThreadingHTTPServer(("127.0.0.1", 0), s.Handler)
        self.http.db, self.http.motor = self.db, self.motor
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.sessao = s.criar_sessao()[0]

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        for pasta in list(self.motor.arquivos.values()):
            pasta.cleanup()
        for envio in self.motor.envios.values():
            if envio["pasta"]:
                envio["pasta"].cleanup()
        self.ambiente.stop()

    def pedir(self, corpo=b"audio", nome="voz.mp3", sessao=None, tamanho=None):
        conexao = http.client.HTTPConnection(*self.http.server_address, timeout=5)
        cabecalhos = {"Content-Type": "application/octet-stream", "X-Decupa-Nome": quote(nome),
                      "X-Decupa-Sessao": self.sessao if sessao is None else sessao}
        if tamanho is not None:
            cabecalhos["Content-Length"] = str(tamanho)
        conexao.request("POST", "/transcrever-arquivo", corpo, cabecalhos)
        resposta = conexao.getresponse()
        import json
        dados = json.loads(resposta.read())
        status = resposta.status
        conexao.close()
        return status, dados

    def wav(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "voz.wav"
            with wave.open(str(caminho), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\x01\x00" * 16000)
            return caminho.read_bytes()

    def executar(self, resposta=None, falha=None):
        trabalho_id = self.motor.fila.get_nowait()
        self.motor.fila.put(trabalho_id)
        pasta = self.motor.arquivos[trabalho_id].name
        # Exercita o laco real, inclusive o finally de limpeza. O motor de
        # fala e substituido para o teste nunca consumir cota externa.
        with patch.object(s, "transcrever", return_value=resposta or {"text": "Audio transcrito.", "duration": 1,
                          "words": [{"word": "Audio", "start": 0, "end": .3}, {"word": "transcrito.", "start": .3, "end": .9}]},
                          side_effect=falha), patch.object(s, "baixar_audio", side_effect=AssertionError("upload nao baixa link")):
            threading.Thread(target=self.motor._laco, daemon=True).start()
            prazo = time.monotonic() + 10
            while time.monotonic() < prazo:
                dados = self.db.dados[trabalho_id]
                if dados.get("estado") in ("erro", "pronto") and not os.path.exists(pasta):
                    break
                time.sleep(.02)
            self.assertFalse(os.path.exists(pasta), "o worker deixou o audio no disco")
        return self.db.dados[trabalho_id]

    def test_recepcao_nome_unicode_e_cors(self):
        corpo = self.wav()
        status, dados = self.pedir(corpo, "Reunião de outubro.wav")
        self.assertEqual(status, 202)
        self.assertEqual(self.db.dados[dados["id"]]["nomeArquivo"], "Reunião de outubro.wav")
        self.assertEqual((Path(self.motor.arquivos[dados["id"]].name) / "entrada").read_bytes(), corpo)
        conexao = http.client.HTTPConnection(*self.http.server_address)
        conexao.request("OPTIONS", "/transcrever-arquivo")
        resposta = conexao.getresponse()
        self.assertIn("X-Decupa-Sessao", resposta.getheader("Access-Control-Allow-Headers"))
        conexao.close()

    def test_recusa_sem_sessao_antes_de_ler_corpo(self):
        with patch.object(s.time, "time", return_value=0):
            expirada = s.criar_sessao()[0]
        for token in ("", "falsa", s.criar_sessao()[0] + "adulterada", expirada):
            status, dados = self.pedir(b"", sessao=token, tamanho=1000)
            self.assertEqual((status, dados["codigo"]), (400, "SESSAO_EXPIRADA"))
        self.assertFalse(self.db.dados)

    def test_envio_cortado_limpa_arquivo_parcial(self):
        antes = set(Path(tempfile.gettempdir()).glob("decupa-envio-*"))
        conexao = http.client.HTTPConnection(*self.http.server_address, timeout=5)
        conexao.putrequest("POST", "/transcrever-arquivo")
        for nome, valor in {"Content-Length": "1000", "X-Decupa-Nome": "voz.wav",
                            "X-Decupa-Sessao": self.sessao}.items():
            conexao.putheader(nome, valor)
        conexao.endheaders(b"parcial")
        conexao.sock.shutdown(socket.SHUT_WR)
        resposta = conexao.getresponse()
        self.assertIn(b"ENVIO_INTERROMPIDO", resposta.read())
        conexao.close()
        self.assertFalse(self.db.dados)
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("decupa-envio-*")), antes)

    def test_vazio_formato_e_limite_antes_de_ler(self):
        for argumentos, esperado in [({"corpo": b""}, "ARQUIVO_VAZIO"),
                                    ({"nome": "arquivo.exe"}, "FORMATO_NAO_SUPORTADO"),
                                    ({"corpo": b"", "tamanho": s.LIMITE_ENVIO_BYTES + 1}, "ENVIO_GRANDE_DEMAIS")]:
            status, dados = self.pedir(**argumentos)
            self.assertEqual(dados["codigo"], esperado)
            self.assertEqual(status, 413 if esperado == "ENVIO_GRANDE_DEMAIS" else 400)
        self.assertFalse(self.motor.arquivos)
        self.assertFalse(self.db.dados)

    def test_conversao_real_e_limpeza_no_sucesso(self):
        self.assertEqual(self.pedir(self.wav(), "voz.wav")[0], 202)
        dados = self.executar()
        self.assertEqual((dados["estado"], dados["texto"]), ("pronto", "Audio transcrito."))
        self.assertGreater(dados["bytesAudio"], 0)
        self.assertNotIn("url", dados)

    def test_limpeza_na_falha_do_motor(self):
        self.pedir(self.wav(), "voz.wav")
        dados = self.executar(falha=s.Falha("GROQ_RECUSOU", "falha simulada"))
        self.assertEqual((dados["estado"], dados["codigo"]), ("erro", "GROQ_RECUSOU"))

    def test_mp3_m4a_e_ogg_convertidos_de_verdade(self):
        with tempfile.TemporaryDirectory() as pasta:
            entrada = Path(pasta) / "origem.wav"
            entrada.write_bytes(self.wav())
            for extensao in ("mp3", "m4a", "ogg"):
                with self.subTest(extensao=extensao):
                    caminho = Path(pasta) / ("audio." + extensao)
                    formato = ["-c:a", "aac", "-f", "mp4"] if extensao == "m4a" else ["-c:a", "libopus"] if extensao == "ogg" else []
                    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(entrada), *formato, str(caminho)],
                                   check=True, capture_output=True)
                    saida = Path(pasta) / extensao
                    saida.mkdir()
                    comprimido = s.comprimir(str(caminho), str(saida))
                    self.assertTrue(s.tem_audio(comprimido))

    def test_conteudo_falso_nao_chega_ao_motor(self):
        self.pedir(b"nao e audio", "falso.mp3")
        dados = self.executar()
        self.assertEqual((dados["estado"], dados["codigo"]), ("erro", "SEM_AUDIO"))

    def test_reinicio_pede_reenvio_sem_rebaixar_links(self):
        self.db.dados["perdido"] = {"plataforma": "arquivo", "estado": "na fila"}
        self.db.consulta = [self.db.document("perdido")]
        with patch.object(s.time, "sleep"):
            self.motor._resgatar()
        self.assertEqual(self.db.dados["perdido"]["codigo"], "ARQUIVO_INDISPONIVEL")

    def test_link_continua_criando_trabalho(self):
        trabalho, estado = s.criar_trabalho(self.db, self.motor,
                                          "https://www.instagram.com/reel/exemplo/", "", self.sessao, "")
        self.assertEqual((estado, self.db.dados[trabalho]["plataforma"]), ("na fila", "instagram"))


if __name__ == "__main__":
    unittest.main()
