"""
VK SMC Terminal
- Data: Bybit (primary) -> OKX (fallback) for crypto, Yahoo for XAUUSD / Gold futures
- Analysis runs ONLY on closed candles, so zones/POC do not repaint or shift
- Optional Telegram alerts: set env TG_TOKEN and TG_CHAT_ID (and SCAN, e.g. "BTCUSDT:15m,XAUUSD:15m")
"""
import os
import time
import threading

import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

SYMBOLS = {
    "BTCUSDT": {"name": "Bitcoin", "src": "crypto"},
    "ETHUSDT": {"name": "Ethereum", "src": "crypto"},
    "SOLUSDT": {"name": "Solana", "src": "crypto"},
    "XAUTUSDT": {"name": "Gold token (Bybit, real volume + depth)", "src": "crypto"},
    "XAUUSD": {"name": "Gold spot (Yahoo, no volume)", "src": "yahoo", "y": "XAUUSD=X"},
    "GC=F": {"name": "Gold futures (Yahoo, delayed)", "src": "yahoo", "y": "GC=F"},
}
TF = {
    "1m": {"by": "1", "okx": "1m", "yi": "1m", "yr": "5d", "sec": 60},
    "5m": {"by": "5", "okx": "5m", "yi": "5m", "yr": "30d", "sec": 300},
    "15m": {"by": "15", "okx": "15m", "yi": "15m", "yr": "30d", "sec": 900},
    "1h": {"by": "60", "okx": "1H", "yi": "60m", "yr": "180d", "sec": 3600},
    "4h": {"by": "240", "okx": "4H", "yi": "60m", "yr": "360d", "sec": 14400},
    "1d": {"by": "D", "okx": "1D", "yi": "1d", "yr": "2y", "sec": 86400},
}

# ---------------------------------------------------------------- data layer
_cache = {}


def _pack(rows):
    """rows: list of (t,o,h,l,c,v) -> clean sorted arrays"""
    seen = {}
    for r in rows:
        if any(x is None for x in r):
            continue
        t, o, h, l, c, v = r
        if h < l or h <= 0:
            continue
        seen[int(t)] = (int(t), float(o), float(h), float(l), float(c), float(v))
    rows = [seen[k] for k in sorted(seen)]
    return {k: [r[i] for r in rows] for i, k in enumerate(["t", "o", "h", "l", "c", "v"])}


