// Execute the exact served scripts with synthetic DOM/API fixtures only.
const vm=require('node:vm'), assert=require('node:assert/strict');
let input=''; process.stdin.on('data',c=>input+=c);
process.stdin.on('end',async()=>{
  try {
    const pages=JSON.parse(input);
    function setup(html) {
      const elem=()=>({textContent:'',innerHTML:'',value:'',dataset:{},children:[],classList:{toggle(){}},setAttribute(){},addEventListener(){},appendChild(c){this.children.push(c)}});
      const nodes=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],elem()]));
      const buttons=[...html.matchAll(/data-(cmd|zone)="([^"]+)"/g)].map(m=>{let b=elem();b.dataset[m[1]]=m[2];return b});
      const writes=[],dialogs=[];
      const state={online:true,version:'SYNTHETIC',battery:85,state:'charging_completed',error:'none',suction:'standard',water:'medium',mode:'sweeping',wetness_supported:true,wetness_level:23,cleaning_time:0,main_brush:0,total_time:0,total_count:0,props:{TASK_STATUS:0,SELF_WASH_BASE_STATUS:0,CLEANING_PROGRESS:0,DRYING_PROGRESS:0,LOW_WATER_WARNING:0,AUTO_EMPTY_STATUS:0}};
      const rooms={ok:true,map_id:1,width:100,height:100,origin:[0,0],grid:50,rooms:[{id:1,bbox:[0,0,20,20]},{id:2,bbox:[21,0,40,20],name:'사용자 지정 이름'}],zones:{},path:[],robot:null};
      const context=vm.createContext({document:{getElementById:id=>{assert.ok(nodes[id],id);return nodes[id]},querySelectorAll:s=>buttons.filter(b=>s.includes('data-cmd')?b.dataset.cmd:b.dataset.zone),createElement:elem,createElementNS:elem},setInterval(){},setTimeout(){},confirm:t=>{dialogs.push(t);return false},prompt:t=>{dialogs.push(t);return null},fetch:async(url,opt={})=>{let body;if(opt.method==='POST'){writes.push(JSON.parse(opt.body));body={ok:true}}else if(url.startsWith('api/state'))body=state;else if(url==='api/diagnostics')body={};else if(url.startsWith('api/rooms'))body=rooms;else throw Error('Unexpected URL '+url);return {ok:true,json:async()=>body,text:async()=>JSON.stringify(body)}}});
      const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
      new vm.Script(script); vm.runInContext(script,context);
      return {context,nodes,buttons,writes,dialogs,state};
    }
    const main=setup(pages.PAGE);
    await vm.runInContext('tick()',main.context);
    assert.match(main.nodes.cards.innerHTML,/충전 완료/);
    assert.match(main.nodes.conn.innerHTML,/연결됨/);
    assert.equal(main.nodes.mode.value,'sweeping');
    assert.match(main.nodes.cards.innerHTML,/0 분/);
    assert.match(main.nodes.stationCards.innerHTML,/진행 중인 작업 없음/);
    assert.match(main.nodes.stationCards.innerHTML,/0 %/);
    assert.match(main.nodes.stationCards.innerHTML,/경고 없음/);
    assert.match(main.nodes.maintenanceCards.innerHTML,/0 %/);
    assert.match(main.nodes.maintenanceCards.innerHTML,/0 회/);
    assert.match(main.nodes.maintenanceCards.innerHTML,/확인 불가/);
    main.state.props.LOW_WATER_WARNING=999;
    main.state.ver='<img src=x onerror=alert(1)>';
    await vm.runInContext('tick()',main.context);
    assert.match(main.nodes.stationCards.innerHTML,/알 수 없음 \(코드 999\)/);
    assert.ok(!main.nodes.cards.innerHTML.includes('<img'));
    assert.match(main.nodes.cards.innerHTML,/&lt;img/);
    for(const [id,value,property] of [['suction','strong','SUCTION_LEVEL'],['water','high','WATER_VOLUME'],['mode','mopping','CLEANING_MODE'],['mode','mopping_after_sweeping','CLEANING_MODE']]) {
      main.nodes[id].value=value; await main.nodes[id].onchange();
      assert.deepEqual(main.writes.at(-1),{set:property,value});
    }
    assert.equal(main.nodes.wetnessControls.hidden,false);
    assert.equal(main.nodes.wetness.value,23);
    main.nodes.wetness.value='16'; await main.nodes.wetnessApply.onclick();
    assert.deepEqual(main.writes.at(-1),{set:'WETNESS_LEVEL',value:16});
    for(const button of main.buttons) {await button.onclick(); assert.deepEqual(main.writes.at(-1),{action:button.dataset.cmd})}
    main.context.fetch=async()=>({ok:true,json:async()=>({ok:false,error:'SYNTHETIC_REJECTION'})});
    await main.nodes.mode.onchange();
    assert.match(main.nodes.commandStatus.textContent,/명령 실패: SYNTHETIC_REJECTION/);
    const map=setup(pages.ROOMS_PAGE);
    await vm.runInContext('load(true)',map.context);
    assert.equal(map.nodes.status.textContent,'방 2개를 불러왔습니다');
    assert.match(map.nodes.hint.textContent,/방을 눌러 선택/);
    assert.match(map.nodes.roomlist.children.at(-2).innerHTML,/방 1/);
    assert.match(map.nodes.roomlist.children.at(-1).innerHTML,/사용자 지정 이름/);
    vm.runInContext("selected.add(1);render();$('rename').onclick();selected.add(2);$('merge').onclick();$('clean').onclick();$('zoneClear').onclick();$('fastmap').onclick()",map.context);
    assert.equal(map.writes.length,0,'Declined confirmations must send no commands');
    assert.equal(map.dialogs.length,5);
    for(const text of map.dialogs) assert.match(text,/[가-힣]/);
    console.log(JSON.stringify({scripts_executed:2,synthetic_option_payloads:main.writes.filter(w=>w.set).length,synthetic_button_payloads:main.writes.filter(w=>w.action).length,korean_dialogs_checked:map.dialogs.length,robot_rejection_displayed:true,network_access:false,robot_commands_sent:false}));
  } catch(e) {console.error(e);process.exitCode=1}
});
