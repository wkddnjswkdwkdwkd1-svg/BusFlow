'use strict';
const $=q=>document.querySelector(q);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let catalog={}, results=[], focusedId=null, liveTimer=null, busy=false;
let plan={mode:'morning',routeFamily:'all',selected:new Set(),ranges:{},destination:null,riskMode:'safe',departureAny:false,deadlineAny:false};
let weatherVersion=0;
const kstToday=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(new Date());
const routes=()=>Object.keys(catalog).filter(r=>r.endsWith(plan.mode==='morning'?'A':'B')&&(plan.routeFamily==='all'||r.startsWith(plan.routeFamily)));
function showPage(name){
  document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id==='page-'+name));
  if(name!=='live') clearInterval(liveTimer);
  window.scrollTo({top:0,behavior:'smooth'});
}
async function api(path,options={}){
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),90000);
  try{
    const r=await fetch(path,{...options,signal:controller.signal});
    const data=await r.json(); if(!r.ok)throw new Error(data.error||'서버 응답 오류');return data;
  }finally{clearTimeout(timer);}
}
function startPlan(mode){
  // 출근 / 퇴근에 따라 추천 성향 문구 변경
  const safeBtn = document.getElementById('riskSafe');
  const fastBtn = document.getElementById('riskFast');

  if (safeBtn && fastBtn) {
    if (mode === 'morning') {
      safeBtn.querySelector('b').textContent = '여유로운 준비';
      safeBtn.querySelector('small').textContent =
        '조금 더 여유롭게 준비하고 출발하세요. 다만 좌석 혼잡도가 비교적 높을 수 있습니다.';

      fastBtn.querySelector('b').textContent = '여유로운 도착';
      fastBtn.querySelector('small').textContent =
        '조금 일찍 출발해 목표 시간보다 여유 있게 도착합니다.';
    } else {
      safeBtn.querySelector('b').textContent = '빠른 귀가';
      safeBtn.querySelector('small').textContent =
        '가능한 빠르게 귀가할 수 있는 경로를 추천합니다.';

      fastBtn.querySelector('b').textContent = '여유로운 귀가';
      fastBtn.querySelector('small').textContent =
        '조금 더 여유롭게 이동하며 혼잡도와 좌석 상황을 고려합니다.';
    }
  }

  plan={mode,routeFamily:'all',selected:new Set(),ranges:{},destination:null,riskMode:'safe',departureAny:false,deadlineAny:false};
  $('#departureTime').value=mode==='morning'?'07:00':'17:30';$('#deadlineTime').value=mode==='morning'?'08:50':'19:00';
  $('#timePageTitle').textContent=mode==='morning'?'출근 경로 설정':'퇴근 경로 설정';
  for(const type of ['departure','deadline']){$('#'+type+'Time').disabled=false;$('#'+type+'AnyBtn').classList.remove('active');}
  setRoute('all');setRisk('safe');showPage('time');
}
function setRoute(route){
  plan.routeFamily=route;
  ['all','5001','5003'].forEach(r=>$('#route'+(r==='all'?'All':r)).classList.toggle('active',r===route));
  $('#routeAllSub').textContent=plan.mode==='morning'?'5001A + 5003A':'5001B + 5003B';
  for(const r of ['5001','5003'])$('#route'+r+'Sub').textContent=r+(plan.mode==='morning'?'A':'B');
}
function toggleAny(type){plan[type+'Any']=!plan[type+'Any'];$('#'+type+'Time').disabled=plan[type+'Any'];$('#'+type+'AnyBtn').classList.toggle('active',plan[type+'Any']);}
function goStations(){
  if(!Object.keys(catalog).length){alert('서버 연결을 먼저 확인해주세요.');return;}
  plan.date=$('#planDate').value;plan.departureTime=$('#departureTime').value;plan.deadlineTime=$('#deadlineTime').value;
  if(!plan.date||plan.date<kstToday()){alert('오늘 이후의 날짜를 선택해주세요.');return;}
  if(!plan.departureAny&&!plan.deadlineAny&&plan.deadlineTime<=plan.departureTime){alert('도착 마감은 출발 가능시각보다 늦어야 합니다.');return;}
  plan.selected.clear();plan.ranges={};plan.destination=null;renderStations();renderDestinations();
  $('#summaryDirection').textContent=plan.mode==='morning'?'용인 → 서울':'서울 → 용인';
  $('#summaryRoute').textContent=routes().join(' · ');$('#summaryDeparture').textContent=plan.departureAny?'05:00부터 탐색':plan.departureTime;
  $('#summaryDeadline').textContent=plan.deadlineAny?'상관없음':plan.deadlineTime;
  showPage('stations');loadWeather(`${plan.date}T${plan.departureAny?'05:00':plan.departureTime}`);
}
function renderStations(){
  $('#stationLine').innerHTML=routes().map(r=>`<div class="route-group-final"><h4>${esc(r)}</h4>${catalog[r].boarding.map(s=>`<button type="button" class="station-item ${plan.selected.has(r+':'+s.id)?'selected':''}" data-route="${esc(r)}" data-station="${esc(s.id)}"><span class="station-node"></span><span class="station-name">${esc(s.name)}</span><span class="station-check">${plan.selected.has(r+':'+s.id)?'후보':'선택'}</span></button>`).join('')}<p class="range-guide-final">${plan.ranges[r]?.end?'선택 완료 · 다시 클릭하면 새 범위를 시작합니다.':'시작·끝 정류장을 클릭하세요.'}</p></div>`).join('');
  $('#stationCount').textContent=plan.selected.size;
}
function toggleStation(route,id){
  const list=catalog[route].boarding, state=plan.ranges[route];
  [...plan.selected].filter(k=>k.startsWith(route+':')).forEach(k=>plan.selected.delete(k));
  if(!state||state.end){plan.ranges[route]={start:id};plan.selected.add(route+':'+id);}
  else{const a=list.findIndex(s=>s.id===state.start),b=list.findIndex(s=>s.id===id);for(let i=Math.min(a,b);i<=Math.max(a,b);i++)plan.selected.add(route+':'+list[i].id);plan.ranges[route].end=id;}
  renderStations();
}
function clearStations(){plan.selected.clear();plan.ranges={};renderStations();}
function renderDestinations(){
  const unique=new Map();routes().forEach(r=>catalog[r].destinations.forEach(s=>unique.set(s.name,s)));
  $('#destinationList').innerHTML=[...unique.values()].map(s=>`<button class="destination-btn ${plan.destination===s.name?'active':''}" data-destination="${esc(s.name)}"><b>${esc(s.name)}</b></button>`).join('');
}
function setRisk(mode){plan.riskMode=mode;$('#riskSafe').classList.toggle('active',mode==='safe');$('#riskFast').classList.toggle('active',mode==='fast');}
async function loadWeather(target){
  const version=++weatherVersion;
  try{
    const data=await api('/api/weather'+(target?'?datetime='+encodeURIComponent(target):''));
    if(version!==weatherVersion)return;
    for(const region of ['giheung','gangnam']){
      const w=data.regions[region], box=$('#weather-'+region);
      box.innerHTML=`<div class="weather-head"><strong>${esc(w.label)} 날씨</strong><span class="weather-status">시간별 예보</span></div>${w.available?`<div class="weather-main"><span class="weather-emoji">${w.condition==='비'?'🌧️':w.condition==='눈'?'🌨️':w.condition==='맑음'?'☀️':'☁️'}</span><b class="weather-temp">${Math.round(w.temperature)}°</b></div><p>${esc(w.condition)} · 강수 ${esc(w.rainfall_mm_per_hour)} mm/h</p><p class="hint">${esc(w.target_time.replace('T',' '))} · 대표 지점</p><small>${esc(w.source)}</small>`:`<p class="hint">${esc(w.message)}</p>`}`;
    }
    const delay=$('#weatherDelayNotice');delay.hidden=!(data.delay&&data.delay.weather_delay_percent>0);
    delay.textContent=data.delay?`기흥구 예보 기준 출근 06~08시 대상 구간의 지연 가중치: +${data.delay.weather_delay_percent.toFixed(1)}%. 구간 전체 적용은 근사입니다.`:'';
  }catch(e){if(version===weatherVersion)for(const r of ['giheung','gangnam'])$('#weather-'+r).innerHTML=`<b>${r==='giheung'?'기흥구':'강남구'} 날씨</b><p>연결 실패 · 다시 조회해주세요.</p>`;}
}
async function runRecommendation(){
  if(busy)return;
  if(!plan.selected.size||!plan.destination){alert('승차 범위와 도착 정류장을 선택해주세요.');return;}
  const selected=[];routes().forEach(route=>catalog[route].boarding.forEach(s=>{if(plan.selected.has(route+':'+s.id))selected.push({id:s.id,route_name:route});}));
  const payload={date:plan.date,commute_mode:plan.mode,route_family:plan.routeFamily==='all'?null:plan.routeFamily,
    selected_boarding_stations:selected,destination:plan.destination,departure_time:plan.departureAny?null:plan.departureTime,
    arrival_time:plan.deadlineAny?null:plan.deadlineTime,risk_mode:plan.riskMode};
  busy=true;showPage('result');$('#resultContent').innerHTML='<div class="result-hero"><h3>기존 기록과 선택한 시간대의 예보를 비교하고 있습니다.</h3></div>';
  try{const data=await api('/api/recommend-final',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});renderRecommendation(data);}
  catch(e){$('#resultContent').innerHTML=`<div class="result-hero"><h3>추천 정보를 불러오지 못했습니다.</h3><p>${esc(e.message)}</p><p>데모 결과로 대체하지 않습니다.</p><button class="tiny-btn" onclick="runRecommendation()">다시 시도</button></div>`;}
  finally{busy=false;}
}
let lastResponse=null;
function renderRecommendation(data){
  lastResponse=data;results=data.candidates||[];focusedId=results[0]?.id||null;renderFocused();
}
function candidateLabel(c){return c.rank===1?'BEST':`${c.rank}위`;}
function renderFocused(){
  const data=lastResponse||{}, c=results.find(r=>r.id===focusedId);

  if(!c){
    $('#resultContent').innerHTML=`
      <div class="result-hero">
        <h3>${esc(data.message||'추천 결과 없음')}</h3>
        <p>${data.status==='insufficient_data'
          ?'현재 조건에서는 추천을 확정하기 위한 데이터가 부족합니다.'
          :'출발 가능시각을 앞당기거나 도착 마감을 늦춰 다시 검색해주세요.'}</p>
      </div>
      <details class="result-card gta-details">
        <summary>데이터 분석 근거 보기</summary>
        ${evidenceCards(data)}
        ${sectionEvidence(data)}
        ${coverage(data)}
      </details>`;
    return;
  }

  const seatRaw=Number(c.remain_seats);

  let seatText='예측 데이터 부족';

  if(Number.isFinite(seatRaw) && seatRaw>0){
    seatText=`약 ${Math.round(seatRaw)}석`;
  }else if(Number.isFinite(seatRaw) && seatRaw<=0){
    seatText='좌석 여유 낮음';
  }

  const congestionRaw=Number(c.historical_congestion?.value);
  let congestionText='자료 부족';

  if(Number.isFinite(congestionRaw)){
    if(congestionRaw>=80) congestionText=`높음 · ${Math.round(congestionRaw)}`;
    else if(congestionRaw>=50) congestionText=`보통 · ${Math.round(congestionRaw)}`;
    else congestionText=`낮음 · ${Math.round(congestionRaw)}`;
  }

  const readyTime=c.station_ready_time||c.departure_time||'시간 확인 필요';
  const destination=c.destination||'목적지';
  const boardingStation=c.boarding_station||'탑승 정류장';

  const detailMetrics=[
    ['정류장 도착 권장',readyTime],
    ['예상 승차',c.departure_time||'자료 부족'],
    ['예상 도착',c.estimated_arrival_time||'자료 부족'],
    ['마감 여유',plan.deadlineAny?'-':(c.margin_minutes!=null?`${c.margin_minutes}분`:'자료 부족')],
    [
      c.full_rate_known===false?'예상 대기':'예상 대기·만석 지연',
      c.arrival_seconds!=null?`${(c.arrival_seconds/60).toFixed(1)}분`:'자료 부족'
    ],
    ['과거 좌석',seatText],
    [
      '과거 추정 배차',
      Number.isFinite(Number(c.headway_minutes))
        ? `${Number(c.headway_minutes).toFixed(1)}분`
        : '자료 부족'
    ],
    [
      '예상 이동시간',
      c.travel_time_minutes!=null?`${c.travel_time_minutes}분`:'자료 부족'
    ],
    ['과거 혼잡도',congestionText]
  ];

  const alternatives=results
    .filter(r=>r.id!==focusedId)
    .sort((a,b)=>a.rank-b.rank)
    .map(r=>`
      <button
        type="button"
        class="gta-alt-card candidate-switch"
        data-candidate-id="${esc(r.id)}"
        aria-label="${candidateLabel(r)} ${esc(r.route)} 상세 보기"
      >
        <div class="gta-alt-top">
          <span>${candidateLabel(r)}</span>
          <b>${esc(r.stability_grade||'')}</b>
        </div>

        <h4>${esc(r.route)} · ${esc(r.boarding_station)}</h4>

        <div class="gta-alt-time">
          ${esc(r.departure_time||'--:--')}
          <span>→</span>
          ${esc(r.estimated_arrival_time||'--:--')}
        </div>

        <p>
          ${r.margin_minutes!=null
            ? `도착 마감 ${esc(r.margin_minutes)}분 여유`
            : '도착 여유 정보 부족'}
        </p>
      </button>
    `).join('');

  $('#resultContent').innerHTML=`
    <section id="focused-candidate" class="gta-best-card" tabindex="-1">

      <div class="gta-best-head">
        <div>
          <span class="gta-best-label">GTA BEST</span>
          <span class="stability-badge-final">${esc(c.stability_grade||'추천')}</span>
        </div>
        <span class="gta-route-badge">${esc(c.route)}</span>
      </div>

      <div class="gta-action">
        <span class="gta-action-time">${esc(readyTime)}</span>
        <div>
          <strong>까지 ${esc(boardingStation)}에 도착하세요</strong>
          <p>목표 시간 내 도착을 위한 추천 출발 계획입니다.</p>
        </div>
      </div>

      <div class="gta-timeline">
        <div class="gta-timeline-point">
          <span>정류장 도착</span>
          <b>${esc(readyTime)}</b>
          <small>${esc(boardingStation)}</small>
        </div>

        <div class="gta-timeline-line">
          <span>${esc(c.route)}</span>
        </div>

        <div class="gta-timeline-point">
          <span>버스 탑승</span>
          <b>${esc(c.departure_time||'--:--')}</b>
          <small>${esc(c.route)}</small>
        </div>

        <div class="gta-timeline-line">
          <span>${c.travel_time_minutes!=null?`${esc(c.travel_time_minutes)}분`:'이동'}</span>
        </div>

        <div class="gta-timeline-point">
          <span>예상 도착</span>
          <b>${esc(c.estimated_arrival_time||'--:--')}</b>
          <small>${esc(destination)}</small>
        </div>
      </div>

      <div class="gta-key-metrics">
        <div>
          <span>도착 여유</span>
          <b>${plan.deadlineAny?'-':(c.margin_minutes!=null?`${esc(c.margin_minutes)}분`:'자료 부족')}</b>
        </div>

        <div>
          <span>예상 좌석</span>
          <b>${esc(seatText)}</b>
        </div>

        <div>
          <span>혼잡 수준</span>
          <b>${esc(congestionText)}</b>
        </div>

        <div>
          <span>예상 소요</span>
          <b>${c.travel_time_minutes!=null?`${esc(c.travel_time_minutes)}분`:'자료 부족'}</b>
        </div>
      </div>

      <div class="gta-source-note">
        ${esc(c.source||'실시간 및 과거 데이터를 함께 반영한 추천')}
      </div>
    </section>

    ${alternatives?`
      <section class="gta-other-section">
        <div class="gta-section-head">
          <div>
            <span>ALTERNATIVES</span>
            <h3>다른 선택지</h3>
          </div>
          <small>카드를 누르면 BEST 영역에서 자세히 비교할 수 있습니다.</small>
        </div>

        <div class="gta-alternatives">
          ${alternatives}
        </div>
      </section>
    `:''}

    <details class="gta-analysis-details">
      <summary>
        <span>
          <b>추천 근거 자세히 보기</b>
          <small>배차 · 좌석 · 혼잡도 · 이동시간 · ML 분석</small>
        </span>
        <span class="gta-detail-arrow">⌄</span>
      </summary>

      <div class="gta-analysis-body">

        <div class="result-card">
          <h4>${candidateLabel(c)} 상세 데이터</h4>

          <div class="metric-grid">
            ${detailMetrics.map(([label,value])=>`
              <div class="metric">
                <span>${label}</span>
                <b>${esc(value)}</b>
              </div>
            `).join('')}
          </div>

          <div class="segment-note">
            <b>구간별 이동시간</b>
            <p>
              대상 구간 전 ${c.segment_minutes?.local_before??'자료 부족'}분 ·
              기흥역→신논현역 범위 ${c.segment_minutes?.giheung_sinnonhyeon??'자료 부족'}분 ·
              대상 구간 후 ${c.segment_minutes?.local_after??'자료 부족'}분
            </p>
          </div>

          ${c.reasons?.length?`
            <ul>
              ${c.reasons.map(r=>`<li>${esc(r)}</li>`).join('')}
            </ul>
          `:''}
        </div>

        ${data.model_note?`
          <p class="data-note-final">${esc(data.model_note)}</p>
        `:''}

        ${evidenceCards(data)}
        ${sectionEvidence(data)}
        ${coverage(data)}
      </div>
    </details>
  `;
}
function evidenceCards(data){
  if(!data.station_evidence?.length)return '';
  return `<section class="result-card"><h3>선택한 정류장의 기존 자료</h3><p>출발 가능시각의 시간대별 비교입니다. 전체 소요시간이 없으면 도착 가능 여부와 BEST를 판정하지 않습니다.</p><div class="evidence-grid">${data.station_evidence.map(e=>{const p=e.profile,c=e.congestion;return `<article class="evidence-item"><b>${esc(e.route)} · ${esc(e.station)}</b><p>${e.hour}시 · 혼잡도 ${c?esc(c.value):'자료 없음'}</p><p>배차 간격 ${p?Number(p.headway_minutes).toFixed(1)+'분':'자료 없음'} · 잔여좌석 ${p?Number(p.seat_median??p.seat_mean).toFixed(1)+'석 ('+(p.seat_median==null?'평균':'중앙값')+')':'자료 없음'}</p>${c?`<small>${esc(c.basis)} · ${c.sample_count}건<br>${esc(c.source||'기존 DB')}</small>`:''}${p?`<p><small>${esc(p.basis)} · ${p.headway_samples}건<br>${esc(p.note||p.source)}</small></p>`:''}</article>`;}).join('')}</div></section>`;
}
function sectionEvidence(data){
  const rows=Object.entries(data.local_section_evidence||{}).flatMap(([route,list])=>list.map(s=>({...s,route})));
  if(!rows.length)return '';
  return `<details class="result-card"><summary>서울 시내 일부 구간의 실제 관측 기록</summary><p>선택한 출발 시간대의 구간 평균입니다. 서울–용인 전체 이동시간을 뜻하지 않습니다.</p>${rows.map(s=>`<p>${esc(s.route)} · ${esc(s.section)}: 평균 ${esc(s.mean_minutes)}분 (${s.samples}건)</p>`).join('')}<small>출처: seat_drop_analysis.xlsx / raw_drops · 요일 미분리</small></details>`;
}
function coverage(data){return data.missing_data?.length?`<details class="data-note-final"><summary>일부 후보는 자료 부족으로 비교에서 제외됨 (${data.missing_data.length}개 조건)</summary><ul>${data.missing_data.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details>`:'';}
function focusCandidate(id){if(!results.some(c=>c.id===id))return;focusedId=id;renderFocused();$('#focused-candidate')?.focus({preventScroll:true});$('#focused-candidate')?.scrollIntoView({behavior:'smooth',block:'start'});}
function updateLiveStations(){
  clearInterval(liveTimer);const route=$('#liveRoute').value+$('#liveDirection').value;
  $('#liveStation').innerHTML=(catalog[route]?.boarding||[]).map(s=>`<option value="${esc(s.realtime_id)}">${esc(s.name)}</option>`).join('');
  $('#liveCards').innerHTML='';$('#liveStatus').textContent='정류장을 선택한 뒤 조회해주세요.';updateLiveSelectionPreview();
}
function updateLiveSelectionPreview(){$('#liveSelectionPreview').textContent=$('#liveRoute').value+$('#liveDirection').value+' · '+($('#liveStation').selectedOptions[0]?.textContent||'정류장 선택');}
function renderLive(data,elapsed=0){
  $('#liveCards').innerHTML=data.buses.length?data.buses.map((b,i)=>`<div class="result-card"><h4>${i===0?'첫 번째 차량':'다음 차량'}</h4><p>${data.stale?'수집 당시 ':''}도착예상: ${b.arrival_seconds==null?'정보 없음':Math.max(0,Math.ceil((b.arrival_seconds-(data.stale?0:elapsed))/60))+'분'}</p><p>잔여좌석: ${b.remain_seats==null?'정보 없음':b.remain_seats+'석'}</p></div>`).join(''):'<p>현재 표시할 도착정보가 없습니다.</p>';
}
async function loadLive(){
  clearInterval(liveTimer);const route=$('#liveRoute').value+$('#liveDirection').value,station=$('#liveStation').value;
  $('#liveStatus').textContent='조회 중…';
  try{const d=await api('/api/realtime-final/'+route+'?station_id='+encodeURIComponent(station));$('#liveStatus').textContent=`${d.source} · ${d.collected_at||'기준시각 없음'}${d.headway_minutes!=null?' · 두 차량 간격 '+d.headway_minutes.toFixed(1)+'분':''}`;renderLive(d);const start=Date.now();if(!d.stale)liveTimer=setInterval(()=>renderLive(d,(Date.now()-start)/1000),1000);}
  catch(e){$('#liveStatus').textContent='조회 실패 · '+e.message;$('#liveCards').innerHTML='';}
}
async function init(){
  if(location.protocol==='file:'){$('#dataModeBadge_dhs_gpt_commited').textContent='통합 서버 실행 필요';$('#resultContent').innerHTML='<div class="result-hero"><h3>통합 서버에서 열어주세요.</h3><p>app_integrated_dhs_gpt_commited.py를 실행한 뒤 <a href="http://127.0.0.1:5001">Time Keeper 열기</a></p></div>';showPage('result');return;}
  for(const id of ['departureTime','deadlineTime'])for(let h=5;h<24;h++)for(let m=0;m<60;m+=5){const t=String(h).padStart(2,'0')+':'+String(m).padStart(2,'0');$('#'+id).add(new Option(t,t));}
  $('#planDate').value=kstToday();$('#planDate').min=kstToday();
  $('#stationLine').addEventListener('click',e=>{const b=e.target.closest('[data-station]');if(b)toggleStation(b.dataset.route,b.dataset.station);});
  $('#destinationList').addEventListener('click',e=>{const b=e.target.closest('[data-destination]');if(b){plan.destination=b.dataset.destination;renderDestinations();}});
  $('#resultContent').addEventListener('click',e=>{const b=e.target.closest('[data-candidate-id]');if(b)focusCandidate(b.dataset.candidateId);});
  $('#liveStation').addEventListener('change',()=>{clearInterval(liveTimer);$('#liveCards').innerHTML='';$('#liveStatus').textContent='조회 버튼을 눌러주세요.';updateLiveSelectionPreview();});
  try{catalog=await api('/api/catalog');updateLiveStations();const h=await api('/api/health');const missing=Object.entries(h.databases).filter(([k,v])=>!v.exists).map(([k])=>k);$('#dataModeBadge_dhs_gpt_commited').textContent=missing.length?'서버 연결 · 일부 자료 미연결':'서버 연결 · 기존 자료 사용';const notes=[];const linked=(h.sources||[]).filter(s=>s.available&&s.usage!=='reference');if(linked.length)notes.push('혼잡도·배차 등 기존 XLSX '+linked.length+'개 연결.');if(missing.length)notes.push('연결되지 않은 기존 DB: '+missing.join(', ')+'. 엑셀 자료를 함께 읽으며, 전체 이동시간이 없는 후보는 도착 여부를 판단하지 않습니다.');if(h.holiday_calendar_available===false)notes.push('공휴일 패키지가 없어 토·일만 휴일로 구분합니다. 기존 추가 requirements를 설치해주세요.');}
  catch(e){console.error('초기화 실패:',e);$('#dataModeBadge_dhs_gpt_commited').textContent='서버 연결 실패';}
  loadWeather();
}
if(typeof window!=='undefined')Object.assign(window,{showPage,startPlan,setRoute,toggleAny,goStations,clearStations,setRisk,runRecommendation,updateLiveStations,loadLive,loadWeather,focusCandidate});
if(typeof document!=='undefined')init();
