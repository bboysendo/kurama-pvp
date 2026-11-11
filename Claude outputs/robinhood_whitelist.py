#!/usr/bin/env python3
"""
robinhood_whitelist.py — Holders actuales de llaves ERC-1155 en Robinhood Chain
(chain ID 4663) -> whitelist sin duplicados para OpenSea.

Única dependencia:  pip install requests
(Opcional: pip install eth-utils  -> direcciones en formato checksum EIP-55.)

USO
    python robinhood_whitelist.py                     # modo auto (recomendado)
    python robinhood_whitelist.py --source rpc        # forzar una fuente
    python robinhood_whitelist.py --verify            # confirmar saldos con balanceOf on-chain

FUENTES (en modo auto se prueban en este orden y, si una falla para un
TokenID, se pasa a la siguiente):
  1. blockscout : explorador Blockscout de Robinhood Chain. Devuelve directamente
                  los holders de cada TokenID. Sin API key en la instancia pública.
  2. rpc        : lee del nodo los eventos TransferSingle / TransferBatch del
                  contrato y reconstruye el saldo de cada wallet. No necesita
                  ninguna API key; es la fuente más "a prueba de todo".
  3. etherscan  : API V2 de Etherscan (RobinScan, robin.etherscan.io) con
                  chainid=4663, acción token1155tx. Requiere ETHERSCAN_API_KEY y,
                  desde el 16/10/2026, un plan de pago (Lite o superior) para esta red.

SALIDA (en la carpeta desde la que lo ejecutes)
  whitelist_final.txt      una wallet por línea, sin duplicados (para OpenSea)
  whitelist_final.csv      igual, en .csv: SOLO la columna wallet, sin cabecera
                           (OpenSea admite como mucho: wallet[,mints[,precio]])
  whitelist_detalle.csv    informe: wallet + qué llaves tiene y cuántas
                           (NO subir a OpenSea)

CONVERTIR un CSV ya generado (p. ej. el antiguo de 6 columnas) al formato OpenSea:
    python robinhood_whitelist.py --convert whitelist_final.csv
  -> crea whitelist_opensea.csv con una wallet por línea, sin duplicados.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import defaultdict

import requests

# =============================================================================
# CONFIGURACIÓN
# =============================================================================
CONTRACT = "0x64Fb2678cCe04A352a405eAa024eE02935f0ACb9"

TOKEN_IDS = {
    "Sendo": 154314001798769644125721834370105252888,
    "Lak4br4": 172583295940408141571035696956442772275,
    "Valdeande": 114920342821086519530444906800617075568,
}

CHAIN_ID = 4663  # Robinhood Chain mainnet

# Blockscout. Instancia pública (sin key):
BLOCKSCOUT_URL = os.getenv("BLOCKSCOUT_URL", "https://robinhoodchain.blockscout.com")
# Alternativa con key gratuita de dev.blockscout.com:
#   BLOCKSCOUT_URL = "https://api.blockscout.com/4663"  + BLOCKSCOUT_API_KEY
BLOCKSCOUT_API_KEY = os.getenv("BLOCKSCOUT_API_KEY", "")

# RPC. El público tiene límite de peticiones; si va lento, usa el tuyo
# (Alchemy: https://robinhood-mainnet.g.alchemy.com/v2/TU_KEY, QuickNode, Dwellir...).
RPC_URL = os.getenv("ROBINHOOD_RPC_URL", "https://rpc.mainnet.chain.robinhood.com")

# Etherscan API V2 (RobinScan). Clave en https://etherscan.io/myapikey
ETHERSCAN_API_URL = "https://api.etherscan.io/v2/api"
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")

ETHERSCAN_PAGE_SIZE = 1000  # máximo por petición en planes bajos

OUT_TXT = "whitelist_final.txt"
OUT_CSV = "whitelist_final.csv"
OUT_DETAIL = "whitelist_detalle.csv"
OUT_CONVERTED = "whitelist_opensea.csv"

# Direcciones que nunca deben entrar en la whitelist.
EXCLUDE = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    CONTRACT.lower(),
}

# Firmas de los eventos ERC-1155
TOPIC_TRANSFER_SINGLE = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
TOPIC_TRANSFER_BATCH = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"
SELECTOR_BALANCE_OF = "0x00fdd58e"  # balanceOf(address,uint256)

HTTP = requests.Session()
HTTP.headers["User-Agent"] = "robinhood-whitelist/1.0"
TIMEOUT = 30


class SourceError(RuntimeError):
    """La fuente no puede dar el dato (sin key, no soportado, caída...)."""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _get_json(url: str, params: dict | None = None, retries: int = 5) -> dict:
    """GET con reintentos y espera progresiva ante 429 / 5xx / cortes."""
    for attempt in range(retries):
        try:
            r = HTTP.get(url, params=params, timeout=TIMEOUT)
        except requests.RequestException as e:
            err = e
        else:
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                raise SourceError(f"404 en {url}")
            err = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        time.sleep(1.5 * (attempt + 1))
    raise SourceError(f"{url}: {err}")


# =============================================================================
# FUENTE 1: Blockscout — holders por TokenID
# =============================================================================
def holders_blockscout(token_id: int) -> dict[str, int]:
    url = f"{BLOCKSCOUT_URL.rstrip('/')}/api/v2/tokens/{CONTRACT}/instances/{token_id}/holders"
    params: dict = {"apikey": BLOCKSCOUT_API_KEY} if BLOCKSCOUT_API_KEY else {}
    balances: dict[str, int] = {}
    while True:
        data = _get_json(url, params)
        if "items" not in data:
            raise SourceError(f"Respuesta inesperada de Blockscout: {str(data)[:200]}")
        for it in data["items"]:
            addr = (it.get("address") or {}).get("hash", "").lower()
            if addr:
                balances[addr] = balances.get(addr, 0) + int(it.get("value") or 0)
        nxt = data.get("next_page_params")
        if not nxt:
            return balances
        params = {**nxt, **({"apikey": BLOCKSCOUT_API_KEY} if BLOCKSCOUT_API_KEY else {})}
        time.sleep(0.25)


# =============================================================================
# FUENTE 2: RPC — reconstrucción desde eventos TransferSingle / TransferBatch
# =============================================================================
def _rpc(method: str, params: list, retries: int = 5):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for attempt in range(retries):
        try:
            r = HTTP.post(RPC_URL, json=payload, timeout=TIMEOUT)
            if r.status_code == 429 or r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            data = r.json()
        except (requests.RequestException, ValueError, RuntimeError) as e:
            err = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if "error" in data:
            # errores "lógicos" (rango demasiado grande, etc.) los gestiona quien llama
            raise ValueError(str(data["error"]))
        return data["result"]
    raise SourceError(f"RPC {method}: {err}")


def _words(hexdata: str) -> list[int]:
    h = hexdata[2:] if hexdata.startswith("0x") else hexdata
    return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]


def _topic_addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def decode_transfer_log(lg: dict) -> list[tuple[str, str, int, int]]:
    """-> [(from, to, token_id, cantidad), ...] para un log ERC-1155."""
    topics = lg["topics"]
    frm, to = _topic_addr(topics[2]), _topic_addr(topics[3])
    w = _words(lg["data"])
    if topics[0].lower() == TOPIC_TRANSFER_SINGLE:
        return [(frm, to, w[0], w[1])]
    # TransferBatch: data = (uint256[] ids, uint256[] values) codificado ABI
    ids_off, vals_off = w[0] // 32, w[1] // 32
    n_ids, n_vals = w[ids_off], w[vals_off]
    ids = w[ids_off + 1: ids_off + 1 + n_ids]
    vals = w[vals_off + 1: vals_off + 1 + n_vals]
    return [(frm, to, i, v) for i, v in zip(ids, vals)]


def _find_creation_block(latest: int) -> int:
    """Búsqueda binaria del bloque en que se desplegó el contrato (eth_getCode).
    Ahorra escanear toda la cadena. Si el nodo no guarda histórico, empieza en 0."""
    try:
        if _rpc("eth_getCode", [CONTRACT, hex(latest)]) in ("0x", None):
            raise SourceError("El contrato no existe en esta red (¿dirección o RPC correctos?).")
        lo, hi = 0, latest
        while lo < hi:
            mid = (lo + hi) // 2
            if _rpc("eth_getCode", [CONTRACT, hex(mid)]) not in ("0x", None):
                hi = mid
            else:
                lo = mid + 1
        return lo
    except ValueError:
        return 0


def balances_rpc(token_ids: set[int], from_block: int | None = None) -> dict[int, dict[str, int]]:
    latest = int(_rpc("eth_blockNumber", []), 16)
    start = from_block if from_block is not None else _find_creation_block(latest)
    log(f"  [rpc] escaneando bloques {start:,} -> {latest:,}")
    bal: dict[int, dict[str, int]] = {t: defaultdict(int) for t in token_ids}
    chunk, cur, n_logs = 50_000, start, 0
    while cur <= latest:
        end = min(cur + chunk - 1, latest)
        try:
            logs = _rpc("eth_getLogs", [{
                "address": CONTRACT, "fromBlock": hex(cur), "toBlock": hex(end),
                "topics": [[TOPIC_TRANSFER_SINGLE, TOPIC_TRANSFER_BATCH]],
            }])
        except ValueError:  # rango o nº de resultados demasiado grande -> rango más pequeño
            if chunk <= 1:
                raise SourceError("El RPC rechaza incluso rangos de 1 bloque.")
            chunk = max(1, chunk // 2)
            continue
        for lg in logs:
            if lg.get("removed"):
                continue
            for frm, to, tid, amount in decode_transfer_log(lg):
                if tid in bal:
                    bal[tid][frm] -= amount
                    bal[tid][to] += amount
        n_logs += len(logs)
        cur = end + 1
        if len(logs) < 2_000 and chunk < 1_000_000:
            chunk *= 2  # va holgado: acelera
        log(f"  [rpc] bloque {end:,}/{latest:,} · {n_logs} eventos")
    return {t: {a: v for a, v in b.items() if v > 0} for t, b in bal.items()}


def balance_of(addr: str, token_id: int) -> int:
    data = SELECTOR_BALANCE_OF + addr[2:].lower().rjust(64, "0") + format(token_id, "064x")
    return int(_rpc("eth_call", [{"to": CONTRACT, "data": data}, "latest"]), 16)


# =============================================================================
# FUENTE 3: Etherscan API V2 (RobinScan) — transferencias ERC-1155
# =============================================================================
def balances_etherscan(token_ids: set[int]) -> dict[int, dict[str, int]]:
    if not ETHERSCAN_API_KEY:
        raise SourceError("Falta ETHERSCAN_API_KEY.")
    bal: dict[int, dict[str, int]] = {t: defaultdict(int) for t in token_ids}
    seen: set = set()
    start, page_size = 0, ETHERSCAN_PAGE_SIZE
    while True:
        data = _get_json(ETHERSCAN_API_URL, {
            "chainid": CHAIN_ID, "module": "account", "action": "token1155tx",
            "contractaddress": CONTRACT, "startblock": start, "endblock": 99_999_999_999,
            "page": 1, "offset": page_size, "sort": "asc", "apikey": ETHERSCAN_API_KEY,
        })
        rows = data.get("result")
        if data.get("status") != "1" or not isinstance(rows, list):
            if isinstance(rows, list) or "No transactions found" in str(data.get("message")):
                break
            raise SourceError(f"Etherscan: {data.get('message')} — {str(rows)[:200]}")
        for r in rows:
            key = (r["hash"], r.get("logIndex"), r["from"], r["to"], r["tokenID"], r["tokenValue"])
            if key in seen:
                continue
            seen.add(key)
            tid = int(r["tokenID"])
            if tid in bal:
                bal[tid][r["from"].lower()] -= int(r["tokenValue"])
                bal[tid][r["to"].lower()] += int(r["tokenValue"])
        if len(rows) < page_size:
            break
        last = int(rows[-1]["blockNumber"])
        # se reanuda en el último bloque (sus filas repetidas se descartan con `seen`)
        if last == start:
            raise SourceError("Más de 1000 transferencias en un solo bloque: usa --source rpc.")
        start = last
        log(f"  [etherscan] {len(seen)} transferencias leídas (bloque {last:,})")
        time.sleep(0.35)
    return {t: {a: v for a, v in b.items() if v > 0} for t, b in bal.items()}


# =============================================================================
# Orquestación
# =============================================================================
def collect(source: str, from_block: int | None) -> dict[int, dict[str, int]]:
    wanted = set(TOKEN_IDS.values())
    order = ["blockscout", "rpc", "etherscan"] if source == "auto" else [source]
    result: dict[int, dict[str, int]] = {}
    for src in order:
        pending = wanted - result.keys()
        if not pending:
            break
        log(f"→ Fuente: {src} ({len(pending)} TokenID pendientes)")
        try:
            if src == "blockscout":
                for tid in sorted(pending):
                    result[tid] = holders_blockscout(tid)
                    log(f"  [blockscout] {tid}: {len(result[tid])} holders")
            elif src == "rpc":
                result.update(balances_rpc(pending, from_block))
            elif src == "etherscan":
                result.update(balances_etherscan(pending))
        except SourceError as e:
            log(f"  ✗ {src} no disponible: {e}")
    missing = wanted - result.keys()
    if missing:
        raise SystemExit(f"No se pudieron obtener los holders de: {sorted(missing)}")
    return {t: {a.lower(): v for a, v in h.items() if v > 0 and a.lower() not in EXCLUDE}
            for t, h in result.items()}


def checksum(addr: str) -> str:
    try:
        from eth_utils import to_checksum_address  # opcional
        return to_checksum_address(addr)
    except ImportError:
        return addr  # minúsculas: OpenSea también las acepta


def build_whitelist(per_token: dict[int, dict[str, int]]) -> dict[str, dict[str, int]]:
    """Consolida todos los TokenID: {wallet: {nombre_llave: cantidad}}, sin duplicados."""
    name_of = {v: k for k, v in TOKEN_IDS.items()}
    merged: dict[str, dict[str, int]] = {}
    for tid, holders in per_token.items():
        for addr, amount in holders.items():
            a = addr.lower()
            if amount > 0 and a not in EXCLUDE and len(a) == 42 and a.startswith("0x"):
                merged.setdefault(a, {})[name_of[tid]] = amount
    return dict(sorted(merged.items()))


def convert_to_opensea(path: str) -> None:
    """Saca la primera dirección 0x... de cada línea de cualquier CSV/TXT y
    escribe whitelist_opensea.csv: una wallet por línea, sin cabecera ni
    duplicados (formato que acepta la allowlist de OpenSea)."""
    import re
    rx = re.compile(r"0x[0-9a-fA-F]{40}")
    seen, out = set(), []
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            m = rx.search(line)
            if m and m.group(0).lower() not in seen and m.group(0).lower() not in EXCLUDE:
                seen.add(m.group(0).lower())
                out.append(checksum(m.group(0).lower()))
    with open(OUT_CONVERTED, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + ("\n" if out else ""))
    log(f"{len(out)} wallets únicas -> {OUT_CONVERTED}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Whitelist de holders de llaves ERC-1155 en Robinhood Chain.")
    ap.add_argument("--source", choices=["auto", "blockscout", "rpc", "etherscan"], default="auto")
    ap.add_argument("--from-block", type=int, default=None,
                    help="(rpc) bloque inicial; por defecto se detecta el de despliegue del contrato")
    ap.add_argument("--verify", action="store_true",
                    help="confirma cada saldo con balanceOf() en el RPC y descarta los que estén a 0")
    ap.add_argument("--convert", metavar="ARCHIVO",
                    help="convierte un CSV/TXT existente al formato de OpenSea (no consulta la blockchain)")
    args = ap.parse_args()
    if args.convert:
        convert_to_opensea(args.convert)
        return

    per_token = collect(args.source, args.from_block)

    if args.verify:
        log("→ Verificando saldos con balanceOf() ...")
        for tid, holders in per_token.items():
            for addr in list(holders):
                real = balance_of(addr, tid)
                if real <= 0:
                    del holders[addr]
                else:
                    holders[addr] = real
                time.sleep(0.05)

    wl = build_whitelist(per_token)
    names = list(TOKEN_IDS)

    with open(OUT_TXT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(checksum(a) for a in wl) + ("\n" if wl else ""))
    # Formato OpenSea: una wallet por línea, sin cabecera ni columnas extra.
    with open(OUT_CSV, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(checksum(a) for a in wl) + ("\n" if wl else ""))
    with open(OUT_DETAIL, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["wallet", *(f"llaves_{n}" for n in names), "total_llaves", "n_tipos"])
        for a, held in wl.items():
            w.writerow([checksum(a), *(held.get(n, 0) for n in names), sum(held.values()), len(held)])

    total_raw = sum(len(h) for h in per_token.values())
    log("\n===== RESUMEN =====")
    for n in names:
        log(f"  {n:<10} {len(per_token[TOKEN_IDS[n]]):>6} holders")
    log(f"  Total (con duplicados): {total_raw}")
    log(f"  Wallets únicas:         {len(wl)}  (duplicados eliminados: {total_raw - len(wl)})")
    log(f"  -> {OUT_TXT}   (subir a OpenSea)\n  -> {OUT_CSV}   (subir a OpenSea)\n"
        f"  -> {OUT_DETAIL}   (solo informe)")


if __name__ == "__main__":
    main()
