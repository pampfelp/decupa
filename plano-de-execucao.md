# Decupa — plano de execução

`versão: 2026-09-28.2` · status: **Fase 1 concluída, três plataformas de quatro**

Webapp que recebe link de vídeo do Instagram, TikTok, YouTube ou Facebook e
devolve a transcrição em português. Na segunda fase, a transcrição vira
registro classificado no segundo cérebro.

Este documento é auto-contido de propósito. Daqui a três meses ele precisa
explicar sozinho por que as decisões foram essas, inclusive as que descartaram
o que o Felipe tinha pedido.

---

## 1. A ideia original, e por que ela não fecha

A proposta que abriu a conversa: extrair o áudio do vídeo, deixar o mais leve
possível, partir em pedaços de até 20 MB e mandar para o `@transcriber_bot` do
Telegram, que transcreve em PT-BR.

Sete problemas, em ordem de impacto.

### 1.1. Bot do Telegram não conversa com bot do Telegram

A Bot API não entrega mensagem de bot para bot. Isso é regra de plataforma, não
configuração. O "bot-to-bot communication mode", lançado em 07/05/2026, exige
que **os dois lados** optem explicitamente, e o `@transcriber_bot` é de terceiro.
Ele nunca vai optar.

A única automação possível seria um userbot via MTProto (Telethon ou Pyrogram),
logando com a conta pessoal do Felipe e fingindo ser ele digitando. Isso é
contra os Termos de Serviço do Telegram, e o modo de falha não é erro de código.
É banimento da conta que ele usa para tudo.

### 1.2. O limite de 20 MB está no lado errado

| Operação | Limite |
|---|---|
| Bot **baixar** arquivo (`getFile`) | 20 MB |
| Bot **enviar** arquivo | 50 MB |
| Usuário enviar arquivo | 2 GB (4 GB Premium) |

Quem envia o áudio ao bot é o usuário, com teto de 2 GB. Os 20 MB são o bot de
terceiro baixando, e esse teto não se contorna partindo o arquivo, porque o
comportamento dele com um arquivo de 19 MB é decisão do dono dele.

Junto disso: cortar áudio por tamanho de byte corta no meio de palavra. Se
corte for necessário algum dia, tem que ser por silêncio detectado, com
sobreposição entre os pedaços.

### 1.3. O bot já falhou em PT-BR

Print do próprio Felipe, dezembro de 2025:

> `ERROR: Oops, no wit.ai API key could be found for the language pt-br. :(`

O TranscriberBot é open source e usa wit.ai, que exige chave de API por idioma.
Sem a chave de pt-br, português simplesmente não transcreve e o resto do bot
continua funcionando normalmente.

Não foi confirmado o estado atual em setembro de 2026. O que dá para afirmar é
que a dependência já provou que quebra, e que a correção não está na mão dele.

### 1.4. A economia que justificava a gambiarra não existe

Groq, Whisper large-v3-turbo: **US$ 0,04 por hora de áudio**.

- 100 reels de 90 segundos = 2,5 h = **US$ 0,10**
- 1.000 reels de 90 segundos = 25 h = **US$ 1,00**

Camada gratuita: 7.200 segundos de áudio por hora e 28.800 por dia, ou seja
**8 horas de áudio por dia sem pagar nada**. Limite de arquivo: 25 MB no grátis,
100 MB no pago.

Montar userbot, fila, retry, tratamento de flood-wait e conviver com risco de
banimento, para economizar um dólar por mil vídeos, é trabalho negativo.

### 1.5. O stack documentado não roda este projeto

`padroes/arquitetura.md` regra 7: hospedagem estática, GitHub Pages. Crença 2:
Firebase como base, Apps Script só para upload no Drive e proxy de segredo.

`yt-dlp` e `ffmpeg` são binários que precisam de processo em servidor. GitHub
Pages não executa nada. Apps Script não roda nenhum dos dois e tem teto de 6
minutos por execução. O navegador não consegue baixar do Instagram nem do
YouTube por CORS e por autenticação.

Este é o primeiro projeto do Felipe que **exige backend de verdade**. Contraria
a regra 7 conscientemente, e por isso precisa virar registro. Gatilho G10.

