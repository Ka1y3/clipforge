const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtT = s => { s = Math.round(s); const h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = s%60; return (h?h+":"+String(m).padStart(2,"0"):m)+":"+String(x).padStart(2,"0"); };
const fmtD = iso => iso ? new Date(iso).toLocaleString("fr-FR",{dateStyle:"short",timeStyle:"short"}) : "";
function toast(msg){const t=$("#toast");t.textContent=msg;t.style.display="block";clearTimeout(toast.h);toast.h=setTimeout(()=>t.style.display="none",4500)}
async function api(path, method="GET", body){
  const r = await fetch(path,{method,headers:body?{"Content-Type":"application/json"}:{},body:body?JSON.stringify(body):undefined});
  if(!r.ok){let d=await r.text();try{d=JSON.parse(d).detail||d}catch{};throw new Error(d)}
  return r.json();
}
const act = (fn) => async (...a) => { try { await fn(...a) } catch(e){ toast("⚠ "+e.message) } };
const tag = s => `<span class="tag ${esc(s)}">${esc(s)}</span>`;
const NAV = [["dashboard","Tableau de bord"],["vods","VOD"],["analysis","Analyses"],["moments","Meilleurs moments"],["clips","Clips"],["publish","Planificateur"],["connections","Connexions"],["settings","Paramètres"]];
let status = {}, timer = null;

async function route(){
  clearInterval(timer);
  const [page="dashboard", arg] = location.hash.slice(2).split("/");
  $("#nav").innerHTML = NAV.map(([k,l])=>`<a href="#/${k}" class="${k===page||(page==="editor"&&k==="moments")?"on":""}">${l}</a>`).join("");
  try { status = await api("/api/status"); } catch(e){
    const hint = location.protocol==="file:" ? "Page ouverte comme fichier : lancez <code>python run.py</code> puis ouvrez <b>http://localhost:8000</b>."
      : /Failed to fetch|NetworkError/i.test(e.message) ? "Le serveur ne répond pas : relancez <code>python run.py</code> et regardez le terminal."
      : "Le serveur répond avec une erreur (détail ci-dessous et dans le terminal / data/clipforge.log).";
    $("#app").innerHTML=`<div class="warn">⚠ ${hint}<br><small>${esc(e.message).slice(0,400)}</small></div>`; return }
  $("#conn").innerHTML = `Twitch : ${status.twitch.connected?"✅ "+esc(status.twitch.user.name):"❌"}<br>YouTube : ${status.platforms.youtube.connected?"✅":"❌"}<br>Whisper : ${status.whisper?"✅":"❌"} · IA : ${status.llm?"✅":"—"}`;
  const p = PAGES[page] || PAGES.dashboard;
  await act(p)(arg);
}
window.addEventListener("hashchange", route); route();
const poll = (fn, ms=2000) => { timer = setInterval(act(fn), ms) };

