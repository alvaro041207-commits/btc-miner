// Lógica del agente: herramientas de mercado y bucle de conversación con Claude.
import Anthropic from "./vendor/anthropic-sdk.mjs";

export const MODEL = "claude-opus-5-5";

export const SYSTEM_PROMPT = `Eres un asistente de análisis de mercados financieros para una persona que hace trading por su cuenta. Respondes siempre en español, de forma clara y directa, como lo haría un analista con experiencia hablando con alguien que está aprendiendo.

Qué haces:
- Analizas criptomonedas con datos en vivo usando tus herramientas: obtener_precio_cripto para el precio y la variación de 24 horas, y analisis_tecnico_cripto para indicadores (medias móviles, RSI, MACD, ATR, soportes y resistencias).
- Para acciones, índices, materias primas, noticias, resultados de empresas o datos macroeconómicos usas la búsqueda web. Indica siempre de dónde sale cada dato y de qué fecha es.
- Cuando te pidan una opinión sobre una operación, da un escenario alcista y uno bajista, los niveles que invalidarían cada uno, y un posible stop y objetivo con la relación riesgo/beneficio.

Reglas:
- Usa las herramientas antes de dar cifras. Nunca inventes precios ni indicadores; si un dato no está disponible, dilo.
- No prometas ganancias ni des certezas. Habla de probabilidades y riesgos.
- Recuerda, cuando sea relevante y sin repetirlo en cada mensaje, que esto es análisis informativo y no asesoramiento financiero, y que la persona decide y asume el riesgo.
- No puedes comprar ni vender nada: si te lo piden, explica que solo analizas y que la operación la hace la persona en su exchange o bróker.
- Formato: frases cortas, listas cuando ayuden, y las cifras clave en negrita. Usa tablas solo para comparar varios activos.`;

const BINANCE_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"];
const INTERVALS = ["15m", "1h", "4h", "1d", "1w"];
const SYMBOL_RE = /^[A-Z0-9]{5,20}$/;

async function binance(path) {
  let lastErr;
  for (const host of BINANCE_HOSTS) {
    try {
      const r = await fetch(host + path);
      const body = await r.json();
      if (!r.ok) throw new Error(body.msg || `Binance respondió ${r.status}`);
      return body;
    } catch (e) { lastErr = e; }
  }
  throw lastErr;
}

function normalizeSymbol(input) {
  if (typeof input !== "string") return null;
  const s = input.toUpperCase().replace(/[^A-Z0-9]/g, "");
  return SYMBOL_RE.test(s) ? s : null;
}

// ---------- indicadores ----------
const round = (v, d = 2) => (v == null || !isFinite(v) ? null : Number(v.toFixed(d)));
const sma = (a, n) => (a.length < n ? null : a.slice(-n).reduce((s, x) => s + x, 0) / n);
function emaSeries(a, n) {
  const k = 2 / (n + 1), out = [];
  let prev = a.slice(0, n).reduce((s, x) => s + x, 0) / n;
  for (let i = 0; i < a.length; i++) {
    if (i < n - 1) { out.push(null); continue; }
    if (i === n - 1) { out.push(prev); continue; }
    prev = a[i] * k + prev * (1 - k); out.push(prev);
  }
  return out;
}
function rsi(closes, n = 14) {
  if (closes.length <= n) return null;
  let gain = 0, loss = 0;
  for (let i = 1; i <= n; i++) { const d = closes[i] - closes[i - 1]; d > 0 ? (gain += d) : (loss -= d); }
  gain /= n; loss /= n;
  for (let i = n + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    gain = (gain * (n - 1) + Math.max(d, 0)) / n;
    loss = (loss * (n - 1) + Math.max(-d, 0)) / n;
  }
  return loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
}
function atr(high, low, close, n = 14) {
  const tr = high.map((h, i) => (i === 0 ? h - low[i] : Math.max(h - low[i], Math.abs(h - close[i - 1]), Math.abs(low[i] - close[i - 1]))));
  if (tr.length <= n) return null;
  let v = tr.slice(1, n + 1).reduce((s, x) => s + x, 0) / n;
  for (let i = n + 1; i < tr.length; i++) v = (v * (n - 1) + tr[i]) / n;
  return v;
}
export function technicals(klines) {
  const open = klines.map(k => +k[1]), high = klines.map(k => +k[2]), low = klines.map(k => +k[3]);
  const close = klines.map(k => +k[4]), vol = klines.map(k => +k[5]);
  const last = close.at(-1);
  const e12 = emaSeries(close, 12), e26 = emaSeries(close, 26);
  const macdLine = close.map((_, i) => (e12[i] != null && e26[i] != null ? e12[i] - e26[i] : null)).filter(v => v != null);
  const signal = emaSeries(macdLine, 9).at(-1);
  const macd = macdLine.at(-1);
  const look = 50;
  const atrV = atr(high, low, close);
  const dp = last >= 100 ? 2 : last >= 1 ? 4 : 8;
  return {
    precio_cierre_ultima_vela: round(last, dp),
    velas_analizadas: close.length,
    variacion_periodo_pct: round((last / open[0] - 1) * 100),
    sma20: round(sma(close, 20), dp), sma50: round(sma(close, 50), dp), sma200: round(sma(close, 200), dp),
    ema20: round(emaSeries(close, 20).at(-1), dp),
    rsi14: round(rsi(close), 1),
    macd: round(macd, dp), macd_senal: round(signal, dp), macd_histograma: round(macd - signal, dp),
    atr14: round(atrV, dp), atr14_pct: round((atrV / last) * 100),
    maximo_ultimas_50_velas: round(Math.max(...high.slice(-look)), dp),
    minimo_ultimas_50_velas: round(Math.min(...low.slice(-look)), dp),
    volumen_ultima_vela_vs_media20: round(vol.at(-1) / sma(vol, 20)),
    ultimas_5_velas: klines.slice(-5).map(k => ({ apertura_utc: new Date(k[0]).toISOString().slice(0, 16), o: +k[1], h: +k[2], l: +k[3], c: +k[4] })),
  };
}

