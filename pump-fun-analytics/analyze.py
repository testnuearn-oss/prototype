#!/usr/bin/env python3
"""
Final analysis — answers two investment questions using all three data sets.

Q1: Is buying at 90%+ bonding curve progression profitable?
Q2: Post-graduation — which exit strategy wins?

Input:
  data/step1_launches.json
  data/step2_near_grad.json
  data/step3_price_action.json

Output:
  output/analysis_report.md
"""

import json
import os
import sys
from statistics import mean, median

os.makedirs("output", exist_ok=True)


def load_json(filename):
    path = os.path.join("data", filename)
    if not os.path.exists(path):
        print(f"WARNING: {path} not found.")
        return None
    with open(path) as f:
        return json.load(f)


def safe_mean(lst):
    return mean(lst) if lst else None


def safe_median(lst):
    return median(lst) if lst else None


# ── Q1: Pre-graduation buy strategy (90%+) ───────────────────────────────────

def analyze_q1(launches, near_grad_data, price_data):
    graduated = [t for t in launches if t.get("status") == "graduated"]
    near_grad_tokens = near_grad_data.get("near_grad_tokens", []) if near_grad_data else []
    price_tokens = price_data.get("tokens", []) if price_data else []

    # Build mint -> price_action map
    price_map = {r["mint"]: r for r in price_tokens}

    tokens_90plus = [t for t in near_grad_tokens if (t.get("grad_pct") or 0) >= 90]
    tokens_90plus_graduated = [t for t in tokens_90plus if t.get("status") == "graduated"]

    grad_rate_90plus = len(tokens_90plus_graduated) / len(tokens_90plus) * 100 if tokens_90plus else 0

    # Performance for graduated 90plus tokens
    perf_90plus = [price_map[t["mint"]] for t in tokens_90plus_graduated if t["mint"] in price_map]

    avg_peak_mult = safe_mean([r["peak_30min_mult"] for r in perf_90plus if r.get("peak_30min_mult")])
    avg_24h_change = safe_mean([r["change_24h_pct"] for r in perf_90plus if r.get("change_24h_pct") is not None])

    # Expected value: P(graduate) * avg_gain - P(die) * 1.0 (full loss)
    p_profit = grad_rate_90plus / 100
    p_loss = 1 - p_profit
    gain_mult = avg_peak_mult if avg_peak_mult else 1.5
    ev_90 = p_profit * (gain_mult - 1.0) - p_loss * 1.0  # net in units of 1x stake

    return {
        "tokens_90plus": len(tokens_90plus),
        "tokens_90plus_graduated": len(tokens_90plus_graduated),
        "grad_rate_90plus_pct": round(grad_rate_90plus, 2),
        "avg_peak_30min_mult": round(avg_peak_mult, 4) if avg_peak_mult else None,
        "avg_24h_change_pct": round(avg_24h_change, 2) if avg_24h_change is not None else None,
        "p_profit": round(p_profit, 4),
        "p_loss": round(p_loss, 4),
        "ev_net_multiplier": round(ev_90, 4),
        "verdict": "BUY" if ev_90 > 0.1 else "PASS" if ev_90 > -0.1 else "AVOID",
    }


# ── Q2: Post-graduation strategies ───────────────────────────────────────────

def strategy_a_quick_flip(price_tokens):
    """Buy at grad, sell at 15min."""
    returns = []
    for r in price_tokens:
        grad_price = r.get("grad_price")
        price_at_15min = r.get("price_at_15min")
        if grad_price and price_at_15min and grad_price > 0:
            ret = (price_at_15min - grad_price) / grad_price * 100
            returns.append(ret)
    return returns


def strategy_b_ladder_sell(price_tokens):
    """50% at 2x, 25% at 5x, 25% hold with -60% stop loss."""
    returns = []
    for r in price_tokens:
        grad_price = r.get("grad_price")
        if not grad_price or grad_price <= 0:
            continue
        peak_mult = r.get("peak_30min_mult") or 1.0

        # Tranche 1: 50% — sell at 2x if reached, else at peak
        ret_t1 = (min(peak_mult, 2.0) - 1.0)
        # Tranche 2: 25% — sell at 5x if reached, else at peak
        ret_t2 = (min(peak_mult, 5.0) - 1.0)
        # Tranche 3: 25% — hold with -60% stop
        change_24h_raw = r.get("change_24h_pct")
        if change_24h_raw is not None:
            ret_t3 = max(change_24h_raw / 100, -0.60)
        else:
            ret_t3 = -0.60  # assume stop triggered if no data

        blended = (0.50 * ret_t1 + 0.25 * ret_t2 + 0.25 * ret_t3) * 100
        returns.append(blended)
    return returns


