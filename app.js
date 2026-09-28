// Decupa — a tela. Um arquivo so, ate o projeto pedir divisao.
//
// O POST devolve o id do trabalho na hora e nao espera nada. Daí em diante
// quem manda e o Firestore: o backend escreve o andamento no documento e esta
// tela escuta por onSnapshot. Por isso nada aqui fica perguntando "ja acabou?".

import { db } from "./firebase-init.js?v=1";
import { doc, onSnapshot } from "https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js";
import { iniciarBannerInstalacao } from "./pwa-instalacao.js?v=1";

// Antes de qualquer await: o evento beforeinstallprompt do Android dispara
// cedo e, se ninguem estiver escutando, passa e nao volta.
iniciarBannerInstalacao({ icone: "icon-192.png", nomeApp: "o Decupa" });
if ("serviceWorker" in navigator) {
  addEventListener("load", () => navigator.serviceWorker.register("./service-worker.js"));
}

const BACKEND = "https://decupa.onrender.com";
const ETAPAS = ["baixando", "convertendo", "transcrevendo", "pronto"];
const PASSO_MS = 2000;

// Cada codigo do backend vira uma frase que diz o que fazer. "Erro ao
// processar" nao e acionavel; "o Instagram recusou" e.
const FRASES = {
  YOUTUBE_BLOQUEADO: "O YouTube não deixa baixar a partir de um servidor. Por enquanto o Decupa cobre Instagram, TikTok e Facebook.",
  INSTAGRAM_BLOQUEADO: "O Instagram recusou esse link agora. Costuma voltar sozinho em alguns minutos.",
  PLATAFORMA_NAO_SUPORTADA: "Esse link não é do Instagram, do TikTok, do YouTube nem do Facebook.",
  DOWNLOAD_FALHOU: "Não deu para baixar esse vídeo. Ele pode ser privado, ter sido removido, ou o link pode estar incompleto.",
  CONVERSAO_FALHOU: "O vídeo baixou, mas o áudio dele não pôde ser convertido.",
  GROQ_RECUSOU: "A transcrição falhou no servidor. Vale tentar de novo.",
  GROQ_SEM_CHAVE: "A transcrição está desligada no servidor.",
  URL_AUSENTE: "Cole um link antes de começar.",
  CORPO_INVALIDO: "Não entendi esse link.",
  TRABALHO_NAO_ENCONTRADO: "Esse trabalho não existe mais.",
  FALHA_INESPERADA: "Alguma coisa quebrou no meio do caminho. Vale tentar de novo.",
  SEM_RESPOSTA: "Não consegui falar com o servidor. Ele pode estar acordando: espere uns 50 segundos e tente de novo.",
  LIMITE_DIARIO_ATINGIDO: "Você já usou as transcrições de hoje. O limite volta amanhã. Link que já foi transcrito antes continua liberado, porque não custa nada.",
  VIDEO_LONGO_DEMAIS: "Esse vídeo passa de 10 minutos. Por enquanto o Decupa cobre vídeo curto.",
  DESAFIO_FALTANDO: "A verificação de que você não é um robô não carregou. Atualize a página.",
  DESAFIO_RECUSADO: "A verificação de que você não é um robô não passou. Atualize a página e tente de novo."
};

// Fica vazio enquanto o Turnstile nao estiver ligado no servidor. Quem decide
// e o /config, nao esta tela: ligar o desafio e mexer no Render, nao publicar
// versao nova do site.
let desafio = { sitekey: null, token: null, widget: null };

const $ = (id) => document.getElementById(id);
const campo = $("campo"), selo = $("selo"), botao = $("botao"), fita = $("fita");
const cheio = $("cheio"), marcador = $("marcador");
const caixas = [...document.querySelectorAll(".etapa")];
const recado = $("recado"), certo = $("certo"), fundoModal = $("fundo-modal");

let etapaAtual = -1, progresso = 0, relogio = null, desescutar = null;
let ultimo = null;

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
  $("recado-frase").textContent = FRASES[codigo] || FRASES.FALHA_INESPERADA;
  $("recado-codigo").textContent = detalhe ? codigo + " · " + detalhe : codigo;
  recado.hidden = false;
  botao.disabled = false;
}

