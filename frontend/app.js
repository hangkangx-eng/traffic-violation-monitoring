'use strict';
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const names = {monitor:'实时监测',events:'告警中心',rules:'规则与标定',assistant:'AI 分析助手',reports:'分析报告',settings:'模型设置'};
const types = {overspeed:['超速候选','amber','S'],illegal_parking:['违停候选','blue','P'],reverse_driving:['逆行候选','violet','R']};
const reviews = {pending:'待复核',confirmed:'已确认',dismissed:'已排除'};
const fields = {speed_limit_kmh:'限速 km/h',speed_enabled:'超速检测',real_distance_m:'A/B 距离 m',meter_per_pixel:'米/像素',parking_enabled:'违停检测',parking_min_stay_sec:'违停时长 秒',parking_max_displacement_px:'停车位移 px',reverse_enabled:'逆行检测',reverse_cos_threshold:'方向余弦阈值',reverse_min_duration_sec:'逆行持续时长 秒',reverse_min_displacement_px:'逆行位移 px'};
const state = {view:'monitor',status:{},stats:{},config:null,dirty:false,events:new Map(),eventPage:0,eventTotal:0,tool:'none',draft:[],lane:0,history:[],eventId:null,starting:false,aiBusy:false,proposal:null,streamSession:null,connected:false};
let toastTimer;
function toast(message,error=false){$('toast').textContent=message;$('toast').classList.toggle('error',error);$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,error?6500:3500);}
async function api(path, options={}){
  const init={...options};
  if(init.body && !(init.body instanceof FormData)){init.body=JSON.stringify(init.body);init.headers={...init.headers,'Content-Type':'application/json'};}
  let response;
  try{response=await fetch('/api'+path,init);}catch(e){throw new Error('无法连接本机后端，请检查 Web 服务是否启动。');}
  if(!response.ok){let data;try{data=await response.json();}catch(e){}const detail=data?.detail;throw new Error(typeof detail==='string'?detail:Array.isArray(detail)?detail.map(v=>v.msg).join('；'):`请求失败（${response.status}）`);}
  return response.json();
}
async function busy(button,work,label='处理中…'){
  const original=button.innerHTML;button.disabled=true;button.textContent=label;
  try{return await work();}catch(e){toast(e.message,true);return null;}finally{button.innerHTML=original;button.disabled=false;}
}
function navigate(view){
  if(!names[view])view='monitor';state.view=view;
  document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active',el.id==='view-'+view));
  document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el.dataset.view===view));
  $('breadcrumb').textContent=names[view];history.replaceState(null,'','#'+view);
  if(view==='events')loadEvents().catch(e=>toast(e.message,true));
  if(view==='reports')loadReports().catch(e=>toast(e.message,true));
  if(view==='rules'){renderDrawing();refreshPreview();}
  window.scrollTo({top:0,behavior:'instant'});
}
document.querySelectorAll('[data-view]').forEach(el=>el.addEventListener('click',()=>navigate(el.dataset.view)));
document.querySelectorAll('[data-go]').forEach(el=>el.addEventListener('click',()=>navigate(el.dataset.go)));
function number(n){return Number(n||0).toLocaleString('zh-CN');}
function duration(n){n=Math.max(0,Math.floor(Number(n)||0));return (Math.floor(n/60)).toString().padStart(2,'0')+':'+(n%60).toString().padStart(2,'0');}
function typeInfo(type){return types[type]||['系统事件','muted','!'];}
function cacheEvents(items){for(const item of items)state.events.set(item.id,item);}
function updateStatus(status){
  state.status=status;
  $('m-vehicles').textContent=number(status.vehicle_count);
  $('m-tracks').innerHTML=number(status.active_tracks)+'<small> 辆</small>';
  const labels={idle:'等待监测',starting:'模型加载中',running:'正在监测',stopped:'监测已停止',completed:'视频已结束',error:'检测异常',stream_ended:'视频流中断'};
  $('run-badge').className='badge '+(status.running?'':status.status_message==='error'?'red':'muted');
  $('run-badge').innerHTML='<span class="dot"></span>'+esc(state.starting?'模型加载中':labels[status.status_message]||status.status_message);
  $('start-btn').disabled=state.starting||status.running;
  $('stop-btn').disabled=state.starting||!status.running;
  $('source-select').disabled=!!status.running;$('source-input').disabled=!!status.running;$('camera-input').disabled=!!status.running;
  $('upload-btn').disabled=!!status.running;
  $('camera-caption').textContent=(status.camera_id||$('camera-input').value).toUpperCase()+' / 960 × 540';
  $('video-position').textContent=duration(status.position_sec)+' / '+duration(status.duration_sec);
  $('video-progress').style.width=(status.duration_sec?Math.min(100,100*status.position_sec/status.duration_sec):0)+'%';
  $('hud-fps').textContent=Number(status.fps||0).toFixed(1)+' FPS';
  $('hud-status').textContent=status.running?'LIVE':'PAUSED';
  if(status.source)$('video-source-label').textContent=status.source.replace(/^.*[\\/]/,'');
  const device=status.tracker_device;
  $('engine-caption').textContent=device==='not_loaded'?'本地视觉检测引擎':(device==='cpu'?'CPU 推理':device)+' · '+number(status.processed_frames)+' 帧';
  if(status.latest_frame_ts){
    $('live-frame').hidden=false;$('video-empty').hidden=true;$('video-hud').hidden=false;
    if(status.running&&state.streamSession!==status.session_id){$('live-frame').src='/api/stream?t='+Date.now();state.streamSession=status.session_id;}
    if(!status.running&&state.streamSession!==null){$('live-frame').src='/api/frame?t='+Date.now();state.streamSession=null;}
  }
  $('task-hint').textContent=status.running?'检测结果和候选告警会实时保存。':status.status_message==='error'?'检测出现异常，请查看告警中心的系统事件。':'首次启动需要加载模型，请稍候。';
}
function updateStats(stats){
  state.stats=stats;$('m-today').textContent=number(stats.today);$('m-pending').textContent=number(stats.pending);$('nav-count').textContent=number(stats.pending);
  const values=Array.from({length:24},(_,i)=>stats.hourly?.[String(i).padStart(2,'0')]||0),max=Math.max(1,...values);
  $('hour-chart').innerHTML=values.map((v,i)=>`<div class="bar-slot" title="${i}:00 · ${v} 条"><div class="bar ${v?'':'zero'}" style="height:${v?Math.max(5,v/max*100):2}%"></div></div>`).join('');
  $('chart-total').textContent=stats.today?'今日共 '+number(stats.today)+' 条候选告警':'今日暂无已保存事件';
}
function renderRecent(items){
  const signature=JSON.stringify(items);if(signature===state.recentSignature)return;state.recentSignature=signature;
  cacheEvents(items);
  $('recent-events').innerHTML=items.length?items.slice(0,4).map(item=>{const[t,c,letter]=typeInfo(item.event_type);return `<button class="recent-item" data-event="${esc(item.id)}"><span class="rule-icon ${c}">${letter}</span><span class="recent-text"><b>${t}</b><small>${esc(item.camera_id)} · 轨迹 ${esc(item.track_id)}</small></span><time>${esc(item.timestamp.slice(11,19))}</time><span class="badge ${item.review_status==='pending'?'amber':'muted'}">${reviews[item.review_status]||'待复核'}</span></button>`;}).join(''):'<div class="empty compact">暂无告警，监测事件将在此显示。</div>';
}
let polling=false;
async function poll(){if(polling)return;polling=true;try{
  const [status,stats,recent]=await Promise.all([api('/status'),api('/stats'),api('/events?limit=4')]);
  state.connected=true;$('connection-text').textContent='已连接';updateStatus(status);updateStats(stats);renderRecent(recent.items);
  if(state.view==='events')await loadEvents();
}catch(e){state.connected=false;$('connection-text').textContent='未连接';}finally{polling=false;}}
async function loadSources(selected){
  const items=await api('/sources');const old=selected||$('source-input').value;
  $('source-select').innerHTML='<option value="">选择已有视频</option>'+items.map(s=>`<option value="${esc(s.id)}">${esc(s.name)} · ${(s.size/1024/1024).toFixed(1)} MB</option>`).join('');
  const source=items.find(i=>i.id===old)||(!old?items[0]:null);
  if(source){$('source-select').value=source.id;$('source-input').value=source.id;$('video-source-label').textContent=source.name;}
  if(source&&!state.status.latest_frame_ts){$('live-frame').src='/api/preview?source='+encodeURIComponent(source.id);$('live-frame').hidden=false;$('video-empty').hidden=true;}
}
$('source-select').addEventListener('change',()=>{if($('source-select').value){$('source-input').value=$('source-select').value;refreshPreview();if(!state.status.latest_frame_ts){$('live-frame').src='/api/preview?source='+encodeURIComponent($('source-input').value);$('live-frame').hidden=false;$('video-empty').hidden=true;}}});
$('source-input').addEventListener('change',()=>{$('source-select').value=$('source-input').value;});
$('upload-btn').addEventListener('click',()=>$('upload-input').click());
$('upload-input').addEventListener('change',async()=>{const file=$('upload-input').files[0];if(!file)return;await busy($('upload-btn'),async()=>{const form=new FormData();form.append('file',file);const result=await api('/sources/upload',{method:'POST',body:form});await loadSources(result.id);toast('视频已上传，可开始监测。');},'正在上传与校验…');$('upload-input').value='';});
$('start-btn').addEventListener('click',async()=>{
  const source=$('source-input').value.trim(),camera=$('camera-input').value.trim();if(!source){toast('请先选择视频或填写视频源。',true);return;}
  state.starting=true;
  await busy($('start-btn'),async()=>{const result=await api('/monitor/start',{method:'POST',body:{source,camera_id:camera}});updateStatus(result);toast('监测任务已启动。');},'正在加载模型…');
  state.starting=false;await poll();
});
$('stop-btn').addEventListener('click',()=>busy($('stop-btn'),async()=>{updateStatus(await api('/monitor/stop',{method:'POST'}));toast('监测已停止，事件已保存。');await poll();},'正在停止…'));
function getPath(object,path){return path.split('.').reduce((o,k)=>o[k],object);}
function setPath(object,path,value){const parts=path.split('.'),last=parts.pop();let target=object;for(const p of parts)target=target[p];target[last]=value;}
function dirty(value=true){state.dirty=value;$('config-dirty').hidden=!value;}
function lane(){return state.config?.reverse.lanes[state.lane];}
function newLane(name){return {name,polygon:[],legal_direction:[],min_duration_sec:2,min_displacement_px:40};}
function populateConfig(){
  if(!state.config)return;
  for(const input of document.querySelectorAll('[data-config]')){const value=getPath(state.config,input.dataset.config);if(input.type==='checkbox')input.checked=value;else input.value=value;}
  $('config-name').textContent=state.config.camera_id;
  $('lane-select').innerHTML=state.config.reverse.lanes.map((l,i)=>`<option value="${i}">${esc(l.name)}</option>`).join('');
  state.lane=Math.max(0,Math.min(state.lane,state.config.reverse.lanes.length-1));$('lane-select').value=String(state.lane);
  $('reverse-duration').value=lane()?.min_duration_sec??2;$('reverse-displacement').value=lane()?.min_displacement_px??40;
  $('reverse-duration').disabled=!lane();$('reverse-displacement').disabled=!lane();
  renderDrawing();updateRuleSummary();
}
function updateRuleSummary(){
  const c=state.config;if(!c)return;
  for(const[k,enabled]of [['speed',c.speed.enabled],['parking',c.parking.enabled],['reverse',c.reverse.enabled]]){const el=$('rule-'+k+'-state');el.textContent=enabled?'开启':'关闭';el.className='badge'+(enabled?'':' muted');}
  $('rule-speed-desc').textContent='限速 '+c.speed.speed_limit_kmh+' km/h';$('rule-parking-desc').textContent='停留 '+c.parking.min_stay_sec+' 秒';
  const missing=[];if(c.speed.enabled&&(!c.speed.line_a.length||!c.speed.line_b.length))missing.push('测速线');if(c.parking.enabled&&!c.parking.rois.length)missing.push('禁停区');if(c.reverse.enabled&&(!c.reverse.lanes.length||c.reverse.lanes.some(l=>!l.polygon.length||!l.legal_direction.length)))missing.push('逆行车道区域或方向');
  $('calibration-hint').textContent=missing.length?'请补充标注：'+missing.join('、')+'。':'已加载场景标注；请确认与当前视频匹配。';
}
async function loadConfig(){
  const c=await api('/configs/'+encodeURIComponent($('camera-input').value.trim()||'cam_001'));state.config=c;state.lane=0;state.draft=[];state.proposal=null;$('rule-proposal').hidden=true;dirty(false);populateConfig();
}
$('load-config').addEventListener('click',()=>busy($('load-config'),async()=>{await loadConfig();toast('已加载摄像头配置。');}));
$('camera-input').addEventListener('change',()=>loadConfig().catch(e=>toast(e.message,true)));
async function saveConfig(config=state.config){
  const result=await api('/configs/'+encodeURIComponent(config.camera_id),{method:'PUT',body:config});state.config=result.config;dirty(false);populateConfig();toast('配置已保存，并应用到当前摄像头任务。');
}
$('save-config').addEventListener('click',()=>busy($('save-config'),async()=>{for(const input of document.querySelectorAll('#view-rules input[type=number]'))if(!input.reportValidity())throw new Error('请修正参数范围。');await saveConfig();}));
document.querySelectorAll('[data-config]').forEach(input=>input.addEventListener('input',()=>{if(!state.config)return;setPath(state.config,input.dataset.config,input.type==='checkbox'?input.checked:Number(input.value));dirty();updateRuleSummary();}));
$('lane-select').addEventListener('change',()=>{state.lane=Number($('lane-select').value);state.draft=[];populateConfig();});
$('add-lane').addEventListener('click',()=>{let i=state.config.reverse.lanes.length+1;while(state.config.reverse.lanes.some(l=>l.name==='lane_'+i))i++;state.config.reverse.lanes.push(newLane('lane_'+i));state.lane=state.config.reverse.lanes.length-1;dirty();populateConfig();});
$('remove-lane').addEventListener('click',()=>{if(!lane())return;state.config.reverse.lanes.splice(state.lane,1);state.lane=0;dirty();populateConfig();});
$('reverse-duration').addEventListener('input',()=>{if(lane()){lane().min_duration_sec=Number($('reverse-duration').value);dirty();}});
$('reverse-displacement').addEventListener('input',()=>{if(lane()){lane().min_displacement_px=Number($('reverse-displacement').value);dirty();}});
function refreshPreview(){
  const source=$('source-input').value.trim();if(source&&!/^rtsps?:/i.test(source)){$('rule-frame').src='/api/preview?source='+encodeURIComponent(source);$('rule-frame').hidden=false;$('drawing-empty').hidden=true;}
  else if(state.status.latest_frame_ts){$('rule-frame').src='/api/frame?t='+Date.now();$('rule-frame').hidden=false;$('drawing-empty').hidden=true;}
}
$('refresh-preview').addEventListener('click',refreshPreview);
$('rule-frame').addEventListener('error',()=>{$('rule-frame').hidden=true;$('drawing-empty').hidden=false;$('drawing-empty').textContent='无法预览，请检查视频源或先启动监测。';});
const toolHints={none:'选择工具开始绘制；区域需至少三个顶点。',line_a:'点击两个端点绘制测速线 A；线条保持水平。',line_b:'点击两个端点绘制测速线 B；线条保持水平。',parking:'依次点击禁停区域顶点，再点“完成区域”。支持绘制多个禁停区。',lane:'依次点击当前车道的区域顶点，再点“完成区域”。',direction:'先点击起点，再点击终点，箭头表示合法行驶方向。'};
document.querySelectorAll('[data-tool]').forEach(button=>button.addEventListener('click',()=>{state.tool=button.dataset.tool;state.draft=[];document.querySelectorAll('[data-tool]').forEach(el=>el.classList.toggle('selected',el===button));$('draw-hint').textContent=toolHints[state.tool];$('drawing-overlay').classList.toggle('drawing',state.tool!=='none');renderDrawing();}));
function renderDrawing(){
  if(!state.config)return;const c=state.config;
  let content='<defs><marker id="direction-marker" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6" fill="none" stroke="#42dfbf" stroke-width="1.5"/></marker></defs>';
  const line=(points,color,label,arrow=false)=>{if(points.length!==2)return '';return `<line x1="${points[0][0]}" y1="${points[0][1]}" x2="${points[1][0]}" y2="${points[1][1]}" stroke="${color}" stroke-width="3" ${arrow?'marker-end="url(#direction-marker)"':''}/><text x="${points[0][0]+8}" y="${Math.max(18,points[0][1]-8)}" fill="${color}" font-size="17" stroke="none">${esc(label)}</text>`;};
  const polygon=(points,color,label)=>points.length?`<polygon points="${points.map(p=>p.join(',')).join(' ')}" fill="${color}" fill-opacity=".09" stroke="${color}" stroke-width="2"/><text x="${points[0][0]+8}" y="${Math.max(20,points[0][1]+20)}" fill="${color}" font-size="16" stroke="none">${esc(label)}</text>`:'';
  content+=line(c.speed.line_a,'#f6bf65','A')+line(c.speed.line_b,'#75aaff','B');
  c.parking.rois.forEach((p,i)=>content+=polygon(p,'#f6bf65','禁停区 '+(i+1)));
  c.reverse.lanes.forEach((l,i)=>{content+=polygon(l.polygon,i===state.lane?'#bc9dff':'#6483af',l.name);content+=line(l.legal_direction,'#42dfbf','合法方向',true);});
  if(state.draft.length){content+=`<polyline points="${state.draft.map(p=>p.join(',')).join(' ')}" fill="none" stroke="#fff" stroke-dasharray="7 5" stroke-width="2"/>`;state.draft.forEach(p=>content+=`<circle cx="${p[0]}" cy="${p[1]}" r="5" fill="#fff"/>`);}
  $('drawing-overlay').innerHTML=content;
}
$('drawing-overlay').addEventListener('click',event=>{
  if(!state.config||state.tool==='none')return;if($('rule-frame').hidden){toast('请先加载视频预览。',true);return;}
  if(['lane','direction'].includes(state.tool)&&!lane()){toast('请先新增一条车道。',true);return;}
  const rect=$('drawing-overlay').getBoundingClientRect();const point=[Math.round((event.clientX-rect.left)/rect.width*960),Math.round((event.clientY-rect.top)/rect.height*540)];state.draft.push(point);
  if(['line_a','line_b','direction'].includes(state.tool)&&state.draft.length===2){
    if(state.tool==='direction')lane().legal_direction=[...state.draft];else{state.draft[1][1]=state.draft[0][1];state.config.speed[state.tool]=[...state.draft];}state.draft=[];dirty();updateRuleSummary();
  }renderDrawing();
});
$('finish-polygon').addEventListener('click',()=>{if(!['parking','lane'].includes(state.tool)||state.draft.length<3){toast('请选择区域工具，并至少绘制三个顶点。',true);return;}if(state.tool==='parking')state.config.parking.rois.push([...state.draft]);else if(lane())lane().polygon=[...state.draft];state.draft=[];dirty();renderDrawing();updateRuleSummary();});
$('undo-point').addEventListener('click',()=>{state.draft.pop();renderDrawing();});
$('clear-shape').addEventListener('click',()=>{if(!state.config)return;if(['line_a','line_b'].includes(state.tool))state.config.speed[state.tool]=[];else if(state.tool==='parking')state.config.parking.rois=[];else if(state.tool==='lane'&&lane())lane().polygon=[];else if(state.tool==='direction'&&lane())lane().legal_direction=[];else{toast('请先选择要清除的标注类型。');return;}state.draft=[];dirty();renderDrawing();updateRuleSummary();});
document.addEventListener('keydown',event=>{if(event.key==='Escape'){state.draft=[];renderDrawing();closeDrawer();}if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)&&document.activeElement===$('chat-input')){event.preventDefault();sendChat();}});
function eventQuery(){const params=new URLSearchParams();for(const[key,id]of[['event_type','filter-type'],['review_status','filter-review'],['since','filter-since'],['until','filter-until']])if($(id).value)params.set(key,$(id).value);return params;}
async function loadEvents(){
  const params=eventQuery();params.set('limit','20');params.set('offset',String(state.eventPage*20));const data=await api('/events?'+params);cacheEvents(data.items);state.eventTotal=data.total;
  const signature=params.toString()+JSON.stringify(data);if(signature===state.eventsSignature)return;state.eventsSignature=signature;
  $('events-table').innerHTML=data.items.length?data.items.map(item=>{const[t,c]=typeInfo(item.event_type);return `<tr><td><span class="badge ${c}">${t}</span><span class="secondary">${esc(item.timestamp.replace('T',' '))}</span></td><td>${esc(item.camera_id)}</td><td class="mono">${esc(item.track_id)}</td><td><div class="message">${esc(eventSummary(item))}</div></td><td><span class="badge ${item.review_status==='pending'?'amber':'muted'}">${reviews[item.review_status]||'待复核'}</span></td><td><button class="text-button" data-event="${esc(item.id)}">查看证据</button></td></tr>`;}).join(''):'<tr><td colspan="6"><div class="empty">没有符合条件的事件。启动监测后，候选告警会自动保存。</div></td></tr>';
  $('events-count').textContent='共 '+number(data.total)+' 条';$('events-page').textContent=String(state.eventPage+1);$('events-prev').disabled=state.eventPage===0;$('events-next').disabled=(state.eventPage+1)*20>=data.total;
}
function eventSummary(item){const m=item.meta||{};if(item.event_type==='overspeed')return `速度 ${m.speed_kmh??'—'} km/h，限速 ${m.speed_limit_kmh??'—'} km/h`;if(item.event_type==='reverse_driving')return `方向余弦 ${m.cos_theta??'—'}，持续 ${m.duration_sec??'—'} 秒`;if(item.event_type==='illegal_parking')return '禁停区域内持续停留，请查看截图与参数';return item.message;}
$('filter-refresh').addEventListener('click',()=>{state.eventPage=0;loadEvents().catch(e=>toast(e.message,true));});
$('events-prev').addEventListener('click',()=>{state.eventPage=Math.max(0,state.eventPage-1);loadEvents().catch(e=>toast(e.message,true));});
$('events-next').addEventListener('click',()=>{state.eventPage++;loadEvents().catch(e=>toast(e.message,true));});
$('export-events').addEventListener('click',()=>{window.location.href='/api/events/export?'+eventQuery();});
function closeDrawer(){$('event-drawer').hidden=true;$('drawer-backdrop').hidden=true;}
$('close-drawer').addEventListener('click',closeDrawer);$('drawer-backdrop').addEventListener('click',closeDrawer);
document.addEventListener('click',event=>{const target=event.target.closest('[data-event]');if(target)openEvent(target.dataset.event);});
function openEvent(id){
  const item=state.events.get(id);if(!item)return;const[t,c]=typeInfo(item.event_type);
  $('event-detail').innerHTML=`<div class="detail-content"><span class="badge ${c}">${t}</span>${item.snapshot_url?`<img class="detail-image" src="${esc(item.snapshot_url)}" alt="候选告警截图">`:'<div class="empty">此系统事件没有截图</div>'}<div class="detail-grid"><div><small>摄像头</small><b>${esc(item.camera_id)}</b></div><div><small>轨迹 ID</small><b>${esc(item.track_id)}</b></div><div><small>发生时间</small><b>${esc(item.timestamp.replace('T',' '))}</b></div><div><small>事件 ID</small><b>${esc(item.id)}</b></div></div><p class="detail-message">${esc(eventSummary(item))}</p><p class="hint">算法输出为候选事件，需要结合视频、区域标注与现场条件复核。</p><pre>${esc(JSON.stringify(item.meta,null,2))}</pre><button id="explain-event" class="btn subtle full">${icon('spark')}交给 AI 解释此事件</button><div class="review-form"><label>复核状态<select id="review-status">${Object.entries(reviews).map(([k,v])=>`<option value="${k}" ${k===item.review_status?'selected':''}>${v}</option>`).join('')}</select></label><label>复核备注<textarea id="review-note" rows="3" placeholder="记录判断依据或后续处理…">${esc(item.note)}</textarea></label><button id="save-review" class="btn primary">保存复核记录</button></div></div>`;
  $('event-drawer').hidden=false;$('drawer-backdrop').hidden=false;
  $('save-review').addEventListener('click',()=>busy($('save-review'),async()=>{const status=$('review-status').value,note=$('review-note').value;await api('/events/'+encodeURIComponent(id)+'/review',{method:'PATCH',body:{status,note}});item.review_status=status;item.note=note;toast('复核记录已保存。');await poll();}));
  $('explain-event').addEventListener('click',()=>{state.eventId=id;closeDrawer();navigate('assistant');showEventContext();$('chat-input').value='请解释这条告警的触发依据、误报可能性，以及我应如何复核。';$('chat-input').focus();});
}
function showEventContext(){const item=state.events.get(state.eventId);$('chat-event-context').hidden=!item;if(item){$('chat-event-context').innerHTML='<span>已关联事件 '+esc(item.id)+' · '+typeInfo(item.event_type)[0]+'</span><button id="remove-chat-context" class="text-button">移除</button>';$('remove-chat-context').addEventListener('click',()=>{state.eventId=null;showEventContext();});}}
function appendMessage(role,text,meta=''){
  const welcome=$('chat-messages').querySelector('.assistant-welcome');if(welcome)welcome.remove();const div=document.createElement('div');div.className='chat-message '+role;div.innerHTML='<div class="message-author">'+(role==='user'?'你':role==='error'?'请求未完成':icon('spark')+'路瞳分析助手')+'</div><div class="message-body"></div><div class="message-meta"></div>';div.querySelector('.message-body').textContent=text;div.querySelector('.message-meta').textContent=meta;$('chat-messages').append(div);$('chat-messages').scrollTop=$('chat-messages').scrollHeight;return div;
}
async function sendChat(mode='chat',preset=null){
  if(state.aiBusy)return;const message=preset||$('chat-input').value.trim();if(!message){toast('请输入问题。');return;}
  state.aiBusy=true;const attach=$('attach-frame').checked;const eventId=state.eventId;const body={message,mode:eventId&&mode==='chat'?'explain':mode,attach_frame:attach,event_id:eventId,history:state.history.slice(-10)};
  appendMessage('user',message,attach?'已请求附加画面':'仅发送相关告警和规则数据');$('chat-input').value='';const pending=appendMessage('assistant','正在分析监测数据…');$('send-chat').disabled=true;
  try{const result=await api('/ai/chat',{method:'POST',body});pending.querySelector('.message-body').textContent=result.content;pending.querySelector('.message-meta').textContent=result.model+(result.frame_attached?' · 含图片输入':' · 文本上下文')+(result.usage?.total_tokens?' · '+result.usage.total_tokens+' tokens':'');state.history.push({role:'user',content:message},{role:'assistant',content:result.content});if(result.report){toast('报告已保存到“分析报告”。');const link=document.createElement('a');link.className='text-button';link.href='/api/reports/'+result.report.id+'/download';link.textContent='下载此报告';pending.append(link);}}
  catch(e){pending.classList.add('error');pending.querySelector('.message-body').textContent=e.message;pending.querySelector('.message-meta').textContent='未生成模型结论；请检查模型设置或稍后重试。';}
  finally{state.aiBusy=false;$('send-chat').disabled=false;$('chat-messages').scrollTop=$('chat-messages').scrollHeight;}
}
$('send-chat').addEventListener('click',()=>sendChat());
document.querySelectorAll('[data-prompt]').forEach(el=>el.addEventListener('click',()=>{$('chat-input').value=el.dataset.prompt;if(el.dataset.image){$('attach-frame').checked=true;updateImageContext();}$('chat-input').focus();}));
function updateImageContext(){$('image-context-state').textContent=$('attach-frame').checked?'发送时附加':'未附加';}
$('attach-frame').addEventListener('change',updateImageContext);
function generateReport(){navigate('assistant');sendChat('report','请基于当前保存的告警、统计与规则，生成一份完整的交通监测分析报告，明确样本范围和复核建议。');}
$('generate-report').addEventListener('click',generateReport);$('new-report').addEventListener('click',generateReport);
$('clear-chat').addEventListener('click',()=>{if(state.aiBusy){toast('请等待当前分析结束。');return;}state.history=[];state.eventId=null;showEventContext();$('chat-messages').innerHTML='<div class="empty">新对话已开始。输入问题，或关联告警进行分析。</div>';});
$('rules-ai-btn').addEventListener('click',()=>busy($('rules-ai-btn'),async()=>{
  if(state.dirty)throw new Error('请先保存当前手动修改，再生成 AI 规则预览。');const message=$('rules-prompt').value.trim();if(!message)throw new Error('请描述你想修改的规则。');
  const result=await api('/ai/rules/preview',{method:'POST',body:{message,camera_id:state.config.camera_id}});state.proposal=result;
  $('rule-proposal').hidden=false;
  if(!result.changes.length){$('rule-proposal').innerHTML='<p class="hint">未识别到需要修改的参数。请明确描述规则、数值和单位。</p>';return;}
  $('rule-proposal').innerHTML='<div class="proposal-list">'+result.changes.map(change=>`<div><span>${esc(fields[change.field.split(':')[0]]||change.field)}</span><span class="proposal-values"><del>${esc(String(change.before))}</del>${esc(String(change.after))}</span></div>`).join('')+'</div><div class="heading-actions"><button id="apply-proposal" class="btn primary">应用这些修改</button><button id="cancel-proposal" class="btn subtle">取消</button></div><p class="hint">预览尚未修改配置。确认后保存，并应用到当前摄像头。</p>';
  $('apply-proposal').addEventListener('click',()=>busy($('apply-proposal'),async()=>{await saveConfig(state.proposal.config);$('rule-proposal').hidden=true;state.proposal=null;}));
  $('cancel-proposal').addEventListener('click',()=>{state.proposal=null;$('rule-proposal').hidden=true;});
},'正在理解规则…'));
async function loadAISettings(){const settings=await api('/ai/settings');$('ai-base').value=settings.base_url;$('ai-model').value=settings.model;$('ai-vision-model').value=settings.vision_model;$('ai-vision-enabled').checked=settings.supports_vision;$('ai-key').value='';$('key-status').textContent=settings.key_configured?(settings.key_from_environment?'使用 DEEPSEEK_API_KEY 环境变量':'已保存密钥（不会回传浏览器）；留空可保留'):'尚未保存密钥';$('ai-connection').textContent=settings.key_configured?'已配置 API':'未连接 API';$('ai-connection').className='badge '+(settings.key_configured?'':'muted');return settings;}
function settingsBody(clear=false){return {base_url:$('ai-base').value.trim(),model:$('ai-model').value.trim(),vision_model:$('ai-vision-model').value.trim(),supports_vision:$('ai-vision-enabled').checked,api_key:$('ai-key').value.trim(),clear_key:clear};}
$('ai-settings-form').addEventListener('submit',event=>{event.preventDefault();busy(event.submitter,async()=>{await api('/ai/settings',{method:'PUT',body:settingsBody()});await loadAISettings();$('api-test-result').textContent='设置已保存。点击“测试连接”验证服务。';toast('API 设置已保存。');});});
$('test-ai').addEventListener('click',()=>busy($('test-ai'),async()=>{$('api-test-result').textContent='正在请求已保存的模型配置…';try{const result=await api('/ai/test',{method:'POST'});$('api-test-result').textContent='连接成功 · '+result.model+' · '+result.content;toast('模型 API 连接成功。');}catch(e){$('api-test-result').textContent=e.message;throw e;}},'测试中…'));
$('clear-key').addEventListener('click',()=>busy($('clear-key'),async()=>{await api('/ai/settings',{method:'PUT',body:settingsBody(true)});await loadAISettings();toast('已清除本机保存的密钥；环境变量需在启动环境中移除。');}));
async function loadReports(){const reports=await api('/reports');$('reports-list').innerHTML=reports.length?reports.map(report=>`<article class="panel report-card"><div class="panel-body"><div class="report-icon">${icon('report')}</div><h2>${esc(report.title)}</h2><div class="report-date">${esc(report.created_at.replace('T',' '))} · ${esc(report.model)}</div><div class="report-preview">${esc(report.content.slice(0,250))}</div><div class="report-actions"><button class="text-button" data-report="${esc(report.id)}">阅读报告</button><a class="text-button" href="/api/reports/${esc(report.id)}/download">下载 Markdown</a></div></div></article>`).join(''):'<article class="panel" style="grid-column:1/-1"><div class="empty">暂无报告。连接模型 API 后，可基于真实监测数据生成报告。</div></article>';document.querySelectorAll('[data-report]').forEach(button=>button.addEventListener('click',()=>{const report=reports.find(r=>r.id===button.dataset.report);$('report-content').textContent=report.content;$('download-report').href='/api/reports/'+report.id+'/download';$('report-dialog').showModal();}));}
$('close-report').addEventListener('click',()=>$('report-dialog').close());
function clock(){$('clock').textContent=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date());}
async function initialize(){clock();setInterval(clock,30000);navigate(location.hash.slice(1)||'monitor');const tasks=await Promise.allSettled([loadConfig(),loadSources(),loadAISettings()]);for(const task of tasks)if(task.status==='rejected')toast(task.reason.message,true);await poll();setInterval(poll,1600);}
initialize();
window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));