### 1.6. A parte difícil é o download, não a transcrição

Este é o risco que não aparece no teste de hoje.

**YouTube** bloqueia IP de datacenter com "Sign in to confirm you're not a bot".
Qualquer VPS cai nisso: funciona no notebook e falha no servidor. A saída é
cookie de conta logada ou proxy residencial, e o YouTube muda as checagens a
cada poucas semanas com o `yt-dlp` correndo atrás.

**Instagram** exige cookie de sessão para boa parte do conteúdo e rate-limita
agressivamente. Há uma fila longa de issues abertas no repositório do `yt-dlp`
exatamente sobre isso, reincidentes há anos.

Duas consequências práticas:

1. Colocar o cookie da conta pessoal do Instagram num servidor é risco de
   bloqueio da conta. Mesma família do problema do userbot.
2. Proxy residencial custa de US$ 1,75/GB (IPRoyal) a US$ 4/GB (Bright Data).
   Um reel pesa de 10 a 20 MB, então de US$ 0,02 a 0,08 por vídeo.

**O download custa de 20 a 80 vezes mais que a transcrição.**

### 1.7. A camada legal muda conforme quem usa

Baixar mídia dessas plataformas viola os termos de uso delas. Para uso pessoal,
é risco assumido. Para link público, entra responsabilidade sobre conteúdo de
terceiro, e o `checklist-legal-basico.md` item 2 pede cláusula de remoção.

---

## 2. O que sobrou da ideia original, e estava certo

**Extrair só o áudio e deixá-lo leve.** Com opus 16 kbps mono a 16 kHz, uma hora
de áudio dá cerca de 7 MB. Um reel de 90 segundos dá cerca de 180 KB.

**Por consequência, o chunking desaparece.** Os 25 MB da camada grátis comportam
cerca de três horas de áudio num arquivo só. Reel, TikTok e Shorts nunca chegam
perto.

Os dois números são cálculo, não medição. A Fase 1 mede.

---

## 3. Decisões

| Questão | Decisão | Motivo |
|---|---|---|
| Nome | **Decupa** | Decupagem é o termo de audiovisual para transcrever material bruto. Seis letras, cabe no PWA, é português, não tem cara de IA. A tagline "vídeo em texto" resolve quem não conhece o termo. |
| Motor de transcrição | Groq Whisper large-v3-turbo | US$ 0,04/hora, 8h/dia grátis, uma chamada HTTP, sem risco de conta. |
| Hospedagem | Render, camada grátis | Deploy é conectar o repositório. Migra para Cloud Run se o sono de 15 min incomodar. |
| Proxy residencial | Não comprar agora | Só depois da Fase 1 medir quem falha. Comprar antes é pagar por um problema que talvez seja só do YouTube. |
| Prioridade das plataformas | Instagram > TikTok > YouTube > Facebook | Os prints são reels e `transcricoes-originais.md` é reel. |
| Acesso | Sem login, com login opcional para a Fase 6 | Decisão do Felipe. Custo analisado na seção 4. |
| Teto do anônimo | 5 transcrições/dia por IP, vídeo até 10 min, Turnstile | Cobre uso curioso e mata uso abusivo. Link em cache não conta no teto. |
| Teto do logado | 60 min por vídeo | A cota grátis dá 8h/dia. Folgado, e ainda protege de um engano. |
| Vídeo longo (podcast, aula) | Fora da v1 | Corte por silêncio é trabalho real para um caso que ainda não existe. Crença 34. |
| Saída | Texto corrido, com botão que liga marcação de tempo | Timestamp sai de graça da Groq (`verbose_json`). Ligado por padrão poluiria a leitura. |
| Separar quem fala | Não | Whisper não faz diarização. Exigiria outro provedor e outro custo, para um ganho que reel não tem. |
| Paleta | Sem manual de marca. Fundo neutro quente, tinta quase preta, accent teal escuro | Distingue do lilás do boilerplate e do verde da Solar Green. Hex exatos saem da etapa de imagem. |
| Workflow de UI | Imagem-primeiro (crença 37) | Escolha do Felipe. Primeira vez que ele testa. |

### A decisão que mais barateia o projeto

