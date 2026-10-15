// PlaudVault GUI: vanilla JS, sin build.
// Cero rastro: nada personal en la URL (historial), ni <form> (autocompletado),
// ni campo type=password (gestor de contraseñas). Todo el texto que viene de la DB o de
// los ficheros se mete con textContent; solo el HTML de Markdown (renderizado en
// el servidor con html=False) va por innerHTML.
"use strict";
const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};

// Audio SOLO por <audio src> + peticiones Range. Nada de fetch → Blob: el spike
// (docs/03 paso 23) vio a Chromium guardar Blobs grandes en <perfil>/blob_storage
// y a Firefox en /tmp/mozilla-temp-*, es decir, el mp3 descifrado en disco.
let state = { recs: [], listSig: "", current: null, locked: true, cryptoEnabled: true };

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}

// ---------- lista (se refresca sola: el sync puede estar publicando carpetas) ----------
async function loadList() {
  const { recordings, locked, crypto_enabled = true } = await api("/api/recordings");
  if (locked !== state.locked || crypto_enabled !== state.cryptoEnabled) {
    state.locked = locked; state.cryptoEnabled = crypto_enabled; renderLock();
  }
  const sig = JSON.stringify(recordings);
  if (sig === state.listSig) return;          // nada nuevo: no repintar (no perder el scroll)
  state.listSig = sig; state.recs = recordings;
  const nav = $("list"); nav.replaceChildren();
  let group = null;
  for (const r of recordings) {
    if (r.group !== group) { group = r.group; nav.append(el("h2", { textContent: group || "(raíz)" })); }
    // Botón y no <a href="#clave">: el título de la grabación no debe acabar en el historial.
    const a = el("button", { className: "rec", textContent: r.title, title: r.title, onclick: () => openRec(r.key) });
    a.dataset.key = r.key;
    if (!r.indexed) a.append(el("span", { className: "badge", textContent: "sin indexar" }));
    if (state.current === r.key) a.classList.add("active");
    nav.append(a);
  }
}

// ---------- una grabación ----------
async function openRec(key) {
  state.current = key;
  document.querySelectorAll("nav .rec").forEach(a => a.classList.toggle("active", a.dataset.key === key));
  $("welcome").hidden = true; $("results").hidden = true; $("rec").hidden = false;
  $("rec-title").textContent = state.recs.find(r => r.key === key)?.title || key.split("/").pop();
  const rec = await api("/api/rec/" + encodeURI(key));
  const tabs = $("tabs"); tabs.replaceChildren();
  rec.docs.forEach((name, i) => {
    const label = name.replace(/^notes\/\d+_/, "").replace(/\.md$/, "");
    const b = el("button", { role: "tab", textContent: label, onclick: () => showDoc(key, name, b) });
    tabs.append(b);
    if (i === 0) showDoc(key, name, b);
  });
  if (!rec.docs.length) $("doc").textContent = "Esta grabación no tiene texto.";
  renderAudio(rec.has_audio);
}

async function showDoc(key, name, btn) {
  document.querySelectorAll("#tabs button").forEach(b => b.setAttribute("aria-selected", b === btn));
  const { html } = await api("/api/doc/" + encodeURI(key) + "?name=" + encodeURIComponent(name));
  $("doc").innerHTML = html;   // seguro: Markdown sin HTML crudo + CSP
}

// ---------- audio ----------
function dropAudio() {
  const old = document.querySelector("audio");
  if (old) { old.pause(); old.removeAttribute("src"); old.load(); }  // suelta el buffer
}

async function renderAudio(hasAudio) {
  dropAudio();
  const box = $("audio-box"); box.replaceChildren();
  if (!hasAudio) return;
  if (state.locked) { box.append(el("p", { className: "badge", textContent: "🔒 Desbloquea el vault para escuchar el audio." })); return; }
  box.append(el("audio", { controls: true, preload: "metadata",
                          src: "/api/audio/" + encodeURI(state.current) }));
}

// ---------- bloqueo ----------
function renderLock() {
  const bar = $("lock-bar"); bar.replaceChildren();
  if (!state.cryptoEnabled) {
    bar.append(el("span", { textContent: "Audio sin cifrar (configuración del vault)" }));
    return;
  }
  if (state.locked) {
    // type=text enmascarado con CSS: un campo type=password activa el gestor de
    // contraseñas («¿Guardar contraseña?»). Sin corrector ortográfico: algunos
    // envían lo que escribes a un servicio en la nube.
    const masked = CSS.supports("-webkit-text-security", "disc");
    const pass = el("input", { type: masked ? "text" : "password", placeholder: "Passphrase",
                               autocomplete: "off", spellcheck: false, autocapitalize: "off" });
    pass.setAttribute("autocorrect", "off");
    pass.classList.add("secret");
    const msg = el("span");
    const go = async () => {
      msg.textContent = "Derivando clave…";
      try { await api("/api/unlock", { method: "POST", headers: { "Content-Type": "application/json" },
                                       body: JSON.stringify({ passphrase: pass.value }) });
            pass.value = ""; state.locked = false; renderLock(); if (state.current) openRec(state.current); }
      catch (e) { msg.textContent = e.message; msg.className = "err"; }
    };
    pass.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
    bar.append(pass, el("button", { textContent: "Desbloquear", onclick: go }), msg);
  } else {
    bar.append(el("span", { textContent: "🔓 Vault desbloqueado" }),
      el("button", { textContent: "Bloquear", onclick: async () => {
        await api("/api/lock", { method: "POST" }); state.locked = true; renderLock();
        if (state.current) renderAudio(true); } }));
  }
}

// ---------- búsqueda ----------
function fragment(text) {
  // \x02 … \x03 = coincidencia (marcadores de snippet()). DOM puro, sin innerHTML.
  const p = el("p");
  text.split(/(\x02[^\x03]*\x03)/).forEach(part =>
    p.append(part.startsWith("\x02") ? el("mark", { textContent: part.slice(1, -1) }) : part));
  return p;
}

async function search(q) {
  dropAudio();
  $("welcome").hidden = true; $("rec").hidden = true;
  const box = $("results"); box.hidden = false; box.replaceChildren(el("h1", { textContent: "Resultados: " + q }));
  try {
    const { results } = await api("/api/search?q=" + encodeURIComponent(q));
    if (!results.length) box.append(el("p", { textContent: "Sin resultados." }));
    for (const r of results) {
      const title = r.key ? r.key.split("/").pop() : r.filename + " (carpeta no encontrada)";
      const link = el("button", { className: "rec", textContent: title, disabled: !r.key, onclick: () => openRec(r.key) });
      box.append(el("div", { className: "hit" }, link, el("span", { className: "badge", textContent: r.kind || "" }), fragment(r.frag || "")));
    }
  } catch (e) { box.append(el("p", { className: "err", textContent: e.message })); }
}

// ---------- arranque ----------
// Enter en el buscador, sin <form>: un submit hace que el navegador guarde la consulta.
$("q").addEventListener("keydown", e => { if (e.key === "Enter") { const q = $("q").value.trim(); if (q) search(q); } });
renderLock();
loadList();
setInterval(() => { if (!document.hidden) loadList().catch(() => {}); }, 10000);
