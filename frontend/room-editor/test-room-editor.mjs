import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
globalThis.HTMLElement=class {};
globalThis.customElements={get:()=>false,define:()=>{}};
globalThis.window={};
const code=await readFile(new URL('./x40-room-editor.js',import.meta.url),'utf8');
const {affine,mapSignature,editPayload,X40RoomEditor}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
// Synthetic coordinates, independent of any household map.
const calibration=[{vacuum:{x:0,y:0},map:{x:200,y:600}},{vacuum:{x:1000,y:0},map:{x:300,y:600}},{vacuum:{x:0,y:1000},map:{x:200,y:500}}];
const t=affine(calibration);
for(const v of [[0,0],[1000,1000],[-3100,5200],[3456,-7890]])assert.deepEqual(t.toVacuum(t.toMap(v)),v);
const rotated=affine([{vacuum:{x:0,y:0},map:{x:10,y:20}},{vacuum:{x:1000,y:0},map:{x:10,y:120}},{vacuum:{x:0,y:1000},map:{x:110,y:20}}]);
assert.deepEqual(rotated.toVacuum(rotated.toMap([-1200,3300])),[-1200,3300]);
assert.throws(()=>affine([]));assert.throws(()=>affine([calibration[0],calibration[0],calibration[0]]));
const rooms={1:{name:'화장실'},2:{name:'화장실 2'}};
const p={room:'1',other:'2',name:'작업실',points:[[0,0],[1500,1000]],mapId:1,rooms};
assert.deepEqual(editPayload('rename',p),{service:'vacuum_rename_segment',data:{segment_id:1,segment_name:'작업실'}});
assert.deepEqual(editPayload('split',p),{service:'vacuum_split_segments',data:{map_id:1,segment:1,line:[0,0,1500,1000]}});
assert.deepEqual(editPayload('merge',p),{service:'vacuum_merge_segments',data:{map_id:1,segments:[1,2]}});
for(const [m,overrides] of [['rename',{name:''}],['rename',{name:'화장실'}],['rename',{room:'99'}],['split',{points:[]}],['split',{points:[[0,0],[1,1]]}],['split',{points:[[NaN,0],[1000,1000]]}],['split',{mapId:null}],['merge',{other:'1'}],['merge',{other:'99'}]])assert.throws(()=>editPayload(m,{...p,...overrides}));
const a={saved_map_id:1,map_id:2,rotation:0,calibration_points:calibration,rooms};
assert.equal(mapSignature(a),mapSignature({...a,frame_id:88}));
assert.notEqual(mapSignature(a),mapSignature({...a,saved_map_id:2}));
assert.notEqual(mapSignature(a),mapSignature({...a,rooms:{...rooms,1:{name:'작업실'}}}));
const idleAttrs={started:false,running:false,located:true,has_saved_map:true,has_temporary_map:false,selected_map_id:1};
const idleProbe={config:{entity:'vacuum.test'},_hass:{states:{'vacuum.test':{state:'docked',attributes:idleAttrs}}},live:()=>({saved_map_id:1})};
assert.equal(X40RoomEditor.prototype.isIdle.call(idleProbe),true);
for(const change of [{started:true},{running:true},{located:false},{has_saved_map:false},{has_temporary_map:true},{selected_map_id:2}]) {
  idleProbe._hass.states['vacuum.test'].attributes={...idleAttrs,...change};
  assert.equal(X40RoomEditor.prototype.isIdle.call(idleProbe),false);
}
const fake={isIdle:()=>false};assert.throws(()=>X40RoomEditor.prototype.checkCurrent.call(fake));
assert.ok(!code.includes("callService('vacuum'"));
assert.ok(code.includes('this.checkCurrent();if(action.signature'));
console.log('PASS: affine round trips incl rotation; exact rename/split/merge payloads; invalid IDs/names/lines; stale maps; idle guard; no movement service.');