def fetch_bybit(sym, tf):
    r = requests.get(
        "https://api.bybit.com/v5/market/kline",
        params={"category": "linear", "symbol": sym, "interval": TF[tf]["by"], "limit": 600},
        headers=UA, timeout=8,
    )
    r.raise_for_status()
    j = r.json()
    if j.get("retCode") != 0:
        raise RuntimeError("Bybit: %s" % j.get("retMsg"))
    rows = [(int(x[0]) // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in j["result"]["list"]]
    return _pack(rows)


def fetch_okx(sym, tf):
    inst = sym[:-4] + "-USDT-SWAP"
    r = requests.get(
        "https://www.okx.com/api/v5/market/candles",
        params={"instId": inst, "bar": TF[tf]["okx"], "limit": 300},
        headers=UA, timeout=8,
    )
    r.raise_for_status()
    j = r.json()
    if j.get("code") != "0":
        raise RuntimeError("OKX: %s" % j.get("msg"))
    rows = [(int(x[0]) // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[6] or x[5])) for x in j["data"]]
    return _pack(rows)


def fetch_yahoo(sym, tf):
    ysym = SYMBOLS[sym]["y"]
    last_err = None
    for host in ("query1", "query2"):
        try:
            r = requests.get(
                "https://%s.finance.yahoo.com/v8/finance/chart/%s" % (host, ysym),
                params={"interval": TF[tf]["yi"], "range": TF[tf]["yr"]},
                headers=UA, timeout=10,
            )
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            rows = list(zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"],
                            [(v or 0) for v in q["volume"]]))
            cs = _pack(rows)
            if tf == "4h":
                cs = resample(cs, 14400)
            return cs
        except Exception as e:  # noqa
            last_err = e
    raise RuntimeError("Yahoo: %s" % last_err)


def resample(cs, sec):
    out = {}
    for i in range(len(cs["t"])):
        k = cs["t"][i] // sec * sec
        if k not in out:
            out[k] = [k, cs["o"][i], cs["h"][i], cs["l"][i], cs["c"][i], cs["v"][i]]
        else:
            b = out[k]
            b[2] = max(b[2], cs["h"][i])
            b[3] = min(b[3], cs["l"][i])
            b[4] = cs["c"][i]
            b[5] += cs["v"][i]
    return _pack(list(out.values()))


def get_candles(sym, tf):
    key = (sym, tf)
    ttl = 2 if SYMBOLS[sym]["src"] == "crypto" else 8
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1], hit[2], hit[3]
    errors = []
    if SYMBOLS[sym]["src"] == "crypto":
        order = [("Bybit", fetch_bybit), ("OKX", fetch_okx)]
    else:
        order = [("Yahoo", fetch_yahoo)]
    for name, fn in order:
        try:
            cs = fn(sym, tf)
            if len(cs["t"]) < 40:
                raise RuntimeError("only %d candles" % len(cs["t"]))
            _cache[key] = (time.time(), cs, name, errors)
            return cs, name, errors
        except Exception as e:  # noqa
            errors.append("%s: %s" % (name, str(e)[:140]))
    if hit:  # serve stale rather than blank
        return hit[1], hit[2] + " (stale)", errors
    raise RuntimeError(" | ".join(errors))


# ------------------------------------------------------------ analysis layer
def atr_series(h, l, c, n=14):
    out, s = [], 0.0
    for i in range(len(c)):
        tr = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        if i < n:
            s += tr
            out.append(s / (i + 1))
        else:
            out.append((out[-1] * (n - 1) + tr) / n)
    return out


def structure(o, h, l, c, at, n):
    """Confirmed pivots (n candles each side), BOS/CHoCH on candle CLOSE, order blocks."""
    N = len(c)
    swings, breaks, obs = [], [], []
    lastH = lastL = None  # [idx, price, broken]
    trend = 0
    for i in range(N):
        p = i - n
        if p >= n:
            if h[p] > max(h[p - n:p]) and h[p] >= max(h[p + 1:p + n + 1]):
                swings.append((p, "H", h[p]))
                lastH = [p, h[p], False]
            if l[p] < min(l[p - n:p]) and l[p] <= min(l[p + 1:p + n + 1]):
                swings.append((p, "L", l[p]))
                lastL = [p, l[p], False]
        if lastH and not lastH[2] and c[i] > lastH[1]:
            kind = "CHoCH" if trend == -1 else "BOS"
            trend = 1
            lastH[2] = True
            s = min(range(lastH[0], i + 1), key=lambda k: l[k])
            cands = [k for k in range(s, i) if c[k] < o[k]]
            k = min(cands, key=lambda k: l[k]) if cands else s
            top, bot = h[k], l[k]
            if top - bot > 3 * at[k]:
                top, bot = o[k], c[k]
            breaks.append({"i": i, "dir": 1, "kind": kind, "level": lastH[1], "from": lastH[0]})
            obs.append({"dir": 1, "k": k, "brk": i, "top": top, "bottom": bot})
        if lastL and not lastL[2] and c[i] < lastL[1]:
            kind = "CHoCH" if trend == 1 else "BOS"
            trend = -1
            lastL[2] = True
            s = max(range(lastL[0], i + 1), key=lambda k: h[k])
            cands = [k for k in range(s, i) if c[k] > o[k]]
            k = max(cands, key=lambda k: h[k]) if cands else s
            top, bot = h[k], l[k]
            if top - bot > 3 * at[k]:
                top, bot = c[k], o[k]
            breaks.append({"i": i, "dir": -1, "kind": kind, "level": lastL[1], "from": lastL[0]})
            obs.append({"dir": -1, "k": k, "brk": i, "top": top, "bottom": bot})
    # mitigation: close through the far side of the zone after the break
    for z in obs:
        z["mit"] = False
        for j in range(z["brk"] + 1, N):
            if (z["dir"] == 1 and c[j] < z["bottom"]) or (z["dir"] == -1 and c[j] > z["top"]):
                z["mit"] = True
                break
    return swings, breaks, obs, trend


def find_fvgs(h, l, c, at, min_mult):
    N = len(c)
    out = []
    for i in range(2, N):
        if l[i] > h[i - 2] and (l[i] - h[i - 2]) >= min_mult * at[i]:
            z = {"dir": 1, "k": i - 1, "top": l[i], "bottom": h[i - 2], "end": i}
        elif h[i] < l[i - 2] and (l[i - 2] - h[i]) >= min_mult * at[i]:
            z = {"dir": -1, "k": i - 1, "top": l[i - 2], "bottom": h[i], "end": i}
        else:
            continue
        z["filled"] = False
        for j in range(i + 1, N):
            if (z["dir"] == 1 and c[j] < z["bottom"]) or (z["dir"] == -1 and c[j] > z["top"]):
                z["filled"] = True
                break
        out.append(z)
    return out


def sr_levels(swings, N, price, tol, lookback=400):
    pts = sorted([(p, idx) for idx, _, p in swings if idx >= N - lookback])
    groups = []
    for p, idx in pts:
        if groups and p - groups[-1]["lo"] <= tol:
            g = groups[-1]
            g["ps"].append(p)
            g["first"] = min(g["first"], idx)
        else:
            groups.append({"lo": p, "ps": [p], "first": idx})
    lv = [{"price": sum(g["ps"]) / len(g["ps"]), "touches": len(g["ps"]), "first": g["first"]}
          for g in groups if len(g["ps"]) >= 2]
    above = sorted([x for x in lv if x["price"] > price], key=lambda x: x["price"])[:3]
    below = sorted([x for x in lv if x["price"] <= price], key=lambda x: -x["price"])[:3]
    return above + below


def fib_leg(swings):
    if len(swings) < 2:
        return None
    last = swings[-1]
    prev = next((s for s in reversed(swings[:-1]) if s[1] != last[1]), None)
    if not prev:
        return None
    hi = max(last[2], prev[2])
    lo = min(last[2], prev[2])
    up = last[1] == "H"
    rs = [0.382, 0.5, 0.618, 0.786]
    levels = [{"r": r, "price": (hi - r * (hi - lo)) if up else (lo + r * (hi - lo))} for r in rs]
    return {"i0": prev[0], "i1": last[0], "up": up, "hi": hi, "lo": lo, "eq": (hi + lo) / 2, "levels": levels}


def vol_profile(t, h, l, v, bins=48):
    hi, lo = max(h), min(l)
    if hi <= lo:
        return None
    step = (hi - lo) / bins
    use_vol = sum(v) > 0
    acc = [0.0] * bins
    for i in range(len(h)):
        a = max(0, int((l[i] - lo) / step))
        b = min(bins - 1, int((h[i] - lo) / step))
        w = (v[i] if use_vol else 1.0) / (b - a + 1)
        for k in range(a, b + 1):
            acc[k] += w
    poc = max(range(bins), key=lambda k: acc[k])
    tot, cur = sum(acc), acc[poc]
    a = b = poc
    while cur < 0.7 * tot and (a > 0 or b < bins - 1):
        up = acc[b + 1] if b < bins - 1 else -1
        dn = acc[a - 1] if a > 0 else -1
        if up >= dn:
            b += 1
            cur += acc[b]
        else:
            a -= 1
            cur += acc[a]
    return {
        "mode": "volume" if use_vol else "time (no volume on this feed)",
        "bins": [{"lo": lo + k * step, "hi": lo + (k + 1) * step, "v": acc[k]} for k in range(bins)],
        "poc": lo + (poc + 0.5) * step, "vah": lo + (b + 1) * step, "val": lo + a * step,
    }


def analyze(sym, tf, mode, cs):
    sec = TF[tf]["sec"]
    lim = 400 if mode == "scalp" else 500
    s0 = max(0, len(cs["t"]) - lim)
    T, O, H, L, C, V = (cs[k][s0:] for k in ("t", "o", "h", "l", "c", "v"))
    last = len(T) - 1
    forming = time.time() < T[-1] + sec
    ncl = last if forming else last + 1
    if ncl < 40:
        raise RuntimeError("not enough closed candles")
    o, h, l, c = O[:ncl], H[:ncl], L[:ncl], C[:ncl]
    at = atr_series(h, l, c)
    atr_now = at[-1]
    n = 3 if mode == "scalp" else 7

    swings, breaks, obs, trend = structure(o, h, l, c, at, n)
    fvgs = find_fvgs(h, l, c, at, 0.3 if mode == "scalp" else 0.5)
    price, hi_live, lo_live = C[last], H[last], L[last]

    live_obs = [z for z in obs if not z["mit"]]
    ob_out = []
    for d in (1, -1):
        ob_out += sorted([z for z in live_obs if z["dir"] == d], key=lambda z: -z["brk"])[:3]
    fvg_out = []
    for d in (1, -1):
        fvg_out += sorted([z for z in fvgs if z["dir"] == d and not z["filled"]], key=lambda z: -z["k"])[:3]

    sr = sr_levels(swings, ncl, price, 0.5 * atr_now)
    fib = fib_leg(swings)
    plen = 120 if mode == "scalp" else 200
    prof = vol_profile(T[:ncl][-plen:], h[-plen:], l[-plen:], V[:ncl][-plen:])

    # ---- signals (limit-entry at order block, needs confluence) ----
    sigs = []
    for d in (1, -1):
        zs = [z for z in live_obs if z["dir"] == d and ((z["bottom"] < price) if d == 1 else (z["top"] > price))]
        if not zs:
            continue
        dist = lambda z: max(0, price - z["top"]) if d == 1 else max(0, z["bottom"] - price)  # noqa
        z = min(zs, key=dist)
        ready = (lo_live <= z["top"] and price >= z["bottom"]) if d == 1 else (hi_live >= z["bottom"] and price <= z["top"])
        if not ready and dist(z) > 1.5 * atr_now:
            continue
        entry = (z["top"] + z["bottom"]) / 2
        sl = z["bottom"] - 0.25 * atr_now if d == 1 else z["top"] + 0.25 * atr_now
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        tg = sorted([p for _, kd, p in swings if (kd == "H" if d == 1 else kd == "L") and ((p > entry + risk) if d == 1 else (p < entry - risk))],
                    reverse=(d == -1))
        tp1 = tg[0] if tg else entry + d * 2 * risk
        tp2 = tg[1] if len(tg) > 1 else entry + d * 3 * risk
        rr = abs(tp1 - entry) / risk
        why = []
        if trend == d:
            why.append("trend aligned")
        if any(f["dir"] == d and f["bottom"] < z["top"] and f["top"] > z["bottom"] for f in fvg_out):
            why.append("FVG overlap")
        if fib and ((entry <= fib["eq"]) if d == 1 else (entry >= fib["eq"])):
            why.append("discount" if d == 1 else "premium")
        if any(abs(x["price"] - (z["bottom"] if d == 1 else z["top"])) <= 0.5 * atr_now for x in sr):
            why.append("S/R at zone")
        valid = rr >= 1.5 and len(why) >= 2
        sigs.append({
            "id": "%s:%s:%s:%d:%d" % (sym, tf, mode, d, T[z["k"]]),
            "dir": d, "status": ("ready" if ready else "watch") if valid else "weak",
            "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "rr": round(rr, 2),
            "score": len(why), "why": why, "zone_t": T[z["k"]], "top": z["top"], "bottom": z["bottom"],
        })
    sigs.sort(key=lambda s: ({"ready": 0, "watch": 1, "weak": 2}[s["status"]], -s["score"]))

    # candle delta: ESTIMATE from close location inside the range (not tick data)
    delta = []
    for i in range(len(T)):
        rng = H[i] - L[i]
        delta.append(round(V[i] * (2 * (C[i] - L[i]) / rng - 1), 4) if rng > 0 and V[i] > 0 else 0)

    ts = lambda i: T[i]  # noqa
    return {
        "candles": {"t": T, "o": O, "h": H, "l": L, "c": C, "v": V, "d": delta},
        "forming": forming, "price": price, "atr": atr_now,
        "trend": trend,
        "obs": [{"dir": z["dir"], "t": ts(z["k"]), "top": z["top"], "bottom": z["bottom"]} for z in ob_out],
        "fvgs": [{"dir": z["dir"], "t": ts(z["k"]), "top": z["top"], "bottom": z["bottom"]} for z in fvg_out],
        "sr": [{"price": x["price"], "touches": x["touches"], "t": ts(x["first"])} for x in sr],
        "breaks": [{"t": ts(b["i"]), "t0": ts(b["from"]), "dir": b["dir"], "kind": b["kind"], "level": b["level"]} for b in breaks[-8:]],
        "fib": None if not fib else {"t0": ts(fib["i0"]), "up": fib["up"], "hi": fib["hi"], "lo": fib["lo"], "eq": fib["eq"], "levels": fib["levels"]},
        "profile": prof, "signals": sigs,
    }


# ------------------------------------------------------------------- API
@app.route("/api/analysis")
def api_analysis():
    sym = request.args.get("symbol", "BTCUSDT")
    tf = request.args.get("tf", "15m")
    mode = request.args.get("mode", "scalp")
    if sym not in SYMBOLS or tf not in TF or mode not in ("scalp", "swing"):
        return jsonify({"error": "bad parameters"}), 400
    try:
        cs, source, errors = get_candles(sym, tf)
        out = analyze(sym, tf, mode, cs)
        out.update({"source": source, "errors": errors, "server_time": int(time.time()),
                    "has_volume": sum(cs["v"][-50:]) > 0, "symbol": sym, "tf": tf, "mode": mode})
        return jsonify(out)
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 502


@app.route("/api/depth")
def api_depth():
    sym = request.args.get("symbol", "BTCUSDT")
    if sym not in SYMBOLS or SYMBOLS[sym]["src"] != "crypto":
        return jsonify({"error": "no order book for this symbol on free data"}), 404
    try:
        r = requests.get("https://api.bybit.com/v5/market/orderbook",
                         params={"category": "linear", "symbol": sym, "limit": 50}, headers=UA, timeout=6)
        j = r.json()["result"]

        def side(rows, rev):
            rows = [(float(p), float(q)) for p, q in rows][:14]
            cum, out = 0.0, []
            for p, q in rows:
                cum += q
                out.append({"p": p, "q": q, "cum": cum})
            return out

        return jsonify({"bids": side(j["b"], False), "asks": side(j["a"], True), "ts": int(time.time())})
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 502


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


# ------------------------------------------------------ Telegram scanner
SENT = set()


def scanner():
    tok, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not tok or not chat:
        return
    items = [x.split(":") for x in os.environ.get("SCAN", "BTCUSDT:15m,XAUUSD:15m").split(",") if ":" in x]
    while True:
        for sym, tf in items:
            try:
                cs, _, _ = get_candles(sym, tf)
                a = analyze(sym, tf, "scalp", cs)
                for s in a["signals"]:
                    if s["status"] == "ready" and s["id"] not in SENT:
                        SENT.add(s["id"])
                        txt = "%s %s %s setup\nEntry %.2f | SL %.2f | TP1 %.2f | TP2 %.2f\nRR %.2f | %s" % (
                            sym, tf, "LONG" if s["dir"] == 1 else "SHORT", s["entry"], s["sl"], s["tp1"], s["tp2"],
                            s["rr"], ", ".join(s["why"]))
                        requests.post("https://api.telegram.org/bot%s/sendMessage" % tok,
                                      json={"chat_id": chat, "text": txt}, timeout=8)
            except Exception:  # noqa
                pass
        time.sleep(60)


threading.Thread(target=scanner, daemon=True).start()

# ------------------------------------------------------------- front-end
PAGE = r'''<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VK Terminal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
:root{--ink:#0c1015;--panel:#121820;--line:#1f2833;--text:#d5dbe4;--mute:#7f8b9b;--up:#26b59a;--down:#e0584f;--gold:#e2a93b;--ice:#6aa8ff}
*{box-sizing:border-box}
html,body{height:100%;margin:0;background:var(--ink);color:var(--text);font:13px/1.45 "IBM Plex Sans",system-ui,sans-serif;font-variant-numeric:tabular-nums}
button,select{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:5px 9px;cursor:pointer}
button:hover,select:hover{border-color:#32404f}
button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--ice);outline-offset:1px}
button.on{background:#1b2531;border-color:var(--gold);color:var(--gold)}
.app{display:grid;grid-template-rows:auto 1fr;height:100%}
.top{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line);background:var(--panel)}
.brand{font-weight:600;font-size:18px;color:var(--gold);letter-spacing:.5px;margin-right:6px}
.grp{display:flex;gap:4px;align-items:center}
.sep{width:1px;height:20px;background:var(--line);margin:0 4px}
.status{margin-left:auto;color:var(--mute);font-size:12px;text-align:right}
.status b{color:var(--text);font-weight:500}
.main{display:grid;grid-template-columns:1fr 310px;min-height:0}
.chartwrap{position:relative;min-height:0}
#chart{position:absolute;inset:0}
#ov{position:absolute;inset:0;pointer-events:none}
.side{border-left:1px solid var(--line);overflow:auto;padding:12px;display:flex;flex-direction:column;gap:12px;background:var(--panel)}
.card{border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:#0f141b}
.card h3{margin:0 0 6px;font-size:12px;font-weight:500;color:var(--mute)}
.big{font-size:20px;font-weight:600}
.long{color:var(--up)}.short{color:var(--down)}.wait{color:var(--mute)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:3px 12px}
.kv span:nth-child(odd){color:var(--mute)}.kv span:nth-child(even){text-align:right}
.why{color:var(--mute);font-size:12px;margin-top:6px}
.ladder{display:grid;gap:1px;font-size:12px}
.row{position:relative;display:flex;justify-content:space-between;padding:1px 6px}
.row i{position:absolute;inset:0 auto 0 0;opacity:.22}
.row span{position:relative}
.ask i{background:var(--down)}.bid i{background:var(--up)}
.log{max-height:150px;overflow:auto;font-size:12px;color:var(--mute)}
.log div{padding:2px 0;border-bottom:1px solid var(--line)}
.note{font-size:11px;color:var(--mute)}
.err{color:var(--down);font-size:12px;word-break:break-word}
#toast{position:fixed;right:16px;top:64px;background:#1b2531;border:1px solid var(--gold);border-radius:8px;padding:10px 14px;display:none;z-index:9;max-width:300px}
@media(max-width:860px){.main{grid-template-columns:1fr;grid-template-rows:62vh auto}.side{border-left:0;border-top:1px solid var(--line)}.status{margin-left:0;text-align:left}}
</style></head>
<body>
<div class="app">
 <div class="top">
  <span class="brand">VK</span>
  <select id="sym" aria-label="Symbol"></select>
  <div class="grp" id="tfs"></div>
  <div class="sep"></div>
  <div class="grp" id="modes"><button data-m="scalp">Scalp</button><button data-m="swing">Swing</button></div>
  <div class="sep"></div>
  <div class="grp" id="tg"></div>
  <button id="alertbtn">Alerts off</button>
  <div class="status" id="status">connecting…</div>
 </div>
 <div class="main">
  <div class="chartwrap"><div id="chart"></div><canvas id="ov"></canvas></div>
  <div class="side">
   <div class="card" id="sigcard"><h3>Trade setup</h3><div class="big wait">Scanning…</div></div>
   <div class="card"><h3>Market</h3><div class="kv" id="mkt"></div></div>
   <div class="card"><h3>Order book (Bybit)</h3><div class="ladder" id="depth"><span class="note">loading…</span></div></div>
   <div class="card"><h3>Alert log</h3><div class="log" id="log"><span class="note">No setups yet.</span></div></div>
   <div class="note" id="notes"></div>
  </div>
 </div>
</div>
<div id="toast"></div>
<script>
const $=id=>document.getElementById(id);
const SYMS=__SYMS__;
const TFS=['1m','5m','15m','1h','4h','1d'];
const TOG=[['ob','Order blocks'],['fvg','FVG'],['sr','S/R'],['fib','Fib'],['prof','Profile'],['str','Structure'],['dl','Delta']];
let S={sym:'BTCUSDT',tf:'15m',mode:'scalp',on:{ob:1,fvg:1,sr:1,fib:0,prof:1,str:1,dl:1},alerts:0};
try{Object.assign(S,JSON.parse(localStorage.getItem('vk')||'{}'));S.alerts=0}catch(e){}
const save=()=>{try{localStorage.setItem('vk',JSON.stringify(S))}catch(e){}};
const OFF=-new Date().getTimezoneOffset()*60;   // show candles in browser-local time
let A=null,seen=new Set(),first=true,prec=2;

// ---- controls
for(const k in SYMS){const o=document.createElement('option');o.value=k;o.textContent=k+' · '+SYMS[k];$('sym').appendChild(o)}
$('sym').value=S.sym;$('sym').onchange=e=>{S.sym=e.target.value;first=true;seen.clear();save();load()};
TFS.forEach(t=>{const b=document.createElement('button');b.textContent=t;b.dataset.t=t;b.onclick=()=>{S.tf=t;first=true;seen.clear();save();paint();load()};$('tfs').appendChild(b)});
$('modes').querySelectorAll('button').forEach(b=>b.onclick=()=>{S.mode=b.dataset.m;first=true;save();paint();load()});
TOG.forEach(([k,n])=>{const b=document.createElement('button');b.textContent=n;b.dataset.k=k;b.onclick=()=>{S.on[k]=S.on[k]?0:1;save();paint();if(A)render()};$('tg').appendChild(b)});
function paint(){
 $('tfs').querySelectorAll('button').forEach(b=>b.classList.toggle('on',b.dataset.t===S.tf));
 $('modes').querySelectorAll('button').forEach(b=>b.classList.toggle('on',b.dataset.m===S.mode));
 $('tg').querySelectorAll('button').forEach(b=>b.classList.toggle('on',!!S.on[b.dataset.k]));
 $('alertbtn').textContent=S.alerts?'Alerts on':'Alerts off';$('alertbtn').classList.toggle('on',!!S.alerts);
}
$('alertbtn').onclick=async()=>{
 S.alerts=S.alerts?0:1;
 if(S.alerts){beep(0.0001);if('Notification' in window&&Notification.permission==='default'){try{await Notification.requestPermission()}catch(e){}}}
 paint()};

// ---- chart
const chart=LightweightCharts.createChart($('chart'),{
 layout:{background:{color:'#0c1015'},textColor:'#8a96a6',fontFamily:'IBM Plex Sans, system-ui, sans-serif'},
 grid:{vertLines:{color:'#141b24'},horzLines:{color:'#141b24'}},
 rightPriceScale:{borderColor:'#1f2833',scaleMargins:{top:.04,bottom:.34}},
 timeScale:{borderColor:'#1f2833',timeVisible:true,secondsVisible:false,rightOffset:10},
 crosshair:{mode:0},
 watermark:{visible:true,text:'VK',color:'rgba(226,169,59,.28)',fontSize:46,horzAlign:'left',vertAlign:'bottom'},
 autoSize:true});
const cs=chart.addCandlestickSeries({upColor:'#26b59a',downColor:'#e0584f',borderVisible:false,wickUpColor:'#26b59a',wickDownColor:'#e0584f',priceLineColor:'#e2a93b'});
const vs=chart.addHistogramSeries({priceScaleId:'vol',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false});
chart.priceScale('vol').applyOptions({scaleMargins:{top:.68,bottom:.16}});
const ds=chart.addHistogramSeries({priceScaleId:'dl',lastValueVisible:false,priceLineVisible:false});
chart.priceScale('dl').applyOptions({scaleMargins:{top:.86,bottom:0}});

// ---- data
async function load(){
 try{
  const r=await fetch(`/api/analysis?symbol=${encodeURIComponent(S.sym)}&tf=${S.tf}&mode=${S.mode}`);
  const j=await r.json();
  if(j.error){$('status').innerHTML='<span class="err">'+j.error+'</span>';return}
  A=j;render();alertCheck();
 }catch(e){$('status').innerHTML='<span class="err">server not reachable (free plan may be waking up)</span>'}
}
function render(){
 const c=A.candles,n=c.t.length,P=A.price;
 prec=P>=100?2:(P>=1?3:5);
 cs.applyOptions({priceFormat:{type:'price',precision:prec,minMove:Math.pow(10,-prec)}});
 const d1=[],vv=[],dd=[];
 for(let i=0;i<n;i++){
  const t=c.t[i]+OFF,up=c.c[i]>=c.o[i];
  d1.push({time:t,open:c.o[i],high:c.h[i],low:c.l[i],close:c.c[i]});
  if(A.has_volume){vv.push({time:t,value:c.v[i],color:up?'rgba(38,181,154,.45)':'rgba(224,88,79,.45)'});
   if(S.on.dl)dd.push({time:t,value:c.d[i],color:c.d[i]>=0?'rgba(38,181,154,.85)':'rgba(224,88,79,.85)'})}
 }
 cs.setData(d1);vs.setData(vv);ds.setData(dd);
 // markers: structure breaks on the exact break candle + active signal
 const m=[];
 if(S.on.str)A.breaks.forEach(b=>m.push({time:b.t+OFF,position:b.dir>0?'belowBar':'aboveBar',color:b.kind==='CHoCH'?'#e2a93b':(b.dir>0?'#26b59a':'#e0584f'),shape:b.dir>0?'arrowUp':'arrowDown',text:b.kind}));
 const sg=A.signals.find(s=>s.status!=='weak');
 if(sg&&sg.status==='ready')m.push({time:c.t[n-1]+OFF,position:sg.dir>0?'belowBar':'aboveBar',color:'#ffffff',shape:'circle',text:sg.dir>0?'LONG':'SHORT'});
 m.sort((a,b)=>a.time-b.time);cs.setMarkers(m);
 if(first){chart.timeScale().setVisibleLogicalRange({from:Math.max(0,n-110),to:n+12});first=false}
 sidebar();
}
function f(x){return Number(x).toFixed(prec)}
function sidebar(){
 const sg=A.signals.find(s=>s.status!=='weak'),card=$('sigcard');
 if(sg){
  const cls=sg.dir>0?'long':'short',side=sg.dir>0?'LONG':'SHORT';
  card.innerHTML=`<h3>Trade setup</h3><div class="big ${cls}">${side} · ${sg.status==='ready'?'price in zone':'approaching zone'}</div>
  <div class="kv" style="margin-top:8px"><span>Entry</span><span>${f(sg.entry)}</span><span>Stop</span><span>${f(sg.sl)}</span><span>TP1</span><span>${f(sg.tp1)}</span><span>TP2</span><span>${f(sg.tp2)}</span><span>Reward : risk</span><span>${sg.rr}</span><span>Confluence</span><span>${sg.score}/4</span></div>
  <div class="why">${sg.why.join(' · ')}</div>`;
 }else card.innerHTML='<h3>Trade setup</h3><div class="big wait">No valid setup</div><div class="why">Scanning every few seconds. A setup needs an untouched order block, at least 2 confluences and reward:risk ≥ 1.5.</div>';
 const tr=A.trend>0?'Bullish':A.trend<0?'Bearish':'Undefined';
 const pr=A.profile;
 $('mkt').innerHTML=`<span>Price</span><span>${f(A.price)}</span><span>Structure</span><span class="${A.trend>0?'long':A.trend<0?'short':''}">${tr}</span><span>ATR</span><span>${f(A.atr)}</span>`+(pr?`<span>POC</span><span>${f(pr.poc)}</span><span>Value area</span><span>${f(pr.val)} – ${f(pr.vah)}</span>`:'');
 const now=new Date().toLocaleTimeString();
 $('status').innerHTML=`<b>${A.source}</b> · ${A.forming?'candle forming, analysis on closed candles':'last candle closed'} · updated ${now}`;
 $('notes').innerHTML=(A.has_volume?'Delta is estimated from where each candle closes inside its range. It is not tick-level delta. ':'This feed has no volume, so there is no delta and the profile is by time spent. ')+(A.errors.length?'<br><span class="err">Fallback used: '+A.errors.join(' | ')+'</span>':'');
}

// ---- overlay drawing
const cv=$('ov'),ctx=cv.getContext('2d');
function fit(){const r=$('chart').getBoundingClientRect(),d=window.devicePixelRatio||1;cv.width=r.width*d;cv.height=r.height*d;cv.style.width=r.width+'px';cv.style.height=r.height+'px';ctx.setTransform(d,0,0,d,0,0)}
new ResizeObserver(fit).observe($('chart'));fit();
const X=t=>{const x=chart.timeScale().timeToCoordinate(t);return x===null?null:x};
const Y=p=>cs.priceToCoordinate(p);
function box(t0,top,bot,fill,stroke,label,dash){
 const W=cv.clientWidth-chart.priceScale('right').width();
 let x0=X(t0+OFF);if(x0===null)x0=0;x0=Math.max(0,x0);
 const y0=Y(top),y1=Y(bot);if(y0===null||y1===null||x0>W)return;
 ctx.fillStyle=fill;ctx.fillRect(x0,y0,W-x0,y1-y0);
 ctx.strokeStyle=stroke;ctx.setLineDash(dash||[]);ctx.lineWidth=1;ctx.strokeRect(x0+.5,y0+.5,W-x0-1,y1-y0-1);ctx.setLineDash([]);
 if(label){ctx.fillStyle=stroke;ctx.font='11px IBM Plex Sans, sans-serif';ctx.fillText(label,x0+6,Math.min(y0,y1)+12)}
}
function hline(t0,p,col,label,dash,w){
 const W=cv.clientWidth-chart.priceScale('right').width();
 let x0=t0===null?0:X(t0+OFF);if(x0===null)x0=0;x0=Math.max(0,x0);
 const y=Y(p);if(y===null)return;
 ctx.strokeStyle=col;ctx.lineWidth=w||1;ctx.setLineDash(dash||[]);ctx.beginPath();ctx.moveTo(x0,y+.5);ctx.lineTo(W,y+.5);ctx.stroke();ctx.setLineDash([]);
 if(label){ctx.fillStyle=col;ctx.font='11px IBM Plex Sans, sans-serif';ctx.textAlign='right';ctx.fillText(label,W-6,y-4);ctx.textAlign='left'}
}
function draw(){
 ctx.clearRect(0,0,cv.clientWidth,cv.clientHeight);
 if(!A)return;
 const W=cv.clientWidth-chart.priceScale('right').width();
 if(S.on.prof&&A.profile){
  const pr=A.profile,mx=Math.max(...pr.bins.map(b=>b.v));
  pr.bins.forEach(b=>{const y0=Y(b.hi),y1=Y(b.lo);if(y0===null||y1===null)return;
   const w=(b.v/mx)*W*.16,inVA=b.hi>pr.val&&b.lo<pr.vah;
   ctx.fillStyle=inVA?'rgba(106,168,255,.30)':'rgba(127,139,155,.20)';ctx.fillRect(W-w,y0+.5,w,Math.max(1,y1-y0-1))});
  hline(null,pr.poc,'#e2a93b','POC',[],1.5);
 }
 if(S.on.sr)A.sr.forEach(s=>hline(s.t,s.price,'rgba(127,139,155,.9)',f(s.price)+' ×'+s.touches,[2,4]));
 if(S.on.fib&&A.fib){const F=A.fib;F.levels.forEach(l=>hline(F.t0,l.price,'rgba(226,169,59,.8)',l.r+'',[1,3]));hline(F.t0,F.eq,'rgba(226,169,59,.45)','EQ',[6,4])}
 if(S.on.fvg)A.fvgs.forEach(z=>box(z.t,z.top,z.bottom,z.dir>0?'rgba(106,168,255,.12)':'rgba(176,124,255,.12)',z.dir>0?'#6aa8ff':'#b07cff','FVG',[3,3]));
 if(S.on.ob)A.obs.forEach(z=>box(z.t,z.top,z.bottom,z.dir>0?'rgba(38,181,154,.16)':'rgba(224,88,79,.16)',z.dir>0?'#26b59a':'#e0584f',z.dir>0?'Bull OB':'Bear OB'));
 if(S.on.str)A.breaks.forEach(b=>{const x0=X(b.t0+OFF),x1=X(b.t+OFF),y=Y(b.level);if(x0===null||x1===null||y===null)return;
  ctx.strokeStyle=b.kind==='CHoCH'?'#e2a93b':'#7f8b9b';ctx.setLineDash([4,3]);ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x0,y+.5);ctx.lineTo(x1,y+.5);ctx.stroke();ctx.setLineDash([])});
 const sg=A.signals.find(s=>s.status!=='weak');
 if(sg){
  const t=A.candles.t[A.candles.t.length-1];
  const yE=Y(sg.entry),yS=Y(sg.sl),yT=Y(sg.tp1);
  let x0=X(t+OFF);if(x0===null)x0=W-60;
  if(yE!==null&&yS!==null){ctx.fillStyle='rgba(224,88,79,.10)';ctx.fillRect(x0,Math.min(yE,yS),W-x0,Math.abs(yE-yS))}
  if(yE!==null&&yT!==null){ctx.fillStyle='rgba(38,181,154,.10)';ctx.fillRect(x0,Math.min(yE,yT),W-x0,Math.abs(yE-yT))}
  hline(t,sg.entry,'#ffffff','ENTRY '+f(sg.entry),[],1.5);hline(t,sg.sl,'#e0584f','SL '+f(sg.sl),[],1.5);
  hline(t,sg.tp1,'#26b59a','TP1 '+f(sg.tp1),[],1.5);hline(t,sg.tp2,'#26b59a','TP2 '+f(sg.tp2),[4,3],1);
 }
 requestAnimationFrame(draw);
}
requestAnimationFrame(draw);

// ---- alerts
function beep(g){try{const a=new (window.AudioContext||window.webkitAudioContext)(),o=a.createOscillator(),v=a.createGain();o.frequency.value=880;v.gain.value=g||.15;o.connect(v);v.connect(a.destination);o.start();o.stop(a.currentTime+.25)}catch(e){}}
function alertCheck(){
 A.signals.filter(s=>s.status==='ready').forEach(s=>{
  if(seen.has(s.id))return;seen.add(s.id);
  const msg=`${S.sym} ${S.tf} ${s.dir>0?'LONG':'SHORT'} · entry ${f(s.entry)} · SL ${f(s.sl)} · TP1 ${f(s.tp1)} · RR ${s.rr}`;
  const L=$('log');if(L.querySelector('.note'))L.innerHTML='';
  const d=document.createElement('div');d.textContent=new Date().toLocaleTimeString()+'  '+msg;L.prepend(d);
  if(S.alerts){beep();const T=$('toast');T.textContent=msg;T.style.display='block';setTimeout(()=>T.style.display='none',9000);
   try{if(Notification.permission==='granted')new Notification('VK setup',{body:msg})}catch(e){}}
 });
}

// ---- order book
async function depth(){
 const el=$('depth');
 try{
  const r=await fetch('/api/depth?symbol='+encodeURIComponent(S.sym));const j=await r.json();
  if(j.error){el.innerHTML='<span class="note">Real order book is not available for this symbol on free data. Use BTC, ETH, SOL or XAUTUSDT.</span>';return}
  const mx=Math.max(j.bids[j.bids.length-1].cum,j.asks[j.asks.length-1].cum);
  const row=(x,c)=>`<div class="row ${c}"><i style="width:${(x.cum/mx*100).toFixed(0)}%"></i><span>${f(x.p)}</span><span>${x.q.toFixed(3)}</span></div>`;
  el.innerHTML=j.asks.slice().reverse().map(x=>row(x,'ask')).join('')+j.bids.map(x=>row(x,'bid')).join('');
 }catch(e){}
}

paint();depth();
(function loop(){load().finally(()=>setTimeout(loop,(S.sym==='XAUUSD'||S.sym==='GC=F')?9000:4000))})();
setInterval(depth,3000);
</script></body></html>'''


@app.route("/")
def index():
    import json
    syms = json.dumps({k: v["name"] for k, v in SYMBOLS.items()})
    return Response(PAGE.replace("__SYMS__", syms), mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
