"""VIRAJ AI TRADING TERMINAL
Flask market dashboard with crypto/Gold candles, basic SMC overlays, drawing tools,
and a browser-local simulated trading panel. Demo trades are NOT sent to a broker.
"""
import os, time, math
from threading import Lock
import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
SYMBOLS = {
    "BTCUSDT": {"name": "Bitcoin / USDT", "kind": "crypto"},
    "ETHUSDT": {"name": "Ethereum / USDT", "kind": "crypto"},
    "SOLUSDT": {"name": "Solana / USDT", "kind": "crypto"},
    "XAUTUSDT": {"name": "Gold-backed token / USDT", "kind": "crypto"},
    "XAUUSD": {"name": "Gold Spot (Yahoo feed; availability may vary)", "kind": "yahoo", "ticker": "XAUUSD=X"},
    "GC=F": {"name": "Gold Futures (Yahoo; delayed/availability may vary)", "kind": "yahoo", "ticker": "GC=F"},
}
TFS = {
    "1m": ("1", "1m", 60, "5d"), "5m": ("5", "5m", 300, "30d"),
    "15m": ("15", "15m", 900, "30d"), "1h": ("60", "1H", 3600, "180d"),
    "4h": ("240", "4H", 14400, "360d"), "1d": ("D", "1D", 86400, "2y")
}
CACHE, CACHE_LOCK = {}, Lock()


def pack(rows):
    clean = {}
    for row in rows:
        try:
            t,o,h,l,c,v = row
            vals = [float(o),float(h),float(l),float(c),float(v or 0)]
            if not all(math.isfinite(x) for x in vals) or vals[1] < vals[2] or vals[1] <= 0: continue
            clean[int(t)] = [int(t), *vals]
        except (TypeError, ValueError):
            continue
    arr = [clean[k] for k in sorted(clean)]
    return {k:[r[i] for r in arr] for i,k in enumerate(("t","o","h","l","c","v"))}


def resample(cs, sec):
    out = {}
    for i,t in enumerate(cs["t"]):
        k = t // sec * sec
        if k not in out: out[k] = [k,cs["o"][i],cs["h"][i],cs["l"][i],cs["c"][i],cs["v"][i]]
        else:
            b=out[k]; b[2]=max(b[2],cs["h"][i]); b[3]=min(b[3],cs["l"][i]); b[4]=cs["c"][i]; b[5]+=cs["v"][i]
    return pack(out.values())


