/* X40 map editing: no robot movement controls. */
export function affine(points) {
  if (!Array.isArray(points) || points.length < 3) throw Error('지도 좌표 정보가 없습니다.');
  const p = points.slice(0, 3), v = p.map(x => x.vacuum), m = p.map(x => x.map);
  const dx1=v[1].x-v[0].x, dy1=v[1].y-v[0].y, dx2=v[2].x-v[0].x, dy2=v[2].y-v[0].y;
  const det=dx1*dy2-dx2*dy1;
  if (!Number.isFinite(det) || Math.abs(det)<1e-8) throw Error('지도 좌표를 계산할 수 없습니다.');
  const a=((m[1].x-m[0].x)*dy2-(m[2].x-m[0].x)*dy1)/det;
  const b=((m[2].x-m[0].x)*dx1-(m[1].x-m[0].x)*dx2)/det;
  const c=((m[1].y-m[0].y)*dy2-(m[2].y-m[0].y)*dy1)/det;
  const d=((m[2].y-m[0].y)*dx1-(m[1].y-m[0].y)*dx2)/det;
  const tx=m[0].x-a*v[0].x-b*v[0].y, ty=m[0].y-c*v[0].x-d*v[0].y, inv=a*d-b*c;
  if (![a,b,c,d,tx,ty,inv].every(Number.isFinite) || Math.abs(inv)<1e-8) throw Error('지도 좌표 변환이 잘못되었습니다.');
  return {toMap: ([x,y])=>[a*x+b*y+tx,c*x+d*y+ty], toVacuum: ([x,y])=>[Math.round((d*(x-tx)-b*(y-ty))/inv)||0,Math.round((-c*(x-tx)+a*(y-ty))/inv)||0]};
}
export function mapSignature(a) {
  return JSON.stringify([a.saved_map_id,a.map_id,a.rotation,a.calibration_points,Object.entries(a.rooms||{}).map(([id,r])=>[id,r.name,r.x0,r.y0,r.x1,r.y1])]);
}
export function editPayload(mode, {room, other, name, points, mapId, rooms}) {
  const id=Number(room), id2=Number(other);
  if (!rooms?.[id] || !Number.isInteger(id)) throw Error('편집할 방을 선택하세요.');
  if (mode==='rename') {
    const value=String(name||'').trim();
    if (!value || value.length>40) throw Error('방 이름을 1~40자로 입력하세요.');
    if (value===rooms[id].name) throw Error('현재 이름과 같습니다.');
    return {service:'vacuum_rename_segment',data:{segment_id:id,segment_name:value}};
  }
  if (!Number.isInteger(Number(mapId)) || Number(mapId)<=0) throw Error('저장된 지도를 확인할 수 없습니다.');
  if (mode==='merge') {
    if (!rooms[id2] || id===id2 || !Number.isInteger(id2)) throw Error('서로 다른 두 방을 선택하세요.');
    return {service:'vacuum_merge_segments',data:{map_id:Number(mapId),segments:[id,id2]}};
  }
  if (mode==='split') {
    if (!Array.isArray(points) || points.length!==2 || !points.flat().every(Number.isFinite)) throw Error('지도에서 분할선의 양 끝을 눌러 주세요.');
    if (Math.hypot(points[1][0]-points[0][0],points[1][1]-points[0][1])<200) throw Error('분할선을 더 길게 지정하세요.');
    return {service:'vacuum_split_segments',data:{map_id:Number(mapId),segment:id,line:points.flat().map(Math.round)}};
  }
  throw Error('편집 작업이 올바르지 않습니다.');
}
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export class X40RoomEditor extends HTMLElement {
  constructor() { super();this.attachShadow({mode:'open'});this.mode='rename';this.points=[];this.zoom=1;this.busy=false; }
  setConfig(c) { if (!c.entity || !c.map_entity) throw Error('entity와 map_entity가 필요합니다.');this.config=c; }
  getCardSize() { return 12; }
  set hass(h) {
    this._hass=h;
    if (!this.ready) { this.render();this.ready=true;this.loadMap(); }
    this.syncState();
  }
  disconnectedCallback() { clearTimeout(this.pendingTimer);if(this.imageURL) URL.revokeObjectURL(this.imageURL);this.imageURL=null;this.ready=false; }
  live() { return this._hass?.states[this.config.map_entity]?.attributes; }
  isIdle() {
    const v=this._hass?.states[this.config.entity],a=v?.attributes;
    return v?.state==='docked' && a?.started===false && a?.running===false && a?.located===true && a?.has_saved_map===true && a?.has_temporary_map===false && Number(a.selected_map_id)===Number(this.live()?.saved_map_id);
  }
  message(text,error=false) { const e=this.shadowRoot.querySelector('#message');if(e){e.textContent=text;e.className=error?'error':'';} }
  syncState() {
    if (!this.ready) return;
    const live=this.live(), stale=this.signature && (!live || this.signature!==mapSignature(live));
    const locked=!this.isIdle() || !this.imageURL || !!stale || this.busy || !!this.pending || this.needsRefresh;
    this.shadowRoot.querySelector('#apply').disabled=locked;
    this.shadowRoot.querySelector('#backup').disabled=!this.isIdle() || this.busy || !!this.pending || !this.snapshot?.saved_map_id;
    this.shadowRoot.querySelector('#refresh').disabled=this.busy || !!this.pending;
    this.shadowRoot.querySelector('#state').textContent=!this.isIdle()?'청소기가 충전대에 돌아온 뒤 편집할 수 있습니다.':stale?'지도가 바뀌었습니다. ‘지도 새로 불러오기’를 눌러 주세요.':this.snapshot?`저장 지도 ${this.snapshot.saved_map_id} · 방 ${Object.keys(this.snapshot.rooms).length}개`:'';
    if (this.pending && live) {
      const p=this.pending, rooms=live.rooms||{};
      const sameMap=live.saved_map_id===p.saved_map_id;
      const applied=sameMap && (p.mode==='rename' ? rooms[p.data.segment_id]?.name===p.data.segment_name : JSON.stringify(Object.keys(rooms).sort())!==p.roomKeys);
      if(applied) {clearTimeout(this.pendingTimer);this.pending=null;this.message('지도 화면에 변경 내용이 표시됩니다. 새 지도를 불러와 확인하세요.');this.syncState();}
    }
  }
  async loadMap() {
    if(this.busy || this.pending) return;
    this.busy=true;this.syncState();this.message('지도를 불러오는 중입니다.');
    let nextURL;
    try {
      const state=this._hass.states[this.config.map_entity], a=state?.attributes;
      if(!a?.entity_picture || a.is_empty || !Object.keys(a.rooms||{}).length) throw Error('지도가 없습니다. 연결 상태를 확인한 뒤 다시 불러오세요.');
      const sig=mapSignature(a), transform=affine(a.calibration_points);
      const response=await fetch(this._hass.hassUrl(a.entity_picture),{cache:'no-store'});
      if(!response.ok) throw Error('지도 이미지를 불러오지 못했습니다. 다시 불러오세요.');
      const blob=await response.blob();nextURL=URL.createObjectURL(blob);
      const image=new Image();image.src=nextURL;await image.decode();
      if(sig!==mapSignature(this.live())) throw Error('불러오는 동안 지도가 바뀌었습니다. 다시 불러오세요.');
      if(this.imageURL) URL.revokeObjectURL(this.imageURL);
      this.imageURL=nextURL;nextURL=null;this.width=image.naturalWidth;this.height=image.naturalHeight;
      this.snapshot=JSON.parse(JSON.stringify({rooms:a.rooms,saved_map_id:a.saved_map_id,map_id:a.map_id,rotation:a.rotation,calibration_points:a.calibration_points}));
      this.signature=sig;this.transform=transform;this.points=[];this.needsRefresh=false;
      this.populate();this.draw();this.message('방을 선택한 뒤 편집하세요.');
    } catch(e) {this.message(e.message,true);} finally {if(nextURL)URL.revokeObjectURL(nextURL);this.busy=false;this.syncState();}
  }
  populate() {
    const rooms=this.snapshot.rooms;
    for(const id of ['room','other']) {
      const e=this.shadowRoot.querySelector('#'+id), prev=e.value;
      e.innerHTML='<option value="">방 선택</option>'+Object.entries(rooms).map(([k,r])=>`<option value="${esc(k)}">${esc(r.name)} · ${esc(k)}번</option>`).join('');
      if(rooms[prev]) e.value=prev;
    }
    this.updateMode();
  }
  render() {
    this.shadowRoot.innerHTML=`<style>
      :host{display:block;color:var(--primary-text-color,#202124);font-family:var(--paper-font-body1_-_font-family,system-ui);--accent:var(--primary-color,#007aaf)}
      ha-card{display:block;padding:20px;max-width:1200px;margin:auto;box-sizing:border-box}
      h2{font-size:24px;margin:0 0 8px;font-weight:600}p{line-height:1.55;margin:8px 0 16px}.muted,#state{color:var(--secondary-text-color,#595959);font-size:14px}
      .layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:24px}.mapbox{overflow:auto;background:var(--secondary-background-color,#f4f5f7);border:1px solid var(--divider-color,#ccc);border-radius:8px;max-height:70vh;min-height:240px}.map{position:relative;width:100%;line-height:0}.map img{width:100%;height:auto;display:block}.map svg{position:absolute;inset:0;width:100%;height:100%;touch-action:pan-x pan-y;cursor:crosshair}.empty{padding:48px 20px;line-height:1.5}
      .toolbar,.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 16px}.toolbar{align-items:center;margin-top:12px}.toolbar label{display:flex;gap:8px;align-items:center;font-size:14px}.tabs button[aria-pressed=true]{color:var(--text-primary-color,#fff);background:var(--accent);border-color:var(--accent)}
      button,input,select{font:inherit;box-sizing:border-box}button{min-height:44px;padding:8px 12px;border:1px solid var(--divider-color,#aaa);border-radius:8px;background:var(--card-background-color,#fff);color:inherit;cursor:pointer}button:hover:not(:disabled){filter:brightness(.95)}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid var(--accent);outline-offset:2px}button:disabled{opacity:.5;cursor:not-allowed}label.field{display:grid;gap:8px;margin:18px 0}select,input[type=text]{width:100%;min-height:48px;border:1px solid var(--divider-color,#aaa);border-radius:8px;padding:10px;background:var(--card-background-color,#fff);color:inherit}
      #apply{background:var(--accent);color:var(--text-primary-color,#fff);border-color:var(--accent);width:100%;margin-top:12px}#message{line-height:1.55;margin:16px 0 0;min-height:24px}#message.error{color:var(--error-color,#b3261e)}#lineInfo{font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.foot{border-top:1px solid var(--divider-color,#ccc);margin-top:24px;padding-top:12px}.foot button{margin-right:8px}::selection{background:var(--accent);color:var(--text-primary-color,#fff)}[hidden]{display:none!important}
      dialog{border:1px solid var(--divider-color,#aaa);border-radius:12px;max-width:min(420px,calc(100vw - 48px));background:var(--card-background-color,#fff);color:inherit;padding:24px}dialog::backdrop{background:#0008}dialog h3{margin:0 0 12px}dialog .actions{display:flex;justify-content:flex-end;gap:12px;margin-top:24px}
      @media(max-width:750px){ha-card{padding:16px}.layout{grid-template-columns:minmax(0,1fr);gap:16px}.mapbox{max-height:55vh}.tabs{margin-bottom:8px}.tabs button{flex:1;padding:8px}h2{font-size:22px}.foot{margin-top:20px}}
    </style><ha-card><h2>방 편집</h2><p class="muted">이름을 바꾸거나, 지도에 선을 그어 방을 나눕니다.</p>
      <div class="layout"><section aria-label="편집할 지도"><div class="mapbox"><div class="map"><div class="empty">지도를 불러오는 중입니다.</div></div></div><div class="toolbar"><button id="refresh">지도 새로 불러오기</button><label>확대 <input id="zoom" type="range" min="1" max="3" step="0.25" value="1" aria-label="지도 확대"></label></div><div id="state" role="status"></div></section>
      <section aria-label="방 편집 도구"><div class="tabs" aria-label="편집 종류"><button data-mode="rename" aria-pressed="true">이름 변경</button><button data-mode="split" aria-pressed="false">분할</button><button data-mode="merge" aria-pressed="false">병합</button></div>
      <label class="field">편집할 방<select id="room" aria-label="편집할 방"><option value="">방 선택</option></select></label>
      <label class="field" id="nameField">새 이름<input id="name" type="text" maxlength="40" autocomplete="off" placeholder="예: 작업실"></label>
      <div id="splitField" hidden><p>선택한 방의 양쪽 바깥을 한 번씩 눌러 분할선을 그리세요. 세 번째로 누르면 새 선을 시작합니다.</p><p id="lineInfo" class="muted">아직 선을 지정하지 않았습니다.</p><button id="reset">분할선 지우기</button></div>
      <label class="field" id="otherField" hidden>합칠 방<select id="other" aria-label="합칠 방"><option value="">방 선택</option></select></label>
      <button id="apply" disabled>변경 내용 확인</button><p id="message" role="status" aria-live="polite"></p></section></div>
      <div class="foot"><button id="backup" disabled>기기에 지도 백업</button><p class="muted">분할·병합은 방 번호와 방별 설정에 영향을 줄 수 있습니다. 먼저 백업하고, 편집 후 방별 예약과 청소 설정을 확인하세요.</p></div>
      <dialog id="confirm"><h3>지도 변경 확인</h3><p id="confirmText"></p><p class="muted">확인을 누르면 청소기에 저장된 지도가 바뀝니다.</p><div class="actions"><button id="cancel">취소</button><button id="confirmApply">확인하고 적용</button></div></dialog></ha-card>`;
    this.shadowRoot.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{this.mode=b.dataset.mode;this.points=[];this.updateMode();this.draw();});
    this.shadowRoot.querySelector('#refresh').onclick=()=>this.loadMap();
    this.shadowRoot.querySelector('#room').onchange=()=>{this.points=[];this.updateMode();this.draw();};
    this.shadowRoot.querySelector('#other').onchange=()=>this.draw();
    this.shadowRoot.querySelector('#reset').onclick=()=>{this.points=[];this.draw();};
    this.shadowRoot.querySelector('#zoom').oninput=e=>{this.zoom=Number(e.target.value);this.shadowRoot.querySelector('.map').style.width=`${this.zoom*100}%`;};
    this.shadowRoot.querySelector('#apply').onclick=()=>this.prepare();
    this.shadowRoot.querySelector('#backup').onclick=()=>this.prepareBackup();
    this.shadowRoot.querySelector('#cancel').onclick=()=>{this.confirmedAction=null;this.shadowRoot.querySelector('dialog').close();};
    this.shadowRoot.querySelector('#confirmApply').onclick=()=>this.submit();
    this.shadowRoot.querySelector('dialog').addEventListener('cancel',()=>{this.confirmedAction=null;});
  }
  updateMode() {
    const s=this.shadowRoot;
    s.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===this.mode)));
    s.querySelector('#nameField').hidden=this.mode!=='rename';s.querySelector('#splitField').hidden=this.mode!=='split';s.querySelector('#otherField').hidden=this.mode!=='merge';
    s.querySelector('#name').value=this.snapshot?.rooms[s.querySelector('#room').value]?.name||'';
  }
  draw() {
    if(!this.imageURL)return;
    const e=this.shadowRoot.querySelector('.map'), selected=this.shadowRoot.querySelector('#room').value, second=this.shadowRoot.querySelector('#other').value;
    const radius=Math.max(15,this.width/38), font=Math.max(16,this.width/46);
    const labels=Object.entries(this.snapshot.rooms).map(([id,r])=>{const [x,y]=this.transform.toMap([r.x,r.y]);const on=id===selected || this.mode==='merge'&&id===second;return `<g data-room="${esc(id)}" role="button" tabindex="0" aria-label="${esc(r.name)} ${esc(id)}번 선택"><circle cx="${x}" cy="${y}" r="${radius}" fill="${on?'#005b85':'#fff'}" stroke="#005b85" stroke-width="3"/><text x="${x}" y="${y}" dy=".35em" text-anchor="middle" font-family="system-ui" font-weight="700" font-size="${font}" fill="${on?'#fff':'#004465'}">${esc(id)}</text></g>`;}).join('');
    const pts=this.points.map(p=>this.transform.toMap(p));
    const line=pts.length===2?`<line x1="${pts[0][0]}" y1="${pts[0][1]}" x2="${pts[1][0]}" y2="${pts[1][1]}" stroke="#fff" stroke-width="8"/><line x1="${pts[0][0]}" y1="${pts[0][1]}" x2="${pts[1][0]}" y2="${pts[1][1]}" stroke="#b3261e" stroke-width="4"/>`:'';
    e.innerHTML=`<img src="${this.imageURL}" alt="현재 청소 지도"><svg viewBox="0 0 ${this.width} ${this.height}" aria-label="방 선택과 분할선 지정">${labels}${line}${pts.map(([x,y])=>`<circle cx="${x}" cy="${y}" r="9" fill="#b3261e" stroke="#fff" stroke-width="3"/>`).join('')}</svg>`;
    e.style.width=`${this.zoom*100}%`;
    const svg=e.querySelector('svg');
    svg.onclick=ev=>{
      const label=ev.target.closest('[data-room]');
      if(label && this.mode!=='split') {this.selectRoom(label.dataset.room);return;}
      if(this.mode!=='split' || !selected || this.busy || this.pending)return;
      const point=svg.createSVGPoint();point.x=ev.clientX;point.y=ev.clientY;const at=point.matrixTransform(svg.getScreenCTM().inverse());
      if(this.points.length===2)this.points=[];this.points.push(this.transform.toVacuum([at.x,at.y]));this.draw();
    };
    svg.onkeydown=ev=>{const label=ev.target.closest('[data-room]');if(label&&['Enter',' '].includes(ev.key)){ev.preventDefault();this.selectRoom(label.dataset.room);}};
    this.shadowRoot.querySelector('#lineInfo').textContent=this.points.length?this.points.map((p,i)=>`${i+1}점 (${p.join(', ')})`).join(' → '):'아직 선을 지정하지 않았습니다.';
  }
  selectRoom(id) {
    const s=this.shadowRoot, room=s.querySelector('#room'), other=s.querySelector('#other');
    if(this.mode==='merge' && room.value && room.value!==id)other.value=id;else room.value=id;
    this.points=[];this.updateMode();this.draw();
  }
  checkCurrent() {
    if(!this.isIdle())throw Error('청소기가 충전대에 돌아온 뒤 다시 시도하세요.');
    if(!this.snapshot || this.signature!==mapSignature(this.live()||{}))throw Error('지도가 바뀌었습니다. 새로 불러온 뒤 다시 지정하세요.');
    if(this.busy || this.pending)throw Error('앞선 요청의 반영을 기다려 주세요.');
    if(this.needsRefresh)throw Error('다시 적용하기 전에 새 지도를 불러와 확인하세요.');
  }
  prepare() {
    try {
      this.checkCurrent();const s=this.shadowRoot, room=s.querySelector('#room').value, other=s.querySelector('#other').value;
      const payload=editPayload(this.mode,{room,other,name:s.querySelector('#name').value,points:this.points,mapId:this.snapshot.saved_map_id,rooms:this.snapshot.rooms});
      const name=this.snapshot.rooms[room].name;
      const text=this.mode==='rename'?`‘${name}’(${room}번)을 ‘${payload.data.segment_name}’(으)로 바꿉니다.`:this.mode==='merge'?`‘${name}’(${room}번)과 ‘${this.snapshot.rooms[other].name}’(${other}번)을 하나로 합칩니다. 서로 붙어 있는 방인지 확인하세요.`:`‘${name}’(${room}번)을 지도에 표시한 빨간 선으로 나눕니다. 방의 양쪽 경계를 가로지르는 선인지 확인하세요.`;
      this.confirmedAction={...payload,mode:this.mode,signature:this.signature};this.openConfirm(text);
    }catch(e){this.message(e.message,true);}
  }
  prepareBackup() {
    try {this.checkCurrent();this.confirmedAction={service:'vacuum_backup_map',data:{map_id:this.snapshot.saved_map_id},mode:'backup',signature:this.signature};this.openConfirm('현재 저장 지도를 기기에 백업하도록 요청합니다. 기존 기기 백업 목록에 새 백업이 추가될 수 있습니다.');}catch(e){this.message(e.message,true);}
  }
  openConfirm(text) {const d=this.shadowRoot.querySelector('dialog');this.shadowRoot.querySelector('#confirmText').textContent=text;this.shadowRoot.querySelector('#confirmApply').disabled=false;d.showModal();}
  async submit() {
    const action=this.confirmedAction;if(!action)return;
    try {
      this.checkCurrent();if(action.signature!==this.signature)throw Error('지도가 바뀌었습니다. 다시 선택하세요.');
      const advertised=this._hass.services?.dreame_vacuum;
      if(advertised && !advertised[action.service])throw Error('이 작업을 지원하지 않습니다. HA의 Dreame 통합을 확인하세요.');
      this.confirmedAction=null;this.shadowRoot.querySelector('dialog').close();this.busy=true;this.syncState();
      if(action.mode!=='backup')this.needsRefresh=true;
      await this._hass.callService('dreame_vacuum',action.service,{entity_id:this.config.entity,...action.data});
      if(action.mode==='backup'){this.message('백업 요청을 전달했습니다. 기기 백업 완료 여부는 아직 확인되지 않았습니다.');}
      else {
        this.pending={...action,saved_map_id:this.snapshot.saved_map_id,roomKeys:JSON.stringify(Object.keys(this.snapshot.rooms).sort())};
        this.message('요청을 전달했습니다. 지도에 반영되는지 기다리는 중입니다.');
        this.pendingTimer=setTimeout(()=>{this.pending=null;this.message('아직 지도 반영을 확인하지 못했습니다. 다시 적용하기 전에 새 지도를 불러와 확인하세요.',true);this.syncState();},30000);
      }
    }catch(e){this.confirmedAction=null;this.shadowRoot.querySelector('dialog').close();this.message(`적용하지 못했습니다: ${e.message||'연결을 확인하세요.'}`,true);}
    finally {this.busy=false;this.syncState();}
  }
}
if(!customElements.get('x40-room-editor'))customElements.define('x40-room-editor',X40RoomEditor);
window.customCards=window.customCards||[];
window.customCards.push({type:'x40-room-editor',name:'X40 방 편집',description:'지도에서 방 이름 변경·분할·병합'});
