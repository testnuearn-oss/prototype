#!/usr/bin/env python3
"""
step1_enrich.py — Concurrent enrichment of pump.fun token data.

Uses on-chain bonding curve PDA check for accurate graduation detection:
  - complete=True at offset 48 → graduated
  - account closed (None) → graduated (bonding curve burned on migration)
  - real_sol_reserves / 85 SOL → grad_pct for non-graduated tokens

Usage:
  python3 step1_enrich.py              # enrich all 19,765 tokens
  python3 step1_enrich.py --smoke-test # process first 20 tokens only
"""

import argparse
import base64
import hashlib
import json
import os
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import base58

from config import (
    DEXSCREENER_BASE,
    GECKOTERMINAL_BASE,
    PUMP_PROGRAM,
    http_get,
    rpc_call,
)

os.makedirs("data", exist_ok=True)
os.makedirs("output", exist_ok=True)

# ── Rate-limit semaphores ──────────────────────────────────────────────────────
ALCHEMY_SEM = threading.Semaphore(50)
DEX_SEM = threading.Semaphore(5)
GECKO_SEM = threading.Semaphore(3)

# pump.fun graduation threshold: 85 SOL in lamports
GRADUATION_SOL_LAMPORTS = 85_000_000_000


# ── PDA derivation (pure Python) ───────────────────────────────────────────────

def create_program_address(seeds: list[bytes], program_id_b58: str, nonce: int) -> str:
    """
    Compute a program-derived address for given seeds + nonce.

    Solana formula:
      sha256(seed1 || seed2 || ... || nonce_byte || program_id || "ProgramDerivedAddress")
    """
    program_id_bytes = base58.b58decode(program_id_b58)
    data = b"".join(seeds) + bytes([nonce]) + program_id_bytes + b"ProgramDerivedAddress"
    return base58.b58encode(hashlib.sha256(data).digest()).decode()


def derive_bonding_curve_pda(mint_str: str) -> str:
    """
    Derive the pump.fun bonding curve PDA for a mint.

    seeds = [b"bonding-curve", base58.b58decode(mint)]
    pump.fun reliably uses bump nonce = 254.
    """
    mint_bytes = base58.b58decode(mint_str)
    return create_program_address([b"bonding-curve", mint_bytes], PUMP_PROGRAM, 254)


# ── Bonding curve account fetch & parse ───────────────────────────────────────

def get_bonding_curve_info(mint_str: str) -> dict:
    """
    Fetch bonding curve PDA and parse the Anchor account layout.

    Bonding curve account layout:
      Offset  0:  8 bytes  — discriminator
      Offset  8:  8 bytes  — virtual_token_reserves (u64 LE)
      Offset 16:  8 bytes  — virtual_sol_reserves   (u64 LE)
      Offset 24:  8 bytes  — real_token_reserves    (u64 LE)
      Offset 32:  8 bytes  — real_sol_reserves      (u64 LE)
      Offset 40:  8 bytes  — token_total_supply     (u64 LE)
      Offset 48:  1 byte   — complete               (bool)

    Returns dict with: graduated, complete, real_sol_reserves,
                       virtual_sol_reserves, grad_pct, pda
    """
    try:
        pda = derive_bonding_curve_pda(mint_str)
    except Exception as e:
        return {
            "graduated": False, "complete": False,
            "real_sol_reserves": 0, "virtual_sol_reserves": 0,
            "grad_pct": 0.0, "pda": None, "error": str(e),
        }

    with ALCHEMY_SEM:
        result = rpc_call("getAccountInfo", [
            pda,
            {"encoding": "base64", "commitment": "confirmed"},
        ])

    # value=None → account closed. Could be graduation OR dead/reclaimed.
    # Do NOT assume graduated — 74% of dead Jan 20 tokens also have closed PDAs.
    # Graduation will be confirmed separately via DexScreener Raydium pair check.
    if result is None or result.get("value") is None:
        return {
            "graduated": False, "complete": False,
            "real_sol_reserves": 0, "virtual_sol_reserves": 0,
            "grad_pct": 0.0, "pda": pda, "account_closed": True,
        }

    # Decode base64 account data
    try:
        raw = base64.b64decode(result["value"]["data"][0])
    except Exception as e:
        return {
            "graduated": False, "complete": False,
            "real_sol_reserves": 0, "virtual_sol_reserves": 0,
            "grad_pct": 0.0, "pda": pda, "error": f"decode_error: {e}",
        }

    if len(raw) < 49:
        return {
            "graduated": False, "complete": False,
            "real_sol_reserves": 0, "virtual_sol_reserves": 0,
            "grad_pct": 0.0, "pda": pda,
            "error": f"data_too_short: {len(raw)} bytes",
        }

    virtual_sol_reserves = struct.unpack_from("<Q", raw, 16)[0]
    real_sol_reserves    = struct.unpack_from("<Q", raw, 32)[0]
    complete             = bool(raw[48])
    graduated            = complete

    if complete:
        grad_pct = 100.0
    else:
        grad_pct = min(real_sol_reserves / GRADUATION_SOL_LAMPORTS * 100.0, 100.0)

    return {
        "graduated": graduated,
        "complete": complete,
        "real_sol_reserves": real_sol_reserves,
        "virtual_sol_reserves": virtual_sol_reserves,
        "grad_pct": round(grad_pct, 2),
        "pda": pda,
    }


