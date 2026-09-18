// Node VM tests exercise production card rendering and the actual delegated click handler.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',min:'',disabled:false,hidden:false,selectedOptions:[],dataset:{},options:[],classList:{toggle(){},remove(){}},add(o){this.options.push(o)},listeners:{},addEventListener(k,fn){this.listeners[k]=fn},focus(){this.focused=true},scrollIntoView(){this.scrolled=true}});return elements.get(id);}
const catalog={'5003A':{boarding:[{id:'1',name:'동백',realtime_id:'11'},{id:'2',name:'기흥',realtime_id:'12'}],destinations:[{id:'3',name:'신논현역'}]}};
const context={console,document:{querySelector:element,querySelectorAll:()=>[]},window:{scrollTo(){}},location:{protocol:'http:'},Option:function(t,v){this.text=t;this.value=v},AbortController,setTimeout,clearTimeout,setInterval,clearInterval,Date,Intl,alert:m=>{throw Error(m)},fetch:async path=>({ok:true,json:async()=>path==='/api/catalog'?catalog:path==='/api/health'?{databases:{}}:{regions:{giheung:{label:'기흥구',available:false},gangnam:{label:'강남구',available:false}}}})};
vm.createContext(context);vm.runInContext(fs.readFileSync('integrated_dhs_gpt_commited.js','utf8'),context);
const run=s=>vm.runInContext(s,context);
const candidates=[1,2,3,4].map(rank=>({id:'c'+rank,rank,route:'5003A',boarding_station:'정류장'+rank,destination:'신논현',departure_time:'07:20',estimated_arrival_time:'08:20',station_ready_time:'07:10',arrival_seconds:600,remain_seats:15,headway_minutes:12,travel_time_minutes:60,margin_minutes:30,stability_grade:'보통',source:'기존 기록',segment_minutes:{local_before:10,giheung_sinnonhyeon:50,local_after:0},reasons:['선택 '+rank]}));
context.fixture={candidates,message:'ok',missing_data:[],model_note:'과거 기록 기반'};
setTimeout(()=>{
run('renderRecommendation(fixture)');assert.match(element('#resultContent').innerHTML,/result-label">BEST/);
const click=id=>element('#resultContent').listeners.click({target:{closest:()=>({dataset:{candidateId:id}})}});
click('c2');let html=element('#resultContent').innerHTML;
assert.match(html,/result-label">2위/);assert.match(html,/선택 2/);
assert.deepEqual([...html.matchAll(/data-candidate-id="(.*?)"/g)].map(x=>x[1]),['c1','c3','c4']);
click('c4');html=element('#resultContent').innerHTML;assert.match(html,/result-label">4위/);
assert.deepEqual([...html.matchAll(/data-candidate-id="(.*?)"/g)].map(x=>x[1]),['c1','c2','c3']);
click('c1');assert.match(element('#resultContent').innerHTML,/result-label">BEST/);
assert.equal(run('results[0].rank'),1);assert.equal(run('results[1].rank'),2);
run("startPlan('morning');toggleStation('5003A','1');toggleStation('5003A','2')");assert.equal(run('plan.selected.size'),2);
run("renderRecommendation({status:'insufficient_data',message:'자료 부족',missing_data:['기록 없음']})");assert.match(element('#resultContent').innerHTML,/자료 부족/);assert.doesNotMatch(element('#resultContent').innerHTML,/result-label">BEST/);
context.evidenceFixture={status:'insufficient_data',message:'자료 부족',station_evidence:[{route:'5003A',station:'기흥역',hour:7,congestion:{value:86.7,sample_count:11,basis:'날짜별',source:'실제 엑셀'},profile:{headway_minutes:10.25,seat_median:null,seat_mean:5.6,headway_samples:179,basis:'시간대별',note:'원본 집계'}}]};
run('renderRecommendation(evidenceFixture)');html=element('#resultContent').innerHTML;assert.match(html,/86.7/);assert.match(html,/10.3분/);assert.match(html,/5.6석 \(평균\)/);assert.doesNotMatch(html,/result-label">BEST/);
console.log('PASS: delegated card clicks 2/4/BEST, original rank order, details, station range, insufficient-data state');
},0);
