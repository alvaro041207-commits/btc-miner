// Interfaz del chat: maneja la clave, los mensajes y pinta las respuestas del agente.
import { runTurn, Anthropic } from "./agent.js";

const $ = id => document.getElementById(id);
const store = {
  get: k => { try { return localStorage.getItem(k) } catch { return null } },
  set: (k, v) => { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k) } catch {} },
};

let apiKey = store.get("anthropicApiKey") || "";
let history = [];
let busy = null; // AbortController del turno en curso

// ---------- clave ----------
function refreshKeyState() {
  $("keyState").textContent = apiKey ? "Claude Opus 5.5 · listo" : "Sin clave de API: pulsa Ajustes";
}
$("openSettings").onclick = () => { $("apiKey").value = apiKey; $("settings").showModal(); };
$("closeSettings").onclick = () => $("settings").close();
$("forgetKey").onclick = () => { apiKey = ""; store.set("anthropicApiKey", ""); $("apiKey").value = ""; refreshKeyState(); $("settings").close(); };
$("settingsForm").addEventListener("submit", () => {
  apiKey = $("apiKey").value.trim(); store.set("anthropicApiKey", apiKey); refreshKeyState();
});
refreshKeyState();

// ---------- render ----------
function renderMarkdown(el, md) {
  const html = DOMPurify.sanitize(marked.parse(md, { gfm: true, breaks: false }), { ADD_ATTR: ["target"] });
  el.innerHTML = html;
  el.querySelectorAll("a").forEach(a => { a.target = "_blank"; a.rel = "noopener"; });
  el.querySelectorAll("table").forEach(t => {
    if (t.parentElement.classList.contains("table-wrap")) return;
    const w = document.createElement("div"); w.className = "table-wrap"; t.replaceWith(w); w.appendChild(t);
  });
}
function scrollToEnd() { const s = $("scroller"); s.scrollTop = s.scrollHeight; }
function addUser(text) {
  const d = document.createElement("div"); d.className = "msg user"; d.textContent = text;
  $("log").appendChild(d); scrollToEnd();
}
function addBot() {
  const wrap = document.createElement("div"); wrap.className = "msg bot";
  const content = document.createElement("div"); content.className = "content";
  const activity = document.createElement("div"); activity.className = "activity"; activity.textContent = "Pensando";
  wrap.append(content, activity); $("log").appendChild(wrap); scrollToEnd();
  let md = "", queued = false;
  return {
    text(delta) {
      md += delta; activity.hidden = true;
      if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; renderMarkdown(content, md); scrollToEnd(); }); }
    },
    activity(label) { activity.textContent = label; activity.hidden = false; scrollToEnd(); },
    finish({ cost, sources, error }) {
      activity.remove(); renderMarkdown(content, md);
      if (error) { const e = document.createElement("div"); e.className = "error"; e.textContent = error; wrap.appendChild(e); }
      if (sources?.length) {
        const s = document.createElement("div"); s.className = "sources";
        s.append(Object.assign(document.createElement("span"), { textContent: "Fuentes:", className: "meta" }));
        for (const src of sources.slice(0, 6)) {
          const a = document.createElement("a"); a.href = src.url; a.textContent = src.title; a.target = "_blank"; a.rel = "noopener"; s.appendChild(a);
        }
        wrap.appendChild(s);
      }
      if (cost != null) {
        const m = document.createElement("div"); m.className = "meta";
        m.textContent = `Coste aproximado de esta respuesta: ${cost < 0.01 ? "< 0,01" : cost.toFixed(2).replace(".", ",")} $`;
        wrap.appendChild(m);
      }
      scrollToEnd();
    },
  };
}

function friendlyError(err) {
  if (err instanceof Anthropic.AuthenticationError) return "La clave de API no es válida. Revísala en Ajustes.";
  if (err instanceof Anthropic.PermissionDeniedError) return "Tu clave no tiene permiso para usar este modelo o función.";
  if (err instanceof Anthropic.RateLimitError) return "Demasiadas peticiones o sin saldo suficiente. Espera un momento o revisa tu saldo en console.anthropic.com.";
  if (err instanceof Anthropic.BadRequestError) return `La petición no es válida: ${err.message}`;
  if (err instanceof Anthropic.APIError) return `Error del servicio (${err.status ?? "sin conexión"}). Inténtalo de nuevo en unos segundos.`;
  return err?.message || "Algo falló. Inténtalo de nuevo.";
}

// ---------- envío ----------
function setBusy(on) {
  $("send").textContent = on ? "Parar" : "Enviar";
  $("send").classList.toggle("primary", !on);
  $("input").disabled = on;
}

async function send(text) {
  text = text.trim();
  if (!text) return;
  if (!apiKey) { $("settings").showModal(); return; }
  $("intro")?.remove();
  addUser(text);
  const ui = addBot();
  const now = new Date().toLocaleString("es-ES", { dateStyle: "full", timeStyle: "short" });
  const checkpoint = history.length;
  history.push({ role: "user", content: [{ type: "text", text: `[Fecha y hora del usuario: ${now}]\n\n${text}` }] });
  busy = new AbortController(); setBusy(true);
  try {
    const result = await runTurn({ apiKey, history, ui, signal: busy.signal });
    ui.finish(result);
  } catch (err) {
    // Deja el historial como estaba antes de esta pregunta para que la conversación siga siendo válida.
    history.length = checkpoint;
    ui.finish({ error: busy.signal.aborted ? "Detenido." : friendlyError(err) });
  } finally {
    busy = null; setBusy(false); $("input").focus();
  }
}

$("composer").addEventListener("submit", e => {
  e.preventDefault();
  if (busy) { busy.abort(); return; }
  const v = $("input").value; $("input").value = ""; autosize(); send(v);
});
$("input").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && matchMedia("(hover: hover)").matches) { e.preventDefault(); $("composer").requestSubmit(); }
});
function autosize() { const t = $("input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 160) + "px"; }
$("input").addEventListener("input", autosize);
$("chips").addEventListener("click", e => { if (e.target.tagName === "BUTTON") send(e.target.textContent); });
$("newChat").onclick = () => { if (busy) busy.abort(); history = []; location.reload(); };

// ---------- precios en la portada ----------
(async () => {
  const pairs = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
  try {
    const r = await fetch(`https://data-api.binance.vision/api/v3/ticker/24hr?symbols=${encodeURIComponent(JSON.stringify(pairs))}`);
    const rows = await r.json();
    for (const t of rows) {
      const ch = +t.priceChangePercent, s = document.createElement("span");
      s.innerHTML = `${t.symbol.replace("USDT", "")} ${(+t.lastPrice).toLocaleString("es-ES", { maximumFractionDigits: 2 })} <b class="${ch >= 0 ? "up" : "down"}">${ch >= 0 ? "+" : ""}${ch.toFixed(2)} %</b>`;
      $("ticker")?.appendChild(s);
    }
  } catch { $("ticker")?.remove(); }
})();