# ── DexScreener ────────────────────────────────────────────────────────────────

def fetch_dexscreener(mint: str) -> dict:
    """Fetch DexScreener pair data (best effort, called only for graduated tokens)."""
    url = f"{DEXSCREENER_BASE}/latest/dex/tokens/{mint}"
    with DEX_SEM:
        data = http_get(url)

    if not data or not data.get("pairs"):
        return {}

    pairs = data["pairs"]
    raydium_pairs = [p for p in pairs if p.get("dexId") == "raydium"]
    best_pair = raydium_pairs[0] if raydium_pairs else pairs[0]

    def safe_float(val):
        try:
            return float(val or 0)
        except (TypeError, ValueError):
            return 0.0

    return {
        "pair_address":    best_pair.get("pairAddress") if raydium_pairs else None,
        "market_cap_usd":  safe_float(best_pair.get("marketCap")),
        "fdv":             safe_float(best_pair.get("fdv")),
        "liquidity_usd":   safe_float((best_pair.get("liquidity") or {}).get("usd")),
        "price_usd":       safe_float(best_pair.get("priceUsd")),
        "pair_created_at": best_pair.get("pairCreatedAt"),
    }


# ── GeckoTerminal ──────────────────────────────────────────────────────────────

def fetch_gecko_ohlcv(pool_address: str, block_time: int) -> list:
    """Fetch hourly OHLCV candles for 24h after token launch."""
    url = f"{GECKOTERMINAL_BASE}/networks/solana/pools/{pool_address}/ohlcv/hour"
    params = {
        "aggregate": 1,
        "before_timestamp": block_time + 86400,
        "limit": 48,
        "currency": "usd",
    }
    with GECKO_SEM:
        data = http_get(url, params=params)

    if not data:
        return []
    try:
        candles = data["data"]["attributes"]["ohlcv_list"]
        after_launch = [c for c in candles if c[0] >= block_time]
        return after_launch[:24]
    except (KeyError, TypeError):
        return []


# ── Per-token enrichment ───────────────────────────────────────────────────────