// ---------- herramientas ----------
export const LOCAL_TOOLS = {
  obtener_precio_cripto: {
    definition: {
      name: "obtener_precio_cripto",
      description: "Precio en vivo de un par de criptomonedas en Binance y su resumen de las últimas 24 horas (variación, máximo, mínimo, volumen). Usa el par completo, por ejemplo BTCUSDT, ETHUSDT, SOLUSDT o ETHBTC. Si el usuario dice solo 'bitcoin' o 'BTC', usa BTCUSDT.",
      input_schema: {
        type: "object",
        properties: { simbolo: { type: "string", description: "Par de Binance en mayúsculas, p. ej. BTCUSDT" } },
        required: ["simbolo"], additionalProperties: false,
      },
      strict: true, eager_input_streaming: true,
    },
    label: i => `Consultando precio de ${i.simbolo}`,
    async run({ simbolo }) {
      const s = normalizeSymbol(simbolo);
      if (!s) throw new Error(`Símbolo no válido: ${simbolo}. Usa un par como BTCUSDT.`);
      const t = await binance(`/api/v3/ticker/24hr?symbol=${s}`);
      return {
        simbolo: t.symbol, precio: +t.lastPrice, variacion_24h_pct: +t.priceChangePercent,
        maximo_24h: +t.highPrice, minimo_24h: +t.lowPrice, precio_medio_24h: +t.weightedAvgPrice,
        volumen_24h_moneda_base: +t.volume, volumen_24h_moneda_cotizada: +t.quoteVolume,
        fuente: "Binance", consultado: new Date().toISOString(),
      };
    },
  },
  analisis_tecnico_cripto: {
    definition: {
      name: "analisis_tecnico_cripto",
      description: "Calcula indicadores técnicos de un par de Binance a partir de sus últimas 250 velas: medias móviles (SMA 20/50/200, EMA 20), RSI 14, MACD (12,26,9), ATR 14, máximo y mínimo de las últimas 50 velas como soporte/resistencia de referencia, volumen relativo y las 5 últimas velas. Elige el intervalo según el horizonte: 15m o 1h para intradía, 4h para swing de días, 1d o 1w para tendencia de fondo.",
      input_schema: {
        type: "object",
        properties: {
          simbolo: { type: "string", description: "Par de Binance en mayúsculas, p. ej. BTCUSDT" },
          intervalo: { type: "string", enum: INTERVALS, description: "Duración de cada vela" },
        },
        required: ["simbolo", "intervalo"], additionalProperties: false,
      },
      strict: true, eager_input_streaming: true,
    },
    label: i => `Calculando indicadores de ${i.simbolo} (${i.intervalo})`,
    async run({ simbolo, intervalo }) {
      const s = normalizeSymbol(simbolo);
      if (!s) throw new Error(`Símbolo no válido: ${simbolo}. Usa un par como BTCUSDT.`);
      if (!INTERVALS.includes(intervalo)) throw new Error(`Intervalo no válido: ${intervalo}. Usa uno de: ${INTERVALS.join(", ")}.`);
      const k = await binance(`/api/v3/klines?symbol=${s}&interval=${intervalo}&limit=250`);
      if (!Array.isArray(k) || k.length < 30) throw new Error("Binance devolvió muy pocas velas para calcular indicadores.");
      return { simbolo: s, intervalo, fuente: "Binance", consultado: new Date().toISOString(), ...technicals(k) };
    },
  },
};

const TOOLS = [
  ...Object.values(LOCAL_TOOLS).map(t => t.definition),
  { type: "web_search_20260209", name: "web_search", max_uses: 5 },
  { type: "web_fetch_20260209", name: "web_fetch", max_uses: 3 },
];

