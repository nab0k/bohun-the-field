(async () => {
  const canvas = document.getElementById('world');
  const ctx = canvas.getContext('2d', { alpha: false });
  const shell = document.getElementById('world-shell');
  const gameToggle = document.getElementById('game-toggle');
  const gameHud = document.getElementById('game-hud');
  const fieldLegend = document.getElementById('field-legend');
  const revealButton = document.getElementById('reveal-button');
  const dossier = document.getElementById('dossier');
  const dossierTitle = document.getElementById('dossier-title');
  const dossierCopy = document.getElementById('dossier-copy');
  const selectionLabel = document.getElementById('selection-label');
  const hudState = document.getElementById('hud-state');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const fallbackWorld = {
    meta:{version:'0.2',world:'BOHUN / THE FIELD'},
    entities:[
      {id:'bohun',type:'bohun',label:'BOHUN',position:{x:8,y:7}},
      {id:'ukraine',type:'market',label:'UKRAINE',position:{x:3,y:8}},
      {id:'europe',type:'market',label:'EUROPE / INTERNATIONAL',position:{x:14,y:4}},
      {id:'supplier',type:'supplier',label:'SUPPLIER',position:{x:4,y:3}},
      {id:'buyer',type:'buyer',label:'BUYER',position:{x:12,y:9}},
      {id:'partner',type:'partner',label:'PARTNER',position:{x:11,y:3}},
      {id:'propulsion',type:'interest',label:'PROPULSION',position:{x:6,y:4}}
    ],
    routes:[
      {id:'supplier-bohun',from:'supplier',to:'bohun',status:'visible'},
      {id:'bohun-buyer',from:'bohun',to:'buyer',status:'visible'},
      {id:'bohun-ukraine',from:'bohun',to:'ukraine',status:'visible'},
      {id:'bohun-europe',from:'bohun',to:'europe',status:'visible'},
      {id:'partner-bohun',from:'partner',to:'bohun',status:'visible'},
      {id:'propulsion-match',from:'propulsion',to:'bohun',status:'locked'}
    ]
  };

  let world=fallbackWorld;
  try {
    const response=await fetch('world-data.json',{cache:'no-store'});
    if(response.ok) world=await response.json();
  } catch (_) {
    // File:// and locked-down preview environments may block fetch. The fallback mirrors the canonical data file.
  }

  const accentByType={bohun:'#d3ba70',market:'#aab092',supplier:'#9eaa78',buyer:'#bd9a78',partner:'#8fa18d',interest:'#e5cc79'};
  const entities=(world.entities||[]).map(e=>({
    ...e,
    x:e.position?.x ?? e.x ?? 0,
    y:e.position?.y ?? e.y ?? 0,
    accent:e.accent || accentByType[e.type] || '#9aa083'
  }));
  const routes=world.routes||[];

  const content={
    propulsion:{title:'PROPULSION',copy:'Bohun is interested in hearing from companies working on propulsion-related capabilities relevant to defence and dual-use applications. At this stage, we are mapping the field and relevant organisations.'},
    bohun:{title:'BOHUN',copy:'Bohun Defence maps requirements, capabilities, organisations and routes to help establish credible commercial conversations.'},
    ukraine:{title:'UKRAINE',copy:'A major market node in the field. Bohun supports market orientation, partner identification and practical routes to engagement.'},
    europe:{title:'EUROPE / INTERNATIONAL',copy:'International market research, partner and supplier discovery, commercial opportunity mapping and market development.'},
    supplier:{title:'SUPPLIER',copy:'A company, manufacturer or technology provider that may hold a relevant capability.'},
    buyer:{title:'BUYER',copy:'A demand-side actor represented only at category level in the public field. No buyer mandate is implied.'},
    partner:{title:'PARTNER',copy:'Industrial, technology, testing or commercial partners can connect to relevant routes.'}
  };

  const routeIntent={
    'supplier-bohun':'need',
    'propulsion-match':'need',
    'bohun-buyer':'have',
    'bohun-ukraine':'have',
    'bohun-europe':'have',
    'partner-bohun':'both'
  };

  let dpr=Math.min(devicePixelRatio||1,2);
  let W=0,H=0;
  let cam={x:0,y:0,zoom:1,targetX:0,targetY:0,targetZoom:1,initialized:false};
  let tileW=92,tileH=46,origin={x:0,y:0};
  let intent='overview',game=false,selected=null,revealed=false,dragging=false,dragStart=null,camStart=null,scrollP=0;
  let revealStarted=0;

  function resize(){
    const r=canvas.getBoundingClientRect(); W=r.width; H=r.height;
    dpr=Math.min(devicePixelRatio||1,2);
    canvas.width=Math.max(1,Math.floor(W*dpr)); canvas.height=Math.max(1,Math.floor(H*dpr));
    ctx.setTransform(dpr,0,0,dpr,0,0);
    tileW=Math.max(64,Math.min(100,W/14)); tileH=tileW/2;
    origin={x:W*.5,y:H*.22};
    if(!cam.initialized){
      cam.x=cam.targetX=0; cam.y=cam.targetY=0;
      cam.zoom=cam.targetZoom=W<760?.78:1;
      cam.initialized=true;
    }
    if(!game) sceneFromScroll();
  }
  addEventListener('resize',resize);

  const baseIso=(x,y)=>({x:origin.x+(x-y)*tileW*.5,y:origin.y+(x+y)*tileH*.5});
  const iso=(x,y)=>{const p=baseIso(x,y);return{x:p.x+cam.x,y:p.y+cam.y}};
  const entityById=id=>entities.find(e=>e.id===id);
  function worldToScreen(e){
    const p=iso(e.x,e.y);
    return {x:(p.x-W/2)*cam.zoom+W/2,y:(p.y-H/2)*cam.zoom+H/2};
  }

  function diamond(x,y,fill,stroke){
    const p=iso(x,y),tw=tileW*.5*cam.zoom,th=tileH*.5*cam.zoom;
    const sx=(p.x-W/2)*cam.zoom+W/2,sy=(p.y-H/2)*cam.zoom+H/2;
    ctx.beginPath();ctx.moveTo(sx,sy-th);ctx.lineTo(sx+tw,sy);ctx.lineTo(sx,sy+th);ctx.lineTo(sx-tw,sy);ctx.closePath();
    ctx.fillStyle=fill;ctx.fill();if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=1;ctx.stroke();}
  }

  function drawTerrain(){
    ctx.fillStyle='#13160f';ctx.fillRect(0,0,W,H);
    for(let y=0;y<13;y++)for(let x=0;x<17;x++){
      const n=(x*13+y*7)%9;let fill=n<2?'#303625':n<6?'#2a3021':'#252b1d';
      if(x<5&&y>6)fill='#2d3422';if(x>11&&y<6)fill='#293024';
      diamond(x,y,fill,'#59604b33');
    }
    [[3,8],[4,7],[5,6],[6,6],[7,6],[8,7],[9,7],[10,8],[11,8],[12,9],[8,6],[9,5],[10,4],[11,3]].forEach(([x,y])=>diamond(x,y,'#454636','#77745b55'));
    for(let i=0;i<7;i++)diamond(14-i*.65,9+i*.35,'#263b3a','#57706c55');
  }

  function routeEmphasis(r){
    if(intent==='overview')return 1;
    const ri=routeIntent[r.id]||'both';
    return ri===intent||ri==='both'?1:.18;
  }

  function drawRoute(r,t){
    const locked=r.status==='locked';
    if(locked&&!revealed)return;
    const a=entityById(r.from),b=entityById(r.to);if(!a||!b)return;
    const A=worldToScreen(a),B=worldToScreen(b);
    const emphasis=routeEmphasis(r);
    ctx.save();ctx.globalAlpha=emphasis;ctx.lineWidth=locked?3:2;ctx.setLineDash(locked?[2,6]:[6,8]);ctx.strokeStyle=locked?'#e5cc79':'#c1b98a99';
    if(!game&&intent!=='overview'&&emphasis===1){ctx.shadowColor='#d8c07b66';ctx.shadowBlur=8;}
    ctx.beginPath();ctx.moveTo(A.x,A.y);ctx.lineTo(B.x,B.y);ctx.stroke();ctx.restore();
    if(locked&&revealed){
      const m=Math.min(1,Math.max(0,(t-revealStarted)/800));
      const x=A.x+(B.x-A.x)*m,y=A.y+(B.y-A.y)*m;
      ctx.fillStyle='#e5cc79';ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fill();
    }
  }

  function entityEmphasis(e){
    if(selected===e.id||e.id==='bohun')return 1;
    if(intent==='overview')return 1;
    if(intent==='need'&&['supplier','partner','interest'].includes(e.type))return 1;
    if(intent==='have'&&['market','buyer','partner'].includes(e.type))return 1;
    return .25;
  }

  function buildingShape(e,p,highlight,t){
    const z=cam.zoom,base=Math.max(9,13*z),h=e.type==='bohun'?32*z:22*z;
    ctx.save();ctx.translate(p.x,p.y);ctx.fillStyle=highlight?e.accent:'#646a50';
    if(e.type==='market'){
      ctx.beginPath();ctx.moveTo(0,-h);ctx.lineTo(base,-h*.25);ctx.lineTo(base,h*.2);ctx.lineTo(0,h*.7);ctx.lineTo(-base,h*.2);ctx.lineTo(-base,-h*.25);ctx.closePath();ctx.fill();ctx.strokeStyle='#d9d4b577';ctx.stroke();
      ctx.fillStyle='#20251a';ctx.fillRect(-base*.38,-h*.55,base*.76,3*z);
    }else if(e.type==='interest'){
      const pulse=6+(reducedMotion?1:Math.sin(t/350)*3);ctx.strokeStyle='#e5cc79';ctx.lineWidth=2;ctx.beginPath();ctx.arc(0,-8*z,base+pulse,0,Math.PI*2);ctx.stroke();
      ctx.fillStyle='#e5cc79';ctx.beginPath();ctx.moveTo(0,-h);ctx.lineTo(base*.7,0);ctx.lineTo(0,base*.55);ctx.lineTo(-base*.7,0);ctx.closePath();ctx.fill();
    }else{
      const roof=e.type==='buyer'?'#8d6e59':highlight?'#a7955b':'#4f5542';
      ctx.beginPath();ctx.moveTo(-base,0);ctx.lineTo(0,-base*.55);ctx.lineTo(base,0);ctx.lineTo(base,-h);ctx.lineTo(0,-h-base*.55);ctx.lineTo(-base,-h);ctx.closePath();ctx.fill();
      ctx.fillStyle=roof;ctx.beginPath();ctx.moveTo(0,-base*.55);ctx.lineTo(base,0);ctx.lineTo(base,-h);ctx.lineTo(0,-h-base*.55);ctx.closePath();ctx.fill();
      ctx.fillStyle=highlight?'#c6ad64':'#74795c';ctx.beginPath();ctx.moveTo(-base,0);ctx.lineTo(0,-base*.55);ctx.lineTo(0,-h-base*.55);ctx.lineTo(-base,-h);ctx.closePath();ctx.fill();
      if(e.type==='supplier'){ctx.fillStyle='#292e22';ctx.fillRect(-base*.55,-h*.55,base*.38,h*.42);ctx.fillRect(base*.1,-h*.55,base*.38,h*.42);}
      if(e.type==='partner'){ctx.strokeStyle='#c4c9ad';ctx.beginPath();ctx.moveTo(-base*.55,-h*.35);ctx.lineTo(base*.55,-h*.35);ctx.stroke();}
      if(e.type==='bohun'){ctx.strokeStyle='#e5cc79';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(0,-h-base*.7);ctx.lineTo(0,-h-base*1.7);ctx.stroke();ctx.beginPath();ctx.moveTo(0,-h-base*1.7);ctx.lineTo(base*.5,-h-base*1.45);ctx.stroke();}
    }
    ctx.restore();
  }

  function drawEntity(e,t){
    const p=worldToScreen(e),alpha=entityEmphasis(e),highlight=alpha>.6;
    ctx.save();ctx.globalAlpha=alpha;buildingShape(e,p,highlight,t);
    ctx.font=`800 ${Math.max(8,10*cam.zoom)}px ui-monospace,monospace`;ctx.textAlign='center';ctx.fillStyle=highlight?'#e6dfbf':'#969982';ctx.fillText(e.label,p.x,p.y+26*cam.zoom);
    if(selected===e.id){ctx.strokeStyle='#e5cc79';ctx.lineWidth=1;ctx.beginPath();ctx.arc(p.x,p.y-8*cam.zoom,27*cam.zoom,0,Math.PI*2);ctx.stroke();}
    ctx.restore();
  }

  function drawAmbientUnit(t){
    const a=entityById('supplier'),b=entityById('bohun');if(!a||!b)return;
    const A=worldToScreen(a),B=worldToScreen(b),unitT=(t/9000)%1,q=unitT<.5?unitT*2:(1-unitT)*2;
    const x=A.x+(B.x-A.x)*q,y=A.y+(B.y-A.y)*q;ctx.globalAlpha=intent==='have'?.35:1;ctx.fillStyle='#c1b98a';ctx.fillRect(x-4,y-4,8,6);ctx.fillStyle='#22261b';ctx.fillRect(x-5,y+2,3,3);ctx.fillRect(x+2,y+2,3,3);ctx.globalAlpha=1;
  }

  function render(t){
    const ease=reducedMotion?1:.085;cam.x+=(cam.targetX-cam.x)*ease;cam.y+=(cam.targetY-cam.y)*ease;cam.zoom+=(cam.targetZoom-cam.zoom)*ease;
    drawTerrain();routes.forEach(r=>drawRoute(r,t));drawAmbientUnit(t);entities.forEach(e=>drawEntity(e,t));requestAnimationFrame(render);
  }

  function setIntent(next){
    intent=next;shell.classList.toggle('intent-need',next==='need');shell.classList.toggle('intent-have',next==='have');
    document.querySelectorAll('[data-intent]').forEach(b=>b.classList.toggle('active',b.dataset.intent===next));
    hudState.textContent=`STATE / ${next.toUpperCase()}`;
  }
  document.querySelectorAll('[data-intent]').forEach(btn=>btn.addEventListener('click',()=>setIntent(btn.dataset.intent)));

  function openDossier(id){
    const c=content[id]||content.propulsion;dossierTitle.textContent=c.title;dossierCopy.textContent=c.copy;
    if(typeof dossier.showModal==='function')dossier.showModal();else dossier.setAttribute('open','');
  }
  document.querySelectorAll('[data-open]').forEach(btn=>btn.addEventListener('click',()=>openDossier(btn.dataset.open)));

  function focusEntity(id,zoom=1.2,offsetX=0,offsetY=0){
    const e=entityById(id);if(!e)return;
    const p=baseIso(e.x,e.y);
    cam.targetZoom=zoom;
    cam.targetX=W/2 + offsetX/zoom - p.x;
    cam.targetY=H/2 + offsetY/zoom - p.y;
  }

  function sceneFromScroll(){
    if(game||!cam.initialized)return;
    const max=Math.max(1,document.documentElement.scrollHeight-innerHeight);scrollP=Math.max(0,Math.min(1,scrollY/max));
    const hero=document.querySelector('.hero-panel'),sig=document.querySelector('.signal-panel'),boh=document.querySelector('.bohun-panel');
    if(scrollP<.33){
      hero.style.opacity=1;hero.style.transform='none';hero.style.pointerEvents='auto';sig.style.opacity=0;sig.style.pointerEvents='none';boh.style.opacity=0;boh.style.pointerEvents='none';
      cam.targetX=0;cam.targetY=0;cam.targetZoom=W<760?.78:1;hudState.textContent=`STATE / ${intent.toUpperCase()}`;
    }else if(scrollP<.68){
      hero.style.opacity=0;hero.style.transform='translateY(-18px)';hero.style.pointerEvents='none';sig.style.opacity=1;sig.style.transform='none';sig.style.pointerEvents='auto';boh.style.opacity=0;boh.style.pointerEvents='none';
      focusEntity('propulsion',W<760?.96:1.22,W<760?0:-W*.13,H*.02);hudState.textContent='STATE / CURRENT INTEREST';
    }else{
      hero.style.opacity=0;hero.style.pointerEvents='none';sig.style.opacity=0;sig.style.pointerEvents='none';boh.style.opacity=1;boh.style.transform='none';boh.style.pointerEvents='auto';
      focusEntity('bohun',W<760?.96:1.28,W<760?0:W*.12,-H*.04);hudState.textContent='STATE / BOHUN';
    }
  }
  addEventListener('scroll',sceneFromScroll,{passive:true});

  function hitEntity(px,py){
    let best=null,dist=Infinity;for(const e of entities){const p=worldToScreen(e),d=Math.hypot(px-p.x,py-p.y+8*cam.zoom);if(d<36*cam.zoom&&d<dist){best=e;dist=d;}}return best;
  }
  function selectEntity(hit,showFile=false){
    if(!hit)return;selected=hit.id;const p=worldToScreen(hit);selectionLabel.textContent=`${game?'SELECTED':'INSPECT'} / ${hit.label}`;selectionLabel.style.left=`${Math.max(8,Math.min(W-190,p.x+18))}px`;selectionLabel.style.top=`${Math.max(70,Math.min(H-60,p.y-48))}px`;selectionLabel.hidden=false;
    revealButton.disabled=hit.id!=='propulsion'||revealed;
    if(showFile)openDossier(hit.id);
  }

  gameToggle.addEventListener('click',()=>{
    game=!game;shell.classList.toggle('game-active',game);shell.classList.toggle('presentation-inspect',!game);gameToggle.setAttribute('aria-pressed',String(game));gameToggle.textContent=game?'EXIT GAME MODE':'GAME MODE';gameHud.hidden=!game;fieldLegend.hidden=game;selected=null;selectionLabel.hidden=true;revealButton.disabled=true;revealButton.textContent=revealed?'ROUTE REVEALED':'RESEARCH / REVEAL';
    if(game){cam.targetX=0;cam.targetY=0;cam.targetZoom=W<760?.78:1;hudState.textContent='STATE / GAME MODE';}else sceneFromScroll();
  });

  canvas.addEventListener('pointerdown',e=>{
    if(!game)return;dragging=true;dragStart={x:e.clientX,y:e.clientY};camStart={x:cam.targetX,y:cam.targetY};canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove',e=>{
    if(!game||!dragging)return;const dx=e.clientX-dragStart.x,dy=e.clientY-dragStart.y;if(Math.hypot(dx,dy)>4){cam.targetX=camStart.x+dx;cam.targetY=camStart.y+dy;}
  });
  canvas.addEventListener('pointerup',e=>{
    const rect=canvas.getBoundingClientRect();
    if(game){
      const dx=e.clientX-dragStart.x,dy=e.clientY-dragStart.y;dragging=false;if(Math.hypot(dx,dy)>=6)return;
      const hit=hitEntity(e.clientX-rect.left,e.clientY-rect.top);if(hit)selectEntity(hit,hit.id!=='propulsion');
    }else{
      const hit=hitEntity(e.clientX-rect.left,e.clientY-rect.top);if(hit)selectEntity(hit,true);
    }
  });
  canvas.addEventListener('wheel',e=>{if(!game)return;e.preventDefault();cam.targetZoom=Math.max(.58,Math.min(1.65,cam.targetZoom*(e.deltaY>0?.92:1.08)));},{passive:false});
  canvas.addEventListener('keydown',e=>{
    if(e.key!=='Enter'&&e.key!==' ')return;const rect=canvas.getBoundingClientRect();const hit=hitEntity(rect.width/2,rect.height/2);if(hit){e.preventDefault();selectEntity(hit,true);}
  });

  revealButton.addEventListener('click',()=>{
    if(selected!=='propulsion'||revealed)return;revealed=true;revealStarted=performance.now();revealButton.textContent='ROUTE REVEALED';revealButton.disabled=true;setTimeout(()=>openDossier('propulsion'),450);
  });

  canvas.tabIndex=0;shell.classList.add('presentation-inspect');resize();requestAnimationFrame(render);sceneFromScroll();
})();
