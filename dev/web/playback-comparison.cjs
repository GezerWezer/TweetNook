const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const vm = require('node:vm');
// Optional real-browser comparison: provide Playwright and CHROMIUM_EXECUTABLE_PATH.
// PLAYBACK_APP_SCRIPT can point to a saved pre-change app.js; no archive is opened.
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
const web = path.join(root,'tweetnook/web');
const out = process.env.PLAYBACK_EVIDENCE_DIR || fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'tweetnook-playback-'));
fs.mkdirSync(out, {recursive:true});
const phase = process.argv[2] || 'after';
const script = process.env.PLAYBACK_APP_SCRIPT || path.join(web,'static/js/app.js');
const defaults = vm.runInNewContext(fs.readFileSync(path.join(web,'static/js/themes.js'),'utf8')+'\n'+fs.readFileSync(script,'utf8')+'\ntweetApp()', {window:{}});
let scenario;
const tweet = (id,media=[])=>({tweet_id:String(id), text: `Playback fixture ${id}`, created_at:'2026-09-05T00:00:00Z', author:{id:'42',username:'fixture',display_name:'Offline fixture'},collections:['like'],media,raw_json:{}});
const media=(id)=>({type:'video',width:720,height:1280,duration_millis:10000,download:{local_path:`fixture/video${id}.mp4`}});
function feed(){if(scenario.popin)return [...Array.from({length:30},(_,i)=>tweet(400+i)),tweet(500,[{...media(1),download:{...media(1).download,thumbnail_local_path:'fixture/image5.jpeg'}}]),...Array.from({length:10},(_,i)=>tweet(600+i))];let ids=scenario.reverse?[3,2,1]:[1,2,3];if(scenario.count===1)ids=[1];return [...ids.flatMap((id,i)=>[tweet(100+i, [media(id)]),tweet(200+i)]),...Array.from({length:9},(_,i)=>tweet(300+i))];}
const server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://localhost'),route=url.pathname;let data;
 if(route==='/api/tweets')data={tweets:feed(),page:1,pages:1,total:feed().length};
 else if(route==='/api/automated-tagging')data={installed:true,values:{...defaults.automatedTagging},accounting:{}};
 else if(route==='/api/setup')data={required:false,completed:true,web:{password_configured:true},auth:{values:{}},archive:{ready:true}};
 else if(route==='/api/config')data={values:{},explicit:{}};
 else if(route==='/api/activity/status')data={active:false};
 else if(route.startsWith('/api/'))data={};
 if(data!==undefined){res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(data));return;}
 let file=route==='/'?path.join(web,'index.html'):route==='/static/js/app.js'?script:route.startsWith('/fixture/')?path.join(root,'demo/demo media',path.basename(route)):path.resolve(web,'.'+route);
 if(fs.existsSync(file)&&fs.statSync(file).isFile()){
  if(scenario.popin&&route.endsWith('.mp4')&&!req.fixtureDelayed){req.fixtureDelayed=true;setTimeout(()=>server.emit('request',req,res),120);return;}
  const stat=fs.statSync(file);const range=req.headers.range;const headers={'Content-Type':{'.html':'text/html','.js':'text/javascript','.css':'text/css','.mp4':'video/mp4','.jpg':'image/jpeg','.jpeg':'image/jpeg'}[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store','Accept-Ranges':'bytes'};
  if(range){const [a,b]=range.replace(/bytes=/,'').split('-');const start=Number(a),end=b?Number(b):stat.size-1;res.writeHead(206,{...headers,'Content-Length':end-start+1,'Content-Range':`bytes ${start}-${end}/${stat.size}`});fs.createReadStream(file,{start,end}).pipe(res);}
  else{res.writeHead(200,{...headers,'Content-Length':stat.size});fs.createReadStream(file).pipe(res);}return;
 }
 res.writeHead(404).end();
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;const results=[];
 try{
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH || undefined,args:['--autoplay-policy=no-user-gesture-required']});
 for(const device of process.env.PLAYBACK_PREPARATION_ONLY?[]:['desktop','mobile'])for(const count of [1,3])for(const reverse of count===1?[false]:[false,true])for(const speed of ['slow','fast']){
  scenario={device,count,reverse,speed};
  const context=await browser.newContext({viewport:device==='desktop'?{width:1440,height:1000}:{width:390,height:844},isMobile:device==='mobile',hasTouch:device==='mobile',serviceWorkers:'block'});
  const origin=`http://127.0.0.1:${server.address().port}`;
  await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(origin);await page.locator('.feed-card video').first().waitFor();
  await page.evaluate(()=>{window.fixtureApp=Alpine.$data(document.querySelector('[x-data="tweetApp()"]'));fixtureApp.videoMuted=false;document.querySelectorAll('video').forEach(v=>fixtureApp.applyVideoAudio(v));});
  const first=page.locator('.feed-card video').first();
  // Put the first video fully below the fixed header, preserving actual feed dimensions.
  await first.evaluate(v=>window.scrollBy(0,v.getBoundingClientRect().top-130));
  await page.waitForFunction(()=>{const v=document.querySelector('.feed-card video');return !v.paused&&v.readyState>=2&&v.currentTime>.05;},{},{timeout:10000});
  const initial=await first.evaluate(v=>({time:v.currentTime,muted:v.muted,ready:v.readyState,height:v.getBoundingClientRect().height}));assert.equal(initial.muted,false);
  // Move to 45% unobscured visibility. The old observer sees more than 50%
  // because it includes pixels underneath the sticky header.
  const paused=await page.evaluate(async speed=>{
   const video=document.querySelector('.feed-card video');const r=video.getBoundingClientRect();
   const header=[...document.querySelectorAll('[data-video-occluder]')].filter(h=>h.getBoundingClientRect().width)[0];
   const target=window.scrollY+r.top-(header.getBoundingClientRect().bottom-r.height*.55);
   const start=performance.now(),from=scrollY,duration=speed==='slow'?400:50;
   while(performance.now()-start<duration){const p=(performance.now()-start)/duration;window.scrollTo(0,from+(target-from)*p);await new Promise(requestAnimationFrame);}
   window.scrollTo(0,target);const crossed=performance.now();
   while(!video.paused&&performance.now()-crossed<700)await new Promise(requestAnimationFrame);
   const rect=video.getBoundingClientRect(),insets=videoFeedInsets(rect);
   return {pauseDelayMs:Math.round(performance.now()-crossed),timedOut:!video.paused,visibleRatio:Math.round((rect.height-insets.top-insets.bottom)/rect.height*100)/100};
  },speed);
  // Scan the same feed from top to bottom and back, sampling actual media state.
  const samples=await page.evaluate(async speed=>{
   const records=[];const collect=()=>{
    const players=[...document.querySelectorAll('.feed-card video')];
    records.push({t:performance.now(),y:scrollY,players:players.map((v,i)=>{const r=v.getBoundingClientRect(),clip=videoFeedInsets(r);return {i,playing:!v.paused,audible:!v.paused&&!v.muted&&v.volume>0,ratio:r.height?Math.max(0,(r.height-clip.top-clip.bottom)/r.height):0,time:v.currentTime,ready:v.readyState};})});
   };
   const max=Math.min(document.documentElement.scrollHeight-innerHeight,document.querySelectorAll('.feed-card video').length*900);
   const scroll=async(from,to)=>{const start=performance.now(),duration=speed==='slow'?2600:600;while(performance.now()-start<duration){window.scrollTo(0,from+(to-from)*Math.min(1,(performance.now()-start)/duration));collect();await new Promise(requestAnimationFrame);}window.scrollTo(0,to);};
   window.scrollTo(0,0);await new Promise(r=>setTimeout(r,150));await scroll(0,max);await scroll(max,0);collect();return records;
  },speed);
  let overlapMs=0,staleAudioMs=0,playingFrames=0,eligibleGapMs=0,maxEligibleGapMs=0;const starts=[];let previous=null;
  for(let i=1;i<samples.length;i++){const s=samples[i],dt=Math.min(100,s.t-samples[i-1].t),active=s.players.filter(p=>p.playing);if(active.length)playingFrames++;if(active.length>1)overlapMs+=dt;if(!active.length&&s.players.some(p=>p.ratio>=.6)){eligibleGapMs+=dt;maxEligibleGapMs=Math.max(maxEligibleGapMs,eligibleGapMs);}else eligibleGapMs=0;if(s.players.some(p=>p.audible&&p.ratio<.6))staleAudioMs+=dt;const owner=active[0]?.i??null;if(owner!==null&&owner!==previous)starts.push(owner);previous=owner;}
  const result={...scenario,initial,...paused,staleAudioMs:Math.round(staleAudioMs),overlapMs:Math.round(overlapMs),maxEligibleGapMs:Math.round(maxEligibleGapMs),playingFrames,starts,errors};results.push(result);console.log(JSON.stringify(result));
  if(!reverse&&speed==='slow')await page.screenshot({path:`${out}/playback-${phase}-${device}-${count}.png`});
  assert.deepEqual(errors,[]);
  if(phase==='after'){assert.equal(paused.timedOut,false);assert.ok(paused.pauseDelayMs<=50);assert.equal(overlapMs,0);assert.ok(staleAudioMs<160);}
  await context.close();
 }
 const preparation=[];
 for(const device of ['desktop','mobile']){
  scenario={device,popin:true};
  const context=await browser.newContext({viewport:device==='desktop'?{width:1440,height:1000}:{width:390,height:844},isMobile:device==='mobile',hasTouch:device==='mobile',serviceWorkers:'block'});
  const origin=`http://127.0.0.1:${server.address().port}`;
  await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  await context.addInitScript(()=>{
   const Native=IntersectionObserver;window.fixtureObservers=[];
   window.IntersectionObserver=class extends Native{
    constructor(...args){super(...args);this.targets=new Set();fixtureObservers.push(this);}
    observe(target){this.targets.add(target);super.observe(target);}
    unobserve(target){this.targets.delete(target);super.unobserve(target);}
   };
  });
  const page=await context.newPage();await page.goto(origin);await page.locator('.feed-card video').waitFor({state:'attached'});
  await page.evaluate(()=>{
   for(const card of document.querySelectorAll('.feed-card'))if(!card.querySelector('video'))card.style.contentVisibility='visible';
  });
  await page.waitForTimeout(150);
  for(let attempt=0;attempt<3;attempt++){
   await page.evaluate(()=>{
    const card=document.querySelector('.feed-card video').closest('.feed-card');
    window.scrollBy(0,card.getBoundingClientRect().top-(innerHeight+1100));
   });
   await page.waitForTimeout(150);
  }
  await page.waitForTimeout(450);
  const beforeEntry=await page.evaluate(()=>{
   const video=document.querySelector('.feed-card video'),card=video.closest('.feed-card');
   return {distanceBelowViewport:Math.round(card.getBoundingClientRect().top-innerHeight),preload:video.preload,readyState:video.readyState,paintPrepared:video.checkVisibility({contentVisibilityAuto:true}),contentVisibility:getComputedStyle(card).contentVisibility};
  });
  const firstFrameMs=await page.evaluate(async()=>{
   const video=document.querySelector('.feed-card video'),start=performance.now();
   const frame=new Promise(resolve=>video.requestVideoFrameCallback(()=>resolve(Math.round(performance.now()-start))));
   window.scrollBy(0,video.getBoundingClientRect().top-130);return Promise.race([frame,new Promise(resolve=>setTimeout(()=>resolve(2000),2000))]);
  });
  await page.screenshot({path:`${out}/media-entry-${phase}-${device}.png`});
  let viewerPlayback=null;
  if(phase==='after'&&device==='mobile'){
   await page.locator('.feed-card .media-video-player').click({position:{x:100,y:100}});
   await page.waitForFunction(()=>document.querySelector('.lightbox-stage video')?.paused===false);
   await page.evaluate(()=>{window.dispatchEvent(new Event('resize'));document.dispatchEvent(new Event('scroll'));});
   await page.waitForTimeout(250);
   viewerPlayback=await page.evaluate(()=>{
    const app=Alpine.$data(document.querySelector('[x-data="tweetApp()"]')),video=document.querySelector('.lightbox-stage video');
    return {open:app.lightboxOpen,playing:!!video&&!video.paused,currentTime:video?.currentTime};
   });
   assert.equal(viewerPlayback.open,true);assert.equal(viewerPlayback.playing,true);
   await page.screenshot({path:`${out}/playback-mobile-viewer-after.png`});
   await page.evaluate(()=>Alpine.$data(document.querySelector('[x-data="tweetApp()"]')).closeLightbox());
   await page.waitForFunction(()=>!Alpine.$data(document.querySelector('[x-data="tweetApp()"]')).lightboxOpen);
   await page.waitForFunction(()=>document.querySelector('.feed-card video')?.paused===false);
  }
  const retainedTargets=await page.evaluate(async()=>{
   const app=Alpine.$data(document.querySelector('[x-data="tweetApp()"]'));app.renderFeedList([]);
   await new Promise(requestAnimationFrame);
   return fixtureObservers.reduce((count,observer)=>count+[...observer.targets].filter(target=>!target.isConnected).length,0);
  });
  const result={device,...beforeEntry,firstFrameMs,retainedTargets,viewerPlayback};preparation.push(result);console.log('media preparation',JSON.stringify(result));
  if(phase==='after'){assert.equal(beforeEntry.contentVisibility,'visible');assert.ok(beforeEntry.readyState>=2);assert.equal(beforeEntry.paintPrepared,true);assert.equal(retainedTargets,0);assert.ok(firstFrameMs<2000);}
  await context.close();
 }
 fs.writeFileSync(`${out}/playback-${phase}${process.env.PLAYBACK_PREPARATION_ONLY?'-preparation':''}.json`,JSON.stringify({phase,browser:browser.version(),measurement:'HTMLMediaElement playback/audio state and real decoded MP4 frames; no acoustic loopback',preparation,results},null,2));
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
