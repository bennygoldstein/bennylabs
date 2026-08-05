#!/usr/bin/env python3
"""Daily Kalshi odds scanner.

Pulls every open market from Kalshi's public API (no account needed) and
produces a digest with two ranked lists:

  1. Safest favorites — contracts the market prices as most likely to win,
     filtered for real liquidity and a payout that is still worth taking
     after Kalshi's trading fee.
  2. Value plays — pricing anomalies: big 24h price moves on real volume,
     mutually-exclusive events whose prices don't add up to $1, and heavily
     traded markets with unusually wide bid/ask spreads.

Stdlib only, so it runs anywhere Python 3 is installed:

    python3 scanner/kalshi_scan.py                 # markdown digest to stdout
    python3 scanner/kalshi_scan.py --out digest.md # also write to a file
    python3 scanner/kalshi_scan.py --json out.json # machine-readable dump
"""

import argparse
import http.client
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
PAGE_LIMIT = 200          # max page size for the events endpoint
REQUEST_TIMEOUT = 30
MAX_RETRIES = 4

# --- Ranking thresholds -----------------------------------------------------
FAVORITE_MIN_PROB = 0.85          # implied probability floor for "favorites"
FAVORITE_MAX_COST = 0.97          # above this the payout isn't worth the risk
FAVORITE_MAX_SPREAD = 0.05        # bid/ask spread must be tight (real market)
FAVORITE_MIN_VOL24 = 100          # contracts traded in the last 24h
FAVORITE_MAX_DAYS = 45            # don't lock money up longer than this
FAVORITE_MIN_DAYS = 3 / 24        # skip markets that expire before you read the email
FAVORITE_TOP_N = 15
FAVORITES_PER_SERIES = 1          # avoid 10 near-identical picks from one series

MOVER_MIN_DELTA = 0.08            # 24h price change (in dollars) to qualify
MOVER_MIN_VOL24 = 500
MOVER_MIN_DAYS = 1.5              # skip markets already resolving today
MOVER_MAX_SPREAD = 0.10           # a "move" in a thin, wide book is just one
                                  # trade printing, not a real repricing
MOVER_TOP_N = 10

ARB_MIN_EDGE = 0.03               # dollars of edge (post-fee) before we flag it
ARB_MAX_EDGE = 0.20               # bigger "edges" mean the outcome list isn't exhaustive
ARB_MIN_EVENT_VOL24 = 100         # ignore zombie books nobody trades
ARB_TOP_N = 8

SPREAD_MIN_WIDTH = 0.10
SPREAD_MIN_VOL24 = 1000
SPREAD_TOP_N = 5