**O YouTube tenta legenda automática antes de baixar qualquer coisa.**

Boa parte dos vídeos já tem legenda automática em português, e o `yt-dlp` pega
ela sem baixar o vídeo (`--write-auto-sub --skip-download`). Quando existe,
pula download, conversão e transcrição de uma vez: sem banda, sem custo de
Groq, sem risco de bloqueio, resposta quase instantânea. Quando não existe, cai
no caminho normal.

Essa decisão só apareceu porque o YouTube é o caso mais bloqueado dos quatro.

**Corrigido em 2026-09-28, depois da Fase 1.** Metade do argumento acima não se
sustenta. Pegar a legenda automática exige a mesma extração de página que o
bloqueio derruba: quando o YouTube recusa o IP, a legenda cai junto, antes de
chegar a ser lida. Ela continua valendo como economia de banda, de tempo e de
cota da Groq **quando o download funciona**, e não vale como saída para
bloqueio, que era como estava escrito.

### Três assinaturas visuais deliberadas

Atende a regra 14 do design system.

1. O cartão de trabalho mostra as quatro etapas como uma trilha que preenche da
   esquerda para a direita, não como barra de progresso genérica.
2. A plataforma detectada vira um selo dentro do próprio campo de link, assim
   que o link é colado, antes de clicar em nada.
3. O texto transcrito é tratado como conteúdo de leitura, com medida de linha e
   entrelinha de artigo, não como saída de terminal.

---

## 4. O custo de "sem login", e como ele foi contornado

Um endereço público que baixa vídeo de qualquer link e transcreve é um serviço
grátis de download mais transcrição. Quem achar, usa. Cinco consequências, todas
pagas pelo dono:

1. **A cota da Groq passa a ser compartilhada com estranhos.** Oito horas de
   áudio por dia, cerca de 320 reels. Quando estoura, para para todo mundo até
   virar o dia.
2. **Banda de proxy vira conta mensal.** Mil vídeos de desconhecidos dá de
   US$ 20 a 80.
3. **O IP do servidor queima mais rápido.** O rate-limit do Instagram é por IP,
   e volume de estranho acelera o bloqueio.
4. **A exposição legal muda de patamar.**
5. **A transcrição do dono entra na fila atrás da dos outros.** Gatilho D3.

A saída não é abandonar o "sem login". É dar teto a ele desde o primeiro dia.

| Camada | O que pode |
|---|---|
| Anônimo | 5 transcrições/dia por IP, vídeo até 10 min, Cloudflare Turnstile |
| Logado | sem teto prático, histórico, registro no segundo cérebro |
| Sempre | cache por URL normalizada: link já transcrito devolve na hora, custo zero |

A escrita anônima vai para uma coleção de antessala isolada, que aceita só
`create` com campos exatos e tamanho limitado, nunca `read`, `update` ou
`delete`. Crença 12.

**A tensão que sobra.** "Sem login e público" exige servidor sempre no ar, o que
elimina rodar na máquina do Felipe. E rodar na máquina dele era a solução
gratuita para o problema do item 1.6, porque a casa dele tem IP residencial e o
YouTube não bloqueia IP residencial. Escolhendo nuvem, o problema volta.

---

## 5. Arquitetura

```
Navegador (HTML/CSS/JS puro, padrão dele)
  cola o link, acompanha o status, lê e copia o texto
        │  POST /transcrever  → devolve id do trabalho na hora
        │  o status chega por onSnapshot do Firestore
        ▼
Backend (container pequeno — o que SAI do padrão dele)
  1. cache por URL normalizada       → link repetido custa zero
  2. é YouTube e tem legenda em pt?  → usa ela, pula todo o resto
  3. yt-dlp -f bestaudio             → só a trilha, nunca o vídeo
  4. ffmpeg 16 kHz mono opus 16k     → 1 hora ≈ 7 MB
  5. Groq whisper-large-v3-turbo     → language=pt, verbose_json
        ▼
Firestore (padrão dele, sem mudança)
  fila de trabalhos, status, transcrição final, cache
```

Quatro pontos de desenho:

