# Task V2: Pump.fun Token Sniper Analytics — CORRECTED IMPLEMENTATION

## CRITICAL CHANGES FROM V1
- pump.fun frontend API is 530 BLOCKED — do NOT use it
- Instruction is "CreateV2" not "Create"
- Use on-chain Alchemy RPC for token discovery
- Use GeckoTerminal API for historical OHLCV (free, no key needed)

## Constants
- ALCHEMY_RPC = "https://solana-mainnet.g.alchemy.com/v2/vdQ02Yrm0xuJNYOCH0MbgJt1FnHEp6zt"
- PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
- TOKEN22_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
- JAN20_START_SLOT = 394635000   # ~Jan 20 2026 00:00 UTC
- JAN20_END_SLOT = 394855000     # ~Jan 20 2026 23:59 UTC
- JAN20_START_TS = 1768867200
- JAN20_END_TS = 1768953600
- GRADUATION_USD = 69000         # ~$69K = 100% bonding curve completion

## Architecture (on-chain approach)

### How to identify CreateV2 transactions from blocks:
Each pump.fun CreateV2 transaction has a UNIQUE fingerprint detectable from accounts data:
1. PUMP_PROGRAM is in accountKeys
2. At least one account in accountKeys ends with "pump" (this is the token mint)
3. That "pump"-suffixed account has preBalance=0 and postBalance>0 (newly created)

This means we can use getBlock(transactionDetails="accounts") — LIGHTER than "full" —
to scan blocks efficiently without fetching full instruction data.

### Block scan strategy:
1. Use getBlocks in 1000-slot chunks to get valid slot list (~220 API calls)
2. Fetch each valid block with transactionDetails="accounts" in batches of 50 concurrent
3. Filter transactions using the fingerprint above
4. Extract: mint_address (account ending in "pump"), creator_wallet (index 0), signature, slot, blockTime

### Graduation detection:
A token "graduated" if it has a Raydium pool (liquidity migrated from bonding curve).
Check via DexScreener:
  GET https://api.dexscreener.com/latest/dex/tokens/{mint_address}
  If response has pairs with dexId=="raydium", it graduated.
  Extract: pairAddress, liquidity, marketCap, fdv from response.

### Price data (GeckoTerminal — FREE, no API key):
Hourly OHLCV: GET https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_address}/ohlcv/hour
  Params: aggregate=1, before_timestamp={unix_ts}, limit=100, currency=usd
  Returns up to 100 hourly candles

1-min OHLCV: GET https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_address}/ohlcv/minute
  Params: aggregate=1, before_timestamp={unix_ts}, limit=60, currency=usd

For pre-graduation price (bonding curve), no OHLCV exists — use DexScreener pair creation
timestamp and first available price as proxy. Note this limitation in output.

### Graduation percentage for non-graduated tokens:
From DexScreener response, use fdv/marketCap fields:
  grad_pct = min((market_cap_usd / 69000) * 100, 100)
  OR if DexScreener has no data (token died with 0 volume): grad_pct = 0, status = "dead"

---

## FILE: config.py (overwrite existing)

```python
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
```

---

## FILE: step1_fetch_launches.py (overwrite existing)

Complete rewrite using on-chain approach:

1. PHASE 1 — Get valid block slots for Jan 20:
   - Call getBlocks(start, start+999) in loop until end of range
   - Collect all valid slot numbers into a list
   - Print progress: "Phase 1: Found X valid blocks in Jan 20 range"

2. PHASE 2 — Scan blocks for CreateV2 transactions:
   - Process blocks in batches of 50 using ThreadPoolExecutor
   - For each block: getBlock(slot, {"encoding":"json","transactionDetails":"accounts","maxSupportedTransactionVersion":0,"rewards":False})
   - For each transaction in block:
     * Get accountKeys as list of strings
     * Check: PUMP_PROGRAM in account_keys
     * Check: any account ending in "pump"
     * Get pre/postBalances from meta
     * Check: the "pump" account has preBalance=0 and postBalance>0
     * If all true: this is a CreateV2 — extract:
       - mint = account ending in "pump"
       - creator = accountKeys[0] (fee payer / token creator)
       - signature = transaction["transaction"]["signatures"][0]
       - slot = block_slot
       - block_time = block["blockTime"]
   - Deduplicate by mint address
   - Print progress every 5000 blocks: "Scanned X/Y blocks | Found Z tokens"

3. PHASE 3 — Enrich with DexScreener data:
   - For each token, GET https://api.dexscreener.com/latest/dex/tokens/{mint}
   - Rate limit: sleep 0.4s between calls (respect 2.5 req/sec)
   - Extract from first pair found:
     * graduated = (any pair with dexId=="raydium" exists)
     * pair_address = first raydium pair's pairAddress (or None)
     * market_cap_usd = priceUsd * (total supply if available, else from fdv)
     * fdv = fdv field
     * liquidity_usd = liquidity.usd
     * price_usd = priceUsd
     * pair_created_at = pairCreatedAt (ms timestamp of when pair was created)
   - Graduation percentage: grad_pct = min(fdv/69000*100, 100) if fdv else 0
   - Status classification:
     * "graduated" if graduated==True
     * "active" if not graduated and (fdv > 1000 or some trading volume)
     * "dead" if fdv < 100 and no recent trades

