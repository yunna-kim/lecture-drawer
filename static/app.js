"use strict";
const $ = s => document.querySelector(s);
const csrf = $('meta[name="csrf-token"]').content;
let current = null, selected = new Set(), playing = null, requestItems = [];
let editingJob = null;
const labels = {queued:"전사 대기",queued_summary:"음성 우선 · 로컬 요약 대기",running:"전사 중",saving:"정리·저장 중",waiting_ai:"정리 AI 대기",completed:"저장 완료",failed:"확인 필요",cancelled:"취소됨"};
const e = (tag, text, cls) => {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
function alertText(text){$('#alert').textContent=text;$('#alert').hidden=!text;}
async function api(path, body){const options=body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:JSON.stringify(body)};const r=await fetch(path,options);const value=await r.json().catch(()=>({error:"프로그램 연결에 실패했습니다. 다시 열어 주세요."}));if(!r.ok)throw Error(value.error||"요청이 완료되지 않았습니다.");return value;}
const bytes = n => n>1024**3?(n/1024**3).toFixed(1)+" GB":(n/1024**2).toFixed(1)+" MB";
const time = n => new Date(n*1000).toLocaleString("ko-KR",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"});
const clock = s => [Math.floor(s/3600),Math.floor(s/60)%60,Math.floor(s)%60].map(n=>String(n).padStart(2,'0')).join(':');
function button(text, fn, cls="secondary"){const b=e('button',text,cls);b.type="button";b.addEventListener('click',()=>Promise.resolve(fn()).catch(x=>alertText(x.message)));return b;}
function badge(text,cls=""){return e('span',text,'badge '+cls);}
function updateSelected(){selected=new Set([...selected].filter(id=>current.assets.some(a=>a.id===id&&a.stable&&(a.candidate||a.transcribed))));$('#selection-count').textContent=selected.size?`${selected.size}개 선택 · 나머지 녹음은 백업만`:'선택한 파일 없음';$('#prepare').disabled=!!current.presentation||!selected.size||!current.profiles.length;}
function renderAssets(){
  const query=$('#search').value.toLowerCase();
  const pending=current.assets.filter(a=>!a.transcribed&&a.candidate);
  const deferred=current.assets.filter(a=>!a.transcribed&&!a.candidate);
  const complete=current.assets.filter(a=>a.transcribed);
  const matching=rows=>rows.filter(a=>a.relative_path.toLowerCase().includes(query));
  const pendingMatches=matching(pending),completedMatches=matching(complete),deferredMatches=matching(deferred);
  const table=$('#assets'),done=$('#completed-assets');table.replaceChildren();done.replaceChildren();
  const deferredTable=$('#deferred-assets');deferredTable.replaceChildren();
  const deferredFold=$('#deferred-recordings');deferredFold.hidden=!deferred.length;
  $('#deferred-count').textContent=`전사 보류 ${deferred.length}개 · 보관된 미전사`+(query?` · 검색 결과 ${deferredMatches.length}개`:'');
  const folded=$('#completed-recordings');folded.hidden=!complete.length;
  $('#completed-count').textContent=`전사 완료 ${complete.length}개`+(query?` · 검색 결과 ${completedMatches.length}개`:'');
  $('#empty').hidden=pendingMatches.length>0;
  $('#empty-title').textContent=!current.assets.length?'녹음이 들어올 자리를 준비했습니다.':query?'전사 예정 목록에 검색 결과가 없습니다.':'전사 예정인 녹음이 없습니다.';
  $('#empty-description').textContent=!current.assets.length?'휴대폰의 녹음을 NAS 백업 폴더에 넣으면 이곳에 나타납니다.':'아래 전사 보류 목록을 펼쳐 전사할 녹음만 예정에 추가하세요.';
  function append(asset,target){
    const tr=e('tr'),box=e('input');box.type='checkbox';box.checked=selected.has(asset.id);box.disabled=!asset.stable||(!asset.candidate&&!asset.transcribed);box.setAttribute('aria-label',asset.relative_path+' 선택');box.addEventListener('change',()=>{box.checked?selected.add(asset.id):selected.delete(asset.id);updateSelected();});
    const cell=e('td');cell.append(box);tr.append(cell,e('td',asset.relative_path,'name'),e('td',time(asset.mtime_ns/1e9)),e('td',bytes(asset.size)));
    const state=e('td');state.append(badge(asset.transcribed?'전사 완료':asset.candidate?'전사 예정':!asset.stable?'파일 확인 중':'전사 보류',asset.transcribed?'green':''));
    const play=e('td');play.append(button('듣기',()=>playAsset(asset),'text-button'));if(!asset.transcribed&&!current.presentation){play.append(button(asset.candidate?'보류로 내리기':'전사 예정에 추가',()=>setCandidate(asset,!asset.candidate),'text-button'));}tr.append(state,play);target.append(tr);
  }
  pendingMatches.forEach(asset=>append(asset,table));
  if(folded.open)completedMatches.forEach(asset=>append(asset,done));
  if(deferredFold.open)deferredMatches.forEach(asset=>append(asset,deferredTable));
  updateSelected();
}
async function setCandidate(asset,transcribe){await api(`/api/assets/${asset.id}/plan`,{transcribe,size:asset.size,mtime_ns:asset.plan_mtime_ns});if(!transcribe)selected.delete(asset.id);await refresh();}
function isOlderCompleted(job, now=Date.now()/1000){
  return job.status==='completed' && Number.isFinite(job.completed_at) && now-job.completed_at>=86400;
}
function renderJobs(){
  const root=$('#jobs'), olderRoot=$('#older-jobs'), details=$('#older-jobs-details');
  root.replaceChildren();olderRoot.replaceChildren();
  const now=Date.now()/1000, older=current.jobs.filter(job=>isOlderCompleted(job,now));
  const visible=current.jobs.filter(job=>!isOlderCompleted(job,now));
  details.hidden=!older.length;
  $('#older-job-count').textContent=`이전 완료 작업 ${older.length}개`;
  if(!visible.length)root.append(e('div',current.jobs.length?'지금 처리할 작업이 없습니다. 이전 완료 작업은 아래에서 펼쳐보세요.':'아직 선택한 전사 작업이 없습니다. 녹음 목록에서 강의를 골라주세요.','no-jobs'));
  for(const job of [...visible,...(details.open?older:[])]){
    const card=e('article',undefined,'job-card');const heading=e('div',undefined,'job-heading');heading.append(e('h3',job.display_title||(job.output_path?job.output_path.split(/[\\/]/).pop().replace(/\.md$/,''):job.params.title)),badge(labels[job.status]||job.status,job.status==='completed'?'green':'orange'));card.append(heading,e('p',`${time(job.requested_at)} · ${job.params.profile.label}${jobAi(job)}`));if(job.progress.message)card.append(e('p',job.progress.message));if(['running','saving'].includes(job.status)){const p=e('progress');p.max=1;if(job.progress.fraction!==undefined)p.value=job.progress.fraction;card.append(p);}if(job.error)card.append(e('p',job.error,'job-error'));const actions=e('div',undefined,'job-actions');if(['queued','queued_summary','running','saving','waiting_ai'].includes(job.status))actions.append(button('취소',()=>act(job.id,'cancel')));if(['failed','cancelled','waiting_ai'].includes(job.status))actions.append(button('같은 선택 재시도 · AI 다시 사용 가능',()=>act(job.id,'retry')));if(['completed','waiting_ai'].includes(job.status)){actions.append(button('전사·원음 확인',()=>playJob(job)));if(job.status==='completed')actions.append(button('옵시디언에서 열기',()=>act(job.id,'open')));}if(!current.presentation)actions.append(button('제목 수정',()=>editTitles(job)));card.append(actions);
    (isOlderCompleted(job,now)?olderRoot:root).append(card);
  }
}
function renderSettings(){const paths=$('#paths');paths.replaceChildren();for(const [label,key] of [['원음 백업 · 선택한 자동 제목에만 이름 변경','nas_root'],['전사 텍스트 · 옵시디언','wiki_root']])paths.append(e('dt',label),e('dd',current.settings[key]));const list=$('#profiles');list.replaceChildren();for(const profile of current.profiles){const item=e('div',undefined,'profile-item');item.append(e('span',profile.label),badge(profile.id===current.settings.active_profile?'기본 모델':profile.tested?'검토 기록 있음':'시험 후보',profile.id===current.settings.active_profile?'green':''));if(profile.id!==current.settings.active_profile){const done=current.jobs.find(j=>j.status==='completed'&&j.params.profile.id===profile.id);if(done)item.append(button('시험 결과를 검토했고 기본값으로 적용',async()=>{await api('/api/profiles/activate',{profile_id:profile.id,reviewed_job:done.id});await refresh();}));}list.append(item);}if(!current.profiles.length)list.append(e('p','로컬 모델 등록이 필요합니다. 환경 점검을 실행하세요.'));$('#rollback').disabled=!!current.presentation||!current.settings.previous_profile;if(current.presentation)for(const b of document.querySelectorAll('#profiles button'))b.disabled=true;}
function jobAi(job){const ai=job.params.note_ai;if(!ai)return '';const used=job.params.auto_title||job.params.note_template?.id!=='raw';if(!used)return ' · AI 미사용';return ai.provider==='claude-api'?' · 정리 AI: Claude API':ai.provider==='openai-api'?' · 정리 AI: OpenAI API':ai.provider==='claude-subscription'?' · 정리 AI: Claude 구독':ai.provider==='ollama'?' · 정리 AI: PC 로컬':'';}
async function refresh(){current=await api('/api/status');const route=current.note_ai_route;$('#ai-route').textContent=route?`새 요청 정리 AI: ${route.text}`:'';$('#ai-route').classList.toggle('warn',!!route&&!route.ready);const credit=current.api_credit,creditBox=$('#ai-credit');creditBox.hidden=!credit;creditBox.replaceChildren();if(credit){const money=v=>v===null||v===undefined?'확인 불가':'$'+v.toFixed(2);const link=document.createElement('a');link.href='https://platform.claude.com/settings/billing';link.target='_blank';link.rel='noopener';link.textContent='정확한 잔액 보기';creditBox.append(document.createTextNode(`크레딧 잔액 추정 ${money(credit.remaining)} / $${credit.limit}`),document.createElement('br'),document.createTextNode(`강의 서랍 ${money(credit.lecture)} · 옵시디언 ${money(credit.obsidian)}`),document.createElement('br'),link);}$('#presentation-banner').hidden=!current.presentation;$('#refresh').disabled=!!current.presentation;for(const n of document.querySelectorAll('.nav'))n.hidden=false;$('#nas-state').textContent=current.catalog.connected?'연결됨':current.catalog.error?'연결 대기':'확인 중';$('#nas-description').textContent=current.catalog.connected?'지정한 녹음 폴더에서 읽기':current.catalog.error||'백업 폴더를 확인합니다';$('#asset-count').textContent=current.assets.length+'개';const pending=current.jobs.filter(j=>['queued','queued_summary','running','saving','waiting_ai'].includes(j.status));$('#job-count').textContent=current.jobs.filter(j=>j.status==='completed').length+'개 완료';$('#job-description').textContent=pending.length?pending.length+'개 처리 대기·진행 중':'기다리는 작업이 없습니다';renderAssets();renderJobs();renderSettings();}
async function act(id,action){await api(`/api/jobs/${id}/${action}`,{});await refresh();}
async function editTitles(job){const value=await api(`/api/jobs/${job.id}/titles`);editingJob=job.id;$('#edit-job-title').value=value.display_title;$('#edit-file-title').value=value.file_title;$('#edit-file-title').disabled=!value.file_editable;$('#file-title-help').textContent=value.file_editable?'원음과 옵시디언 노트에 함께 적용합니다. 날짜·분류·확장자는 자동으로 유지합니다.':'파일 제목은 전사·저장 완료 후 바꿀 수 있습니다. 작업 제목은 지금 수정할 수 있습니다.';$('#title-edit-error').hidden=true;$('#title-dialog').showModal();}
$('#dismiss-title').addEventListener('click',()=>{$('#title-dialog').close();editingJob=null;});
$('#title-form').addEventListener('submit',async event=>{event.preventDefault();const save=$('#save-title');save.disabled=true;try{await api(`/api/jobs/${editingJob}/titles`,{display_title:$('#edit-job-title').value,file_title:$('#edit-file-title').disabled?null:$('#edit-file-title').value});$('#title-dialog').close();editingJob=null;alertText('제목을 수정했습니다. 전사 기록과 원음 연결은 유지됩니다.');await refresh();}catch(error){$('#title-edit-error').textContent=error.message;$('#title-edit-error').hidden=false;}finally{save.disabled=false;}});
function showTab(tab){if(tab==='team')loadTeamPreview();for(const section of document.querySelectorAll('.tab'))section.hidden=section.id!==`tab-${tab}`;for(const nav of document.querySelectorAll('.nav'))nav.classList.toggle('active',nav.dataset.tab===tab);}
function prepare(items){
  requestItems=items;const root=$('#request-fields');root.replaceChildren();
  for(const [index,item] of items.entries()){
    const block=e('div',undefined,'request-item');block.append(e('small',item.relative_path));
    const title=e('label','강좌명·회차 · 선택사항');const input=e('input');
    input.value=item.title||'';input.placeholder='예: 예시스터디 오프라인 1교시 · 내용 주제와 조합해 제목에 반영';input.maxLength=120;input.dataset.title=index;title.append(input);
    const hints=e('label','용어 힌트 · 선택사항');const textarea=e('textarea');textarea.placeholder='예: 강사명, 약재명, 영문 약어';textarea.maxLength=1500;textarea.dataset.hints=index;hints.append(textarea);
    const templateLabel=e('label','정리 템플릿');const template=e('select');template.dataset.template=index;
    for(const entry of current.templates||[{id:'raw',label:'전사 원문만'}]){const option=e('option',entry.label);option.value=entry.id;template.append(option);}templateLabel.append(template);
    const details=e('details');details.hidden=true;details.append(e('summary','이 작업의 AI 프롬프트 확인·수정'));
    const prompt=e('textarea');prompt.dataset.prompt=index;prompt.rows=9;prompt.maxLength=20000;prompt.setAttribute('aria-label','정리 프롬프트');details.append(prompt,e('small','기존 PLAUD 원본은 바꾸지 않고 이번 요청에만 적용합니다. {transcript}는 전사 원문 자리입니다.'));
    template.addEventListener('change',async()=>{const id=template.value;details.hidden=id==='raw';prompt.value='';if(id!=='raw'){try{const value=await api('/api/templates/'+id);if(template.value===id)prompt.value=value.body;}catch(error){alertText(error.message);template.value='raw';details.hidden=true;}}});
    if(current.note_ai_provider!=='disabled'&&[...template.options].some(o=>o.value==='note-lecture')){template.value='note-lecture';template.dispatchEvent(new Event('change'));}
    const autoLabel=e('label','');const auto=e('input');auto.type='checkbox';auto.dataset.autoTitle=index;auto.checked=current.note_ai_provider!=='disabled';autoLabel.append(auto,document.createTextNode(' 내용으로 강의 제목 자동 생성'));
    const roleLabel=e('label','노트 제목의 분류');const role=e('select');role.dataset.role=index;for(const [value,label] of [['수강','수강 · 내가 듣는 강의'],['강의','강의 · 내가 진행한 강의']]){const option=e('option',label);option.value=value;role.append(option);}roleLabel.append(role);
    const aiLabel=e('label','제목·정리 AI');const ai=e('select');ai.dataset.noteAi=index;for(const profile of current.note_ai_profiles||[]){const option=e('option',profile.label);option.value=profile.id;option.disabled=!profile.available;ai.append(option);}if((current.note_ai_profiles||[]).some(p=>p.id===current.note_ai_default&&p.available))ai.value=current.note_ai_default;aiLabel.append(ai);
    block.append(title,hints,roleLabel,templateLabel,details,aiLabel,autoLabel,e('small','NAS 음성: 기존파일명_강의제목 · 노트: 날짜 [수강/강의] 제목.md · 변경 전후 파일명은 노트에 기록합니다. 음성 수정일을 유지합니다.'));
    block.append(e('small','Claude 선택 시 전사 텍스트를 기존 구독으로 전송하며 포함 사용량을 씁니다. 추가 API로 전환하지 않습니다. 원문만 + 자동 제목 끄기는 정리 AI를 쓰지 않습니다.'));
    if(item.start!==undefined)block.append(e('p',`${clock(item.start)}부터 ${clock(item.end)}까지 · NAS 원본은 변경하지 않습니다.`));root.append(block);
  }
  const select=$('#request-profile');select.replaceChildren();for(const profile of current.profiles){const option=e('option',profile.label);option.value=profile.id;option.selected=profile.id===current.settings.active_profile;select.append(option);}$('#request-dialog').showModal();
}
async function playAsset(asset,position=0){playing=asset;$('#player-section').hidden=false;$('#player-title').textContent=asset.relative_path;$('#segments').replaceChildren();const player=$('#player');player.src=`/api/audio/${asset.id}`;player.load();if(position)player.addEventListener('loadedmetadata',()=>player.currentTime=position,{once:true});$('#range-start').value=Math.floor(position);$('#range-end').value='';$('#player-section').scrollIntoView({behavior:'smooth'});}
async function playJob(job){const asset=current.assets.find(a=>a.id===job.asset_id);if(!asset)throw Error('NAS에서 원음을 찾지 못했습니다. 백업 연결을 확인하세요.');await playAsset(asset);$('#player').src=`/api/audio/${asset.id}?job=${job.id}`;const result=await api(`/api/jobs/${job.id}/result`);const root=$('#segments');for(const segment of result.paragraphs||result.segments){const row=e('div',undefined,'segment');row.append(button(clock(segment.start),()=>{$('#player').currentTime=segment.start;$('#range-start').value=Math.floor(segment.start);$('#range-end').value=Math.ceil(segment.end);return $('#player').play();},'text-button'),e('p',segment.text));root.append(row);}}
document.querySelectorAll('.nav').forEach(b=>b.addEventListener('click',()=>showTab(b.dataset.tab)));
$('#search').addEventListener('input',()=>current&&renderAssets());$('#refresh').addEventListener('click',async()=>{try{await api('/api/scan',{});await refresh();}catch(x){alertText(x.message);}});
$('#prepare').addEventListener('click',()=>prepare(current.assets.filter(a=>selected.has(a.id))));$('#dismiss').addEventListener('click',()=>$('#request-dialog').close());
$('#request-form').addEventListener('submit',async event=>{event.preventDefault();const submit=$('#submit-request');submit.disabled=true;submit.textContent='선택한 파일의 지문 확인 중…';try{const selections=requestItems.map((item,index)=>({asset_id:item.id,title:document.querySelector(`[data-title="${index}"]`).value,hints:document.querySelector(`[data-hints="${index}"]`).value,template_id:document.querySelector(`[data-template="${index}"]`).value,prompt:document.querySelector(`[data-template="${index}"]`).value==='raw'?null:document.querySelector(`[data-prompt="${index}"]`).value,auto_title:document.querySelector(`[data-auto-title="${index}"]`).checked,lecture_role:document.querySelector(`[data-role="${index}"]`).value,note_ai_id:document.querySelector(`[data-note-ai="${index}"]`).value||null,start:item.start||0,end:item.end||null}));await api('/api/jobs',{selections,profile_id:$('#request-profile').value});selected.clear();$('#request-dialog').close();alertText('선택한 작업을 등록했습니다. 완료되면 옵시디언에 자동 저장합니다.');showTab('jobs');await refresh();}catch(x){alertText(x.message);$('#request-dialog').close();}finally{submit.disabled=false;submit.textContent='전사하고 옵시디언에 저장';}});
$('#close-player').addEventListener('click',()=>{$('#player').pause();$('#player').removeAttribute('src');$('#player-section').hidden=true;playing=null;});$('#range-request').addEventListener('click',()=>{const start=Number($('#range-start').value),end=Number($('#range-end').value);if(!playing||end<=start){alertText('구간 시작과 끝을 초 단위로 입력해 주세요.');return;}prepare([{...playing,title:playing.relative_path.replace(/\.[^.]+$/,'')+' 구간',start,end}]);});
$('#rollback').addEventListener('click',async()=>{try{await api('/api/profiles/activate',{rollback:true});await refresh();}catch(x){alertText(x.message);}});
refresh().then(async()=>{if(current.presentation)showTab('jobs');else if(new URLSearchParams(location.search).get('tab')==='team')showTab('team');const id=new URLSearchParams(location.search).get('job');if(id){const job=current.jobs.find(j=>j.id===id);if(job){showTab('jobs');await playJob(job);}}}).catch(x=>alertText(x.message));setInterval(()=>refresh().catch(()=>alertText('프로그램 연결이 끊겼습니다. 실행 바로가기로 다시 열어주세요.')),4000);

$('#completed-recordings').addEventListener('toggle',()=>current&&renderAssets());

// An already-running Flask process may still serve its cached previous template.
// Keep a page reload safe while transcription continues without a restart.
if(!$('#older-jobs-details')){
  const details=e('details',undefined,'completed-recordings');details.id='older-jobs-details';details.hidden=true;
  const summary=e('summary'),count=e('span'),toggle=e('small',undefined,'completed-toggle'),body=e('div');
  count.id='older-job-count';body.id='older-jobs';summary.append(count,toggle);details.append(summary,body);$('#jobs').after(details);
}
$('#older-jobs-details').addEventListener('toggle',()=>current&&renderJobs());

function setupFloatingPlayer(){
  const player=$('#player'), panel=$('#player-section');
  const anchor=e('div'),dock=e('div'),caption=e('div',undefined,'audio-dock-caption');
  anchor.id='player-anchor';dock.id='audio-dock';
  const name=e('span',undefined,'audio-dock-name');
  caption.append(name,button('재생창으로',()=>anchor.scrollIntoView({behavior:'smooth',block:'center'}),'text-button'),
    button('닫기',()=>$('#close-player').click(),'text-button'));
  player.before(anchor);anchor.append(dock);dock.append(caption,player);
  let scheduled=false;
  function update(){
    scheduled=false;
    const floating=!panel.hidden&&anchor.getBoundingClientRect().bottom<=12;
    name.textContent=$('#player-title').textContent;
    dock.classList.toggle('floating',floating);
    document.body.classList.toggle('has-floating-player',floating);
  }
  function schedule(){if(!scheduled){scheduled=true;requestAnimationFrame(update);}}
  window.addEventListener('scroll',schedule,{passive:true});
  window.addEventListener('resize',schedule);
  new MutationObserver(schedule).observe(panel,{attributes:true,attributeFilter:['hidden']});
  update();
}
setupFloatingPlayer();

let verificationTimer=null;
async function loadTeamPreview(){
  if(!$('#team-reports'))return;
  const error=$('#team-error');error.hidden=true;
  try{
    const team=await api('/api/team-preview');
    const jobs=$('#verify-job'),previous=jobs.value;jobs.replaceChildren();
    for(const job of (current?.jobs||[]).filter(j=>j.status==='completed')){const option=e('option',job.display_title||job.params.title);option.value=job.id;jobs.append(option);}
    if([...jobs.options].some(o=>o.value===previous))jobs.value=previous;
    const validation=team.verification||{};
    $('#verify-status').textContent=validation.message||validation.error||'완료한 강의를 골라 검증할 수 있습니다.';
    $('#verify-run').disabled=!!current?.presentation||['running','waiting_voice'].includes(validation.status)||!jobs.options.length;
    clearTimeout(verificationTimer);
    if(['running','waiting_voice'].includes(validation.status))verificationTimer=setTimeout(loadTeamPreview,3000);
    $('#team-state').textContent=team.processing_enabled?'공동 처리 설정 확인 필요':'공동 자동 처리 꺼짐';
    const status=$('#team-status');status.replaceChildren();
    for(const [label,value] of [
      ['공동 시험 공간',team.prepared?'준비됨 · 개인 설정과 분리':'아직 준비하지 않음'],
      ['PC 접근 권한 분리',team.isolation_verified?'검증 표시 있음':'실제 권한 검증 전'],
      ['구글 공유 폴더',team.drive_connected?'연결됨':'연결 준비 중'],
      ['공용 요약 모델',team.model_adopted?'채택됨':'Qwen 4B 시험 완료 · 채택 보류']
    ])status.append(e('dt',label),e('dd',value));
    const paths=$('#team-paths');paths.replaceChildren();
    for(const [label,key] of [['시험 설정·보고','state'],['시험 입력','input'],['시험 출력','output']])paths.append(e('dt',label),e('dd',team.paths[key]));
    const reports=$('#team-reports');
    const drafts=new Map([...reports.querySelectorAll('[data-report]')].map(card=>[card.dataset.key,{note:card.querySelector('textarea')?.value||'',open:card.querySelector('details')?.open||false}]));
    const histories=new Set([...reports.querySelectorAll('details[data-history][open]')].map(node=>node.dataset.history));
    reports.replaceChildren();
    if(!team.reports.length)reports.append(e('p','아직 저장된 시험 결과가 없습니다.','no-jobs'));
    const groups=new Map();
    for(const report of team.reports){const key=report.source_job||report.report_id;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(report);}
    for(const [key,items] of groups){
      const group=e('section',undefined,'report-group');group.dataset.group=key;
      const history=e('details',undefined,'report-history');history.dataset.history=key;history.open=histories.has(key);
      history.append(e('summary',`이전 검증 이력 ${items.length-1}개 · 펼쳐서 비교`));
      for(const [index,report] of items.entries()){

      const card=e('article',undefined,'settings-card');card.dataset.report=report.report_id;card.dataset.key=report.report_id+report.report_sha;const draft=drafts.get(card.dataset.key);
      card.append(e('span',index===0?'최근 검증':'이전 검증','report-kicker'));
      const heading=e('div',undefined,'job-heading');heading.append(e('h3',report.title),badge(report.review?.verdict==='passed'?'사용자 내용 검토 통과 · 미채택':'시험 초안 · 검토 필요','orange'));
      card.append(heading,e('p',`${report.whole_lecture?'전체 강의':'일부 구간'} · ${clock(report.start)}–${clock(report.end)} · 처리 ${Number(report.seconds).toFixed(1)}초`));
      card.append(e('p',`${report.pipeline==='hierarchical-v3'?'개선 시험 v3':'이전 시험 v2'}${report.cache_hits?` · 이전 구간 정리 ${report.cache_hits}개 재사용 · ${report.new_calls===0?'캐시 조회':'이번 생성 단계'} ${Number(report.seconds).toFixed(1)}초 (전체 처리시간 아님)`:''}`));
      if(report.quality_issues.length){const issues=e('ul',undefined,'team-issues');for(const issue of report.quality_issues)issues.append(e('li',issue));card.append(issues);}
      if(report.error)card.append(e('p',report.error,'job-error'));
      card.append(e('p',`구조 검사: ${report.status==='completed'&&!report.quality_issues.length?'통과':'보완 필요'} · 원문 보존: ${report.asr_unchanged?'확인':'확인 필요'} · 내용 검토: ${report.review?(report.review.verdict==='passed'?'사용자 검토 통과':'보완 필요'):'미검토'}`));
      if(report.review)card.append(e('p',report.review.note));
      const reviewNote=e('textarea');reviewNote.placeholder='인물·숫자·부정·누락을 확인한 내용 또는 보완할 점';reviewNote.maxLength=2000;reviewNote.value=draft?.note||'';reviewNote.setAttribute('aria-label',report.title+' 검토 근거');
      const actions=e('div',undefined,'job-actions');
      for(const [label,verdict] of [['내용 검토 통과','passed'],['보완 필요 기록','needs-fix']]){const b=button(label,async()=>{await api('/api/verification/review',{report_id:report.report_id,report_sha:report.report_sha,verdict,note:reviewNote.value});await loadTeamPreview();});if(verdict==='passed')b.disabled=report.status!=='completed'||report.quality_issues.length>0||!report.asr_unchanged;actions.append(b);}
      card.append(reviewNote,actions);
      if(report.summary){const details=e('details',undefined,'team-result');details.open=draft?.open||false;details.append(e('summary','생성된 요약 초안 보기'),e('div',report.summary,'team-summary'));card.append(details);}
      (index===0?group:history).append(card);
      }
      if(items.length>1)group.append(history);
      reports.append(group);
    }
  }catch(x){error.textContent=x.message;error.hidden=false;}
}
$('#team-refresh')?.addEventListener('click',loadTeamPreview);

$('#verify-run')?.addEventListener('click',async()=>{try{await api('/api/verification/run',{job_id:$('#verify-job').value,start:Number($('#verify-start').value),seconds:Number($('#verify-seconds').value),whole:$('#verify-whole').checked});await loadTeamPreview();}catch(x){alertText(x.message);}});



$('#deferred-recordings').addEventListener('toggle',()=>{if(current)renderAssets();});

$('#presentation-exit').addEventListener('click',async()=>{try{await api('/api/presentation/exit',{});location.href='/';}catch(x){alertText(x.message);}});
