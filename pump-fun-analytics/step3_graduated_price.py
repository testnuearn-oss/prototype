#!/usr/bin/env python3
"""
STEP 3 — For each graduated token, fetch pre/post-graduation price action.

Input:  data/step1_launches.json  (filter where status == 'graduated')
Output:
  data/step3_price_action.json   — per-token price structure + aggregate stats
  output/step3_report.md         — stats + individual tables
"""

import json
import os
import sys
import time
from statistics import median

from config import http_get, GECKOTERMINAL_BASE

os.makedirs("data", exist_ok=True)
os.makedirs("output", exist_ok=True)


def load_launches():
    path = "data/step1_launches.json"
    if not os.path.exists(path):
        print(f"ERROR: {path} not found. Run step1_fetch_launches.py first.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def fetch_ohlcv(pool_address, timeframe, before_timestamp, limit):
    """Fetch OHLCV candles from GeckoTerminal. Returns list of [ts, open, high, low, close, volume]."""
    url = f"{GECKOTERMINAL_BASE}/networks/solana/pools/{pool_address}/ohlcv/{timeframe}"
    params = {
        "aggregate": 1,
        "before_timestamp": int(before_timestamp),
        "limit": limit,
        "currency": "usd",
    }
    data = http_get(url, params=params)
    if not data:
        return []
    try:
        candles = data["data"]["attributes"]["ohlcv_list"]
        return candles
    except (KeyError, TypeError):
        return []


def analyze_token(token):
    """Fetch price action for a graduated token."""
    pair_address = token.get("pair_address")
    if not pair_address:
        return None

    pair_created_ms = token.get("pair_created_at") or 0
    if pair_created_ms:
        graduation_time = pair_created_ms / 1000
    else:
        graduation_time = token.get("block_time") or 0

    if not graduation_time:
        return None

    # 1. Pre-graduation: last 5 min before graduation (1-min candles)
    pre_grad_candles = fetch_ohlcv(
        pair_address, "minute",
        before_timestamp=graduation_time + 300,
        limit=10,
    )
    time.sleep(0.5)
    pre_grad_candles = [c for c in pre_grad_candles if c[0] < graduation_time]

    # 2. Post-graduation 30-min (1-min candles)
    post_30min_candles = fetch_ohlcv(
        pair_address, "minute",
        before_timestamp=graduation_time + 1800,
        limit=60,
    )
    time.sleep(0.5)
    post_30min_candles = [c for c in post_30min_candles if c[0] >= graduation_time]
    post_30min_candles = post_30min_candles[:30]

    # 3. Post-graduation 24h hourly
    hourly_24h = token.get("hourly_prices_24h") or []
    if not hourly_24h:
        hourly_24h = fetch_ohlcv(
            pair_address, "hour",
            before_timestamp=graduation_time + 86400,
            limit=48,
        )
        time.sleep(0.5)
        hourly_24h = [c for c in hourly_24h if c[0] >= graduation_time][:24]

    # Compute per-token stats
    grad_price = None
    if post_30min_candles:
        grad_price = post_30min_candles[0][1]  # open of first post-grad candle

    peak_30min = None
    peak_30min_mult = None
    if post_30min_candles and grad_price and grad_price > 0:
        peak_30min = max(c[2] for c in post_30min_candles)  # max high across candles
        peak_30min_mult = peak_30min / grad_price

    # Price at 15min (candle index 14, close)
    price_at_15min = None
    if len(post_30min_candles) >= 15 and grad_price:
        price_at_15min = post_30min_candles[14][4]

    immediate_dump = False
    if price_at_15min and grad_price and grad_price > 0:
        immediate_dump = price_at_15min < grad_price * 0.8  # -20% in 15 min

    price_at_24h = None
    if hourly_24h:
        price_at_24h = hourly_24h[-1][4]  # last close

    change_24h_pct = None
    if price_at_24h and grad_price and grad_price > 0:
        change_24h_pct = (price_at_24h - grad_price) / grad_price * 100

    return {
        "mint": token["mint"],
        "pair_address": pair_address,
        "graduation_time": graduation_time,
        "grad_price": grad_price,
        "peak_30min": peak_30min,
        "peak_30min_mult": peak_30min_mult,
        "price_at_15min": price_at_15min,
        "immediate_dump": immediate_dump,
        "price_at_24h": price_at_24h,
        "change_24h_pct": change_24h_pct,
        "pre_grad_candles": pre_grad_candles,
        "post_30min_candles": post_30min_candles,
        "hourly_24h": hourly_24h,
    }


def main():
    print("=" * 60)
    print("STEP 3 — Graduated token price action analysis")
    print("=" * 60)

    all_launches = load_launches()
    graduated = [t for t in all_launches if t.get("status") == "graduated"]
    print(f"Loaded {len(all_launches)} total tokens, {len(graduated)} graduated.")

    if not graduated:
        print("No graduated tokens to process.")
        json.dump({"aggregate": {}, "tokens": []},
                  open("data/step3_price_action.json", "w"), indent=2)
        with open("output/step3_report.md", "w") as f:
            f.write("# Step 3 Report\n\nNo graduated tokens found.\n")
        return

    results = []
    total = len(graduated)
    for i, token in enumerate(graduated):
        print(f"  [{i+1}/{total}] Analyzing {token['mint'][:12]}...")
        r = analyze_token(token)
        if r:
            results.append(r)

        if (i + 1) % 10 == 0:
            print(f"  Progress: {i+1}/{total} analyzed, {len(results)} with data")

    # Aggregate stats
    mults = [r["peak_30min_mult"] for r in results if r.get("peak_30min_mult") is not None]
    changes_24h = [r["change_24h_pct"] for r in results if r.get("change_24h_pct") is not None]
    dumps = [r for r in results if r.get("immediate_dump")]
    two_x = [r for r in results if r.get("peak_30min_mult") and r["peak_30min_mult"] >= 2.0]
    five_x = [r for r in results if r.get("peak_30min_mult") and r["peak_30min_mult"] >= 5.0]
    higher_at_24h = [
        r for r in results
        if r.get("change_24h_pct") is not None and r["change_24h_pct"] > 0
    ]

    n = len(results)
    agg = {
        "tokens_analyzed": n,
        "pct_immediate_dump": round(len(dumps) / n * 100, 2) if n else 0,
        "pct_2x_in_30min": round(len(two_x) / n * 100, 2) if n else 0,
        "pct_5x_in_30min": round(len(five_x) / n * 100, 2) if n else 0,
        "median_peak_30min_multiplier": round(median(mults), 4) if mults else None,
        "median_24h_change_pct": round(median(changes_24h), 2) if changes_24h else None,
        "pct_higher_at_24h": round(len(higher_at_24h) / n * 100, 2) if n else 0,
    }

    output = {"aggregate": agg, "tokens": results}
    out_path = "data/step3_price_action.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Saved {out_path}")
    print(f"\nAggregate: {agg}")

    # Report
    report_lines = [
        "# Step 3 Report — Graduated Token Price Action (Jan 20, 2026)",
        "",
        f"**Tokens analyzed:** {agg['tokens_analyzed']}",
        "",
        "## Aggregate Stats",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Immediate dump (>20% down in 15 min) | {agg['pct_immediate_dump']:.1f}% |",
        f"| 2x in 30 min | {agg['pct_2x_in_30min']:.1f}% |",
        f"| 5x in 30 min | {agg['pct_5x_in_30min']:.1f}% |",
        f"| Median peak 30min multiplier | {agg['median_peak_30min_multiplier']}x |",
        f"| Median 24h change | {agg['median_24h_change_pct']}% |",
        f"| Still above grad price at 24h | {agg['pct_higher_at_24h']:.1f}% |",
        "",
        "## Top 20 Tokens by 30-Min Peak Multiplier",
        "",
        "| # | Mint | Grad Price | Peak 30min | Multiplier | 24h Change | Dump? |",
        "|---|------|-----------|-----------|-----------|-----------|-------|",
    ]

    sorted_results = sorted(results, key=lambda x: x.get("peak_30min_mult") or 0, reverse=True)
    for i, r in enumerate(sorted_results[:20], 1):
        mult = f"{r['peak_30min_mult']:.2f}x" if r.get("peak_30min_mult") else "N/A"
        ch24 = f"{r['change_24h_pct']:.1f}%" if r.get("change_24h_pct") is not None else "N/A"
        dump = "YES" if r.get("immediate_dump") else "no"
        report_lines.append(
            f"| {i} | `{r['mint'][:12]}...` | ${r.get('grad_price') or 0:.8f} | "
            f"${r.get('peak_30min') or 0:.8f} | {mult} | {ch24} | {dump} |"
        )

    report_path = "output/step3_report.md"
    with open(report_path, "w") as f:
        f.write("\n".join(report_lines) + "\n")
    print(f"Report saved to {report_path}")

    print("\nSTEP 3 COMPLETE.")


if __name__ == "__main__":
    main()
