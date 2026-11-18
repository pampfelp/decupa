"""Classificacao dos erros do yt-dlp no Instagram e extensoes aceitas no envio."""
import unittest

import servidor as s

# Texto real devolvido em 2026-11-18 para instagram.com/reel/Dfo-mnsMXXZ/, no Render.
VAZIO = ("ERROR: [Instagram] Dfo-mnsMXXZ: Instagram sent an empty media response. Check if this post is "
         "accessible in your browser without being logged-in. If it is not, then use --cookies-from-browser "
         "or --cookies for the authentication.")


class CodigoDaFalha(unittest.TestCase):
    def test_midia_vazia_nao_e_limite_temporario(self):
        self.assertEqual(s.codigo_da_falha("instagram", VAZIO), "INSTAGRAM_SEM_ACESSO")

    def test_post_para_seguidores_e_sem_acesso(self):
        d = "This content is only available for registered users who follow this account"
        self.assertEqual(s.codigo_da_falha("instagram", d), "INSTAGRAM_SEM_ACESSO")

    def test_429_continua_sendo_bloqueio_temporario(self):
        d = "ERROR: [Instagram] X: Unable to download webpage: HTTP Error 429: Too Many Requests"
        self.assertEqual(s.codigo_da_falha("instagram", d), "INSTAGRAM_BLOQUEADO")

    def test_redirecionamento_por_limite_e_temporario(self):
        d = ("The webpage request was redirected to the login page. "
             "You have exceeded the rate-limit for accessing posts anonymously")
        self.assertEqual(s.codigo_da_falha("instagram", d), "INSTAGRAM_BLOQUEADO")

    def test_outras_plataformas_nao_mudam(self):
        self.assertEqual(s.codigo_da_falha("youtube", VAZIO), "YOUTUBE_BLOQUEADO")
        self.assertEqual(s.codigo_da_falha("tiktok", VAZIO), "DOWNLOAD_FALHOU")

    def test_sem_acesso_tambem_nao_devolve_o_teto(self):
        self.assertIn("INSTAGRAM_SEM_ACESSO", s.SEM_DEVOLUCAO)


class ExtensoesDeEnvio(unittest.TestCase):
    def test_video_do_celular_entra(self):
        for nome in ("reel.mp4", "REEL.MOV", "reel.m4v", "voz.mp3"):
            self.assertTrue(nome.lower().endswith(s.EXTENSOES_AUDIO), nome)

    def test_executavel_continua_fora(self):
        self.assertFalse("arquivo.exe".endswith(s.EXTENSOES_AUDIO))


if __name__ == "__main__":
    unittest.main()
