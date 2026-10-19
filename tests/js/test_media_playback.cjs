'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = ['themes.js', 'app.js'].map(file => fs.readFileSync(
    path.resolve(__dirname, '../../tweetnook/web/static/js', file), 'utf8')).join('\n');

function fixture() {
    const videos = [], headers = [];
    const document = {
        hidden: false, body: {},
        querySelectorAll: selector => selector === '[data-video-occluder]' ? headers : videos,
    };
    const window = { innerWidth: 400, innerHeight: 800 };
    const context = { window, document, console, setTimeout, clearTimeout,
        localStorage: {setItem() {}},
        getComputedStyle: node => node.style || {},
    };
    const { tweetApp, feedVideoVisibility } = vm.runInNewContext(source + '\n({tweetApp, feedVideoVisibility})', context);
    const app = tweetApp();
    app.videoMuted = false;
    app.videoVolume = 1;
    const player = (top, height = 400) => {
        const video = {tagName: 'VIDEO', top, height, isConnected: true, paused: true,
            muted: false, volume: 1, playCalls: 0, pauseCalls: 0,
            hasAttribute: name => name === 'controls', closest: () => null,
            getBoundingClientRect: () => ({top:video.top,left:0,width:400,height:video.height}),
            play() {this.playCalls++; this.paused = false; app.handleVideoPlay(this); return Promise.resolve();},
            pause() {if(this.paused) return;this.pauseCalls++;this.paused=true;app.handleVideoPause(this);},
        };
        videos.push(video);
        return video;
    };
    return {app, player, window, document, headers, visibility: feedVideoVisibility};
}
const tests = [];
function test(name, run) { tests.push([name, run]); }
const settle = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };

