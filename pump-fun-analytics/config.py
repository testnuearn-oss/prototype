import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

ALCHEMY_RPC = "https://solana-mainnet.g.alchemy.com/v2/vdQ02Yrm0xuJNYOCH0MbgJt1FnHEp6zt"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TOKEN22_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

JAN20_START_SLOT = 394635000
JAN20_END_SLOT = 394855000
JAN20_START_TS = 1768867200
JAN20_END_TS = 1768953600
GRADUATION_USD = 69000

DEXSCREENER_BASE = "https://api.dexscreener.com"
GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})

def rpc_call(method, params, retries=3):
    for attempt in range(retries):
        try:
            r = SESSION.post(ALCHEMY_RPC, json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params
            }, timeout=30)
            d = r.json()
            if "error" in d:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return None
            return d.get("result")
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None

def http_get(url, params=None, retries=3, delay=0.5):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=20)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 200:
                return r.json()
            time.sleep(delay)
        except Exception:
            time.sleep(delay)
    return None
