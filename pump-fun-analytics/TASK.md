# Task: Pump.fun Token Sniper Analytics — Jan 20, 2026

## Goal
Build a 3-step analytics pipeline for pump.fun token launches on January 20, 2026.
Each step is a separate Python script with its own output file.

## Constants
- **Date range**: Jan 20, 2026 UTC (Unix: 1768867200 to 1768953600)
- **Alchemy RPC**: https://solana-mainnet.g.alchemy.com/v2/vdQ02Yrm0xuJNYOCH0MbgJt1FnHEp6zt
- **Pump.fun program ID**: 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P
- **Pump.fun API base**: https://frontend-api.pump.fun
- **DexScreener API base**: https://api.dexscreener.com
- **Graduation threshold**: 100% bonding curve completion (~$69K SOL collected = ~800M+ market cap in lamports)

## Project Structure
```
pump-fun-analytics/
  config.py                   # Shared config, constants, RPC helpers
  step1_fetch_launches.py     # STEP 1 script
  step2_near_graduation.py    # STEP 2 script
  step3_graduated_price.py    # STEP 3 script
  analyze.py                  # Final analysis + recommendations
  requirements.txt
  data/
    step1_launches.json       # Raw launch + hourly price data (output of step1)
    step2_near_grad.json      # Near-graduation tokens + % (output of step2)
    step3_price_action.json   # Price action for graduated tokens (output of step3)
  output/
    step1_report.md
    step2_report.md
    step3_report.md
    analysis_report.md
```

## Implementation Details

### config.py
- Constants: DATE_START, DATE_END, ALCHEMY_RPC, PUMPFUN_API, DEXSCREENER_API
- Helper: rate-limited HTTP get with retry (respect 429s, sleep 1s between calls)
- Helper: get_solana_block_time(slot) via Alchemy RPC

---

## STEP 1 — step1_fetch_launches.py

### Purpose
Fetch every pump.fun token launched on Jan 20, 2026.

### Data Sources
1. **Pump.fun API pagination**: `GET /coins?offset=N&limit=50&sort=created_timestamp&order=DESC`
   - Paginate until `created_timestamp` falls before Jan 20, 2026 UTC
   - Filter for tokens where `created_timestamp` is between DATE_START and DATE_END
   - For each token collect:
     - `mint` (token address)
     - `name`, `symbol`
     - `created_timestamp`
     - `complete` (bool — graduated?)
     - `raydium_pool` (null if not graduated)
     - `king_of_the_hill_timestamp`
     - `bonding_curve` address
     - `usd_market_cap`
     - `virtual_sol_reserves`, `virtual_token_reserves` (for graduation % calc)

2. **Hourly price data (for 24h after launch)**:
   - For each token, attempt DexScreener pair lookup by mint address
   - Endpoint: `GET https://api.dexscreener.com/latest/dex/tokens/{mint}`
   - If pair found: use pair address to fetch candles
   - Candle endpoint: `GET https://api.dexscreener.com/latest/dex/candles/solana/{pairAddress}?from={launch_ts}&to={launch_ts+86400}&res=1h`
   - If DexScreener fails/returns nothing: store empty array with note "no_price_data"
   - Store hourly OHLCV array per token

### Statistics to compute
- total_launched: count of all tokens
- total_graduated: count where `complete == true`
- total_died: count where `complete == false` AND no significant volume detected
- graduation_rate: percentage

### Output
- `data/step1_launches.json`: array of token objects with all fields + hourly_prices
- `output/step1_report.md`: markdown summary with stats table

---

## STEP 2 — step2_near_graduation.py

### Purpose
Among tokens that did NOT graduate within 24h of launch, calculate how close they got.

### Input
- Load `data/step1_launches.json`

### Graduation % Formula
Pump.fun uses a bonding curve. Graduation % can be approximated from:
- `virtual_sol_reserves` and `virtual_token_reserves` fields from pump.fun API
- OR from `usd_market_cap` relative to graduation threshold (~$69,000 USD or ~65 SOL raised)
- Formula: `grad_pct = min(usd_market_cap / 69000 * 100, 100)`
- Alternatively, pump.fun API may return a `bonding_curve_progress` field — use it if present

