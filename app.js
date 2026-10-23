// Decupa — a tela. Um arquivo so, ate o projeto pedir divisao.
//
// O POST devolve o id do trabalho na hora e nao espera nada. Daí em diante
// quem manda e o Firestore: o backend escreve o andamento no documento e esta
// tela escuta por onSnapshot. Por isso nada aqui fica perguntando "ja acabou?".

import { db } from "./firebase-init.js?v=10";
import { doc, onSnapshot } from "https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js";
import { iniciarBannerInstalacao } from "./pwa-instalacao.js?v=10";

// Antes de qualquer await: o evento beforeinstallprompt do Android dispara
// cedo e, se ninguem estiver escutando, passa e nao volta.
iniciarBannerInstalacao({ icone: "icon-192.png", nomeApp: "o Decupa" });
if ("serviceWorker" in navigator) {
  addEventListener("load", () => navigator.serviceWorker.register("./service-worker.js"));
}

const BACKEND = "https://decupa.onrender.com";
const ETAPAS = ["baixando", "convertendo", "transcrevendo", "pronto"];
const PASSO_MS = 2000;
const EXTENSOES_AUDIO = /\.(mp3|m4a|wav|ogg|opus|flac|aac|webm|wma|aif|aiff)$/i;

// Cada codigo do backend vira uma frase que diz o que fazer. "Erro ao
// processar" nao e acionavel; "o Instagram recusou" e.
const FRASES = {
  YOUTUBE_BLOQUEADO: "O YouTube não deixa baixar a partir de um servidor. Por enquanto o Decupa cobre Instagram, TikTok e Facebook.",
  INSTAGRAM_BLOQUEADO: "O Instagram recusou esse link agora. Costuma voltar sozinho em alguns minutos.",
  PLATAFORMA_NAO_SUPORTADA: "Esse link não é do Instagram, do TikTok, do YouTube nem do Facebook.",
  DOWNLOAD_FALHOU: "Não deu para baixar esse vídeo. Ele pode ser privado, ter sido removido, ou o link pode estar incompleto.",
  CONVERSAO_FALHOU: "O áudio não pôde ser convertido. Confira se o arquivo está completo e pode ser reproduzido.",
  GROQ_RECUSOU: "A transcrição falhou no servidor. Vale tentar de novo.",
  GROQ_SEM_CHAVE: "A transcrição está desligada no servidor.",
  URL_AUSENTE: "Cole um link ou selecione um arquivo de áudio antes de começar.",
  ARQUIVO_VAZIO: "Esse arquivo está vazio. Selecione um arquivo com áudio.",
  FORMATO_NAO_SUPORTADO: "Selecione um áudio em MP3, M4A, WAV, OGG, OPUS, FLAC, AAC, WEBM, WMA ou AIFF.",
  ENVIO_GRANDE_DEMAIS: "Um bloco excedeu o limite de envio. Atualize a página e tente novamente.",
  ENVIO_INVALIDO: "Um bloco do arquivo não pôde ser confirmado. Selecione o áudio e envie novamente.",
  ESPACO_INSUFICIENTE: "O servidor está sem espaço temporário agora. Tente novamente mais tarde.",
  TEMPOS_AUSENTES: "O motor não devolveu os tempos necessários para juntar os trechos. Tente novamente.",
  ENVIO_INTERROMPIDO: "O envio foi interrompido. Confira sua conexão e tente de novo.",
  ARQUIVO_INDISPONIVEL: "O servidor reiniciou antes de terminar. Selecione o áudio e envie novamente.",
  ENVIO_INDISPONIVEL: "O servidor ainda não está pronto para receber arquivos. Tente novamente em alguns minutos.",
  CORPO_INVALIDO: "Não entendi esse link.",
  TRABALHO_NAO_ENCONTRADO: "Esse trabalho não existe mais.",
  FALHA_INESPERADA: "Alguma coisa quebrou no meio do caminho. Vale tentar de novo.",
  SEM_RESPOSTA: "Não consegui falar com o servidor. Ele pode estar acordando: espere uns 50 segundos e tente de novo.",
  SEM_AUDIO: "O arquivo recebido pelo servidor não contém uma faixa de áudio legível. Confira se ele pode ser reproduzido; se usou um link, tente enviar o áudio diretamente.",
  AUDIO_GRANDE_DEMAIS: "O áudio ainda excede o limite do motor depois de comprimido. Divida-o em partes menores.",
  DESAFIO_FALTANDO: "A verificação de que você não é um robô não carregou. Atualize a página.",
  DESAFIO_RECUSADO: "A verificação de que você não é um robô não passou. Atualize a página e tente de novo.",
  DESAFIO_INDISPONIVEL: "A verificação de acesso está indisponível agora. Atualize a página em alguns minutos.",
  SESSAO_EXPIRADA: "Sua verificação expirou. Aguarde a nova validação e tente de novo."
};

