"""
Arbitrage Scanner — All Live Sports
=====================================
Automatically finds ALL active sports via The Odds API,
scans every one, and ranks arbs by profit %.

Setup:
1. Sign up free at https://the-odds-api.com
2. In Railway -> Variables -> add:
   ODDS_API_KEY = your_key_here
   STAKE = 30
   MIN_PROFIT = 0.5
"""

import time, requests, sys, os, logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────
API_KEY       = os.environ.get("ODDS_API_KEY", "YOUR_API_KEY_HERE")
STAKE         = float(os.environ.get("STAKE", "30"))
MIN_PROFIT    = float(os.environ.get("MIN_PROFIT", "0.5"))
SCAN_INTERVAL = 60   # seconds between full scans (saves API calls)

BOOKMAKERS_UK = [
    "bet365", "betfair", "paddypower", "williamhill",
    "ladbrokes", "coral", "skybet", "betway",
    "unibet", "betvictor", "matchbook", "sportingbet",
]

# ── Fetch all active sports ────────────────────────────────────────────

def get_active_sports() -> list:
    """Return all sports that currently have live/upcoming events."""
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports",
            params={"apiKey": API_KEY, "all": "false"},
            timeout=15,
        )
        if r.status_code == 401:
            log.error("Invalid API key — get one free at https://the-odds-api.com")
            return []
        sports = r.json()
        log.info(f"  🌍 {len(sports)} active sports found")
        return sports
    except Exception as e:
        log.error(f"  Failed to fetch sports list: {e}")
        return []


# ── Fetch odds for one sport ───────────────────────────────────────────

def fetch_events(sport_key: str) -> list:
    try:
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
            params={
                "apiKey":     API_KEY,
                "regions":    "uk,eu",
                "markets":    "h2h",
                "oddsFormat": "decimal",
                "bookmakers": ",".join(BOOKMAKERS_UK),
            },
            timeout=15,
        )
        if r.status_code in (401, 422):
            return []
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


# ── Arb maths ─────────────────────────────────────────────────────────

def get_best_odds(event: dict) -> list:
    best = {}
    for book in event.get("bookmakers", []):
        bname = book.get("key", "unknown")
        for market in book.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []):
                name = outcome.get("name", "")
                odds = outcome.get("price", 0)
                if name not in best or odds > best[name]["odds"]:
                    best[name] = {"odds": odds, "bookmaker": bname}
    return [{"name": k, **v} for k, v in best.items()]


def find_arb(outcomes: list) -> dict:
    if len(outcomes) < 2:
        return None
    total_implied = sum(1 / o["odds"] for o in outcomes)
    if total_implied >= 1.0:
        return None

    profit_pct = (1 - total_implied) * 100
    stakes = []
    for o in outcomes:
        stake  = (1 / o["odds"]) / total_implied * STAKE
        payout = stake * o["odds"]
        stakes.append({
            "name":      o["name"],
            "odds":      o["odds"],
            "bookmaker": o["bookmaker"],
            "stake":     round(stake, 2),
            "payout":    round(payout, 2),
        })

    total_stake = sum(s["stake"] for s in stakes)
    guaranteed  = round(stakes[0]["payout"], 2)
    profit      = round(guaranteed - total_stake, 2)

    return {
        "profit_pct":  round(profit_pct, 2),
        "total_stake": round(total_stake, 2),
        "profit":      profit,
        "guaranteed":  guaranteed,
        "bets":        stakes,
    }


# ── Display ────────────────────────────────────────────────────────────

def display_arb(rank: int, event_name: str, sport_title: str, arb: dict):
    log.info(f"\n{'='*55}")
    log.info(f"  #{rank}  {sport_title}")
    log.info(f"  {event_name}")
    log.info(f"  Profit: £{arb['profit']:.2f}  ({arb['profit_pct']:.2f}%)")
    log.info(f"  Stake £{arb['total_stake']:.2f} -> Guaranteed £{arb['guaranteed']:.2f}")
    log.info(f"  {'-'*45}")
    for bet in arb["bets"]:
        log.info(f"  £{bet['stake']:.2f}  {bet['name']:<25} @ {bet['odds']:.2f}  ->  {bet['bookmaker'].upper()}")
    log.info(f"  Place ALL bets NOW before odds move!")
    log.info(f"  {'='*55}\n")


# ── Main ───────────────────────────────────────────────────────────────

def run():
    log.info("="*55)
    log.info("  Arbitrage Scanner — ALL Live Sports")
    log.info(f"  Stake: £{STAKE} | Min profit: {MIN_PROFIT}%")
    log.info(f"  Scan interval: {SCAN_INTERVAL}s")
    log.info("="*55)

    if API_KEY == "YOUR_API_KEY_HERE":
        log.error("\nNO API KEY — go to the-odds-api.com, sign up free, add ODDS_API_KEY in Railway Variables\n")
        time.sleep(60)
        return

    scan_count     = 0
    session_arbs   = 0
    session_profit = 0.0

    while True:
        try:
            scan_count += 1
            now = datetime.now().strftime('%H:%M:%S')
            log.info(f"\nScan #{scan_count} — {now} | Session arbs: {session_arbs} | Profit: £{session_profit:.2f}")

            sports = get_active_sports()
            if not sports:
                log.info("  No active sports — retrying in 60s")
                time.sleep(60)
                continue

            all_arbs = []

            for sport in sports:
                sport_key   = sport.get("key", "")
                sport_title = sport.get("title", sport_key)

                events = fetch_events(sport_key)
                time.sleep(0.3)

                for event in events:
                    name     = event.get("home_team", "") + " vs " + event.get("away_team", "")
                    outcomes = get_best_odds(event)
                    arb      = find_arb(outcomes)

                    if arb and arb["profit_pct"] >= MIN_PROFIT:
                        all_arbs.append({
                            "event":       name,
                            "sport_title": sport_title,
                            "arb":         arb,
                        })

            # Rank by profit % — best first
            all_arbs.sort(key=lambda x: x["arb"]["profit_pct"], reverse=True)

            if all_arbs:
                log.info(f"\n🏆 {len(all_arbs)} ARB(S) FOUND — RANKED BY PROFIT:")
                for i, item in enumerate(all_arbs, 1):
                    display_arb(i, item["event"], item["sport_title"], item["arb"])
                    session_arbs   += 1
                    session_profit += item["arb"]["profit"]
            else:
                log.info(f"  No arbs above {MIN_PROFIT}% across {len(sports)} sports this scan")

            log.info(f"\nNext scan in {SCAN_INTERVAL}s...")
            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            log.info(f"\nDone | Total arbs: {session_arbs} | Potential profit: £{session_profit:.2f}")
            break
        except Exception as e:
            log.error(f"Error: {e}")
            time.sleep(15)


if __name__ == "__main__":
    run()
