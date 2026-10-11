(async()=>{
const canvas=document.getElementById('world'),ctx=canvas.getContext('2d',{alpha:false});
const minimap=document.getElementById('minimap'),mctx=minimap.getContext('2d');
const shell=document.getElementById('field');
const selectionPanel=document.getElementById('selection-panel');
const selectionTitle=document.getElementById('selection-title');
const selectionType=document.getElementById('selection-type');
const selectionStatus=document.getElementById('selection-status');
const selectionCopy=document.getElementById('selection-copy');
const commandBar=document.getElementById('command-bar');
const objectiveText=document.getElementById('objective-text');
const worldState=document.getElementById('world-state');
const modeLabel=document.getElementById('mode-label');
const dossier=document.getElementById('dossier');
const needBtn=document.getElementById('intent-need'),haveBtn=document.getElementById('intent-have');
const gameToggle=document.getElementById('game-toggle');
const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
let data={}; try{const r=await fetch('world-data.json',{cache:'no-store'});if(r.ok)data=await r.json()}catch(e){}
if(!data.entities){data={entities:[{id:'bohun',type:'bohun',label:'BOHUN HQ',x:47,y:44,status:'active'},{id:'propulsion',type:'signal',label:'PROPULSION',x:28,y:31,status:'detected'},{id:'supplier',type:'supplier',label:'SUPPLIER',x:15,y:25,status:'hidden'},{id:'partner',type:'partner',label:'PARTNER',x:36,y:35,status:'available'},{id:'market',type:'market',label:'UKRAINE MARKET',x:61,y:45,status:'active'},{id:'port',type:'market',label:'BLACK SEA ROUTE',x:54,y:68,status:'active'}]}}
const entities=data.entities;
const byId=id=>entities.find(e=>e.id===id);

// Stylised, simplified outline in normalized map coordinates. Includes Crimea in the country's internationally recognised outline.
const ua=[[20,24],[26,18],[36,19],[42,16],[52,18],[60,16],[70,20],[78,19],[84,24],[87,31],[83,36],[86,42],[82,48],[79,55],[74,59],[69,61],[65,67],[61,70],[57,68],[54,72],[48,70],[44,66],[37,65],[32,61],[26,60],[21,56],[17,52],[14,46],[13,39],[16,34],[14,29]];
const crimea=[[58,67],[62,69],[66,70],[69,74],[65,78],[60,79],[57,76],[55,72]];
const europe=[[2,15],[20,24],[14,29],[16,34],[13,39],[17,52],[21,56],[26,60],[22,74],[4,75]];
const sea=[[22,74],[55,72],[57,76],[60,79],[65,78],[69,74],[73,75],[92,74],[96,94],[18,94]];
const river=[[55,18],[53,25],[55,31],[52,37],[54,43],[52,49],[55,55],[54,61],[57,68],[55,72]];
const roads=[[[20,36],[31,35],[42,39],[47,44],[58,45],[72,43],[83,38]],[[32,58],[39,50],[47,44],[52,36],[62,29],[76,26]],[[47,44],[51,52],[54,61],[54,70]],[[17,31],[28,31],[36,35],[47,44]]];
const towns=[[29,27],[33,39],[42,27],[45,54],[57,28],[61,51],[69,36],[72,52],[35,57],[51,62],[77,31],[24,48]];
const forests=[[23,27],[25,29],[39,24],[42,23],[68,27],[72,28],[77,46],[74,48],[31,51],[28,53],[65,57],[68,58],[45,30],[57,56]];

let W=0,H=0,dpr=1,origin={x:0,y:0},tileW=22,tileH=11;
let cam={x:0,y:0,zoom:1,targetX:0,targetY:0,targetZoom:1};
let intent='need',game=false,selected=null,stage=0; // 0 select,1 researched,2 dispatched,3 connected
let dragging=false,dragStart=null,camStart=null,dispatchStart=0;

const content={
 propulsion:{copy:'A category-level signal representing current interest in propulsion-related capabilities. Research the signal to reveal a potential supplier route.',actions:['research']},
 supplier:{copy:'A potential capability provider revealed by research. Dispatch a Bohun liaison to qualify the route.',actions:['dispatch']},
 bohun:{copy:'Coordination node for research, qualification and commercial route-building.',actions:['inspect']},
 partner:{copy:'A commercial or industrial partner that can support a relevant route.',actions:['inspect']},
 market:{copy:'A demand-side market node. No buyer mandate or procurement is implied.',actions:['inspect']},
 port:{copy:'A strategic commercial/logistics route into wider international markets.',actions:['inspect']}
};
function resize(){const r=canvas.getBoundingClientRect();W=r.width;H=r.height;dpr=Math.min(devicePixelRatio||1,2);canvas.width=Math.max(1,W*dpr);canvas.height=Math.max(1,H*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);origin={x:W*.52,y:H*.16};tileW=Math.max(17,Math.min(27,W/62));tileH=tileW*.5; if(!game){cam.targetX=0;cam.targetY=H*.01;cam.targetZoom=W<820?.82:1.02}}
addEventListener('resize',resize);resize();

function pointInPoly(p,poly){let c=false;for(let i=0,j=poly.length-1;i<poly.length;j=i++){const a=poly[i],b=poly[j];if(((a[1]>p[1])!=(b[1]>p[1]))&&(p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]))c=!c}return c}
function baseIso(x,y){return{x:origin.x+(x-y)*tileW*.5,y:origin.y+(x+y)*tileH*.5}}
function screen(x,y){const p=baseIso(x,y);return{x:(p.x-W/2)*cam.zoom+W/2+cam.x,y:(p.y-H/2)*cam.zoom+H/2+cam.y}}
function diamond(x,y,fill,stroke){const p=screen(x,y),tw=tileW*.5*cam.zoom,th=tileH*.5*cam.zoom;ctx.beginPath();ctx.moveTo(p.x,p.y-th);ctx.lineTo(p.x+tw,p.y);ctx.lineTo(p.x,p.y+th);ctx.lineTo(p.x-tw,p.y);ctx.closePath();ctx.fillStyle=fill;ctx.fill();if(stroke){ctx.strokeStyle=stroke;ctx.stroke()}}
function line(points,color,width=2,dash=[]){ctx.save();ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dash);ctx.beginPath();points.forEach((q,i)=>{const p=screen(q[0],q[1]);i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y)});ctx.stroke();ctx.restore()}

