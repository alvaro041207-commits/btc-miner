# btc-miner

Panel de control y minero de Bitcoin en minería solo.

## Panel (web)

`index.html` es el panel. Está publicado con GitHub Pages:
**https://alvaro041207-commits.github.io/btc-miner/**

Escribe tu dirección de Bitcoin y verás tus mineros, tu mejor hash, la probabilidad
de encontrar un bloque y los datos de la red. Se actualiza cada minuto.

Lee los datos de [public-pool.io](https://web.public-pool.io) (pool de minería solo) y
[mempool.space](https://mempool.space).

## Conectar un minero

Cualquier minero (Bitaxe, Antminer, hashrate alquilado en NiceHash o MiningRigRentals) se configura así:

| Campo | Valor |
|---|---|
| Pool / URL | `stratum+tcp://public-pool.io:21496` |
| Usuario | `TU_DIRECCION_BTC.nombre` (por ejemplo `bc1q....bitaxe1`) |
| Contraseña | `x` |

Si encuentra un bloque, la recompensa va directamente a tu dirección.

## Minero en Python (`minero/`)

Minero Stratum educativo que usa la CPU. Sirve para aprender; con CPU las probabilidades
de encontrar un bloque son prácticamente nulas.

```bash
python3 minero/miner.py --verify                  # comprueba el hash del bloque génesis
python3 minero/miner.py --demo                    # minado local de prueba
python3 minero/miner.py --pool public-pool.io:21496 --address TU_DIRECCION_BTC
```

Para dejarlo como servicio en un Linux: `sudo bash minero/instalar.sh TU_DIRECCION_BTC`.