**Fila com status desde o primeiro dia.** Download mais transcrição leva de 10 a
60 segundos e pode falhar. Uma requisição síncrona que espera isso estoura
timeout de proxy e deixa o usuário olhando para tela parada. Gatilho A2.

**Cache por URL normalizada.** Corta custo de proxy, corta risco de rate-limit,
e é a defesa mais barata que existe contra o gatilho E1.

**Chave da Groq só no servidor.** Repositório é público por padrão (crença 38),
e chave em código público é vazamento na hora. Crença 13.

**Falha por plataforma degrada em vez de derrubar.** Instagram bloqueado não
derruba TikTok. Gatilho E5.

### Fluxo da tela

```
[ cola o link ]  →  reconhece a plataforma pelo domínio e mostra qual é
        ↓
[ Transcrever ]  →  responde na hora com um cartão de trabalho
        ↓
  cartão mostra:  baixando → convertendo → transcrevendo → pronto
                  cada etapa com o tempo decorrido
        ↓
[ texto pronto ]  copiar tudo · baixar .txt · (logado) enviar pro segundo cérebro
```

Três decisões de usabilidade:

- **Status por etapa, não barra genérica.** Quando falha, a pessoa já sabe onde
  falhou. "O Instagram recusou o download" é acionável; "erro ao processar" não.
- **Nunca tela travada esperando.** O trabalho vira cartão na hora e a pessoa
  pode colar o próximo link.
- **Link repetido responde instantâneo pelo cache**, com marcação discreta de
  que veio de transcrição anterior e quando. Crença 32: regra invisível é
  indistinguível de bug.

---

## 6. O plano, passo a passo

`[VOCÊ]` é o que depende do Felipe. `[EU]` é o que é da sessão. Crença 30.

### Fase 0 — decidir e validar

Concluída em 2026-09-28. Nada a executar.

### Fase 1 — prova de fogo (meio dia)

Existe para responder a pergunta mais cara do projeto antes de qualquer tela:
**dá para baixar desses quatro sites a partir de um servidor de nuvem?**

**Critério de aceite, literal:** quatro links, um por plataforma, entram e saem
quatro arquivos `.txt` em português legível, rodando **no Render**, não no
notebook. Passar no notebook e falhar na nuvem conta como falha, porque é
exatamente a armadilha do IP de datacenter. Crença 36.

**1.1 `[VOCÊ]` Criar a conta da Groq**

1. Abra `console.groq.com`.
2. Clique em **Sign up** e entre com sua conta Google.
3. No menu da esquerda, clique em **API Keys**.
4. Clique em **Create API Key**.
5. Dê o nome `decupa` e confirme.
6. A chave aparece uma vez só. Copie e guarde num lugar seguro.
7. Não mande a chave no chat. Ela vai ser colada direto no painel do Render no
   passo 1.6, e nunca entra no repositório.

**1.2 `[VOCÊ]` Criar o repositório**

1. Abra `github.com/new`.
2. Nome: `decupa`.
3. Marque **Public** (crença 38).
4. Não marque nada em "Initialize this repository".
5. Clique em **Create repository** e mande o link.

**1.3 `[VOCÊ]` Criar a conta do Render**

1. Abra `render.com`, clique em **Get Started** e entre com o GitHub.
2. Autorize o Render a ver seus repositórios.
3. Pare aqui. O serviço se cria no passo 1.6, depois de o código existir.

**1.4 `[EU]` Escrever a prova.** Um script único, cerca de oitenta linhas, que
recebe uma URL e faz o caminho inteiro. Mais um `Dockerfile` mínimo com `yt-dlp`
e `ffmpeg`. Sem tela, sem fila, sem banco.

**1.5 `[EU]` Subir para o repositório.**

**1.6 `[VOCÊ]` Criar o serviço no Render**

1. No painel do Render, clique em **New +** e depois em **Web Service**.
2. Escolha o repositório `decupa`.
3. Em **Language**, escolha **Docker**.
4. Em **Instance Type**, escolha **Free**.
5. Role até **Environment Variables** e clique em **Add Environment Variable**.
6. Em **Key**, escreva `GROQ_API_KEY`. Em **Value**, cole a chave do passo 1.1.
7. Clique em **Create Web Service** e espere o deploy (3 a 5 minutos).
8. Mande a URL que o Render gerar.