function validInput(def, input) {
  if (!input || typeof input !== "object") return false;
  const { properties, required } = def.input_schema;
  if (!required.every(k => typeof input[k] === "string" && input[k].length)) return false;
  return Object.keys(input).every(k => k in properties);
}

async function runLocalTool(block) {
  const tool = LOCAL_TOOLS[block.name];
  if (!tool) return { type: "tool_result", tool_use_id: block.id, is_error: true, content: `Herramienta desconocida: ${block.name}` };
  if (!validInput(tool.definition, block.input)) {
    return { type: "tool_result", tool_use_id: block.id, is_error: true, content: JSON.stringify({ INVALID_JSON: JSON.stringify(block.input) }) };
  }
  try {
    return { type: "tool_result", tool_use_id: block.id, content: JSON.stringify(await tool.run(block.input)) };
  } catch (e) {
    return { type: "tool_result", tool_use_id: block.id, is_error: true, content: `Error al consultar los datos: ${e.message}` };
  }
}

// Precios por millón de tokens de Claude Opus 5.5 y por búsqueda web, para estimar el coste.
const PRICE = { input: 4, output: 20, cacheWrite: 5, cacheRead: 0.2, search: 0.01 };
export function estimateCost(u) {
  return (u.input * PRICE.input + u.output * PRICE.output + u.cacheWrite * PRICE.cacheWrite + u.cacheRead * PRICE.cacheRead) / 1e6
    + u.searches * PRICE.search;
}

/**
 * Ejecuta un turno del agente. `history` (Anthropic.Beta.BetaMessageParam[]) ya termina con el mensaje del usuario
 * y se amplía con las respuestas y los resultados de herramientas.
 * `ui` recibe: text(delta), activity(label), done(), y devuelve fuentes y uso.
 */
export async function runTurn({ apiKey, history, ui, signal }) {
  const client = new Anthropic({ apiKey, dangerouslyAllowBrowser: true });
  const usage = { input: 0, output: 0, cacheWrite: 0, cacheRead: 0, searches: 0 };
  const sources = new Map();
  let continuations = 0, retries = 0;

  while (true) {
    const stream = client.beta.messages.stream({
      model: MODEL,
      max_tokens: 64000,
      betas: ["server-side-fallback-2026-07-01"],
      fallbacks: "default",
      thinking: { type: "adaptive" },
      output_config: { effort: "medium" },
      cache_control: { type: "ephemeral" },
      system: SYSTEM_PROMPT,
      tools: TOOLS,
      messages: history,
    }, { signal });

    stream.on("text", d => ui.text(d));
    stream.on("streamEvent", ev => {
      if (ev.type !== "content_block_start") return;
      const b = ev.content_block;
      if (b.type === "server_tool_use") ui.activity(b.name === "web_search" ? "Buscando en internet" : "Leyendo una página web");
      else if (b.type === "thinking") ui.activity("Pensando");
    });

    let msg;
    try {
      msg = await stream.finalMessage();
      retries = 0;
    } catch (err) {
      if (signal?.aborted || err instanceof Anthropic.APIError || retries++ >= 2) throw err;
      continue; // conexión cortada o entrada de herramienta ilegible: repetir el turno
    }

    const u = msg.usage;
    usage.input += u.input_tokens || 0; usage.output += u.output_tokens || 0;
    usage.cacheWrite += u.cache_creation_input_tokens || 0; usage.cacheRead += u.cache_read_input_tokens || 0;
    usage.searches += u.server_tool_use?.web_search_requests || 0;
    for (const b of msg.content) {
      if (b.type === "text") for (const c of b.citations || []) if (c.url) sources.set(c.url, c.title || c.url);
    }

    if (msg.stop_reason === "refusal") {
      ui.text("\n\n_No puedo ayudar con esta petición tal como está planteada. Prueba a reformularla._");
      break;
    }
    if (msg.stop_reason === "pause_turn") {
      history.push({ role: "assistant", content: msg.content });
      if (++continuations > 5) break;
      continue;
    }

    const toolUses = msg.content.filter(b => b.type === "tool_use");
    if (toolUses.length && msg.stop_reason === "max_tokens") throw new Error("La respuesta se cortó antes de terminar. Inténtalo de nuevo.");
    history.push({ role: "assistant", content: msg.content });
    if (!toolUses.length) break;

    for (const t of toolUses) ui.activity(LOCAL_TOOLS[t.name]?.label(t.input || {}) || t.name);
    const results = await Promise.all(toolUses.map(runLocalTool));
    history.push({ role: "user", content: results });
    ui.text("\n\n");
  }
  return { usage, cost: estimateCost(usage), sources: [...sources].map(([url, title]) => ({ url, title })) };
}

export { Anthropic };