test('sticky headers stop audio while the raw viewport still contains most of the player', async () => {
    const {app,player,headers,visibility}=fixture();
    headers.push({getBoundingClientRect:()=>({top:0,left:0,width:400,height:100})});
    const video=player(120);
    app.updateAutoplayVisibility(video,true);await settle();assert.equal(video.paused,false);
    video.top=-120;assert.equal(visibility(video).ratio,.45);
    app.autoplayNextVideo();assert.equal(video.paused,true);
    video.top=120;app.autoplayNextVideo();await settle();assert.equal(video.paused,false,'automatic pauses do not leave a manual hold');
});
test('a tall portrait video can autoplay while filling a short viewport', async () => {
    const {app,player,visibility}=fixture();const video=player(0,1600);
    assert.equal(visibility(video).ratio,1);app.updateAutoplayVisibility(video,true);await settle();assert.equal(video.paused,false);
});
test('a nested scroll panel clips candidates outside its visible region', async () => {
    const {app,player,visibility}=fixture();const video=player(300);
    video.parentElement={style:{overflowY:'auto'},getBoundingClientRect:()=>({top:200,left:0,right:400,bottom:400,width:400,height:200})};
    assert.equal(visibility(video).ratio,.25);app.updateAutoplayVisibility(video,true);await settle();assert.equal(video.playCalls,0);
});
test('observer batches choose the same topmost player in either feed insertion order', async () => {
    for(const reverse of [false,true]){
        const {app,player}=fixture(),upper=player(80),lower=player(400);
        for(const video of reverse?[lower,upper]:[upper,lower])app.updateAutoplayVisibility(video,true,false);
        app.autoplayNextVideo();await settle();assert.equal(upper.paused,false);assert.equal(lower.playCalls,0);
    }
});
test('handoffs pause the old audio before calling play on its replacement', async () => {
    const {app,player}=fixture(),upper=player(80),lower=player(900);
    app.updateAutoplayVisibility(upper,true,false);app.updateAutoplayVisibility(lower,true);await settle();
    upper.top=-300;lower.top=150;let previousPaused;
    const play=lower.play;lower.play=function(){previousPaused=upper.paused;return play.call(this);};
    app.autoplayNextVideo();await settle();assert.equal(previousPaused,true);assert.equal(lower.paused,false);
});
test('manual pause survives scrolling within the viewport and clears only after leaving', async () => {
    const {app,player}=fixture(),video=player(120);
    app.updateAutoplayVisibility(video,true);await settle();video.pause();video.top=140;
    app.autoplayNextVideo();assert.equal(video.playCalls,1);
    video.top=-500;app.autoplayNextVideo();video.top=120;app.autoplayNextVideo();await settle();assert.equal(video.playCalls,2);
});
test('hidden tabs and blocking overlays stop audio and resume without a manual hold', async () => {
    for(const key of ['hidden','showSettingsModal','showStatsModal','showSetupModal']){
        const {app,player,document}=fixture(),video=player(120);
        app.updateAutoplayVisibility(video,true);await settle();const target=key==='hidden'?document:app;
        target[key]=true;app.autoplayNextVideo();assert.equal(video.paused,true);
        target[key]=false;app.autoplayNextVideo();await settle();assert.equal(video.paused,false);
    }
});
test('a superseded decoder cannot restart offscreen audio or interrupt the new owner', async () => {
    const {app,player}=fixture(),old=player(120),next=player(900);let resolveOld;
    old.play=function(){this.playCalls++;return new Promise(resolve=>{resolveOld=resolve;});};
    app.updateAutoplayVisibility(old,true,false);app.updateAutoplayVisibility(next,true);
    old.top=-500;next.top=120;app.autoplayNextVideo();await settle();assert.equal(next.paused,false);
    old.paused=false;app.handleVideoPlay(old);resolveOld();await settle();
    assert.equal(old.paused,true);assert.equal(next.paused,false);
});
test('failed muted playback has no retry storm and can retry after scrolling away', async () => {
    const {app,player}=fixture(),video=player(120);app.videoMuted=true;
    video.play=function(){this.playCalls++;return Promise.reject(new Error('decoder failed'));};
    app.updateAutoplayVisibility(video,true);await settle();
    app.autoplayNextVideo();app.autoplayNextVideo();assert.equal(video.playCalls,1);
    video.top=-500;app.autoplayNextVideo();video.top=120;app.autoplayNextVideo();await settle();assert.equal(video.playCalls,2);
});
test('an old completion cannot pause a newer successful request for the same video', async () => {
    const {app,player}=fixture(),video=player(120);let resolveOld;
    const play=video.play;
    video.play=function(){this.playCalls++;return new Promise(resolve=>{resolveOld=resolve;});};
    app.updateAutoplayVisibility(video,true);
    video.top=-500;app.autoplayNextVideo();video.top=120;video.play=play;app.autoplayNextVideo();await settle();
    assert.equal(video.paused,false);resolveOld();await settle();assert.equal(video.paused,false);
});
test('switching to click-to-play cancels a decoder still waiting to start', async () => {
    const {app,player}=fixture(),video=player(120);let resolve;
    video.play=()=>new Promise(done=>{resolve=done;});
    app.updateAutoplayVisibility(video,true);app.setVideoPlayback('paused');
    video.paused=false;app.handleVideoPlay(video);resolve();await settle();assert.equal(video.paused,true);
});
test('audible autoplay refusal falls back once to muted playback', async () => {
    const {app,player}=fixture(),video=player(120);const play=video.play;
    video.play=function(){if(!this.muted){this.playCalls++;return Promise.reject(new Error('gesture needed'));}return play.call(this);};
    app.updateAutoplayVisibility(video,true);await settle();assert.equal(video.playCalls,2);assert.equal(video.paused,false);assert.equal(video.muted,true);assert.equal(app.videoMuted,false);
});
test('pausing an unresolved audible request prevents its muted fallback', async () => {
    const {app,player}=fixture(),video=player(120);let reject;
    video.play=function(){this.playCalls++;this.paused=false;app.handleVideoPlay(this);return new Promise((_,fail)=>{reject=fail;});};
    app.updateAutoplayVisibility(video,true);video.pause();reject(new Error('aborted by reader'));await settle();
    assert.equal(video.playCalls,1);assert.equal(video.paused,true);assert.equal(video.muted,false);
});
test('offscreen preparation warms metadata without enabling full-video buffering', () => {
    const {app}=fixture();const observed=[];app.mediaPrefetchObserver={observe:node=>observed.push(node)};
    const video={tagName:'VIDEO',preload:'none',closest:()=>null,querySelectorAll:()=>[]};
    app.observeMediaPrefetch(video);assert.equal(observed[0],video);
    app.prefetchMedia(video);assert.equal(video.preload,'metadata');
    app.prefetchMedia(video);assert.equal(video.preload,'metadata');
});
test('a playing inline decoder moved into the mobile viewer is no longer a feed candidate', async () => {
    const {app,player}=fixture(),video=player(120);
    app.updateAutoplayVisibility(video,true);await settle();
    app.lightboxOpen=true;video.closest=selector=>selector==='.lightbox-stage'?{}:null;
    app.autoplayNextVideo();app.updateAutoplayVisibility(video,false);app.autoplayNextVideo();
    assert.equal(video.paused,false,'feed scroll and viewport events must not pause the viewer');
});
test('a pending inline decoder hands play completion to its mobile viewer', async () => {
    const {app,player}=fixture(),video=player(120);let resolve;
    video.play=function(){this.playCalls++;return new Promise(done=>{resolve=done;});};
    app.updateAutoplayVisibility(video,true);
    app.lightboxOpen=true;video.closest=selector=>selector==='.lightbox-stage'?{}:null;
    app.autoplayNextVideo();video.paused=false;app.handleVideoPlay(video);resolve();await settle();
    assert.equal(video.paused,false);
});

(async()=>{
    let failures=0;
    for(const [name,run] of tests){try{await run();console.log(`ok - ${name}`);}catch(error){failures++;console.error(`not ok - ${name}\n${error.stack}`);}}
    console.log(`${tests.length-failures}/${tests.length} media playback tests passed`);
    if(failures)process.exitCode=1;
})();