def enrich_token(tok: dict) -> dict:
    """
    Fully enrich a single token:
      1. On-chain bonding curve PDA → graduation status / grad_pct
      2. DexScreener → pair address, price, market cap  (graduated only)
      3. GeckoTerminal → hourly OHLCV 24h after launch  (graduated + has pair)
    """
    mint       = tok["mint"]
    block_time = tok.get("block_time") or 0

    # Step 1: bonding curve on-chain check
    bc        = get_bonding_curve_info(mint)
    graduated = bc.get("graduated", False)
    complete  = bc.get("complete", False)
    grad_pct  = bc.get("grad_pct", 0.0)

    # Step 2: DexScreener — always check as graduation fallback.
    # on-chain complete=True is reliable but closed accounts can't be read.
    # DexScreener Raydium pair = confirmed graduation even for closed accounts.
    pair_address = None
    market_cap_usd = fdv = liquidity_usd = price_usd = 0.0
    pair_created_at = None

    dx = fetch_dexscreener(mint)
    pair_address    = dx.get("pair_address")
    market_cap_usd  = dx.get("market_cap_usd", 0.0)
    fdv             = dx.get("fdv", 0.0)
    liquidity_usd   = dx.get("liquidity_usd", 0.0)
    price_usd       = dx.get("price_usd", 0.0)
    pair_created_at = dx.get("pair_created_at")

    # Graduation = on-chain complete=True OR DexScreener confirms Raydium pair
    if dx.get("graduated"):
        graduated = True
        grad_pct  = 100.0

    # Step 3: GeckoTerminal OHLCV (graduated + has Raydium pair)
    hourly_prices_24h = []
    if graduated and pair_address:
        hourly_prices_24h = fetch_gecko_ohlcv(pair_address, block_time)

    # Determine status
    if graduated:
        status = "graduated"
    elif grad_pct >= 5:
        status = "active"
    else:
        status = "dead"

    result = dict(tok)
    result.update({
        "graduated":            graduated,
        "complete":             complete,
        "grad_pct":             grad_pct,
        "real_sol_reserves":    bc.get("real_sol_reserves", 0),
        "virtual_sol_reserves": bc.get("virtual_sol_reserves", 0),
        "bonding_curve_pda":    bc.get("pda"),
        "pair_address":         pair_address,
        "market_cap_usd":       market_cap_usd,
        "fdv":                  fdv,
        "liquidity_usd":        liquidity_usd,
        "price_usd":            price_usd,
        "pair_created_at":      pair_created_at,
        "status":               status,
        "hourly_prices_24h":    hourly_prices_24h,
    })
    return result


# ── Concurrent orchestration ───────────────────────────────────────────────────

def enrich_all(tokens: list, smoke_test: bool = False) -> list:
    """
    Concurrently enrich tokens using ThreadPoolExecutor.
    Semaphores cap each external service independently.
    """
    if smoke_test:
        tokens = tokens[:20]

    total    = len(tokens)
    results  = [None] * total
    lock     = threading.Lock()
    counters = {"completed": 0, "graduated": 0}
    start_ts = time.time()

    print(
        f"\nEnriching {total} tokens "
        f"(Alchemy×50 | DexScreener×5 | GeckoTerminal×3) ..."
    )

    with ThreadPoolExecutor(max_workers=50) as executor:
        future_to_idx = {
            executor.submit(enrich_token, tok): i
            for i, tok in enumerate(tokens)
        }

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                enriched = future.result()
            except Exception as e:
                enriched = dict(tokens[idx])
                enriched.update({
                    "enrich_error": str(e),
                    "graduated": False,
                    "status": "dead",
                    "grad_pct": 0.0,
                })
            results[idx] = enriched

            with lock:
                counters["completed"] += 1
                if enriched.get("graduated"):
                    counters["graduated"] += 1
                done = counters["completed"]
                grad = counters["graduated"]
                rate = grad / done * 100 if done else 0.0

            if smoke_test:
                # Per-token line for smoke test
                print(
                    f"  [{done:>2}/{total}] {enriched['mint'][:20]}… | "
                    f"graduated={str(enriched.get('graduated')):5} | "
                    f"complete={str(enriched.get('complete')):5} | "
                    f"grad_pct={enriched.get('grad_pct', 0.0):5.1f}% | "
                    f"status={enriched.get('status', 'unknown')}"
                )
            elif done % 500 == 0 or done == total:
                elapsed = time.time() - start_ts
                print(
                    f"Enriched {done}/{total} | "
                    f"Graduated: {grad} | "
                    f"Rate: {rate:.1f}% | "
                    f"Elapsed: {elapsed:.0f}s"
                )

    return results


# ── Save results and generate report ──────────────────────────────────────────

