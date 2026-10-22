# Decupa

Cola o link de um video do Instagram, TikTok ou Facebook, ou envia um arquivo de audio, e recebe a
transcricao em portugues.

Status: site publico com tela, backend, PWA e verificacao Turnstile.

## O que tem no repositorio

| Arquivo | Para que serve |
|---|---|
| `servidor.py` | A fila, o worker e o pipeline inteiro |
| `Dockerfile` | Imagem com `yt-dlp`, `ffmpeg` e `deno`, que o Render usa pra subir |
| `firestore.rules` | O que o navegador pode ler; o backend nao passa por elas |
| `plano-de-execucao.md` | Por que cada decisao foi essa, inclusive as descartadas |

## Variaveis de ambiente

Ficam no painel do Render e nenhuma entra no repositorio.

```
GROQ_API_KEY        a chave de console.groq.com
FIREBASE_CREDENCIAL o JSON inteiro da conta de servico, numa linha so
TURNSTILE_SITEKEY   chave publica do widget servida em /config
TURNSTILE_SECRET    segredo usado na validacao e na assinatura da sessao
```

## A API

```
POST /sessao          corpo {"desafio": "<token Turnstile>"}; devolve sessao
POST /transcrever     corpo {"url": "<link do video>", "sessao": "<sessao>"}
                      devolve {"id": "...", "estado": "na fila"} na hora
POST /transcrever-arquivo  corpo binario com o audio (ate 50 MiB)
                      X-Decupa-Nome: nome do arquivo com encodeURIComponent
                      X-Decupa-Sessao: mesma sessao assinada de /sessao
GET  /trabalho/<id>   estado e resultado daquele trabalho
```

O Turnstile e validado ao abrir a pagina. A sessao assinada vale por 24 horas
na mesma aba e e reutilizada nos pedidos seguintes. O `POST /transcrever` nao
espera o trabalho terminar. Download mais transcricao levam de 10
a 60 segundos, e requisicao que segura isso estoura timeout de proxy e deixa a
pessoa olhando pra tela parada. O documento em `trabalhos` anda sozinho por
`na fila`, `baixando`, `convertendo`, `transcrevendo`, e para em `pronto` ou
`erro`. A tela vai acompanhar por `onSnapshot`; o `GET /trabalho/<id>` existe
pra conferir sem tela.

A chave publica tambem esta no `index.html` para a caixa aparecer antes do
Render acordar. Ao trocar a sitekey, atualizar o HTML e publicar o site.

O envio aceita MP3, M4A, WAV, OGG, OPUS, FLAC, AAC, WEBM, WMA e AIFF.
A extensao e conferida antes do upload, e o ffprobe confere o conteudo no
worker. O arquivo vai em blocos para uma pasta temporaria; nao vai para o
Firestore nem para armazenamento permanente. O worker apaga a pasta mesmo
quando a conversao ou a transcricao falha. Se a instancia reiniciar antes
de concluir, o trabalho informa ARQUIVO_INDISPONIVEL e pede novo envio.
O teto de envio e separado do teto de 24 MiB do audio ja comprimido.

O navegador acorda o servidor com GET /config antes de enviar o arquivo e
nao repete o upload automaticamente se a resposta se perder. O limite de
recepcao e de tres minutos. O processamento continua na fila existente.

## Erros com codigo, nunca mensagem generica

A frase que a pessoa le e montada pela tela. O backend devolve o codigo e o
detalhe tecnico.

| Codigo | Quando |
|---|---|
| `PLATAFORMA_NAO_SUPORTADA` | dominio fora das quatro |
| `YOUTUBE_BLOQUEADO` | o YouTube recusa pedido vindo de nuvem |
| `INSTAGRAM_BLOQUEADO` | bloqueio ou exigencia de login no Instagram |
| `DOWNLOAD_FALHOU` | qualquer outra falha do `yt-dlp` |
| `CONVERSAO_FALHOU` | falha do `ffmpeg` |
| `GROQ_RECUSOU` | a Groq devolveu erro |
| `ARQUIVO_VAZIO` | arquivo sem conteudo |
| `FORMATO_NAO_SUPORTADO` | extensao fora dos formatos de audio aceitos |
| `ENVIO_GRANDE_DEMAIS` | arquivo original acima de 50 MiB |
| `ENVIO_INTERROMPIDO` | conexao interrompida ou envio acima de tres minutos |
| `ARQUIVO_INDISPONIVEL` | servidor reiniciou e perdeu o arquivo temporario |

## O caminho que o codigo faz

1. Reconhece a plataforma pelo dominio.
2. Se o mesmo link ja foi transcrito nesta aba, devolve o texto da memoria
   da pagina. O servidor nao guarda transcricoes concluidas.
3. Tenta legenda automatica em portugues. Quando existe, pula download,
   conversao e transcricao de uma vez.
4. `yt-dlp -f bestaudio`, so a trilha de audio, nunca o video.
5. `ffmpeg` para opus 16 kbps mono a 16 kHz. Medido em 6,8 a 7,0 MB por hora.
6. Groq `whisper-large-v3-turbo`, `language=pt`.

## O YouTube

Medido em 2026-09-28 a partir do Render: o YouTube recusa pedido vindo de IP
de datacenter, com `429` e `403`. A legenda automatica cai junto, porque
depende da mesma extracao de pagina. Instagram, TikTok e Facebook passaram sem
cookie e sem proxy.

A decisao foi aceitar a degradacao em vez de comprar proxy residencial: link
do YouTube responde `YOUTUBE_BLOQUEADO` e as outras tres seguem funcionando.
Ligar um proxy depois e configuracao, nao reescrita.

## Onde isso sai do padrao

O padrao de arquitetura destes projetos e hospedagem estatica, sem servidor.
`yt-dlp` e `ffmpeg` sao binarios e precisam de um processo rodando, entao este
e o primeiro projeto que exige backend de verdade. A excecao e consciente e
esta explicada no plano de execucao.
