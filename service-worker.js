/*
  Service worker do Decupa — ver padroes/pwa-checklist.md.
  Network-first com queda pro cache. Cacheia so o esqueleto estatico, nunca
  chamada de API: o andamento de um trabalho precisa estar sempre fresco.
*/

// Varios apps dele dividem a origem pampfelp.github.io, e o Cache Storage e
// POR ORIGEM. Por isso o prefixo e proprio, e o activate so apaga o que
// comeca com ele. Apagar "tudo que nao e meu" derruba o cache dos outros apps.
const PREFIXO = "decupa-";
const CACHE_NAME = PREFIXO + "shell-v5";
const SHELL = [
  "./",
  "./index.html",
  "./style.css?v=5",
  "./app.js?v=5",
  "./firebase-init.js?v=5",
  "./pwa-instalacao.js",
  "./manifest.json?v=5",
  "./icon-192.png",
  "./termos.html",
  "./privacidade.html",
];

self.addEventListener("install", (e) => {
  // { cache: "reload" } por arquivo em vez de addAll puro: evita que o cache
  // HTTP comum do navegador sirva JS/CSS velho mesmo com o SW novo instalado.
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      Promise.all(SHELL.map((url) =>
        fetch(url, { cache: "reload" }).then((res) => cache.put(url, res)).catch(() => {})))
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((chaves) => Promise.all(
      chaves.filter((k) => k.startsWith(PREFIXO) && k !== CACHE_NAME).map((k) => caches.delete(k))
    ))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = e.request.url;

  const chamadaDeApi =
    url.includes("googleapis.com") ||            // Firestore
    url.includes("gstatic.com/firebasejs") ||    // SDK
    url.includes("onrender.com") ||              // o backend do Decupa
    url.includes("challenges.cloudflare.com");   // Turnstile

  if (e.request.method !== "GET" || chamadaDeApi) return;

  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copia = res.clone();
        caches.open(CACHE_NAME).then((c) => c.put(e.request, copia));
        return res;
      })
      // abre o proprio cache: caches.match() solto procura no de todos os apps
      .catch(() => caches.open(CACHE_NAME).then((c) => c.match(e.request)))
  );
});