### Filters
- Only include tokens where `complete == false` (did not graduate)
- Only include tokens where grad_pct > 0 (had some activity)
- Sort by grad_pct DESC

### Bucket analysis
Compute counts/percentages for ranges:
- 90–99% (extremely close)
- 75–89% (close)
- 50–74% (halfway)
- 25–49% (some traction)
- 1–24% (minimal)
- 0% (dead on arrival)

### Output
- `data/step2_near_grad.json`: array of {mint, name, symbol, grad_pct, usd_market_cap, hourly_prices}
- `output/step2_report.md`: markdown with bucket table + top 20 closest tokens table

---

## STEP 3 — step3_graduated_price.py

### Purpose
For each graduated token, fetch:
1. Last 5 minutes of 1-min candles BEFORE graduation
2. First 30 minutes of 1-min candles AFTER graduation
3. Hourly candles for 24h AFTER graduation

### Input
- Load `data/step1_launches.json` — filter where `complete == true`

### Data Sources
For graduated tokens (they have Raydium pools):
- Use DexScreener pair address from Raydium pool
- 1-min candles: `GET /latest/dex/candles/solana/{pairAddress}?from={ts}&to={ts}&res=1m`
- 1-hour candles: same endpoint with `res=1h`

For pre-graduation price (on bonding curve):
- Try pump.fun trade history: `GET https://frontend-api.pump.fun/trades/{mint}?limit=1000`
- Reconstruct 1-min OHLCV from raw trades if available
- Fallback: note "bonding_curve_only_no_candles"

### Per-token structure
```json
{
  "mint": "...",
  "name": "...",
  "symbol": "...",
  "created_timestamp": 1234567890,
  "graduation_timestamp": 1234567890,
  "grad_pct_at_graduation": 100,
  "pre_grad_5min_candles": [...],   // 1-min OHLCV, 5 candles
  "post_grad_30min_candles": [...], // 1-min OHLCV, 30 candles
  "post_grad_24h_hourly": [...]     // 1-hour OHLCV, 24 candles
}
```

### Statistics
- tokens_graduated: count
- immediate_dump (price -20% within 30 min of grad): count + %
- sustained_pump (price +50% within 30 min of grad): count + %
- median_peak_multiplier: median of (peak price / graduation price) in first 30 min
- median_24h_change: median price change at 24h vs graduation price

### Output
- `data/step3_price_action.json`
- `output/step3_report.md`: markdown with stats + individual token tables

---

## analyze.py

### Purpose
Load all three data files and produce final analysis answering two specific questions:

**Q1: Is it profitable to buy when graduation % is close to 100% (pre-graduation)?**
- Use step2 data (near-graduation tokens) + step3 pre-grad price action
- Compare: tokens at 90–99% that eventually graduated vs those that didn't
- Calculate: if you bought at 90%+ graduation threshold, what % of the time did you profit?
- Calculate: average return, win rate, expected value

**Q2: Post-graduation strategy — what actually works?**
Compare three strategies using step3 data:
1. **Quick flip**: Buy at graduation, sell at 15-min mark — what % profitable? avg return?
2. **Ladder sell**: Buy at graduation, sell 50% at 2x, 25% at 5x, hold rest — simulation
3. **Hold 24h**: Buy at graduation, sell at 24h mark — avg return?

Pick the winner based on expected value and consistency.

### Output
- `output/analysis_report.md`: Investment thesis with data-backed conclusions

---

## Requirements
```
requests>=2.31.0
python-dateutil>=2.8.2
```

## Notes
- Add generous rate limiting (1 req/sec for pump.fun, 2 req/sec for DexScreener)
- If API data is unavailable for historical prices, note it in output but don't crash
- Print progress to stdout as you paginate (e.g., "Fetched 500/~10000 tokens...")
- All timestamps are Unix seconds UTC
- Run each step independently: `python step1_fetch_launches.py`, etc.
- step2 and step3 depend on step1's output existing
