"""Envio grande, repeticao de pedidos e cobertura das amostras nas emendas."""
from array import array
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote
import wave

import servidor as s
import test_upload as suporte


class EnviosEmPartes(unittest.TestCase):
    setUp = suporte.Upload.setUp
    tearDown = suporte.Upload.tearDown
    wav = suporte.Upload.wav
    executar = suporte.Upload.executar

    def parte(self, corpo, posicao, total, chave="a" * 32, sessao=None):
        conexao = http.client.HTTPConnection(*self.http.server_address, timeout=15)
        conexao.request("POST", "/transcrever-arquivo", corpo, {
            "Content-Type": "application/octet-stream", "X-Decupa-Nome": quote("Reunião.wav"),
            "X-Decupa-Sessao": self.sessao if sessao is None else sessao,
            "X-Decupa-Envio": chave, "X-Decupa-Total": str(total), "X-Decupa-Posicao": str(posicao),
        })
        resposta = conexao.getresponse()
        dados = json.loads(resposta.read())
        status = resposta.status
        conexao.close()
        return status, dados

    def test_repeticao_de_blocos_e_finalizacao_nao_duplicam(self):
        corpo = self.wav()
        meio = len(corpo) // 2
        for _ in range(2):
            status, dados = self.parte(corpo[:meio], 0, len(corpo))
            self.assertEqual((status, dados["recebidos"]), (200, meio))
        for _ in range(2):
            self.assertEqual(self.parte(corpo[meio:], meio, len(corpo))[0], 202)
        self.assertEqual(self.motor.fila.qsize(), 1)
        self.assertEqual(len(self.db.dados), 1)
        caminho = Path(self.motor.arquivos["a" * 32].name) / "entrada"
        self.assertEqual(caminho.read_bytes(), corpo)
        self.assertEqual(self.executar()["texto"], "Audio transcrito.")

    def test_bloco_diferente_fora_de_ordem_e_outra_sessao(self):
        self.parte(b"abc", 0, 9)
        outra = s.criar_sessao()[0]
        for corpo, posicao, sessao in [(b"xxx", 0, self.sessao), (b"def", 6, self.sessao), (b"def", 3, outra)]:
            status, dados = self.parte(corpo, posicao, 9, sessao=sessao)
            self.assertEqual((status, dados["codigo"]), (400, "ENVIO_INVALIDO"))
        self.assertFalse(self.db.dados)
        self.assertEqual(self.motor.envios["a" * 32]["recebidos"], 3)

    def test_protecao_do_envio_em_partes_e_falta_de_espaco(self):
        self.assertEqual(self.parte(b"abc", 0, 3, sessao="falsa")[1]["codigo"], "SESSAO_EXPIRADA")
        self.assertFalse(self.motor.envios)
        with patch.object(s.shutil, "disk_usage") as disco:
            disco.return_value.free = 0
            self.assertEqual(self.parte(b"abc", 0, 3)[1]["codigo"], "ESPACO_INSUFICIENTE")
        self.assertFalse(self.db.dados)

    def test_cancelamento_e_expiracao_limpam_temporarios(self):
        self.parte(b"abc", 0, 9)
        caminho = self.motor.envios["a" * 32]["pasta"].name
        conexao = http.client.HTTPConnection(*self.http.server_address)
        conexao.request("DELETE", "/envio/" + "a" * 32, headers={"X-Decupa-Sessao": self.sessao})
        resposta = conexao.getresponse()
        self.assertEqual(resposta.status, 200)
        resposta.read()
        conexao.close()
        self.assertFalse(os.path.exists(caminho))
        self.parte(b"abc", 0, 9)
        caminho = self.motor.envios["a" * 32]["pasta"].name
        self.motor.envios["a" * 32]["atualizado"] = time.monotonic() - 3601
        self.motor.limpar_envios()
        self.assertFalse(os.path.exists(caminho))
        self.assertEqual(self.parte(b"def", 3, 9)[1]["codigo"], "ARQUIVO_INDISPONIVEL")

    def test_resposta_final_perdida_e_reinicio_nao_reenfileiram(self):
        self.assertEqual(self.parte(b"abc", 0, 3)[0], 202)
        self.motor.envios.clear()  # Metadados em memoria perdidos no reinicio.
        self.assertEqual(self.parte(b"abc", 0, 3)[0], 202)
        self.assertEqual(self.motor.fila.qsize(), 1)
        self.assertEqual(len(self.db.dados), 1)

    def test_bloco_interrompido_pode_ser_reenviado_inteiro(self):
        conexao = http.client.HTTPConnection(*self.http.server_address, timeout=5)
        conexao.putrequest("POST", "/transcrever-arquivo")
        for nome, valor in {"Content-Length": "6", "X-Decupa-Nome": "voz.wav", "X-Decupa-Sessao": self.sessao,
                            "X-Decupa-Envio": "b" * 32, "X-Decupa-Total": "12", "X-Decupa-Posicao": "0"}.items():
            conexao.putheader(nome, valor)
        conexao.endheaders(b"abc")
        conexao.sock.shutdown(socket.SHUT_WR)
        resposta = conexao.getresponse()
        self.assertIn(b"ENVIO_INTERROMPIDO", resposta.read())
        conexao.close()
        envio = self.motor.envios["b" * 32]
        self.assertEqual((Path(envio["pasta"].name) / "entrada").stat().st_size, 0)
        self.assertEqual(envio["recebidos"], 0)

    def test_arquivo_maior_que_50mb_reconstituido_sem_perdas(self):
        total = 51 * 1024 * 1024 + 53
        modelo = bytes(range(256)) * 32768  # 8 MiB reutilizados; nao duplica o arquivo na memoria.
        esperado = hashlib.sha256()
        for posicao in range(0, total, len(modelo)):
            bloco = modelo[:min(len(modelo), total - posicao)]
            esperado.update(bloco)
            status, dados = self.parte(bloco, posicao, total)
            self.assertEqual(status, 202 if posicao + len(bloco) == total else 200)
        with open(Path(self.motor.arquivos["a" * 32].name) / "entrada", "rb") as arquivo:
            self.assertEqual(hashlib.file_digest(arquivo, "sha256").hexdigest(), esperado.hexdigest())
        self.assertEqual(self.motor.fila.qsize(), 1)

    def test_tres_requisicoes_formam_um_unico_resultado(self):
        with tempfile.TemporaryDirectory() as pasta:
            entrada = Path(pasta) / "voz.wav"
            with wave.open(str(entrada), "wb") as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
                audio.writeframes(b"\x01\x00" * 16000 * 11)
            self.parte(entrada.read_bytes(), 0, entrada.stat().st_size)
        def resposta(palavras):
            return {"text": " ".join(p for p, _ in palavras),
                    "words": [{"word": p, "start": t, "end": t + .2} for p, t in palavras]}
        respostas = [resposta([("Um", 0), ("teste", 3.2), ("inteiro", 4.1)]),
                     resposta([("teste", .2), ("inteiro", 1.1), ("sem", 4.5), ("perder", 5.3)]),
                     resposta([("sem", .5), ("perder", 1.3), ("conteudo.", 3)])]
        with patch.object(s, "TRECHO_SEGUNDOS", 4), patch.object(s, "SOBREPOSICAO_SEGUNDOS", 1):
            dados = self.executar(falha=respostas)
        self.assertEqual(dados["texto"], "Um teste inteiro sem perder conteudo.")
        self.assertEqual(dados["partesTotal"], 3)
        self.assertEqual(dados["duracaoAudio"], 11)

    def test_sem_tempos_nao_entrega_resultado_incompleto_como_pronto(self):
        corpo = self.wav()
        self.parte(corpo, 0, len(corpo))
        dados = self.executar(resposta={"text": "Texto sem tempos"})
        self.assertEqual((dados["estado"], dados["codigo"]), ("erro", "TEMPOS_AUSENTES"))


