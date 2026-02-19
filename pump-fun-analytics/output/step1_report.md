# Step 1 Enriched Report — pump.fun CreateV2 Launches on Jan 20, 2026

## Summary Statistics

| Metric | Value |
|--------|-------|
| Total Launched | 19,765 |
| Graduated (Raydium) | 37 |
| Active (bonding curve) | 4 |
| Dead | 19,724 |
| Graduation Rate | 0.19% |

## Top 20 Graduated Tokens (by market cap)

| Mint | Market Cap | grad_pct | Slot |
|------|-----------|---------|------|
| `jpxfcZEzCX5h1usb…` | $18,019 | 100% | 394688443 |
| `BLXbbr3QNHntRQrQ…` | $6,810 | 100% | 394709439 |
| `J8REZaR7ZwKbhPTG…` | $6,709 | 100% | 394744744 |
| `C3PoSr3d4PVis4Mm…` | $3,806 | 100% | 394689116 |
| `HGT3rH6KKy9w765z…` | $3,012 | 100% | 394708179 |
| `HD9B5L6BZfFSWFBp…` | $2,305 | 100% | 394833231 |
| `9vrm9a2nRTPHWTzZ…` | $2,174 | 100% | 394703279 |
| `J4vxMBX4u9Hcwbke…` | $2,144 | 100% | 394810977 |
| `5AqzXHDa8ZypNoXN…` | $1,963 | 100% | 394637239 |
| `Bnpk2YijexDU5dhg…` | $1,796 | 100% | 394659003 |
| `mkRQSXX6FdnBuFSG…` | $1,730 | 100% | 394765752 |
| `7Wn3pSw2qdb1LPXS…` | $1,555 | 100% | 394847839 |
| `G8J5Xdz5qPKjNrDY…` | $0 | 100% | 394641707 |
| `JDptj2BqNXqU1bgq…` | $0 | 100% | 394644692 |
| `G2g49vYEaENhSPra…` | $0 | 100% | 394647464 |
| `DgPK1PYHPazw6gAS…` | $0 | 100% | 394685277 |
| `BvRMmCBPmRjRaYDK…` | $0 | 100% | 394689988 |
| `YtQtDq1zofzbyBS2…` | $0 | 100% | 394720199 |
| `BCNue5xwfm3prevL…` | $0 | 100% | 394722439 |
| `D6AwMs3VueyJtnxT…` | $0 | 100% | 394724030 |

## Methodology

- Graduation detection: on-chain bonding curve PDA (`complete` bool @ offset 48)
- Bonding curve PDA: seeds=[b"bonding-curve", mint_bytes], nonce=254
- Account closed (None) → graduated (bonding curve burned on Raydium migration)
- Price data: DexScreener + GeckoTerminal OHLCV (graduated tokens only)
- Concurrency: Alchemy×50 | DexScreener×5 | GeckoTerminal×3 (Semaphore-capped)
