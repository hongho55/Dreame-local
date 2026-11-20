"""Web-UI / REST-API (HA-Ingress oder direkt auf Port 8099)."""
from __future__ import annotations

import base64
import binascii
import json
import logging
import math
import re
import struct
import zlib
from pathlib import Path
from typing import Any

from aiohttp import web

import events
from device import Device, MODE, SUCTION, WATER

log = logging.getLogger("web")

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta http-equiv="cache-control" content="no-store">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>드리미 로컬</title>
<style>
:root{color-scheme:light dark}
body{font:15px/1.4 system-ui,sans-serif;margin:0;padding:16px;max-width:780px}
h1{font-size:1.2rem;margin:.2rem 0 1rem}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.card{background:#8882;border-radius:10px;padding:10px}
.k{font-size:.75rem;opacity:.7;text-transform:uppercase;letter-spacing:.04em}
.v{font-size:1.15rem;font-weight:600;margin-top:2px}
button{font:inherit;padding:10px 14px;border-radius:10px;border:1px solid #8886;background:#8881;cursor:pointer}
button:active{transform:scale(.97)}
.row{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
pre{background:#8881;padding:10px;border-radius:10px;overflow:auto;font-size:12px}
input,select,textarea{font:inherit;padding:8px;border-radius:8px;border:1px solid #8886;background:transparent}
.off{color:#e55}.on{color:#3c5}
</style></head><body>
<h1>드리미 로컬 <small id="ver" style="font-size:.7rem;opacity:.6"></small></h1>
<div class="card"><span class="k">연결 상태</span><div class="v" id="conn">…</div>
<div class="k" id="clients" style="margin-top:6px;text-transform:none;letter-spacing:0"></div></div>
<div class="grid" id="cards" style="margin-top:8px"></div>
<div class="row">
  <button data-cmd="start">청소 시작</button>
  <button data-cmd="pause">일시정지</button>
  <button data-cmd="stop">정지</button>
  <button data-cmd="return_to_base">충전대로 복귀</button>
  <button data-cmd="locate">로봇 찾기</button>
</div>
<div class="row">
  <select id="suction"><option value="">흡입 세기…</option><option value="quiet">저소음</option><option value="standard">표준</option><option value="strong">강력</option><option value="turbo">터보</option></select>
  <select id="water"><option value="">물 사용량…</option><option value="low">적게</option><option value="medium">보통</option><option value="high">많이</option></select>
  <select id="mode"><option value="">청소 방식…</option><option value="sweeping">흡입 청소</option><option value="mopping">물걸레 청소</option><option value="sweeping_and_mopping">흡입·물걸레 동시 청소</option></select>
</div>
<div class="row"><button id="refresh">새로고침</button>
<button id="mapreq">지도 요청</button>
<button id="mapcheck">지도 점검</button>
<a href="rooms" style="align-self:center">지도·방 관리 →</a></div>
<pre id="log"></pre>
<h3 style="font-size:1rem">진단 정보</h3>
<pre id="diag">…</pre>
<script>
async function j(p, opt){ const r = await fetch(p, opt); return r.json(); }
// Display labels only: API values and command payloads stay unchanged.
const LABELS_KO={unknown:'알 수 없음',idle:'대기 중',paused:'일시정지',error:'오류',
  returning:'충전대로 복귀 중',charging:'충전 중',charging_completed:'충전 완료',
  sweeping:'흡입 청소',mopping:'물걸레 청소',sweeping_and_mopping:'흡입·물걸레 동시 청소',
  mopping_after_sweeping:'흡입 후 물걸레 청소',drying:'물걸레 건조 중',washing:'물걸레 세척 중',
  returning_to_wash:'물걸레 세척을 위해 복귀 중',washing_paused:'물걸레 세척 일시정지',
  auto_emptying:'먼지 비우는 중',building:'지도 작성 중',upgrading:'업데이트 중',
  clean_add_water:'물 보충 중',waiting_for_task:'작업 대기 중',smart_charging:'스마트 충전 중',
  quiet:'저소음',standard:'표준',strong:'강력',turbo:'터보',low:'적게',medium:'보통',high:'많이',
  no_error:'없음',none:'없음'};
function label(v){return v==null?v:(LABELS_KO[String(v).toLowerCase()]??v)}
function card(k,v){ return `<div class="card"><div class="k">${k}</div><div class="v">${v??'–'}</div></div>`; }
async function tick(){
  try {
    const s = await j('api/state?t=' + Date.now());
    document.getElementById('ver').textContent = s.version ? 'v' + s.version : '';
    document.getElementById('conn').innerHTML = s.online ? '<span class="on">연결됨</span>' : '<span class="off">연결 끊김</span>';
    document.getElementById('clients').textContent = (s.clients || []).map(c => c.client_id).join(', ') || '연결된 MQTT 클라이언트 없음';
    document.getElementById('cards').innerHTML = [
      card('배터리', s.battery!=null ? s.battery+' %' : null),
      card('로봇 상태', label(s.state)), card('오류', label(s.error)||'–'),
      card('청소 면적', s.cleaned_area), card('청소 시간', s.cleaning_time!=null ? s.cleaning_time+' 초' : null),
      card('흡입 세기', label(s.suction)), card('물 사용량', label(s.water)), card('청소 방식', label(s.mode)),
      card('펌웨어', s.ver)
    ].join('');
    if(s.suction) document.getElementById('suction').value = s.suction;
    if(s.water) document.getElementById('water').value = s.water;
    if(s.mode) document.getElementById('mode').value = s.mode;
    document.getElementById('log').textContent = JSON.stringify(s.props,null,1).slice(0,4000);
  } catch(e){ document.getElementById('conn').textContent = '애드온에 연결할 수 없습니다'; }
}
document.querySelectorAll('button[data-cmd]').forEach(b=>b.onclick=async()=>{
  await j('api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:b.dataset.cmd})});
  setTimeout(tick,500);
});
for (const [id,prop] of [['suction','SUCTION_LEVEL'],['water','WATER_VOLUME'],['mode','CLEANING_MODE']]){
  const el=document.getElementById(id);
  el.onchange=async()=>{ if(!el.value) return;
    await j('api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({set:prop,value:el.value})});
    setTimeout(tick,500); };
}
document.getElementById('mapreq').onclick=async()=>{
  const s=await j('api/state');
  const frame = JSON.stringify({req_type:1,frame_type:'I',force_type:1,time:Date.now()});
  await j('api/command',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({method:'action',params:{did:String(s.did),siid:6,aiid:1,
      in:[{piid:2,value:frame}]}})});
  setTimeout(diag,1000);
};
async function diag(){
  try{
    const d=await j('api/diagnostics');
    const lines=[];
    lines.push('연결된 클라이언트:');
    (d.clients||[]).forEach(c=>lines.push(`  ${c.client_id}  ${c.peer}  ${(c.topics||[]).join(', ')}`));
    lines.push('', '업로드 파일:');
    (d.uploads||[]).slice(0,10).forEach(u=>lines.push(`  ${u.name}  ${u.size} B  ${new Date(u.mtime*1000).toLocaleString('ko-KR')}`));
    lines.push('', '이벤트:');
    (d.events||[]).slice(0,30).forEach(e=>{
      const {ts,kind,...rest}=e; lines.push(`  ${ts} ${kind} ${JSON.stringify(rest).slice(0,220)}`);
    });
    document.getElementById('diag').textContent=lines.join('\\n');
  }catch(e){ document.getElementById('diag').textContent='진단 정보를 불러올 수 없습니다'; }
}
document.getElementById('mapcheck').onclick=async()=>{
  try {
    const r=await j('api/mapcheck');
    document.getElementById('diag').textContent=JSON.stringify(r,null,2);
  }catch(e){ document.getElementById('diag').textContent='지도 점검에 실패했습니다'; }
};
document.getElementById('refresh').onclick=()=>{tick();diag();};
diag(); setInterval(diag, 5000);
tick(); setInterval(tick, 3000);
</script></body></html>"""


ROOMS_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta http-equiv="cache-control" content="no-store">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>지도·방 관리 – 드리미 로컬</title>
<style>
:root{color-scheme:light dark}
body{font:14px/1.4 system-ui,sans-serif;margin:0;padding:16px;max-width:1200px}
h1{font-size:1.2rem;margin:.2rem 0 .2rem}
a{color:#49a}
.editor{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap;margin-top:10px}
.mapwrap{position:relative;flex:1 1 520px;min-width:300px;max-width:100%}
#mapimg{width:100%;height:auto;display:block;border-radius:10px;background:#222}
#overlay{position:absolute;inset:0;width:100%;height:100%}
#overlay rect{cursor:pointer}
#overlay text{pointer-events:none;font-size:9px;fill:#fff;font-weight:700;paint-order:stroke;stroke:#000;stroke-width:2px}
.panel{flex:0 1 320px;min-width:260px}
.card{background:#8882;border-radius:10px;padding:10px;margin-bottom:10px}
.k{font-size:.72rem;opacity:.7;text-transform:uppercase;letter-spacing:.04em}
button{font:inherit;padding:8px 10px;border-radius:8px;border:1px solid #8886;background:#8881;cursor:pointer;margin:2px}
button:disabled{opacity:.4;cursor:default}
button.active{background:#49a;border-color:#49a;color:#fff}
.room{display:flex;align-items:center;gap:8px;padding:6px;border-radius:8px;cursor:pointer;border:1px solid transparent}
.room.sel{border-color:#49a;background:#49a2}
.dot{width:12px;height:12px;border-radius:50%;flex:none}
small{opacity:.7}
#hint{min-height:2.5em;opacity:.8}
#zonehint{min-height:2.5em;opacity:.8;margin-top:6px}
</style></head><body>
<h1>지도·방 관리 <small id="mapid"></small></h1>
<a href="./">← 상태·제어</a>
<div class="editor">
  <div class="mapwrap">
    <img id="mapimg" alt="청소 지도">
    <svg id="overlay" preserveAspectRatio="none"></svg>
  </div>
  <div class="panel">
    <div class="card"><div class="k">상태</div><div id="status">불러오는 중…</div></div>
    <div class="card"><div class="k">방 목록</div><div id="roomlist"></div></div>
    <div class="card">
      <div class="k">구역 설정</div>
      <div class="row">
        <button data-zone="nogo">진입 금지 구역</button>
        <button data-zone="nomop">물걸레 금지 구역</button>
        <button data-zone="wall">가상 벽</button>
        <button data-zone="clean">청소 구역</button>
      </div>
      <div class="row">
        <button id="zoneSave">구역 설정 저장</button>
        <button id="zoneClean" disabled>지정 구역 청소</button>
        <button id="zoneClear">모든 구역 설정 삭제</button>
      </div>
      <div id="zonehint"></div>
    </div>
    <div class="card">
      <div class="k">방 관리</div>
      <button id="rename" disabled>방 이름 변경</button>
      <button id="merge" disabled>방 합치기</button>
      <button id="split" disabled>방 나누기</button>
      <button id="clean" disabled>선택한 방 청소</button>
      <button id="reload">새로고침</button>
      <div class="row" style="margin-top:8px">
        <button id="request">지도 요청</button>
        <button id="backup">지도 백업</button>
        <button id="fastmap">빠른 지도 작성</button>
      </div>
      <div id="hint"></div>
    </div>
  </div>
</div>
<script>
const palette=['#4c78a8','#f58518','#54a24b','#b279a2','#72b7b2','#e45756','#9d755d','#bab0ac'];
let data=null, selected=new Set(), splitMode=false, splitPts=[];
let zones={line:[],rect:[],mop:[]}, cleanZones=[], zoneMode=null, zonePts=[], zoneExtra={};
const ZONE_NAMES={nogo:'진입 금지 구역',nomop:'물걸레 금지 구역',wall:'가상 벽',clean:'청소 구역'};
const $=id=>document.getElementById(id);
const img=$('mapimg'), overlay=$('overlay'), statusEl=$('status'), listEl=$('roomlist'), hint=$('hint');

async function j(url,opt){const r=await fetch(url,opt); const t=await r.text(); try{return JSON.parse(t)}catch(e){throw new Error(t.slice(0,300))}}
function colorFor(id){return palette[(Number(id)-1+palette.length)%palette.length]}
function toPx(x,y){return [(x-data.origin[0])/data.grid,data.height-1-(y-data.origin[1])/data.grid]}
function toMap(px,py){return [Math.round(data.origin[0]+px*data.grid),Math.round(data.origin[1]+(data.height-1-py)*data.grid)]}

async function load(force){
  try{
    data=await j('api/rooms?t='+Date.now());
    if(!data.ok){statusEl.textContent=data.error||'지도 데이터가 없습니다';return}
    if(force){
      const z=data.zones||{};
      zones={
        line:(z.line||[]).map(a=>a.slice(0,4)),
        rect:(z.rect||[]).map(a=>a.slice(0,4)),
        mop:(z.mop||[]).map(a=>a.slice(0,4))
      };
      zoneExtra={nocpt:z.nocpt||[],addcpt:z.addcpt||[]};
      cleanZones=[];
    }
    $('mapid').textContent='지도 '+data.map_id+' · '+data.width+'×'+data.height;
    img.src='api/map.png?t='+Date.now();
    overlay.setAttribute('viewBox',`0 0 ${data.width} ${data.height}`);
    statusEl.textContent='방 '+(data.rooms||[]).length+'개를 불러왔습니다';
    render();
  }catch(e){statusEl.textContent='오류: '+e.message}
}
function render(){
  if(!data) return;
  overlay.innerHTML='';
  const NS='http://www.w3.org/2000/svg';
  for(const r of data.rooms||[]){
    const b=r.bbox;
    const rect=document.createElementNS(NS,'rect');
    rect.setAttribute('x',b[0]); rect.setAttribute('y',b[1]);
    rect.setAttribute('width',Math.max(1,b[2]-b[0])); rect.setAttribute('height',Math.max(1,b[3]-b[1]));
    rect.setAttribute('fill',(r.color||colorFor(r.id))+'33');
    rect.setAttribute('stroke',selected.has(r.id)?'#fff':'#0008');
    rect.setAttribute('stroke-width',selected.has(r.id)?2:1);
    rect.addEventListener('click',ev=>{ if(splitMode||zoneMode) return; ev.stopPropagation(); toggle(r.id)});
    overlay.appendChild(rect);
    const t=document.createElementNS(NS,'text');
    t.setAttribute('x',(b[0]+b[2])/2); t.setAttribute('y',(b[1]+b[3])/2);
    t.setAttribute('text-anchor','middle'); t.setAttribute('dominant-baseline','middle');
    t.textContent=r.name||('방 '+r.id);
    overlay.appendChild(t);
  }
  if(data.path && data.path.length>1){
    const pl=document.createElementNS(NS,'polyline');
    pl.setAttribute('points',data.path.map(p=>p[0]+','+p[1]).join(' '));
    pl.setAttribute('fill','none'); pl.setAttribute('stroke','#ffd166');
    pl.setAttribute('stroke-width',1.3); pl.setAttribute('stroke-opacity','0.9');
    overlay.appendChild(pl);
  }
  if(data.robot){
    const r=data.robot;
    const c=document.createElementNS(NS,'circle');
    c.setAttribute('cx',r.px); c.setAttribute('cy',r.py); c.setAttribute('r',3);
    c.setAttribute('fill','#fff'); c.setAttribute('stroke','#111'); c.setAttribute('stroke-width',1);
    overlay.appendChild(c);
    const l=document.createElementNS(NS,'line');
    l.setAttribute('x1',r.px); l.setAttribute('y1',r.py);
    l.setAttribute('x2',r.px+Math.cos(r.a*Math.PI/180)*7);
    l.setAttribute('y2',r.py-Math.sin(r.a*Math.PI/180)*7);
    l.setAttribute('stroke','#111'); l.setAttribute('stroke-width',1.5);
    overlay.appendChild(l);
  }
  const drawRectZone=(area,stroke,fill,dash)=>{
    const a=toPx(area[0],area[1]), b=toPx(area[2],area[3]);
    const x=Math.min(a[0],b[0]), y=Math.min(a[1],b[1]);
    const w=Math.max(0.5,Math.abs(b[0]-a[0])), h=Math.max(0.5,Math.abs(b[1]-a[1]));
    const el=document.createElementNS(NS,'rect');
    el.setAttribute('x',x); el.setAttribute('y',y);
    el.setAttribute('width',w); el.setAttribute('height',h);
    el.setAttribute('fill',fill); el.setAttribute('stroke',stroke);
    el.setAttribute('stroke-width',1.5); el.setAttribute('pointer-events','none');
    if(dash) el.setAttribute('stroke-dasharray','3 2');
    overlay.appendChild(el);
  };
  const drawLineZone=(line,stroke,dash)=>{
    const a=toPx(line[0],line[1]), b=toPx(line[2],line[3]);
    const el=document.createElementNS(NS,'line');
    el.setAttribute('x1',a[0]); el.setAttribute('y1',a[1]);
    el.setAttribute('x2',b[0]); el.setAttribute('y2',b[1]);
    el.setAttribute('stroke',stroke); el.setAttribute('stroke-width',2);
    el.setAttribute('pointer-events','none');
    if(dash) el.setAttribute('stroke-dasharray','3 2');
    overlay.appendChild(el);
  };
  for(const z of zones.rect) drawRectZone(z,'#e45756','#e4575633',false);
  for(const z of zones.mop) drawRectZone(z,'#f58518','#f5851833',false);
  for(const z of zones.line) drawLineZone(z,'#ffffff',false);
  for(const z of cleanZones) drawRectZone(z,'#54a24b','#54a24b33',true);
  if(zonePts.length===1){
    const c=document.createElementNS(NS,'circle');
    c.setAttribute('cx',zonePts[0][0]); c.setAttribute('cy',zonePts[0][1]);
    c.setAttribute('r',2); c.setAttribute('fill','#fff'); overlay.appendChild(c);
  }
  if(splitPts.length===1){
    const c=document.createElementNS(NS,'circle');
    c.setAttribute('cx',splitPts[0][0]); c.setAttribute('cy',splitPts[0][1]);
    c.setAttribute('r',2); c.setAttribute('fill','#fff'); overlay.appendChild(c);
  }
  listEl.innerHTML='';
  for(const r of data.rooms||[]){
    const div=document.createElement('div');
    div.className='room'+(selected.has(r.id)?' sel':'');
    div.innerHTML=`<span class="dot" style="background:${r.color||colorFor(r.id)}"></span><span>${r.name||('방 '+r.id)}</span><small>#${r.id}</small>`;
    div.onclick=()=>toggle(r.id);
    listEl.appendChild(div);
  }
  $('rename').disabled=selected.size!==1;
  $('merge').disabled=selected.size!==2;
  $('split').disabled=selected.size!==1;
  $('clean').disabled=selected.size===0;
  $('zoneClean').disabled=cleanZones.length===0;
  $('split').textContent=splitMode?'나누기 취소':'방 나누기';
  document.querySelectorAll('button[data-zone]').forEach(b=>b.classList.toggle('active',b.dataset.zone===zoneMode));
  hint.textContent=splitMode?'지도에서 두 점을 눌러 방을 나누세요.':'지도나 목록에서 방을 눌러 선택하세요.';
  $('zonehint').textContent=zoneMode
    ? ZONE_NAMES[zoneMode]+': 지도에서 두 점을 누르세요.'
    : ('진입 금지 '+zones.rect.length+'개, 물걸레 금지 '+zones.mop.length+'개, 가상 벽 '+zones.line.length+'개, 청소 구역 '+cleanZones.length+'개');
}
function toggle(id){if(selected.has(id))selected.delete(id);else selected.add(id);render()}
overlay.addEventListener('click',ev=>{
  if(!data) return;
  const p=overlay.createSVGPoint(); p.x=ev.clientX; p.y=ev.clientY;
  const q=p.matrixTransform(overlay.getScreenCTM().inverse());
  const px=Math.max(0,Math.min(data.width,q.x)), py=Math.max(0,Math.min(data.height,q.y));
  if(zoneMode){
    zonePts.push([px,py]);
    if(zonePts.length===2){
      const a=toMap(zonePts[0][0],zonePts[0][1]);
      const b=toMap(zonePts[1][0],zonePts[1][1]);
      if(zoneMode==='wall'){
        zones.line.push([a[0],a[1],b[0],b[1]]);
      }else{
        const area=[a[0],a[1],b[0],b[1]];
        if(zoneMode==='nogo') zones.rect.push(area);
        else if(zoneMode==='nomop') zones.mop.push(area);
        else cleanZones.push(area);
      }
      zonePts=[]; zoneMode=null;
    }
    render(); return;
  }
  if(!splitMode||selected.size!==1) return;
  splitPts.push([px,py]);
  if(splitPts.length===2){
    const [a,b]=splitPts;
    const A=toMap(a[0],a[1]), B=toMap(b[0],b[1]);
    const line=[A[0],A[1],B[0],B[1]];
    post({action:'split', id:[...selected][0], line});
    splitPts=[]; splitMode=false;
  }
  render();
});
async function post(body){
  statusEl.textContent='명령 전송 중…';
  try{
    const r=await j('api/rooms/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    statusEl.textContent=r.ok?'명령을 보냈습니다. 지도를 새로 불러오는 중…':('오류: '+(r.error||'알 수 없는 오류'));
    setTimeout(load,2500);
  }catch(e){statusEl.textContent='오류: '+e.message}
}
$('rename').onclick=()=>{
  if(selected.size!==1) return;
  const id=[...selected][0];
  const r=(data.rooms||[]).find(x=>x.id===id)||{};
  const name=prompt('새 방 이름:',r.name||('방 '+id));
  if(name!==null&&name.trim()!=='') post({action:'rename',id,name:name.trim()});
};
$('merge').onclick=()=>{
  if(selected.size!==2) return;
  const ids=[...selected];
  if(confirm('방 '+ids.join(' + ')+'을 합칠까요?')) post({action:'merge',ids});
};
$('split').onclick=()=>{ if(selected.size!==1) return; zoneMode=null; zonePts=[]; splitMode=!splitMode; splitPts=[]; render(); };
document.querySelectorAll('button[data-zone]').forEach(b=>b.onclick=()=>{
  splitMode=false; splitPts=[]; zonePts=[];
  zoneMode = zoneMode===b.dataset.zone ? null : b.dataset.zone;
  render();
});
$('zoneSave').onclick=()=>post({action:'zones',walls:zones.line,zones:zones.rect,no_mops:zones.mop,extras:zoneExtra});
$('zoneClean').onclick=()=>{
  if(cleanZones.length===0) return;
  if(confirm('지정한 구역 '+cleanZones.length+'개를 지금 청소할까요?')) post({action:'zone_clean',areas:cleanZones});
};
$('zoneClear').onclick=()=>{
  if(!confirm('지도에 저장된 모든 구역 설정을 삭제할까요?')) return;
  zones={line:[],rect:[],mop:[]}; cleanZones=[]; zonePts=[]; zoneMode=null;
  render();
  post({action:'zones',walls:[],zones:[],no_mops:[],extras:zoneExtra});
};
$('clean').onclick=()=>{
  if(selected.size===0) return;
  const ids=[...selected];
  if(confirm('방 '+ids.join(', ')+'을 지금 청소할까요?')) post({action:'clean',ids});
};
$('request').onclick=async()=>{
  const frame=JSON.stringify({req_type:1,frame_type:'I',force_type:1,time:Date.now()});
  await post({action:'request_map',frame});
};
$('backup').onclick=()=>post({action:'backup_map'});
$('fastmap').onclick=()=>{
  if(confirm('로봇을 이동시켜 빠른 지도 작성을 시작할까요?')) post({action:'fast_map'});
};
$('reload').onclick=()=>load(true);
load(true); setInterval(()=>load(false),5000);
</script></body></html>"""


@web.middleware
async def ingress_middleware(request: web.Request, handler):
    """HA-Ingress stellt /api/hassio_ingress/<token>/... voran."""
    path = request.path
    if path.startswith("/api/hassio_ingress/"):
        parts = path.split("/", 4)
        rest = "/" + (parts[4] if len(parts) > 4 else "")
        request = request.clone(rel_url=request.rel_url.with_path(rest))
    return await handler(request)


@web.middleware
async def no_cache(request: web.Request, handler):
    response = await handler(request)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


class Web:
    def __init__(
        self,
        device: Device,
        port: int = 8099,
        data_dir: Path | None = None,
        clients_fn=None,
        version: str = "",
    ) -> None:
        self.dev = device
        self.clients_fn = clients_fn
        self.version = version
        self.port = port
        self.data_dir = data_dir or Path("/data")
        self.app = web.Application(
            client_max_size=512 * 1024 * 1024,
            middlewares=[ingress_middleware, no_cache],
        )
        self.app.add_routes(
            [
                web.get("/", self.index),
                web.get("/api/state", self.state),
                web.get("/api/uploads", self.uploads),
                web.get("/api/diagnostics", self.diagnostics),
                web.get("/api/mapcheck", self.mapcheck),
                web.get("/rooms", self.rooms_page),
                web.get("/api/rooms", self.rooms_data),
                web.get("/api/map.png", self.map_png),
                web.post("/api/rooms/action", self.room_action),
                web.post("/api/command", self.command),
                web.put("/upload/{name:.*}", self.upload),
            ]
        )

    async def start(self) -> None:
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        await site.start()
        log.info("Web-UI auf :%d", self.port)

    async def index(self, request: web.Request) -> web.Response:
        return web.Response(text=PAGE, content_type="text/html")

    async def state(self, request: web.Request) -> web.Response:
        snap = self.dev.snapshot()
        snap["version"] = self.version
        if self.clients_fn:
            # Online-Status direkt aus den aktiven MQTT-Sessions ableiten,
            # damit eine verspaetete alte Session ihn nicht dauerhaft kippt.
            clients = self.clients_fn()
            snap["online"] = any(
                str(c.get("client_id", "")).startswith("device_") for c in clients
            )
            snap["clients"] = clients
        return web.json_response(snap)

    async def uploads(self, request: web.Request) -> web.Response:
        root = self.data_dir / "uploads"
        files = []
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    st = p.stat()
                    files.append({"name": str(p.relative_to(root)), "size": st.st_size,
                                  "mtime": int(st.st_mtime)})
        files.sort(key=lambda x: x["mtime"], reverse=True)
        return web.json_response({"uploads": files, "log": str(self.data_dir / "api.log")})

    async def diagnostics(self, request: web.Request) -> web.Response:
        root = self.data_dir / "uploads"
        uploads = []
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    st = p.stat()
                    uploads.append({"name": str(p.relative_to(root)), "size": st.st_size,
                                    "mtime": int(st.st_mtime)})
        uploads.sort(key=lambda x: x["mtime"], reverse=True)
        clients = self.clients_fn() if self.clients_fn else []
        return web.json_response({"clients": clients, "events": events.all(), "uploads": uploads})

    async def mapcheck(self, request: web.Request) -> web.Response:
        """Prueft den zuletzt hochgeladenen Kartenblob (Format/Header)."""
        root = self.data_dir / "uploads"
        name = request.query.get("name") or f"ali_dreame/{self.dev.uid}/{self.dev.did}/9"
        target = root / name
        if not target.is_file():
            candidates = (
                [p for p in root.rglob("*") if p.is_file() and not p.name.startswith("__")]
                if root.exists()
                else []
            )
            if not candidates:
                return web.json_response({"ok": False, "error": "업로드된 파일이 없습니다"})
            target = max(candidates, key=lambda p: p.stat().st_mtime)
            name = str(target.relative_to(root))

        result: dict[str, Any] = {
            "ok": False,
            "name": name,
            "size": target.stat().st_size,
        }
        try:
            data = target.read_bytes()
            text = data.decode("utf-8", "replace").strip()
            result["text_len"] = len(text)
            result["head"] = text[:60]
            key = None
            if "," in text:
                text, key = text.split(",", 1)
                result["embedded_key"] = True
            raw_b64 = text.replace("_", "/").replace("-", "+")
            while len(raw_b64) % 4:
                raw_b64 += "="
            raw = base64.b64decode(raw_b64, validate=False)
            result["decoded_len"] = len(raw)
            result["magic"] = raw[:4].hex()
            try:
                plain = zlib.decompress(raw)
                result["zlib"] = True
            except Exception:
                plain = raw
                result["zlib"] = False
            result["plain_len"] = len(plain)
            if result["zlib"] and len(plain) >= 27:
                result["map_id"] = int.from_bytes(plain[0:2], "little")
                result["frame_id"] = int.from_bytes(plain[2:4], "little")
                result["frame_type"] = plain[4]
                width = int.from_bytes(plain[19:21], "little")
                height = int.from_bytes(plain[21:23], "little")
                result["width"] = width
                result["height"] = height
                image_size = 27 + width * height
                result["image_size"] = image_size
                if len(plain) >= image_size:
                    try:
                        meta = json.loads(plain[image_size:].decode("utf-8"))
                        result["json_keys"] = sorted(meta.keys())
                        result["json_timestamp_ms"] = meta.get("timestamp_ms")
                    except Exception as exc:
                        result["json_error"] = str(exc)
                result["ok"] = True
            elif not result["zlib"]:
                result["error"] = (
                    "zlib 헤더가 없습니다. 암호화된 파일이거나 "
                    "지도 파일이 아닐 수 있습니다 (식별값 %s)" % result["magic"]
                )
        except Exception as exc:
            result["error"] = str(exc)
        return web.json_response(result)

    # ---------------------------------------------------------- Raum-Editor
    @staticmethod
    def _decode_map_text(text: str):
        text = text.strip()
        if not text:
            return None
        if text.startswith("{"):
            try:
                entries = json.loads(text).get("mapstr")
            except (ValueError, TypeError):
                return None
            if not isinstance(entries, list):
                return None
            for entry in entries:
                blob = entry.get("map") if isinstance(entry, dict) else None
                # Accept the same map-list wrapper as the cloud API, not
                # recursively nested wrappers or unrelated uploaded JSON.
                if isinstance(blob, str) and not blob.lstrip().startswith("{"):
                    decoded = Web._decode_map_text(blob)
                    if decoded:
                        return decoded
            return None
        if "," in text:
            text = text.split(",", 1)[0]
        t = text.replace("_", "/").replace("-", "+")
        t += "=" * (-len(t) % 4)
        try:
            raw = zlib.decompress(base64.b64decode(t, validate=False))
        except Exception:
            return None
        if len(raw) < 27:
            return None
        width = int.from_bytes(raw[19:21], "little")
        height = int.from_bytes(raw[21:23], "little")
        image_end = 27 + width * height
        if width <= 0 or height <= 0 or len(raw) < image_end:
            return None
        try:
            info = json.loads(raw[image_end:].decode("utf-8"))
        except Exception:
            return None
        return raw, width, height, info

    def _load_map(self):
        root = self.data_dir / "uploads" / f"ali_dreame/{self.dev.uid}/{self.dev.did}"
        if not self.dev.uid:
            # UID can be empty after a server restart until the next dev_start.
            # Read only an unambiguous cache for this exact device; never
            # change identity or select another device/account by freshness.
            base = self.data_dir / "uploads" / "ali_dreame"
            candidates = [p for p in base.glob("*/*")
                          if p.name == str(self.dev.did) and p.is_dir()]
            if len(candidates) == 1:
                root = candidates[0]
        best = None
        for name in ("9", "9.frame"):
            path = root / name
            try:
                if not path.is_file():
                    continue
                decoded = self._decode_map_text(path.read_text(errors="replace"))
                if not decoded:
                    continue
                raw, width, height, info = decoded
                item = {
                    "raw": raw, "width": width, "height": height, "info": info,
                    "map_id": int.from_bytes(raw[0:2], "little"),
                    "grid": int.from_bytes(raw[17:19], "little") or 50,
                    "origin": info.get("origin") or [
                        int.from_bytes(raw[23:25], "little", signed=True),
                        int.from_bytes(raw[25:27], "little", signed=True),
                    ],
                    "name": name, "mtime": path.stat().st_mtime,
                }
                if best is None or item["mtime"] > best["mtime"]:
                    best = item
            except Exception:
                continue
        if best is None:
            live = self.dev.props.get("6.1")
            if isinstance(live, str) and live:
                decoded = self._decode_map_text(live)
                if decoded:
                    raw, width, height, info = decoded
                    best = {
                        "raw": raw, "width": width, "height": height, "info": info,
                        "map_id": int.from_bytes(raw[0:2], "little"),
                        "grid": int.from_bytes(raw[17:19], "little") or 50,
                        "origin": info.get("origin") or [0, 0],
                        "name": "live", "mtime": 0,
                    }
        return best

    def _room_names(self) -> dict[str, str]:
        try:
            data = json.loads((self.data_dir / "room_names.json").read_text())
            return {str(k): str(v) for k, v in data.items()}
        except Exception:
            return {}

    def _save_room_name(self, room_id: int, name: str) -> None:
        names = self._room_names()
        names[str(room_id)] = name
        try:
            (self.data_dir / "room_names.json").write_text(json.dumps(names, ensure_ascii=False))
        except OSError:
            pass

    def _room_list(self, m):
        raw, width, height = m["raw"], m["width"], m["height"]
        origin_x, origin_y = m["origin"][0], m["origin"][1]
        grid = m["grid"] or 50
        names = self._room_names()
        img = raw[27:27 + width * height]
        bboxes = {}
        for y in range(height):
            for x in range(width):
                v = img[y * width + x]
                if 0 < v < 64:
                    b = bboxes.get(v)
                    if b is None:
                        bboxes[v] = [x, y, x, y]
                    else:
                        if x < b[0]: b[0] = x
                        if y < b[1]: b[1] = y
                        if x > b[2]: b[2] = x
                        if y > b[3]: b[3] = y
        palette = ['#4c78a8', '#f58518', '#54a24b', '#b279a2', '#72b7b2', '#e45756', '#9d755d', '#bab0ac']
        rooms = []
        for rid, b in sorted(bboxes.items()):
            # Rohbild ist von unten nach oben gespeichert; die Integration
            # rendert mit y = height - 1 - raw_y. Fuer die Anzeige und die
            # Klickkoordinaten muessen wir genauso spiegeln.
            display_bbox = [b[0], height - 1 - b[3], b[2], height - 1 - b[1]]
            rooms.append({
                "id": rid,
                "name": names.get(str(rid)) or f"방 {rid}",
                "color": palette[(rid - 1) % len(palette)],
                "bbox": display_bbox,
                "center": [(display_bbox[0] + display_bbox[2]) / 2, (display_bbox[1] + display_bbox[3]) / 2],
                "map_bbox": [
                    origin_x + b[0] * grid, origin_y + b[1] * grid,
                    origin_x + b[2] * grid, origin_y + b[3] * grid,
                ],
            })
        return rooms

    def _map_png(self, m) -> bytes:
        raw, width, height = m["raw"], m["width"], m["height"]
        img = raw[27:27 + width * height]
        palette = ['#4c78a8', '#f58518', '#54a24b', '#b279a2', '#72b7b2', '#e45756', '#9d755d', '#bab0ac']

        def rgb(hex_color):
            hex_color = hex_color.lstrip('#')
            return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

        room_colors = [rgb(c) for c in palette]
        colors = {
            0: (250, 250, 250), 255: (45, 45, 45), 254: (43, 108, 176),
            253: (160, 174, 192), 252: (160, 160, 160), 251: (210, 80, 80),
            250: (235, 180, 100), 249: (72, 187, 120), 248: (160, 220, 160),
        }
        scale = 4
        rows = []
        for y in range(height * scale):
            row = bytearray([0])
            raw_y = height - 1 - (y // scale)
            for x in range(width * scale):
                v = img[raw_y * width + (x // scale)]
                if 0 < v < 64:
                    c = room_colors[(v - 1) % len(room_colors)]
                elif 100 < v < 200:
                    c = room_colors[(v - 101) % len(room_colors)]
                else:
                    c = colors.get(v, (120, 120, 120))
                row += bytes(c)
            rows.append(bytes(row))
        W, H = width * scale, height * scale

        def chunk(tag, data):
            return struct.pack("!I", len(data)) + tag + data + struct.pack(
                "!I", binascii.crc32(tag + data) & 0xFFFFFFFF
            )

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack("!IIBBBBB", W, H, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b"")
        )

    async def rooms_page(self, request: web.Request) -> web.Response:
        return web.Response(text=ROOMS_PAGE, content_type="text/html")

    async def rooms_data(self, request: web.Request) -> web.Response:
        m = self._load_map()
        if not m:
            return web.json_response({"ok": False, "error": "저장된 지도 데이터를 찾을 수 없습니다"})
        origin_x, origin_y = m["origin"][0], m["origin"][1]
        grid = m["grid"] or 50
        robot = None
        live = self.dev.props.get("6.1")
        if isinstance(live, str) and live:
            decoded = self._decode_map_text(live)
            if decoded:
                lraw, _lw, _lh, _linfo = decoded
                x = int.from_bytes(lraw[5:7], "little", signed=True)
                y = int.from_bytes(lraw[7:9], "little", signed=True)
                a = int.from_bytes(lraw[9:11], "little", signed=True)
                if x != 32767 and y != 32767:
                    robot = {
                        "x": x, "y": y, "a": a,
                        "px": (x - origin_x) / grid,
                        "py": m["height"] - 1 - (y - origin_y) / grid,
                    }
        path = []
        raw_path = getattr(self.dev, "map_path", "")
        if isinstance(raw_path, str) and raw_path:
            current = (0, 0)
            for match in re.finditer(r"([MWSLl])(-?\d+),(-?\d+)", raw_path):
                operator = match.group(1)
                x = int(match.group(2))
                y = int(match.group(3))
                if operator == "L":
                    current = (current[0] + x, current[1] + y)
                else:
                    current = (x, y)
                path.append([
                    (current[0] - origin_x) / grid,
                    m["height"] - 1 - (current[1] - origin_y) / grid,
                ])
            if len(path) > 4000:
                path = path[-4000:]
        return web.json_response({
            "ok": True,
            "map_id": m["map_id"],
            "width": m["width"],
            "height": m["height"],
            "origin": m["origin"],
            "grid": m["grid"],
            "source": m["name"],
            "mtime": int(m["mtime"]),
            "rooms": self._room_list(m),
            "zones": m["info"].get("vw") or {},
            "robot": robot,
            "path": path,
        })

    async def map_png(self, request: web.Request) -> web.Response:
        m = self._load_map()
        if not m:
            raise web.HTTPNotFound()
        return web.Response(body=self._map_png(m), content_type="image/png")

    async def room_action(self, request: web.Request) -> web.Response:
        body = await request.json()
        action = str(body.get("action") or "")
        try:
            if action == "request_map":
                frame = str(body.get("frame") or '{"frame_type":"I"}')
                result = await self.dev.raw_action(6, 1, [{"piid": 2, "value": frame}])
                events.add("map_request", result=result)
                return web.json_response({"ok": True, "result": result})
            if action == "fast_map":
                # START_CUSTOM mit Status 21 = Schnellkartierung
                result = await self.dev.raw_action(4, 1, [{"piid": 1, "value": 21}])
                events.add("fast_map", result=result)
                return web.json_response({"ok": True, "result": result})

            m = self._load_map()
            if not m:
                return web.json_response({"ok": False, "error": "불러온 지도가 없습니다"})
            map_id = int(body.get("map_id") or m["map_id"])

            if action == "clean":
                ids = [int(v) for v in (body.get("ids") or [])]
                if not ids:
                    raise ValueError("선택한 방이 없습니다")
                fan = int(self.dev.get("SUCTION_LEVEL") or 2)
                water = int(self.dev.get("WATER_VOLUME") or 2)
                cleanlist = [[rid, 1, fan, water, 1] for rid in ids]
                payload = {"selects": cleanlist}
                params = {
                    "did": self.dev.did,
                    "siid": 4,
                    "aiid": 1,
                    "in": [
                        {"piid": 1, "value": 18},  # SEGMENT_CLEANING
                        {"piid": 10, "value": json.dumps(payload, separators=(",", ":"))},
                    ],
                }
                log.info("Raumreinigung %s", payload)
                await self.dev.ensure_saved_map()
                result = await self.dev.send("action", params, timeout=20)
                events.add("room_clean", ids=ids, result=result)
                if isinstance(result, dict) and result.get("code") not in (0, None):
                    return web.json_response({"ok": False, "error": str(result), "result": result})
                return web.json_response({"ok": True, "result": result})

            if action == "zone_clean":
                areas = []
                for area in (body.get("areas") or []):
                    coords = [int(round(float(v))) for v in area[:4]]
                    if len(coords) == 4:
                        areas.append(coords)
                if not areas:
                    raise ValueError("선택한 청소 구역이 없습니다")
                payload = {"areas": areas}
                params = {
                    "did": self.dev.did,
                    "siid": 4,
                    "aiid": 1,
                    "in": [
                        {"piid": 1, "value": 19},  # ZONE_CLEANING
                        {"piid": 10, "value": json.dumps(payload, separators=(",", ":"))},
                    ],
                }
                log.info("Zonenreinigung %s", payload)
                await self.dev.ensure_saved_map()
                result = await self.dev.send("action", params, timeout=20)
                events.add("zone_clean", areas=areas, result=result)
                if isinstance(result, dict) and result.get("code") not in (0, None):
                    return web.json_response({"ok": False, "error": str(result), "result": result})
                return web.json_response({"ok": True, "result": result})

            if action == "backup_map":
                result = await self.dev.raw_action(6, 3, [{"piid": 4, "value": str(map_id)}])
                events.add("map_backup", map_id=map_id, result=result)
                return web.json_response({"ok": True, "result": result})

            if action == "rename":
                rid = int(body.get("id"))
                name = str(body.get("name") or "").strip()
                if not name:
                    raise ValueError("방 이름을 입력하세요")
                payload = {
                    "nsr": {str(rid): {
                        "name": base64.b64encode(name.encode("utf-8")).decode("ascii"),
                        "type": 0,
                        "index": 0,
                    }},
                    "mapid": map_id,
                }
            elif action == "merge":
                ids = [int(v) for v in (body.get("ids") or [])]
                if len(ids) != 2:
                    raise ValueError("방을 정확히 두 개 선택하세요")
                payload = {"msr": ids, "mapid": map_id}
            elif action == "split":
                rid = int(body.get("id"))
                line = [int(round(float(v))) for v in (body.get("line") or [])]
                if len(line) != 4:
                    raise ValueError("방을 나눌 경계선이 없습니다")
                payload = {"dsrid": line + [rid], "mapid": map_id}
            elif action == "zones":
                walls = [[int(round(float(v))) for v in wall[:4]] for wall in (body.get("walls") or [])]
                zones = [[int(round(float(v))) for v in zone[:4]] for zone in (body.get("zones") or [])]
                no_mops = [[int(round(float(v))) for v in zone[:4]] for zone in (body.get("no_mops") or [])]
                vw = {"line": walls, "rect": zones, "mop": no_mops}
                extras = body.get("extras") or {}
                if isinstance(extras, dict):
                    for key in ("nocpt", "addcpt"):
                        if isinstance(extras.get(key), list):
                            vw[key] = extras[key]
                payload = {"vw": vw}
            else:
                raise ValueError("알 수 없는 동작입니다")
            params = {
                "did": self.dev.did,
                "siid": 6,
                "aiid": 2,
                "in": [{"piid": 4, "value": json.dumps(payload, separators=(",", ":"))}],
            }
            log.info("Raum-Aktion %s: %s", action, payload)
            result = await self.dev.send("action", params, timeout=20)
            events.add("room_edit", action=action, map_id=map_id, result=result)
            if isinstance(result, dict) and result.get("code") not in (0, None):
                return web.json_response({"ok": False, "error": str(result), "result": result})
            if action == "rename":
                self._save_room_name(rid, name)
            return web.json_response({"ok": True, "result": result})
        except Exception as exc:
            log.warning("Raum-Aktion fehlgeschlagen: %s", exc)
            return web.json_response({"ok": False, "error": str(exc)})

    async def command(self, request: web.Request) -> web.Response:
        body: dict[str, Any] = await request.json()
        try:
            if "action" in body:
                res = await self.dev.action(str(body["action"]))
            elif "method" in body:
                res = await self.dev.send(str(body["method"]), body.get("params"))
            elif "set" in body:
                prop = str(body["set"])
                value = body.get("value")
                if prop == "SUCTION_LEVEL" and isinstance(value, str):
                    value = SUCTION.get(value.upper(), value)
                elif prop == "WATER_VOLUME" and isinstance(value, str):
                    value = WATER.get(value.upper(), value)
                elif prop == "CLEANING_MODE" and isinstance(value, str):
                    value = MODE.get(value.upper(), value)
                res = await self.dev.set_property(prop, value)
            elif "get" in body:
                res = await self.dev.get_properties(list(body["get"]))
            else:
                return web.json_response({"ok": False, "error": "action|method|set|get 중 하나가 필요합니다"}, status=400)
            return web.json_response({"ok": True, "result": res})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    async def upload(self, request: web.Request) -> web.Response:
        name = request.match_info["name"].replace("..", "_")
        target = self.data_dir / "uploads" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with target.open("wb") as f:
            async for chunk in request.content.iter_chunked(65536):
                f.write(chunk)
                size += len(chunk)
        log.info("Upload gespeichert: %s (%d Bytes)", target, size)
        return web.Response(text="ok")