**1.7 `[EU]` Rodar e medir.** Um link de cada plataforma. Para cada um: funcionou,
quanto tempo levou, quanto pesou o áudio, e se a transcrição está legível.

**1.8 `[NÓS]` Decidir com o número na frente.** Três cenários:

- **Os quatro funcionam.** Segue para a Fase 2 sem proxy e sem custo extra.
- **Só o YouTube falha.** Provável. A legenda automática resolve boa parte.
- **O Instagram falha.** Para tudo e conversa antes de escrever mais uma linha.
  As saídas são conta descartável (que pode ser bloqueada) ou proxy residencial,
  e as duas têm custo que precisa de aprovação.

#### Resultado, medido em 2026-09-28 no Render

Caiu no segundo cenário. Instagram, TikTok e Facebook passaram a partir de IP
de datacenter, sem cookie e sem proxy. O YouTube recusou com `429 Too Many
Requests` e `403 Forbidden`, em duas tentativas separadas por vinte minutos.

| Plataforma | Download | Conversão | Groq | Áudio opus | Por hora |
|---|---|---|---|---|---|
| Instagram | 8,1 s | 3,2 s | 0,5 s | 48 KB / 24,6 s | 6,9 MB |
| TikTok | 7,6 s | 5,2 s | 0,5 s | 88 KB / 44,5 s | 7,0 MB |
| Facebook | 10,1 s | 6,3 s | 0,7 s | 154 KB / 79,4 s | 6,8 MB |
| YouTube | recusado | — | — | — | — |

A primeira tentativa do YouTube trazia junto um erro que não era bloqueio: o
`yt-dlp` passou a exigir runtime de JavaScript e a imagem não tinha nenhum.
Só depois de instalar o deno (commit `2618f28`) a medição virou limpa, e aí
sobrou bloqueio puro. Sem essa segunda rodada, "o YouTube bloqueia" e "faltou
dependência" ficariam confundidos, que é o que a Fase 1 existe para evitar.

**Decisão do Felipe, 2026-09-28: aceitar a degradação.** Sem proxy residencial
e sem cookie de conta. O YouTube é a terceira prioridade das quatro, e é a
única das quatro onde a pessoa tem saída sem o Decupa, porque o próprio
YouTube mostra transcrição na interface dele. Link de YouTube responde
`YOUTUBE_BLOQUEADO` com frase acionável, e as outras três seguem funcionando.
A porta fica aberta: ligar proxy depois é configuração, não reescrita.

Consequência para a Fase 2: o gatilho **E5** deixa de ser precaução e vira
requisito com caso real no primeiro dia.

### Fase 2 — backend (1 a 2 dias)

**2.1 `[VOCÊ]` Criar o projeto Firebase.** Passo a passo detalhado na hora,
seguindo `conectar-firebase.md`. São cerca de 8 cliques no console.

**2.2 `[EU]` A fila.** `POST /transcrever` grava um documento em `trabalhos` e
devolve o id na mesma resposta, sem esperar nada. Um worker separado consome.
A tela acompanha por `onSnapshot`.

**2.3 `[EU]` O pipeline.** Conforme a seção 5.

**2.4 `[EU]` Erros com código, não mensagem genérica.** Seguindo
`desenhar-api-e-apps-script.md`: `INSTAGRAM_BLOQUEADO`, `YOUTUBE_EXIGE_LOGIN`,
`VIDEO_LONGO_DEMAIS`, `PLATAFORMA_NAO_SUPORTADA`, `LIMITE_DIARIO_ATINGIDO`.
A tela traduz cada um numa frase acionável.

**2.5 `[EU]` Falha isolada por plataforma.**

### Fase 3 — a tela, pelo workflow imagem-primeiro

Primeira vez que esse workflow é testado. Vai com revisão entre cada etapa, do
jeito que está em `design-system.md`. Sem revisão no meio, vira exatamente o
problema que o gatilho G9 descreve: 20 a 30 minutos para um resultado distante
da referência.

1. `[EU]` Gerar a tela como imagem, duas ou três variações. Uma tela só: campo de
   link no topo, lista de cartões embaixo, texto pronto abrindo em modal central
   no computador e tela cheia no celular (crença 15).