function drawGround(){ctx.fillStyle='#14180f';ctx.fillRect(0,0,W,H);
 // Sea first
 for(let y=14;y<95;y+=1.4)for(let x=1;x<98;x+=1.4){if(pointInPoly([x,y],sea))diamond(x,y,((Math.floor(x+y)%4)==0)?'#345053':'#30494b','#79908d12')}
 // Europe land
 for(let y=14;y<76;y+=1.35)for(let x=2;x<27;x+=1.35){if(pointInPoly([x,y],europe))diamond(x,y,((Math.floor(x*2+y)%6)<2)?'#343c29':'#2d3524','#6f755b14')}
 // Ukraine
 for(let y=15;y<80;y+=1.25)for(let x=12;x<89;x+=1.25){if(pointInPoly([x,y],ua)||pointInPoly([x,y],crimea)){let v=(Math.floor(x*3+y*2)%11);let f=v<2?'#485739':v<7?'#3f4d31':'#38452c';if(y>57)f=v<5?'#56543a':'#4b4d33';diamond(x,y,f,'#8b916a16')}}
 // borders
 line([...ua,ua[0]],'#b3b68a99',1.3);line([...crimea,crimea[0]],'#b3b68a66',1);
 // river
 line(river,'#4f7172',7);line(river,'#77949688',2);
 // roads
 roads.forEach(r=>{line(r,'#292b20aa',5);line(r,'#a7a17a66',1,[5,5])});
 // forests
 forests.forEach((q,i)=>{for(let k=0;k<4;k++){const dx=(k%2)*1.1,dy=Math.floor(k/2)*.9;drawTree(q[0]+dx,q[1]+dy,i)}});
 // towns
 towns.forEach((q,i)=>drawSettlement(q[0],q[1],i));
 // Direction labels
 drawRegionLabel(10,19,'EUROPE');drawRegionLabel(66,19,'UKRAINE');drawRegionLabel(72,81,'BLACK SEA');
}
function drawTree(x,y,i){const p=screen(x,y),z=cam.zoom;ctx.fillStyle=i%2?'#20341f':'#263923';ctx.beginPath();ctx.moveTo(p.x,p.y-9*z);ctx.lineTo(p.x+5*z,p.y);ctx.lineTo(p.x-5*z,p.y);ctx.closePath();ctx.fill();ctx.fillStyle='#29261c';ctx.fillRect(p.x-1*z,p.y,2*z,4*z)}
function drawSettlement(x,y,i){const p=screen(x,y),z=cam.zoom;for(let k=0;k<3;k++){const ox=(k-1)*7*z,oy=(k%2)*3*z;ctx.fillStyle=i%3===0?'#777052':'#6b684d';ctx.fillRect(p.x+ox-4*z,p.y+oy-8*z,8*z,7*z);ctx.fillStyle='#8e7955';ctx.beginPath();ctx.moveTo(p.x+ox-5*z,p.y+oy-8*z);ctx.lineTo(p.x+ox,p.y+oy-12*z);ctx.lineTo(p.x+ox+5*z,p.y+oy-8*z);ctx.closePath();ctx.fill()}}
function drawRegionLabel(x,y,text){const p=screen(x,y);ctx.save();ctx.globalAlpha=.55;ctx.fillStyle='#d8d3b0';ctx.font='800 10px ui-monospace,monospace';ctx.letterSpacing='1px';ctx.fillText(text,p.x,p.y);ctx.restore()}
function drawHQ(e,t){const p=screen(e.x,e.y),z=cam.zoom;ctx.save();ctx.translate(p.x,p.y);ctx.fillStyle='#786d4a';ctx.fillRect(-18*z,-25*z,36*z,22*z);ctx.fillStyle='#9a8654';ctx.beginPath();ctx.moveTo(-21*z,-25*z);ctx.lineTo(0,-39*z);ctx.lineTo(21*z,-25*z);ctx.closePath();ctx.fill();ctx.fillStyle='#313827';ctx.fillRect(-11*z,-19*z,8*z,15*z);ctx.fillRect(4*z,-19*z,8*z,15*z);ctx.strokeStyle='#d3b766';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(0,-39*z);ctx.lineTo(0,-58*z);ctx.stroke();ctx.beginPath();ctx.moveTo(0,-57*z);ctx.lineTo(12*z,-52*z);ctx.stroke();ctx.globalAlpha=.55;ctx.strokeStyle='#d3b766';ctx.beginPath();ctx.arc(0,-54*z,10*z+(reduced?0:Math.sin(t/450)*2),0,Math.PI*2);ctx.stroke();ctx.restore();labelEntity(e,p,selected===e.id)}
function drawNode(e,t){const p=screen(e.x,e.y),z=cam.zoom;if(e.id==='bohun')return drawHQ(e,t); if(e.id==='supplier'&&stage<1)return;ctx.save();ctx.translate(p.x,p.y);let col=e.type==='signal'?'#e6c351':e.type==='supplier'?'#8d9b68':e.type==='partner'?'#83957c':'#997860';if(e.type==='signal'){const pulse=10*z+(reduced?0:Math.sin(t/300)*3*z);ctx.strokeStyle=col;ctx.lineWidth=2;ctx.beginPath();ctx.arc(0,-9*z,pulse,0,Math.PI*2);ctx.stroke();ctx.fillStyle=col;ctx.beginPath();ctx.moveTo(0,-25*z);ctx.lineTo(10*z,-8*z);ctx.lineTo(0,1*z);ctx.lineTo(-10*z,-8*z);ctx.closePath();ctx.fill()}else{ctx.fillStyle=col;ctx.fillRect(-11*z,-20*z,22*z,18*z);ctx.fillStyle=e.type==='supplier'?'#596548':e.type==='partner'?'#5c6a57':'#674d3f';ctx.beginPath();ctx.moveTo(-13*z,-20*z);ctx.lineTo(0,-30*z);ctx.lineTo(13*z,-20*z);ctx.closePath();ctx.fill();if(e.type==='supplier'){ctx.fillStyle='#303628';ctx.fillRect(-7*z,-13*z,5*z,11*z);ctx.fillRect(3*z,-13*z,5*z,11*z)}}if(selected===e.id){ctx.strokeStyle='#f0d371';ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(0,-10*z,24*z,0,Math.PI*2);ctx.stroke()}ctx.restore();labelEntity(e,p,selected===e.id)}
function labelEntity(e,p,on){ctx.save();ctx.fillStyle=on?'#fff4c2':'#d1ceb1';ctx.textAlign='center';ctx.font=`800 ${Math.max(8,9*cam.zoom)}px ui-monospace,monospace`;ctx.fillText(e.label,p.x,p.y+16*cam.zoom);ctx.restore()}
function drawScenarioRoutes(){const supplier=byId('supplier'),bohun=byId('bohun'),market=byId('market'),partner=byId('partner'),prop=byId('propulsion');if(stage>=1){line([[prop.x,prop.y],[supplier.x,supplier.y]],'#e5c75d88',1.5,[3,5])}line([[partner.x,partner.y],[bohun.x,bohun.y]],intent==='have'?'#d5bb6faa':'#9c9e7a55',1.4,[4,5]);if(stage>=3){line([[supplier.x,supplier.y],[bohun.x,bohun.y],[market.x,market.y]],'#e2c667',3);}}
function drawDispatch(t){if(stage!==2)return;const a=byId('bohun'),b=byId('supplier');const A=screen(a.x,a.y),B=screen(b.x,b.y);let m=Math.min(1,(t-dispatchStart)/2600);if(reduced)m=1;const x=A.x+(B.x-A.x)*m,y=A.y+(B.y-A.y)*m;ctx.save();ctx.fillStyle='#d3b766';ctx.fillRect(x-5,y-3,10,6);ctx.fillStyle='#202319';ctx.beginPath();ctx.arc(x-3,y+4,2,0,Math.PI*2);ctx.arc(x+3,y+4,2,0,Math.PI*2);ctx.fill();ctx.restore();if(m>=1){stage=2.5;setObjective('Connection route available');worldState.textContent='FIELD / LIAISON ARRIVED';select('supplier')}}
function drawAmbient(t){const r=roads[0];const a=r[1],b=r[2],m=(Math.sin(t/2200)+1)/2;const p=screen(a[0]+(b[0]-a[0])*m,a[1]+(b[1]-a[1])*m);ctx.fillStyle='#7c8061';ctx.fillRect(p.x-3,p.y-2,7,4)}
function draw(t){cam.x+=(cam.targetX-cam.x)*.08;cam.y+=(cam.targetY-cam.y)*.08;cam.zoom+=(cam.targetZoom-cam.zoom)*.08;drawGround();drawScenarioRoutes();drawAmbient(t);['partner','market','port','bohun','propulsion','supplier'].forEach(id=>{const e=byId(id);if(e)drawNode(e,t)});drawDispatch(t);drawMinimap();requestAnimationFrame(draw)}

