#!/usr/bin/env python3
"""
Minero de Bitcoin (Stratum v1) en Python puro - educativo.

Se conecta a un pool (o a un pool "solo" como solo.ckpool.org), recibe trabajos,
busca nonces cuyo hash doble SHA-256 quede por debajo del objetivo y envía shares.
Si un hash además está por debajo del objetivo de la red, ¡es un bloque!

Uso:
  python3 miner.py --address TU_DIRECCION_BTC                 # public-pool.io
  python3 miner.py --pool solo.ckpool.org:3333 --address TU_DIRECCION_BTC
  python3 miner.py --demo                                     # minado local de prueba
  python3 miner.py --verify                                   # comprueba con el bloque génesis
"""
import argparse, hashlib, json, multiprocessing as mp, os, socket, struct, threading, time

DIFF1_TARGET = 0x00000000FFFF0000000000000000000000000000000000000000000000000000

def dsha256(b): return hashlib.sha256(hashlib.sha256(b).digest()).digest()

def bits_to_target(nbits_hex):
    n = int(nbits_hex, 16)
    exp, mant = n >> 24, n & 0xFFFFFF
    return mant << (8 * (exp - 3))

def swap_words(b):  # prevhash de Stratum: cada palabra de 4 bytes viene invertida
    return b"".join(b[i:i + 4][::-1] for i in range(0, len(b), 4))

def build_header_prefix(job, extranonce1, extranonce2):
    coinbase = bytes.fromhex(job["coinb1"] + extranonce1 + extranonce2 + job["coinb2"])
    root = dsha256(coinbase)
    for branch in job["merkle_branch"]:
        root = dsha256(root + bytes.fromhex(branch))
    return (bytes.fromhex(job["version"])[::-1] + swap_words(bytes.fromhex(job["prevhash"]))
            + root + bytes.fromhex(job["ntime"])[::-1] + bytes.fromhex(job["nbits"])[::-1])

# ---------------------------------------------------------------- trabajador
def worker(wid, nworkers, job_q, result_q, gen, hash_counter):
    job = None
    while True:
        job = job_q.get()                           # (gen, job, en1, en2_size, share_target)
        while not job_q.empty():                    # quedarse con el trabajo más reciente
            job = job_q.get()
        g, j, en1, en2_size, share_target = job
        en2_counter = wid
        while gen.value == g:
            en2 = format(en2_counter, f"0{en2_size * 2}x")[-en2_size * 2:]
            prefix = build_header_prefix(j, en1, en2)       # 76 bytes
            mid = hashlib.sha256(prefix[:64])               # "midstate"
            tail = prefix[64:]
            block_target = bits_to_target(j["nbits"])
            nonce, done = 0, 0
            while nonce < 0x100000000:
                h = mid.copy(); h.update(tail + struct.pack("<I", nonce))
                hv = int.from_bytes(hashlib.sha256(h.digest()).digest(), "little")
                if hv <= share_target:
                    result_q.put((g, j["job_id"], en2, j["ntime"], format(nonce, "08x"),
                                  hv, hv <= block_target))
                nonce += 1; done += 1
                if done == 20000:
                    with hash_counter.get_lock(): hash_counter.value += done
                    done = 0
                    if gen.value != g: break
            en2_counter += nworkers

# ---------------------------------------------------------------- Stratum
class Stratum:
    def __init__(self, host, port, user, password):
        self.sock = socket.create_connection((host, port), timeout=60)
        self.f = self.sock.makefile("r")
        self.user, self.password, self.msg_id = user, password, 0
        self.lock = threading.Lock()

    def send(self, method, params):
        with self.lock:
            self.msg_id += 1
            self.sock.sendall((json.dumps({"id": self.msg_id, "method": method, "params": params}) + "\n").encode())
            return self.msg_id

    def recv(self):
        line = self.f.readline()
        if not line: raise ConnectionError("el pool cerró la conexión")
        return json.loads(line)