def fetch_json(url):
    """GET a URL with retries and exponential backoff."""
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bennylabs-kalshi-scanner/1.0"})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        # OSError covers URLError plus mid-body failures (connection reset,
        # RemoteDisconnected); HTTPException covers IncompleteRead — all of
        # which can escape resp.read() without being wrapped in URLError.
        except (OSError, http.client.HTTPException, json.JSONDecodeError, UnicodeDecodeError) as err:
            last_err = err
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def fetch_all_open_events():
    """Page through every open event, markets nested inside each."""
    events, cursor = [], None
    while True:
        params = {"limit": PAGE_LIMIT, "status": "open", "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        data = fetch_json(f"{API_BASE}/events?{urllib.parse.urlencode(params)}")
        page = data.get("events", [])
        events.extend(page)
        cursor = data.get("cursor")
        if not cursor or not page:
            return events


def dollars(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def taker_fee(price):
    """Kalshi trading fee per contract: 7% of price*(1-price), in dollars."""
    return 0.07 * price * (1.0 - price)


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def kalshi_url(series_ticker):
    return f"https://kalshi.com/markets/{series_ticker.lower()}" if series_ticker else "https://kalshi.com"


def collect_markets(events, now):
    """Flatten events into scored market records, skipping junk."""
    records = []
    for event in events:
        category = event.get("category") or "Other"
        series = event.get("series_ticker") or ""
        event_title = event.get("title") or event.get("sub_title") or ""
        for m in event.get("markets", []):
            if m.get("status") != "active" or m.get("market_type") != "binary":
                continue
            if m.get("is_provisional"):
                continue
            if m.get("mve_collection_ticker"):   # multivariate parlay combos
                continue
            yes_bid = dollars(m.get("yes_bid_dollars"))
            yes_ask = dollars(m.get("yes_ask_dollars"))
            no_bid = dollars(m.get("no_bid_dollars"))
            no_ask = dollars(m.get("no_ask_dollars"))
            if None in (yes_bid, yes_ask, no_bid, no_ask):
                continue
            if yes_ask <= 0 or yes_ask > 1 or yes_bid < 0:
                continue
            close = parse_time(m.get("expected_expiration_time")) or parse_time(m.get("close_time"))
            records.append({
                "ticker": m.get("ticker", ""),
                "title": m.get("title", ""),
                "yes_sub_title": m.get("yes_sub_title", ""),
                "event_ticker": m.get("event_ticker", ""),
                "event_title": event_title,
                "category": category,
                "series": series,
                "url": kalshi_url(series),
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "no_bid": no_bid,
                "no_ask": no_ask,
                "mid": (yes_bid + yes_ask) / 2,
                "spread": yes_ask - yes_bid,
                "last": dollars(m.get("last_price_dollars")),
                "prev": dollars(m.get("previous_price_dollars")),
                "vol24": dollars(m.get("volume_24h_fp")) or 0.0,
                "volume": dollars(m.get("volume_fp")) or 0.0,
                "open_interest": dollars(m.get("open_interest_fp")) or 0.0,
                "close": close,
                "days_left": (close - now).total_seconds() / 86400 if close else None,
            })
    return records


def md_safe(text):
    """Keep market titles from breaking markdown tables."""
    return (text or "").replace("|", "/").replace("\n", " ").strip()


def label(rec, side):
    """Human-readable name of a bet, e.g. 'YES Seattle — Mariners win?'."""
    what = rec["yes_sub_title"] or rec["title"]
    name = f"{side} {what}" if what else side
    if rec["title"] and rec["title"] != what:
        name += f" — {rec['title']}"
    return md_safe(name)


def find_favorites(records, now):
    """Highest-probability contracts with real liquidity and payout left."""
    picks = []
    for r in records:
        if r["days_left"] is None or not (FAVORITE_MIN_DAYS <= r["days_left"] <= FAVORITE_MAX_DAYS):
            continue
        if r["vol24"] < FAVORITE_MIN_VOL24 or r["spread"] > FAVORITE_MAX_SPREAD:
            continue
        # Which side is the favorite, and what does it cost to buy right now?
        if r["mid"] >= 0.5:
            side, cost, prob = "YES", r["yes_ask"], r["mid"]
        else:
            side, cost, prob = "NO", r["no_ask"], 1.0 - r["mid"]
        if prob < FAVORITE_MIN_PROB or cost > FAVORITE_MAX_COST or cost <= 0:
            continue
        net_profit = (1.0 - cost) - taker_fee(cost)   # per contract, if it wins
        if net_profit <= 0.005:
            continue
        picks.append({
            **{k: r[k] for k in ("ticker", "title", "category", "url", "vol24", "days_left", "series")},
            "bet": label(r, side),
            "side": side,
            "cost": cost,
            "prob": prob,
            "net_return_pct": 100.0 * net_profit / cost,
        })
    picks.sort(key=lambda p: (-p["prob"], -p["vol24"]))
    # Keep the list diverse: cap picks per series, otherwise one lopsided
    # tournament fills the whole table with near-identical bets.
    seen, out = {}, []
    for p in picks:
        series = p["series"] or p["ticker"].rsplit("-", 1)[0]
        if seen.get(series, 0) >= FAVORITES_PER_SERIES:
            continue
        seen[series] = seen.get(series, 0) + 1
        out.append(p)
        if len(out) >= FAVORITE_TOP_N:
            break
    return out


def find_movers(records):
    """Big 24h price moves on real volume — something changed; worth a look."""
    movers = []
    for r in records:
        if r["last"] is None or r["prev"] is None or r["prev"] <= 0:
            continue
        delta = r["last"] - r["prev"]
        if abs(delta) < MOVER_MIN_DELTA or r["vol24"] < MOVER_MIN_VOL24:
            continue
        # Skip markets that have effectively resolved (nothing left to bet on)
        # or are resolving right now (today's weather, games in progress).
        if not (0.05 <= r["last"] <= 0.95):
            continue
        if r["days_left"] is None or r["days_left"] < MOVER_MIN_DAYS:
            continue
        if r["spread"] > MOVER_MAX_SPREAD:
            continue
        movers.append({
            **{k: r[k] for k in ("ticker", "title", "category", "url", "vol24", "days_left")},
            "bet": label(r, "YES"),
            "prev": r["prev"],
            "last": r["last"],
            "delta": delta,
        })
    movers.sort(key=lambda m: -abs(m["delta"]))
    return movers[:MOVER_TOP_N]


def find_event_arbs(events, records_by_ticker):
    """Mutually-exclusive events whose YES prices don't sum to ~$1.

    If exactly one outcome must win, the YES asks should sum to at least
    $1 (else buying every YES locks in a profit) and the YES bids should
    sum to at most $1 (else buying every NO locks in a profit). Real gaps
    are rare and can also mean the outcome list isn't exhaustive, so these
    are flagged for review rather than called free money.
    """
    finds = []
    for event in events:
        if not event.get("mutually_exclusive"):
            continue
        nested = [m for m in event.get("markets", [])
                  if m.get("status") == "active" and m.get("market_type") == "binary"]
        markets = [records_by_ticker[m["ticker"]] for m in nested
                   if m.get("ticker") in records_by_ticker]
        # Every active outcome must be present with a real, purchasable quote
        # on both sides — if even one leg is unquotable, the "edge" is fiction.
        if len(markets) < 2 or len(markets) != len(nested):
            continue
        if any(not (0 < m["yes_ask"] < 1) or not (0 < m["no_ask"] < 1) for m in markets):
            continue
        if sum(m["vol24"] for m in markets) < ARB_MIN_EVENT_VOL24:
            continue
        sum_ask = sum(m["yes_ask"] for m in markets)
        sum_bid = sum(m["yes_bid"] for m in markets)
        yes_fees = sum(taker_fee(m["yes_ask"]) for m in markets)
        entry = {
            "event_ticker": event.get("event_ticker", ""),
            "event_title": md_safe(event.get("title") or event.get("event_ticker", "")),
            "category": event.get("category") or "Other",
            "url": kalshi_url(event.get("series_ticker") or ""),
            "n_outcomes": len(markets),
            "sum_yes_ask": sum_ask,
            "sum_yes_bid": sum_bid,
        }
        # Compute each direction's post-fee edge directly from the prices you
        # would actually pay — gating on the other side's fees drops real finds.
        yes_edge = 1.0 - sum_ask - yes_fees
        no_cost = sum(m["no_ask"] for m in markets)
        no_payout = len(markets) - 1   # every NO pays except the winner's
        no_edge = no_payout - no_cost - sum(taker_fee(m["no_ask"]) for m in markets)
        if ARB_MIN_EDGE < yes_edge <= ARB_MAX_EDGE:   # bigger gaps = non-exhaustive event
            finds.append({**entry, "kind": "buy_all_yes", "edge": yes_edge})
        elif ARB_MIN_EDGE < no_edge <= ARB_MAX_EDGE:
            finds.append({**entry, "kind": "buy_all_no", "edge": no_edge})
    finds.sort(key=lambda f: -f["edge"])
    return finds[:ARB_TOP_N]


def find_wide_spreads(records):
    """Heavily traded markets with wide spreads — limit orders get paid here."""
    wides = [
        {
            **{k: r[k] for k in ("ticker", "title", "category", "url", "vol24")},
            "bet": label(r, "YES"),
            "yes_bid": r["yes_bid"],
            "yes_ask": r["yes_ask"],
            "spread": r["spread"],
        }
        for r in records
        if r["spread"] >= SPREAD_MIN_WIDTH and r["vol24"] >= SPREAD_MIN_VOL24
        # Both sides must hold real orders — a 1¢ bid against a 99¢ ask is
        # an empty book (e.g. a game in progress), not an opportunity.
        and 0.03 <= r["yes_bid"] and r["yes_ask"] <= 0.97
    ]
    wides.sort(key=lambda w: -(w["spread"] * w["vol24"]))
    return wides[:SPREAD_TOP_N]


def cents(x):
    return f"{round(x * 100)}¢"


def fmt_days(days):
    if days is None:
        return "—"
    if days < 1:
        return f"{max(1, round(days * 24))}h"
    return f"{round(days)}d"


def render_digest(now, n_markets, favorites, movers, arbs, wides):
    lines = [
        f"# Kalshi Daily Digest — {now.strftime('%A, %B %-d, %Y')}",
        "",
        f"Scanned **{n_markets:,} active markets** across all categories at "
        f"{now.strftime('%H:%M UTC')}.",
        "",
        "## 🎯 Safest favorites",
        "",
        "Highest-probability contracts with real trading volume, tight spreads, "
        f"and settlement within {FAVORITE_MAX_DAYS} days. `Prob` is the market's "
        "implied chance of winning; `Net return` is profit per dollar staked if "
        "it wins, after Kalshi's trading fee.",
        "",
    ]
    if favorites:
        lines += ["| Bet | Category | Cost | Prob | Net return | Closes | 24h vol |",
                  "|---|---|---|---|---|---|---|"]
        for p in favorites:
            lines.append(
                f"| [{p['bet']}]({p['url']}) | {p['category']} | {cents(p['cost'])} "
                f"| {p['prob']:.0%} | +{p['net_return_pct']:.1f}% | {fmt_days(p['days_left'])} "
                f"| {p['vol24']:,.0f} |"
            )
    else:
        lines.append("_No markets passed the filters today._")

    lines += ["", "## 📈 Value plays", "", "### Big 24-hour movers",
              "", "Prices that jumped or fell sharply on real volume — news moved "
              "these markets, and early moves often overshoot or undershoot.", ""]
    if movers:
        lines += ["| Market | Category | Yesterday | Now | Move | Closes | 24h vol |",
                  "|---|---|---|---|---|---|---|"]
        for m in movers:
            arrow = "▲" if m["delta"] > 0 else "▼"
            lines.append(
                f"| [{m['bet']}]({m['url']}) | {m['category']} | {cents(m['prev'])} "
                f"| {cents(m['last'])} | {arrow} {cents(abs(m['delta']))} "
                f"| {fmt_days(m['days_left'])} | {m['vol24']:,.0f} |"
            )
    else:
        lines.append("_No large moves in the last 24 hours._")

    lines += ["", "### Prices that don't add up", "",
              "Mutually-exclusive events where the YES prices don't sum to ~$1. "
              "In theory covering every outcome locks in the edge shown (fees "
              "already deducted) — but verify the outcome list is exhaustive and "
              "the books are deep enough before treating it as free money.", ""]
    if arbs:
        lines += ["| Event | Category | Outcomes | Σ YES ask | Play | Edge |",
                  "|---|---|---|---|---|---|"]
        for a in arbs:
            play = "Buy every YES" if a["kind"] == "buy_all_yes" else "Buy every NO"
            lines.append(
                f"| [{a['event_title']}]({a['url']}) | {a['category']} | {a['n_outcomes']} "
                f"| {cents(a['sum_yes_ask'])} | {play} | +{cents(a['edge'])} |"
            )
    else:
        lines.append("_No pricing gaps found — the books are consistent today._")

    lines += ["", "### Wide spreads on busy markets", "",
              "Actively traded but with a big gap between bid and ask — patient "
              "limit orders in the middle tend to get filled at good prices.", ""]
    if wides:
        lines += ["| Market | Category | Bid | Ask | Spread | 24h vol |",
                  "|---|---|---|---|---|---|"]
        for w in wides:
            lines.append(
                f"| [{w['bet']}]({w['url']}) | {w['category']} | {cents(w['yes_bid'])} "
                f"| {cents(w['yes_ask'])} | {cents(w['spread'])} | {w['vol24']:,.0f} |"
            )
    else:
        lines.append("_None today._")

    lines += ["", "---", "",
              "_Prices are live order-book quotes at scan time and move constantly. "
              "Implied probability is the market's estimate, not a guarantee — a 95¢ "
              "favorite still loses 1 time in 20. Net returns assume taker fees of "
              "7% × price × (1−price) per contract. This is a data scan, not "
              "financial advice._"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Scan Kalshi for the strongest odds.")
    ap.add_argument("--out", help="also write the markdown digest to this file")
    ap.add_argument("--json", dest="json_out", help="write machine-readable results to this file")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    events = fetch_all_open_events()
    records = collect_markets(events, now)
    records_by_ticker = {r["ticker"]: r for r in records}

    favorites = find_favorites(records, now)
    movers = find_movers(records)
    arbs = find_event_arbs(events, records_by_ticker)
    wides = find_wide_spreads(records)

    digest = render_digest(now, len(records), favorites, movers, arbs, wides)
    print(digest)
    if args.out:
        with open(args.out, "w") as f:
            f.write(digest + "\n")
    if args.json_out:
        payload = {
            "generated_at": now.isoformat(),
            "markets_scanned": len(records),
            "favorites": favorites,
            "movers": movers,
            "arbs": arbs,
            "wide_spreads": wides,
        }
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2, default=str)


if __name__ == "__main__":
    sys.exit(main())
