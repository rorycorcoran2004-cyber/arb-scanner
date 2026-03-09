""" 
Arbitrage Scanner - All Live Sports + Dashboard + Phone Alerts 
"""
import time, requests, os, logging, threading
from datetime import datetime
from flask import Flask, jsonify, render_template_string

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger(__name__)

API_KEY       = os.environ.get("ODDS_API_KEY", "YOUR_API_KEY_HERE")
STAKE         = float(os.environ.get("STAKE", "30"))
MIN_PROFIT    = float(os.environ.get("MIN_PROFIT", "0.5"))
SCAN_INTERVAL = 60
PORT          = int(os.environ.get("PORT", "8080"))
NTFY_TOPIC    = os.environ.get("NTFY_TOPIC", "arb_scanner_ronan")

BOOKMAKERS_UK = ["bet365","betfair","paddypower","williamhill","ladbrokes","coral","skybet","betway","unibet","betvictor","matchbook","sportingbet"]

state = {"arbs":[],"last_scan":None,"scan_count":0,"sports_count":0,"total_arbs":0,"total_profit":0.0,"api_remaining":"?","status":"starting"}
state_lock = threading.Lock()
alerted_arbs = set()

def send_phone_alert(event, sport, arb):
    try:
        lines = [f"£{b['stake']:.2f} on {b['name']} @ {b['odds']:.2f} -> {b['bookmaker'].upper()}" for b in arb["bets"]]
        message = f"Profit: £{arb['profit']:.2f} ({arb['profit_pct']:.1f}%)\nStake £{arb['total_stake']:.2f} -> Get back £{arb['guaranteed']:.2f}\n\n" + "\n".join(lines)
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode("utf-8"), headers={"Title":f"ARB: {event[:40]}","Priority":"urgent","Tags":"money_with_wings"}, timeout=5)
        log.info(f"Phone alert sent for {event}")
    except Exception as e:
        log.warning(f"Alert failed: {e}")

def get_active_sports():
    try:
        r = requests.get("https://api.the-odds-api.com/v4/sports", params={"apiKey":API_KEY,"all":"false"}, timeout=15)
        if r.status_code != 200: return []
        sports = r.json()
        log.info(f"  {len(sports)} active sports found")
        return sports
    except Exception as e:
        log.error(f"Failed: {e}")
        return []

