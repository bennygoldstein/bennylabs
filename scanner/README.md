# Kalshi Daily Odds Scanner

Scans every open market on [Kalshi](https://kalshi.com) via the free public
API (no account or API key needed) and produces a daily digest of where the
odds are strongest.

## What it finds

1. **Safest favorites** — the contracts the market prices as most likely to
   win, filtered so only real opportunities make the list:
   - implied probability ≥ 85%, cost ≤ 97¢ (so there's still a payout)
   - tight bid/ask spread and real 24-hour trading volume
   - settles within 45 days, and not within the next 3 hours
   - at most one pick per series, so one tournament can't fill the table
   - net return shown **after** Kalshi's taker fee (7% × price × (1−price))

2. **Value plays**
   - *Big 24-hour movers* — prices that jumped or fell 8¢+ on real volume
   - *Prices that don't add up* — mutually-exclusive events whose YES prices
     don't sum to ~$1 after fees (flagged for review; verify the outcome
     list is exhaustive before treating it as an arbitrage)
   - *Wide spreads on busy markets* — heavily traded books with a 10¢+ gap
     where patient limit orders tend to get filled well

## Usage

```bash
python3 scanner/kalshi_scan.py                    # markdown digest to stdout
python3 scanner/kalshi_scan.py --out digest.md    # also write to a file
python3 scanner/kalshi_scan.py --json digest.json # machine-readable output
```

Python 3 standard library only — no dependencies to install. A full scan of
~65k active markets takes about 20–30 seconds.

Thresholds live at the top of `kalshi_scan.py` as named constants and are
easy to tune.

## Disclaimer

Prices are live order-book quotes at scan time and move constantly. Implied
probability is the market's estimate, not a guarantee — a 95¢ favorite still
loses 1 time in 20. This is a data scan, not financial advice.