def strategy_c_hold_24h(price_tokens):
    """Buy at grad, sell exactly 24h later."""
    returns = []
    for r in price_tokens:
        ch = r.get("change_24h_pct")
        if ch is not None:
            returns.append(ch)
    return returns


def strategy_d_momentum_filter(price_tokens):
    """Only buy if price UP >20% in first 5 min post-grad, then hold 24h."""
    returns = []
    for r in price_tokens:
        grad_price = r.get("grad_price")
        if not grad_price or grad_price <= 0:
            continue
        candles = r.get("post_30min_candles", [])
        if len(candles) >= 5:
            price_at_5min = candles[4][4]  # close of 5th candle
            pct_5min = (price_at_5min - grad_price) / grad_price * 100
            if pct_5min > 20:
                ch = r.get("change_24h_pct")
                if ch is not None:
                    returns.append(ch)
    return returns


def compute_strategy_stats(returns, label):
    if not returns:
        return {
            "label": label, "n": 0,
            "win_rate": None, "avg_return": None, "median_return": None,
            "expected_value": None, "max_drawdown": None,
            "verdict": "INSUFFICIENT DATA",
        }
    wins = [r for r in returns if r > 0]
    win_rate = len(wins) / len(returns) * 100
    avg_ret = mean(returns)
    med_ret = median(returns)
    max_dd = min(returns)
    verdict = "BUY" if avg_ret > 10 else "PASS" if avg_ret > 0 else "AVOID"
    return {
        "label": label,
        "n": len(returns),
        "win_rate": round(win_rate, 2),
        "avg_return": round(avg_ret, 2),
        "median_return": round(med_ret, 2),
        "expected_value": round(avg_ret, 2),
        "max_drawdown": round(max_dd, 2),
        "verdict": verdict,
    }


def analyze_q2(price_data):
    price_tokens = price_data.get("tokens", []) if price_data else []

    strat_a = strategy_a_quick_flip(price_tokens)
    strat_b = strategy_b_ladder_sell(price_tokens)
    strat_c = strategy_c_hold_24h(price_tokens)
    strat_d = strategy_d_momentum_filter(price_tokens)

    strategies = [
        compute_strategy_stats(strat_a, "Strategy A: Quick Flip (buy@grad, sell@15min)"),
        compute_strategy_stats(strat_b, "Strategy B: Ladder Sell (50%@2x, 25%@5x, 25% stop-60%)"),
        compute_strategy_stats(strat_c, "Strategy C: Hold 24h (buy@grad, sell@24h)"),
        compute_strategy_stats(strat_d, "Strategy D: Momentum Filter (>20% in 5min, hold 24h)"),
    ]

    ranked = sorted(
        [s for s in strategies if s["expected_value"] is not None],
        key=lambda s: s["expected_value"],
        reverse=True,
    )

    return strategies, ranked


# ── Report writer ─────────────────────────────────────────────────────────────