// A chave publica esta no HTML para o widget aparecer antes do Firebase carregar.
let desafio = { sitekey: null, sessao: null, expiraEm: 0, validando: false };
const CHAVE_SESSAO = "decupa_sessao";

const $ = (id) => document.getElementById(id);
const campo = $("campo"), selo = $("selo"), botao = $("botao"), fita = $("fita");
const cheio = $("cheio"), marcador = $("marcador");
const caixas = [...document.querySelectorAll(".etapa")];
const recado = $("recado"), certo = $("certo"), fundoModal = $("fundo-modal");
const arquivo = $("arquivo"), removerArquivo = $("remover-arquivo");

let etapaAtual = -1, progresso = 0, relogio = null, desescutar = null;
let ultimo = null;
let ocupado = false;

function ocupar(valor) {
  ocupado = valor;
  campo.disabled = valor;
  arquivo.disabled = valor;
  removerArquivo.disabled = valor;
  botao.disabled = valor || !!(desafio.sitekey && !desafio.sessao);
}

function atualizarArquivo() {
  removerArquivo.hidden = !arquivo.files.length;
  caixas[0].firstElementChild.textContent = arquivo.files.length ? "Enviando" : "Baixando";
  if (/^(URL_AUSENTE|ARQUIVO_VAZIO|FORMATO_NAO_SUPORTADO|ENVIO_GRANDE_DEMAIS)$/.test($("recado-codigo").textContent)) {
    zerarFita();
    fita.hidden = true;
  }
}

// ----------------------------------------------------------- a marca digitada

function digitarMarca() {
  const alvo = $("marca-letras"), cursor = $("cursor"), palavra = "Decupa";
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
    alvo.textContent = palavra;
    cursor.remove();
    return;
  }
  let i = 0;
  (function proxima() {
    alvo.textContent = palavra.slice(0, ++i);
    if (i < palavra.length) setTimeout(proxima, 135);
    else cursor.classList.add("fim");
  })();
}

// ------------------------------------------------------------------- a fita

function pintar() {
  cheio.style.width = (progresso * 100) + "%";
  marcador.style.left = (progresso * 100) + "%";
  caixas.forEach((c, i) => {
    c.classList.toggle("ativa", i === etapaAtual);
    c.classList.toggle("feita", i < etapaAtual);
  });
}

function irPara(indice) {
  etapaAtual = indice;
  progresso = Math.max(progresso, indice / ETAPAS.length);
  pintar();
}

// Anda dentro da etapa atual e nunca alcanca o fim dela sozinha: o passo e
// proporcional ao que falta. Quem passa de etapa e o backend, nunca o relogio.
function rastejar() {
  const teto = (etapaAtual + 1) / ETAPAS.length;
  progresso += (teto - progresso) * 0.38;
  pintar();
}

function ligarRelogio() {
  clearInterval(relogio);
  relogio = setInterval(rastejar, PASSO_MS);
}

function zerarFita() {
  clearInterval(relogio);
  etapaAtual = -1;
  progresso = 0;
  pintar();
  recado.hidden = true;
}

// -------------------------------------------------------------------- erros

function mostrarErro(codigo, detalhe) {
  clearInterval(relogio);
  recado.classList.remove("esperando", "quebrado");
  $("recado-frase").textContent = FRASES[codigo] || FRASES.FALHA_INESPERADA;
  $("recado-codigo").textContent = detalhe ? codigo + " · " + detalhe : codigo;
  recado.hidden = false;
  ocupar(false);
}

// ------------------------------------------------------------ o fim do trabalho

function concluir(dados) {
  clearInterval(relogio);
  etapaAtual = 3;
  progresso = 1;
  pintar();

  ultimo = dados;
  if (dados.texto && dados.url) jaTranscrito.set(chaveDoLink(dados.url), dados);

  certo.hidden = false;
  certo.classList.remove("rodando");
  void certo.offsetWidth;
  certo.classList.add("rodando");

  setTimeout(() => abrirModal(dados), 950);
}

