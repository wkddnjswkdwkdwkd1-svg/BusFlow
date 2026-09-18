// Real browser + local production server. Weather/bus API calls are disabled.
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const assert=require('node:assert/strict');
(async()=>{
 const server=spawn('python',['app_integrated_dhs_gpt_commited.py'],{env:{...process.env,BUSFLOW_OFFLINE:'1',PORT:'5001'},stdio:['ignore','pipe','inherit']});
 let browser;
 try{
  await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(Error('server startup timeout')),10000);server.stdout.once('data',()=>{clearTimeout(timeout);resolve()});server.once('exit',()=>reject(Error('server exited')))});
  browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:5001');
  await page.waitForFunction(()=>document.querySelector('#dataModeBadge_dhs_gpt_commited').textContent.includes('서버 연결'));
  await page.evaluate(()=>startPlan('morning'));
  const future=new Date(Date.now()+7*86400000);
  await page.locator('#planDate').fill(new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(future));
  await page.evaluate(()=>{goStations();const r='5003A',s=catalog[r].boarding.at(-1);toggleStation(r,s.id);plan.destination='신논현역';renderDestinations();});
  await page.evaluate(()=>runRecommendation());
  assert.match(await page.locator('#resultContent').innerText(),/선택한 정류장의 기존 자료/);
  assert.match(await page.locator('#resultContent').innerText(),/배차 간격 10.2분/);
  assert.equal(await page.locator('#resultContent .result-label').count(),0);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  await page.setViewportSize({width:1280,height:900});
  // Focus interaction fixture checks display only, never substitutes for real data.
  await page.evaluate(()=>renderRecommendation({candidates:[1,2,3].map(rank=>({id:'test-'+rank,rank,route:'5003A',boarding_station:'상호작용 검증',destination:'신논현',departure_time:'07:20',estimated_arrival_time:'08:20',arrival_seconds:600,remain_seats:15,headway_minutes:12,travel_time_minutes:60,margin_minutes:30,stability_grade:'주의',segment_minutes:{local_before:10,giheung_sinnonhyeon:50,local_after:0},reasons:[]}))}));
  await page.locator('[data-candidate-id="test-2"]').click();assert.equal(await page.locator('#resultContent .result-label').innerText(),'2위');
  assert.deepEqual(await page.locator('.candidate-switch .alt-label').allTextContents(),['BEST','3위']);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  assert.deepEqual(errors,[]);console.log('PASS: actual XLSX data, missing-travel state, real card clicks and mobile overflow');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1});