class CortesDeAudio(unittest.TestCase):
    def test_cobertura_exata_de_todas_as_amostras_e_ultima_fracao(self):
        # Valores variam por amostra: comparar bytes detecta lacuna ou deslocamento.
        amostras = array("h", (i % 30000 - 15000 for i in range(16000 * 11 + 37))).tobytes()
        with tempfile.TemporaryDirectory() as pasta:
            entrada = str(Path(pasta) / "original.wav")
            with wave.open(entrada, "wb") as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
                audio.writeframes(amostras)
            intervalos = []
            with patch.object(s, "TRECHO_SEGUNDOS", 4), patch.object(s, "SOBREPOSICAO_SEGUNDOS", 1):
                for caminho, inicio, duracao in s.partes_de_audio(entrada, pasta):
                    with wave.open(caminho, "rb") as audio:
                        parte = audio.readframes(audio.getnframes())
                    byte_inicial = round(inicio * 32000)
                    self.assertEqual(parte, amostras[byte_inicial:byte_inicial + len(parte)])
                    self.assertLess(Path(caminho).stat().st_size, s.LIMITE_GROQ_BYTES)
                    intervalos.append((inicio, inicio + duracao))
            self.assertEqual([inicio for inicio, _ in intervalos], [0, 3, 7])
            self.assertAlmostEqual(intervalos[-1][1], len(amostras) / 32000)
            for anterior, proximo in zip(intervalos, intervalos[1:]):
                self.assertGreater(anterior[1], proximo[0])

    def test_limite_real_de_dez_minutos_e_requisicoes_separadas(self):
        with tempfile.TemporaryDirectory() as pasta:
            entrada = str(Path(pasta) / "longo.wav")
            with wave.open(entrada, "wb") as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
                for _ in range(1201):
                    audio.writeframesraw(b"\x01\x00" * 16000)
            intervalos = []
            for caminho, inicio, duracao in s.partes_de_audio(entrada, pasta):
                self.assertLess(Path(caminho).stat().st_size, 20 * 1024 * 1024)
                intervalos.append((inicio, duracao))
            self.assertEqual(intervalos, [(0, 602), (598, 603)])

    def test_juncao_preserva_palavra_no_corte_e_repeticao_real(self):
        def palavras(textos, inicio):
            return [{"word": palavra, "start": inicio + i * .3, "end": inicio + i * .3 + .2} for i, palavra in enumerate(textos)]
        antes = palavras(["uma", "ideia", "muito", "muito", "boa"], 598.8)
        depois = palavras(["muito", "muito", "boa", "continua", "aqui"], 599.4)
        resultado = s.unir_palavras(antes, depois, 600)
        self.assertEqual([p["word"] for p in resultado], ["uma", "ideia", "muito", "muito", "boa", "continua", "aqui"])


if __name__ == "__main__":
    unittest.main()
