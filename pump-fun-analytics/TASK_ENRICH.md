# Task: Rewrite Enrichment — Concurrent Phase 3/4 + Fix Graduation Detection

## Context
- step1_launches.json exists with 19,765 tokens (mint, creator, slot, block_time)
- Phase 3/4 ran sequentially (0.4s sleep per call) → too slow
- Graduation rate came out at 0.01% (2/19765) — WRONG, expected ~1-2%
- Root cause: DexScreener only shows CURRENTLY active pairs. Tokens that graduated
  4 weeks ago but died since then have no active Raydium pair anymore.

## Fix graduation detection
DexScreener is insufficient for historical graduation. Use on-chain approach:
- The pump.fun bonding curve account has a `complete` boolean field at a known offset
- When complete=true, the token graduated to Raydium (even if the pool is now dead)
- Bonding curve PDA = findProgramAddressSync(["bonding-curve", mint_bytes], PUMP_PROGRAM)
- Fetch the account data via Alchemy getAccountInfo
- Parse the `complete` field from the binary data

Bonding curve account layout (Anchor, BondingCurve discriminator):
  Offset 0:  8 bytes  — discriminator
  Offset 8:  8 bytes  — virtual_token_reserves (u64, little-endian)
  Offset 16: 8 bytes  — virtual_sol_reserves (u64, little-endian)
  Offset 24: 8 bytes  — real_token_reserves (u64, little-endian)
  Offset 32: 8 bytes  — real_sol_reserves (u64, little-endian)
  Offset 40: 8 bytes  — token_total_supply (u64, little-endian)
  Offset 48: 1 byte   — complete (bool)

Graduation percentage (for non-complete tokens):
  real_sol_reserves / 85_000_000_000 * 100  (85 SOL = graduation threshold in lamports)
  OR: virtual_sol_reserves gives bonding curve progress

## New file: step1_enrich.py
Creates an enrichment-only script that:
1. Loads data/step1_launches.json
2. Re-enriches all tokens concurrently
3. Overwrites step1_launches.json with enriched data
4. Regenerates step1_summary.json and step1_report.md

## Implementation

### Rate limits
- Alchemy RPC: 50 concurrent max (getAccountInfo calls)
- DexScreener: 5 concurrent max (secondary, for price/market cap data)
- GeckoTerminal: 3 concurrent max (OHLCV data)
- Use threading.Semaphore to cap each

### Concurrent enrichment function
```python
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

ALCHEMY_SEM = threading.Semaphore(50)
DEX_SEM = threading.Semaphore(5)
GECKO_SEM = threading.Semaphore(3)
```

### Per-token enrichment steps:
1. Derive bonding curve PDA from mint (use base58 + hashlib + ed25519 or use solders/solana-py if available, else use pre-computed approach via RPC)
2. getAccountInfo(bonding_curve_pda) → parse binary → extract complete, real_sol_reserves, virtual_sol_reserves
3. graduated = (complete == True) OR (account doesn't exist = migrated and closed)
4. grad_pct = min(real_sol_reserves / 85_000_000_000 * 100, 100) if not complete else 100
5. If graduated: call DexScreener to get current pair address, price, market cap (best effort)
6. If graduated + has pair: call GeckoTerminal for hourly OHLCV (24h after launch)

### PDA derivation WITHOUT solana-py (pure Python):
```python
import hashlib, base58

def find_program_address(seeds, program_id):
    """Pure Python PDA derivation"""
    program_id_bytes = base58.b58decode(program_id)
    for nonce in range(255, -1, -1):
        seed_bytes = b"".join(seeds) + bytes([nonce]) + program_id_bytes + b"ProgramDerivedAddress"
        hash_bytes = hashlib.sha256(seed_bytes).digest()
        # Check if on curve (simplified - just try and verify via RPC)
        try:
            return base58.b58encode(hash_bytes).decode(), nonce
        except:
            continue
    raise ValueError("Could not find PDA")
```

Actually, the reliable way: compute PDA via Alchemy's getProgramDerivedAddress if available,
OR use the known fact that pump.fun bonding curve PDA can be computed as:
  seeds = [b"bonding-curve", base58.b58decode(mint_address)]
  Use create_program_address with nonce=254 (pump.fun always uses 254)

If PDA derivation is complex, fallback: use getTokenLargestAccounts on the mint to find
the bonding curve token account, then find the bonding curve account via the owner.

### Simplest reliable fallback for graduation:
If PDA derivation is not working, use this multi-signal approach:
  graduated = any of:
    - DexScreener has raydium pair (currently active)
    - Token has more than 1 SOL in any associated wallet (indicates real trading happened)
    - getSignaturesForAddress(mint, limit=1) shows activity after block_time + 3600
      (tokens that graduate show Raydium activity; dead tokens go silent)

### Smoke test mode
Add --smoke-test flag: processes only first 20 tokens, prints per-token results, exits.
Run for max 2 minutes.

### Progress reporting
Print every 500 tokens: "Enriched X/Y | Graduated: Z | Rate: N.N%"

### Output
Same format as step1 enrichment — update step1_launches.json in place.
Also write step1_summary.json and step1_report.md.

## Install check
May need: pip install base58 solders
Check first, install if missing.
