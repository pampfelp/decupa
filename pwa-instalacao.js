// Banner de instalação do app (PWA) — Android e iPhone. Sem dependência do
// resto do projeto: cria o próprio HTML e injeta o próprio CSS.
//
//   import { iniciarBannerInstalacao, podeInstalar, instalarAgora } from "./pwa-instalacao.js";
//   iniciarBannerInstalacao({ icone: "icon-192.png", nomeApp: "Financeiro" });
//
// Chamar UMA vez, logo no começo do app.js, antes de qualquer await: o
// evento do Android dispara cedo e, se ninguém estiver escutando, passa.
//
// Por que existem dois caminhos (padroes/pwa-checklist.md, regra 8):
//  - Android, Chrome e Edge disparam `beforeinstallprompt`. Guardamos o
//    evento e chamamos .prompt() no clique do nosso botão.
//  - iPhone e iPad (Safari) NÃO têm esse evento. Não existe API. O único jeito
//    é ensinar o caminho: Compartilhar → Adicionar à Tela de Início.
// Um sistema que só trata o primeiro deixa o iPhone sem instalação nenhuma,
// sem erro e sem aviso, e ninguém percebe até um cliente perguntar.

let evento = null;
let cfg = { icone: "icon-192.png", nomeApp: "este app", dispensaDias: 14, atrasoMs: 2500 };
const CHAVE = "pwa_dispensado_ate";

export const ehIOS = () =>
  /iphone|ipad|ipod/i.test(navigator.userAgent)
  // iPad novo se anuncia como Mac; o que o entrega é a tela de toque
  || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

export const jaInstalado = () =>
  window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;

export const podeInstalar = () => !jaInstalado() && (!!evento || ehIOS());

const lerDispensa = () => { try { return Number(localStorage.getItem(CHAVE) || 0); } catch { return 0; } };
const gravarDispensa = dias => { try { localStorage.setItem(CHAVE, String(Date.now() + dias * 86400000)); } catch {} };

const SHARE = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:15px;height:15px;vertical-align:-3px"><path d="M12 3v12M8 7l4-4 4 4"/><path d="M5 12v7a2 2 0 002 2h10a2 2 0 002-2v-7"/></svg>`;

function injetarCss() {
  if (document.getElementById("pwa-css")) return;
  const s = document.createElement("style");
  s.id = "pwa-css";
  s.textContent = `
  .pwa-banner{position:fixed;z-index:9000;right:20px;bottom:20px;width:min(420px,calc(100vw - 24px));display:flex;align-items:center;gap:12px;
    padding:12px 14px;border-radius:16px;background:var(--panel,#1c1c1c);color:var(--ink,#fff);border:1px solid var(--line,#333);
    box-shadow:0 16px 44px rgba(0,0,0,.35);font:13px/1.4 var(--sans,system-ui,sans-serif)}
  .pwa-banner img{width:44px;height:44px;border-radius:11px;flex:none}
  .pwa-banner .tx{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px;color:var(--ink-soft,#bbb);font-size:12px}
  .pwa-banner .tx b{color:var(--ink,#fff);font-size:13px}
  .pwa-banner button{font:inherit;cursor:pointer}
  .pwa-banner .ok{border:0;border-radius:10px;padding:8px 13px;font-weight:600;background:var(--accent,#9B5DE5);color:#fff}
  .pwa-banner .x{border:0;background:none;color:inherit;opacity:.6;font-size:20px;line-height:1;padding:2px 6px}
  .pwa-fundo{position:fixed;inset:0;z-index:9100;background:rgba(0,0,0,.6);display:grid;place-items:center;padding:16px}
  .pwa-caixa{width:min(440px,100%);border-radius:18px;padding:22px;background:var(--panel,#1c1c1c);color:var(--ink,#fff);
    font:14px/1.55 var(--sans,system-ui,sans-serif)}
  .pwa-caixa h3{margin:0 0 14px;font-size:17px}
  .pwa-caixa ol{margin:0 0 18px;padding-left:20px;display:flex;flex-direction:column;gap:10px;color:var(--ink-soft,#bbb)}
  .pwa-caixa b{color:var(--ink,#fff)}
  @media(max-width:900px){.pwa-banner{left:12px;right:12px;width:auto;bottom:calc(12px + env(safe-area-inset-bottom))}}`;
  document.head.appendChild(s);
}

function passoAPassoIOS() {
  document.querySelector(".pwa-banner")?.remove();
  injetarCss();
  const f = document.createElement("div");
  f.className = "pwa-fundo";
  f.innerHTML = `<div class="pwa-caixa" role="dialog" aria-label="Instalar no iPhone">
    <h3>Instalar no iPhone</h3>
    <ol>
      <li>Abra este endereço no <b>Safari</b>. Em outros navegadores o iPhone pode não oferecer a instalação.</li>
      <li>Toque em <b>Compartilhar</b> ${SHARE}, na barra de baixo.</li>
      <li>Role a lista e toque em <b>Adicionar à Tela de Início</b>.</li>
      <li>Toque em <b>Adicionar</b>, no canto de cima.</li>
    </ol>
    <button class="ok" style="border:0;border-radius:10px;padding:9px 16px;font:inherit;font-weight:600;cursor:pointer;background:var(--accent,#9B5DE5);color:#fff">Entendi</button>
  </div>`;
  f.addEventListener("click", e => { if (e.target === f || e.target.classList.contains("ok")) f.remove(); });
  document.body.appendChild(f);
}

export async function instalarAgora() {
  if (evento) {
    const ev = evento; evento = null;
    document.querySelector(".pwa-banner")?.remove();   // sai na hora: userChoice só resolve depois da escolha
    ev.prompt();
    try { await ev.userChoice; } catch {}
    return;
  }
  if (ehIOS()) passoAPassoIOS();
}

function mostrar() {
  if (document.querySelector(".pwa-banner") || !podeInstalar() || Date.now() < lerDispensa()) return;
  injetarCss();
  const ios = !evento && ehIOS();
  const b = document.createElement("div");
  b.className = "pwa-banner";
  b.setAttribute("role", "dialog");
  b.innerHTML = `<img src="${cfg.icone}" alt="">
    <div class="tx"><b>Instale ${cfg.nomeApp} no seu ${ios ? "iPhone" : "celular"}</b>
      <span>${ios ? `Toque em Compartilhar ${SHARE} e depois em Adicionar à Tela de Início.`
                   : "Abre em tela cheia, direto da tela inicial."}</span></div>
    <button class="ok">${ios ? "Como instalar" : "Instalar"}</button>
    <button class="x" aria-label="Agora não">×</button>`;
  b.querySelector(".ok").addEventListener("click", instalarAgora);
  b.querySelector(".x").addEventListener("click", () => { gravarDispensa(cfg.dispensaDias); b.remove(); });
  document.body.appendChild(b);
}

export function iniciarBannerInstalacao(opcoes = {}) {
  cfg = { ...cfg, ...opcoes };
  window.addEventListener("beforeinstallprompt", e => {
    e.preventDefault();
    evento = e;
    setTimeout(mostrar, 400);           // o evento pode chegar depois do atraso
  });
  window.addEventListener("appinstalled", () => { evento = null; document.querySelector(".pwa-banner")?.remove(); });
  if (!jaInstalado()) setTimeout(mostrar, cfg.atrasoMs);
}
