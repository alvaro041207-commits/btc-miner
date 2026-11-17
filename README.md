# Agente de Trading

Asistente de análisis de mercados con IA (Claude), en una página web que funciona desde el móvil.

**Abrir:** https://alvaro041207-commits.github.io/btc-miner/

## Qué hace

- **Criptomonedas en vivo** (datos de Binance): precio, variación de 24 h e indicadores técnicos
  (SMA 20/50/200, EMA 20, RSI 14, MACD, ATR, soportes y resistencias, volumen relativo).
- **Acciones, noticias y macro**: busca y lee páginas web y cita las fuentes.
- **Planes de operación**: escenarios alcista y bajista, stop, objetivo y relación riesgo/beneficio.
- **No opera**: solo analiza. No compra ni vende nada.

## Cómo usarlo

1. Crea una clave de API en https://console.anthropic.com/settings/keys y añade saldo (pago por uso).
2. Abre la página, pulsa **Ajustes** y pega la clave.
3. Pregunta, por ejemplo: "Analiza Bitcoin en gráfico de 4 horas".

La clave se guarda solo en tu navegador y solo se envía a `api.anthropic.com`. Cada respuesta muestra
su coste aproximado (normalmente entre 0,02 $ y 0,15 $).

## Archivos

| Archivo | Para qué |
|---|---|
| `index.html` | La página y su diseño |
| `app.js` | El chat: mensajes, clave y pintado de respuestas |
| `agent.js` | El agente: instrucciones, herramientas de mercado y bucle con la API de Claude |
| `vendor/` | Librerías incluidas: SDK oficial de Anthropic 1.4.0, marked y DOMPurify |

Es información orientativa, no asesoramiento financiero.
