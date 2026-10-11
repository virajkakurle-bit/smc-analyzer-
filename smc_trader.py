#!/usr/bin/env python3
"""
SMC Lab (educational). Live data -> structure, liquidity, order blocks, FVG, buy/sell zones,
volume profile, order flow (buy vs sell volume). Standard library only (Python 3.8+).
Run: python smc_trader.py -> open http://127.0.0.1:8000
NOT financial advice. Nothing here guarantees profit.
"""
import json, os, time, urllib.request, urllib.parse, http.server, socketserver, webbrowser

CACHE, BAD = {}, {}
TTL = 4  # seconds
IVS = ("1m", "5m", "15m", "1h", "4h", "1d")

def _get(u, t=10):
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.load(r)

def mk(t, o, h, l, c, v, b=None):
    v = float(v or 0)
    if b is None:
        rng = h - l
        b = v * ((c - l) / rng if rng > 0 else 0.5)   # estimate: close position in range
    return dict(t=int(t), o=float(o), h=float(h), l=float(l), c=float(c), v=v, b=float(b))

# ---------- data sources: each returns (candles, exact_buy_sell_split) ----------
def _binance(host):
    def f(sym, iv):
        raw = _get(f"{host}/api/v3/klines?symbol={sym}&interval={iv}&limit=500")
        return [mk(x[0] // 1000, x[1], x[2], x[3], x[4], x[5], float(x[9])) for x in raw], True
    return f

def _bybit(sym, iv):
    m = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}[iv]
    raw = _get(f"https://api.bybit.com/v5/market/kline?category=spot&symbol={sym}&interval={m}&limit=500")["result"]["list"]
    return [mk(int(x[0]) // 1000, x[1], x[2], x[3], x[4], x[5]) for x in reversed(raw)], False

def _okx(sym, iv):
    m = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}[iv]
    raw = _get(f"https://www.okx.com/api/v5/market/candles?instId={sym.replace('USDT', '-USDT')}&bar={m}&limit=300")["data"]
    return [mk(int(x[0]) // 1000, x[1], x[2], x[3], x[4], x[5]) for x in reversed(raw)], False

def _kraken(sym, iv):
    m = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}[iv]
    pair = {"BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD", "PAXGUSDT": "PAXGUSD"}[sym]
    res = _get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={m}")["result"]
    raw = next(v for k, v in res.items() if k != "last")
    return [mk(x[0], x[1], x[2], x[3], x[4], x[6]) for x in raw], False

def _yahoo(sym, iv):
    tk = {"XAUUSD": "XAUUSD=X", "GC": "GC=F"}[sym]
    cfg = {"1m": ("1m", "5d"), "5m": ("5m", "1mo"), "15m": ("15m", "1mo"),
           "1h": ("60m", "6mo"), "4h": ("60m", "6mo"), "1d": ("1d", "2y")}[iv]
    j = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(tk)}?interval={cfg[0]}&range={cfg[1]}")
    r = j["chart"]["result"][0]; q = r["indicators"]["quote"][0]; rows = []
    for i, t in enumerate(r["timestamp"]):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c): continue
        rows.append([t, o, h, l, c, q["volume"][i] or 0])
    if iv == "4h":
        g = {}
        for t, o, h, l, c, v in rows:
            k = t - t % 14400
            if k in g: g[k][2] = max(g[k][2], h); g[k][3] = min(g[k][3], l); g[k][4] = c; g[k][5] += v
            else: g[k] = [k, o, h, l, c, v]
        rows = [g[k] for k in sorted(g)]
    return [mk(*x) for x in rows[-500:]], False

SRC = {"binance": _binance("https://api.binance.com"), "binance-vision": _binance("https://data-api.binance.vision"),
       "bybit": _bybit, "okx": _okx, "kraken": _kraken, "yahoo": _yahoo}
CRYPTO = ["binance", "binance-vision", "bybit", "okx", "kraken"]
ROUTE = {"BTCUSDT": CRYPTO, "ETHUSDT": CRYPTO, "PAXGUSDT": CRYPTO, "XAUUSD": ["yahoo"], "GC": ["yahoo"]}

def fetch(sym, iv):
    errs = []
    for name in ROUTE[sym]:
        if time.time() - BAD.get((name, sym), 0) < 600: continue
        try:
            d, exact = SRC[name](sym, iv)
            if len(d) < 60: raise ValueError("too few candles")
            novol = sum(x['v'] for x in d) == 0
            if novol:   # spot FX has no volume: use time-at-price (1 unit per candle)
                for x in d:
                    rng = x['h'] - x['l']; x['v'] = 1.0; x['b'] = (x['c'] - x['l']) / rng if rng > 0 else 0.5
            return d, exact, name, novol
        except Exception as e:
            BAD[(name, sym)] = time.time(); errs.append(f"{name}: {e}")
    for k in [k for k in BAD if k[1] == sym]: BAD.pop(k)
    raise RuntimeError("All data sources failed -> " + " | ".join(errs))

# ---------- SMC logic ----------
def swings(d, k=3):
    H, L = [], []
    for i in range(k, len(d) - k):
        if all(d[i]['h'] > d[i-j]['h'] and d[i]['h'] >= d[i+j]['h'] for j in range(1, k + 1)): H.append(i)
        if all(d[i]['l'] < d[i-j]['l'] and d[i]['l'] <= d[i+j]['l'] for j in range(1, k + 1)): L.append(i)
    return H, L

def mirror(d):
    return [dict(t=x['t'], o=-x['o'], h=-x['l'], l=-x['h'], c=-x['c'], v=x['v'], b=x['v'] - x['b']) for x in d]

def eq_levels(vals, tol):
    out = []
    for a in range(len(vals)):
        for b in range(a + 1, len(vals)):
            if abs(vals[a] - vals[b]) <= tol:
                m = (vals[a] + vals[b]) / 2
                if not any(abs(m - o) <= tol for o in out): out.append(m)
    return out[-3:]

def ob_bull(d):
    H, L = swings(d)
    if not L: return None
    a = L[-1]; lo = max(a - 4, 0); i = a
    while i >= lo and d[i]['c'] >= d[i]['o']: i -= 1   # last bearish candle near swing low
    if i < lo: i = a
    return dict(bot=d[i]['l'], top=d[i]['h'], t=d[i]['t'], a=a)

def bull_fvgs(d):
    n = len(d); fv = []
    for j in range(max(0, n - 80), n - 2):
        if d[j+2]['l'] > d[j]['h'] and all(x['l'] > d[j]['h'] for x in d[j+3:]):
            fv.append((d[j+2]['l'], d[j]['h'], d[j]['t']))     # top, bottom, time
    return fv[-3:]

def long_setup(d, aligned):
    n = len(d); H, L = swings(d)
    ob = ob_bull(d)
    if not L or not H or not ob: return None
    last = d[-1]['c']; atr = sum(x['h'] - x['l'] for x in d[-14:]) / 14
    bot, top = ob['bot'], ob['top']
    if last < bot: return None                       # price already below the zone: invalid
    entry = top if last > top else last
    sl = min(bot, d[ob['a']]['l']) - 0.15 * atr
    if entry - sl <= 0: return None
    fvg_in = any(f[1] <= top and f[0] >= bot for f in bull_fvgs(d))
    lv = [d[x]['l'] for x in L[-8:] if x < n - 10]
    sweep = any(c['l'] < v and c['c'] > v for v in lv for c in d[-10:])
    above = [d[x]['h'] for x in H[-8:] if d[x]['h'] > entry]
    liq = min(above) if above else None
    risk = entry - sl; rr_liq = (liq - entry) / risk if liq else 0
    reasons, score = [], 0
    if aligned: score += 1; reasons.append("Trend/structure agrees")
    if sweep: score += 1; reasons.append("Liquidity below was swept (stop hunt)")
    if fvg_in: score += 1; reasons.append("FVG overlaps the zone")
    if rr_liq >= 2: score += 1; reasons.append(f"Liquidity target gives {rr_liq:.1f}R")
    if last - entry <= 1.5 * atr: score += 1; reasons.append("Price is near the zone")
    return dict(entry=entry, sl=sl, ob=(bot, top, ob['t']), liq=liq, rr_liq=rr_liq, score=score, reasons=reasons)

def side_plan(d, sign, aligned):
    s = long_setup(d if sign == 1 else mirror(d), aligned)
    if not s: return None
    e, sl, (b, t_, ot), liq = s['entry'], s['sl'], s['ob'], s['liq']
    if sign == -1: e, sl, b, t_, liq = -e, -sl, -t_, -b, (-liq if liq else None)
    r = abs(e - sl)
    return dict(side="BUY" if sign == 1 else "SELL", aligned=aligned, entry=e, sl=sl,
                tp1=e + sign * r, tp2=e + sign * 2 * r, tp3=e + sign * 3 * r, trail=e + sign * 1.1 * r,
                liq=liq, rr_liq=s['rr_liq'], score=s['score'], reasons=s['reasons'], ob=dict(top=t_, bot=b, t=ot))

def vprofile(d, bins=48):
    lo = min(x['l'] for x in d); hi = max(x['h'] for x in d)
    step = (hi - lo) / bins or 1.0
    B, S = [0.0] * bins, [0.0] * bins
    for x in d:
        i0 = min(int((x['l'] - lo) / step), bins - 1); i1 = min(int((x['h'] - lo) / step), bins - 1)
        n = i1 - i0 + 1; b = x['b'] / n; s = (x['v'] - x['b']) / n
        for i in range(i0, i1 + 1): B[i] += b; S[i] += s
    tot = [B[i] + S[i] for i in range(bins)]
    poc = max(range(bins), key=lambda i: tot[i]); total = sum(tot); acc = tot[poc]; l = h = poc
    while acc < 0.7 * total and (l > 0 or h < bins - 1):
        up = tot[h+1] if h < bins - 1 else -1; dn = tot[l-1] if l > 0 else -1
        if up >= dn: h += 1; acc += up
        else: l -= 1; acc += dn
    return dict(step=step, poc=lo + step * (poc + .5), vah=lo + step * (h + 1), val=lo + step * l,
                bins=[dict(p=lo + step * (i + .5), b=B[i], s=S[i]) for i in range(bins)])

def analyze(d, exact=False, source="", novol=False):
    if len(d) < 60: return {"error": "Not enough candles"}
    last = d[-1]['c']; H, L = swings(d)
    if len(H) < 2 or len(L) < 2: return {"error": "Not enough swings"}
    sh, sl_ = d[H[-1]]['h'], d[L[-1]]['l']
    up = d[H[-1]]['h'] > d[H[-2]]['h'] and d[L[-1]]['l'] > d[L[-2]]['l']
    dn = d[H[-1]]['h'] < d[H[-2]]['h'] and d[L[-1]]['l'] < d[L[-2]]['l']
    bias = "bull" if up else "bear" if dn else "range"; brk = None
    if last > sh: brk = "Bullish BOS" if bias == "bull" else "Bullish CHoCH"; bias = "bull"
    elif last < sl_: brk = "Bearish BOS" if bias == "bear" else "Bearish CHoCH"; bias = "bear"
    tol = last * 0.001
    ob_b = ob_bull(d); ob_s = ob_bull(mirror(d))
    fb = bull_fvgs(d); fs = bull_fvgs(mirror(d))
    obs = {}
    if ob_b: obs['buy'] = dict(top=ob_b['top'], bot=ob_b['bot'], t=ob_b['t'])
    if ob_s: obs['sell'] = dict(top=-ob_s['bot'], bot=-ob_s['top'], t=ob_s['t'])
    top_vol = sorted(d[-200:], key=lambda x: -x['v'])[:10]
    w = d[-100:]; wb = sum(x['b'] for x in w); wv = sum(x['v'] for x in w)
    return dict(
        candles=d, bias=bias, brk=brk, last=last, source=source, exact=exact, novol=novol,
        sh=[dict(t=d[i]['t'], p=d[i]['h']) for i in H[-10:]], sl=[dict(t=d[i]['t'], p=d[i]['l']) for i in L[-10:]],
        last_sh=sh, last_sl=sl_,
        eqh=eq_levels([d[i]['h'] for i in H[-10:]], tol), eql=eq_levels([d[i]['l'] for i in L[-10:]], tol),
        obs=obs,
        fvgs=[dict(top=f[0], bot=f[1], t=f[2], type="bull") for f in fb] + [dict(top=-f[1], bot=-f[0], t=f[2], type="bear") for f in fs],
        buy=side_plan(d, 1, bias == "bull"), sell=side_plan(d, -1, bias == "bear"),
        vp=vprofile(d),
        flow=dict(buy=wb, sell=wv - wb, n=len(w),
                  bubbles=[dict(t=x['t'], p=(x['h'] + x['l']) / 2, v=x['v'], b=x['b']) for x in top_vol]))

HTML = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SMC Lab ⚡</title>
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
*{box-sizing:border-box}
body{margin:0;color:#e8eaf2;font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:radial-gradient(1200px 600px at 10% -10%,#2a1457 0,transparent 60%),radial-gradient(900px 500px at 100% 0,#06324a 0,transparent 55%),#080a10;min-height:100vh}
main{max-width:1500px;margin:auto;padding:10px}
header{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:8px}
.logo{font-size:22px;font-weight:800;letter-spacing:-.5px}.logo b{background:linear-gradient(90deg,#a78bfa,#22d3ee);-webkit-background-clip:text;background-clip:text;color:transparent}
.pills{display:flex;gap:6px;overflow-x:auto;max-width:100%;padding:2px}
.pill{border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.05);color:#cbd2e4;border-radius:999px;padding:7px 13px;cursor:pointer;font-size:13px;white-space:nowrap;transition:.15s}
.pill:hover{transform:translateY(-1px);border-color:#7c3aed}
.pill.on{background:linear-gradient(135deg,#7c3aed,#06b6d4);color:#fff;border-color:transparent;box-shadow:0 4px 18px rgba(124,58,237,.4)}
.warn{font-size:12px;color:#fcd34d;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.35);border-radius:10px;padding:6px 10px;margin-bottom:8px}
.chips{display:flex;gap:7px;overflow-x:auto;padding:2px 2px 8px}
.chip{border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.04);border-radius:12px;padding:7px 12px;cursor:pointer;white-space:nowrap;font-size:13px;user-select:none;transition:.15s}
.chip.on{background:rgba(34,211,238,.15);border-color:#22d3ee;color:#fff;box-shadow:0 0 14px rgba(34,211,238,.35)}
#wrap{position:relative;height:calc(100vh - 175px);min-height:480px;border-radius:16px;overflow:hidden;border:1px solid rgba(255,255,255,.09);background:rgba(10,12,20,.75);backdrop-filter:blur(8px)}
#c,#ov{position:absolute;inset:0}#ov{pointer-events:none;z-index:2}
#badge{position:absolute;left:12px;top:10px;z-index:4;font-size:13px;display:flex;gap:8px;align-items:center;pointer-events:none}
#badge .px{font-size:20px;font-weight:700}
.tag{padding:2px 8px;border-radius:8px;font-size:11px;font-weight:700}
.bull{background:rgba(52,211,153,.2);color:#34d399}.bear{background:rgba(248,113,113,.2);color:#f87171}.rng{background:rgba(251,191,36,.2);color:#fbbf24}
#tip{position:absolute;z-index:5;display:none;background:rgba(15,18,30,.95);border:1px solid rgba(255,255,255,.15);border-radius:10px;padding:8px 10px;font-size:12px;pointer-events:none;min-width:170px}
#load{position:absolute;inset:0;z-index:6;display:flex;align-items:center;justify-content:center;background:rgba(8,10,16,.8);font-size:15px;text-align:center;padding:20px}
#fs{position:absolute;right:70px;top:8px;z-index:4;border:0;background:rgba(255,255,255,.1);color:#fff;border-radius:8px;padding:5px 9px;cursor:pointer}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:10px;margin-top:10px}
.card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.09);border-radius:14px;padding:12px;backdrop-filter:blur(8px)}
.card.sel{border-color:#22d3ee;box-shadow:0 0 18px rgba(34,211,238,.25)}
.card h3{margin:0 0 6px;font-size:15px}
.card table{width:100%;border-collapse:collapse}.card td{padding:3px 0;border-bottom:1px solid rgba(255,255,255,.06)}.card td:last-child{text-align:right;font-weight:600}
.g{color:#34d399}.r{color:#f87171}.a{color:#fbbf24}.m{color:#8b93a7}.cy{color:#22d3ee}
.in{display:flex;gap:8px;margin-bottom:6px}.in div{flex:1}.in label{font-size:11px;color:#8b93a7;display:block}
input{width:100%;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);color:#fff;border-radius:8px;padding:7px;font-size:15px}
small{color:#8b93a7}
.spin{width:22px;height:22px;border:3px solid rgba(255,255,255,.2);border-top-color:#22d3ee;border-radius:50%;animation:s 1s linear infinite;margin:0 auto 8px}@keyframes s{to{transform:rotate(360deg)}}
</style></head><body><main>
<header><div class="logo">SMC<b>Lab</b> ⚡</div><div class="pills" id="syms"></div><div class="pills" id="tfs"></div></header>
<div class="warn">⚠ Educational tool, not financial advice. Rule-based reading; no setup is guaranteed and losses happen. Trade at your own risk.</div>
<div class="chips" id="chips"></div>
<div id="wrap"><div id="c"></div><canvas id="ov"></canvas>
<div id="badge"></div><button id="fs" title="Fullscreen">⛶</button><div id="tip"></div>
<div id="load"><div><div class="spin"></div><span id="lt">Loading live data...</span></div></div></div>
<div class="grid" id="cards"></div>
<div class="m" style="text-align:center;margin:14px 0 24px;font-size:12px" id="st"></div>
</main>
<script>
const $=id=>document.getElementById(id);
const SYMS=[['BTCUSDT','₿ BTC'],['ETHUSDT','Ξ ETH'],['XAUUSD','🥇 XAU/USD'],['GC','🥇 Gold Fut (vol)'],['PAXGUSDT','🪙 PAXG']];
const TFS=['1m','5m','15m','1h','4h','1d'];
const CH=[['zones','🟢🔴 Buy/Sell Zones'],['plan','🎯 Trade Plan'],['struct','🧱 Structure'],['liq','💧 Liquidity'],['ob','🟪 Order Blocks'],['fvg','⬜ FVG'],['vol','📊 Volume'],['vp','🧬 Best Volume (Profile)'],['flow','⚡ Orders Executed']];
let sym='BTCUSDT',tf='15m',A=null,fit=true,sel='auto',lines=[],ws=null,CM={},busy=false;
const L={zones:1,plan:1,struct:0,liq:0,ob:0,fvg:0,vol:1,vp:0,flow:0};
const f=n=>n==null?'-':(+n).toLocaleString(undefined,{maximumFractionDigits:Math.abs(n)>=1000?2:4});
const fv=v=>v>=1e6?(v/1e6).toFixed(1)+'M':v>=1e3?(v/1e3).toFixed(1)+'K':v.toFixed(v<10?2:0);
function pills(){
 $('syms').innerHTML='';SYMS.forEach(([k,n])=>{const b=document.createElement('div');b.className='pill'+(k===sym?' on':'');b.textContent=n;b.onclick=()=>{sym=k;reset()};$('syms').appendChild(b)});
 $('tfs').innerHTML='';TFS.forEach(k=>{const b=document.createElement('div');b.className='pill'+(k===tf?' on':'');b.textContent=k;b.onclick=()=>{tf=k;reset()};$('tfs').appendChild(b)});
 $('chips').innerHTML='';CH.forEach(([k,n])=>{const b=document.createElement('div');b.className='chip'+(L[k]?' on':'');b.textContent=n;b.onclick=()=>{L[k]=L[k]?0:1;pills();applyLayers();cards()};$('chips').appendChild(b)});
}
const chart=LightweightCharts.createChart($('c'),{autoSize:true,layout:{background:{color:'rgba(0,0,0,0)'},textColor:'#8b93a7'},grid:{vertLines:{color:'rgba(255,255,255,.04)'},horzLines:{color:'rgba(255,255,255,.04)'}},timeScale:{timeVisible:true,rightOffset:8,borderColor:'rgba(255,255,255,.08)'},rightPriceScale:{borderColor:'rgba(255,255,255,.08)',scaleMargins:{top:.08,bottom:.2}},crosshair:{mode:0}});
const ser=chart.addCandlestickSeries({upColor:'#22c55e',downColor:'#ef4444',wickUpColor:'#22c55e',wickDownColor:'#ef4444',borderVisible:false});
const vs=chart.addHistogramSeries({priceFormat:{type:'volume'},priceScaleId:'vol'});
chart.priceScale('vol').applyOptions({scaleMargins:{top:.86,bottom:0}});
function pl(p,color,title,st,w){lines.push(ser.createPriceLine({price:p,color,lineWidth:w||1,lineStyle:st??2,axisLabelVisible:true,title}))}
function curPlan(){if(!A)return null;if(sel==='buy')return A.buy;if(sel==='sell')return A.sell;return A.bias==='bull'?A.buy:A.bias==='bear'?A.sell:null}
function draw(){
 ser.setData(A.candles.map(x=>({time:x.t,open:x.o,high:x.h,low:x.l,close:x.c})));
 vs.setData(A.novol?[]:A.candles.map(x=>({time:x.t,value:x.v,color:x.b>=x.v-x.b?'rgba(34,197,94,.45)':'rgba(239,68,68,.45)'})));
 if(fit){chart.timeScale().fitContent();fit=false}
 applyLayers();cards();badge();connectWS();
}
function applyLayers(){
 if(!A)return;
 lines.forEach(l=>ser.removePriceLine(l));lines=[];
 vs.applyOptions({visible:!!L.vol&&!A.novol});
 const mk=[];
 if(L.struct){A.sh.forEach(x=>mk.push({time:x.t,position:'aboveBar',color:'#f59e0b',shape:'circle',text:'SH'}));A.sl.forEach(x=>mk.push({time:x.t,position:'belowBar',color:'#38bdf8',shape:'circle',text:'SL'}));pl(A.last_sh,'#9ca3af','Swing High',3);pl(A.last_sl,'#9ca3af','Swing Low',3)}
 ser.setMarkers(mk.sort((a,b)=>a.time-b.time));
 if(L.liq){A.eqh.forEach(v=>pl(v,'#f59e0b','EQH 💧',2));A.eql.forEach(v=>pl(v,'#38bdf8','EQL 💧',2))}
 const p=curPlan();
 if(L.plan&&p){pl(p.entry,'#60a5fa','ENTRY',0,2);pl(p.sl,'#f87171','SL',0,2);pl(p.tp1,'#34d399','TP 1:1',2);pl(p.tp2,'#34d399','TP 1:2',0,2);pl(p.tp3,'#34d399','TP 1:3',2)}
 if(L.vp&&A.vp){pl(A.vp.poc,'#fde047','POC',0,2);pl(A.vp.vah,'#fde047','VAH',3);pl(A.vp.val,'#fde047','VAL',3)}
}
function badge(){const b=A.bias;$('badge').innerHTML=`<span>${SYMS.find(s=>s[0]===sym)[1]}</span><span class="px">${f(A.last)}</span><span class="tag ${b==='bull'?'bull':b==='bear'?'bear':'rng'}">${b==='bull'?'BULLISH':b==='bear'?'BEARISH':'RANGE'}</span>${A.brk?`<span class="tag rng">${A.brk}</span>`:''}`}
// ---------- overlay: zones, FVG, OB, volume profile, order flow ----------
const ov=$('ov'),cx=ov.getContext('2d');
function frame(){try{drawOv()}catch(e){}requestAnimationFrame(frame)}
function drawOv(){
 const w=$('wrap'),d=devicePixelRatio||1,W=w.clientWidth,H=w.clientHeight;
 if(ov.width!==Math.round(W*d)||ov.height!==Math.round(H*d)){ov.width=Math.round(W*d);ov.height=Math.round(H*d)}
 cx.setTransform(d,0,0,d,0,0);cx.clearRect(0,0,W,H);if(!A)return;
 let R=W-62;try{R=W-chart.priceScale('right').width()}catch(e){}
 const ts=chart.timeScale(),X=t=>{const x=ts.timeToCoordinate(t);return x==null?0:x},Y=p=>ser.priceToCoordinate(p);
 const box=(top,bot,t,fill,stroke,label,dash,lab2)=>{const y1=Y(top),y2=Y(bot);if(y1==null||y2==null)return;const x1=Math.max(0,X(t)),yt=Math.min(y1,y2),h=Math.max(Math.abs(y2-y1),2);
  cx.fillStyle=fill;cx.fillRect(x1,yt,R-x1,h);cx.strokeStyle=stroke;cx.lineWidth=1;cx.setLineDash(dash?[5,4]:[]);cx.strokeRect(x1,yt,R-x1,h);cx.setLineDash([]);
  cx.fillStyle=stroke;cx.font='bold 12px system-ui';cx.fillText(label,x1+8,lab2?yt+h-6:yt+14)};
 if(L.fvg)A.fvgs.forEach(z=>box(z.top,z.bot,z.t,'rgba(148,163,184,.13)','#94a3b8','FVG '+(z.type==='bull'?'▲':'▼'),1));
 if(L.ob){if(A.obs.buy)box(A.obs.buy.top,A.obs.buy.bot,A.obs.buy.t,'rgba(167,139,250,.14)','#a78bfa','OB (bull)',1,1);if(A.obs.sell)box(A.obs.sell.top,A.obs.sell.bot,A.obs.sell.t,'rgba(167,139,250,.14)','#a78bfa','OB (bear)',1,1)}
 if(L.zones){[['buy','rgba(34,197,94,.20)','#22c55e','BUY ZONE ▲'],['sell','rgba(239,68,68,.20)','#ef4444','SELL ZONE ▼']].forEach(([k,fl,st,lb])=>{const p=A[k];if(p)box(p.ob.top,p.ob.bot,p.ob.t,fl,st,lb+(p.aligned?'  ★ with trend':'  (counter-trend)'))})}
 if(L.vp&&A.vp){const mx=Math.max(...A.vp.bins.map(b=>b.b+b.s))||1,mw=(R)*.22;
  A.vp.bins.forEach(b=>{const y1=Y(b.p+A.vp.step/2),y2=Y(b.p-A.vp.step/2);if(y1==null||y2==null)return;const h=Math.max(Math.abs(y2-y1)-1,1),yt=Math.min(y1,y2),wb=mw*b.b/mx,ws_=mw*b.s/mx;
   cx.fillStyle='rgba(239,68,68,.45)';cx.fillRect(R-ws_,yt,ws_,h);cx.fillStyle='rgba(34,197,94,.45)';cx.fillRect(R-ws_-wb,yt,wb,h);
   if(Math.abs(b.p-A.vp.poc)<1e-9){cx.strokeStyle='#fde047';cx.lineWidth=1.5;cx.strokeRect(R-ws_-wb,yt,wb+ws_,h);cx.fillStyle='#fde047';cx.font='bold 12px system-ui';cx.fillText('POC = best volume',R-mw-120,yt+h/2+4)}})}
 if(L.flow&&!A.novol){const mx=Math.max(...A.flow.bubbles.map(b=>b.v))||1;
  A.flow.bubbles.forEach(b=>{const x=X(b.t),y=Y(b.p);if(y==null||x<0||x>R)return;const r=7+20*Math.sqrt(b.v/mx),bp=b.b/b.v,col=bp>=.5?'34,197,94':'239,68,68';
   cx.beginPath();cx.arc(x,y,r,0,7);cx.fillStyle=`rgba(${col},.30)`;cx.fill();cx.strokeStyle=`rgb(${col})`;cx.lineWidth=1.5;cx.stroke();
   cx.fillStyle='#fff';cx.font='bold 11px system-ui';cx.textAlign='center';cx.fillText(fv(b.v),x,y-1);cx.font='10px system-ui';cx.fillText((bp>=.5?'B ':'S ')+Math.round((bp>=.5?bp:1-bp)*100)+'%',x,y+11);cx.textAlign='left'})}
}
chart.subscribeCrosshairMove(p=>{const t=$('tip');if(!A||!p.time||!p.point||!(L.vol||L.flow||L.vp)){t.style.display='none';return}
 const c=CM[p.time];if(!c){t.style.display='none';return}const s=c.v-c.b,e=A.exact?'':' ~est';
 t.innerHTML=`<b>${new Date(c.t*1000).toLocaleString()}</b><br>O ${f(c.o)} H ${f(c.h)}<br>L ${f(c.l)} C ${f(c.c)}`+(A.novol?'':`<br>Volume <b>${fv(c.v)}</b><br><span class="g">Buy ${fv(c.b)}</span> · <span class="r">Sell ${fv(s)}</span>${e}<br>Delta <b class="${c.b>=s?'g':'r'}">${fv(c.b-s)}</b>`);
 t.style.display='block';const w=$('wrap').clientWidth;t.style.left=Math.min(p.point.x+14,w-190)+'px';t.style.top=Math.max(p.point.y-60,40)+'px'});
// ---------- panels ----------
function planCard(p,name,key){
 const bal=+$('bal')?.value||100,rp=+$('rp')?.value||10;
 if(!p)return `<div class="card"><h3>${name}</h3><p class="m">No valid ${name.toLowerCase()} zone right now (price is not at / has broken that zone). Waiting is a valid decision.</p></div>`;
 const risk=bal*rp/100,dist=Math.abs(p.entry-p.sl),u=risk/dist,val=u*p.entry;
 const gr=p.score>=4?['A strong','g']:p.score==3?['B average','a']:['C weak','r'],cur=curPlan()===p;
 return `<div class="card ${cur?'sel':''}" onclick="sel='${key}';applyLayers();cards()" style="cursor:pointer"><h3>${p.side==='BUY'?'🟢':'🔴'} ${name} <span class="${gr[1]}">${gr[0]} (${p.score}/5)</span></h3>
 <small>${p.aligned?'★ with trend':'⚠ counter-trend (riskier)'} · tap to show on chart</small><table>
 <tr><td>Entry (limit)</td><td>${f(p.entry)}</td></tr><tr><td>Stop loss</td><td class="r">${f(p.sl)}</td></tr>
 <tr><td>TP 1:1 / 1:2 / 1:3</td><td class="g">${f(p.tp1)} / ${f(p.tp2)} / ${f(p.tp3)}</td></tr>
 <tr><td>Liquidity target</td><td>${p.liq?f(p.liq)+' ('+p.rr_liq.toFixed(1)+'R)':'none'}</td></tr>
 <tr><td>SL to breakeven at (1.1R)</td><td>${f(p.trail)}</td></tr>
 <tr><td>Risk $ / size (units)</td><td>$${f(risk)} / ${u.toFixed(5)}</td></tr><tr><td>Position value / leverage</td><td>$${f(val)} / ${(val/bal).toFixed(1)}x</td></tr></table>
 <small>${p.reasons.join(' · ')||'No extra confluence'}</small></div>`}
function cards(){
 if(!A)return;const bal=$('bal')?.value||100,rp=$('rp')?.value||10;
 const vp=A.vp,fl=A.flow,tot=fl.buy+fl.sell,bp=tot?fl.buy/tot*100:50;
 const stat=A.novol?`<p class="a">Spot XAU/USD has no real volume. Profile shows time-at-price instead. Use "Gold Fut (vol)" or PAXG for volume.</p>`:
 `<table><tr><td>Best volume price (POC)</td><td class="a">${f(vp.poc)}</td></tr><tr><td>Value area (70%)</td><td>${f(vp.val)} – ${f(vp.vah)}</td></tr>
 <tr><td>Buy vs Sell (last ${fl.n} candles)</td><td><span class="g">${bp.toFixed(0)}%</span> / <span class="r">${(100-bp).toFixed(0)}%</span></td></tr>
 <tr><td>Net delta</td><td class="${fl.buy>=fl.sell?'g':'r'}">${fv(fl.buy-fl.sell)}</td></tr></table>
 <small>${A.exact?'Buy/sell split is exact exchange taker data.':'Buy/sell split is estimated from candle shape (this source gives no taker data).'}</small>`;
 const risk=`<div class="card"><h3>🧮 Your risk</h3><div class="in"><div><label>Balance $</label><input id="bal" type="number" value="${bal}"></div><div><label>Risk %</label><input id="rp" type="number" value="${rp}"></div></div>
 ${+rp>=5?`<p class="r">High risk: ${rp}% per trade. 5 losses in a row = about -${((1-Math.pow(1-rp/100,5))*100).toFixed(0)}% of the account.</p>`:'<p class="m">Common practice is 0.5-2% per trade.</p>'}
 <small>Structure: <b>${A.bias.toUpperCase()}</b> · ${A.brk||'no fresh break'}<br>Source: ${A.source}</small></div>`;
 const act=document.activeElement&&document.activeElement.id,pos=document.activeElement&&document.activeElement.selectionStart;
 $('cards').innerHTML=planCard(A.buy,'Buy plan','buy')+planCard(A.sell,'Sell plan','sell')+`<div class="card"><h3>🧬 Volume &amp; orders</h3>${stat}</div>`+risk;
 ['bal','rp'].forEach(i=>$(i).oninput=cards);
 if(act==='bal'||act==='rp'){const e=$(act);e.focus();try{e.setSelectionRange(pos,pos)}catch(x){}}
}
// ---------- live ticks (Binance websocket when source is Binance) ----------
function connectWS(){
 if(ws){try{ws.close()}catch(e){}ws=null}
 if(!A||A.source!=='binance'||!['BTCUSDT','ETHUSDT','PAXGUSDT'].includes(sym))return;
 try{ws=new WebSocket(`wss://stream.binance.com:9443/ws/${sym.toLowerCase()}@kline_${tf}`);
  ws.onmessage=e=>{const k=JSON.parse(e.data).k;if(!k)return;const t=Math.floor(k.t/1000),o=+k.o,h=+k.h,l=+k.l,c=+k.c,v=+k.v,b=+k.V;
   ser.update({time:t,open:o,high:h,low:l,close:c});if(!A.novol)vs.update({time:t,value:v,color:b>=v-b?'rgba(34,197,94,.45)':'rgba(239,68,68,.45)'});
   CM[t]={t,o,h,l,c,v,b};A.last=c;badge()}}catch(e){}
}
// ---------- loading ----------
function showLoad(m){$('load').style.display='flex';$('lt').textContent=m}
function reset(){A=null;CM={};fit=true;ser.setData([]);vs.setData([]);ser.setMarkers([]);lines.forEach(l=>ser.removePriceLine(l));lines=[];pills();showLoad('Loading live data...');load()}
async function load(){
 if(busy)return;busy=true;const hint=setTimeout(()=>{if(!A)showLoad('Server is waking up (free plan can take ~50 sec)...')},4000);
 try{const r=await fetch(`/api/analyze?symbol=${sym}&interval=${tf}`);const a=await r.json();if(a.error)throw a.error;
  A=a;CM={};a.candles.forEach(c=>CM[c.t]=c);draw();$('load').style.display='none';$('st').textContent='Live · updated '+new Date().toLocaleTimeString()+' · source: '+a.source}
 catch(e){if(!A)showLoad('⚠ '+e+' (retrying...)');else $('st').textContent='⚠ '+e}
 finally{clearTimeout(hint);busy=false}
}
$('fs').onclick=()=>{const w=$('wrap');document.fullscreenElement?document.exitFullscreen():w.requestFullscreen&&w.requestFullscreen()};
pills();frame();load();setInterval(load,8000);
</script></body></html>
"""

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype):
        b = body.encode(); self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/": return self._send(200, HTML, "text/html; charset=utf-8")
        if u.path == "/health": return self._send(200, "ok", "text/plain")   # for uptime pingers
        if u.path == "/api/analyze":
            q = urllib.parse.parse_qs(u.query)
            sym = q.get("symbol", ["BTCUSDT"])[0]; iv = q.get("interval", ["15m"])[0]
            try:
                if sym not in ROUTE or iv not in IVS: raise ValueError("Unsupported symbol/interval")
                hit = CACHE.get((sym, iv))
                if hit and time.time() - hit[0] < TTL: res = hit[1]
                else:
                    d, exact, src, novol = fetch(sym, iv)
                    res = analyze(d, exact, src, novol); CACHE[(sym, iv)] = (time.time(), res)
            except Exception as e:
                res = {"error": str(e)}
            return self._send(200, json.dumps(res), "application/json")
        self._send(404, "not found", "text/plain")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000")); online = "PORT" in os.environ
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0" if online else "127.0.0.1", port), Handler) as srv:
        print(f"Running on port {port}  (Ctrl+C to stop)")
        if not online:
            try: webbrowser.open(f"http://127.0.0.1:{port}")
            except Exception: pass
        srv.serve_forever()