4. PHASE 4 — Fetch hourly price data (24h window):
   - Only for graduated tokens (have a Raydium pair_address)
   - GET GeckoTerminal OHLCV for 24h after launch:
     url = f"{GECKOTERMINAL_BASE}/networks/solana/pools/{pair_address}/ohlcv/hour"
     params = {"aggregate": 1, "before_timestamp": block_time + 86400, "limit": 48, "currency": "usd"}
   - Store ohlcv array (trim to first 24 candles after launch time)
   - Rate limit: 0.5s between calls

5. SAVE:
   - data/step1_launches.json:
     Array of objects: {mint, creator, signature, slot, block_time, graduated, pair_address,
       market_cap_usd, fdv, liquidity_usd, price_usd, pair_created_at, grad_pct, status, hourly_prices_24h}
   - Also save summary stats to data/step1_summary.json:
     {total_launched, total_graduated, total_active, total_dead, graduation_rate_pct, scan_completed_at}

6. PRINT step1_report.md:
   - Summary table with stats
   - Top 20 graduated tokens by peak market cap
   - Methodology note explaining on-chain approach

IMPORTANT implementation notes:
- Use ThreadPoolExecutor(max_workers=50) for block fetching — Alchemy handles concurrent RPC calls well
- Add a global rate limiter or simple sleep(0.01) between concurrent calls to avoid 429s
- getBlocks API: returns list of slot numbers. Call as: rpc_call("getBlocks", [start_slot, end_slot])
  Note: getBlocks may have a max range limit of 500,000 slots — test with 1000-slot chunks first
- If a block returns null/None (skipped slot), skip it
- Print estimated time remaining based on blocks processed per second

---

## FILE: step2_near_graduation.py (overwrite existing)

Load data/step1_launches.json.
Filter: status != "graduated" (non-graduated tokens).
For each: use grad_pct already computed in step1.
Compute bucket distribution.
For near-grad tokens (grad_pct >= 50), try to fetch more precise market cap from DexScreener
if not already fetched (may have been "dead" with no pair).

Save data/step2_near_grad.json and output/step2_report.md.

---

## FILE: step3_graduated_price.py (overwrite existing)

Load data/step1_launches.json.
Filter: status == "graduated".

For each graduated token:
1. Pre-graduation price action (last 5 min before graduation):
   - graduation_time = pair_created_at (ms) / 1000
   - GET GeckoTerminal minute candles:
     url = f"{GECKOTERMINAL_BASE}/networks/solana/pools/{pair_address}/ohlcv/minute"
     params = {"aggregate": 1, "before_timestamp": int(graduation_time) + 300, "limit": 10, "currency": "usd"}
   - Filter to candles BEFORE graduation_time
   - Note: pre-graduation price on bonding curve may not be available — store what's found

2. Post-graduation 30-min (1-min candles):
   - Same endpoint, before_timestamp = graduation_time + 1800, limit=60
   - Filter to candles AFTER graduation_time, first 30 candles

3. Post-graduation 24h hourly (already in step1 hourly_prices_24h if fetched):
   - If not already fetched in step1, fetch now

4. Compute per-token stats:
   - grad_price = first post-grad candle open price
   - peak_30min = max high in post_grad_30min_candles
   - peak_30min_mult = peak_30min / grad_price
   - price_at_24h = last candle close in post_grad_24h_hourly
   - change_24h_pct = (price_at_24h - grad_price) / grad_price * 100
   - immediate_dump = (price at 15min < grad_price * 0.8)  # -20% in 15 min

5. Aggregate stats:
   - tokens_analyzed
   - pct_immediate_dump (down >20% in 30 min)
   - pct_2x_in_30min
   - pct_5x_in_30min
   - median_peak_30min_multiplier
   - median_24h_change_pct
   - pct_higher_at_24h (still above grad price at 24h)

Save data/step3_price_action.json and output/step3_report.md.

---

## FILE: analyze.py (overwrite existing)

Load all three data files. Produce output/analysis_report.md answering:

Q1: Buy at 90%+ graduation threshold — profitable?
- From step2: how many tokens at 90-99% eventually graduated?
- From step3: for those that graduated, what was the 30min and 24h performance?
- Calculate: P(profit) if you bought at 90%+ and sold at graduation (2x) or held
- Calculate: P(loss) — tokens at 90%+ that died before graduating
- Expected value calculation

Q2: Post-graduation strategy comparison:
Strategy A "Quick flip": Buy at grad, sell at 15min. Avg return using step3 data.
Strategy B "Ladder sell": Simulate 50% sell at 2x, 25% at 5x, hold rest with -60% stop loss.
Strategy C "Hold 24h": Buy at grad, sell exactly 24h later. Avg return.
Strategy D "Momentum filter": Only buy if price UP >20% in first 5 min post-grad.

Compare by: win rate, average return, expected value, max drawdown.

Output clear BUY/PASS/AVOID recommendations for each strategy with supporting data.
