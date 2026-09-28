# Decupa

Cola o link de um video do Instagram, TikTok, YouTube ou Facebook e recebe a
transcricao em portugues.

Status: **Fase 1**, a prova de fogo. Nao existe tela, nem fila, nem banco.
O que esta aqui serve pra responder uma pergunta so, antes de construir
qualquer coisa: da pra baixar dessas quatro plataformas a partir de um
servidor de nuvem, ou o IP de datacenter e bloqueado?

## O que tem no repositorio

| Arquivo | Para que serve |
|---|---|
| `prova.py` | O caminho inteiro: baixa o audio, comprime, transcreve na Groq |
| `Dockerfile` | Imagem com `yt-dlp` e `ffmpeg`, que o Render usa pra subir |

## Como rodar

O servico sobe no Render como Web Service do tipo Docker, plano Free, com uma
unica variavel de ambiente:

```
GROQ_API_KEY=<a chave de console.groq.com>
```

Com ele no ar:

```
GET /transcrever?url=<link do video>
```

A resposta e um JSON com o texto e as medicoes de cada etapa: quanto tempo
levou o download, a conversao e a transcricao, quanto pesou o audio, e de
onde veio o texto.

## O caminho que o codigo faz

1. Reconhece a plataforma pelo dominio.
2. Se for YouTube, tenta a legenda automatica em portugues primeiro. Quando
   existe, pula download, conversao e transcricao de uma vez.
3. `yt-dlp -f bestaudio`, so a trilha de audio, nunca o video.
4. `ffmpeg` para opus 16 kbps mono a 16 kHz. Uma hora de audio deve dar cerca
   de 7 MB, e a Fase 1 existe tambem pra conferir esse numero.
5. Groq `whisper-large-v3-turbo`, `language=pt`.

## Onde isso sai do padrao

O padrao de arquitetura destes projetos e hospedagem estatica, sem servidor.
`yt-dlp` e `ffmpeg` sao binarios e precisam de um processo rodando, entao este
e o primeiro projeto que exige backend de verdade. A excecao e consciente e
esta registrada no plano de execucao.

A chave da Groq nunca entra no repositorio. Ela e colada direto no painel do
Render.
