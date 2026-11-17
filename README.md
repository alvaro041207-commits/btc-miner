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

---

# Bot autónomo (carpeta `bot/`)

Opera solo en Binance spot, en un servidor encendido 24 horas. Cada hora:

1. Revisa sus posiciones y apunta las que se cerraron por stop u objetivo.
2. Si la pérdida del día llega al límite, vende todo y no opera hasta el día siguiente (UTC).
3. Envía a Claude tus reglas (`bot/reglas.md`), los límites, sus posiciones e indicadores de 1 h, 4 h y 1 día.
4. Comprueba cada operación propuesta contra `bot/ajustes.json` y ejecuta solo las que cumplen.
   Cada compra sale con stop-loss y objetivo puestos en Binance (orden OCO).

## Límites (los aplica el código, la IA no puede saltárselos)

| Ajuste | Valor |
|---|---|
| Modo | `pruebas` (testnet de Binance, dinero ficticio) |
| Monedas permitidas | BTCUSDT, ETHUSDT, SOLUSDT |
| Capital del bot | 100 USDT |
| Máximo por operación | 25 USDT |
| Pérdida máxima por día | 10 USDT (si se llega, cierra todo y para) |
| Operaciones por día | 6 |
| Stop-loss | entre 0,8 % y 6 % |

Una caída muy brusca puede hacer que el stop se ejecute peor de lo previsto, así que la pérdida real
de un día puede pasar algo del límite.

## Instalación en el servidor

```bash
git clone https://github.com/alvaro041207-commits/btc-miner.git
cd btc-miner/bot && bash instalar.sh
nano .env                                    # claves de Claude y de Binance
.venv/bin/python bot.py --simular            # decide sin enviar órdenes
systemctl enable --now bot-trading.timer     # cada hora
journalctl -u bot-trading -f                 # ver lo que hace
```

Para pasar a dinero real: crea claves de Binance con permiso **solo de Spot trading** (nunca retiradas),
restringidas a la IP del servidor, ponlas en `.env` y cambia `"modo"` a `"real"` en `ajustes.json`.