2. `[VOCÊ]` Aprovar ou mandar refazer. **Nada avança sem isso.** Imagem é barata;
   código feito em cima de composição errada, não.
3. `[EU]` Converter a imagem aprovada num esqueleto mínimo, só estrutura e
   posição, sem cor nem tipografia final.
4. `[VOCÊ]` Aprovar o esqueleto. É aqui que se pega "o cartão está grande demais"
   antes de o erro ser herdado por tudo que vem depois.
5. `[EU]` Camadas nesta ordem: fundo, cards, ícones SVG feather, tipografia por
   último.
6. `[EU]` Ligar no backend. `shared.js` único, `?v=` em toda a cadeia de imports
   (gatilho B8, não só no arquivo de entrada), toast e modal próprios, zero
   emoji.
7. `[EU]` Registrar como foi. O workflow está no repositório como não testado.
   Resultado negativo vale tanto quanto confirmação.

### Fase 4 — teto, antes de o link circular

1. `[EU]` Rate limit de 5/dia por IP.
2. `[EU]` Turnstile da Cloudflare, grátis e invisível na maioria dos casos.
3. `[EU]` Limite de duração: 10 min anônimo, 60 min logado.
4. `[EU]` Antessala isolada (crença 12).
5. `[VOCÊ]` Criar a conta Cloudflare e passar a chave do site do Turnstile.
6. `[EU]` Termos de uso com cláusula de remoção e política de privacidade que
   bata com o que o sistema faz de verdade. **A redação exata é mostrada no chat
   antes de virar arquivo.** Crença 19.

### Fase 5 — pronto de verdade

1. `[EU]` Manifest, ícone próprio do Decupa (distinto dos outros apps da mesma
   origem), service worker com prefixo `decupa-`.
2. `[EU]` Banner de instalação nas duas variantes: Android via
   `beforeinstallprompt`, iPhone com passo a passo pelo Compartilhar.
3. `[EU]` Rodar `verificar-pwa.mjs`. Só dizer "pronto" com ele verde. Crença 39.
4. `[EU]` Open Graph no `<head>`, validado depois do domínio no ar.
5. `[EU]` Rodar `/security-review` antes de o link ficar público.
6. `[VOCÊ]` Testar no celular de verdade. Navegador embutido não vale como prova.

### Fase 6 — o segundo cérebro

Só depois de tudo acima funcionando. Logado, a transcrição passa pelas sete
lentes da crença 31 e o sistema devolve uma proposta de classificação para
validar. Nunca arquiva sozinho, que é o Passo 5 do método.

**Fica em aberto de propósito:** se isso escreve direto no repositório do segundo
cérebro ou só entrega o texto pronto para colar. São desenhos muito diferentes,
e decidir hoje, seis semanas antes de chegar lá, seria escolher no escuro.

---

## 7. O que foi decidido não fazer

Cada um seria trabalho especulativo. Crença 34.

- **Corte de áudio em pedaços.** Reel não chega perto de limite nenhum. Entra só
  se vídeo longo entrar.
- **Proxy residencial.** Só depois da Fase 1 provar que precisa, e só para a
  plataforma que precisa.
- **Separação de quem fala.** Whisper não faz, e reel é uma pessoa só.
- **Tradução para outros idiomas.** O pedido foi PT-BR.
- **Tela de Configurações.** A regra 8 do design system pede, mas não há nada
  para configurar ainda.
- **Firebase Auth nas Fases 1 a 4.** O login só ganha função na Fase 6.
  Construir antes seria tela sem uso.

---

## 8. Gatilhos de `antecipacao.md` que se aplicam

| Gatilho | Por quê |
|---|---|
| **E1** camada gratuita | Três de uma vez: Groq, Firestore e hospedagem |
| **E3** API de terceiro | `yt-dlp` é engenharia reversa de site, sem documentação. Pior que o caso Autentique |
| **E5** ponto único de falha | O extrator. Se cai, o produto cai |
| **D1** repositório público | Chave da Groq e cookie não podem encostar no repositório |
| **D2/D3** público anônimo e spam | Aplicam, porque a escolha foi sem login |
| **A2** escrita que espera servidor | Resolvido pela fila |
| **A4** cota consumida por terceiro | Variante: estranhos em vez da equipe |
| **G10** construir sem ler o padrão | Este projeto contraria `arquitetura.md` regra 7 conscientemente |

