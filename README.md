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
POST /transcrever-arquivo  corpo binario com um bloco (ate 50 MiB)
                      X-Decupa-Nome: nome do arquivo com encodeURIComponent
                      X-Decupa-Sessao: mesma sessao assinada de /sessao
                      X-Decupa-Envio: identificador aleatorio, 32 hexadecimais
                      X-Decupa-Total: tamanho do arquivo completo em bytes
                      X-Decupa-Posicao: inicio do bloco em bytes, comecando em zero
                      devolve id, recebidos e estado; 202 ao confirmar o ultimo
DELETE /envio/<id>    cancela um envio incompleto; exige X-Decupa-Sessao
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
Os 50 MiB sao o teto de cada requisicao, nao do arquivo completo. A tela usa
Blob.slice para enviar blocos de 8 MiB em ordem. O servidor grava direto em
disco, confere posicao e SHA-256 e aceita repeticao do mesmo bloco sem
duplicar bytes nem trabalhos. Cada envio fica vinculado a sessao que o criou.
Envios abandonados expiram por inatividade e sao limpos pela varredura.
O limite pratico do arquivo completo depende do espaco temporario do servidor.

O navegador acorda o servidor com GET /config antes de enviar o arquivo e
pode repetir cada bloco ate tres vezes em falha de rede ou 502/503/504.
Cada bloco tem ate tres minutos para chegar. O processamento continua na fila
existente; arquivos na fila nao sao apagados pela idade do primeiro bloco.

O servidor decodifica o audio uma vez, em fluxo, para PCM mono 16 kHz.
Divide por amostras em trechos de dez minutos, com dois segundos de contexto
antes e depois de cada corte. Cada WAV tem ate 604 segundos, menos de 20 MB,
abaixo do limite de 24 MiB reservado para as chamadas da Groq. Pausas nao sao
removidas. Cada trecho e transcrito em requisicao separada com tempos de
palavras; a juncao alinha somente a sobreposicao, preservando repeticoes reais.
A tela acompanha trecho atual/total e recebe uma unica transcricao no fim.
Se um trecho falhar, o trabalho inteiro aparece como erro, nunca como texto
completo. Precisao do reconhecimento e estimativa dos tempos dependem da Groq.
Referencia: https://console.groq.com/docs/speech-to-text (conferida em 23/10/2026).

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
| `ENVIO_GRANDE_DEMAIS` | bloco acima de 50 MiB |
| `ENVIO_INTERROMPIDO` | conexao interrompida ou bloco acima de tres minutos |
| `ARQUIVO_INDISPONIVEL` | servidor reiniciou e perdeu o arquivo temporario |
| `ENVIO_INVALIDO` | bloco alterado, fora de ordem ou envio de outra sessao |
| `ESPACO_INSUFICIENTE` | sem espaco temporario para continuar o envio |
| `TEMPOS_AUSENTES` | resposta sem tempos para juntar os trechos com seguranca |

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