def mine_pool(args):
    host, port = args.pool.rsplit(":", 1)
    user = f"{args.address}.{args.worker}"
    s = Stratum(host, int(port), user, args.password)
    print(f"[+] Conectado a {args.pool} como {user}")
    s.send("mining.subscribe", ["pyminer/1.0"])
    sub = s.recv()
    while "result" not in sub or sub.get("id") != 1: sub = s.recv()
    _, en1, en2_size = sub["result"]
    s.send("mining.authorize", [user, args.password])

    n = args.threads
    gen, counter = mp.Value("i", 0), mp.Value("Q", 0)
    job_qs = [mp.Queue() for _ in range(n)]
    result_q = mp.Queue()
    procs = [mp.Process(target=worker, args=(i, n, job_qs[i], result_q, gen, counter), daemon=True) for i in range(n)]
    for p in procs: p.start()

    state = {"diff": 1.0, "job": None, "accepted": 0, "rejected": 0, "pending": {}}

    def dispatch():
        if not state["job"]: return
        with gen.get_lock(): gen.value += 1
        target = int(DIFF1_TARGET / state["diff"])
        for q in job_qs: q.put((gen.value, state["job"], en1, en2_size, target))

    def submitter():
        while True:
            g, job_id, en2, ntime, nonce, hv, is_block = result_q.get()
            if g != gen.value: continue
            try: mid = s.send("mining.submit", [user, job_id, en2, ntime, nonce])
            except OSError: return
            state["pending"][mid] = is_block
            tag = "*** ¡BLOQUE ENCONTRADO! ***" if is_block else "share"
            print(f"[>] Enviando {tag} job={job_id} nonce={nonce} hash={hv:064x}")

    def stats():
        last, t0 = 0, time.time()
        while True:
            time.sleep(args.stats)
            v = counter.value; t = time.time()
            print(f"[i] {(v - last) / (t - t0) / 1000:.1f} kH/s | diff {state['diff']} | "
                  f"aceptados {state['accepted']} rechazados {state['rejected']}")
            last, t0 = v, t

    threading.Thread(target=submitter, daemon=True).start()
    threading.Thread(target=stats, daemon=True).start()

    try:
        pool_loop(s, state, dispatch)
    finally:
        for p in procs: p.terminate()
        s.sock.close()

def pool_loop(s, state, dispatch):
    while True:
        msg = s.recv()
        method = msg.get("method")
        if method == "mining.set_difficulty":
            state["diff"] = float(msg["params"][0]); print(f"[i] Dificultad del pool: {state['diff']}")
            dispatch()
        elif method == "mining.notify":
            p = msg["params"]
            state["job"] = dict(job_id=p[0], prevhash=p[1], coinb1=p[2], coinb2=p[3],
                                merkle_branch=p[4], version=p[5], nbits=p[6], ntime=p[7])
            print(f"[i] Nuevo trabajo {p[0]} (clean={p[8]})")
            dispatch()
        elif msg.get("id") in state["pending"]:
            is_block = state["pending"].pop(msg["id"])
            if msg.get("result"):
                state["accepted"] += 1; print("[✓] Aceptado" + (" — ¡BLOQUE!" if is_block else ""))
            else:
                state["rejected"] += 1; print(f"[x] Rechazado: {msg.get('error')}")
        elif msg.get("id") == 2:
            print("[+] Autorizado" if msg.get("result") else f"[x] Autorización fallida: {msg.get('error')}")

# ---------------------------------------------------------------- pruebas
def verify_genesis():
    """Reconstruye y hashea la cabecera del bloque génesis de Bitcoin."""
    header = (struct.pack("<I", 1) + b"\x00" * 32
              + bytes.fromhex("4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b")[::-1]
              + struct.pack("<III", 1231006505, 0x1d00ffff, 2083236893))
    h = dsha256(header)[::-1].hex()
    ok = h == "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
    print(f"Hash génesis: {h}\n{'OK ✓' if ok else 'FALLO ✗'}")
    return ok

def demo(args):
    """Mina un bloque falso localmente con dificultad baja para ver el proceso."""
    zeros = args.demo_bits
    target = (1 << (256 - zeros)) - 1
    prefix = struct.pack("<I", 0x20000000) + os.urandom(64) + struct.pack("<II", int(time.time()), 0x1d00ffff)
    print(f"[demo] Buscando un hash con {zeros} bits a cero al inicio...")
    mid = hashlib.sha256(prefix[:64]); tail = prefix[64:]; t0 = time.time()
    for nonce in range(0x100000000):
        h = mid.copy(); h.update(tail + struct.pack("<I", nonce))
        d = hashlib.sha256(h.digest()).digest()
        if int.from_bytes(d, "little") <= target:
            dt = time.time() - t0
            print(f"[demo] ¡Encontrado! nonce={nonce} hash={d[::-1].hex()}")
            print(f"[demo] {nonce + 1} hashes en {dt:.2f}s ({(nonce + 1) / dt / 1000:.1f} kH/s)")
            return

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Minero de Bitcoin Stratum en Python")
    ap.add_argument("--pool", default="public-pool.io:21496", help="host:puerto del pool")
    ap.add_argument("--address", help="tu dirección de Bitcoin (recibe la recompensa)")
    ap.add_argument("--worker", default="py")
    ap.add_argument("--password", default="x")
    ap.add_argument("--threads", type=int, default=os.cpu_count())
    ap.add_argument("--stats", type=int, default=30, help="segundos entre estadísticas")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--demo-bits", type=int, default=20)
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.verify: verify_genesis()
    elif a.demo: demo(a)
    elif not a.address: ap.error("--address es obligatorio para minar en un pool")
    else:
        while True:
            try: mine_pool(a)
            except (ConnectionError, OSError, json.JSONDecodeError) as e:
                print(f"[!] {e} — reconectando en 10s"); time.sleep(10)