// ------------------------------------------------------------ o fim do trabalho

function concluir(dados) {
  clearInterval(relogio);
  etapaAtual = 3;
  progresso = 1;
  pintar();

  ultimo = dados;

  certo.hidden = false;
  certo.classList.remove("rodando");
  void certo.offsetWidth;
  certo.classList.add("rodando");

  setTimeout(() => abrirModal(dados), 950);
}

function legenda(dados) {
  const partes = [];
  if (dados.plataforma) partes.push(dados.plataforma);
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

  const cache = $("marca-cache");
  if (dados.veioDoCache && dados.transcritoEm) {
    cache.textContent = "Já transcrito antes, em " + formatarData(dados.transcritoEm);
    cache.hidden = false;
  } else {
    cache.hidden = true;
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

function formatarData(valor) {
  const d = valor && valor.toDate ? valor.toDate() : new Date(valor);
  return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "long" }) +
         " às " + d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

// ------------------------------------------------------------------ escutar

function escutar(id) {
  if (desescutar) desescutar();
  desescutar = onSnapshot(doc(db, "trabalhos", id), (instantaneo) => {
    if (!instantaneo.exists()) return;
    const dados = instantaneo.data();

    if (dados.estado === "erro") {
      mostrarErro(dados.codigo || "FALHA_INESPERADA", "");
      desescutar();
      desescutar = null;
      return;
    }
    if (dados.estado === "pronto") {
      concluir(dados);
      botao.disabled = false;
      desescutar();
      desescutar = null;
      return;
    }
    const indice = ETAPAS.indexOf(dados.estado);
    if (indice >= 0) irPara(indice);
  }, () => mostrarErro("SEM_RESPOSTA", ""));
}

// ------------------------------------------------------------------- comecar

async function comecar(url) {
  botao.disabled = true;
  fita.hidden = false;
  zerarFita();
  irPara(0);
  ligarRelogio();
  rastejar();

  let resposta;
  try {
    resposta = await fetch(BACKEND + "/transcrever", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, desafio: desafio.token || "" })
    });
  } catch {
    mostrarErro("SEM_RESPOSTA", "");
    return;
  }

  const corpo = await resposta.json().catch(() => ({}));
  if (!resposta.ok) {
    mostrarErro(corpo.codigo || "FALHA_INESPERADA", "");
    renovarDesafio();
    return;
  }
  renovarDesafio();
  escutar(corpo.id);
}

// ------------------------------------------------------------------ desafio

// O token do Turnstile vale uma vez so. Depois de usar, pede outro, senao a
// segunda transcricao da sessao seria recusada sem a pessoa entender por que.
function renovarDesafio() {
  if (desafio.widget === null || !window.turnstile) return;
  desafio.token = null;
  turnstile.reset(desafio.widget);
}

async function ligarDesafio() {
  let config;
  try {
    config = await (await fetch(BACKEND + "/config")).json();
  } catch {
    return;  // servidor dormindo: o teto por IP segura, e o POST avisa depois
  }
  if (!config.turnstile) return;

  desafio.sitekey = config.turnstile;
  await new Promise((pronto) => {
    const s = document.createElement("script");
    s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    s.async = true;
    s.onload = pronto;
    s.onerror = pronto;
    document.head.appendChild(s);
  });
  if (!window.turnstile) return;

  const caixa = document.getElementById("desafio");
  caixa.hidden = false;
  desafio.widget = turnstile.render(caixa, {
    sitekey: desafio.sitekey,
    size: "flexible",
    callback: (t) => { desafio.token = t; }
  });
}

// -------------------------------------------------------------------- ligacao

$("forma").addEventListener("submit", (e) => {
  e.preventDefault();
  const url = campo.value.trim();
  if (!url) { mostrarErro("URL_AUSENTE", ""); fita.hidden = false; return; }
  comecar(url);
});

// O selo acende dentro do proprio campo, assim que o link e colado, antes de
// clicar em nada. E uma das tres assinaturas visuais do projeto.
const NOMES = { instagram: "Instagram", tiktok: "TikTok", youtube: "YouTube", "youtu.be": "YouTube", facebook: "Facebook", "fb.watch": "Facebook" };
campo.addEventListener("input", () => {
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
