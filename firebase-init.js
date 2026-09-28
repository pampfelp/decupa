// Config web do Firebase. E publica por design: quem protege o dado sao as
// firestore.rules, nao o segredo desta chave. A credencial de servico, essa
// sim secreta, vive so na variavel de ambiente do Render.

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js";

const firebaseConfig = {
  apiKey: "AIzaSyBtoiboE0yqLytPI_KUYgfF6yv_s_zeReQ",
  authDomain: "decupa-2660b.firebaseapp.com",
  projectId: "decupa-2660b",
  storageBucket: "decupa-2660b.firebasestorage.app",
  messagingSenderId: "847289550957",
  appId: "1:847289550957:web:8a4a1d4258abe86068d764"
};

export const app = initializeApp(firebaseConfig);
export const db = getFirestore(app);
