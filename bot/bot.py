#!/usr/bin/env python3
"""
Bot de trading autónomo para Binance (spot).

Cada vez que se ejecuta (el servidor lo lanza cada hora):
  1. Revisa las posiciones abiertas por el bot y si algún stop u objetivo se ha ejecutado.
  2. Calcula el resultado del día. Si la pérdida llega al límite, cierra todo y no opera hasta mañana.
  3. Envía a Claude tus reglas, los datos del mercado y las posiciones, y recibe su decisión.
  4. Comprueba la decisión contra los límites de ajustes.json y ejecuta solo lo que los cumple.
     Toda compra sale con su stop-loss y su objetivo (orden OCO) puestos en Binance.

Uso:  python3 bot.py             una ejecución
      python3 bot.py --simular   decide pero no envía órdenes
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import anthropic
from binance.error import ClientError
from binance.spot import Spot
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
MODEL = "claude-opus-5-5"
TESTNET_URL = "https://testnet.binance.vision"
LIVE_URL = "https://api.binance.com"


# ----------------------------------------------------------------- utilidades
def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC  {msg}", flush=True)


def notify(msg: str) -> None:
    """Envía un aviso por Telegram si está configurado; si no, solo lo registra."""
    log(msg)
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    try:
        import urllib.parse
        import urllib.request
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg[:4000]}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=15)
    except Exception as e:  # un fallo de aviso nunca debe parar el bot
        log(f"No se pudo avisar por Telegram: {e}")


def floor_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    decimals = max(0, -int(math.floor(math.log10(step))))
    return round(math.floor(value / step + 1e-9) * step, decimals)


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ----------------------------------------------------------------- estado
class State:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {}
        self.data.setdefault("posiciones", {})   # simbolo -> {cantidad, coste_usdt, precio_entrada, oco_id, abierta}
        self.data.setdefault("dias", {})         # fecha -> {realizado_usdt, operaciones, detenido}
        self.data.setdefault("historial", [])

    @property
    def positions(self) -> dict:
        return self.data["posiciones"]

    @property
    def day(self) -> dict:
        return self.data["dias"].setdefault(today(), {"realizado_usdt": 0.0, "operaciones": 0, "detenido": False})

    def record(self, entry: dict) -> None:
        entry["fecha"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.data["historial"] = (self.data["historial"] + [entry])[-500:]

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)


# ----------------------------------------------------------------- mercado
def technicals(klines: list) -> dict:
    close = [float(k[4]) for k in klines]
    high = [float(k[2]) for k in klines]
    low = [float(k[3]) for k in klines]
    vol = [float(k[5]) for k in klines]

    def sma(a, n):
        return sum(a[-n:]) / n if len(a) >= n else None

    def ema(a, n):
        k, e = 2 / (n + 1), sum(a[:n]) / n
        out = [e]
        for x in a[n:]:
            e = x * k + e * (1 - k)
            out.append(e)
        return out

    gains = losses = 0.0
    n = 14
    for i in range(1, n + 1):
        d = close[i] - close[i - 1]
        gains += max(d, 0); losses += max(-d, 0)
    gains, losses = gains / n, losses / n
    for i in range(n + 1, len(close)):
        d = close[i] - close[i - 1]
        gains = (gains * (n - 1) + max(d, 0)) / n
        losses = (losses * (n - 1) + max(-d, 0)) / n
    rsi = 100 if losses == 0 else 100 - 100 / (1 + gains / losses)

    e12, e26 = ema(close, 12), ema(close, 26)
    macd_line = [a - b for a, b in zip(e12[14:], e26)]
    signal = ema(macd_line, 9)[-1]
    tr = [high[0] - low[0]] + [max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])) for i in range(1, len(close))]
    atr = sum(tr[1:15]) / 14
    for x in tr[15:]:
        atr = (atr * 13 + x) / 14
    last = close[-1]
    r = lambda v: None if v is None else round(v, 6 if last < 1 else 2)
    return {
        "cierre": r(last), "sma20": r(sma(close, 20)), "sma50": r(sma(close, 50)), "sma200": r(sma(close, 200)),
        "rsi14": round(rsi, 1), "macd": r(macd_line[-1]), "macd_senal": r(signal), "atr14_pct": round(atr / last * 100, 2),
        "max_50": r(max(high[-50:])), "min_50": r(min(low[-50:])),
        "volumen_vs_media20": round(vol[-1] / (sma(vol, 20) or 1), 2),
        "variacion_periodo_pct": round((last / close[0] - 1) * 100, 2),
    }


def market_snapshot(client: Spot, symbols: list[str]) -> dict:
    out = {}
    for s in symbols:
        t = client.ticker_24hr(symbol=s)
        out[s] = {
            "precio": float(t["lastPrice"]), "variacion_24h_pct": float(t["priceChangePercent"]),
            "maximo_24h": float(t["highPrice"]), "minimo_24h": float(t["lowPrice"]),
            "velas_1h": technicals(client.klines(s, "1h", limit=250)),
            "velas_4h": technicals(client.klines(s, "4h", limit=250)),
            "velas_1d": technicals(client.klines(s, "1d", limit=250)),
        }
    return out


def symbol_filters(client: Spot, symbols: list[str]) -> dict:
    info = client.exchange_info(symbols=symbols)
    out = {}
    for s in info["symbols"]:
        f = {x["filterType"]: x for x in s["filters"]}
        out[s["symbol"]] = {
            "base": s["baseAsset"],
            "step": float(f["LOT_SIZE"]["stepSize"]),
            "tick": float(f["PRICE_FILTER"]["tickSize"]),
            "min_notional": float((f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}).get("minNotional", 5)),
        }
    return out


# ----------------------------------------------------------------- decisión de Claude
class Accion(BaseModel):
    tipo: Literal["comprar", "vender"]
    simbolo: str
    monto_usdt: float = Field(description="Solo para comprar: cuántos USDT gastar. Para vender, 0.")
    stop_loss_pct: float = Field(description="Solo para comprar: distancia del stop-loss bajo el precio de entrada, en %. Para vender, 0.")
    take_profit_pct: float = Field(description="Solo para comprar: distancia del objetivo sobre el precio de entrada, en %. Para vender, 0.")
    motivo: str


class Decision(BaseModel):
    analisis: str = Field(description="Resumen breve de la lectura del mercado, en español")
    acciones: list[Accion] = Field(description="Operaciones a ejecutar ahora. Lista vacía si lo mejor es no hacer nada.")


SYSTEM_PROMPT = """Eres el gestor de un bot de trading autónomo en Binance spot. Cada hora recibes el estado de la cuenta del bot, los datos del mercado y las reglas de tu dueño, y decides qué operaciones hacer ahora mismo.