function drawMinimap(){const w=minimap.width,h=minimap.height;mctx.clearRect(0,0,w,h);mctx.fillStyle='#11160f';mctx.fillRect(0,0,w,h);const sx=w/100,sy=h/100;function poly(p,c){mctx.beginPath();p.forEach((q,i)=>i?mctx.lineTo(q[0]*sx,q[1]*sy):mctx.moveTo(q[0]*sx,q[1]*sy));mctx.closePath();mctx.fillStyle=c;mctx.fill()}poly(sea,'#263f41');poly(europe,'#283225');poly(ua,'#465437');poly(crimea,'#465437');entities.forEach(e=>{if(e.id==='supplier'&&stage<1)return;mctx.fillStyle=e.id==='propulsion'?'#e8c95c':e.id==='bohun'?'#d3b766':'#c2c3a2';mctx.fillRect(e.x*sx-2,e.y*sy-2,4,4)});if(stage>=3){mctx.strokeStyle='#d3b766';mctx.lineWidth=2;mctx.beginPath();['supplier','bohun','market'].forEach((id,i)=>{const e=byId(id);i?mctx.lineTo(e.x*sx,e.y*sy):mctx.moveTo(e.x*sx,e.y*sy)});mctx.stroke()}}

function hitEntity(mx,my){let best=null,bd=28;for(const e of entities){if(e.id==='supplier'&&stage<1)continue;const p=screen(e.x,e.y),d=Math.hypot(mx-p.x,my-(p.y-10*cam.zoom));if(d<bd){best=e;bd=d}}return best}
function actionBtn(label,fn,primary=true,disabled=false){const b=document.createElement('button');b.textContent=label;b.className=primary?'primary':'';b.disabled=disabled;b.addEventListener('click',fn);commandBar.appendChild(b)}
function select(id){selected=id;const e=byId(id);if(!e)return;selectionPanel.hidden=false;selectionTitle.textContent=e.label;selectionType.textContent=e.type.toUpperCase();selectionStatus.textContent=(e.id==='supplier'&&stage>=2.5)?'QUALIFIED ROUTE':(e.status||'ACTIVE').toUpperCase();selectionCopy.textContent=(content[id]?.copy)||'Field object.';commandBar.innerHTML='';
 if(id==='propulsion'){actionBtn(stage>=1?'RESEARCH COMPLETE':'RESEARCH SIGNAL',research,true,stage>=1);actionBtn('OPEN FILE',()=>dossier.showModal(),false)}
 else if(id==='supplier'){actionBtn(stage>=2.5?'LIAISON ARRIVED':'DISPATCH LIAISON',dispatch,true,stage>=2);actionBtn('CREATE ROUTE',connect,false,stage<2.5||stage>=3)}
 else if(id==='bohun'){actionBtn('FIELD OVERVIEW',()=>focus(47,44,1.05),false);actionBtn('TRACE SIGNAL',()=>select('propulsion'),true)}
 else {actionBtn('INSPECT',()=>{},false)}
}
function research(){if(stage>=1)return;stage=1;worldState.textContent='FIELD / SIGNAL RESEARCHED';setObjective('Select the revealed SUPPLIER');select('supplier');focus(22,28,1.22)}
function dispatch(){if(stage>=2)return;stage=2;dispatchStart=performance.now();worldState.textContent='FIELD / LIAISON EN ROUTE';setObjective('Bohun liaison moving to supplier');focus(31,34,1.08)}
function connect(){if(stage<2.5||stage>=3)return;stage=3;worldState.textContent='FIELD / CONNECTION ESTABLISHED';setObjective('Route established. Open the PROPULSION file');focus(42,39,1.0);select('propulsion')}
function setObjective(t){objectiveText.textContent=t}
function focus(x,y,z=1.1){const p=baseIso(x,y);cam.targetX=W*.52-p.x;cam.targetY=H*.48-p.y;cam.targetZoom=z}