function legenda(dados) {
  const partes = [];
  if (dados.nomeArquivo) partes.push(dados.nomeArquivo);
  else if (dados.plataforma) partes.push(dados.plataforma);
  if (dados.duracaoAudio) partes.push(Math.round(dados.duracaoAudio) + " s de áudio");
  if (dados.fonte && dados.fonte.startsWith("legenda")) partes.push("legenda automática");
  return partes.join(" · ");
}

function abrirModal(dados) {
  $("modal-texto").textContent = dados.texto || "";
  $("modal-meta").textContent = legenda(dados);
  fundoModal.classList.add("aberto");
  $("copiar").focus();
}

// Fechar devolve a tela ao estado de recomeco: o certo some, o campo esvazia,
// e o texto desce pro bloco de leitura, que ate aqui mostrava o manifesto.
function fechar() {
  fundoModal.classList.remove("aberto");
  certo.hidden = true;
  certo.classList.remove("rodando");

  campo.value = "";
  arquivo.value = "";
  atualizarArquivo();
  ocupar(false);
  selo.classList.remove("aceso");
  campo.focus();

  if (ultimo) guardarNaLeitura(ultimo);
  fita.hidden = true;
  zerarFita();
}

function guardarNaLeitura(dados) {
  const texto = (dados.texto || "").trim();
  if (!texto) return;

  $("etiqueta-leitura").textContent = "Transcrição";
  $("etiqueta-leitura").classList.remove("apagada");
  $("nota-texto").textContent = legenda(dados);

  // A abertura vai em corpo grande e o resto segue como texto corrido, que e
  // o tratamento que a referencia da a uma pagina.
  const [abertura, resto] = partir(texto);

  $("abertura").textContent = abertura;
  $("corpo").innerHTML = "";
  if (resto) {
    const p = document.createElement("p");
    p.textContent = resto;
    $("corpo").appendChild(p);
  }
}

// Corta a abertura do resto. Transcricao automatica costuma vir com pouca
// pontuacao, entao procurar fim de frase nao basta: sem essa rede, um texto
// sem ponto nenhum viraria uma abertura gigante em corpo de titulo.
const TETO_ABERTURA = 240;

function partir(texto) {
  if (texto.length <= TETO_ABERTURA) return [texto, ""];

  const inicio = texto.slice(0, TETO_ABERTURA);
  const fimDeFrase = Math.max(
    inicio.lastIndexOf(". "), inicio.lastIndexOf("! "), inicio.lastIndexOf("? ")
  );

  // Fim de frase e o corte preferido, desde que nao deixe a abertura curta
  // demais. Sem ele, corta na ultima palavra inteira.
  let corte = fimDeFrase > 60 ? fimDeFrase + 1 : inicio.lastIndexOf(" ");
  if (corte <= 0) corte = TETO_ABERTURA;

  return [texto.slice(0, corte).trim(), texto.slice(corte).trim()];
}

// O servidor esquece o texto assim que a tela o recebe. A partir daqui a
// unica copia e a do navegador de quem pediu, e e por isso que a pagina avisa
// que fechar sem copiar perde a transcricao.
function esquecer(id) {
  fetch(BACKEND + "/trabalho/" + id, { method: "DELETE" }).catch(() => {});
}

// ------------------------------------------------------------------ escutar

function escutar(id) {
  if (desescutar) desescutar();
  desescutar = onSnapshot(doc(db, "trabalhos", id), (instantaneo) => {
    if (!instantaneo.exists()) return;
    const dados = instantaneo.data();

    if (dados.estado === "erro") {
      mostrarErro(dados.codigo || "FALHA_INESPERADA", dados.detalhe || "");
      desescutar();
      desescutar = null;
      return;
    }
    if (dados.estado === "pronto") {
      concluir(dados);
      esquecer(id);
      desescutar();
      desescutar = null;
      return;
    }
    const indice = ETAPAS.indexOf(dados.estado);
    if (indice >= 0) irPara(indice);
    if (dados.estado === "na fila" && dados.plataforma === "arquivo") mostrarAviso("Áudio recebido. Aguardando a vez na fila de transcrição.");
    if (dados.estado === "convertendo") esconderAviso();
    if (dados.parteAtual) mostrarAviso("Transcrevendo trecho " + dados.parteAtual + " de " + dados.partesTotal + ". O resultado será um único texto.");
  }, () => mostrarErro("SEM_RESPOSTA", ""));
}