Não se aplicam: A1, A3, A6, A11, A12, B1 a B10 em grande parte, C1 a C5,
F1 a F6, G1 a G9.

---

## 9. Conexão com o que já existe no segundo cérebro

`ideias-futuras.md` tem **"Captura por voz via WhatsApp pro segundo cérebro"**,
levantada em 2026-08-21 e reforçada em 2026-09-04: áudio, transcrição, Claude,
entrada organizada no repositório.

`marketing/pesquisa/transcricoes-originais.md` são **dez transcrições automáticas
de reels de terceiros que o Felipe já salva à mão**, capturadas entre 01 e
07/07/2026, que alimentam `marketing/padroes/`.

Ou seja, ele já faz esse trabalho manualmente e ele já tem destino. Foi o que
motivou a pergunta que levou à Fase 6.

---

## 10. Onde este plano pode estar errado

**A Fase 1 pode matar o projeto no formato atual.** Se o Instagram recusar
download de nuvem sem cookie, as saídas todas têm custo: conta descartável (que
pode ser bloqueada), proxy residencial, ou rodar na máquina dele, o que mata o
"público sem login". É melhor descobrir em meio dia do que depois da tela pronta.

> **Fechado em 2026-09-28.** O Instagram passou sem cookie e sem proxy. O
> formato "público sem login, rodando em nuvem" sobrevive. Medição de um dia,
> não garantia: o gatilho E3 continua valendo, e o extrator continua sendo o
> ponto único de falha.

**A estimativa de 7 MB por hora em opus 16 kbps é cálculo, não medição.** Se
estiver errada para mais, o teto de 60 min do logado aperta. A Fase 1 mede.

> **Fechado em 2026-09-28.** Medido em 6,8 a 7,0 MB por hora nas três
> plataformas que funcionam. O cálculo estava certo e o teto de 60 min do
> logado dá cerca de 7 MB, folgado dentro dos 25 MB da camada grátis.

---

## 11. Pendente de validação, sem arquivar

Passo 5 do método: validar antes de arquivar.

1. **Candidato a crença.** Projeto que exige binário de servidor (`ffmpeg`,
   `yt-dlp`) não cabe no padrão estático. Quando contrariar `arquitetura.md`
   regra 7, registrar a exceção em vez de deixar implícito. Este projeto é o
   primeiro caso.
2. **Candidato a gatilho novo em `antecipacao.md`, família E.** "Vai depender de
   baixar conteúdo de plataforma que não quer ser baixada": funciona no notebook
   e falha no servidor por IP de datacenter, o download custa mais que o
   processamento, e a correção é corrida armamentista permanente. Ainda é
   antecipação pura, sem custo real medido.
3. **Candidato a `pendencias.md`.** Corte de áudio por silêncio, para quando
   vídeo longo entrar.
4. **`Histórico de teste` do workflow imagem-primeiro.** A preencher na Fase 3.

---

## Fontes

- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Bot-to-bot communication](https://core.telegram.org/api/bots/bot-to-bot)
- [Telegram Bots FAQ](https://core.telegram.org/bots/faq)
- [Groq Speech to Text](https://console.groq.com/docs/speech-to-text)
- [Groq Rate Limits](https://console.groq.com/docs/rate-limits)
- [TranscriberBot (charslab)](https://github.com/charslab/TranscriberBot)
- [yt-dlp: Sign in to confirm you're not a bot](https://ytdlp.org/guides/fix-sign-in-to-confirm-not-a-bot)
- [yt-dlp issue #11166, Instagram rate-limit](https://github.com/yt-dlp/yt-dlp/issues/11166)
- [Render vs Railway vs Fly.io, preços 2026](https://hostim.dev/blog/render-vs-railway-vs-fly-pricing/)
- [Preço de proxy residencial 2026](https://proxyfacts.com/blog/proxy-pricing-comparison)
