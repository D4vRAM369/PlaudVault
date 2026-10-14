// PlaudVault GUI: vanilla JS, sin build. Todo el texto que viene de la DB o de
// los ficheros se mete con textContent; solo el HTML de Markdown (renderizado en
// el servidor con html=False) va por innerHTML.
"use strict";
const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};

// ponytail: modo de audio fijado por el spike (docs/SESION_3_GUI.md).
const AUDIO_MODE = "range";   // "range" = <audio src> con peticiones Range · "blob" = fetch → Blob

let state = { recs: [], listSig: "", current: null, locked: true, blobUrl: null };

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}

// ---------- lista (se refresca sola: el sync puede estar publicando carpetas) ----------
async function loadList() {
  const { recordings, locked } = await api("/api/recordings");
  if (locked !== state.locked) { state.locked = locked; renderLock(); }
  const sig = JSON.stringify(recordings);
  if (sig === state.listSig) return;          // nada nuevo: no repintar (no perder el scroll)
  state.listSig = sig; state.recs = recordings;
  const nav = $("list"); nav.replaceChildren();
  let group = null;
  for (const r of recordings) {
    if (r.group !== group) { group = r.group; nav.append(el("h2", { textContent: group || "(raíz)" })); }
    const a = el("a", { href: "#" + encodeURIComponent(r.key), textContent: r.title, title: r.title });
    if (!r.indexed) a.append(el("span", { className: "badge", textContent: "sin indexar" }));
    if (state.current === r.key) a.classList.add("active");
    nav.append(a);
  }
}

// ---------- una grabación ----------
async function openRec(key) {
  state.current = key;
  document.querySelectorAll("nav a").forEach(a => a.classList.toggle("active", a.getAttribute("href") === "#" + encodeURIComponent(key)));
  $("welcome").hidden = true; $("results").hidden = true; $("rec").hidden = false;
  $("rec-title").textContent = key.split("/").pop();
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
  if (state.blobUrl) { URL.revokeObjectURL(state.blobUrl); state.blobUrl = null; }
}

async function renderAudio(hasAudio) {
  dropAudio();
  const box = $("audio-box"); box.replaceChildren();
  if (!hasAudio) return;
  if (state.locked) { box.append(el("p", { className: "badge", textContent: "🔒 Desbloquea el vault para escuchar el audio." })); return; }
  const url = "/api/audio/" + encodeURI(state.current);
  const audio = el("audio", { controls: true, preload: "metadata" });
  if (AUDIO_MODE === "blob") {
    const r = await fetch(url);
    if (!r.ok) { box.append(el("p", { className: "err", textContent: "No se pudo descifrar el audio." })); return; }
    state.blobUrl = URL.createObjectURL(await r.blob());
    audio.src = state.blobUrl;
  } else {
    audio.src = url;
  }
  box.append(audio);
}

// ---------- bloqueo ----------
function renderLock() {
  const bar = $("lock-bar"); bar.replaceChildren();
  if (state.locked) {
    const pass = el("input", { type: "password", placeholder: "Passphrase", autocomplete: "off" });
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
      const link = el("a", { textContent: title, href: r.key ? "#" + encodeURIComponent(r.key) : "#" });
      box.append(el("div", { className: "hit" }, link, el("span", { className: "badge", textContent: r.kind || "" }), fragment(r.frag || "")));
    }
  } catch (e) { box.append(el("p", { className: "err", textContent: e.message })); }
}

// ---------- arranque ----------
window.addEventListener("hashchange", () => { const k = decodeURIComponent(location.hash.slice(1)); if (k) openRec(k); });
$("search-form").addEventListener("submit", e => { e.preventDefault(); const q = $("q").value.trim(); if (q) search(q); });
renderLock();
loadList().then(() => { const k = decodeURIComponent(location.hash.slice(1)); if (k) openRec(k); });
setInterval(() => { if (!document.hidden) loadList().catch(() => {}); }, 10000);