def write_report(q1, strategies, ranked, launches):
    total = len(launches)
    graduated_count = sum(1 for t in launches if t.get("status") == "graduated")
    grad_rate = graduated_count / total * 100 if total else 0

    lines = [
        "# Pump.fun Token Analytics — Investment Strategy Analysis",
        "## Jan 20, 2026 — On-Chain Data",
        "",
        f"**Total tokens launched:** {total:,}",
        f"**Graduated:** {graduated_count:,} ({grad_rate:.1f}%)",
        "",
        "---",
        "",
        "## Q1: Is buying at 90%+ graduation threshold profitable?",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Tokens at 90-99% grad progress | {q1['tokens_90plus']:,} |",
        f"| Of those that eventually graduated | {q1['tokens_90plus_graduated']:,} ({q1['grad_rate_90plus_pct']:.1f}%) |",
        f"| Avg peak 30min multiplier (post-grad) | {q1['avg_peak_30min_mult']}x |" if q1['avg_peak_30min_mult'] else "| Avg peak 30min multiplier | N/A |",
        f"| Avg 24h change (post-grad) | {q1['avg_24h_change_pct']}% |" if q1['avg_24h_change_pct'] is not None else "| Avg 24h change | N/A |",
        f"| P(graduate) | {q1['p_profit']:.2%} |",
        f"| P(die before grad) | {q1['p_loss']:.2%} |",
        f"| Expected Value | {q1['ev_net_multiplier']:+.2f}x net |",
        "",
        "### Conclusion",
        "",
    ]

    ev = q1["ev_net_multiplier"]
    verdict = q1["verdict"]
    if verdict == "BUY":
        lines.append(
            f"> **{verdict}** — Expected value is positive ({ev:+.2f}x net). "
            f"Buying at 90%+ completion is profitable on expectation with "
            f"{q1['grad_rate_90plus_pct']:.0f}% graduation rate."
        )
    elif verdict == "PASS":
        lines.append(
            f"> **{verdict}** — Expected value near zero ({ev:+.2f}x net). "
            "Risk/reward is marginal. Only pursue with very small position sizes."
        )
    else:
        lines.append(
            f"> **{verdict}** — Expected value is negative ({ev:+.2f}x net). "
            "Losses from failed graduations outweigh gains from successful ones."
        )

    lines += [
        "",
        "---",
        "",
        "## Q2: Post-Graduation Strategy Comparison",
        "",
        "| Strategy | N | Win Rate | Avg Return | Median Return | EV | Max Drawdown | Verdict |",
        "|----------|---|---------|-----------|--------------|-----|-------------|---------|",
    ]

    for s in strategies:
        if s["n"] == 0:
            lines.append(
                f"| {s['label']} | 0 | N/A | N/A | N/A | N/A | N/A | **INSUFFICIENT DATA** |"
            )
        else:
            lines.append(
                f"| {s['label']} | {s['n']} | {s['win_rate']:.1f}% | "
                f"{s['avg_return']:.1f}% | {s['median_return']:.1f}% | "
                f"{s['expected_value']:.1f}% | {s['max_drawdown']:.1f}% | **{s['verdict']}** |"
            )

    lines += [
        "",
        "## Ranked Strategies by Expected Value",
        "",
    ]
    for i, s in enumerate(ranked, 1):
        lines.append(f"{i}. **{s['label']}** — EV: {s['expected_value']:.1f}% — **{s['verdict']}**")

    if ranked:
        winner = ranked[0]
        lines += [
            "",
            f"### Best Strategy: {winner['label']}",
            "",
            f"- Win rate: {winner['win_rate']:.1f}%",
            f"- Average return: {winner['avg_return']:.1f}%",
            f"- Median return: {winner['median_return']:.1f}%",
            f"- Max drawdown: {winner['max_drawdown']:.1f}%",
        ]

    lines += [
        "",
        "---",
        "",
        "## Methodology Notes",
        "",
        "- Token discovery: on-chain Alchemy RPC scanning Jan 20 2026 blocks (getBlocks + getBlock)",
        "- CreateV2 fingerprint: PUMP_PROGRAM in accounts + 'pump'-suffix mint with preBalance=0",
        "- Graduation detection: DexScreener Raydium pair presence",
        "- Price data: GeckoTerminal OHLCV (free tier, 1-min and hourly candles)",
        "- Pre-graduation price: bonding curve prices not available via OHLCV — limited data",
        "- Near-graduation %: estimated from FDV / $69,000 graduation threshold",
        "- pump.fun frontend API was NOT used (returns 530 errors)",
        "",
        "*Generated by analyze.py from pump-fun-analytics pipeline.*",
    ]

    report_path = "output/analysis_report.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Analysis report saved to {report_path}")


def main():
    print("=" * 60)
    print("ANALYZE — Final investment thesis")
    print("=" * 60)

    launches = load_json("step1_launches.json") or []
    near_grad_data = load_json("step2_near_grad.json")
    price_data = load_json("step3_price_action.json")

    if not launches:
        print("ERROR: step1_launches.json is empty or missing. Run step1 first.")
        sys.exit(1)

    print(f"Loaded {len(launches)} launches, "
          f"{len((near_grad_data or {}).get('near_grad_tokens', []))} near-grad tokens, "
          f"{len((price_data or {}).get('tokens', []))} graduated price records.")

    print("\n[Q1] Analyzing pre-graduation buy strategy...")
    q1 = analyze_q1(launches, near_grad_data, price_data)
    print(f"  90%+ tokens: {q1['tokens_90plus']} | "
          f"Grad rate: {q1['grad_rate_90plus_pct']}% | "
          f"EV: {q1['ev_net_multiplier']:+.2f}x | Verdict: {q1['verdict']}")

    print("\n[Q2] Simulating post-graduation strategies...")
    strategies, ranked = analyze_q2(price_data)
    for s in strategies:
        if s["n"] > 0:
            print(f"  {s['label']}: n={s['n']}, win={s['win_rate']}%, avg={s['avg_return']}%")
        else:
            print(f"  {s['label']}: INSUFFICIENT DATA")

    if ranked:
        print(f"\n  Best strategy: {ranked[0]['label']} (EV: {ranked[0]['expected_value']:.1f}%)")

    write_report(q1, strategies, ranked, launches)

    print("\nANALYSIS COMPLETE.")


if __name__ == "__main__":
    main()
