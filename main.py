"""
Arbitrage Scanner — All Live Sports + Web Dashboard
=====================================================
Runs a live web dashboard at your Railway URL.
Scanner runs in background, dashboard auto-updates every 15s.

Railway Variables:
  ODDS_API_KEY = your_key
  STAKE        = 30
  MIN_PROFIT   = 0.5
  PORT         = 8080  (Railway sets this automatically)
"""

import time, requests, os, logging, threading, json
from datetime import datetime
from flask import Flask, jsonify, render_template_string

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────
API_KEY       = os.environ.get("ODDS_API_KEY", "YOUR_API_KEY_HERE")
STAKE         = float(os.environ.get("STAKE", "30"))
MIN_PROFIT    = float(os.environ.get("MIN_PROFIT", "0.5"))
SCAN_INTERVAL = 60
PORT          = int(os.environ.get("PORT", "8080"))

BOOKMAKERS_UK = [
    "bet365","betfair","paddypower","williamhill",
    "ladbrokes","coral","skybet","betway",
    "unibet","betvictor","matchbook","sportingbet",
]

# ── Shared state ─────────────────────────────────────────────────────────
state = {
    "arbs":          [],
    "last_scan":     None,
    "scan_count":    0,
    "sports_count":  0,
    "total_arbs":    0,
    "total_profit":  0.0,
    "api_remaining": "?",
    "status":        "starting",
}
state_lock = threading.Lock()

# ── Odds API ──────────────────────────────────────────────────────────────
def get_active_sports():
    r = requests.get("https://api.the-odds-api.com/v4/sports",
                     params={"apiKey": API_KEY, "all": "false"}, timeout=15)
    if r.status_code != 200:
        return []
    return r.json()

def fetch_events(sport_key):
    r = requests.get(
        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
        params={"apiKey": API_KEY, "regions": "uk,eu", "markets": "h2h",
                "oddsFormat": "decimal", "bookmakers": ",".join(BOOKMAKERS_UK)},
        timeout=15)
    if r.status_code not in (200,):
        return [], "?"
    remaining = r.headers.get("x-requests-remaining", "?")
    return r.json(), remaining

def get_best_odds(event):
    best = {}
    for book in event.get("bookmakers", []):
        bname = book.get("key", "")
        for market in book.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []):
                name = outcome.get("name", "")
                odds = outcome.get("price", 0)
                if name not in best or odds > best[name]["odds"]:
                    best[name] = {"odds": odds, "bookmaker": bname}
    return [{"name": k, **v} for k, v in best.items()]

def find_arb(outcomes):
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
        stakes.append({"name": o["name"], "odds": o["odds"],
                        "bookmaker": o["bookmaker"],
                        "stake": round(stake, 2), "payout": round(payout, 2)})
    total_stake = sum(s["stake"] for s in stakes)
    guaranteed  = round(stakes[0]["payout"], 2)
    return {"profit_pct": round(profit_pct, 2), "total_stake": round(total_stake, 2),
            "profit": round(guaranteed - total_stake, 2),
            "guaranteed": guaranteed, "bets": stakes}

# ── Scanner thread ────────────────────────────────────────────────────────
def scanner_loop():
    while True:
        try:
            if API_KEY == "YOUR_API_KEY_HERE":
                with state_lock:
                    state["status"] = "no_key"
                time.sleep(30)
                continue

            with state_lock:
                state["status"] = "scanning"

            sports = get_active_sports()
            all_arbs = []
            remaining = "?"

            for sport in sports:
                key   = sport.get("key", "")
                title = sport.get("title", key)
                events, rem = fetch_events(key)
                remaining = rem
                time.sleep(0.3)

                for event in events:
                    name     = event.get("home_team","") + " vs " + event.get("away_team","")
                    commence = event.get("commence_time", "")
                    outcomes = get_best_odds(event)
                    arb      = find_arb(outcomes)
                    if arb and arb["profit_pct"] >= MIN_PROFIT:
                        all_arbs.append({
                            "event":        name,
                            "sport":        title,
                            "commence":     commence,
                            "arb":          arb,
                            "found_at":     datetime.now().strftime("%H:%M:%S"),
                        })

            all_arbs.sort(key=lambda x: x["arb"]["profit_pct"], reverse=True)

            with state_lock:
                state["scan_count"]   += 1
                state["last_scan"]     = datetime.now().strftime("%H:%M:%S")
                state["sports_count"]  = len(sports)
                state["arbs"]          = all_arbs
                state["total_arbs"]   += len(all_arbs)
                state["total_profit"] += sum(a["arb"]["profit"] for a in all_arbs)
                state["api_remaining"] = remaining
                state["status"]        = "idle"

            log.info(f"Scan #{state['scan_count']} — {len(all_arbs)} arbs across {len(sports)} sports")

        except Exception as e:
            log.error(f"Scanner error: {e}")
            with state_lock:
                state["status"] = "error"

        time.sleep(SCAN_INTERVAL)

