# Mis reglas de trading

Escribe aquí tu estrategia con tus palabras. El bot la sigue al pie de la letra.
Los límites de dinero están en ajustes.json y el código los aplica siempre.

- Opero a favor de la tendencia: solo compro si el precio está por encima de la media de 50 en el gráfico de 4 horas.
- Busco entradas en retrocesos: RSI de 1 hora entre 35 y 55, o rebote cerca de un soporte.
- No compro si el RSI de 4 horas está por encima de 70.
- Stop-loss según la volatilidad: entre 1 y 1,5 veces el ATR de 4 horas.
- El objetivo debe ser al menos el doble de la distancia del stop.
- Vendo antes del objetivo si la tendencia de 4 horas se rompe (cierre por debajo de la media de 50).
- Si hay dudas, no opero.
