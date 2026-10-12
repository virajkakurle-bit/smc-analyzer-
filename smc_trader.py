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
// ===== DEMO BROKER ENGINE (pure functions, unit-tested; simulated only, never a real broker) =====
const FEE=0.0005,clamp=(v,a,b)=>Math.min(b,Math.max(a,v));
function sizeQty(riskUsd,entry,sl,spec){const d=Math.abs(entry-sl);if(!(d>0))return{qty:0,err:'SL distance is 0'};
 const q=+(Math.floor(riskUsd/d/spec.step+1e-9)*spec.step).toFixed(8);
 if(q<spec.min)return{qty:0,err:'Below min size '+spec.min+' (raise risk or tighten SL)'};
 if(q*entry<spec.notional)return{qty:0,err:'Below min notional $'+spec.notional};
 return{qty:q,risk:q*d,notional:q*entry}}
const newAcct=b=>({bal0:clamp(+b||100,10,1000),bal:clamp(+b||100,10,1000),pos:[],ord:[],hist:[],id:1});
const dirOf=s=>s==='BUY'?1:-1;
const markPx=(p,bid,ask)=>p.side==='BUY'?bid:ask;
const upnl=(a,bid,ask)=>a.pos.reduce((s,p)=>s+(markPx(p,bid,ask)-p.entry)*p.qty*dirOf(p.side),0);
const equity=(a,bid,ask)=>a.bal+upnl(a,bid,ask);
const usedMargin=(a,lev)=>a.pos.reduce((s,p)=>s+p.qty*p.entry/lev,0);
function openPos(a,o,bid,ask,lev,slip){ // o:{side,qty,sl,tp,trail,price?}
 const fill=o.side==='BUY'?ask*(1+slip):bid*(1-slip),fee=fill*o.qty*FEE;
 if(o.sl&&((o.side==='BUY'&&o.sl>=fill)||(o.side==='SELL'&&o.sl<=fill)))return{err:'Stop loss is on the wrong side of price'};
 if(o.tp&&((o.side==='BUY'&&o.tp<=fill)||(o.side==='SELL'&&o.tp>=fill)))return{err:'Take profit is on the wrong side of price'};
 const free=equity(a,bid,ask)-usedMargin(a,lev);if(fill*o.qty/lev>free)return{err:'Not enough free margin ('+free.toFixed(2)+')'};
 a.bal-=fee;const p={id:a.id++,sym:o.sym,side:o.side,qty:o.qty,entry:fill,sl:o.sl||null,sl0:o.sl||null,tp:o.tp||null,trail:o.trail||null,fee,ts:Date.now(),log:[]};a.pos.push(p);return{pos:p}}
function closePos(a,p,px,why,slip){const ex=p.side==='BUY'?px*(1-slip):px*(1+slip),fee=ex*p.qty*FEE,pnl=(ex-p.entry)*p.qty*dirOf(p.side)-fee;
 a.bal+=pnl+0;a.pos=a.pos.filter(x=>x!==p);a.hist.unshift({id:p.id,side:p.side,qty:p.qty,entry:p.entry,exit:ex,pnl:pnl-p.fee,why,ts:Date.now(),log:p.log});return pnl}
function stepAcct(a,bid,ask,lev,slip){const ev=[];
 a.ord=a.ord.filter(o=>{const hit=o.side==='BUY'?ask<=o.price:bid>=o.price;if(!hit)return true;const r=openPos(a,{...o},bid,ask,lev,0);ev.push(r.err?'Pending order cancelled: '+r.err:'Pending '+o.side+' filled @ '+r.pos.entry.toFixed(2));return false});
 [...a.pos].forEach(p=>{const L=p.side==='BUY',px=L?bid:ask,R=p.sl0?Math.abs(p.entry-p.sl0):0,prof=(px-p.entry)*dirOf(p.side);
  if(p.trail&&R>0&&prof>=p.trail.act*R){let ns=p.entry+dirOf(p.side)*R*(p.trail.lock||0);if(p.trail.dist)ns=L?Math.max(ns,px-p.trail.dist*R):Math.min(ns,px+p.trail.dist*R);
   if(p.sl==null||(L?ns>p.sl:ns<p.sl)){p.sl=ns;p.log.push('SL moved to '+ns.toFixed(2)+' (trail active at +'+p.trail.act+'R)');ev.push('#'+p.id+' trailing SL -> '+ns.toFixed(2))}}
  if(p.sl!=null&&(L?px<=p.sl:px>=p.sl)){const r=closePos(a,p,p.sl,'SL',slip);ev.push('#'+p.id+' stop hit, P&L '+r.toFixed(2))}
  else if(p.tp!=null&&(L?px>=p.tp:px<=p.tp)){const r=closePos(a,p,p.tp,'TP',0);ev.push('#'+p.id+' target hit, P&L '+r.toFixed(2))}});
 return ev}
