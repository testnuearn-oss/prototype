#!/usr/bin/env python3
"""
STEP 1 — Fetch every pump.fun CreateV2 token launched on Jan 20, 2026.
Uses on-chain Alchemy RPC (getBlocks + getBlock) — pump.fun frontend API is 530 BLOCKED.

Outputs:
  data/step1_launches.json   — array of token objects
  data/step1_summary.json    — summary stats
  output/step1_report.md     — markdown summary
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from config import (
    rpc_call, http_get,
    PUMP_PROGRAM, JAN20_START_SLOT, JAN20_END_SLOT,
    DEXSCREENER_BASE, GECKOTERMINAL_BASE,
)

os.makedirs("data", exist_ok=True)
os.makedirs("output", exist_ok=True)

CHUNK_SIZE = 1000      # getBlocks max range per call
BATCH_SIZE = 15        # concurrent block fetches (conservative to avoid Alchemy 429s)
CHECKPOINT_FILE = "data/step1_checkpoint.json"
CHECKPOINT_INTERVAL = 2000  # save progress every N blocks


# ── PHASE 1: Get valid block slots ────────────────────────────────────────────

def phase1_get_slots():
    print("=" * 60)
    print("PHASE 1 — Collecting valid block slots for Jan 20, 2026")
    print("=" * 60)

    all_slots = []
    start = JAN20_START_SLOT
    end = JAN20_END_SLOT
    total_range = end - start
    chunks_done = 0
    total_chunks = (total_range + CHUNK_SIZE - 1) // CHUNK_SIZE

    current = start
    while current <= end:
        chunk_end = min(current + CHUNK_SIZE - 1, end)
        slots = rpc_call("getBlocks", [current, chunk_end])
        if slots is None:
            print(f"  WARNING: getBlocks({current}, {chunk_end}) returned None, skipping")
        elif isinstance(slots, list):
            all_slots.extend(slots)

        chunks_done += 1
        if chunks_done % 20 == 0 or chunks_done == total_chunks:
            pct = chunks_done / total_chunks * 100
            print(f"  Phase 1: {chunks_done}/{total_chunks} chunks ({pct:.1f}%) | "
                  f"Slots found so far: {len(all_slots)}")

        current = chunk_end + 1
        time.sleep(0.05)  # gentle rate limit

    print(f"\nPhase 1: Found {len(all_slots)} valid blocks in Jan 20 range")
    return all_slots


# ── PHASE 2: Scan blocks for CreateV2 transactions ───────────────────────────

def fetch_block(slot):
    """Fetch a single block with transactionDetails='accounts'."""
    result = rpc_call("getBlock", [
        slot,
        {
            "encoding": "json",
            "transactionDetails": "accounts",
            "maxSupportedTransactionVersion": 0,
            "rewards": False,
        }
    ])
    return slot, result


def extract_createv2_from_block(slot, block):
    """
    Filter block transactions for CreateV2 fingerprint:
    1. PUMP_PROGRAM in accountKeys
    2. At least one account ends with 'pump'
    3. That 'pump' account has preBalance=0 and postBalance>0
    Returns list of token dicts.
    """
    if not block:
        return []

    tokens = []
    transactions = block.get("transactions") or []
    block_time = block.get("blockTime")

    for tx in transactions:
        try:
            tx_data = tx.get("transaction") or {}
            meta = tx.get("meta") or {}

            # accountKeys can be list of strings or list of objects
            raw_keys = tx_data.get("accountKeys") or []
            account_keys = []
            for k in raw_keys:
                if isinstance(k, str):
                    account_keys.append(k)
                elif isinstance(k, dict):
                    account_keys.append(k.get("pubkey", ""))

            # Check fingerprint condition 1: PUMP_PROGRAM in accounts
            if PUMP_PROGRAM not in account_keys:
                continue

            # Check fingerprint condition 2: account ending in 'pump'
            pump_accounts = [a for a in account_keys if a.endswith("pump")]
            if not pump_accounts:
                continue

            # Check fingerprint condition 3: pump account has preBalance=0, postBalance>0
            pre_balances = meta.get("preBalances") or []
            post_balances = meta.get("postBalances") or []

            found_mint = None
            for pump_acct in pump_accounts:
                if pump_acct not in account_keys:
                    continue
                idx = account_keys.index(pump_acct)
                pre_bal = pre_balances[idx] if idx < len(pre_balances) else -1
                post_bal = post_balances[idx] if idx < len(post_balances) else -1
                if pre_bal == 0 and post_bal > 0:
                    found_mint = pump_acct
                    break

            if not found_mint:
                continue

            # Extract fields
            creator = account_keys[0] if account_keys else None
            sigs = tx_data.get("signatures") or []
            signature = sigs[0] if sigs else None

            tokens.append({
                "mint": found_mint,
                "creator": creator,
                "signature": signature,
                "slot": slot,
                "block_time": block_time,
            })

        except Exception as e:
            continue

    return tokens


def phase2_scan_blocks(all_slots):
    print("\n" + "=" * 60)
    print("PHASE 2 — Scanning blocks for CreateV2 transactions")
    print("=" * 60)

    total_slots = len(all_slots)
    found_tokens = {}   # mint -> token dict (dedup by mint)
    scanned = 0
    start_time = time.time()

    # Resume from checkpoint if exists
    resume_from = 0
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE) as f:
                cp = json.load(f)
            resume_from = cp.get("scanned", 0)
            for tok in cp.get("tokens", []):
                found_tokens[tok["mint"]] = tok
            print(f"  Resuming from checkpoint: {resume_from}/{total_slots} blocks already scanned, "
                  f"{len(found_tokens)} tokens found so far")
        except Exception:
            resume_from = 0

    # Process in batches of BATCH_SIZE concurrent requests
    for batch_start in range(resume_from, total_slots, BATCH_SIZE):
        batch = all_slots[batch_start:batch_start + BATCH_SIZE]

        with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
            futures = {executor.submit(fetch_block, slot): slot for slot in batch}
            for future in as_completed(futures):
                slot, block = future.result()
                tokens_in_block = extract_createv2_from_block(slot, block)
                for tok in tokens_in_block:
                    mint = tok["mint"]
                    if mint not in found_tokens:
                        found_tokens[mint] = tok

        scanned = batch_start + len(batch)

        # Checkpoint every CHECKPOINT_INTERVAL blocks
        if scanned % CHECKPOINT_INTERVAL < BATCH_SIZE or scanned >= total_slots:
            with open(CHECKPOINT_FILE, "w") as f:
                json.dump({"scanned": scanned, "total": total_slots,
                           "tokens": list(found_tokens.values())}, f)

        if scanned % 5000 < BATCH_SIZE or scanned >= total_slots:
            elapsed = time.time() - start_time
            rate = scanned / elapsed if elapsed > 0 else 0
            remaining = (total_slots - scanned) / rate if rate > 0 else 0
            print(f"  Scanned {scanned}/{total_slots} blocks | "
                  f"Found {len(found_tokens)} tokens | "
                  f"Rate: {rate:.1f} blocks/s | "
                  f"ETA: {remaining:.0f}s")

        time.sleep(0.05)  # gentle rate limit between batches

    print(f"\nPhase 2: Scanned {scanned} blocks, found {len(found_tokens)} unique CreateV2 tokens")
    # Clean up checkpoint
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
    return list(found_tokens.values())


# ── PHASE 3: Enrich with DexScreener ─────────────────────────────────────────

def fetch_dexscreener(mint):
    url = f"{DEXSCREENER_BASE}/latest/dex/tokens/{mint}"
    data = http_get(url)
    if not data or "pairs" not in data or not data["pairs"]:
        return {}

    pairs = data["pairs"]
    raydium_pairs = [p for p in pairs if p.get("dexId") == "raydium"]
    graduated = len(raydium_pairs) > 0
    best_pair = raydium_pairs[0] if raydium_pairs else pairs[0]

    try:
        fdv = float(best_pair.get("fdv") or 0)
    except (TypeError, ValueError):
        fdv = 0.0

    try:
        market_cap_usd = float(best_pair.get("marketCap") or 0)
    except (TypeError, ValueError):
        market_cap_usd = 0.0

    try:
        liquidity_usd = float((best_pair.get("liquidity") or {}).get("usd") or 0)
    except (TypeError, ValueError):
        liquidity_usd = 0.0

    try:
        price_usd = float(best_pair.get("priceUsd") or 0)
    except (TypeError, ValueError):
        price_usd = 0.0

    pair_created_at = best_pair.get("pairCreatedAt")  # ms timestamp
    pair_address = best_pair.get("pairAddress") if graduated else None

    # grad_pct
    if fdv > 0:
        grad_pct = min(fdv / 69000 * 100, 100)
    elif market_cap_usd > 0:
        grad_pct = min(market_cap_usd / 69000 * 100, 100)
    else:
        grad_pct = 0.0

    # Status
    if graduated:
        status = "graduated"
    elif fdv > 1000 or liquidity_usd > 100:
        status = "active"
    else:
        status = "dead"

    return {
        "graduated": graduated,
        "pair_address": pair_address,
        "market_cap_usd": market_cap_usd,
        "fdv": fdv,
        "liquidity_usd": liquidity_usd,
        "price_usd": price_usd,
        "pair_created_at": pair_created_at,
        "grad_pct": grad_pct,
        "status": status,
    }


def phase3_enrich_dexscreener(tokens):
    print("\n" + "=" * 60)
    print("PHASE 3 — Enriching with DexScreener data")
    print("=" * 60)

    enriched = []
    total = len(tokens)

    for i, tok in enumerate(tokens):
        dx = fetch_dexscreener(tok["mint"])
        tok.update({
            "graduated": dx.get("graduated", False),
            "pair_address": dx.get("pair_address"),
            "market_cap_usd": dx.get("market_cap_usd", 0),
            "fdv": dx.get("fdv", 0),
            "liquidity_usd": dx.get("liquidity_usd", 0),
            "price_usd": dx.get("price_usd", 0),
            "pair_created_at": dx.get("pair_created_at"),
            "grad_pct": dx.get("grad_pct", 0),
            "status": dx.get("status", "dead"),
            "hourly_prices_24h": [],
        })
        enriched.append(tok)

        if (i + 1) % 50 == 0 or (i + 1) == total:
            print(f"  DexScreener: {i+1}/{total} tokens enriched")

        time.sleep(0.4)  # 2.5 req/sec limit

    return enriched


# ── PHASE 4: Fetch hourly price data for graduated tokens ─────────────────────

def fetch_gecko_ohlcv(pool_address, timeframe, before_timestamp, limit):
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


def phase4_fetch_prices(tokens):
    print("\n" + "=" * 60)
    print("PHASE 4 — Fetching hourly price data for graduated tokens")
    print("=" * 60)

    graduated = [t for t in tokens if t.get("status") == "graduated" and t.get("pair_address")]
    print(f"  Fetching OHLCV for {len(graduated)} graduated tokens...")

    for i, tok in enumerate(graduated):
        pair_address = tok["pair_address"]
        block_time = tok.get("block_time") or 0

        candles = fetch_gecko_ohlcv(
            pair_address, "hour",
            before_timestamp=block_time + 86400,
            limit=48,
        )
        # Trim to first 24 candles after launch
        after_launch = [c for c in candles if c[0] >= block_time]
        tok["hourly_prices_24h"] = after_launch[:24]

        if (i + 1) % 20 == 0 or (i + 1) == len(graduated):
            print(f"  GeckoTerminal: {i+1}/{len(graduated)} tokens fetched")

        time.sleep(0.5)

    return tokens


# ── PHASE 5: Save and report ──────────────────────────────────────────────────

def phase5_save_report(tokens):
    print("\n" + "=" * 60)
    print("PHASE 5 — Saving results and generating report")
    print("=" * 60)

    total_launched = len(tokens)
    total_graduated = sum(1 for t in tokens if t.get("status") == "graduated")
    total_active = sum(1 for t in tokens if t.get("status") == "active")
    total_dead = sum(1 for t in tokens if t.get("status") == "dead")
    grad_rate = total_graduated / total_launched * 100 if total_launched else 0

    # Save launches
    with open("data/step1_launches.json", "w") as f:
        json.dump(tokens, f, indent=2)
    print(f"Saved {len(tokens)} tokens -> data/step1_launches.json")

    # Save summary
    summary = {
        "total_launched": total_launched,
        "total_graduated": total_graduated,
        "total_active": total_active,
        "total_dead": total_dead,
        "graduation_rate_pct": round(grad_rate, 2),
        "scan_completed_at": int(time.time()),
    }
    with open("data/step1_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Saved summary -> data/step1_summary.json")

    # Top 20 graduated by peak market cap
    graduated_tokens = sorted(
        [t for t in tokens if t.get("status") == "graduated"],
        key=lambda t: t.get("market_cap_usd", 0),
        reverse=True,
    )[:20]

    report_lines = [
        "# Step 1 Report — pump.fun CreateV2 Launches on Jan 20, 2026",
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
        "| Mint | Creator | Market Cap | FDV | Slot |",
        "|------|---------|-----------|-----|------|",
    ]

    for t in graduated_tokens:
        report_lines.append(
            f"| `{t['mint'][:12]}...` | `{(t.get('creator') or '')[:12]}...` | "
            f"${t.get('market_cap_usd', 0):,.0f} | ${t.get('fdv', 0):,.0f} | {t.get('slot')} |"
        )

    report_lines += [
        "",
        "## Methodology",
        "",
        "- Token discovery: on-chain Alchemy RPC (getBlocks + getBlock)",
        "- CreateV2 fingerprint: PUMP_PROGRAM in accounts + 'pump'-suffix mint with preBalance=0",
        "- Graduation detection: DexScreener Raydium pair presence",
        "- Price data: GeckoTerminal OHLCV (free tier)",
        "- Note: pump.fun frontend API (530 blocked) was NOT used",
    ]

    with open("output/step1_report.md", "w") as f:
        f.write("\n".join(report_lines) + "\n")
    print("Report saved -> output/step1_report.md")

    print(f"\nSummary: {total_launched} launched | {total_graduated} graduated "
          f"({grad_rate:.1f}%) | {total_active} active | {total_dead} dead")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("STEP 1 — pump.fun CreateV2 token discovery (Jan 20, 2026)")
    print("Using on-chain Alchemy RPC — pump.fun frontend is BLOCKED")
    print("=" * 60)
    print()

    # Phase 1: collect valid slot numbers
    all_slots = phase1_get_slots()

    if not all_slots:
        print("ERROR: No valid slots found in range. Check Alchemy RPC.")
        sys.exit(1)

    # Phase 2: scan blocks for CreateV2 transactions
    tokens = phase2_scan_blocks(all_slots)

    if not tokens:
        print("WARNING: No CreateV2 tokens found. Check block scan logic.")
        json.dump([], open("data/step1_launches.json", "w"), indent=2)
        json.dump({}, open("data/step1_summary.json", "w"), indent=2)
        return

    # Phase 3: enrich with DexScreener
    tokens = phase3_enrich_dexscreener(tokens)

    # Phase 4: fetch hourly price data for graduated tokens
    tokens = phase4_fetch_prices(tokens)

    # Phase 5: save and report
    phase5_save_report(tokens)

    print("\nSTEP 1 COMPLETE.")


if __name__ == "__main__":
    main()
