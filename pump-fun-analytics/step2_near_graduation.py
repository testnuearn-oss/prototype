#!/usr/bin/env python3
"""
STEP 2 — Among non-graduated tokens, calculate how close they got to graduation.

Input:  data/step1_launches.json
Output:
  data/step2_near_grad.json   — near-graduation tokens with grad_pct and bucket distribution
  output/step2_report.md      — markdown bucket table + top-20 list
"""

import json
import os
import sys
import time

from config import http_get, DEXSCREENER_BASE

os.makedirs("data", exist_ok=True)
os.makedirs("output", exist_ok=True)


def load_launches():
    path = "data/step1_launches.json"
    if not os.path.exists(path):
        print(f"ERROR: {path} not found. Run step1_fetch_launches.py first.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def fetch_dexscreener(mint):
    url = f"{DEXSCREENER_BASE}/latest/dex/tokens/{mint}"
    data = http_get(url)
    if not data or "pairs" not in data or not data["pairs"]:
        return None
    pairs = data["pairs"]
    raydium = [p for p in pairs if p.get("dexId") == "raydium"]
    best = raydium[0] if raydium else pairs[0]

    try:
        fdv = float(best.get("fdv") or 0)
    except (TypeError, ValueError):
        fdv = 0.0

    return {
        "graduated": len(raydium) > 0,
        "pair_address": best.get("pairAddress"),
        "fdv": fdv,
        "market_cap_usd": float(best.get("marketCap") or 0),
        "liquidity_usd": float((best.get("liquidity") or {}).get("usd") or 0),
        "price_usd": float(best.get("priceUsd") or 0),
    }


def compute_buckets(tokens):
    """Compute graduation percentage bucket distribution."""
    buckets = {
        "0-10": 0,
        "10-25": 0,
        "25-50": 0,
        "50-75": 0,
        "75-90": 0,
        "90-99": 0,
        "100": 0,
    }
    for t in tokens:
        pct = t.get("grad_pct", 0) or 0
        if pct >= 100:
            buckets["100"] += 1
        elif pct >= 90:
            buckets["90-99"] += 1
        elif pct >= 75:
            buckets["75-90"] += 1
        elif pct >= 50:
            buckets["50-75"] += 1
        elif pct >= 25:
            buckets["25-50"] += 1
        elif pct >= 10:
            buckets["10-25"] += 1
        else:
            buckets["0-10"] += 1
    return buckets


def main():
    print("=" * 60)
    print("STEP 2 — Near-graduation analysis")
    print("=" * 60)

    all_launches = load_launches()
    non_graduated = [t for t in all_launches if t.get("status") != "graduated"]
    print(f"Loaded {len(all_launches)} total tokens, {len(non_graduated)} non-graduated.")

    near_grad = [t for t in non_graduated if (t.get("grad_pct") or 0) >= 50]
    print(f"Near-graduation tokens (>=50% grad): {len(near_grad)}")

    # Refresh DexScreener for near-grad tokens with low confidence data
    print("Refreshing DexScreener data for near-grad tokens with low/missing FDV...")
    refreshed = 0
    for i, token in enumerate(near_grad):
        if not token.get("fdv") or token.get("fdv", 0) < 100:
            data = fetch_dexscreener(token["mint"])
            if data:
                token.update(data)
                if data.get("fdv", 0) > 0:
                    token["grad_pct"] = min(data["fdv"] / 69000 * 100, 100)
                refreshed += 1
            time.sleep(0.4)

        if (i + 1) % 20 == 0:
            print(f"  Refreshed {i+1}/{len(near_grad)} near-grad tokens...")

    print(f"Refreshed DexScreener data for {refreshed} tokens.")

    buckets = compute_buckets(non_graduated)

    result = {
        "total_non_graduated": len(non_graduated),
        "total_near_grad_50plus": len(near_grad),
        "bucket_distribution": buckets,
        "near_grad_tokens": sorted(near_grad, key=lambda x: x.get("grad_pct", 0), reverse=True),
    }

    out_path = "data/step2_near_grad.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Saved {out_path}")

    # Report
    report_lines = [
        "# Step 2 Report — Near-Graduation Analysis (Jan 20, 2026)",
        "",
        "## Overview",
        "",
        f"- **Total non-graduated**: {len(non_graduated):,}",
        f"- **Near-graduation (>=50%)**: {len(near_grad):,}",
        "",
        "## Graduation % Distribution",
        "",
        "| Bucket | Count | % of Non-Grads |",
        "|--------|-------|---------------|",
    ]
    total_ng = len(non_graduated)
    for bucket, count in buckets.items():
        share = count / total_ng * 100 if total_ng else 0
        report_lines.append(f"| {bucket}% | {count:,} | {share:.1f}% |")

    report_lines += [
        "",
        "## Top 20 Closest to Graduation",
        "",
        "| # | Mint | Grad% | FDV | Status |",
        "|---|------|-------|-----|--------|",
    ]

    sorted_near = sorted(near_grad, key=lambda x: x.get("grad_pct", 0), reverse=True)
    for i, t in enumerate(sorted_near[:20], 1):
        report_lines.append(
            f"| {i} | `{t['mint'][:12]}...` | {t.get('grad_pct', 0):.1f}% | "
            f"${t.get('fdv', 0):,.0f} | {t.get('status', 'unknown')} |"
        )

    report_path = "output/step2_report.md"
    with open(report_path, "w") as f:
        f.write("\n".join(report_lines) + "\n")
    print(f"Report saved to {report_path}")

    print("\nSTEP 2 COMPLETE.")


if __name__ == "__main__":
    main()