def fetch_events(sport_key):
    try:
        r = requests.get(f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds", params={"apiKey":API_KEY,"regions":"uk,eu","markets":"h2h","oddsFormat":"decimal","bookmakers":",".join(BOOKMAKERS_UK)}, timeout=15)
        if r.status_code != 200: return [], "?"
        return r.json(), r.headers.get("x-requests-remaining","?")
    except Exception:
        return [], "?"

def get_best_odds(event):
    best = {}
    for book in event.get("bookmakers",[]):
        bname = book.get("key","")
        for market in book.get("markets",[]):
            if market.get("key") != "h2h": continue
            for outcome in market.get("outcomes",[]):
                name = outcome.get("name","")
                odds = outcome.get("price",0)
                if name not in best or odds > best[name]["odds"]:
                    best[name] = {"odds":odds,"bookmaker":bname}
    return [{"name":k,**v} for k,v in best.items()]

def find_arb(outcomes):
    if len(outcomes) < 2: return None
    total_implied = sum(1/o["odds"] for o in outcomes)
    if total_implied >= 1.0: return None
    profit_pct = (1 - total_implied) * 100
    stakes = []
    for o in outcomes:
        stake = (1/o["odds"])/total_implied*STAKE
        payout = stake*o["odds"]
        stakes.append({"name":o["name"],"odds":o["odds"],"bookmaker":o["bookmaker"],"stake":round(stake,2),"payout":round(payout,2)})
    total_stake = sum(s["stake"] for s in stakes)
    guaranteed = round(stakes[0]["payout"],2)
    return {"profit_pct":round(profit_pct,2),"total_stake":round(total_stake,2),"profit":round(guaranteed-total_stake,2),"guaranteed":guaranteed,"bets":stakes}

def scanner_loop():
    global alerted_arbs
    while True:
        try:
            if API_KEY == "YOUR_API_KEY_HERE":
                with state_lock: state["status"] = "no_key"
                time.sleep(30)
                continue
            with state_lock: state["status"] = "scanning"
            sports = get_active_sports()
            all_arbs = []
            remaining = "?"
            for sport in sports:
                key = sport.get("key","")
                title = sport.get("title",key)
                events, rem = fetch_events(key)
                remaining = rem
                time.sleep(0.3)
                for event in events:
                    name = event.get("home_team","") + " vs " + event.get("away_team","")
                    outcomes = get_best_odds(event)
                    arb = find_arb(outcomes)
                    if arb and arb["profit_pct"] >= MIN_PROFIT:
                        all_arbs.append({"event":name,"sport":title,"arb":arb,"found_at":datetime.now().strftime("%H:%M:%S")})
            all_arbs.sort(key=lambda x: x["arb"]["profit_pct"], reverse=True)
            current_keys = set()
            for item in all_arbs:
                k = f"{item['event']}_{item['arb']['profit_pct']}"
                current_keys.add(k)
                if k not in alerted_arbs:
                    send_phone_alert(item["event"],item["sport"],item["arb"])
            alerted_arbs = current_keys
            with state_lock:
                state["scan_count"] += 1
                state["last_scan"] = datetime.now().strftime("%H:%M:%S")
                state["sports_count"] = len(sports)
                state["arbs"] = all_arbs
                state["total_arbs"] += len(all_arbs)
                state["total_profit"] += sum(a["arb"]["profit"] for a in all_arbs)
                state["api_remaining"] = remaining
                state["status"] = "idle"
            log.info(f"Scan #{state['scan_count']} -- {len(all_arbs)} arbs across {len(sports)} sports")
        except Exception as e:
            log.error(f"Scanner error: {e}")
            with state_lock: state["status"] = "error"
        time.sleep(SCAN_INTERVAL)

app = Flask(__name__)

@app.route("/")
def dashboard():
    return '''<!DOCTYPE html><html><head><meta charset=UTF-8><title>ARB SCANNER</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Bebas+Neue&display=swap" rel=stylesheet>
<style>:root{--g:#00ff88;--d:#00994d;--gold:#ffd700;--bg:#050a06;--s:#0a120b;--b:#1a2e1c}*{margin:0;padding:0;box-sizing:border-box}body{background:var(--bg);color:var(--g);font-family:'Share Tech Mono',monospace}header{border-bottom:1px solid var(--b);padding:16px 32px;display:flex;align-items:center;justify-content:space-between;background:var(--s);position:sticky;top:0}.logo{font-family:'Bebas Neue',sans-serif;font-size:2rem;letter-spacing:.2em}.logo span{color:var(--gold)}.sb{display:flex;gap:24px;font-size:.75rem;color:var(--d)}.sb .v{color:var(--g)}.pulse{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--g);box-shadow:0 0 8px var(--g);animation:p 1.5s ease-in-out infinite;margin-right:6px}.pulse.idle{background:var(--d);box-shadow:none;animation:none}.pulse.no_key{background:var(--gold)}@keyframes p{0%,100%{opacity:1}50%{opacity:.4}}.main{padding:24px 32px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:28px}.stat{background:var(--s);border:1px solid var(--b);padding:16px 20px;position:relative}.stat::before{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:var(--g)}.sl{font-size:.65rem;color:var(--d);letter-spacing:.15em;text-transform:uppercase}.sv{font-size:1.8rem;font-family:'Bebas Neue',sans-serif;line-height:1.1;margin-top:4px}.st{font-size:.7rem;letter-spacing:.25em;color:var(--d);text-transform:uppercase;margin-bottom:14px;border-bottom:1px solid var(--b);padding-bottom:8px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:16px}.card{background:var(--s);border:1px solid var(--b);position:relative;animation:si .4s ease-out}@keyframes si{from{opacity:0;transform:translateY(10px)}to{opacity:1}}.card.top{border-color:var(--gold);box-shadow:0 0 20px rgba(255,215,0,.1)}.card.top::after{content:'BEST';position:absolute;top:10px;right:10px;font-size:.6rem;background:var(--gold);color:#000;padding:2px 8px}.ch{padding:14px 16px;border-bottom:1px solid var(--b);display:flex;justify-content:space-between}.cs{font-size:.6rem;color:var(--d);text-transform:uppercase;margin-bottom:4px}.pp{font-family:'Bebas Neue',sans-serif;font-size:2rem;color:var(--gold);line-height:1}.cg{padding:8px 16px;font-size:.72rem;color:var(--d);border-bottom:1px solid var(--b)}.cg span{color:var(--g)}.bets{padding:12px 16px;display:flex;flex-direction:column;gap:8px}.br{display:flex;align-items:center;gap:8px;padding:8px 10px;border-left:2px solid var(--b)}.bn{font-size:.78rem;flex:1}.bst{color:var(--gold);min-width:55px;text-align:right}.bo{font-size:.75rem;color:var(--d);min-width:40px;text-align:center}.bb{font-size:.6rem;background:var(--b);padding:2px 6px;color:var(--d);text-transform:uppercase}.cf{padding:8px 16px;font-size:.6rem;color:var(--d);border-top:1px solid var(--b);display:flex;justify-content:space-between}.empty{grid-column:1/-1;text-align:center;padding:80px 20px;color:var(--d)}.big{font-family:'Bebas Neue',sans-serif;font-size:4rem;opacity:.2}.warn{background:rgba(255,215,0,.05);border:1px solid var(--gold);padding:20px;margin-bottom:24px}.warn a{color:var(--gold)}.rb{position:fixed;bottom:0;left:0;height:2px;background:var(--g);animation:rf 15s linear infinite;transform-origin:left}@keyframes rf{from{width:100%}to{width:0%}}.nb{border:1px solid var(--b);padding:14px 20px;margin-bottom:20px;font-size:.75rem;color:var(--d)}.nb span{color:var(--g)}</style></head>
<body><header><div class=logo>ARB <span>SCANNER</span></div><div class=sb><div><span class=pulse id=pulse></span><span id=st>connecting...</span></div><div>LAST SCAN <span class=v id=ls>-</span></div><div>SPORTS <span class=v id=sc>-</span></div><div>API LEFT <span class=v id=ar>-</span></div></div></header>
<div class=main><div id=wb></div><div class=nb id=nb></div><div class=stats><div class=stat><div class=sl>Scan #</div><div class=sv id=s1>0</div></div><div class=stat><div class=sl>Arbs This Scan</div><div class=sv id=s2>0</div></div><div class=stat><div class=sl>Session Arbs</div><div class=sv id=s3>0</div></div><div class=stat><div class=sl>Best Profit</div><div class=sv id=s4>-</div></div></div>
<div class=st>Live Opportunities - Ranked by Profit</div><div class=grid id=grid><div class=empty><div class=big>SCANNING</div><p>Waiting...</p></div></div></div><div class=rb></div>
<script>async function f(){try{const r=await fetch('/api/state');const d=await r.json();render(d);}catch(e){document.getElementById('st').textContent='error';}}
function render(d){document.getElementById('pulse').className='pulse '+(d.status||'');const L={scanning:'SCANNING',idle:'LIVE',error:'ERROR',no_key:'NO API KEY',starting:'STARTING'};document.getElementById('st').textContent=L[d.status]||d.status;document.getElementById('ls').textContent=d.last_scan||'-';document.getElementById('sc').textContent=d.sports_count||'-';document.getElementById('ar').textContent=d.api_remaining||'-';document.getElementById('s1').textContent=d.scan_count;document.getElementById('s2').textContent=d.arbs.length;document.getElementById('s3').textContent=d.total_arbs;document.getElementById('s4').textContent=d.arbs.length?d.arbs[0].arb.profit_pct.toFixed(1)+'%':'-';document.getElementById('nb').innerHTML='Phone alerts: subscribe to topic <span>'+d.ntfy_topic+'</span> on ntfy.sh';document.getElementById('wb').innerHTML=d.status==='no_key'?'<div class=warn>NO API KEY - add ODDS_API_KEY to Railway Variables at <a href="https://the-odds-api.com">the-odds-api.com</a></div>':'';const g=document.getElementById('grid');if(!d.arbs.length){g.innerHTML='<div class=empty><div class=big>CLEAR</div><p>No arbs right now</p></div>';return;}g.innerHTML=d.arbs.map((item,i)=>{const a=item.arb;const b=a.bets.map(x=>'<div class=br><div class=bn>'+x.name+'</div><div class=bo>@'+x.odds.toFixed(2)+'</div><div class=bst>£'+x.stake.toFixed(2)+'</div><div class=bb>'+x.bookmaker+'</div></div>').join('');return '<div class="card '+(i===0?'top':'')+'"><div class=ch><div><div class=cs>'+item.sport+'</div><div>'+item.event+'</div></div><div><div class=pp>'+a.profit_pct.toFixed(1)+'%</div><div style="font-size:.6rem;color:var(--d)">PROFIT</div></div></div><div class=cg>Stake <span>£'+a.total_stake.toFixed(2)+'</span> Return <span>£'+a.guaranteed.toFixed(2)+'</span> Profit <span>£'+a.profit.toFixed(2)+'</span></div><div class=bets>'+b+'</div><div class=cf><span>'+item.found_at+'</span><span>PLACE ALL BETS NOW</span></div></div>';}).join('');}
f();setInterval(f,15000);</script></body></html>'''

@app.route("/api/state")
def api_state():
    with state_lock:
        return jsonify({**state, "min_profit": MIN_PROFIT, "ntfy_topic": NTFY_TOPIC})

if __name__ == "__main__":
    log.info(f"Dashboard -> http://localhost:{PORT}")
    threading.Thread(target=scanner_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=False)