def fetch_crypto(sym, tf, venue="bybit"):
    by,okx,sec,_ = TFS[tf]
    if venue == "bybit":
        r=requests.get("https://api.bybit.com/v5/market/kline",params={"category":"linear","symbol":sym,"interval":by,"limit":500},headers=UA,timeout=10); r.raise_for_status(); j=r.json()
        if j.get("retCode") != 0: raise RuntimeError("Bybit: "+str(j.get("retMsg")))
        rows=[(int(x[0])//1000,x[1],x[2],x[3],x[4],x[5]) for x in j["result"]["list"]]
        return pack(rows)
    base=sym[:-4] if sym.endswith("USDT") else sym
    inst=base+"-USDT-SWAP"
    r=requests.get("https://www.okx.com/api/v5/market/candles",params={"instId":inst,"bar":okx,"limit":300},headers=UA,timeout=10); r.raise_for_status(); j=r.json()
    if j.get("code") != "0": raise RuntimeError("OKX: "+str(j.get("msg")))
    return pack((int(x[0])//1000,x[1],x[2],x[3],x[4],x[6] or x[5]) for x in j["data"])


def fetch_yahoo(sym, tf):
    _,_,sec,rng=TFS[tf]; ticker=SYMBOLS[sym]["ticker"]; last=None
    interval={"1m":"1m","5m":"5m","15m":"15m","1h":"60m","4h":"60m","1d":"1d"}[tf]
    for host in ("query1","query2"):
        try:
            r=requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}",params={"interval":interval,"range":rng},headers=UA,timeout=12); r.raise_for_status()
            j=r.json()["chart"]
            if not j.get("result"): raise RuntimeError(str(j.get("error")))
            res=j["result"][0]; q=res["indicators"]["quote"][0]
            vols=q.get("volume") or [0]*len(res["timestamp"])
            cs=pack(zip(res["timestamp"],q["open"],q["high"],q["low"],q["close"],vols))
            return resample(cs,14400) if tf=="4h" else cs
        except Exception as e: last=e
    raise RuntimeError("Yahoo feed unavailable: "+str(last))


def candles(sym,tf):
    key=(sym,tf); now=time.time()
    with CACHE_LOCK:
        hit=CACHE.get(key)
        if hit and now-hit[0] < (3 if SYMBOLS[sym]["kind"]=="crypto" else 12): return hit[1],hit[2],False
    errs=[]
    providers=[("Bybit",lambda:fetch_crypto(sym,tf,"bybit")),("OKX",lambda:fetch_crypto(sym,tf,"okx"))] if SYMBOLS[sym]["kind"]=="crypto" else [("Yahoo Finance",lambda:fetch_yahoo(sym,tf))]
    for name,fn in providers:
        try:
            cs=fn()
            if len(cs["t"])<25: raise RuntimeError("Not enough candles returned")
            with CACHE_LOCK: CACHE[key]=(now,cs,name)
            return cs,name,False
        except Exception as e: errs.append(name+": "+str(e)[:150])
    if hit: return hit[1],hit[2],True
    raise RuntimeError(" | ".join(errs))


def atr(h,l,c,n=14):
    out=[]
    for i in range(len(c)):
        tr=h[i]-l[i] if i==0 else max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
        out.append(tr if i==0 else (out[-1]*(n-1)+tr)/n)
    return out


def analyze(cs, tf):
    # Only closed candles are used for structure/zone calculations.
    t,o,h,l,c,v=(cs[k][:] for k in ("t","o","h","l","c","v"))
    sec=TFS[tf][2]; forming=bool(t and time.time()<t[-1]+sec)
    end=len(t)-1 if forming else len(t)
    if end<25: raise RuntimeError("Not enough closed candles")
    T,O,H,L,C,V=(x[:end] for x in (t,o,h,l,c,v)); at=atr(H,L,C); n=3 if tf in ("1m","5m","15m") else 5
    swings=[]; breaks=[]; obs=[]; lastH=lastL=None; trend=0
    for i in range(2*n,len(C)):
        p=i-n
        if H[p]>max(H[p-n:p]) and H[p]>=max(H[p+1:p+n+1]): lastH=[p,H[p],False]; swings.append({"i":p,"kind":"H","price":H[p]})
        if L[p]<min(L[p-n:p]) and L[p]<=min(L[p+1:p+n+1]): lastL=[p,L[p],False]; swings.append({"i":p,"kind":"L","price":L[p]})
        if lastH and not lastH[2] and C[i]>lastH[1]:
            kind="CHoCH" if trend<0 else "BOS"; trend=1; lastH[2]=True
            start=lastH[0]; candidates=[k for k in range(start,i) if C[k]<O[k]]; k=min(candidates,key=lambda x:L[x]) if candidates else start
            top,bot=H[k],L[k]
            if top-bot>3*at[k]: top,bot=O[k],C[k]
            breaks.append({"i":i,"from":start,"dir":1,"kind":kind,"level":lastH[1]}); obs.append({"i":k,"break_i":i,"dir":1,"top":top,"bottom":bot,"mit":False})
        if lastL and not lastL[2] and C[i]<lastL[1]:
            kind="CHoCH" if trend>0 else "BOS"; trend=-1; lastL[2]=True
            start=lastL[0]; candidates=[k for k in range(start,i) if C[k]>O[k]]; k=max(candidates,key=lambda x:H[x]) if candidates else start
            top,bot=H[k],L[k]
            if top-bot>3*at[k]: top,bot=C[k],O[k]
            breaks.append({"i":i,"from":start,"dir":-1,"kind":kind,"level":lastL[1]}); obs.append({"i":k,"break_i":i,"dir":-1,"top":top,"bottom":bot,"mit":False})
    for z in obs:
        z["mit"]=any((C[j]<z["bottom"] if z["dir"]==1 else C[j]>z["top"]) for j in range(z["break_i"]+1,len(C)))
    fvgs=[]
    for i in range(2,len(C)):
        if L[i]>H[i-2] and L[i]-H[i-2]>=.25*at[i]: z={"i":i-1,"dir":1,"top":L[i],"bottom":H[i-2]}
        elif H[i]<L[i-2] and L[i-2]-H[i]>=.25*at[i]: z={"i":i-1,"dir":-1,"top":L[i-2],"bottom":H[i]}
        else: continue
        z["filled"]=any((C[j]<z["bottom"] if z["dir"]==1 else C[j]>z["top"]) for j in range(i+1,len(C))); fvgs.append(z)
    active_obs=[z for z in obs if not z["mit"]][-8:]; active_fvgs=[z for z in fvgs if not z["filled"]][-8:]
    price=c[-1]; atr_now=at[-1]; levels=[]
    for sw in swings[-180:]:
        if not any(abs(sw["price"]-x["price"])<.45*atr_now for x in levels): levels.append({"price":sw["price"],"t":T[sw["i"]],"kind":sw["kind"]})
    levels=sorted(levels,key=lambda x:abs(x["price"]-price))[:8]
    # Transparent candle-range weighted profile estimate, not exchange volume-at-price.
    plen=min(120,len(C)); hi=max(H[-plen:]); lo=min(L[-plen:]); profile=None
    if hi>lo:
        bins=36; step=(hi-lo)/bins; acc=[0.0]*bins; has_vol=sum(V[-plen:])>0
        for hh,ll,vv in zip(H[-plen:],L[-plen:],V[-plen:]):
            a=max(0,min(bins-1,int((ll-lo)/step))); b=max(0,min(bins-1,int((hh-lo)/step))); w=(vv if has_vol else 1)/(b-a+1)
            for k in range(a,b+1): acc[k]+=w
        poc=max(range(bins),key=lambda k:acc[k]); left=right=poc; total=sum(acc); cum=acc[poc]
        while cum<.7*total and (left>0 or right<bins-1):
            up=acc[right+1] if right<bins-1 else -1; dn=acc[left-1] if left>0 else -1
            if up>=dn: right+=1; cum+=acc[right]
            else: left-=1; cum+=acc[left]
        profile={"mode":"exchange-volume weighted estimate" if has_vol else "time-weighted (no volume)","poc":lo+(poc+.5)*step,"vah":lo+(right+1)*step,"val":lo+left*step,"bins":[{"lo":lo+k*step,"hi":lo+(k+1)*step,"v":acc[k]} for k in range(bins)]}
    sigs=[]
    for d in (1,-1):
        zs=[z for z in active_obs if z["dir"]==d and (z["bottom"]<price if d==1 else z["top"]>price)]
        if not zs: continue
        z=min(zs,key=lambda z: max(0,price-z["top"]) if d==1 else max(0,z["bottom"]-price))
        ready=(L[-1]<=z["top"] and price>=z["bottom"]) if d==1 else (H[-1]>=z["bottom"] and price<=z["top"])
        dist=max(0,price-z["top"]) if d==1 else max(0,z["bottom"]-price)
        if not ready and dist>1.5*atr_now: continue
        entry=(z["top"]+z["bottom"])/2; sl=z["bottom"]-.25*atr_now if d==1 else z["top"]+.25*atr_now; risk=abs(entry-sl)
        if risk<=0: continue
        targets=sorted([s["price"] for s in swings if s["kind"]==("H" if d==1 else "L") and (s["price"]>entry+risk if d==1 else s["price"]<entry-risk)],reverse=d<0)
        tp1=targets[0] if targets else entry+d*2*risk; tp2=targets[1] if len(targets)>1 else entry+d*3*risk; rr=abs(tp1-entry)/risk
        why=[]
        if trend==d: why.append("structure aligned")
        if any(f["dir"]==d and f["bottom"]<z["top"] and f["top"]>z["bottom"] for f in active_fvgs): why.append("FVG overlap")
        if profile and ((entry<=((profile["vah"]+profile["val"])/2)) if d==1 else (entry>=((profile["vah"]+profile["val"])/2))): why.append("profile location")
        if any(abs(x["price"]-(z["bottom"] if d==1 else z["top"]))<.5*atr_now for x in levels): why.append("near swing level")
        valid=rr>=1.5 and len(why)>=2
        sigs.append({"dir":d,"status":("ready" if ready else "watch") if valid else "weak","entry":entry,"sl":sl,"tp1":tp1,"tp2":tp2,"rr":round(rr,2),"score":len(why),"why":why,"top":z["top"],"bottom":z["bottom"],"zone_t":T[z["i"]]})
    return {"candles":{"t":t,"o":o,"h":h,"l":l,"c":c,"v":v},"closed_count":len(C),"forming":forming,"price":price,"atr":atr_now,"trend":trend,"obs":[{"dir":z["dir"],"t":T[z["i"]],"top":z["top"],"bottom":z["bottom"]} for z in active_obs],"fvgs":[{"dir":z["dir"],"t":T[z["i"]],"top":z["top"],"bottom":z["bottom"]} for z in active_fvgs],"sr":levels,"breaks":[{"t":T[b["i"]],"t0":T[b["from"]],"dir":b["dir"],"kind":b["kind"],"level":b["level"]} for b in breaks[-12:]],"profile":profile,"signals":sorted(sigs,key=lambda x:(0 if x["status"]=="ready" else 1 if x["status"]=="watch" else 2,-x["score"]))}


@app.get("/api/analysis")
def api_analysis():
    sym=request.args.get("symbol","BTCUSDT"); tf=request.args.get("tf","15m")
    if sym not in SYMBOLS or tf not in TFS: return jsonify({"error":"Unsupported symbol or timeframe"}),400
    try:
        cs,source,stale=candles(sym,tf); out=analyze(cs,tf)
        out.update({"symbol":sym,"tf":tf,"source":source,"stale":stale,"server_time":int(time.time()),"has_volume":sum(cs["v"][-50:])>0,"feed_note":"Prices and analysis are informational, not a guarantee of future performance."})
        return jsonify(out)
    except Exception as e: return jsonify({"error":str(e)}),502

@app.get("/api/depth")
def api_depth():
    sym=request.args.get("symbol","BTCUSDT")
    if sym not in SYMBOLS or SYMBOLS[sym]["kind"]!="crypto": return jsonify({"error":"Order book unavailable for this symbol"}),404
    try:
        r=requests.get("https://api.bybit.com/v5/market/orderbook",params={"category":"linear","symbol":sym,"limit":50},headers=UA,timeout=8); r.raise_for_status(); j=r.json()
        if j.get("retCode")!=0: raise RuntimeError(j.get("retMsg","order book error"))
        result=j["result"]
        def side(rows):
            cum=0; out=[]
            for p,q in rows[:14]: cum+=float(q); out.append({"p":float(p),"q":float(q),"cum":cum})
            return out
        return jsonify({"bids":side(result["b"]),"asks":side(result["a"]),"ts":int(time.time()),"source":"Bybit"})
    except Exception as e: return jsonify({"error":str(e)}),502

@app.get("/api/health")
def health(): return jsonify({"ok":True,"app":"Viraj AI Trading Terminal"})

PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Viraj AI Trading Terminal</title><link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"><script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script><style>
:root{--bg:#090d12;--panel:#111821;--card:#0d131b;--line:#25303c;--text:#e3e9f0;--muted:#8c99a9;--gold:#e6b34d;--up:#27c49a;--dn:#f06464;--blue:#72aaff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:13px 'IBM Plex Sans',sans-serif;font-variant-numeric:tabular-nums}.light{--bg:#f2f5f8;--panel:#fff;--card:#f7f9fb;--line:#d8e0e8;--text:#17212d;--muted:#637184;--gold:#a66c00}.app{height:100vh;min-height:620px;display:grid;grid-template-rows:auto 1fr}.top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 14px;background:var(--panel);border-bottom:1px solid var(--line)}.brand{font-size:16px;font-weight:700;letter-spacing:.7px;color:var(--gold);margin-right:8px}.brand small{display:block;color:var(--muted);font-size:9px;letter-spacing:2px;font-weight:500}.controls{display:flex;gap:4px;align-items:center}button,select,input{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 9px}button{cursor:pointer}button:hover,select:hover{border-color:var(--gold)}button.on{border-color:var(--gold);color:var(--gold);background:color-mix(in srgb,var(--gold) 9%,var(--panel))}.status{margin-left:auto;color:var(--muted);font-size:11px}.main{display:grid;grid-template-columns:minmax(0,1fr) 320px;min-height:0}.chartwrap{position:relative;min-width:0;min-height:0}#chart{position:absolute;inset:0}#overlay{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.side{overflow:auto;background:var(--panel);border-left:1px solid var(--line);padding:10px;display:flex;flex-direction:column;gap:9px}.card{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:11px}.card h3{margin:0 0 9px;font-size:11px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.7px}.big{font-size:21px;font-weight:700}.up{color:var(--up)}.dn{color:var(--dn)}.muted{color:var(--muted)}.kv{display:grid;grid-template-columns:1fr auto;gap:5px}.kv span:nth-child(odd){color:var(--muted)}.kv span:nth-child(even){text-align:right}.small{font-size:11px;color:var(--muted);line-height:1.5}.signal{border-left:3px solid var(--gold)}.actions{display:grid;grid-template-columns:1fr 1fr;gap:6px}.buy{background:#123c32;border-color:#216c58;color:#69e0ba}.sell{background:#401e25;border-color:#77303a;color:#ff9393}.log{max-height:120px;overflow:auto;font-size:11px;color:var(--muted)}.log div{padding:4px 0;border-bottom:1px solid var(--line)}.pill{display:inline-block;border:1px solid var(--line);border-radius:20px;padding:2px 7px;font-size:10px;color:var(--muted)}.warn{color:#efbd61}.ladder{font-size:11px}.ladrow{display:flex;justify-content:space-between;position:relative;padding:2px 5px}.ladrow i{position:absolute;left:0;top:0;bottom:0;opacity:.18}.ask i{background:var(--dn)}.bid i{background:var(--up)}.ladrow span{position:relative}.toolrow{display:flex;gap:4px;flex-wrap:wrap}.toolrow button{font-size:11px;padding:5px 7px}.foot{font-size:10px;color:var(--muted);padding:2px}.toast{position:fixed;right:15px;top:70px;z-index:5;background:var(--panel);border:1px solid var(--gold);padding:12px;border-radius:8px;display:none;max-width:300px}.modal{position:fixed;inset:0;display:none;place-items:center;background:#0009;z-index:10}.modalbox{width:min(420px,92vw);background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;display:grid;gap:10px}.modalbox label{display:grid;gap:4px;color:var(--muted)}@media(max-width:900px){.main{grid-template-columns:1fr;grid-template-rows:58vh auto}.side{border-left:0;border-top:1px solid var(--line)}.status{margin-left:0;width:100%}.app{height:auto;min-height:100vh}.chartwrap{min-height:58vh}}
</style></head><body><div class="app"><header class="top"><div class="brand">VIRAJ <small>AI TRADING TERMINAL</small></div><select id="symbol" aria-label="Symbol"><option>BTCUSDT</option><option>ETHUSDT</option><option>SOLUSDT</option><option>XAUTUSDT</option><option>XAUUSD</option><option>GC=F</option></select><div class="controls" id="tfbuttons"></div><div class="controls" id="toggles"></div><button id="theme">Light</button><button id="alerts">Alerts Off</button><span class="status" id="status">Connecting to market feed…</span></header><main class="main"><section class="chartwrap"><div id="chart"></div><canvas id="overlay"></canvas></section><aside class="side"><section class="card signal" id="setup"><h3>AI / SMC setup scan</h3><div class="big muted">Loading…</div></section><section class="card"><h3>Market overview</h3><div class="kv" id="market"></div><div class="small" id="feednote"></div></section><section class="card"><h3>Order book · Bybit</h3><div id="depth" class="ladder"><span class="small">Loading order book…</span></div><div class="small">Exchange depth is only available for supported crypto instruments; it is not available for spot gold here.</div></section><section class="card"><h3>Chart drawing tools</h3><div class="toolrow" id="drawtools"><button data-tool="cursor" class="on">Cursor</button><button data-tool="hline">H-Line</button><button data-tool="trend">Trendline</button><button data-tool="rect">Rectangle</button><button data-tool="fib">Fib retracement</button><button data-tool="text">Text</button><button data-tool="clear">Clear drawings</button></div><div class="small">Select a tool, then click/drag on the chart. Drawings are local to this browser session.</div></section><section class="card"><h3>Demo trading account</h3><div class="kv" id="account"></div><label class="small">Starting balance (reset demo) <div style="display:flex;gap:5px;margin-top:4px"><input id="balanceInput" type="number" min="10" max="1000" step="10" value="100"><button id="reset">Reset</button></div></label><div class="actions" style="margin-top:8px"><button class="buy" id="buy">Simulate Buy</button><button class="sell" id="sell">Simulate Sell</button></div><div class="small" style="margin-top:7px">Demo only: orders are simulated in this browser, not sent to a broker. Use small risk; market-feed prices can be delayed.</div></section><section class="card"><h3>Simulated open positions</h3><div id="positions" class="small">No open demo trades.</div></section><section class="card"><h3>Recent activity</h3><div class="log" id="log">No activity yet.</div></section><div class="foot">SMC/ICT concepts are algorithmic estimates, not financial advice. No setup is guaranteed.</div></aside></main></div><div id="toast" class="toast"></div><div id="modal" class="modal"><div class="modalbox"><h3 id="modalTitle">Demo order</h3><label>Entry price <input id="entry" type="number" step="any"></label><label>Stop loss <input id="sl" type="number" step="any"></label><label>Take profit <input id="tp" type="number" step="any"></label><label>Risk amount in account currency <input id="risk" type="number" min="0.1" step="0.1" value="1"></label><div style="display:flex;gap:8px"><button id="confirm" style="flex:1;border-color:var(--gold);color:var(--gold)">Open demo position</button><button id="cancel">Cancel</button></div><div class="small">Position size is estimated from risk and entry-to-stop distance; P&amp;L is simulated from price movement only.</div></div></div><script>
const $=id=>document.getElementById(id),SYMS={BTCUSDT:'Bitcoin / USDT',ETHUSDT:'Ethereum / USDT',SOLUSDT:'Solana / USDT',XAUTUSDT:'Gold-backed token / USDT',XAUUSD:'Gold spot · Yahoo', 'GC=F':'Gold futures · Yahoo'},TFS=['1m','5m','15m','1h','4h','1d'];let state={symbol:'BTCUSDT',tf:'15m',tog:{ob:true,fvg:true,sr:true,str:true,prof:true},theme:'dark',alerts:false,tool:'cursor'},A=null,first=true,loadBusy=false,orderDir=1,drawings=[],draft=null,account={balance:100,start:100,positions:[],history:[]};
const saved=()=>{try{localStorage.setItem('viraj_terminal_ui',JSON.stringify({state,drawings,account}))}catch(e){}};try{let x=JSON.parse(localStorage.getItem('viraj_terminal_ui')||'{}');if(x.state)state={...state,...x.state,tog:{...state.tog,...x.state.tog}};if(Array.isArray(x.drawings))drawings=x.drawings;if(x.account)account={...account,...x.account}}catch(e){}
$('symbol').value=state.symbol;function mkButtons(){TFS.forEach(tf=>{let b=document.createElement('button');b.textContent=tf;b.dataset.tf=tf;b.onclick=()=>{state.tf=tf;first=true;paint();load()};$('tfbuttons').append(b)});[['ob','OB'],['fvg','FVG'],['sr','S/R'],['str','Structure'],['prof','Profile']].forEach(([k,label])=>{let b=document.createElement('button');b.textContent=label;b.dataset.tog=k;b.onclick=()=>{state.tog[k]=!state.tog[k];paint();drawOverlay();saved()};$('toggles').append(b)});paint()}function paint(){$('tfbuttons').querySelectorAll('button').forEach(b=>b.classList.toggle('on',b.dataset.tf===state.tf));$('toggles').querySelectorAll('button').forEach(b=>b.classList.toggle('on',!!state.tog[b.dataset.tog]));$('theme').textContent=state.theme==='dark'?'Light':'Dark';$('alerts').textContent=state.alerts?'Alerts On':'Alerts Off';document.body.classList.toggle('light',state.theme==='light')}
mkButtons();$('symbol').onchange=()=>{state.symbol=$('symbol').value;first=true;load();depth();saved()};$('theme').onclick=()=>{state.theme=state.theme==='dark'?'light':'dark';paint();chart.applyOptions({layout:{background:{color:state.theme==='dark'?'#090d12':'#f2f5f8'},textColor:state.theme==='dark'?'#8c99a9':'#637184'},grid:{vertLines:{color:state.theme==='dark'?'#151e28':'#e1e7ee'},horzLines:{color:state.theme==='dark'?'#151e28':'#e1e7ee'}}});saved()};$('alerts').onclick=()=>{state.alerts=!state.alerts;if(state.alerts&&'Notification'in window&&Notification.permission==='default')Notification.requestPermission().catch(()=>{});paint();saved()};
const chart=LightweightCharts.createChart($('chart'),{autoSize:true,layout:{background:{color:'#090d12'},textColor:'#8c99a9',fontFamily:'IBM Plex Sans, sans-serif'},grid:{vertLines:{color:'#151e28'},horzLines:{color:'#151e28'}},rightPriceScale:{borderColor:'#25303c',scaleMargins:{top:.05,bottom:.28}},timeScale:{borderColor:'#25303c',timeVisible:true,secondsVisible:false,rightOffset:8},crosshair:{mode:0},watermark:{visible:true,text:'VIRAJ AI',color:'rgba(230,179,77,.18)',fontSize:42,horzAlign:'left',vertAlign:'bottom'}});const cs=chart.addCandlestickSeries({upColor:'#27c49a',downColor:'#f06464',borderVisible:false,wickUpColor:'#27c49a',wickDownColor:'#f06464',priceLineColor:'#e6b34d'});const vs=chart.addHistogramSeries({priceScaleId:'vol',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false});chart.priceScale('vol').applyOptions({scaleMargins:{top:.74,bottom:.08}});let markers=[];function precision(p){return p>=100?2:p>=1?3:5}function fmt(p){return Number(p).toLocaleString('en-US',{minimumFractionDigits:precision(A?.price||p),maximumFractionDigits:precision(A?.price||p)})}
async function load(){if(loadBusy)return;loadBusy=true;try{let r=await fetch(`/api/analysis?symbol=${encodeURIComponent(state.symbol)}&tf=${state.tf}`);let j=await r.json();if(!r.ok||j.error)throw Error(j.error||'Market request failed');A=j;render();checkPositions();checkAlerts()}catch(e){$('status').textContent='Feed error: '+e.message;$('status').className='status warn'}finally{loadBusy=false}}function render(){if(!A)return;const c=A.candles,n=c.t.length,off=-new Date().getTimezoneOffset()*60;let data=[],vol=[];for(let i=0;i<n;i++){let time=c.t[i]+off;data.push({time,open:c.o[i],high:c.h[i],low:c.l[i],close:c.c[i]});if(A.has_volume)vol.push({time,value:c.v[i],color:c.c[i]>=c.o[i]?'rgba(39,196,154,.4)':'rgba(240,100,100,.4)'})}cs.applyOptions({priceFormat:{type:'price',precision:precision(A.price),minMove:10**-precision(A.price)}});cs.setData(data);vs.setData(vol);markers=A.breaks.filter(b=>state.tog.str).map(b=>({time:b.t+off,position:b.dir>0?'belowBar':'aboveBar',color:b.kind==='CHoCH'?'#e6b34d':b.dir>0?'#27c49a':'#f06464',shape:b.dir>0?'arrowUp':'arrowDown',text:b.kind}));cs.setMarkers(markers.sort((a,b)=>a.time-b.time));if(first){chart.timeScale().fitContent();first=false}let s=A.signals.find(x=>x.status!=='weak');$('setup').innerHTML='<h3>AI / SMC setup scan</h3>'+(s?`<div class="big ${s.dir>0?'up':'dn'}">${s.dir>0?'LONG':'SHORT'} · ${s.status.toUpperCase()}</div><div class="kv" style="margin-top:8px"><span>Entry</span><span>${fmt(s.entry)}</span><span>Stop loss</span><span>${fmt(s.sl)}</span><span>Target 1</span><span>${fmt(s.tp1)}</span><span>Target 2</span><span>${fmt(s.tp2)}</span><span>Reward / risk</span><span>${s.rr}:1</span><span>Confluences</span><span>${s.score}/4</span></div><p class="small">${s.why.join(' · ')||'Limited confluence'}<br><button id="useSetup" style="margin-top:6px">Load setup into demo order</button></p>`:'<div class="big muted">No valid setup</div><p class="small">Waiting for a nearby active order block with sufficient confluence. This is a rules-based scan, not a prediction guarantee.</p>');if(s)$('useSetup').onclick=()=>openOrder(s.dir,s.entry,s.sl,s.tp1);$('market').innerHTML=`<span>Last price</span><span>${fmt(A.price)}</span><span>Structure</span><span class="${A.trend>0?'up':A.trend<0?'dn':''}">${A.trend>0?'Bullish':A.trend<0?'Bearish':'Neutral'}</span><span>ATR(14)</span><span>${fmt(A.atr)}</span>`+(A.profile?`<span>POC estimate</span><span>${fmt(A.profile.poc)}</span><span>Value area</span><span>${fmt(A.profile.val)} – ${fmt(A.profile.vah)}</span>`:'');$('feednote').textContent=`Feed: ${A.source}${A.stale?' · STALE DATA':''} · ${A.forming?'current candle forming; analysis uses closed candles':'last candle closed'} · ${new Date(A.server_time*1000).toLocaleTimeString()}`+(A.stale?' · Refresh may be delayed.':'');$('status').textContent=`${A.source}${A.stale?' · stale':''} · ${state.symbol} · ${state.tf}`;drawOverlay();renderAccount();}
const canvas=$('overlay'),ctx=canvas.getContext('2d');function fitCanvas(){const r=$('chart').getBoundingClientRect(),d=devicePixelRatio||1;canvas.width=r.width*d;canvas.height=r.height*d;canvas.style.width=r.width+'px';canvas.style.height=r.height+'px';ctx.setTransform(d,0,0,d,0,0);drawOverlay()}new ResizeObserver(fitCanvas).observe($('chart'));function X(t){let x=chart.timeScale().timeToCoordinate(t+(-new Date().getTimezoneOffset()*60));return x}function Y(p){return cs.priceToCoordinate(p)}function toUTC(tm){let off=-new Date().getTimezoneOffset()*60;let v=typeof tm==='number'?tm:Math.floor(Date.UTC(tm.year,tm.month-1,tm.day)/1000);return v-off}function line(x1,y1,x2,y2,color,dash=[]){ctx.strokeStyle=color;ctx.setLineDash(dash);ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();ctx.setLineDash([])}function zone(z,color,label){let x=X(z.t),y1=Y(z.top),y2=Y(z.bottom);if(x==null||y1==null||y2==null)return;let w=canvas.clientWidth-chart.priceScale('right').width();ctx.fillStyle=color+'20';ctx.fillRect(Math.max(0,x),Math.min(y1,y2),Math.max(0,w-x),Math.abs(y2-y1));ctx.strokeStyle=color;ctx.strokeRect(Math.max(0,x),Math.min(y1,y2),Math.max(0,w-x),Math.abs(y2-y1));ctx.fillStyle=color;ctx.font='11px IBM Plex Sans';ctx.fillText(label,Math.max(3,x+5),Math.min(y1,y2)+12)}function drawOverlay(){if(!ctx)return;ctx.clearRect(0,0,canvas.clientWidth,canvas.clientHeight);if(!A)return;let W=canvas.clientWidth-chart.priceScale('right').width();if(state.tog.prof&&A.profile){let bins=A.profile.bins,mx=Math.max(...bins.map(b=>b.v));bins.forEach(b=>{let y1=Y(b.hi),y2=Y(b.lo);if(y1==null||y2==null)return;let w=b.v/mx*W*.15;ctx.fillStyle=b.hi>A.profile.val&&b.lo<A.profile.vah?'rgba(114,170,255,.23)':'rgba(140,153,169,.13)';ctx.fillRect(W-w,y1,w,Math.max(1,y2-y1))});hline(A.profile.poc,'#e6b34d','POC');hline(A.profile.vah,'#72aaff','VAH');hline(A.profile.val,'#72aaff','VAL')}if(state.tog.sr)A.sr.forEach(x=>{let y=Y(x.price);if(y!=null){line(0,y,W,y,'#8c99a9',[3,4]);ctx.fillStyle='#8c99a9';ctx.fillText('S/R '+fmt(x.price),W-65,y-3)}});if(state.tog.ob)A.obs.forEach(z=>zone(z,z.dir>0?'#27c49a':'#f06464',z.dir>0?'Bull OB':'Bear OB'));if(state.tog.fvg)A.fvgs.forEach(z=>zone(z,z.dir>0?'#72aaff':'#b18aff','FVG'));if(state.tog.str)A.breaks.forEach(b=>{let x1=X(b.t0),x2=X(b.t),y=Y(b.level);if(x1!=null&&x2!=null&&y!=null)line(x1,y,x2,y,b.kind==='CHoCH'?'#e6b34d':'#8c99a9',[4,3])});drawings.forEach(d=>drawOne(d));if(draft)drawOne(draft)}function hline(p,color,label){let y=Y(p);if(y==null)return;line(0,y,W,color,[]);ctx.fillStyle=color;ctx.font='11px IBM Plex Sans';ctx.fillText(label+' '+fmt(p),W-95,y-4)}function drawOne(d){let x1=X(d.t1),y1=Y(d.p1),x2=X(d.t2),y2=Y(d.p2);if(d.tool==='hline'){x1=0;x2=canvas.clientWidth-chart.priceScale('right').width()}if([x1,y1,x2,y2].some(x=>x==null))return;ctx.strokeStyle=d.color||'#e6b34d';ctx.fillStyle=(d.color||'#e6b34d')+'18';if(d.tool==='rect'||d.tool==='fib'){ctx.fillRect(x1,y1,x2-x1,y2-y1);ctx.strokeRect(x1,y1,x2-x1,y2-y1);if(d.tool==='fib'){[0,.382,.5,.618,.786,1].forEach(r=>{let y=y1+(y2-y1)*r;line(x1,y,x2,y,d.color||'#e6b34d',[3,3]);ctx.fillText((r*100).toFixed(1)+'%',x1+4,y-3)})}}else if(d.tool==='text'){ctx.fillText(d.text||'Note',x1,y1)}else line(x1,y1,x2,y2,d.color||'#e6b34d',d.tool==='trend'?[0]:[])}
function tool(k){state.tool=k;canvas.style.pointerEvents=k==='cursor'?'none':'auto';document.querySelectorAll('[data-tool]').forEach(b=>b.classList.toggle('on',b.dataset.tool===k));if(k==='clear'){drawings=[];draft=null;state.tool='cursor';document.querySelectorAll('[data-tool]').forEach(b=>b.classList.toggle('on',b.dataset.tool==='cursor'));drawOverlay();saved()}}document.querySelectorAll('[data-tool]').forEach(b=>b.onclick=()=>tool(b.dataset.tool));let dragStart=null;canvas.style.pointerEvents=state.tool==='cursor'?'none':'auto';canvas.style.cursor='crosshair';canvas.addEventListener('pointerdown',e=>{if(state.tool==='cursor')return;let r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top,tm=chart.timeScale().coordinateToTime(x),p=cs.coordinateToPrice(y);if(tm==null||p==null)return;dragStart={x,y,t:toUTC(tm),p};draft={tool:state.tool,t1:dragStart.t,t2:dragStart.t,p1:p,p2:p,color:'#e6b34d'};canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(!dragStart||!draft)return;let r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top,tm=chart.timeScale().coordinateToTime(x),p=cs.coordinateToPrice(y);if(tm!=null&&p!=null){draft.t2=toUTC(tm);draft.p2=p;drawOverlay()}});canvas.addEventListener('pointerup',()=>{if(draft){if(draft.tool==='hline'){draft.p2=draft.p1;draft.t1=draft.t2=0}else if(draft.tool==='text'){draft.text=prompt('Text label:','Important level');if(!draft.text){draft=null;dragStart=null;drawOverlay();return}}drawings.push(draft);draft=null;drawOverlay();saved()}dragStart=null});
function openOrder(dir,entry,sl,tp){orderDir=dir;$('modalTitle').textContent=(dir>0?'BUY':'SELL')+' · Demo order';$('entry').value=entry??A?.price??'';$('sl').value=sl??(A?.price*(dir>0?.995:1.005))??'';$('tp').value=tp??(A?.price*(dir>0?1.01:.99))??'';$('risk').value='1';$('modal').style.display='grid'}$('buy').onclick=()=>openOrder(1);$('sell').onclick=()=>openOrder(-1);$('cancel').onclick=()=>$('modal').style.display='none';$('reset').onclick=()=>{let b=Number($('balanceInput').value);if(!Number.isFinite(b)||b<10||b>1000){toast('Balance must be between $10 and $1,000');return}if(!confirm('Reset demo balance and clear demo positions/history?'))return;account={balance:b,start:b,positions:[],history:[]};renderAccount();saved()};$('confirm').onclick=()=>{let entry=Number($('entry').value),sl=Number($('sl').value),tp=Number($('tp').value),risk=Number($('risk').value);if(![entry,sl,tp,risk].every(Number.isFinite)||entry<=0||risk<=0||risk>account.balance||sl===entry||((orderDir>0)&&(sl>=entry||tp<=entry))||((orderDir<0)&&(sl<=entry||tp>=entry))){toast('Check entry, SL, TP and risk values.');return}let qty=risk/Math.abs(entry-sl);account.positions.push({id:Date.now(),symbol:state.symbol,dir:orderDir,entry,sl,tp,risk,qty,opened:new Date().toISOString()});account.balance-=risk;account.history.unshift({time:new Date().toLocaleString(),text:`Opened ${orderDir>0?'BUY':'SELL'} ${state.symbol} · risk $${risk.toFixed(2)}`});$('modal').style.display='none';renderAccount();saved()};function currentPrice(p){return state.symbol===A?.symbol?A.price:p}function checkPositions(){if(!A)return;let changed=false;account.positions=account.positions.filter(p=>{if(p.symbol!==state.symbol)return true;let price=A.price,exit=null;if(p.dir>0){if(price<=p.sl)exit={price:p.sl,why:'SL'};else if(price>=p.tp)exit={price:p.tp,why:'TP'}}else{if(price>=p.sl)exit={price:p.sl,why:'SL'};else if(price<=p.tp)exit={price:p.tp,why:'TP'}}if(exit){let pnl=p.dir*(exit.price-p.entry)*p.qty;account.balance+=p.risk+pnl;account.history.unshift({time:new Date().toLocaleString(),text:`Closed ${p.symbol} at ${fmt(exit.price)} · ${exit.why} · P&L ${pnl>=0?'+':''}$${pnl.toFixed(2)}`});changed=true;return false}return true});if(changed){renderAccount();saved()}else renderAccount()}function renderAccount(){let upnl=0;account.positions.forEach(p=>{if(A&&p.symbol===A.symbol)upnl+=p.dir*(A.price-p.entry)*p.qty});$('account').innerHTML=`<span>Free demo balance</span><span>$${account.balance.toFixed(2)}</span><span>Open P&amp;L (estimate)</span><span class="${upnl>=0?'up':'dn'}">${upnl>=0?'+':''}$${upnl.toFixed(2)}</span><span>Equity estimate</span><span>$${(account.balance+account.positions.reduce((s,p)=>s+p.risk,0)+upnl).toFixed(2)}</span><span>Open positions</span><span>${account.positions.length}</span>`;$('positions').innerHTML=account.positions.length?account.positions.map(p=>`<div style="border-bottom:1px solid var(--line);padding:6px 0"><b class="${p.dir>0?'up':'dn'}">${p.dir>0?'BUY':'SELL'} ${p.symbol}</b><div>Entry ${fmt(p.entry)} · SL ${fmt(p.sl)} · TP ${fmt(p.tp)}</div><div>Risk $${p.risk.toFixed(2)} · Qty ${p.qty.toFixed(5)}</div></div>`).join(''):'No open demo trades.';$('log').innerHTML=account.history.slice(0,20).map(x=>`<div>${x.time} · ${x.text}</div>`).join('')||'No activity yet.'}function toast(s){$('toast').textContent=s;$('toast').style.display='block';setTimeout(()=>$('toast').style.display='none',5000)}function checkAlerts(){if(!A||!state.alerts)return;let s=A.signals.find(x=>x.status==='ready');if(!s)return;let key=[A.symbol,A.tf,s.dir,s.zone_t].join(':');try{let last=localStorage.getItem('viraj_last_alert');if(last===key)return;localStorage.setItem('viraj_last_alert',key)}catch(e){}let msg=`${A.symbol} ${s.dir>0?'LONG':'SHORT'} · Entry ${fmt(s.entry)} · SL ${fmt(s.sl)} · TP ${fmt(s.tp1)}`;toast(msg);if('Notification'in window&&Notification.permission==='granted')new Notification('Viraj AI Trading Terminal',{body:msg})}
async function depth(){let el=$('depth');if(!el)return;try{let r=await fetch('/api/depth?symbol='+encodeURIComponent(state.symbol)),j=await r.json();if(!r.ok||j.error)throw Error(j.error);let mx=Math.max(j.bids.at(-1)?.cum||1,j.asks.at(-1)?.cum||1);el.innerHTML=j.asks.slice().reverse().map(x=>ladrow(x,'ask',mx)).join('')+j.bids.map(x=>ladrow(x,'bid',mx)).join('')}catch(e){el.innerHTML='<span class="small">Order book is available only for supported crypto symbols when the exchange feed responds.</span>'}}function ladrow(x,side,mx){return `<div class="ladrow ${side}"><i style="width:${x.cum/mx*100}%"></i><span>${fmt(x.p)}</span><span>${x.q.toFixed(3)}</span></div>`} // Order book panel is added only if the user wants it in a later enhancement.
$('balanceInput').value=account.start;paint();renderAccount();load();depth();setInterval(load,6000);setInterval(depth,5000);window.addEventListener('resize',fitCanvas);
</script></body></html>'''

@app.get("/")
def index(): return Response(PAGE,mimetype="text/html")

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)))
