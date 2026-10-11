#!/usr/bin/env python3
"""
SMC Analyzer (educational). Live Binance data -> structure, liquidity, order block, FVG,
example entry/SL/TP + lot size. Runs locally. No pip install needed (Python 3.8+).
Run:  python smc_trader.py     then open http://127.0.0.1:8000
NOT financial advice. Nothing here guarantees profit.
"""
import json, os, time, urllib.request, urllib.parse, http.server, socketserver, webbrowser

CACHE = {}  # (symbol, interval) -> (timestamp, result); protects Binance from many users

HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]

def klines(sym, iv, limit=500):
    err = None
    for h in HOSTS:
        try:
            u = f"{h}/api/v3/klines?symbol={sym}&interval={iv}&limit={limit}"
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = json.load(r)
            return [dict(t=int(x[0] // 1000), o=float(x[1]), h=float(x[2]), l=float(x[3]), c=float(x[4])) for x in raw]
        except Exception as e:
            err = e
    raise RuntimeError(f"Data fetch failed: {err}")

def swings(d, k=3):
    H, L = [], []
    for i in range(k, len(d) - k):
        if all(d[i]['h'] > d[i-j]['h'] and d[i]['h'] >= d[i+j]['h'] for j in range(1, k + 1)): H.append(i)
        if all(d[i]['l'] < d[i-j]['l'] and d[i]['l'] <= d[i+j]['l'] for j in range(1, k + 1)): L.append(i)
    return H, L

def mirror(d):
    return [dict(t=x['t'], o=-x['o'], h=-x['l'], l=-x['h'], c=-x['c']) for x in d]

def eq_levels(vals, tol):
    out = []
    for a in range(len(vals)):
        for b in range(a + 1, len(vals)):
            if abs(vals[a] - vals[b]) <= tol:
                m = (vals[a] + vals[b]) / 2
                if not any(abs(m - o) <= tol for o in out): out.append(m)
    return out[-3:]

def long_setup(d):
    """Bullish setup logic on d. Bearish setups are found by running this on mirrored data."""
    n = len(d); H, L = swings(d)
    if not L or not H: return None
    last = d[-1]['c']; atr = sum(x['h'] - x['l'] for x in d[-14:]) / 14
    a = L[-1]; i = a; lo = max(a - 4, 0)
    while i >= lo and d[i]['c'] >= d[i]['o']: i -= 1   # last bearish candle near swing low = order block
    if i < lo: i = a
    bot, top = d[i]['l'], d[i]['h']
    if last < bot: return None                          # price already below OB: invalid
    entry = top if last > top else last
    sl = min(bot, d[a]['l']) - 0.15 * atr
    if entry - sl <= 0: return None
    fv = []
    for j in range(max(0, n - 80), n - 2):
        if d[j+2]['l'] > d[j]['h'] and all(x['l'] > d[j]['h'] for x in d[j+3:]):
            fv.append((d[j+2]['l'], d[j]['h']))        # (top, bottom) unfilled bullish FVG
    fv = fv[-3:]
    fvg_in = any(f[1] <= top and f[0] >= bot for f in fv)
    lv = [d[x]['l'] for x in L[-8:] if x < n - 10]
    sweep = any(c['l'] < v and c['c'] > v for v in lv for c in d[-10:])
    above = [d[x]['h'] for x in H[-8:] if d[x]['h'] > entry]
    liq = min(above) if above else None
    risk = entry - sl
    rr_liq = (liq - entry) / risk if liq else 0
    near = (last - entry) <= 1.5 * atr
    reasons = ["Structure aligned (bullish bias)"]
    score = 1
    if sweep: score += 1; reasons.append("Sell-side liquidity swept recently (stop hunt below lows)")
    if fvg_in: score += 1; reasons.append("Unfilled FVG overlaps the order block")
    if rr_liq >= 2: score += 1; reasons.append(f"Liquidity target gives {rr_liq:.1f}R")
    if near: score += 1; reasons.append("Price is near the entry zone")
    return dict(entry=entry, sl=sl, ob=(bot, top), fvg=fv, liq=liq, rr_liq=rr_liq, score=score, reasons=reasons)

def analyze(d):
    if len(d) < 60: return {"error": "Not enough candles"}
    last = d[-1]['c']; H, L = swings(d)
    if len(H) < 2 or len(L) < 2: return {"error": "Not enough swings"}
    sh, sl_ = d[H[-1]]['h'], d[L[-1]]['l']
    up = d[H[-1]]['h'] > d[H[-2]]['h'] and d[L[-1]]['l'] > d[L[-2]]['l']
    dn = d[H[-1]]['h'] < d[H[-2]]['h'] and d[L[-1]]['l'] < d[L[-2]]['l']
    bias = "bull" if up else "bear" if dn else "range"
    brk = None
    if last > sh: brk = "Bullish BOS" if bias == "bull" else "Bullish CHoCH"; bias = "bull"
    elif last < sl_: brk = "Bearish BOS" if bias == "bear" else "Bearish CHoCH"; bias = "bear"
    tol = last * 0.001
    out = dict(
        candles=d, bias=bias, brk=brk, last=last,
        sh=[dict(t=d[i]['t'], p=d[i]['h']) for i in H[-10:]],
        sl=[dict(t=d[i]['t'], p=d[i]['l']) for i in L[-10:]],
        eqh=eq_levels([d[i]['h'] for i in H[-10:]], tol),
        eql=eq_levels([d[i]['l'] for i in L[-10:]], tol),
        setup=None)
    if bias == "range": return out
    s = long_setup(d) if bias == "bull" else long_setup(mirror(d))
    if not s: return out
    if bias == "bear":   # un-mirror back to real prices
        s['entry'], s['sl'] = -s['entry'], -s['sl']
        s['ob'] = (-s['ob'][1], -s['ob'][0])
        s['fvg'] = [(-f[1], -f[0]) for f in s['fvg']]
        s['liq'] = -s['liq'] if s['liq'] else None
    sign = 1 if bias == "bull" else -1
    r = abs(s['entry'] - s['sl'])
    out['setup'] = dict(
        side="LONG" if sign == 1 else "SHORT", entry=s['entry'], sl=s['sl'],
        tp1=s['entry'] + sign * r, tp2=s['entry'] + sign * 2 * r, tp3=s['entry'] + sign * 3 * r,
        trail=s['entry'] + sign * 1.1 * r, liq=s['liq'], rr_liq=s['rr_liq'],
        score=s['score'], reasons=s['reasons'],
        ob=dict(top=s['ob'][1], bot=s['ob'][0]), fvg=[dict(top=f[0], bot=f[1]) for f in s['fvg']])
    return out

HTML = r"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SMC Analyzer</title>
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
body{margin:0;background:#0e1116;color:#e8eaee;font:14px/1.45 system-ui,sans-serif}
main{max-width:1100px;margin:auto;padding:10px}
.bar{display:flex;flex-wrap:wrap;gap:6px;align-items:end;margin-bottom:8px}
.bar label{font-size:11px;color:#9aa3b2;display:block}
select,input{background:#171b22;color:#e8eaee;border:1px solid #262c36;border-radius:6px;padding:7px;font-size:14px;width:90px}
button{background:#262c36;color:#e8eaee;border:0;border-radius:6px;padding:8px 11px;cursor:pointer}
button.on{background:#2563eb}
#c{height:460px;border:1px solid #262c36;border-radius:8px}
.card{background:#171b22;border:1px solid #262c36;border-radius:10px;padding:10px;margin-top:10px}
.w{background:#2a2210;border:1px solid #d97706;border-radius:8px;padding:8px;font-size:12.5px;margin-bottom:8px}
.g{color:#34d399}.r{color:#f87171}.a{color:#fbbf24}.m{color:#9aa3b2}
table{width:100%;border-collapse:collapse}td{padding:3px 0;border-bottom:1px solid #262c36}td:last-child{text-align:right;font-weight:600}
</style></head><body><main>
<div class="w"><b>Educational tool, not financial advice.</b> Rule-based reading of structure and liquidity. No setup is guaranteed; losses happen. Trade at your own risk.</div>
<div class="bar">
<div><label>Symbol</label><select id="sym" style="width:150px"><option value="BTCUSDT">BTC/USDT</option><option value="ETHUSDT">ETH/USDT</option><option value="PAXGUSDT">Gold (PAXG)</option></select></div>
<div id="tfs"></div>
<div><label>Balance $</label><input id="bal" type="number" value="100"></div>
<div><label>Risk %</label><input id="rp" type="number" value="10"></div>
<span id="st" class="m"></span>
</div>
<div id="c"></div>
<div class="card" id="p"></div>
</main>
<script>
const $=id=>document.getElementById(id);let tf='15m',chart,ser,lines=[],fit=true,A=null;
['5m','15m','1h','4h','1d'].forEach(t=>{const b=document.createElement('button');b.textContent=t;b.id='tf'+t;b.onclick=()=>{tf=t;fit=true;mark();load()};$('tfs').appendChild(b)});
function mark(){['5m','15m','1h','4h','1d'].forEach(t=>$('tf'+t).className=t===tf?'on':'')}
const f=n=>n==null?'-':(+n).toLocaleString(undefined,{maximumFractionDigits:n>1000?1:3});
chart=LightweightCharts.createChart($('c'),{autoSize:true,layout:{background:{color:'#0e1116'},textColor:'#9aa3b2'},grid:{vertLines:{color:'#1c222b'},horzLines:{color:'#1c222b'}},timeScale:{timeVisible:true}});
ser=chart.addCandlestickSeries({upColor:'#16a34a',downColor:'#dc2626',wickUpColor:'#16a34a',wickDownColor:'#dc2626',borderVisible:false});
function pl(p,color,title,st){lines.push(ser.createPriceLine({price:p,color,lineWidth:1,lineStyle:st??0,axisLabelVisible:true,title}))}
function draw(a){
 ser.setData(a.candles.map(x=>({time:x.t,open:x.o,high:x.h,low:x.l,close:x.c})));
 ser.setMarkers([...a.sh.map(x=>({time:x.t,position:'aboveBar',color:'#d97706',shape:'circle',text:'SH'})),...a.sl.map(x=>({time:x.t,position:'belowBar',color:'#2563eb',shape:'circle',text:'SL'}))].sort((x,y)=>x.time-y.time));
 lines.forEach(l=>ser.removePriceLine(l));lines=[];
 a.eqh.forEach(v=>pl(v,'#d97706','EQH liq',2));a.eql.forEach(v=>pl(v,'#60a5fa','EQL liq',2));
 const s=a.setup;
 if(s){pl(s.ob.top,'#a78bfa','OB top',1);pl(s.ob.bot,'#a78bfa','OB bot',1);
  s.fvg.forEach(z=>{pl(z.top,'#6b7280','FVG',3);pl(z.bot,'#6b7280','',3)});
  pl(s.entry,'#60a5fa','ENTRY',0);pl(s.sl,'#f87171','SL',0);pl(s.tp1,'#34d399','TP 1:1',2);pl(s.tp2,'#34d399','TP 1:2',0);pl(s.tp3,'#34d399','TP 1:3',2)}
 if(fit){chart.timeScale().fitContent();fit=false}
}
function panel(){
 const a=A;if(!a)return;const bal=+$('bal').value,rp=+$('rp').value,s=a.setup;
 let h=`<table><tr><td>Bias</td><td class="${a.bias=='bull'?'g':a.bias=='bear'?'r':'a'}">${a.bias.toUpperCase()}</td></tr><tr><td>Last break</td><td>${a.brk||'none fresh'}</td></tr><tr><td>Price</td><td>${f(a.last)}</td></tr></table>`;
 if(!s){h+='<p><b class="a">No clean setup right now.</b> Waiting is a valid decision.</p>'}
 else{
  const risk=bal*rp/100,dist=Math.abs(s.entry-s.sl),u=risk/dist,notional=u*s.entry,lev=notional/bal;
  const grade=s.score>=4?['A: strong confluence','g']:s.score==3?['B: average','a']:['C: weak, better to skip','r'];
  h+=`<h3 style="margin:10px 0 4px">Example ${s.side} setup <span class="${grade[1]}">${grade[0]} (${s.score}/5)</span></h3><table>
  <tr><td>Entry</td><td>${f(s.entry)}</td></tr><tr><td>Stop loss</td><td class="r">${f(s.sl)}</td></tr>
  <tr><td>TP 1:1 / 1:2 / 1:3</td><td class="g">${f(s.tp1)} / ${f(s.tp2)} / ${f(s.tp3)}</td></tr>
  <tr><td>Liquidity target</td><td>${s.liq?f(s.liq)+' ('+s.rr_liq.toFixed(1)+'R)':'none'}</td></tr>
  <tr><td>Move SL to breakeven at (1.1R)</td><td>${f(s.trail)}</td></tr>
  <tr><td>Risk amount</td><td>$${f(risk)}</td></tr><tr><td>Position size (units)</td><td>${u.toFixed(5)}</td></tr>
  <tr><td>Position value / leverage needed</td><td>$${f(notional)} / ${lev.toFixed(1)}x</td></tr>
  <tr><td>Profit at 1:2</td><td class="g">$${f(risk*2)}</td></tr></table>
  <p class="m">Why: ${s.reasons.join(' · ')}</p>
  <p class="m">Entry is a limit at the order block. If price never returns to it, there is no trade. If price closes beyond the SL, the idea is invalid.</p>`}
 if(rp>=5)h+=`<div class="w" style="margin-top:8px"><b>High risk:</b> ${rp}% per trade. 5 losses in a row = about -${((1-Math.pow(1-rp/100,5))*100).toFixed(0)}% of the account.</div>`;
 $('p').innerHTML=h}
async function load(){
 $('st').textContent='updating...';
 try{const r=await fetch(`/api/analyze?symbol=${$('sym').value}&interval=${tf}`);const a=await r.json();if(a.error)throw a.error;A=a;draw(a);panel();$('st').textContent='live '+new Date().toLocaleTimeString()}
 catch(e){$('st').textContent='Error: '+e}
}
$('sym').onchange=()=>{fit=true;load()};['bal','rp'].forEach(i=>$(i).oninput=panel);
mark();load();setInterval(load,15000);
</script></body></html>"""

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype):
        b = body.encode(); self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/": return self._send(200, HTML, "text/html; charset=utf-8")
        if u.path == "/api/analyze":
            q = urllib.parse.parse_qs(u.query)
            sym = q.get("symbol", ["BTCUSDT"])[0].upper(); iv = q.get("interval", ["15m"])[0]
            try:
                if sym not in ("BTCUSDT", "ETHUSDT", "PAXGUSDT") or iv not in ("1m","5m","15m","1h","4h","1d"):
                    raise ValueError("Unsupported symbol/interval")
                hit = CACHE.get((sym, iv))
                if hit and time.time() - hit[0] < 10:
                    res = hit[1]
                else:
                    res = analyze(klines(sym, iv))
                    CACHE[(sym, iv)] = (time.time(), res)
            except Exception as e:
                res = {"error": str(e)}
            return self._send(200, json.dumps(res), "application/json")
        self._send(404, "not found", "text/plain")

if __name__ == "__main__":
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    port = int(os.environ.get("PORT", "8000"))
    online = "PORT" in os.environ          # hosting platforms set PORT
    with socketserver.ThreadingTCPServer(("0.0.0.0" if online else "127.0.0.1", port), H) as srv:
        print(f"Running on port {port}  (Ctrl+C to stop)")
        if not online:
            try: webbrowser.open(f"http://127.0.0.1:{port}")
            except Exception: pass
        srv.serve_forever()