Cómo decides:
- Sigue las reglas de tu dueño al pie de la letra. Son su estrategia y su decisión; aplícalas sin cuestionarlas.
- Solo puedes comprar o vender los símbolos permitidos y dentro de los límites indicados. El sistema rechazará cualquier operación fuera de ellos, así que no la propongas.
- Cada compra lleva un stop-loss y un objetivo en porcentaje. Basa sus distancias en la volatilidad (ATR) y en los niveles de soporte y resistencia.
- Vender cierra una posición abierta del bot entera, al precio de mercado.
- No operar es una decisión válida. No hagas operaciones por hacer.
- Responde solo con la decisión en el formato pedido."""


def ask_claude(rules: str, limits: dict, account: dict, market: dict) -> Decision:
    client = anthropic.Anthropic(max_retries=3)
    user = (
        f"Fecha y hora UTC: {datetime.now(timezone.utc):%Y-%m-%d %H:%M}\n\n"
        f"## Reglas del dueño\n{rules.strip()}\n\n"
        f"## Límites (los impone el sistema)\n{json.dumps(limits, ensure_ascii=False, indent=2)}\n\n"
        f"## Estado del bot\n{json.dumps(account, ensure_ascii=False, indent=2)}\n\n"
        f"## Mercado (Binance; indicadores por temporalidad)\n{json.dumps(market, ensure_ascii=False)}"
    )
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
        output_format=Decision,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude no respondió a esta petición (refusal). No se opera en esta ronda.")
    if response.parsed_output is None:
        raise RuntimeError(f"Respuesta sin decisión válida (stop_reason={response.stop_reason}).")
    return response.parsed_output


# ----------------------------------------------------------------- el bot
class Bot:
    def __init__(self, settings: dict, state: State, client: Spot, simulate: bool = False):
        self.s, self.state, self.client, self.simulate = settings, state, client, simulate
        self.symbols = settings["simbolos_permitidos"]
        self.filters = symbol_filters(client, self.symbols)

    # ---- posiciones
    def balance(self, asset: str) -> tuple[float, float]:
        for b in self.client.account(omitZeroBalances="true")["balances"]:
            if b["asset"] == asset:
                return float(b["free"]), float(b["locked"])
        return 0.0, 0.0

    def order_proceeds(self, symbol: str, order_id: int) -> tuple[float, float]:
        o = self.client.get_order(symbol, orderId=order_id)
        return float(o["executedQty"]), float(o["cummulativeQuoteQty"])

    def reconcile(self) -> None:
        """Detecta posiciones cerradas por su stop o su objetivo y apunta el resultado."""
        for sym, pos in list(self.state.positions.items()):
            if not pos.get("oco_id"):
                continue
            oco = self.client.get_oco_order(orderListId=pos["oco_id"])
            if oco["listOrderStatus"] != "ALL_DONE":
                continue
            qty_sold = proceeds = 0.0
            for o in oco["orders"]:
                q, p = self.order_proceeds(sym, o["orderId"])
                qty_sold += q; proceeds += p
            if qty_sold <= 0:  # la OCO caducó o se canceló fuera del bot: se vende a mercado
                self.close(sym, "La orden de protección ya no está activa")
                continue
            pnl = proceeds - pos["coste_usdt"] * min(1.0, qty_sold / pos["cantidad"])
            self.state.day["realizado_usdt"] += pnl
            self.state.record({"tipo": "cierre_automatico", "simbolo": sym, "ingreso_usdt": round(proceeds, 4), "resultado_usdt": round(pnl, 4)})
            del self.state.positions[sym]
            notify(f"{'🟢' if pnl >= 0 else '🔴'} {sym} cerrada por {'objetivo' if pnl >= 0 else 'stop-loss'}: {pnl:+.2f} USDT")

    def prices(self) -> dict:
        return {s: float(self.client.ticker_price(symbol=s)["price"]) for s in self.symbols}

    def unrealized(self, prices: dict) -> float:
        return sum(p["cantidad"] * prices[s] - p["coste_usdt"] for s, p in self.state.positions.items())

    def exposure(self) -> float:
        return sum(p["coste_usdt"] for p in self.state.positions.values())

    # ---- órdenes
    def buy(self, a: Accion) -> None:
        f = self.filters[a.simbolo]
        if self.simulate:
            log(f"[simulación] Compraría {a.monto_usdt:.2f} USDT de {a.simbolo}, stop -{a.stop_loss_pct}% objetivo +{a.take_profit_pct}%")
            return
        free_before, _ = self.balance(f["base"])
        r = self.client.new_order(a.simbolo, "BUY", "MARKET", quoteOrderQty=round(a.monto_usdt, 2), newOrderRespType="FULL")
        cost = float(r["cummulativeQuoteQty"])
        avg = cost / float(r["executedQty"])
        free_after, _ = self.balance(f["base"])
        qty = floor_step(min(float(r["executedQty"]), free_after - free_before), f["step"])
        tp = floor_step(avg * (1 + a.take_profit_pct / 100), f["tick"])
        sl = floor_step(avg * (1 - a.stop_loss_pct / 100), f["tick"])
        sl_limit = floor_step(sl * 0.995, f["tick"])
        self.state.positions[a.simbolo] = {"cantidad": qty, "coste_usdt": cost, "precio_entrada": avg, "oco_id": None,
                                          "abierta": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.state.day["operaciones"] += 1
        self.state.save()
        try:
            oco = self.client.new_oco_order(
                a.simbolo, "SELL", qty, aboveType="LIMIT_MAKER", belowType="STOP_LOSS_LIMIT",
                abovePrice=tp, belowStopPrice=sl, belowPrice=sl_limit, belowTimeInForce="GTC",
            )
            self.state.positions[a.simbolo]["oco_id"] = oco["orderListId"]
        except ClientError as e:  # nunca dejar una posición sin protección
            notify(f"⚠️ No se pudo poner el stop de {a.simbolo} ({e.error_message}). Se vende de inmediato.")
            self.close(a.simbolo, "Sin protección")
            return
        self.state.record({"tipo": "compra", "simbolo": a.simbolo, "coste_usdt": round(cost, 4), "precio": avg, "stop": sl, "objetivo": tp, "motivo": a.motivo})
        notify(f"🛒 Compra {a.simbolo}: {cost:.2f} USDT a {avg:g}\nStop {sl:g} (-{a.stop_loss_pct}%) · Objetivo {tp:g} (+{a.take_profit_pct}%)\n{a.motivo}")

    def close(self, symbol: str, reason: str) -> None:
        pos = self.state.positions.get(symbol)
        if not pos:
            return
        if self.simulate:
            log(f"[simulación] Vendería toda la posición de {symbol}: {reason}")
            return
        if pos.get("oco_id"):
            try:
                self.client.cancel_oco_order(symbol, orderListId=pos["oco_id"])
            except ClientError as e:
                log(f"Aviso al cancelar la OCO de {symbol}: {e.error_message}")
        f = self.filters[symbol]
        free, _ = self.balance(f["base"])
        qty = floor_step(min(pos["cantidad"], free), f["step"])
        proceeds = 0.0
        if qty > 0 and qty * self.prices()[symbol] >= f["min_notional"]:
            r = self.client.new_order(symbol, "SELL", "MARKET", quantity=qty, newOrderRespType="FULL")
            proceeds = float(r["cummulativeQuoteQty"])
        pnl = proceeds - pos["coste_usdt"]
        self.state.day["realizado_usdt"] += pnl
        del self.state.positions[symbol]
        self.state.record({"tipo": "venta", "simbolo": symbol, "ingreso_usdt": round(proceeds, 4), "resultado_usdt": round(pnl, 4), "motivo": reason})
        self.state.save()
        notify(f"💰 Venta {symbol}: {pnl:+.2f} USDT\n{reason}")

    # ---- validación de la decisión (límites duros)
    def validate(self, a: Accion, budget_left: float, prices: dict) -> str | None:
        s = self.s
        if a.simbolo not in self.symbols:
            return f"{a.simbolo} no está en los símbolos permitidos"
        if a.tipo == "vender":
            return None if a.simbolo in self.state.positions else f"no hay posición abierta en {a.simbolo}"
        if self.state.day["detenido"]:
            return "el bot está detenido por pérdida diaria"
        if a.simbolo in self.state.positions:
            return f"ya hay una posición abierta en {a.simbolo}"
        if self.state.day["operaciones"] >= s["max_operaciones_dia"]:
            return "se alcanzó el máximo de operaciones del día"
        if a.monto_usdt > s["max_por_operacion_usdt"] + 1e-9:
            return f"{a.monto_usdt} USDT supera el máximo por operación ({s['max_por_operacion_usdt']})"
        if a.monto_usdt > budget_left + 1e-9:
            return f"{a.monto_usdt} USDT supera el capital libre del bot ({budget_left:.2f})"
        if a.monto_usdt < max(self.filters[a.simbolo]["min_notional"], 5):
            return "monto por debajo del mínimo de Binance"
        if not (s["stop_min_pct"] <= a.stop_loss_pct <= s["stop_max_pct"]):
            return f"stop-loss {a.stop_loss_pct}% fuera del rango permitido"
        if not (0.5 <= a.take_profit_pct <= 50):
            return f"objetivo {a.take_profit_pct}% fuera del rango permitido"
        if a.monto_usdt * a.stop_loss_pct / 100 > s["perdida_max_dia_usdt"] - self.loss_today(prices) + 1e-9:
            return "si salta el stop, superaría la pérdida máxima del día"
        free_usdt, _ = self.balance("USDT")
        if a.monto_usdt > free_usdt:
            return f"no hay suficientes USDT libres en la cuenta ({free_usdt:.2f})"
        return None

    def loss_today(self, prices: dict) -> float:
        return max(0.0, -(self.state.day["realizado_usdt"] + self.unrealized(prices)))

    # ---- una ronda
    def run(self) -> None:
        s = self.s
        self.reconcile()
        prices = self.prices()
        loss = self.loss_today(prices)
        if not self.state.day["detenido"] and loss >= s["perdida_max_dia_usdt"]:
            self.state.day["detenido"] = True
            notify(f"🛑 Pérdida del día {loss:.2f} USDT ≥ límite {s['perdida_max_dia_usdt']}. Cierro todo y paro hasta mañana (UTC).")
            for sym in list(self.state.positions):
                self.close(sym, "Límite de pérdida diaria alcanzado")
        if self.state.day["detenido"]:
            log("Detenido por pérdida diaria: no se consulta a Claude hoy.")
            return

        budget_left = max(0.0, s["capital_usdt"] - self.exposure())
        account = {
            "modo": "PRUEBAS (testnet)" if s["modo"] == "pruebas" else "REAL",
            "capital_libre_usdt": round(budget_left, 2),
            "resultado_hoy_usdt": round(self.state.day["realizado_usdt"] + self.unrealized(prices), 2),
            "operaciones_hoy": self.state.day["operaciones"],
            "posiciones_abiertas": {
                k: {**v, "precio_actual": prices[k], "resultado_usdt": round(v["cantidad"] * prices[k] - v["coste_usdt"], 2)}
                for k, v in self.state.positions.items()
            },
            "ultimas_operaciones": self.state.data["historial"][-8:],
        }
        limits = {k: s[k] for k in ("simbolos_permitidos", "capital_usdt", "max_por_operacion_usdt", "perdida_max_dia_usdt",
                                    "max_operaciones_dia", "stop_min_pct", "stop_max_pct")}
        rules = (HERE / "reglas.md").read_text()
        decision = ask_claude(rules, limits, account, market_snapshot(self.client, self.symbols))
        log(f"Análisis: {decision.analisis}")
        if not decision.acciones:
            log("Decisión: no operar.")
        for a in decision.acciones:
            a.simbolo = a.simbolo.upper().strip()
            problem = self.validate(a, budget_left, prices)
            if problem:
                notify(f"⛔ Operación rechazada por los límites ({a.tipo} {a.simbolo}): {problem}")
                continue
            if a.tipo == "comprar":
                self.buy(a)
                budget_left -= a.monto_usdt
            else:
                self.close(a.simbolo, a.motivo)


def main() -> int:
    load_env(HERE / ".env")
    settings = json.loads((HERE / "ajustes.json").read_text())
    simulate = "--simular" in sys.argv
    live = settings["modo"] == "real"
    key = os.environ.get("BINANCE_API_KEY_REAL" if live else "BINANCE_API_KEY_PRUEBAS")
    secret = os.environ.get("BINANCE_API_SECRET_REAL" if live else "BINANCE_API_SECRET_PRUEBAS")
    if not (key and secret):
        log("Faltan las claves de Binance en .env para el modo " + settings["modo"])
        return 1
    client = Spot(api_key=key, api_secret=secret, base_url=LIVE_URL if live else TESTNET_URL, timeout=20)
    state = State(HERE / ("estado_real.json" if live else "estado_pruebas.json"))
    try:
        Bot(settings, state, client, simulate).run()
    except ClientError as e:
        notify(f"⚠️ Error de Binance: {e.error_message}")
        return 1
    except anthropic.APIError as e:
        notify(f"⚠️ Error de la API de Claude: {e}. Se reintenta en la próxima ronda.")
        return 1
    except Exception as e:
        notify(f"⚠️ Error inesperado: {e!r}")
        raise
    finally:
        state.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