# ── Flask app ─────────────────────────────────────────────────────────────
app = Flask(__name__)

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ARB SCANNER</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Bebas+Neue&display=swap" rel="stylesheet">
<style>
  :root {
    --green:   #00ff88;
    --dim:     #00994d;
    --red:     #ff3355;
    --gold:    #ffd700;
    --bg:      #050a06;
    --surface: #0a120b;
    --border:  #1a2e1c;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    background: var(--bg);
    color: var(--green);
    font-family: 'Share Tech Mono', monospace;
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* scanline overlay */
  body::before {
    content: '';
    position: fixed; inset: 0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,0,0,0.08) 2px,
      rgba(0,0,0,0.08) 4px
    );
    pointer-events: none;
    z-index: 9999;
  }

  /* noise texture */
  body::after {
    content: '';
    position: fixed; inset: 0;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.04'/%3E%3C/svg%3E");
    pointer-events: none;
    z-index: 9998;
    opacity: 0.4;
  }

  header {
    border-bottom: 1px solid var(--border);
    padding: 16px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--surface);
    position: sticky; top: 0; z-index: 100;
  }

  .logo {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 2rem;
    letter-spacing: 0.2em;
    color: var(--green);
    text-shadow: 0 0 20px rgba(0,255,136,0.5);
  }

  .logo span { color: var(--gold); }

  .status-bar {
    display: flex;
    gap: 24px;
    font-size: 0.75rem;
    color: var(--dim);
  }

  .status-bar .val {
    color: var(--green);
    font-size: 0.9rem;
  }

  .pulse {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse 1.5s ease-in-out infinite;
    margin-right: 6px;
  }

  .pulse.idle   { background: var(--dim); box-shadow: none; animation: none; }
  .pulse.error  { background: var(--red); box-shadow: 0 0 8px var(--red); }
  .pulse.no_key { background: var(--gold); box-shadow: 0 0 8px var(--gold); animation: pulse 2s infinite; }

  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%       { opacity: 0.4; transform: scale(0.8); }
  }

  .main { padding: 24px 32px; }

  /* stat strip */
  .stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 28px;
  }

  .stat {
    background: var(--surface);
    border: 1px solid var(--border);
    padding: 16px 20px;
    position: relative;
    overflow: hidden;
  }

  .stat::before {
    content: '';
    position: absolute;
    top: 0; left: 0;
    width: 3px; height: 100%;
    background: var(--green);
    box-shadow: 0 0 10px var(--green);
  }

  .stat-label { font-size: 0.65rem; color: var(--dim); letter-spacing: 0.15em; text-transform: uppercase; }
  .stat-val   { font-size: 1.8rem; font-family: 'Bebas Neue', sans-serif;
                color: var(--green); line-height: 1.1; margin-top: 4px;
                text-shadow: 0 0 12px rgba(0,255,136,0.4); }

  /* arb cards */
  .section-title {
    font-size: 0.7rem;
    letter-spacing: 0.25em;
    color: var(--dim);
    text-transform: uppercase;
    margin-bottom: 14px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 8px;
  }

  .arb-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
    gap: 16px;
  }

  .arb-card {
    background: var(--surface);
    border: 1px solid var(--border);
    position: relative;
    overflow: hidden;
    animation: slideIn 0.4s ease-out;
  }

  @keyframes slideIn {
    from { opacity: 0; transform: translateY(10px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .arb-card.top {
    border-color: var(--gold);
    box-shadow: 0 0 20px rgba(255,215,0,0.1);
  }

  .arb-card.top::after {
    content: 'BEST';
    position: absolute;
    top: 10px; right: 10px;
    font-size: 0.6rem;
    letter-spacing: 0.2em;
    background: var(--gold);
    color: #000;
    padding: 2px 8px;
    font-weight: bold;
  }

  .card-header {
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
  }

  .card-sport {
    font-size: 0.6rem;
    letter-spacing: 0.2em;
    color: var(--dim);
    text-transform: uppercase;
    margin-bottom: 4px;
  }

  .card-event {
    font-size: 0.85rem;
    color: var(--green);
    line-height: 1.3;
  }

  .card-profit {
    text-align: right;
  }

  .profit-pct {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 2rem;
    color: var(--gold);
    text-shadow: 0 0 15px rgba(255,215,0,0.5);
    line-height: 1;
  }

  .profit-label { font-size: 0.6rem; color: var(--dim); letter-spacing: 0.1em; }

  .card-guarantee {
    padding: 8px 16px;
    background: rgba(0,255,136,0.03);
    font-size: 0.72rem;
    color: var(--dim);
    border-bottom: 1px solid var(--border);
  }

  .card-guarantee span { color: var(--green); }

  .bets { padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }

  .bet-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 8px 10px;
    background: rgba(0,255,136,0.03);
    border-left: 2px solid var(--border);
  }

  .bet-row:hover { border-left-color: var(--green); background: rgba(0,255,136,0.06); }

  .bet-name  { font-size: 0.78rem; color: var(--green); flex: 1; }
  .bet-stake { font-size: 0.9rem; color: var(--gold); min-width: 55px; text-align: right; }
  .bet-odds  { font-size: 0.75rem; color: var(--dim); min-width: 40px; text-align: center; }
  .bet-book  {
    font-size: 0.6rem; letter-spacing: 0.1em;
    background: var(--border);
    padding: 2px 6px;
    color: var(--dim);
    min-width: 70px;
    text-align: center;
    text-transform: uppercase;
  }

  .card-footer {
    padding: 8px 16px;
    font-size: 0.6rem;
    color: var(--dim);
    border-top: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
  }

  /* empty state */
  .empty {
    grid-column: 1 / -1;
    text-align: center;
    padding: 80px 20px;
    color: var(--dim);
  }

  .empty .big { font-family: 'Bebas Neue', sans-serif; font-size: 4rem; opacity: 0.2; }
  .empty p    { font-size: 0.8rem; margin-top: 12px; }

  /* no key warning */
  .warning {
    background: rgba(255,215,0,0.05);
    border: 1px solid var(--gold);
    padding: 20px 24px;
    margin-bottom: 24px;
    font-size: 0.82rem;
    line-height: 1.7;
  }

  .warning a { color: var(--gold); }

  /* refresh indicator */
  .refresh-bar {
    position: fixed;
    bottom: 0; left: 0;
    height: 2px;
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: refill 15s linear infinite;
    transform-origin: left;
  }

  @keyframes refill {
    from { width: 100%; }
    to   { width: 0%; }
  }
</style>
</head>
<body>

<header>
  <div class="logo">ARB <span>SCANNER</span></div>
  <div class="status-bar">
    <div><span class="pulse" id="pulse"></span><span id="status-text">connecting...</span></div>
    <div>LAST SCAN <span class="val" id="last-scan">—</span></div>
    <div>SPORTS <span class="val" id="sports-count">—</span></div>
    <div>API CALLS LEFT <span class="val" id="api-rem">—</span></div>
  </div>
</header>

<div class="main">
  <div id="warning-box"></div>

  <div class="stats">
    <div class="stat">
      <div class="stat-label">Scan #</div>
      <div class="stat-val" id="stat-scans">0</div>
    </div>
    <div class="stat">
      <div class="stat-label">Arbs This Scan</div>
      <div class="stat-val" id="stat-current">0</div>
    </div>
    <div class="stat">
      <div class="stat-label">Session Arbs</div>
      <div class="stat-val" id="stat-total">0</div>
    </div>
    <div class="stat">
      <div class="stat-label">Best Profit %</div>
      <div class="stat-val" id="stat-best">—</div>
    </div>
  </div>

  <div class="section-title">Live Opportunities — Ranked by Profit</div>
  <div class="arb-grid" id="arb-grid">
    <div class="empty">
      <div class="big">SCANNING</div>
      <p>Waiting for first scan results...</p>
    </div>
  </div>
</div>

<div class="refresh-bar" id="refresh-bar"></div>

<script>
async function fetchState() {
  try {
    const res  = await fetch('/api/state');
    const data = await res.json();
    render(data);
  } catch(e) {
    document.getElementById('status-text').textContent = 'connection error';
  }
}

function render(d) {
  // status pulse
  const pulse = document.getElementById('pulse');
  const statusEl = document.getElementById('status-text');
  pulse.className = 'pulse ' + (d.status || '');
  const labels = { scanning:'SCANNING', idle:'LIVE', error:'ERROR', no_key:'NO API KEY', starting:'STARTING' };
  statusEl.textContent = labels[d.status] || d.status;

  document.getElementById('last-scan').textContent    = d.last_scan || '—';
  document.getElementById('sports-count').textContent = d.sports_count || '—';
  document.getElementById('api-rem').textContent      = d.api_remaining || '—';
  document.getElementById('stat-scans').textContent   = d.scan_count;
  document.getElementById('stat-current').textContent = d.arbs.length;
  document.getElementById('stat-total').textContent   = d.total_arbs;

  const best = d.arbs.length ? d.arbs[0].arb.profit_pct.toFixed(1) + '%' : '—';
  document.getElementById('stat-best').textContent = best;

  // warning
  const warn = document.getElementById('warning-box');
  if (d.status === 'no_key') {
    warn.innerHTML = `<div class="warning">
      ⚠ NO API KEY SET — Go to <a href="https://the-odds-api.com" target="_blank">the-odds-api.com</a>,
      sign up free, then add <code>ODDS_API_KEY = your_key</code> in Railway → Variables → Redeploy.
    </div>`;
  } else {
    warn.innerHTML = '';
  }

  // arb cards
  const grid = document.getElementById('arb-grid');
  if (!d.arbs.length) {
    grid.innerHTML = `<div class="empty">
      <div class="big">CLEAR</div>
      <p>No arbs above ${d.min_profit || 0.5}% right now — scanner checking every 60s</p>
    </div>`;
    return;
  }

  grid.innerHTML = d.arbs.map((item, i) => {
    const arb  = item.arb;
    const top  = i === 0 ? 'top' : '';
    const bets = arb.bets.map(b => `
      <div class="bet-row">
        <div class="bet-name">${b.name}</div>
        <div class="bet-odds">@ ${b.odds.toFixed(2)}</div>
        <div class="bet-stake">£${b.stake.toFixed(2)}</div>
        <div class="bet-book">${b.bookmaker}</div>
      </div>`).join('');

    return `
    <div class="arb-card ${top}">
      <div class="card-header">
        <div>
          <div class="card-sport">${item.sport}</div>
          <div class="card-event">${item.event}</div>
        </div>
        <div class="card-profit">
          <div class="profit-pct">${arb.profit_pct.toFixed(1)}%</div>
          <div class="profit-label">PROFIT</div>
        </div>
      </div>
      <div class="card-guarantee">
        Stake <span>£${arb.total_stake.toFixed(2)}</span>
        → Guaranteed return <span>£${arb.guaranteed.toFixed(2)}</span>
        → Profit <span>£${arb.profit.toFixed(2)}</span>
      </div>
      <div class="bets">${bets}</div>
      <div class="card-footer">
        <span>FOUND ${item.found_at}</span>
        <span>⚡ PLACE ALL BETS BEFORE ODDS MOVE</span>
      </div>
    </div>`;
  }).join('');
}

// Restart refresh bar animation
function restartBar() {
  const bar = document.getElementById('refresh-bar');
  bar.style.animation = 'none';
  bar.offsetHeight;
  bar.style.animation = 'refill 15s linear infinite';
}

fetchState();
setInterval(() => { fetchState(); restartBar(); }, 15000);
</script>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route("/api/state")
def api_state():
    with state_lock:
        return jsonify({**state, "min_profit": MIN_PROFIT})

# ── Entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info(f"Starting Arb Scanner — dashboard on port {PORT}")
    t = threading.Thread(target=scanner_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT, debug=False)