// ------------------------------------------------------------------- comecar

// Memoria da aba, e so dela: um Map que morre quando a pagina fecha. Nao usa
// localStorage nem sessionStorage, entao nada e guardado e a politica de
// privacidade continua valendo palavra por palavra.
//
// Serve pro caso mais comum de desperdicio: a mesma pessoa colar de novo o
// link que ela acabou de transcrever. Antes isso custava download, banda e
// cota da Groq de novo; agora custa zero.
const jaTranscrito = new Map();

function chaveDoLink(url) {
  try {
    const u = new URL(url);
    const host = u.hostname.toLowerCase().replace(/^www\./, "");
    const caminho = u.pathname.replace(/\/+$/, "");
    if (host === "youtu.be") return "youtube.com/watch?v=" + caminho.replace(/^\//, "");
    if (host.endsWith("youtube.com") && u.searchParams.get("v")) {
      return "youtube.com/watch?v=" + u.searchParams.get("v");
    }
    return host + caminho;
  } catch {
    return url.trim().toLowerCase();
  }
}

async function comecar(url, audio) {
  const guardado = !audio && jaTranscrito.get(chaveDoLink(url));
  if (guardado) {
    ocupar(true);
    fita.hidden = false;
    zerarFita();
    concluir(guardado);
    return;
  }

  if (desafio.sitekey && desafio.expiraEm <= Date.now() / 1000 + 60) renovarDesafio();
  if (desafio.sitekey && !desafio.sessao) {
    mostrarErro("DESAFIO_FALTANDO", "");
    botao.disabled = true;
    fita.hidden = false;
    return;
  }

  ocupar(true);
  fita.hidden = false;
  zerarFita();
  irPara(0);
  ligarRelogio();
  rastejar();

  let resposta;
  try {
    resposta = audio ? await enviarArquivo(audio) : await pedirComPaciencia(url);
  } catch {
    mostrarErro(audio ? "ENVIO_INTERROMPIDO" : "SEM_RESPOSTA", "");
    return;
  }
  esconderAviso();

  const corpo = await resposta.json().catch(() => ({}));
  if (!resposta.ok) {
    mostrarErro(corpo.codigo || "FALHA_INESPERADA", corpo.detalhe || "");
    if (corpo.codigo === "SESSAO_EXPIRADA") renovarDesafio();
    return;
  }
  escutar(corpo.id);
}

// ------------------------------------------------- esperar o servidor acordar

// O Render grátis derruba a instância depois de 15 minutos parada, e acordar
// leva até uns 50 segundos. Enquanto ela sobe, o próprio Render responde uma
// página de erro que não traz cabeçalho de CORS, e aí o fetch nem chega a ser
// lido: estoura antes. O plano já sabia desse sono; o que faltava era a tela
// esperar em vez de mandar a pessoa tentar de novo.
const ESPERAS = [0, 4000, 8000, 12000, 20000, 25000];   // ~70s no total

function mostrarAviso(frase) {
  $("recado-frase").textContent = frase;
  $("recado-codigo").textContent = "";
  recado.classList.add("esperando");
  recado.hidden = false;
}

function esconderAviso() {
  recado.classList.remove("esperando", "quebrado");
  recado.hidden = true;
}

async function pedirComPaciencia(url) {
  let ultimoErro;
  for (let i = 0; i < ESPERAS.length; i++) {
    if (ESPERAS[i]) await new Promise((r) => setTimeout(r, ESPERAS[i]));
    try {
      const r = await fetch(BACKEND + (url === null ? "/config" : "/transcrever"), url === null ? { cache: "no-store" } : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, sessao: desafio.sessao || "" })
      });
      // 502 e 503 são o Render dizendo que a instância ainda está subindo.
      // Qualquer outro código é resposta de verdade e não se repete.
      if (r.status !== 502 && r.status !== 503) return r;
      ultimoErro = new Error("instância subindo");
    } catch (e) {
      ultimoErro = e;
    }
    if (i === 0) mostrarAviso("Acordando o servidor. Ele dorme quando fica um tempo sem uso, e a primeira transcrição depois disso leva até um minuto.");
  }
  throw ultimoErro;
}