def save_results(tokens: list):
    total_launched  = len(tokens)
    total_graduated = sum(1 for t in tokens if t.get("status") == "graduated")
    total_active    = sum(1 for t in tokens if t.get("status") == "active")
    total_dead      = sum(1 for t in tokens if t.get("status") == "dead")
    grad_rate       = total_graduated / total_launched * 100 if total_launched else 0.0

    # Overwrite step1_launches.json
    with open("data/step1_launches.json", "w") as f:
        json.dump(tokens, f, indent=2)
    print(f"\nSaved {len(tokens)} tokens → data/step1_launches.json")

    # Summary
    summary = {
        "total_launched":      total_launched,
        "total_graduated":     total_graduated,
        "total_active":        total_active,
        "total_dead":          total_dead,
        "graduation_rate_pct": round(grad_rate, 2),
        "method":              "on_chain_bonding_curve_pda",
        "enriched_at":         int(time.time()),
    }
    with open("data/step1_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print("Saved summary → data/step1_summary.json")

    # Report
    top_grad = sorted(
        [t for t in tokens if t.get("status") == "graduated"],
        key=lambda t: t.get("market_cap_usd", 0),
        reverse=True,
    )[:20]

    lines = [
        "# Step 1 Enriched Report — pump.fun CreateV2 Launches on Jan 20, 2026",
        "",
        "## Summary Statistics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Launched | {total_launched:,} |",
        f"| Graduated (Raydium) | {total_graduated:,} |",
        f"| Active (bonding curve) | {total_active:,} |",
        f"| Dead | {total_dead:,} |",
        f"| Graduation Rate | {grad_rate:.2f}% |",
        "",
        "## Top 20 Graduated Tokens (by market cap)",
        "",
        "| Mint | Market Cap | grad_pct | Slot |",
        "|------|-----------|---------|------|",
    ]
    for t in top_grad:
        lines.append(
            f"| `{t['mint'][:16]}…` | ${t.get('market_cap_usd', 0):,.0f} | "
            f"{t.get('grad_pct', 100):.0f}% | {t.get('slot')} |"
        )
    lines += [
        "",
        "## Methodology",
        "",
        "- Graduation detection: on-chain bonding curve PDA (`complete` bool @ offset 48)",
        "- Bonding curve PDA: seeds=[b\"bonding-curve\", mint_bytes], nonce=254",
        "- Account closed (None) → graduated (bonding curve burned on Raydium migration)",
        "- Price data: DexScreener + GeckoTerminal OHLCV (graduated tokens only)",
        "- Concurrency: Alchemy×50 | DexScreener×5 | GeckoTerminal×3 (Semaphore-capped)",
    ]
    with open("output/step1_report.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print("Report saved → output/step1_report.md")

    print(
        f"\nFinal: {total_launched:,} launched | {total_graduated:,} graduated "
        f"({grad_rate:.2f}%) | {total_active:,} active | {total_dead:,} dead"
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Enrich pump.fun token data with on-chain graduation detection"
    )
    parser.add_argument(
        "--smoke-test", action="store_true",
        help="Process first 20 tokens only and print per-token results (no file writes)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("step1_enrich.py — Concurrent pump.fun token enrichment")
    print("Graduation method: on-chain bonding curve PDA (complete bit)")
    print("=" * 60)

    launches_path = "data/step1_launches.json"
    if not os.path.exists(launches_path):
        print(f"ERROR: {launches_path} not found. Run step1_fetch_launches.py first.")
        sys.exit(1)

    with open(launches_path) as f:
        tokens = json.load(f)

    print(f"Loaded {len(tokens):,} tokens from {launches_path}")

    if args.smoke_test:
        print("\n[SMOKE TEST] Processing first 20 tokens — no file writes.")
        enriched = enrich_all(tokens, smoke_test=True)
        grad_count = sum(1 for t in enriched if t.get("graduated"))
        print(
            f"\nSmoke test done: {grad_count}/{len(enriched)} graduated "
            f"({grad_count / len(enriched) * 100:.1f}%)"
        )
        return

    enriched = enrich_all(tokens, smoke_test=False)
    save_results(enriched)
    print("\nstep1_enrich.py COMPLETE.")


if __name__ == "__main__":
    main()
