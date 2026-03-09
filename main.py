"""
Cheltenham Arbitrage Scanner
=============================
Finds guaranteed profit opportunities across bookmakers.
Uses The Odds API free tier - sign up at https://the-odds-api.com

Setup:
1. Sign up free at https://the-odds-api.com
2. Copy your API key
3. Run: API_KEY=your_key_here python main.py
"""

import time, requests, sys, os, logging
from datetime import datetime
from itertools import combinations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────
API_KEY       = os.environ.get("ODDS_API_KEY", "f4758bdf001434479d7b2993446949fb")
STAKE         = float(os.environ.get("STAKE", "30"))       # total stake per arb £
MIN_PROFIT    = float(os.environ.get("MIN_PROFIT", "0.5")) # min profit % (0.5 = 0.5%)
SCAN_INTERVAL = 30                                          # seconds between scans

# Sports to scan — horse racing + tennis for tonight/tomorrow
SPORTS = [
    "horse_racing-greyhound_racing",  # Cheltenham
    "tennis_atp_indian_wells",        # Indian Wells tonight
    "tennis_wta_indian_wells",
    "soccer_epl",                     # Premier League backup
]

BOOKMAKERS_UK = [
    "bet365", "betfair", "paddypower", "williamhill",
    "ladbrokes", "coral", "skybet", "betway",
    "unibet", "betvictor",
]

# ── Maths ──────────────────────────────────────────────────────────────

def find_arb(outcomes: list[dict]) -> dict | None:
    """
    Given a list of outcomes with best odds across bookmakers,
    check if an arb exists and return the bet details.
    
    outcomes = [{"name": "Horse A", "odds": 3.2, "bookmaker": "bet365"}, ...]
    """
    if len(outcomes) < 2:
        return None

    # Sum of implied probabilities
    total_implied = sum(1 / o["odds"] for o in outcomes)

    if total_implied >= 1.0:
        return None  # No arb

    profit_pct = (1 - total_implied) * 100

    # Calculate individual stakes
    stakes = []
    for o in outcomes:
        stake = (1 / o["odds"]) / total_implied * STAKE
        payout = stake * o["odds"]
        stakes.append({
            "name":       o["name"],
            "odds":       o["odds"],
            "bookmaker":  o["bookmaker"],
            "stake":      round(stake, 2),
            "payout":     round(payout, 2),
        })

    total_stake  = sum(s["stake"] for s in stakes)
    guaranteed   = stakes[0]["payout"]  # all payouts equal
    profit       = round(guaranteed - total_stake, 2)

    return {
        "profit_pct":   round(profit_pct, 2),
        "total_stake":  round(total_stake, 2),
        "profit":       profit,
        "guaranteed":   round(guaranteed, 2),
        "bets":         stakes,
    }


def get_best_odds(event: dict) -> list[dict]:
    """
    For each outcome in an event, find the best odds
    available across all bookmakers.
    """
    best = {}  # outcome_name -> {odds, bookmaker}

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


def fetch_events(sport: str) -> list[dict]:
    """Fetch live odds for a sport from The Odds API."""
    try:
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{sport}/odds",
            params={
                "apiKey":      API_KEY,
                "regions":     "uk,eu",
                "markets":     "h2h",
                "oddsFormat":  "decimal",
                "bookmakers":  ",".join(BOOKMAKERS_UK),
            },
            timeout=15,
        )
        if r.status_code == 401:
            log.error("❌ Invalid API key — get one free at https://the-odds-api.com")
            return []
        if r.status_code == 422:
            return []  # Sport not available right now
        remaining = r.headers.get("x-requests-remaining", "?")
        log.info(f"  📡 {sport} | API calls remaining: {remaining}")
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        log.error(f"  ❌ Fetch failed for {sport}: {e}")
        return []


# ── Display ────────────────────────────────────────────────────────────

def display_arb(event_name: str, sport: str, arb: dict):
    log.info(f"\n{'🏆'*20}")
    log.info(f"  🚨 ARB FOUND — {sport.upper()}")
    log.info(f"  📌 {event_name}")
    log.info(f"  💰 Profit: £{arb['profit']:.2f} ({arb['profit_pct']:.2f}%)")
    log.info(f"  📊 Stake £{arb['total_stake']:.2f} → Get back £{arb['guaranteed']:.2f} guaranteed")
    log.info(f"  {'─'*45}")
    for bet in arb["bets"]:
        log.info(f"  ✅ £{bet['stake']:.2f} on {bet['name']:<20} "
                 f"@ {bet['odds']:.2f} → {bet['bookmaker'].upper()}")
    log.info(f"  {'─'*45}")
    log.info(f"  ⚡ Place ALL bets before odds change!")
    log.info(f"{'🏆'*20}\n")


# ── Main ───────────────────────────────────────────────────────────────

def run():
    log.info("="*55)
    log.info("  🎰  Cheltenham Arbitrage Scanner")
    log.info("  Scanning: Horse Racing + Tennis + Football")
    log.info(f"  Stake: £{STAKE} per arb | Min profit: {MIN_PROFIT}%")
    log.info(f"  Bookmakers: {', '.join(BOOKMAKERS_UK[:5])}...")
    log.info("="*55)

    if API_KEY == "YOUR_API_KEY_HERE":
        log.error("\n❌ NO API KEY SET!")
        log.error("  1. Go to https://the-odds-api.com and sign up FREE")
        log.error("  2. Copy your API key")
        log.error("  3. In Railway → Variables → add:")
        log.error("     ODDS_API_KEY = your_key_here")
        log.error("     STAKE = 30")
        log.error("\n  Then redeploy and you're live.\n")
        time.sleep(60)
        return

    scan_count   = 0
    total_arbs   = 0
    total_profit = 0.0

    while True:
        try:
            scan_count += 1
            now = datetime.now().strftime('%H:%M:%S')
            log.info(f"\n🔍 Scan #{scan_count} — {now} | "
                     f"Arbs found: {total_arbs} | "
                     f"Profit: £{total_profit:.2f}")

            found_any = False

            for sport in SPORTS:
                events = fetch_events(sport)

                for event in events:
                    name    = event.get("home_team", "") + " vs " + event.get("away_team", "")
                    outcomes = get_best_odds(event)
                    arb      = find_arb(outcomes)

                    if arb and arb["profit_pct"] >= MIN_PROFIT:
                        found_any    = True
                        total_arbs  += 1
                        total_profit += arb["profit"]
                        display_arb(name, sport, arb)

                time.sleep(0.5)  # be nice to the API

            if not found_any:
                log.info(f"  — No arbs found this scan (threshold: {MIN_PROFIT}%)")

            log.info(f"\n⏱  Next scan in {SCAN_INTERVAL}s...")
            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            log.info(f"\n👋 Stopped | Total arbs: {total_arbs} | "
                     f"Potential profit: £{total_profit:.2f}")
            break
        except Exception as e:
            log.error(f"Error: {e}")
            time.sleep(15)


if __name__ == "__main__":
    run()