async function enviarArquivo(audio) {
  const resposta = await pedirComPaciencia(null);
  if (!resposta.ok) return resposta;
  const config = await resposta.json();
  if (!config.limiteEnvioBytes) {
    return new Response(JSON.stringify({ codigo: "ENVIO_INDISPONIVEL" }), { status: 503 });
  }
  const cabecalhos = {
    "Content-Type": "application/octet-stream",
    "X-Decupa-Nome": encodeURIComponent(audio.name),
    "X-Decupa-Sessao": desafio.sessao || ""
  };
  // Compatibilidade enquanto o Render ainda publica o backend novo.
  if (!config.envioEmPartes) {
    if (audio.size > config.limiteEnvioBytes) return new Response(JSON.stringify({ codigo: "ENVIO_INDISPONIVEL" }), { status: 503 });
    return fetch(BACKEND + "/transcrever-arquivo", { method: "POST", headers: cabecalhos, body: audio });
  }
  const envio = crypto.randomUUID().replaceAll("-", "");
  const fatia = Math.min(config.fatiaEnvioBytes, config.limiteEnvioBytes);
  if (!Number.isSafeInteger(fatia) || fatia <= 0) throw new Error("configuração de envio inválida");
  let confirmado = false;
  try {
    for (let posicao = 0; posicao < audio.size; posicao += fatia) {
      const bloco = audio.slice(posicao, posicao + fatia);
      mostrarAviso("Enviando bloco " + (Math.floor(posicao / fatia) + 1) + " de " + Math.ceil(audio.size / fatia) + ". Mantenha a página aberta.");
      let r;
      for (let tentativa = 0; tentativa < 3; tentativa++) {
        if (tentativa) await new Promise((resolve) => setTimeout(resolve, tentativa * 2000));
        try {
          r = await fetch(BACKEND + "/transcrever-arquivo", {
            method: "POST",
            headers: { ...cabecalhos, "X-Decupa-Envio": envio, "X-Decupa-Total": String(audio.size), "X-Decupa-Posicao": String(posicao) },
            body: bloco
          });
          if (![502, 503, 504].includes(r.status)) break;
        } catch (erro) {
          if (tentativa === 2) throw erro;
        }
      }
      if (!r.ok) return r;
      const dados = await r.json();
      if (dados.id !== envio || dados.recebidos !== Math.min(posicao + fatia, audio.size)) throw new Error("confirmação de bloco inválida");
      if (dados.recebidos === audio.size) {
        confirmado = true;
        return new Response(JSON.stringify({ id: envio }), { status: 202 });
      }
    }
  } finally {
    if (!confirmado) fetch(BACKEND + "/envio/" + envio, { method: "DELETE", headers: { "X-Decupa-Sessao": desafio.sessao || "" } }).catch(() => {});
  }
}

// ------------------------------------------------------------------ desafio

function renovarDesafio() {
  desafio.sessao = null;
  desafio.expiraEm = 0;
  window.decupaTurnstileToken = null;
  try { sessionStorage.removeItem(CHAVE_SESSAO); } catch {}
  botao.disabled = true;
  $("desafio").hidden = false;
  if (window.turnstile) turnstile.reset();
}

async function validarDesafio(token) {
  if (desafio.validando || desafio.sessao) return;
  desafio.validando = true;
  try {
    let resposta;
    for (const espera of ESPERAS) {
      if (espera) await new Promise((r) => setTimeout(r, espera));
      try {
        resposta = await fetch(BACKEND + "/sessao", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ desafio: token })
        });
        if (resposta.status !== 502 && resposta.status !== 503) break;
      } catch {}
      resposta = null;
      fita.hidden = false;
      mostrarAviso("Acordando o servidor para concluir a verificação...");
    }
    if (!resposta) throw new Error("SEM_RESPOSTA");
    const dados = await resposta.json();
    if (!resposta.ok) throw new Error(dados.codigo || "DESAFIO_RECUSADO");
    desafio.sessao = dados.sessao;
    desafio.expiraEm = dados.expiraEm;
    window.decupaTurnstileToken = null;
    try { sessionStorage.setItem(CHAVE_SESSAO, JSON.stringify(dados)); } catch {}
    botao.disabled = false;
    esconderAviso();
    fita.hidden = true;
  } catch (erro) {
    if (erro.message === "DESAFIO_INDISPONIVEL") mostrarErro(erro.message, "");
    else avisarDesafioQuebrado();
    botao.disabled = true;
  } finally {
    desafio.validando = false;
  }
}