const PAGES = {
async dashboard(){
  const c = status.counts, jobs = await api("/api/jobs?limit=6");
  const warns = [!status.ffmpeg&&"FFmpeg introuvable", !status.twitch.configured&&"Clés Twitch absentes du .env", !status.whisper&&"faster-whisper non installé : pas de transcription ni de sous-titres", !status.chat_files.length&&"Aucun chat enregistré : lancez python -m app.chat_recorder <chaine> pendant vos lives"].filter(Boolean);
  $("#app").innerHTML = `<h2>Tableau de bord</h2>${warns.map(w=>`<div class="warn">⚠ ${esc(w)}</div>`).join("")}
  <div class="stats">${[["VOD",c.vods],["Moments détectés",c.moments],["Clips prêts",c.clips],["Publiés",c.published]].map(([l,n])=>`<div class="card stat"><b>${n}</b><span>${l}</span></div>`).join("")}</div>
  <div class="card"><h3 style="margin-top:0">Activité récente</h3>${jobsTable(jobs)}</div>`;
},
async vods(){
  const draw = async () => {
    const v = await api("/api/vods");
    $("#vlist").innerHTML = v.length ? `<table><tr><th></th><th>Titre</th><th>Date</th><th>Durée</th><th>Statut</th><th></th></tr>${v.map(x=>`<tr>
      <td>${x.thumbnail?`<img class="thumb" src="${esc(x.thumbnail)}">`:""}</td><td>${esc(x.title)}</td><td>${fmtD(x.created_at)}</td><td>${fmtT(x.duration)}</td>
      <td>${tag(x.status)}${x.error?`<div class="mut">${esc(x.error)}</div>`:""}</td>
      <td class="row">${["queued","analyzing"].includes(x.status)?"":`<button onclick="analyze(${x.id})">${x.moments?"Ré-analyser":"Analyser"}</button>`}${x.moments?`<a class="btn sec" href="#/moments/${x.id}">${x.moments} moments</a>`:""}</td></tr>`).join("")}</table>` : `<p class="mut">Aucune VOD. Connectez Twitch (Paramètres) puis synchronisez, ou ajoutez une URL / un fichier local.</p>`;
  };
  $("#app").innerHTML = `<h2>Bibliothèque de VOD</h2><div class="row" style="margin-bottom:14px"><button id="sync">Synchroniser Twitch</button>
    <input id="addurl" style="max-width:420px" placeholder="URL twitch.tv/videos/… ou chemin d'un fichier vidéo local"><button class="sec" id="add">Ajouter</button></div><div class="card" id="vlist"></div>`;
  window.analyze = act(async id => { await api(`/api/vods/${id}/analyze`,"POST"); toast("Analyse lancée"); draw() });
  $("#sync").onclick = act(async()=>{ const r=await api("/api/vods/sync","POST"); toast(`${r.new} nouvelle(s) VOD`); draw() });
  $("#add").onclick = act(async()=>{ await api("/api/vods","POST",{url:$("#addurl").value}); $("#addurl").value=""; draw() });
  await draw(); poll(draw, 3000);
},
async analysis(){
  const draw = async () => { $("#jl").innerHTML = jobsTable(await api("/api/jobs?limit=40")) };
  $("#app").innerHTML = `<h2>Analyses & tâches</h2><div class="card" id="jl"></div>`; await draw(); poll(draw);
},
async moments(vid){
  const vods=(await api("/api/vods")).filter(v=>v.moments);
  const q={tab:"new",sort:"score",vod:vid||"",first:true}; let sig="";
  const TABS=[["all","Tous"],["new","Nouveau"],["draft","Brouillon"],["exported","Exportés"],["scheduled","Programmés"],["hidden","Masqués"]];
  const LBL={new:"Nouveau",draft:"Brouillon",exported:"Exporté",scheduled:"Programmé",hidden:"Masqué"};
  $("#app").innerHTML=`<h2>Meilleurs moments</h2><div class="tabs" id="tabs"></div><div class="row" style="margin:12px 0">
    <select id="sort" style="width:auto"><option value="score">Tri : score</option><option value="recent">Tri : plus récent</option><option value="duration">Tri : durée</option></select>
    <select id="vodf" style="width:auto"><option value="">Toutes les VOD</option>${vods.map(v=>`<option value="${v.id}" ${v.id==q.vod?"selected":""}>${esc(v.title).slice(0,60)}</option>`).join("")}</select></div><div class="gal" id="gal"></div>`;
  const draw=async(force)=>{
    const r=await api(`/api/gallery?status=${q.tab}&sort=${q.sort}${q.vod?`&vod_id=${q.vod}`:""}`);
    if(q.first){q.first=false; if(r.counts.new===0&&r.counts.all>0){q.tab="all";return draw(true)}}
    $("#tabs").innerHTML=TABS.map(([k,l])=>`<a href="javascript:void 0" data-t="${k}" class="${k===q.tab?"on":""}">${l} <span>${r.counts[k]}</span></a>`).join("");
    document.querySelectorAll("#tabs a").forEach(a=>a.onclick=()=>{q.tab=a.dataset.t;sig="";draw()});
    const nsig=JSON.stringify(r.items.map(m=>[m.id,m.tab,m.score,m.preview&&m.preview.status]))+q.tab; if(nsig===sig&&!force)return; sig=nsig;
    $("#gal").innerHTML=r.items.length?r.items.map(m=>{const pv=m.preview,ready=pv&&pv.status==="preview";
      return `<div class="vcard"><div class="media">${ready?`<video id="v${m.id}" src="/media/clips/${pv.id}" poster="/media/clips/${pv.id}/poster" preload="none" playsinline loop onclick="togglePlay(${m.id})"></video><div class="play" id="p${m.id}" onclick="togglePlay(${m.id})">▶</div>`
        :`<div class="ph">${pv&&pv.status==="error"?"Aperçu en échec — Plus → Régénérer":pv?"Génération de l'aperçu…":"Pas d'aperçu"}</div>`}
        <span class="b st">${LBL[m.tab]||""}</span><span class="b sc" title="Score ${m.score}/100">${Math.max(1,Math.round(m.score/10))}</span><span class="b du">${fmtT(m.duration)}</span></div>
        <div class="ttl" title="${esc(m.title||m.label||"")}">${esc(m.title||m.label||"Moment")}</div>
        <div class="row" style="gap:6px"><a class="btn" style="flex:1;text-align:center" href="#/editor/${m.id}">✎ Éditer</a>
        <button class="sec" onclick="hideM(${m.id},${m.tab!=="hidden"})">${m.tab==="hidden"?"Afficher":"Masquer"}</button>
        <details class="more"><summary class="btn sec">⋯</summary><div class="menu"><a href="javascript:void 0" onclick="regen(${m.id})">Régénérer l'aperçu</a><a href="javascript:void 0" onclick="quick(${m.id})">Exporter en HD (9:16)</a><a href="javascript:void 0" onclick="delM(${m.id})">Supprimer</a></div></details></div></div>`}).join("")
      :`<p class="mut">Rien ici pour l'instant.${vods.length?"":" Analysez une VOD pour générer des clips."}</p>`;
  };
  $("#sort").onchange=e=>{q.sort=e.target.value;sig="";draw()}; $("#vodf").onchange=e=>{q.vod=e.target.value;sig="";draw()};
  window.togglePlay=id=>{const v=$("#v"+id),p=$("#p"+id); document.querySelectorAll("video").forEach(x=>{if(x!==v)x.pause()}); if(v.paused){v.play();v.controls=true;p.style.display="none"}else{v.pause();p.style.display="flex"}};
  window.hideM=act(async(id,h)=>{await api(`/api/moments/${id}/hide?hidden=${h}`,"POST");sig="";draw()});
  window.regen=act(async id=>{await api(`/api/moments/${id}/preview`,"POST");toast("Aperçu en cours de génération");sig="";draw()});
  window.delM=act(async id=>{if(confirm("Supprimer ce moment ?")){await api(`/api/moments/${id}`,"DELETE");sig="";draw()}});
  window.quick=act(async id=>{await api(`/api/moments/${id}/clips`,"POST",{fmt:"9:16"});toast("Export HD lancé → onglet Clips");sig="";draw()});
  await draw(); poll(draw,4000);
},
async editor(mid){
  const m=await api(`/api/moments/${mid}`), st=await api("/api/settings"), dur=m.vod.duration, ed=m.edit||{};
  const E={layout:ed.layout||st.layout||"split",cam:ed.webcam_rect||st.webcam_rect||{x:.02,y:.04,w:.28,h:.3},game:ed.game_rect||st.game_rect||null,
    subtitles:ed.subtitles!==false,style:ed.subtitle_style||st.subtitle_style||"bold",tracks:(ed.audio_tracks||[]).join(","),fmt:"9:16",title:m.title||m.label||"",tab:"layouts",start:m.start,end:m.end};
  const lo=Math.max(0,m.start-30),hi=Math.min(dur,m.end+30);
  const LAY=[["split","Split"],["small_facecam","Petite facecam"],["circle_facecam","Facecam ronde"],["blurred","Flou"],["fullscreen","Plein écran"]];
  const img=new Image(); let ready=false;
  $("#app").innerHTML=`<div class="ed"><div class="ed-side" id="tabs2"></div><div class="ed-panel" id="panel"></div>
   <div class="ed-main"><div class="ed-top"><div style="flex:1;min-width:0"><div class="mut">Source <span id="loadmsg">— chargement de l'image…</span></div>
     <div id="srcbox" class="srcbox"><div id="camb" class="rb cam"><span>Facecam</span><i class="h"></i></div><div id="gameb" class="rb gm"><span>Gameplay</span><i class="h"></i></div></div></div>
    <div><div class="mut">Aperçu <select id="fmtsel" style="width:auto;padding:2px 6px"><option>9:16</option><option>16:9</option><option>1:1</option></select></div>
     <canvas id="pv" class="pv"></canvas><video id="pvv" controls style="display:none;width:270px"></video><div class="row" style="margin-top:6px"><button class="sec" id="vprev">▶ Aperçu vidéo</button><button class="sec" id="vback" style="display:none">Aperçu instantané</button></div><div class="mut" id="vst"></div></div></div>
    <div class="card" style="margin-top:12px"><div class="row"><div style="flex:1"><label>Début <b id="sv"></b></label><input type="range" id="s" min="${lo}" max="${hi}" step="0.5" value="${m.start}"></div>
     <div style="flex:1"><label>Fin <b id="ev"></b></label><input type="range" id="e" min="${lo}" max="${hi}" step="0.5" value="${m.end}"></div><div class="mut" id="dv"></div></div></div></div></div>`;
  const camb=$("#camb"),gameb=$("#gameb"),box=$("#srcbox"),cv=$("#pv");
  const ar=()=>({"9:16":[270,480],"16:9":[480,270],"1:1":[360,360]}[E.fmt]);
  const pxr=(r)=>({x:r.x*img.naturalWidth,y:r.y*img.naturalHeight,w:r.w*img.naturalWidth,h:r.h*img.naturalHeight});
  const gameAspect=()=>{ if(E.layout==="blurred")return null; const c=pxr(E.cam); let h=1920;
    if(E.layout==="split") h=1920-Math.min(1080*c.h/c.w,1920*.36); return 1080/h };
  const fixGame=()=>{ if(!ready)return; const a=gameAspect(),W=img.naturalWidth,H=img.naturalHeight; let g=E.game;
    if(!a){ if(!g)g={x:.1,y:.05,w:.8,h:.9}; E.game=g; return }
    let h=g?g.h:.92, w=h*H*a/W; if(w>1){w=1;h=w*W/(a*H)} const cx=g?g.x+g.w/2:.5, cy=g?g.y+g.h/2:.5;
    E.game={x:Math.min(Math.max(cx-w/2,0),1-w),y:Math.min(Math.max(cy-h/2,0),1-h),w,h} };
  const place=()=>{ for(const [el,r] of [[camb,E.cam],[gameb,E.game]]){ if(!r){el.style.display="none";continue} el.style.display="block";
    Object.assign(el.style,{left:r.x*100+"%",top:r.y*100+"%",width:r.w*100+"%",height:r.h*100+"%"}) } camb.style.display=(E.layout==="blurred"||E.layout==="fullscreen"||E.fmt!=="9:16")?"none":"block"; };
  function paint(c,w,h,layout,fmt){ const x=c.getContext("2d"); x.fillStyle="#000"; x.fillRect(0,0,w,h); if(!ready)return;
    const W=img.naturalWidth,H=img.naturalHeight, cover=(r,dx,dy,dw,dh)=>{const s=pxr(r),k=Math.max(dw/s.w,dh/s.h),sw=dw/k,sh=dh/k; x.drawImage(img,s.x+(s.w-sw)/2,s.y+(s.h-sh)/2,sw,sh,dx,dy,dw,dh)};
    if(fmt==="16:9"){x.drawImage(img,0,0,w,h);return} if(fmt==="1:1"){cover({x:0,y:0,w:1,h:1},0,0,w,h);return}
    const g=E.game||{x:.25,y:0,w:.5,h:1}, cp=pxr(E.cam);
    if(layout==="split"){ const wh=Math.min(w*cp.h/cp.w,h*.36); cover(E.cam,0,0,w,wh); cover(g,0,wh,w,h-wh) }
    else if(layout==="small_facecam"){ cover(g,0,0,w,h); const cw=w*.5,ch=cw*cp.h/cp.w; x.drawImage(img,cp.x,cp.y,cp.w,cp.h,(w-cw)/2,h*.05,cw,ch) }
    else if(layout==="circle_facecam"){ cover(g,0,0,w,h); const d=w*.42,s=Math.min(cp.w,cp.h); x.save(); x.beginPath(); x.arc(w/2,h*.06+d/2,d/2,0,7); x.clip();
      x.drawImage(img,cp.x+(cp.w-s)/2,cp.y+(cp.h-s)/2,s,s,(w-d)/2,h*.06,d,d); x.restore() }
    else if(layout==="blurred"){ x.filter="blur(8px)"; cover(g,0,0,w,h); x.filter="none"; const gp=pxr(g),fh=w*gp.h/gp.w; x.drawImage(img,gp.x,gp.y,gp.w,gp.h,0,(h-fh)/2,w,fh) }
    else cover(g,0,0,w,h);
  }
  let raf=0; const redraw=()=>{ cancelAnimationFrame(raf); raf=requestAnimationFrame(()=>{ const [w,h]=ar(); cv.width=w; cv.height=h; paint(cv,w,h,E.layout,E.fmt);
    document.querySelectorAll("canvas.lt").forEach(c=>paint(c,c.width,c.height,c.dataset.l,"9:16")) }) };
  const changed=()=>{ place(); redraw() };
  // glisser / redimensionner
  const drag=(el,get,set,resizeLock)=>{ el.onmousedown=e=>{ e.preventDefault(); const b=box.getBoundingClientRect(),r0={...get()},p0={x:e.clientX,y:e.clientY},rs=e.target.classList.contains("h");
    const mv=ev=>{ const dx=(ev.clientX-p0.x)/b.width,dy=(ev.clientY-p0.y)/b.height; let r={...r0};
      if(!rs){ r.x=Math.min(Math.max(r0.x+dx,0),1-r0.w); r.y=Math.min(Math.max(r0.y+dy,0),1-r0.h) }
      else { r.w=Math.min(Math.max(r0.w+dx,.05),1-r0.x); const a=resizeLock&&resizeLock(); r.h=a?r.w*img.naturalWidth/(a*img.naturalHeight):Math.min(Math.max(r0.h+dy,.05),1-r0.y);
        if(r.y+r.h>1){ r.h=1-r.y; if(a)r.w=r.h*a*img.naturalHeight/img.naturalWidth } }
      set(r); changed() };
    const up=()=>{window.removeEventListener("mousemove",mv);window.removeEventListener("mouseup",up)}; window.addEventListener("mousemove",mv);window.addEventListener("mouseup",up) } };
  drag(camb,()=>E.cam,r=>{E.cam=r;fixGame()}); drag(gameb,()=>E.game,r=>{E.game=r},gameAspect);
  img.onload=()=>{ ready=true; $("#loadmsg").textContent=""; box.insertBefore(img,box.firstChild); fixGame(); changed() };
  img.onerror=()=>{$("#loadmsg").textContent="— image indisponible (VOD inaccessible ?)"};
  img.src=`/api/vods/${m.vod.id}/frame?t=${Math.round(m.peak||m.start+10)}`; img.draggable=false;
  // panneaux
  const TABS=[["layouts","▦","Mises en page"],["captions","CC","Sous-titres"],["audio","♪","Audio"],["export","⬆","Export"]];
  const panel=()=>{ $("#tabs2").innerHTML=TABS.map(([k,i,l])=>`<a href="javascript:void 0" data-k="${k}" class="${E.tab===k?"on":""}"><b>${i}</b>${l}</a>`).join("");
    document.querySelectorAll("#tabs2 a").forEach(a=>a.onclick=()=>{E.tab=a.dataset.k;panel()});
    const P=$("#panel");
    if(E.tab==="layouts"){ P.innerHTML=`<h3>Choisissez votre mise en page</h3><div class="lgrid">${LAY.map(([k,l])=>`<div class="lc ${E.layout===k?"on":""}" data-l="${k}"><canvas class="lt" data-l="${k}" width="110" height="196"></canvas><div>${l}</div></div>`).join("")}</div>
      <p class="mut" style="font-size:12px">Déplacez le cadre violet (webcam) et le cadre bleu (jeu) sur l'image source.</p>`;
      P.querySelectorAll(".lc").forEach(c=>c.onclick=()=>{E.layout=c.dataset.l;fixGame();panel();changed()}) }
    else if(E.tab==="captions"){ P.innerHTML=`<h3>Sous-titres automatiques</h3><label><input type="checkbox" id="csub" ${E.subtitles?"checked":""}> Activer</label><label>Style</label>
      <select id="csty">${[["bold","Gras blanc"],["yellow","Jaune"],["karaoke","Karaoké"],["minimal","Minimal"]].map(([k,l])=>`<option value="${k}" ${E.style===k?"selected":""}>${l}</option>`).join("")}</select>
      <p class="mut" style="font-size:12px">Nécessite faster-whisper. Visibles dans l'export (pas dans l'aperçu instantané).</p>`;
      $("#csub").onchange=e=>E.subtitles=e.target.checked; $("#csty").onchange=e=>E.style=e.target.value }
    else if(E.tab==="audio"){ P.innerHTML=`<h3>Audio</h3><label>Pistes à mixer (indices, ex : 0,1)</label><input id="atr" value="${esc(E.tracks)}" placeholder="vide = piste 0"><p class="mut" style="font-size:12px">Utile si votre VOD contient plusieurs pistes (jeu + micro).</p>`;
      $("#atr").oninput=e=>E.tracks=e.target.value }
    else { P.innerHTML=`<h3>Export</h3><label>Titre</label><input id="etl" value="${esc(E.title)}"><label>Format</label><select id="efm">${["9:16","16:9","1:1"].map(f=>`<option ${E.fmt===f?"selected":""}>${f}</option>`).join("")}</select>
      <label><input type="checkbox" id="edef"> Utiliser cette mise en page pour tous les prochains clips</label>
      <div class="row" style="margin-top:14px"><button class="sec" id="esv">Enregistrer en brouillon</button><button id="eex">Exporter</button></div>`;
      $("#etl").oninput=e=>E.title=e.target.value; $("#efm").onchange=e=>{E.fmt=e.target.value;$("#fmtsel").value=E.fmt;changed()};
      $("#esv").onclick=act(async()=>{await save();toast("Brouillon enregistré")});
      $("#eex").onclick=act(async()=>{await save(); if($("#edef").checked) await api("/api/settings","PUT",{layout:E.layout,webcam_rect:E.cam,game_rect:E.game,subtitle_style:E.style});
        await api(`/api/moments/${mid}/clips`,"POST",{fmt:E.fmt,options:opts(),title:E.title}); toast("Export lancé → onglet Clips"); location.hash="#/clips"}) }
    changed() };
  const opts=()=>{ const o={start:+$("#s").value,end:+$("#e").value,subtitles:E.subtitles,subtitle_style:E.style,layout:E.layout,crop_x:"auto",webcam_rect:E.cam,game_rect:E.game};
    const a=E.tracks.split(",").map(x=>parseInt(x)).filter(x=>x>=0); if(a.length)o.audio_tracks=a; return o };
  const save=async()=>{ const o=opts(); await api(`/api/moments/${mid}`,"PATCH",{start:o.start,end:o.end,title:E.title,edit:o}) };
  const upd=()=>{ if(+$("#s").value>=+$("#e").value-3)$("#s").value=+$("#e").value-3; $("#sv").textContent=fmtT($("#s").value);$("#ev").textContent=fmtT($("#e").value);$("#dv").textContent=Math.round($("#e").value-$("#s").value)+" s" };
  $("#s").oninput=$("#e").oninput=upd; upd();
  $("#fmtsel").onchange=e=>{E.fmt=e.target.value;changed()};
  $("#vprev").onclick=act(async()=>{ await save(); const r=await api(`/api/moments/${mid}/clips`,"POST",{fmt:E.fmt,draft:true,options:opts(),title:E.title});
    $("#vst").textContent="Rendu de l'aperçu vidéo…"; clearInterval(timer);
    timer=setInterval(act(async()=>{ const c=await api(`/api/clips/${r.clip}`); if(c.status==="preview"){clearInterval(timer);cv.style.display="none";const v=$("#pvv");v.style.display="block";v.src=`/media/clips/${r.clip}?t=${Date.now()}`;$("#vback").style.display="inline-block";$("#vst").textContent=""}
      else if(c.status==="error"){clearInterval(timer);$("#vst").textContent="Erreur de rendu (voir Analyses)"} }),1500) });
  $("#vback").onclick=()=>{$("#pvv").pause();$("#pvv").style.display="none";cv.style.display="block";$("#vback").style.display="none"};
  panel();
},
async clips(){
  const draw = async () => { const c = await api("/api/clips");
    $("#cl").innerHTML = c.length ? c.map(x=>`<div class="card"><b>${esc(x.title||"Clip "+x.id)}</b> ${tag(x.status)}<p class="mut">${x.fmt} · ${x.duration?Math.round(x.duration)+" s":""}${x.score?` · score ${x.score}`:""}</p>
      ${x.status==="done"?`<video controls preload="metadata" src="/media/clips/${x.id}"></video><div class="row" style="margin-top:8px"><a class="btn sec" href="/media/clips/${x.id}?download=true">Télécharger</a><a class="btn" href="#/publish/${x.id}">Publier</a><button class="danger" onclick="delClip(${x.id})">Supprimer</button></div>`:`<div class="bar"><i style="width:40%"></i></div>`}</div>`).join("") : `<p class="mut">Aucun clip. Exportez un moment depuis « Meilleurs moments ».</p>` };
  $("#app").innerHTML = `<h2>Bibliothèque de clips</h2><div class="grid" id="cl"></div>`;
  window.delClip = act(async id => { if(confirm("Supprimer ce clip ?")){ await api(`/api/clips/${id}`,"DELETE"); draw() } });
  await draw(); poll(draw,4000);
},
async publish(cid){
  let [pubs,clips,st]=await Promise.all([api("/api/publications"),api("/api/clips"),api("/api/settings")]); clips=clips.filter(c=>c.status==="done");
  const P=status.platforms; let cur=new Date(); cur.setDate(1); cur.setHours(0,0,0,0); let flt="",sig="";
  const DAYS=["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"], PL={youtube:"YT",tiktok:"TT",instagram:"IG"};
  const hhmm=d=>d.toLocaleTimeString("fr-FR",{hour:"2-digit",minute:"2-digit"}), key=d=>d.getFullYear()+"-"+d.getMonth()+"-"+d.getDate();
  $("#app").innerHTML=`<div class="row" style="justify-content:space-between;margin-bottom:14px"><h2 style="margin:0">Planificateur</h2><div class="row"><select id="pf" style="width:auto"><option value="">Toutes les plateformes</option>${Object.keys(P).map(k=>`<option>${k}</option>`).join("")}</select><button class="sec" id="ptimes">⏱ Horaires de publication</button><button id="pnew">＋ Planifier une publication</button></div></div>
    <div class="row" style="justify-content:center;margin-bottom:10px"><button class="sec" id="pp">‹</button><button class="sec" id="pt">Aujourd'hui</button><button class="sec" id="pn">›</button><h3 id="pm" style="margin:0 12px;min-width:170px;text-align:center"></h3></div><div id="cal"></div><div id="modal"></div>`;
  const draw=()=>{ const by={}; pubs.filter(p=>!flt||p.platform===flt).forEach(p=>{const d=new Date((p.scheduled_at||p.created)*1000); (by[key(d)]=by[key(d)]||[]).push([d,p])});
    $("#pm").textContent=cur.toLocaleDateString("fr-FR",{month:"long",year:"numeric"});
    const first=new Date(cur), off=(first.getDay()+6)%7, start=new Date(first); start.setDate(1-off); const today=new Date(); today.setHours(0,0,0,0); const weeks=Math.ceil((off+new Date(cur.getFullYear(),cur.getMonth()+1,0).getDate())/7);
    let h=`<div class="cal">${DAYS.map(d=>`<div class="ch">${d}</div>`).join("")}`;
    for(let i=0;i<weeks*7;i++){ const d=new Date(start); d.setDate(start.getDate()+i); const inM=d.getMonth()===cur.getMonth(), items=(by[key(d)]||[]).sort((a,b)=>a[0]-b[0]), future=d>=today, taken=items.map(x=>hhmm(x[0]));
      const slots=future&&inM?(st.posting_times||[]).filter(t=>!taken.includes(t)):[];
      h+=`<div class="cd ${inM?"":"out"} ${key(d)===key(new Date())?"today":""}"><div class="dn">${d.getDate()}</div>${items.map(([dt,p])=>`<a href="javascript:void 0" class="post ${esc(p.status)}" onclick="postClick(${p.id})" title="${esc(p.title||"")}">${hhmm(dt)} ${PL[p.platform]||""} ${esc(p.title||"")}</a>`).join("")}
        ${slots.map(t=>`<a href="javascript:void 0" class="slot" onclick="openModal('${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}T${t}')">${t}</a>`).join("")}</div>` }
    $("#cal").innerHTML=h+"</div>" };
  const render=()=>{ const s=JSON.stringify(pubs.map(p=>[p.id,p.status]))+flt+cur; if(s===sig)return; sig=s; draw() };
  const refresh=async()=>{ pubs=await api("/api/publications"); render() };
  $("#pp").onclick=()=>{cur.setMonth(cur.getMonth()-1);sig="";render()}; $("#pn").onclick=()=>{cur.setMonth(cur.getMonth()+1);sig="";render()};
  $("#pt").onclick=()=>{cur=new Date();cur.setDate(1);cur.setHours(0,0,0,0);sig="";render()}; $("#pf").onchange=e=>{flt=e.target.value;sig="";render()};
  $("#ptimes").onclick=act(async()=>{ const v=prompt("Horaires de publication (HH:MM séparés par des virgules)",(st.posting_times||[]).join(", ")); if(v===null)return;
    const t=v.split(",").map(x=>x.trim()).filter(Boolean); if(!t.every(x=>/^([01]\d|2[0-3]):[0-5]\d$/.test(x))) throw new Error("Format attendu : 08:00, 14:30, 19:55");
    st=await api("/api/settings","PUT",{posting_times:t.sort()}); sig="";render() });
  window.postClick=act(async id=>{ const p=pubs.find(x=>x.id===id); if(p.external_url){window.open(p.external_url,"_blank","noopener");return}
    if(["scheduled","error"].includes(p.status)){ if(confirm(`Annuler cette publication (${p.platform}) ?`)){await api(`/api/publications/${id}`,"DELETE");refresh()} } else toast(`${p.platform} : ${p.status}${p.error?" — "+p.error:""}`) });
  const PRIV={PUBLIC_TO_EVERYONE:"Tout le monde",MUTUAL_FOLLOW_FRIENDS:"Amis (abonnement mutuel)",FOLLOWER_OF_CREATOR:"Mes abonnés",SELF_ONLY:"Moi uniquement (privé)"};
  window.openModal=(when,clipId)=>{ if(!clips.length){toast("Aucun clip prêt : exportez d'abord un clip.");return}
    $("#modal").innerHTML=`<div class="mback"><div class="card mbox"><h3 style="margin-top:0">Planifier une publication</h3><label>Clip</label><select id="mc">${clips.map(c=>`<option value="${c.id}" ${c.id==clipId?"selected":""}>#${c.id} ${esc(c.title||"")} (${c.fmt})</option>`).join("")}</select>
      <label>Plateforme</label><select id="mp">${Object.entries(P).map(([k,v])=>`<option value="${k}" ${v.implemented&&v.connected?"":"disabled"}>${k}${!v.implemented?" (non implémenté)":!v.connected?" (non connecté)":""}</option>`).join("")}</select>
      <div id="tk" style="display:none"><label>Mode TikTok</label><select id="tkmode"><option value="direct">Publication directe</option><option value="draft">Brouillon (je termine dans l'app TikTok)</option></select>
       <div id="tkd"><label>Visibilité (à choisir obligatoirement)</label><select id="tkpriv"><option value="">— choisir —</option></select><label><input type="checkbox" id="tkcom" checked> Commentaires</label><label><input type="checkbox" id="tkduo" checked> Duos</label><label><input type="checkbox" id="tkst" checked> Stitchs</label>
       <label><input type="checkbox" id="tkok"> J'autorise ClipForge à envoyer cette vidéo à mon compte TikTok</label><p class="mut" id="tkinfo"></p></div></div>
      <label>Titre</label><input id="mt"><label>Description</label><textarea id="md" rows="2"></textarea><label>Hashtags</label><input id="mh" value="${esc(st.default_hashtags)}">
      <label>Date et heure</label><input type="datetime-local" id="mw" value="${when||""}"><div class="row" style="margin-top:14px"><button id="mgo">Planifier</button><button class="sec" id="mnow">Publier maintenant</button><button class="sec" id="mx">Fermer</button></div></div></div>`;
    const fill=()=>{const c=clips.find(x=>x.id==$("#mc").value);if(c)$("#mt").value=c.title||""}; $("#mc").onchange=fill; fill();
    let loaded=false; const tk=act(async()=>{ const on=$("#mp").value==="tiktok"; $("#tk").style.display=on?"block":"none"; $("#tkd").style.display=on&&$("#tkmode").value==="direct"?"block":"none";
      if(on&&!loaded){loaded=true;const c=await api("/api/tiktok/creator");$("#tkpriv").innerHTML=`<option value="">— choisir —</option>`+(c.privacy_level_options||[]).map(p=>`<option value="${esc(p)}">${esc(PRIV[p]||p)}</option>`).join("");
        $("#tkinfo").textContent=`Compte : @${c.creator_username||"?"} — durée max ${c.max_video_post_duration_sec||"?"} s. Tant que l'app TikTok n'est pas auditée, seule la visibilité privée est acceptée.`} });
    $("#mp").onchange=tk; $("#tkmode").onchange=tk; tk();
    const send=act(async when=>{ let options={}; if($("#mp").value==="tiktok"){ options={mode:$("#tkmode").value}; if(options.mode==="direct"){ if(!$("#tkpriv").value)throw new Error("Choisissez la visibilité TikTok"); if(!$("#tkok").checked)throw new Error("Cochez l'autorisation d'envoi à TikTok");
        Object.assign(options,{privacy:$("#tkpriv").value,disable_comment:!$("#tkcom").checked,disable_duet:!$("#tkduo").checked,disable_stitch:!$("#tkst").checked})} }
      await api("/api/publications","POST",{clip_id:+$("#mc").value,platform:$("#mp").value,title:$("#mt").value,description:$("#md").value,hashtags:$("#mh").value,scheduled_at:when,options});
      $("#modal").innerHTML=""; toast(when?"Publication planifiée":"Publication lancée"); refresh() });
    $("#mgo").onclick=()=>{ if(!$("#mw").value){toast("⚠ Choisissez une date et une heure");return} send(new Date($("#mw").value).getTime()/1000) }; $("#mnow").onclick=()=>send(null); $("#mx").onclick=()=>$("#modal").innerHTML="" };
  $("#pnew").onclick=()=>{ const d=new Date(Date.now()+3600e3); openModal(`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}T${String(d.getHours()).padStart(2,"0")}:00`) };
  render(); if(cid) openModal("",+cid); poll(refresh,5000);
},
async connections(){
  const P=status.platforms, tw=status.twitch;
  const card=(id,name,ok,conf,impl,loginHref,extra="")=>`<div class="card"><h3 style="margin-top:0">${name}</h3><p>${ok?"✅ Connecté"+extra:"❌ Non connecté"}</p>${!impl?`<p class="mut">Non implémenté</p>`:!conf?`<p class="mut">Clés manquantes dans le .env</p>`:ok?`<button class="sec" onclick="disc('${id}')">Déconnecter</button>`:`<a class="btn" href="${loginHref}">Connecter</a>`}</div>`;
  $("#app").innerHTML=`<h2>Connexions</h2><div class="grid">${card("twitch","Twitch",tw.connected,tw.configured,true,"/auth/twitch/login",tw.user?" — "+esc(tw.user.name):"")}
    ${card("tiktok","TikTok",P.tiktok.connected,P.tiktok.configured,P.tiktok.implemented,"/auth/tiktok/login")}${card("youtube","YouTube Shorts",P.youtube.connected,P.youtube.configured,P.youtube.implemented,"/auth/google/login")}
    ${card("instagram","Instagram Reels",false,false,false,"")}</div>`;
  window.disc=act(async id=>{ if(confirm("Déconnecter ce compte ?")){ await api(`/auth/${id}/logout`,"POST"); route() } });
},
async settings(){
  const s = await api("/api/settings"); const P = status.platforms;
  $("#app").innerHTML = `<h2>Paramètres</h2><div class="split"><div class="card"><h3 style="margin-top:0">Comptes</h3>
    <p>Twitch : ${status.twitch.connected?`✅ ${esc(status.twitch.user.name)} <button class="sec" id="tlo">Déconnecter</button>`:`<a class="btn" href="/auth/twitch/login">Connecter Twitch</a>`}</p>
    <p>YouTube Shorts : ${P.youtube.connected?"✅ connecté":`<a class="btn" href="/auth/google/login">Connecter YouTube</a>`}</p>
    <p>TikTok : ${P.tiktok.connected?"✅ connecté":`<a class="btn" href="/auth/tiktok/login">Connecter TikTok</a>`}</p>
    <p class="mut">Instagram : publication non implémentée.</p>
    <h3>Chat</h3><p class="mut">Pendant vos lives : <code>python -m app.chat_recorder ${esc(status.twitch.user?.login||"votre_chaine")}</code><br>Enregistrés : ${status.chat_files.map(esc).join(", ")||"aucun"}</p></div>
  <div class="card"><h3 style="margin-top:0">Détection & export</h3>
   <div class="row"><div style="flex:1"><label>Moments par VOD</label><input id="max_moments" type="number" value="${s.max_moments}"></div><div style="flex:1"><label>Durée min (s)</label><input id="clip_min" type="number" value="${s.clip_min}"></div><div style="flex:1"><label>Durée max (s)</label><input id="clip_max" type="number" value="${s.clip_max}"></div></div>
   <label>Style de sous-titres par défaut</label><select id="subtitle_style">${["bold","yellow","karaoke","minimal"].map(x=>`<option ${x==s.subtitle_style?"selected":""}>${x}</option>`).join("")}</select>
   <label>Hashtags par défaut</label><input id="default_hashtags" value="${esc(s.default_hashtags)}">
   <label>Zone webcam (clips 9:16 : webcam en haut, jeu en bas)</label>
   <div class="row"><select id="pv" style="flex:1">${(await api("/api/vods")).map(v=>`<option value="${v.id}">${esc(v.title).slice(0,50)}</option>`).join("")}</select><input id="pt" type="number" value="120" style="width:90px" title="seconde de la VOD"><button class="sec" id="pl">Afficher une image</button><button class="sec" id="pc">Effacer</button></div>
   <div id="pbox" style="position:relative;display:none;margin-top:8px;user-select:none;cursor:crosshair"><img id="pimg" style="width:100%;display:block;border-radius:8px" draggable="false"><div id="prect" style="position:absolute;border:2px solid #ff5c8a;background:rgba(255,92,138,.2);display:none;pointer-events:none"></div></div>
   <p class="mut" id="ptxt">${s.webcam_rect?"Zone enregistrée.":"Aucune zone : le clip 9:16 sera un simple recadrage centré."} Choisissez une VOD, affichez une image, puis tracez un rectangle autour de la webcam.</p>
   <label><input type="checkbox" id="auto_previews" ${s.auto_previews?"checked":""}> Créer automatiquement un aperçu 9:16 de chaque moment détecté</label>
   <label><input type="checkbox" id="use_llm" ${s.use_llm?"checked":""}> Évaluation IA des moments (si clé API)</label>
   <label><input type="checkbox" id="auto_sync" ${s.auto_sync?"checked":""}> Synchroniser les nouvelles VOD automatiquement</label>
   <label><input type="checkbox" id="auto_analyze" ${s.auto_analyze?"checked":""}> Analyser automatiquement les nouvelles VOD (< 7 jours)</label>
   <div class="row" style="margin-top:14px"><button id="save">Enregistrer</button><button class="sec" id="purge">Vider le cache vidéo</button></div></div></div>`;
  $("#tlo")&&($("#tlo").onclick=act(async()=>{ await api("/auth/twitch/logout","POST"); route() }));
  let rect=s.webcam_rect&&s.webcam_rect.w<=1?s.webcam_rect:null, drag=null;
  const showRect=()=>{const r=$("#prect"); if(!rect){r.style.display="none";return} Object.assign(r.style,{display:"block",left:rect.x*100+"%",top:rect.y*100+"%",width:rect.w*100+"%",height:rect.h*100+"%"})};
  $("#pl").onclick=act(async()=>{ const img=$("#pimg"); $("#ptxt").textContent="Chargement de l'image… (peut durer quelques secondes pour une VOD Twitch)";
    img.onload=()=>{$("#pbox").style.display="block";showRect();$("#ptxt").textContent="Tracez un rectangle autour de la webcam, puis cliquez sur Enregistrer."}; img.onerror=()=>{$("#ptxt").textContent="Image indisponible."};
    img.src=`/api/vods/${$("#pv").value}/frame?t=${+$("#pt").value||120}&_=${Date.now()}` });
  const pos=e=>{const b=$("#pimg").getBoundingClientRect(); return {x:Math.min(Math.max((e.clientX-b.left)/b.width,0),1),y:Math.min(Math.max((e.clientY-b.top)/b.height,0),1)}};
  $("#pbox").onmousedown=e=>{drag=pos(e);e.preventDefault()};
  window.onmousemove=e=>{ if(!drag)return; const p=pos(e); rect={x:Math.min(drag.x,p.x),y:Math.min(drag.y,p.y),w:Math.abs(p.x-drag.x),h:Math.abs(p.y-drag.y)}; showRect() };
  window.onmouseup=()=>{ if(drag){drag=null; if(rect&&(rect.w<0.04||rect.h<0.04)){rect=null;showRect()}} };
  $("#pc").onclick=()=>{rect=null;showRect();$("#ptxt").textContent="Zone effacée (pensez à enregistrer)."};
  $("#purge").onclick=act(async()=>{ await api("/api/maintenance/purge-cache","POST"); toast("Cache nettoyé") });
  $("#save").onclick=act(async()=>{
    await api("/api/settings","PUT",{max_moments:+$("#max_moments").value,clip_min:+$("#clip_min").value,clip_max:+$("#clip_max").value,subtitle_style:$("#subtitle_style").value,
      default_hashtags:$("#default_hashtags").value,webcam_rect:rect,auto_previews:$("#auto_previews").checked,use_llm:$("#use_llm").checked,auto_sync:$("#auto_sync").checked,auto_analyze:$("#auto_analyze").checked}); toast("Enregistré") });
}};
function chips(f){ const c=[]; if(f.audio!=null)c.push(`audio ${Math.round(f.audio*100)}`); if(f.chat!=null)c.push(`chat ${Math.round(f.chat*100)}`); if(f.video)c.push(`action ${Math.round(f.video*100)}`); if(f.text)c.push(`voix ${Math.round(f.text*100)}`); if(f.llm!=null)c.push(`IA ${f.llm}/10`); return c.map(x=>`<span class="tag">${x}</span>`).join("") }
function jobsTable(j){ return j.length?`<table><tr><th>#</th><th>Type</th><th>Statut</th><th>Progression</th><th></th></tr>${j.map(x=>`<tr><td>${x.id}</td><td>${esc(x.kind)}</td><td>${tag(x.status)}</td><td style="width:30%"><div class="bar"><i style="width:${Math.round((x.progress||0)*100)}%"></i></div></td><td class="mut">${esc(x.error||x.message||"")}</td></tr>`).join("")}</table>`:`<p class="mut">Rien pour l'instant.</p>` }
function jobsPubs(p){ return p.length?`<table>${p.map(x=>`<tr><td>${esc(x.platform)}</td><td>${esc(x.title||"")}</td><td>${tag(x.status)}</td><td>${x.scheduled_at?fmtD(x.scheduled_at*1000):""}</td><td>${x.external_url?`<a href="${esc(x.external_url)}" target="_blank" rel="noopener">voir</a>`:esc(x.error||"")}</td><td>${["scheduled","error"].includes(x.status)?`<button class="sec" onclick="cancelPub(${x.id})">✕</button>`:""}</td></tr>`).join("")}</table>`:`<p class="mut">Aucune publication.</p>` }
