#!/usr/bin/env python3
"""VK Terminal server. Serves the dashboard; all market data is fetched by your browser directly from Binance.
Run locally: python smc_trader.py  -> http://127.0.0.1:8000   (or just open vk-terminal.html)
Educational tool, not financial advice."""
import os, http.server, socketserver, webbrowser

HTML = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VK Terminal</title>
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
*{box-sizing:border-box}body{margin:0;background:#07090e;color:#e6e9f2;font:13px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 10px;border-bottom:1px solid #1a2030;background:linear-gradient(90deg,#120a26,#07121c)}
.logo{font-weight:900;font-size:20px;letter-spacing:-1px;background:linear-gradient(90deg,#a78bfa,#22d3ee);-webkit-background-clip:text;background-clip:text;color:transparent;margin-right:4px}
.row{display:flex;gap:5px;overflow-x:auto;max-width:100%}
.b{border:1px solid #232b3d;background:#0e1320;color:#c3cade;border-radius:8px;padding:5px 10px;cursor:pointer;white-space:nowrap;font-size:12px}
.b.on{background:linear-gradient(135deg,#7c3aed,#0891b2);color:#fff;border-color:transparent}
.chips{padding:6px 10px}.chips .b{border-radius:999px}.chips .b.on{background:rgba(34,211,238,.15);border-color:#22d3ee;box-shadow:0 0 10px rgba(34,211,238,.3)}
#px{font-size:18px;font-weight:700}.tag{padding:2px 7px;border-radius:6px;font-size:11px;font-weight:700}
.bull{background:rgba(52,211,153,.2);color:#34d399}.bear{background:rgba(248,113,113,.2);color:#f87171}.rng{background:rgba(251,191,36,.2);color:#fbbf24}
.main{display:grid;grid-template-columns:1fr 320px;gap:8px;padding:0 10px 10px}
@media(max-width:900px){.main{grid-template-columns:1fr}}
#wrap,#p2w{position:relative;border:1px solid #1a2030;border-radius:10px;overflow:hidden;background:#090c14}
#wrap{height:calc(100vh - 330px);min-height:360px}#p2w{height:140px;margin-top:6px}
#c,#ov,#c2{position:absolute;inset:0}#ov{pointer-events:none;z-index:2}
#wm{position:absolute;left:12px;bottom:8px;z-index:3;font:900 34px system-ui;letter-spacing:-2px;color:rgba(167,139,250,.35);pointer-events:none}
#load{position:absolute;inset:0;z-index:6;background:rgba(7,9,14,.85);display:flex;align-items:center;justify-content:center;text-align:center;padding:20px}
#md{position:absolute;right:76px;top:4px;z-index:4;display:flex;gap:4px}
aside{display:flex;flex-direction:column;gap:8px;max-height:calc(100vh - 150px);overflow:auto}
.card{background:#0c1019;border:1px solid #1a2030;border-radius:10px;padding:10px}.card h3{margin:0 0 6px;font-size:13px}
table{width:100%;border-collapse:collapse}td{padding:2px 0;border-bottom:1px solid #151b29}td:last-child{text-align:right;font-weight:600}
.g{color:#34d399}.r{color:#f87171}.a{color:#fbbf24}.m{color:#7d869c}
input{width:100%;background:#0e1320;border:1px solid #232b3d;color:#fff;border-radius:6px;padding:5px;font-size:13px}
#toast{position:fixed;right:12px;top:60px;z-index:20;display:flex;flex-direction:column;gap:6px}
.t{background:#14102a;border:1px solid #7c3aed;border-radius:10px;padding:10px 12px;max-width:320px;box-shadow:0 6px 24px rgba(124,58,237,.4)}
</style></head><body>
<header><div class="logo">VK</div><div class="row" id="syms"></div><div class="row" id="tfs"></div><span id="dot" style="margin-left:auto;font-size:11px"></span><span style="display:flex;gap:4px;align-items:center"><input id="tvp" type="number" step="any" placeholder="TV price" style="width:92px"><div class="b" onclick="matchPrice()">Match</div><div class="b" onclick="clearOff()">✕</div></span><span id="px"></span></header>
<div class="chips row" id="chips"></div>
<div class="main"><div><div id="wrap"><div id="c"></div><canvas id="ov"></canvas><div id="wm">VK</div><div id="load"><span id="lt">Loading Binance data...</span></div></div>
<div id="p2w"><div id="c2"></div><div id="md"></div></div></div><aside id="side"></aside></div>
<div id="toast"></div>
<div class="m" style="text-align:center;font-size:11px;padding:0 10px 14px">Educational tool, not financial advice. Rule-based; no setup is guaranteed. Data: Binance (same feed as TradingView BINANCE:BTCUSDT).</div>
<script>
const avg=(a)=>a.reduce((s,x)=>s+x,0)/(a.length||1);
const niceStep=x=>{const e=Math.pow(10,Math.floor(Math.log10(x))),m=x/e;return(m<1.5?1:m<3.5?2:m<7.5?5:10)*e};
function sw(D,k){const H=[],L=[];for(let i=k;i<D.length-k;i++){let h=1,l=1;for(let j=1;j<=k;j++){if(D[i].h<=D[i-j].h||D[i].h<D[i+j].h)h=0;if(D[i].l>=D[i-j].l||D[i].l>D[i+j].l)l=0}if(h)H.push(i);if(l)L.push(i)}return[H,L]}
const mir=D=>D.map(x=>({t:x.t,o:-x.o,h:-x.l,l:-x.h,c:-x.c,v:x.v,b:x.v-x.b}));
function obFind(M,Lw,atr,last){const n=M.length;
 for(let q=Lw.length-1;q>=Math.max(0,Lw.length-4);q--){const a=Lw[q];if(a<n-120)break;
  let i=a;while(i>=a-4&&i>=0&&M[i].c>=M[i].o)i--;if(i<a-4||i<0)i=a;
  const top=M[i].h,bot=M[i].l;let mit=0,mx=-1e18;
  for(let j=i+1;j<n;j++){if(M[j].c<bot){mit=1;break}mx=Math.max(mx,M[j].h)}
  if(mit||mx<top+atr||last-top>8*atr)continue;return{t:M[i].t,top,bot}}
 return null}
function build(D,sg,atr,aligned){
 const M=sg>0?D:mir(D),n=M.length,last=M[n-1].c,[H,Lw]=sw(M,5),ob=obFind(M,Lw,atr,last);if(!ob)return null;
 const entry=last>ob.top?ob.top:last,sl=ob.bot-.1*atr,r=entry-sl;if(r<=0)return null;
 const ab=H.map(i=>M[i].h).filter(h=>h>entry),liq=ab.length?Math.min(...ab):null,rr=liq?(liq-entry)/r:0;
 const lv=Lw.filter(i=>i<n-15).map(i=>M[i].l),sweep=M.slice(-15).some(c=>lv.some(v=>c.l<v&&c.c>v));
 const dl=M.slice(-3).reduce((a,x)=>a+2*x.b-x.v,0),rs=[],add=(c,t)=>{if(c)rs.push(t)};
 add(aligned,'Trend agrees');add(sweep,'Liquidity swept (stop hunt)');add(dl>0,'Delta confirms');add(rr>=2,'Liquidity target '+rr.toFixed(1)+'R');add(last-entry<=2*atr,'Price near zone');
 const f=x=>sg>0?x:-x;
 const o={side:sg>0?'BUY':'SELL',aligned,id:(sg>0?'B':'S')+ob.t,ob:sg>0?{t:ob.t,top:ob.top,bot:ob.bot}:{t:ob.t,top:-ob.bot,bot:-ob.top},entry:f(entry),sl:f(sl),liq:liq==null?null:f(liq),rr,score:rs.length,reasons:rs};
 [1,2,3].forEach(k=>o['tp'+k]=f(entry+k*r));o.trail=f(entry+1.1*r);return o}
function srLevels(D,atr){const[H,L]=sw(D.slice(-400),3),S=D.slice(-400),p=[...H.map(i=>S[i].h),...L.map(i=>S[i].l)].sort((a,b)=>a-b),g=[];
 p.forEach(v=>{const c=g[g.length-1];if(c&&v-c.s<=.4*atr){c.v.push(v)}else g.push({s:v,v:[v]})});
 return g.filter(c=>c.v.length>=2).map(c=>({p:avg(c.v),n:c.v.length}))}
function vprof(D,step){const lo=Math.floor(Math.min(...D.map(x=>x.l))/step)*step,hi=Math.max(...D.map(x=>x.h)),nb=Math.ceil((hi-lo)/step)+1,B=Array(nb).fill(0),S=Array(nb).fill(0);
 D.forEach(x=>{const i0=Math.floor((x.l-lo)/step),i1=Math.floor((x.h-lo)/step),k=i1-i0+1;for(let i=i0;i<=i1;i++){B[i]+=x.b/k;S[i]+=(x.v-x.b)/k}});
 const T=B.map((b,i)=>b+S[i]);let poc=T.indexOf(Math.max(...T)),tot=T.reduce((a,b)=>a+b,0),acc=T[poc],l=poc,h=poc;
 while(acc<.7*tot&&(l>0||h<nb-1)){const u=h<nb-1?T[h+1]:-1,d=l>0?T[l-1]:-1;if(u>=d){h++;acc+=u}else{l--;acc+=d}}
 return{lo,step,B,S,poc:lo+(poc+.5)*step,vah:lo+(h+1)*step,val:lo+l*step}}
function analyzeAll(D,tf){
 const n=D.length;if(n<80)return null;const atr=avg(D.slice(-14).map(x=>x.h-x.l)),last=D[n-1].c,[H,Lw]=sw(D,5);
 if(H.length<2||Lw.length<2)return null;
 const ih=H[H.length-1],il=Lw[Lw.length-1],up=D[ih].h>D[H[H.length-2]].h&&D[il].l>D[Lw[Lw.length-2]].l,dn=D[ih].h<D[H[H.length-2]].h&&D[il].l<D[Lw[Lw.length-2]].l;
 let bias=up?'bull':dn?'bear':'range';
 const bk=(idx,hi)=>{const lv=hi?D[idx].h:D[idx].l;for(let j=idx+1;j<n;j++)if(hi?D[j].c>lv:D[j].c<lv)return{t1:D[idx].t,t2:D[j].t,p:lv,up:hi,j};return null};
 const bu=bk(ih,1),bd=bk(il,0),brks=[];
 [bu,bd].forEach(b=>{if(b&&b.j>n-150)brks.push({...b,txt:((b.up&&bias==='bull')||(!b.up&&bias==='bear'))?'BOS':'CHoCH'})});
 const lb=[bu,bd].filter(Boolean).sort((a,b)=>b.j-a.j)[0];if(lb&&lb.j>n-40)bias=lb.up?'bull':'bear';
 const buy=build(D,1,atr,bias==='bull'),sell=build(D,-1,atr,bias==='bear');
 const intra=['1m','5m','15m','1h'].includes(tf),day=D[n-1].t-D[n-1].t%86400;let pd=intra?D.filter(x=>x.t>=day):D.slice(-150);if(pd.length<40)pd=D.slice(-100);
 const vp=vprof(pd,niceStep(atr/2));
 let fib=null;const bullLeg=bias!=='bear';
 if(bullLeg){const l=Lw.filter(i=>i<ih).pop();if(l!=null)fib={lo:D[l].l,hi:D[ih].h,up:true,t:D[l].t}}else{const h=H.filter(i=>i<il).pop();if(h!=null)fib={lo:D[il].l,hi:D[h].h,up:false,t:D[h].t}}
 const cv=[];let c=0;D.forEach(x=>{c+=2*x.b-x.v;cv.push(c)});
 return{bias,atr,last,brks,buy,sell,vp,fib,cvd:cv,sr:srLevels(D,atr),
  sh:H.slice(-6).map(i=>({t:D[i].t,p:D[i].h})),sl:Lw.slice(-6).map(i=>({t:D[i].t,p:D[i].l})),pdn:pd.length}}

const $=id=>document.getElementById(id);
const SY={BTC:['BTCUSDT','spot','₿ BTC'],ETH:['ETHUSDT','spot','Ξ ETH'],XAU:['XAUUSDT','fut','🥇 XAU/USD'],PAXG:['PAXGUSDT','spot','🪙 PAXG']};
const TF={'1m':60,'5m':300,'15m':900,'1h':3600,'4h':14400,'1d':86400};
const HOST={spot:['https://api.binance.com/api/v3','https://data-api.binance.vision/api/v3'],fut:['https://fapi.binance.com/fapi/v1']};
const WSH={spot:'wss://stream.binance.com:443/stream?streams=',fut:'wss://fstream.binance.com/stream?streams='};
let OFF=0,sy='BTC',tf='5m',C=[],A=null,FP={},fpStep=1,fpReady=0,ws=null,tok=0,p2m='Delta',seen={},log=[],sel='auto',fit=1;
const L={zones:1,plan:1,struct:1,sr:0,fib:0,vp:0,fp:0,both:0,vol:1};
const CHIPS=[['zones','🟩 Zones'],['plan','🎯 Plan'],['struct','🧱 Structure'],['sr','📏 S/R'],['fib','🌀 Fib'],['vp','🧬 Volume Profile'],['fp','⚡ Footprint'],['both','↔ Both sides'],['alert','🔔 Alerts']];
const f=n=>n==null?'-':(+n).toLocaleString(undefined,{maximumFractionDigits:Math.abs(n)>=1000?2:Math.abs(n)>=1?3:5});
const fv=v=>v>=1e6?(v/1e6).toFixed(1)+'M':v>=1e3?(v/1e3).toFixed(1)+'K':v>=100?v.toFixed(0):v>=10?v.toFixed(1):v.toFixed(2);
function bar(){
 $('syms').innerHTML=Object.entries(SY).map(([k,v])=>`<div class="b ${k===sy?'on':''}" onclick="sy='${k}';reset()">${v[2]}</div>`).join('');
 $('tfs').innerHTML=Object.keys(TF).map(k=>`<div class="b ${k===tf?'on':''}" onclick="tf='${k}';reset()">${k}</div>`).join('');
 $('chips').innerHTML=CHIPS.map(([k,n])=>`<div class="b ${L[k]?'on':''}" onclick="tog('${k}')">${n}</div>`).join('');
 $('md').innerHTML=['Delta','Volume','CVD'].map(m=>`<div class="b ${m===p2m?'on':''}" onclick="p2m='${m}';bar();p2()">${m}</div>`).join('');
}
function tog(k){if(k==='alert'){L.alert=L.alert?0:1;if(L.alert&&window.Notification&&Notification.permission==='default')Notification.requestPermission()}else L[k]=L[k]?0:1;bar();lay();side()}
const tfmt=(t,ty)=>{const d=new Date(t*1000);return ty>=3?d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',hour12:false}):d.toLocaleDateString([],{day:'2-digit',month:'short'})};
const opt=el=>({autoSize:true,layout:{background:{color:'rgba(0,0,0,0)'},textColor:'#7d869c'},grid:{vertLines:{color:'rgba(255,255,255,.03)'},horzLines:{color:'rgba(255,255,255,.03)'}},rightPriceScale:{borderColor:'#1a2030'},timeScale:{timeVisible:true,rightOffset:6,borderColor:'#1a2030',tickMarkFormatter:tfmt},localization:{timeFormatter:t=>new Date(t*1000).toLocaleString()}});
const chart=LightweightCharts.createChart($('c'),opt());chart.priceScale('right').applyOptions({scaleMargins:{top:.06,bottom:.06}});
const ser=chart.addCandlestickSeries({upColor:'#22c55e',downColor:'#ef4444',wickUpColor:'#22c55e',wickDownColor:'#ef4444',borderVisible:false});
const chart2=LightweightCharts.createChart($('c2'),opt());const s2=chart2.addHistogramSeries({priceFormat:{type:'volume'}}),l2=chart2.addLineSeries({color:'#22d3ee',lineWidth:2,visible:false});
let sg=0;const sync=(a,b)=>a.timeScale().subscribeVisibleLogicalRangeChange(r=>{if(sg||!r)return;sg=1;b.timeScale().setVisibleLogicalRange(r);sg=0});sync(chart,chart2);sync(chart2,chart);
let lines=[];const pl=(p,c,t,st,w)=>lines.push(ser.createPriceLine({price:p,color:c,lineWidth:w||1,lineStyle:st??2,axisLabelVisible:true,title:t}));
const prim=()=>!A?null:sel==='buy'?A.buy:sel==='sell'?A.sell:A.bias==='bull'?A.buy:A.bias==='bear'?A.sell:null;
function lay(){lines.forEach(l=>ser.removePriceLine(l));lines=[];if(!A)return;const p=prim();
 if(L.plan&&p){pl(p.entry,'#60a5fa','ENTRY',0,2);pl(p.sl,'#f87171','SL',0,2);pl(p.tp1,'#34d399','TP 1:1',2);pl(p.tp2,'#34d399','TP 1:2',0,2);pl(p.tp3,'#34d399','TP 1:3',2)}
 if(L.vp)pl(A.vp.poc,'#fde047','POC',0,2);
 ser.setMarkers(L.struct?[...A.sh.map(x=>({time:x.t,position:'aboveBar',color:'#f59e0b',shape:'circle',text:'SH'})),...A.sl.map(x=>({time:x.t,position:'belowBar',color:'#38bdf8',shape:'circle',text:'SL'}))].sort((a,b)=>a.time-b.time):[])}
function p2(){const dl=x=>2*x.b-x.v;let c=0;
 if(p2m==='CVD'){l2.applyOptions({visible:true});s2.setData([]);l2.setData(C.map(x=>({time:x.t,value:(c+=dl(x))})))}
 else{l2.applyOptions({visible:false});l2.setData([]);s2.setData(C.map(x=>p2m==='Delta'?{time:x.t,value:dl(x),color:dl(x)>=0?'#22c55e':'#ef4444'}:{time:x.t,value:x.v,color:x.b>=x.v-x.b?'rgba(34,197,94,.6)':'rgba(239,68,68,.6)'}))}}
// ---- overlay ----
const ov=$('ov'),cx=ov.getContext('2d');
function frame(){try{draw()}catch(e){}requestAnimationFrame(frame)}
function draw(){const w=$('wrap'),d=devicePixelRatio||1,W=w.clientWidth,H=w.clientHeight;
 if(ov.width!==Math.round(W*d)||ov.height!==Math.round(H*d)){ov.width=Math.round(W*d);ov.height=Math.round(H*d)}
 cx.setTransform(d,0,0,d,0,0);cx.clearRect(0,0,W,H);if(!A||C.length<3)return;
 let R=W-60;try{R=W-chart.priceScale('right').width()}catch(e){}
 const ts=chart.timeScale(),X=t=>{const x=ts.timeToCoordinate(t);return x==null?-9999:x},Y=p=>ser.priceToCoordinate(p),sp=Math.abs(X(C[C.length-1].t)-X(C[C.length-2].t));
 const box=(top,bot,t,fill,st,lb,dash)=>{const y1=Y(top),y2=Y(bot);if(y1==null||y2==null)return;const x1=Math.max(0,X(t)),yt=Math.min(y1,y2),h=Math.max(Math.abs(y2-y1),2);cx.fillStyle=fill;cx.fillRect(x1,yt,R-x1,h);cx.strokeStyle=st;cx.lineWidth=1;cx.setLineDash(dash?[4,3]:[]);cx.strokeRect(x1,yt,R-x1,h);cx.setLineDash([]);if(lb){cx.fillStyle=st;cx.font='bold 11px system-ui';cx.fillText(lb,x1+6,yt+12)}};
 const hl=(p,c,lb,x0,dash)=>{const y=Y(p);if(y==null)return;cx.strokeStyle=c;cx.lineWidth=1;cx.setLineDash(dash?[4,4]:[]);cx.beginPath();cx.moveTo(x0||0,y);cx.lineTo(R,y);cx.stroke();cx.setLineDash([]);if(lb){cx.fillStyle=c;cx.font='11px system-ui';cx.fillText(lb,(x0||0)+6,y-3)}};
 if(L.vp){const v=A.vp,mx=Math.max(...v.B.map((b,i)=>b+v.S[i]))||1,mw=R*.2;v.B.forEach((b,i)=>{const y1=Y(v.lo+(i+1)*v.step),y2=Y(v.lo+i*v.step);if(y1==null||y2==null)return;const h=Math.max(y2-y1-1,1),s=v.S[i],wb=mw*b/mx,wsl=mw*s/mx;cx.fillStyle='rgba(239,68,68,.4)';cx.fillRect(R-wsl,y1,wsl,h);cx.fillStyle='rgba(34,197,94,.4)';cx.fillRect(R-wsl-wb,y1,wb,h)});hl(v.poc,'#fde047','POC (best volume)',R-mw-100);hl(v.vah,'#fde047','VAH',R-mw-30,1);hl(v.val,'#fde047','VAL',R-mw-30,1)}
 if(L.sr)A.sr.filter(s=>Math.abs(s.p-A.last)<10*A.atr).forEach(s=>{const c=s.p>A.last?'#f472b6':'#38bdf8';hl(s.p,c,(s.p>A.last?'R ':'S ')+f(s.p)+' x'+s.n,0,1)});
 if(L.fib&&A.fib){const F=A.fib,r=F.hi-F.lo,lv=x=>F.up?F.hi-x*r:F.lo+x*r;box(lv(.618),lv(.786),F.t,'rgba(250,204,21,.12)','#facc15','OTE .618-.786');[.5,1].forEach(x=>hl(lv(x),'#facc15',x===.5?'Fib 0.5':'Fib 1.0',X(F.t),1))}
 if(L.zones){const ps=[prim()];if(L.both){ps.push(A.buy,A.sell)}[...new Set(ps)].filter(Boolean).forEach(p=>{const b=p.side==='BUY';box(p.ob.top,p.ob.bot,p.ob.t,b?'rgba(34,197,94,.2)':'rgba(239,68,68,.2)',b?'#22c55e':'#ef4444',(b?'BUY ZONE ▲':'SELL ZONE ▼')+(p.aligned?' ★':' (counter)'))})}
 if(L.struct)A.brks.forEach(b=>{const x1=X(b.t1),x2=X(b.t2),y=Y(b.p);if(y==null)return;cx.strokeStyle=b.up?'#34d399':'#f87171';cx.setLineDash([3,3]);cx.beginPath();cx.moveTo(x1,y);cx.lineTo(x2,y);cx.stroke();cx.setLineDash([]);cx.fillStyle=cx.strokeStyle;cx.font='bold 11px system-ui';cx.fillText(b.txt,(x1+x2)/2-10,y+(b.up?-4:12))});
 if(L.fp&&sp>=44)C.forEach(c=>{const m=FP[c.t],x=X(c.t);if(!m||x<-sp||x>R+sp)return;const bw=sp*.46;let pk=null,pv=0;m.forEach((v,k)=>{if(v[0]+v[1]>pv){pv=v[0]+v[1];pk=k}});
  m.forEach((v,k)=>{const p=k*fpStep,y1=Y(p+fpStep/2),y2=Y(p-fpStep/2);if(y1==null||y2==null)return;const h=y2-y1;if(h<8)return;const im=v[1]>=3*v[0]?1:v[0]>=3*v[1]?-1:0;
   cx.fillStyle=im>0?'rgba(34,197,94,.4)':im<0?'rgba(239,68,68,.4)':'rgba(255,255,255,.06)';cx.fillRect(x-bw,y1,bw*2,h-1);if(k===pk){cx.strokeStyle='#fde047';cx.strokeRect(x-bw,y1,bw*2,h-1)}
   cx.fillStyle='#e5e7eb';cx.font=Math.min(11,h-1)+'px system-ui';cx.textAlign='right';cx.fillText(fv(v[0]),x-2,y1+h-2);cx.textAlign='left';cx.fillText(fv(v[1]),x+2,y1+h-2)})});
 if(L.fp&&sp<44){cx.fillStyle='#fbbf24';cx.font='12px system-ui';cx.fillText('Zoom in (scroll) to see footprint numbers',70,24)}
}
// ---- panels ----
function side(){if(!A){$('side').innerHTML='';return}
 const bal=+($('bal')?$('bal').value:100)||100,rp=+($('rp')?$('rp').value:2)||2,p=prim(),cd=C.slice(-10).reduce((a,x)=>a+2*x.b-x.v,0),d1=C[C.length-2]?2*C[C.length-2].b-C[C.length-2].v:0,v=A.vp;
 const pc=(p,nm)=>{if(!p)return`<div class="card"><h3>${nm}</h3><p class="m">No fresh, valid zone right now. Waiting is a valid decision.</p></div>`;
  const rk=bal*rp/100,u=rk/Math.abs(p.entry-p.sl),g=p.score>=4?['A','g']:p.score==3?['B','a']:['C','r'];
  return`<div class="card"><h3>${p.side==='BUY'?'🟢':'🔴'} ${nm} <span class="${g[1]}">grade ${g[0]} (${p.score}/5)</span></h3><div class="m">${p.aligned?'★ with trend':'⚠ counter-trend, riskier'}</div><table>
  <tr><td>Entry (limit)</td><td>${f(p.entry)}</td></tr><tr><td>Stop loss</td><td class="r">${f(p.sl)}</td></tr><tr><td>TP 1:1 · 1:2 · 1:3</td><td class="g">${f(p.tp1)} · ${f(p.tp2)} · ${f(p.tp3)}</td></tr>
  <tr><td>Liquidity target</td><td>${p.liq?f(p.liq)+' ('+p.rr.toFixed(1)+'R)':'none'}</td></tr><tr><td>SL to breakeven at 1.1R</td><td>${f(p.trail)}</td></tr>
  <tr><td>Size (units) / value</td><td>${u.toFixed(5)} / $${f(u*p.entry)}</td></tr><tr><td>Leverage needed</td><td>${(u*p.entry/bal).toFixed(1)}x</td></tr></table><div class="m">${p.reasons.join(' · ')||'no extra confluence'}</div></div>`};
 const keep=document.activeElement&&document.activeElement.id;
 $('side').innerHTML=`<div class="card"><h3>Market: <span class="tag ${A.bias}">${A.bias==='bull'?'BULLISH':A.bias==='bear'?'BEARISH':'RANGE'}</span> ${A.brks.length?'<span class="tag rng">'+A.brks.slice(-1)[0].txt+'</span>':''}</h3>
 <table><tr><td>Price offset (match)</td><td>${OFF>=0?'+':''}${OFF}</td></tr><tr><td>Volume POC (fixed per session)</td><td class="a">${f(v.poc)}</td></tr><tr><td>Value area</td><td>${f(v.val)} – ${f(v.vah)}</td></tr><tr><td>Last candle delta</td><td class="${d1>=0?'g':'r'}">${fv(d1)}</td></tr><tr><td>Delta (last 10)</td><td class="${cd>=0?'g':'r'}">${fv(cd)}</td></tr></table></div>`
 +(p?pc(p,p.side==='BUY'?'Buy plan':'Sell plan')+(L.both?pc(p===A.buy?A.sell:A.buy,'Other side'):''):'<div class="card"><h3>Range market</h3><p class="m">No clear trend, so no main plan. Both sides shown below for reference.</p></div>'+pc(A.buy,'Buy plan')+pc(A.sell,'Sell plan'))
 +`<div class="card"><h3>🧮 Risk</h3><div style="display:flex;gap:6px"><div><span class="m">Balance $</span><input id="bal" type="number" value="${bal}"></div><div><span class="m">Risk %</span><input id="rp" type="number" value="${rp}"></div></div>${rp>=5?`<p class="r">High risk: 5 losses in a row = about -${((1-Math.pow(1-rp/100,5))*100).toFixed(0)}% of account.</p>`:''}</div>`
 +`<div class="card"><h3>🔔 Alerts ${L.alert?'(on)':'(off, tap chip)'}</h3>${log.slice(0,6).map(x=>'<div class="m">'+x+'</div>').join('')||'<div class="m">Nothing yet. Alerts fire when a new zone forms and when price enters it.</div>'}</div>`;
 ['bal','rp'].forEach(i=>{$(i).oninput=side});if(keep==='bal'||keep==='rp')$(keep).focus()}
function notify(msg){log.unshift(new Date().toLocaleTimeString()+' '+msg);side();if(!L.alert)return;const t=document.createElement('div');t.className='t';t.textContent=msg;$('toast').appendChild(t);setTimeout(()=>t.remove(),12000);
 try{const a=new(window.AudioContext||window.webkitAudioContext)(),o=a.createOscillator();o.connect(a.destination);o.frequency.value=880;o.start();setTimeout(()=>o.stop(),250)}catch(e){}
 try{if(Notification.permission==='granted')new Notification('VK Terminal',{body:msg})}catch(e){}}
function alerts(){if(!A)return;const k=SY[sy][2]+' '+tf;[A.buy,A.sell].forEach(p=>{if(!p||!(p.aligned||L.both))return;const id=sy+tf+p.id;
  if(!seen[id]){seen[id]=1;notify(`${k}: new ${p.side} zone ${f(p.ob.bot)}-${f(p.ob.top)} | SL ${f(p.sl)} | TP 1:2 ${f(p.tp2)}`)}
  const c=C[C.length-1];if(!seen[id+'t']&&c.l<=p.ob.top&&c.h>=p.ob.bot){seen[id+'t']=1;notify(`${k}: price ENTERED ${p.side} zone. Entry ${f(p.entry)} SL ${f(p.sl)}`)}})}
// ---- data ----
const err=m=>{$('load').style.display='flex';$('lt').textContent=m};
async function getK(){const[s,m]=SY[sy],es=[];for(const h of HOST[m]){try{const r=await fetch(`${h}/klines?symbol=${s}&interval=${tf}&limit=1000`);if(!r.ok)throw'HTTP '+r.status;return(await r.json()).map(x=>({t:x[0]/1000,o:+x[1]+OFF,h:+x[2]+OFF,l:+x[3]+OFF,c:+x[4]+OFF,v:+x[5],b:+x[9]}))}catch(e){es.push(h.split('/')[2]+': '+e)}}throw es.join(' | ')+'. Binance may be blocked on this network (try another network/VPN).'}
function fpAdd(d){const t=Math.floor(d.T/1000/TF[tf])*TF[tf],k=Math.round((+d.p+OFF)/fpStep),m=FP[t]||(FP[t]=new Map()),v=m.get(k)||[0,0];v[d.m?0:1]+=+d.q;m.set(k,v)}
async function backfill(tk){const[s,m]=SY[sy],h=HOST[m][0];let from=null,last=0;
 for(let i=0;i<8&&tk===tok;i++){try{const u=from==null?`${h}/aggTrades?symbol=${s}&startTime=${C[C.length-1].t*1000}&limit=1000`:`${h}/aggTrades?symbol=${s}&fromId=${from}&limit=1000`;const r=await (await fetch(u)).json();if(!Array.isArray(r)||!r.length)break;
  r.forEach(d=>{if(d.T>=C[C.length-1].t*1000)fpAdd(d);last=d.a});from=last+1;if(r.length<1000)break}catch(e){break}}return last}
function startWS(tk,lastA){lastMsg=0;startPoll(tk);const[s,m]=SY[sy],q=s.toLowerCase();ws=new WebSocket(WSH[m]+`${q}@kline_${tf}/${q}@aggTrade`);
 ws.onmessage=e=>{if(tk!==tok)return;const j=JSON.parse(e.data),d=j.data;if(!d)return;
  lastMsg=Date.now();if(d.e==='aggTrade'){if(d.a>lastA){lastA=d.a;fpAdd(d)}return}
  const k=d.k;onK({t:k.t/1000,o:+k.o+OFF,h:+k.h+OFF,l:+k.l+OFF,c:+k.c+OFF,v:+k.v,b:+k.V},k.x)};
 ws.onclose=()=>{if(tk===tok)setTimeout(()=>{if(tk===tok)reset(1)},2000)}}
let lastMsg=0,poll=null;
function onK(c,x){const l=C[C.length-1];if(l.t===c.t)C[C.length-1]=c;else if(c.t>l.t){C.push(c);if(C.length>1500)C.shift()}else return;
 ser.update({time:c.t,open:c.o,high:c.h,low:c.l,close:c.c});const dl=2*c.b-c.v;if(p2m==='Delta')s2.update({time:c.t,value:dl,color:dl>=0?'#22c55e':'#ef4444'});else if(p2m==='Volume')s2.update({time:c.t,value:c.v,color:c.b>=c.v-c.b?'rgba(34,197,94,.6)':'rgba(239,68,68,.6)'});
 $('px').innerHTML=`${f(c.c)} <span class="tag ${A?A.bias:'rng'}">${A?(A.bias==='bull'?'BULLISH':A.bias==='bear'?'BEARISH':'RANGE'):''}</span>`;
 if(x){recalc();p2()}else if(A){A.last=c.c;alerts()}}
function setDot(live){$('dot').innerHTML=live?'<span class="g">● LIVE</span>':'<span class="a">● POLLING (websocket blocked, REST every 2s)</span>'}
function startPoll(tk){clearInterval(poll);const[s,m]=SY[sy];poll=setInterval(async()=>{if(tk!==tok)return clearInterval(poll);const live=Date.now()-lastMsg<4000;setDot(live);if(live)return;
 try{const r=await(await fetch(`${HOST[m][0]}/klines?symbol=${s}&interval=${tf}&limit=2`)).json(),q=r.map(x=>({t:x[0]/1000,o:+x[1]+OFF,h:+x[2]+OFF,l:+x[3]+OFF,c:+x[4]+OFF,v:+x[5],b:+x[9]}));
  onK(q[0],false);const n0=C.length;onK(q[1],false);if(C.length>n0){recalc();p2()}}catch(e){}},2000)}
function recalc(){const D=C.slice(0,-1),a=analyzeAll(D,tf);if(a){A=a;lay();side();alerts()}}   // analysis only on CLOSED candles: nothing shifts mid-candle
function loadOff(){try{OFF=+localStorage.getItem('off_'+sy)||0}catch(e){OFF=0}}
function setOff(v){OFF=+v.toFixed(2);try{localStorage.setItem('off_'+sy,OFF)}catch(e){}reset()}
function matchPrice(){const v=+$('tvp').value;if(!v||!C.length)return;setOff(v-(C[C.length-1].c-OFF))}
function clearOff(){setOff(0)}
function reset(keep){tok++;loadOff();if(ws){try{ws.close()}catch(e){}}A=null;if(!keep){FP={};seen={};fit=1}ser.setData([]);bar();err('Loading Binance data...');init(tok)}
async function init(tk){try{C=await getK();if(tk!==tok)return;ser.setData(C.map(x=>({time:x.t,open:x.o,high:x.h,low:x.l,close:x.c})));
  if(!Object.keys(FP).length)fpStep=niceStep(avg(C.slice(-14).map(x=>x.h-x.l))/10);const a=analyzeAll(C.slice(0,-1),tf);if(!a)throw'Not enough data';A=a;if(fit){chart.timeScale().fitContent();fit=0}
  p2();lay();side();$('load').style.display='none';const la=await backfill(tk);if(tk!==tok)return;startWS(tk,la);alerts()}
 catch(e){if(tk===tok){err('⚠ '+e);setTimeout(()=>{if(tk===tok)init(tk)},5000)}}}
loadOff();bar();frame();init(tok);

</script></body></html>"""

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body, ct = (b"ok", "text/plain") if self.path.startswith("/health") else (HTML.encode(), "text/html; charset=utf-8")
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000")); online = "PORT" in os.environ
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0" if online else "127.0.0.1", port), H) as s:
        print(f"Running on port {port}")
        if not online:
            try: webbrowser.open(f"http://127.0.0.1:{port}")
            except Exception: pass
        s.serve_forever()