function ligarDesafio() {
  const caixa = $("desafio");
  desafio.sitekey = caixa.dataset.sitekey;
  botao.disabled = true;
  try {
    const guardada = JSON.parse(sessionStorage.getItem(CHAVE_SESSAO) || "null");
    if (guardada?.expiraEm > Date.now() / 1000 + 60) {
      desafio.sessao = guardada.sessao;
      desafio.expiraEm = guardada.expiraEm;
      botao.disabled = false;
      esconderAviso();
      fita.hidden = true;
      caixa.hidden = true;
      return;
    }
  } catch {}
  caixa.hidden = false;
  if (window.decupaTurnstileToken) validarDesafio(window.decupaTurnstileToken);

  // Se a chave estiver mal configurada, o Turnstile nao monta o desafio e nao
  // dispara evento nenhum: nem sucesso, nem erro. Sem esta checagem a pessoa
  // so descobre depois de colar o link e clicar, e o motivo fica escondido.
  setTimeout(() => { if (!caixa.querySelector("iframe")) avisarDesafioQuebrado(); }, 12000);
}

window.addEventListener("decupa-turnstile", (e) => validarDesafio(e.detail));
window.addEventListener("decupa-turnstile-erro", avisarDesafioQuebrado);
window.addEventListener("decupa-turnstile-expirou", () => { if (!desafio.sessao) renovarDesafio(); });

function avisarDesafioQuebrado() {
  if (desafio.sessao || recado.classList.contains("quebrado")) return;
  $("recado-frase").textContent =
    "A verificação de que você não é um robô não carregou, e sem ela o servidor recusa o pedido. Atualize a página; se continuar, o problema é de configuração e não seu.";
  $("recado-codigo").textContent = "DESAFIO_NAO_CARREGOU";
  recado.classList.remove("esperando");
  recado.classList.add("quebrado");
  recado.hidden = false;
  fita.hidden = false;
}

// -------------------------------------------------------------------- ligacao

$("forma").addEventListener("submit", (e) => {
  e.preventDefault();
  if (ocupado) return;
  const url = campo.value.trim();
  const audio = arquivo.files[0];
  let erro = "";
  if (audio) {
    if (!audio.size) erro = "ARQUIVO_VAZIO";
    else if (!EXTENSOES_AUDIO.test(audio.name)) erro = "FORMATO_NAO_SUPORTADO";
  } else if (!url) erro = "URL_AUSENTE";
  if (erro) { mostrarErro(erro, ""); fita.hidden = false; return; }
  comecar(url, audio);
});

arquivo.addEventListener("change", () => {
  if (arquivo.files.length) {
    campo.value = "";
    selo.classList.remove("aceso");
  }
  atualizarArquivo();
});
removerArquivo.addEventListener("click", () => {
  arquivo.value = "";
  atualizarArquivo();
  campo.focus();
});

// O selo acende dentro do proprio campo, assim que o link e colado, antes de
// clicar em nada. E uma das tres assinaturas visuais do projeto.
const NOMES = { instagram: "Instagram", tiktok: "TikTok", youtube: "YouTube", "youtu.be": "YouTube", facebook: "Facebook", "fb.watch": "Facebook" };
campo.addEventListener("input", () => {
  arquivo.value = "";
  atualizarArquivo();
  const v = campo.value.toLowerCase();
  const chave = Object.keys(NOMES).find((k) => v.includes(k));
  if (chave) selo.textContent = NOMES[chave];
  selo.classList.toggle("aceso", !!chave);
});

$("fechar").addEventListener("click", fechar);
fundoModal.addEventListener("click", (e) => { if (e.target === fundoModal) fechar(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && fundoModal.classList.contains("aberto")) fechar();
});

$("copiar").addEventListener("click", async (e) => {
  try {
    await navigator.clipboard.writeText($("modal-texto").textContent);
    e.target.textContent = "Copiado";
  } catch {
    e.target.textContent = "Não deu para copiar";
  }
  setTimeout(() => { e.target.textContent = "Copiar tudo"; }, 1800);
});

$("baixar").addEventListener("click", () => {
  const laco = document.createElement("a");
  laco.href = URL.createObjectURL(new Blob([$("modal-texto").textContent], { type: "text/plain;charset=utf-8" }));
  laco.download = "decupa.txt";
  laco.click();
  URL.revokeObjectURL(laco.href);
});

digitarMarca();
pintar();
ligarDesafio();