canvas.addEventListener('pointerdown',e=>{const r=canvas.getBoundingClientRect();const mx=e.clientX-r.left,my=e.clientY-r.top;if(game){dragging=true;dragStart={x:e.clientX,y:e.clientY};camStart={x:cam.targetX,y:cam.targetY};canvas.setPointerCapture(e.pointerId)}else{const ent=hitEntity(mx,my);if(ent)select(ent.id)}});
canvas.addEventListener('pointermove',e=>{if(game&&dragging){cam.targetX=camStart.x+(e.clientX-dragStart.x);cam.targetY=camStart.y+(e.clientY-dragStart.y)}});
canvas.addEventListener('pointerup',e=>{if(game&&dragging){const moved=Math.hypot(e.clientX-dragStart.x,e.clientY-dragStart.y);dragging=false;if(moved<7){const r=canvas.getBoundingClientRect();const ent=hitEntity(e.clientX-r.left,e.clientY-r.top);if(ent)select(ent.id)}}});
canvas.addEventListener('wheel',e=>{if(!game)return;e.preventDefault();cam.targetZoom=Math.max(.65,Math.min(1.65,cam.targetZoom-e.deltaY*.001))},{passive:false});

document.getElementById('selection-close').addEventListener('click',()=>{selectionPanel.hidden=true;selected=null});
function setIntent(v){intent=v;shell.classList.toggle('intent-need',v==='need');shell.classList.toggle('intent-have',v==='have');needBtn.classList.toggle('active',v==='need');haveBtn.classList.toggle('active',v==='have');modeLabel.textContent='MODE / '+(v==='need'?'I NEED SOMETHING':'I HAVE SOMETHING');worldState.textContent=v==='need'?'FIELD / CAPABILITY SEARCH':'FIELD / MARKET ROUTES';if(v==='need'){setObjective(stage===0?'Select the PROPULSION signal':objectiveText.textContent)}else{setObjective('Explore Ukraine market and partner routes')}}
needBtn.addEventListener('click',()=>setIntent('need'));haveBtn.addEventListener('click',()=>setIntent('have'));
gameToggle.addEventListener('click',()=>{game=!game;shell.classList.toggle('game-mode',game);gameToggle.setAttribute('aria-pressed',String(game));gameToggle.textContent=game?'RETURN TO PRESENTATION':'EXPLORE THE FIELD';if(!game){cam.targetX=0;cam.targetY=0;cam.targetZoom=W<820?.82:1.02}});

focus(48,43,W<820?.82:1.02);requestAnimationFrame(draw);
})();
