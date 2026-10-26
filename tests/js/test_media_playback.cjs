'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { appScripts, readScripts } = require('./page_scripts.cjs');
const source = readScripts(appScripts());

function fixture(timers = {}) {
    const videos = [], headers = [];
    const document = {
        hidden: false, body: {},
        querySelectorAll: selector => selector === '[data-video-occluder]' ? headers : videos,
    };
    const window = { innerWidth: 400, innerHeight: 800 };
    const context = { window, document, console, Date: timers.Date || Date, setTimeout: timers.setTimeout || setTimeout, clearTimeout: timers.clearTimeout || clearTimeout,
        localStorage: {setItem() {}},
        getComputedStyle: node => node.style || {},
        requestAnimationFrame: timers.requestAnimationFrame,
    };
    const { tweetApp, feedVideoVisibility, forcedMuteVideos, tweetNookWatchVideoPoster } = vm.runInNewContext(source + '\n({tweetApp, feedVideoVisibility, forcedMuteVideos, tweetNookWatchVideoPoster})', context);
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
    return {app, player, window, document, headers, visibility: feedVideoVisibility, forcedMuteVideos, watchPoster: tweetNookWatchVideoPoster};
}
const tests = [];
function test(name, run) { tests.push([name, run]); }
const settle = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };

test('expanded audio stays local through queued volume events and restores the feed preference', () => {
    for (const muted of [true, false]) for (const volume of [0, .6]) {
        const {app,player}=fixture(),video=player(120),other=player(550);
        app.videoMuted=muted;app.videoVolume=volume;
        app.applyVideoAudio(video);app.applyVideoAudio(other);
        app.setExpandedVideoAudio(video,true);
        app.handleVideoVolumeChange(video); // Real volumechange events arrive asynchronously.
        assert.equal(video.muted,false);assert.equal(video.volume,volume||1);
        assert.equal(app.videoMuted,muted);assert.equal(app.videoVolume,volume);
        assert.equal(other.muted,muted);assert.equal(other.volume,volume);
        app.setExpandedVideoAudio(video,true); // Repeated playback sync must retain controls.
        app.setSharedVideoAudio(video,true,.2,true);
        app.applyVideoAudio(video);assert.equal(video.muted,true);assert.equal(video.volume,.2);
        app.setSharedVideoAudio(video,false,.4,true);
        app.setExpandedVideoAudio(video,false);
        app.handleVideoVolumeChange(video);
        assert.equal(video.muted,muted);assert.equal(video.volume,volume);
        assert.equal(app.videoMuted,muted);assert.equal(app.videoVolume,volume);
    }
});

test('expansion temporarily clears browser mute and retries playback within the gesture', () => {
    const {app,player,forcedMuteVideos}=fixture(),video=player(120);
    forcedMuteVideos.add(video);app.applyVideoAudio(video);video.paused=false;
    let muted=true;
    Object.defineProperty(video,'muted',{get:()=>muted,set(value){muted=value;video.paused=true;}});
    app.setExpandedVideoAudio(video,true);
    assert.equal(video.muted,false);assert.equal(video.paused,false);assert.equal(video.playCalls,1);
    app.setExpandedVideoAudio(video,false);
    assert.equal(video.muted,true);assert.equal(forcedMuteVideos.has(video),true);
    assert.equal(app.videoMuted,false);
});

test('swipe dismissal releases standalone viewer audio and GIFs remain silent', () => {
    const {app,player}=fixture(),video=player(120),gif=player(550);
    app.videoMuted=true;app.applyVideoAudio(video);app.applyVideoAudio(gif);
    gif.hasAttribute=()=>false;
    app.setExpandedVideoAudio(gif,true);assert.equal(gif.muted,true);
    app.setExpandedVideoAudio(video,true);assert.equal(video.muted,false);
    app.$refs={lightboxStage:{querySelectorAll:()=>[video,gif]}};
    app.queueLightboxMotion=()=>{};app.lightboxOpen=true;
    app.finishLightboxDismiss();
    assert.equal(video.muted,true);assert.equal(app.videoMuted,true);assert.equal(app.lightboxOpen,false);
});

test('sticky headers count toward the later stop boundary', async () => {
    const {app,player,headers,visibility}=fixture();
    headers.push({getBoundingClientRect:()=>({top:0,left:0,width:400,height:100})});
    const video=player(120);
    app.updateAutoplayVisibility(video,true);await settle();assert.equal(video.paused,false);
    video.top=-120;assert.equal(visibility(video).ratio,.45);
    app.autoplayNextVideo();assert.equal(video.paused,false);
    video.top=-270;assert.equal(visibility(video).ratio,.075);
    app.autoplayNextVideo();assert.equal(video.paused,true);
    video.top=120;app.autoplayNextVideo();await settle();assert.equal(video.paused,false,'automatic pauses do not leave a manual hold');
});
test('videos start at a quarter visible and keep playing until less than a tenth remains', async () => {
    for (const enteringFromTop of [false,true]) {
        const {app,player}=fixture();
        const video=player(enteringFromTop?-301:701);
        app.updateAutoplayVisibility(video,true);await settle();assert.equal(video.playCalls,0);
        video.top=enteringFromTop?-300:700;
        app.autoplayNextVideo();await settle();assert.equal(video.paused,false);
        for (const top of enteringFromTop?[-359,-360,-359,-360]:[759,760,759,760]) {
            video.top=top;app.autoplayNextVideo();await settle();assert.equal(video.paused,false);
        }
        assert.equal(video.playCalls,1,'edge jitter must not restart playback');
        video.top=enteringFromTop?-361:761;
        app.autoplayNextVideo();assert.equal(video.paused,true);
        video.top=enteringFromTop?-359:759;
        app.autoplayNextVideo();assert.equal(video.playCalls,1,'a stopped video needs the entry threshold again');
    }
});
test('vertical handoffs follow the prominent picture in either direction without waiting for a sliver', async () => {
    for (const reverse of [false,true]) {
        const {app,player}=fixture(),current=player(120),next=player(reverse?-500:900);
        app.updateAutoplayVisibility(current,true,false);app.updateAutoplayVisibility(next,true);await settle();
        current.top=reverse?420:-20;next.top=reverse?-20:420;
        app.autoplayNextVideo();await settle();assert.equal(current.paused,false,'a small lead keeps the current owner');assert.equal(next.playCalls,0);
        current.top=reverse?500:-100;next.top=reverse?60:340;
        let previousPaused;const play=next.play;
        next.play=function(){previousPaused=current.paused;return play.call(this);};
        app.autoplayNextVideo();await settle();assert.equal(previousPaused,true);assert.equal(next.paused,false);
    }
});
test('prominence compares visible picture area rather than preferring a smaller fully visible clip', async () => {
    const {app,player,window}=fixture(),small=player(120,100),large=player(500,600);
    app.updateAutoplayVisibility(small,true,false);app.updateAutoplayVisibility(large,true);await settle();
    assert.equal(small.paused,true);assert.equal(large.paused,false,'the half-visible large picture occupies more of the viewport');
    large.play=function(){this.paused=false;app.handleVideoPlay(this);return Promise.resolve();};
    small.play();await settle();app.autoplayNextVideo();assert.equal(small.paused,false,'explicit play stays selected');
    window.scrollY=10;app.autoplayNextVideo();await settle();assert.equal(large.paused,false,'vertical movement restores the prominence rule');
});
test('a pending play may finish below the start boundary while still above the stop boundary', async () => {
    const {app,player}=fixture(),video=player(700);let resolve;
    video.play=function(){this.playCalls++;return new Promise(done=>{resolve=done;});};
    app.updateAutoplayVisibility(video,true);
    video.top=740;app.autoplayNextVideo();video.paused=false;app.handleVideoPlay(video);
    resolve();await settle();assert.equal(video.paused,false);assert.equal(video.playCalls,1);
    video.top=761;app.autoplayNextVideo();assert.equal(video.paused,true);
});
test('leaving a view rewinds played videos and leaves untouched players alone', () => {
    const {app,player}=fixture();
    const played=player(0),untouched=player(500);
    played.paused=false;played.currentTime=12;untouched.currentTime=0;
    let seeks=0;
    for(const video of [played,untouched]){
        let time=video.currentTime;
        Object.defineProperty(video,'currentTime',{get:()=>time,set(value){seeks++;time=value;}});
    }
    app.pauseAllVideos();
    assert.equal(played.paused,true);assert.equal(played.currentTime,0);
    assert.equal(untouched.pauseCalls,0);assert.equal(seeks,1);
});
test('a tall portrait video can autoplay while filling a short viewport', async () => {
    const {app,player,visibility}=fixture();const video=player(0,1600);
    assert.equal(visibility(video).ratio,1);app.updateAutoplayVisibility(video,true);await settle();assert.equal(video.paused,false);
});
test('a nested scroll panel clips candidates outside its visible region', async () => {
    const {app,player,visibility}=fixture();const video=player(300);
    video.parentElement={style:{overflowY:'auto'},getBoundingClientRect:()=>({top:200,left:0,right:400,bottom:380,width:400,height:180})};
    assert.equal(visibility(video).ratio,.2);app.updateAutoplayVisibility(video,true);await settle();assert.equal(video.playCalls,0);
});
test('observer batches choose the same topmost player in either feed insertion order', async () => {
    for(const reverse of [false,true]){
        const {app,player}=fixture(),upper=player(80),lower=player(400);
        for(const video of reverse?[lower,upper]:[upper,lower])app.updateAutoplayVisibility(video,true,false);
        app.autoplayNextVideo();await settle();assert.equal(upper.paused,false);assert.equal(lower.playCalls,0);
    }
});
test('carousel handoffs follow the more visible clip in both directions without edge jitter', async () => {
    const {app,player,document}=fixture();
    const track={scrollLeft:0,style:{overflowX:'auto'},parentElement:document.body,
        getBoundingClientRect:()=>({top:0,left:0,right:400,bottom:800,width:400,height:800})};
    const first=player(120),second=player(120);
    for(const [video,left] of [[first,0],[second,240]]){
        video.left=left;video.parentElement=track;
        video.closest=selector=>selector==='.media-carousel-track'?track:null;
        video.getBoundingClientRect=()=>({top:video.top,left:video.left,width:200,height:400});
        app.updateAutoplayVisibility(video,true,false);
    }
    app.autoplayNextVideo();await settle();assert.equal(first.paused,false);
    first.left=-12;second.left=228;track.scrollLeft=12;
    app.autoplayNextVideo();assert.equal(first.paused,false,'small moves retain the owner');
    first.left=-100;second.left=140;track.scrollLeft=100;
    app.autoplayNextVideo();await settle();assert.equal(second.paused,false);assert.equal(first.paused,true);
    first.left=0;second.left=240;track.scrollLeft=0;
    app.autoplayNextVideo();await settle();assert.equal(first.paused,false);assert.equal(second.paused,true);
});
test('explicit playback chooses a visible sibling until the reader scrolls its carousel', async () => {
    const {app,player,document}=fixture();
    const track={scrollLeft:0,style:{overflowX:'auto'},parentElement:document.body,
        getBoundingClientRect:()=>({top:0,left:0,right:400,bottom:800,width:400,height:800})};
    const first=player(120),second=player(120),third=player(120);
    for(const [video,left] of [[first,0],[second,210],[third,300]]){
        video.left=left;video.parentElement=track;
        video.closest=selector=>selector==='.media-carousel-track'?track:null;
        video.getBoundingClientRect=()=>({top:120,left:video.left,width:200,height:400});
        app.updateAutoplayVisibility(video,true,false);
    }
    app.autoplayNextVideo();await settle();
    third.play();app.autoplayNextVideo();await settle();
    assert.equal(third.paused,false);assert.equal(second.paused,true);
    track.scrollLeft=100;first.left=-100;second.left=110;third.left=350;
    app.autoplayNextVideo();await settle();assert.equal(second.paused,false);
});
test('carousel warmup prepares clips ahead of horizontal movement and changes direction on reversal', async () => {
    const {app,player,document}=fixture(),prepared=[];
    const track={scrollLeft:210,style:{overflowX:'auto'},parentElement:document.body,
        getBoundingClientRect:()=>({top:0,left:0,right:400,bottom:800,width:400,height:800})};
    const videos=[-210,0,210,420].map((left,index)=>{
        const v=player(120);v.left=left;v.parentElement=track;
        v.closest=selector=>selector==='.media-carousel-track'?track:null;
        v.getBoundingClientRect=()=>({top:120,left:v.left,width:200,height:400});
        Object.assign(v,{src:`/${index}.mp4`,preload:'metadata',getAttribute:()=>'/poster.jpg',querySelectorAll:()=>[],addEventListener() {}});
        app.updateAutoplayVisibility(v,true,false);return v;
    });
    app.autoplayNextVideo();await settle();assert.equal(videos[1].paused,false);
    app.prepareVideoPoster=v=>prepared.push(videos.indexOf(v));
    for(const v of videos)app.prefetchMedia(v);
    assert.deepEqual(prepared,[2,3],'past tiles cannot consume warmup slots');
    track.scrollLeft=200;videos.forEach(v=>v.left+=10);app.updateVideoWarmup();
    assert.deepEqual(prepared,[2,3,0]);
});
test('handoffs pause the old audio before calling play on its replacement', async () => {
    const {app,player}=fixture(),upper=player(80),lower=player(900);
    app.updateAutoplayVisibility(upper,true,false);app.updateAutoplayVisibility(lower,true);await settle();
    upper.top=-370;lower.top=150;let previousPaused;
    const play=lower.play;lower.play=function(){previousPaused=upper.paused;return play.call(this);};
    app.autoplayNextVideo();await settle();assert.equal(previousPaused,true);assert.equal(lower.paused,false);
});
test('a late unregistered offscreen play cannot steal audio from the visible owner', async () => {
    const {app,player}=fixture(),visible=player(120),stale=player(-2000);
    app.updateAutoplayVisibility(visible,true);await settle();
    stale.play();await settle();
    assert.equal(stale.paused,true);assert.equal(visible.paused,false);
    app.autoplayNextVideo();assert.equal(visible.paused,false);
});
test('native scrolling stops outgoing audio before the next selection frame', async () => {
    const {app,player}=fixture(),old=player(120),next=player(1000);
    app.updateAutoplayVisibility(old,true);await settle();
    old.top=-2000;next.top=120;
    app.stopOffscreenVideoPlayers();assert.equal(old.paused,true);assert.equal(next.playCalls,0);
    app.updateAutoplayVisibility(next,true);await settle();assert.equal(next.paused,false);
});
test('continuous fast scrolls start the visible replacement on the next frame without an idle wait', async () => {
    let now = 1000;
    const Clock = class extends Date { static now() { return now; } };
    const frames = [], timers = [];
    const {app, player, window} = fixture({Date: Clock,
        requestAnimationFrame: fn => {frames.push(fn); return frames.length;},
        setTimeout: fn => {timers.push(fn); return timers.length;}, clearTimeout() {},
    });
    const videos = [120, 1000, 2000, 3000].map(top => player(top));
    videos.forEach(video => app.updateAutoplayVisibility(video, true, false));
    app.autoplayNextVideo(); await settle();
    for (let i = 1; i < videos.length; i++) {
        now += 16;
        window.scrollY = i * 1000;
        videos.forEach((video, index) => {video.top = (index - i) * 1000 + 120;});
        app.queueVideoVisibility();
        assert.equal(videos[i - 1].paused, true, 'outgoing audio stops in the scroll event');
        assert.equal(videos[i].playCalls, 0);
        app.queueVideoVisibility();
        assert.equal(frames.length, 2, 'scroll events share one selection frame beside the running-player guard');
        frames.splice(0).forEach(frame => frame()); await settle();
        assert.equal(videos[i].paused, false, 'the visible clip starts while scrolling continues');
        assert.equal(videos.filter(video => !video.paused).length, 1);
        videos[i - 1].play();
        assert.equal(videos[i - 1].paused, true, 'a late offscreen play cannot take ownership');
        assert.equal(videos[i].paused, false);
    }
    assert.equal(timers.length, 0, 'playback never schedules a scroll-idle timer');
});

test('running-player frame checks stop audio when native scrolling precedes its scroll event', async () => {
    const frames = [];
    const {app, player} = fixture({requestAnimationFrame: fn => {frames.push(fn); return frames.length;}});
    const video = player(120);
    app.updateAutoplayVisibility(video, true); await settle();
    assert.equal(frames.length, 1);
    frames.shift()();
    assert.equal(frames.length, 1, 'only one guard follows a running player');
    video.top = -2000; // The compositor moved; no scroll/observer callback has run yet.
    frames.shift()();
    assert.equal(video.paused, true);
    assert.equal(frames.length, 0, 'the guard stops when no feed player is running');
});

test('scrolling keeps nearby warmup buffering and yields its decoder immediately to the visible owner', async () => {
    const frames = [];
    const {app, player, window} = fixture({requestAnimationFrame: fn => {frames.push(fn); return frames.length;}});
    const owner = player(120), next = player(1000), events = {};
    Object.assign(next, {src: '/next.mp4', preload: 'none', currentTime: 0, duration: 10, readyState: 0,
        getAttribute: () => '/poster.jpg', querySelectorAll: () => [],
        addEventListener: (name, fn) => {events[name] = fn;}, removeEventListener: name => {delete events[name];},
        buffered: {length: 1, start: () => 0, end: () => .5},
    });
    app.updateAutoplayVisibility(owner, true); await settle();
    app.prefetchMedia(next);
    next.readyState = 2; next.currentTime = .04; events.loadeddata();
    assert.equal(next.preload, 'auto'); assert.equal(next.paused, true);
    window.scrollY = 100; next.top = 900;
    app.queueVideoVisibility(); frames.splice(0).forEach(frame => frame()); await settle();
    assert.equal(next.preload, 'auto', 'moving the page does not cancel buffering');
    assert.equal(typeof events.progress, 'function');
    owner.top = -900; next.top = 120; window.scrollY = 1000;
    app.updateAutoplayVisibility(next, true, false);
    app.queueVideoVisibility(); frames.splice(0).forEach(frame => frame()); await settle();
    assert.equal(next.paused, false); assert.equal(next.muted, false);
    assert.equal(owner.paused, true); assert.equal(events.progress, undefined);
});
test('distant media releases its resource and restores source and progress when it returns', () => {
    const {app,player}=fixture(),video=player(120),attrs=new Map([['src','/clip.mp4']]);let loads=0;
    Object.assign(video,{currentTime:7,getAttribute:name=>attrs.get(name),setAttribute:(n,v)=>attrs.set(n,v),
        removeAttribute:n=>attrs.delete(n),load(){loads++;this.currentTime=0;}});
    video.play();app.releaseVideoResource(video);
    assert.equal(video.paused,true);assert.equal(attrs.has('src'),false);assert.equal(loads,1);
    app.releaseVideoResource(video);assert.equal(loads,1);
    app.restoreVideoResource(video);assert.equal(attrs.get('src'),'/clip.mp4');assert.equal(video.currentTime,7);
    video.closest=selector=>selector==='.lightbox-stage'?{}:null;
    app.releaseVideoResource(video);assert.equal(loads,1,'the viewer owns its resource');
});
test('a fast jump restores and starts a visible GIF even if its viewport entry arrived first', () => {
    const {app,player}=fixture(),gif=player(120),attrs=new Map([['src','/loop.mp4']]);
    Object.assign(gif,{muted:true,hasAttribute:name=>name==='autoplay',currentTime:0,getAttribute:name=>attrs.get(name),
        setAttribute:(n,v)=>attrs.set(n,v),removeAttribute:n=>attrs.delete(n),load(){}});
    app.releaseVideoResource(gif);app.restoreVideoResource(gif);assert.equal(gif.playCalls,1);assert.equal(gif.muted,true);
});
test('an unmute gesture primes only two silent spare decoders', () => {
    const {app,player,window}=fixture();window.matchMedia=()=>({matches:true});
    const owner=player(120),prepared=[],attrs=new Map([['src','/owner.mp4']]);let emptied=0;
    Object.assign(owner,{getAttribute:n=>attrs.get(n),removeAttribute:n=>attrs.delete(n),load(){emptied++}});
    const next=[900,1300,1700].map((top,i)=>{const v=player(top);Object.assign(v,{src:`/${i}.mp4`,preload:'metadata',getAttribute:()=>'/poster',querySelectorAll:()=>[],addEventListener(){}});return v});
    app.prepareVideoPoster=v=>prepared.push(v);
    next.forEach(v=>app.prefetchMedia(v));prepared.length=0;
    app.setSharedVideoAudio(owner,false,1,true);
    assert.equal(prepared.length,2);assert.deepEqual(prepared,next.slice(0,2));
    app.releaseVideoResource(owner);assert.equal(attrs.get('src'),'/owner.mp4','keep permission on the bounded authorized pool');
    assert.equal(emptied,0);
    prepared.length=0;app.setSharedVideoAudio(owner,false,1,true);assert.equal(prepared.length,0,'the pool stays bounded on later taps');
    app.setSharedVideoAudio(next[2],false,1,true);app.releaseVideoResource(owner);
    assert.equal(emptied,1,'unmuting a new player replaces a spare instead of growing the pool');
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
    video.play=function(){if(!this.muted){this.playCalls++;return Promise.reject(Object.assign(new Error('gesture needed'),{name:'NotAllowedError'}));}return play.call(this);};
    app.updateAutoplayVisibility(video,true);await settle();assert.equal(video.playCalls,2);assert.equal(video.paused,false);assert.equal(video.muted,true);assert.equal(app.videoMuted,false);
});
test('audible playback keeps the prepared decoder when permission allows it', async () => {
    const {app,player,window}=fixture(),video=player(120);window.matchMedia=()=>({matches:true});
    app.reuseAudibleFeedVideo=()=>{throw new Error('An allowed warm decoder must not be replaced');};
    app.updateAutoplayVisibility(video,true);await settle();
    assert.equal(video.paused,false);assert.equal(video.muted,false);assert.equal(video.playCalls,1);
});
test('an audible permission denial retries on the authorized decoder before muting', async () => {
    const {app,player}=fixture(),old=player(120),next=player(900),authorized=player(-500);
    app.updateAutoplayVisibility(old,true);await settle();
    old.top=-500;next.top=120;
    next.play=function(){this.playCalls++;return Promise.reject({name:'NotAllowedError'});};
    let handoffs=0;
    app.reuseAudibleFeedVideo=target=>{
        assert.equal(target,next);assert.equal(old.paused,true);handoffs++;
        authorized.top=next.top;next.top=-500;return authorized;
    };
    app.updateAutoplayVisibility(next,true);await settle();await settle();
    assert.equal(handoffs,1);assert.equal(authorized.paused,false);
    assert.equal(authorized.muted,false);assert.equal(app.videoMuted,false);
    assert.equal(next.playCalls,1);assert.equal(next.muted,false);
});
test('an interrupted source load cannot be mistaken for denied audible permission', async () => {
    const {app,player}=fixture(),video=player(120);const play=video.play;
    video.play=function(){this.playCalls++;return Promise.reject(Object.assign(new Error('source interrupted'),{name:'AbortError'}));};
    app.updateAutoplayVisibility(video,true);await settle();
    assert.equal(video.muted,false);assert.equal(video.playCalls,1);
    video.play=play;app.autoplayNextVideo();await settle();assert.equal(video.paused,false);assert.equal(video.muted,false);
});
test('queued pause and play events from a prior source cannot hold or interrupt resumed playback', async () => {
    const {app,player}=fixture(),current=player(120),old=player(900);
    app.updateAutoplayVisibility(current,true);await settle();
    app.handleVideoPause(current);app.autoplayNextVideo();
    assert.equal(current.paused,false);
    app.handleVideoPlay(old);assert.equal(current.paused,false);
});
test('pausing an unresolved audible request prevents its muted fallback', async () => {
    const {app,player}=fixture(),video=player(120);let reject;
    video.play=function(){this.playCalls++;this.paused=false;app.handleVideoPlay(this);return new Promise((_,fail)=>{reject=fail;});};
    app.updateAutoplayVisibility(video,true);video.pause();reject(new Error('aborted by reader'));await settle();
    assert.equal(video.playCalls,1);assert.equal(video.paused,true);assert.equal(video.muted,false);
});
test('offscreen preparation warms metadata without enabling full-video buffering', () => {
    const {app}=fixture();const observed=[];app.mediaPrefetchObserver={observe:node=>observed.push(node)};
    const video={tagName:'VIDEO',preload:'none',hasAttribute:()=>false,closest:()=>null,querySelectorAll:()=>[]};
    app.observeMediaPrefetch(video);assert.equal(observed[0],video);
    app.prefetchMedia(video);assert.equal(video.preload,'metadata');
    app.prefetchMedia(video);assert.equal(video.preload,'metadata');
});
test('an empty poster attribute still prepares a frame when its URL property resolves to the page', () => {
    const {app}=fixture();let prepared=0;app.prepareVideoPoster=()=>prepared++;
    app.prefetchMedia({tagName:'VIDEO',src:'/next.mp4',preload:'metadata',poster:'http://localhost/',
        hasAttribute:name=>name==='controls',getAttribute:()=>'',closest:()=>null,
        isConnected:true,paused:true,getBoundingClientRect:()=>({top:1000,height:400}),
        querySelectorAll:()=>[],play() {},addEventListener() {},
    });
    assert.equal(prepared,1);
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

test('explicit unmute clears every browser override and is inherited by later players', () => {
    const {app,player,forcedMuteVideos}=fixture();
    const first=player(120), second=player(900);
    app.videoMuted=true;first.muted=second.muted=true;
    forcedMuteVideos.add(first);forcedMuteVideos.add(second);
    app.setSharedVideoAudio(first,false,.7,true);
    assert.equal(first.muted,false);assert.equal(second.muted,false);
    assert.equal(forcedMuteVideos.has(second),false);
    const appended=player(1400);app.applyVideoAudio(appended);
    assert.equal(appended.muted,false);assert.equal(appended.volume,.7);
});
test('unmute retries in the gesture if the browser pauses on an audio change', () => {
    const {app,player}=fixture(),video=player(120);video.paused=false;video.muted=true;
    let muted=true;
    Object.defineProperty(video,'muted',{get:()=>muted,set(value){muted=value;video.paused=true;}});
    app.setSharedVideoAudio(video,false,1,true);
    assert.equal(video.playCalls,1);assert.equal(video.paused,false);
});
test('mobile audible handoffs reuse the gesture-authorized element across new pages and reverse scrolling', () => {
    const {app,player,window}=fixture();window.matchMedia=()=>({matches:true});
    const prepare=(video,src)=>{
        const attrs=new Map([['controls',''],['src',src]]);
        Object.defineProperty(video,'attributes',{get:()=>[...attrs].map(([name,value])=>({name,value}))});
        video.sourceWrites=0;
        Object.defineProperty(video,'src',{get:()=>attrs.get('src'),set(value){video.sourceWrites++;attrs.set('src',value);}});
        video.setAttribute=(name,value)=>attrs.set(name,value);
        video.getAttribute=name=>attrs.get(name);
        video.removeAttribute=name=>attrs.delete(name);
        video.hasAttribute=name=>attrs.has(name);
        video.currentTime=0;
        const holder={video,querySelector:()=>null,style:{height:''},offsetHeight:300,
            replaceChild(incoming,outgoing){assert.equal(this.video,outgoing);const from=incoming.parentElement;assert.equal(from.style.height,'300px');from.video=null;this.video=incoming;incoming.parentElement=this;outgoing.parentElement=null;},
            prepend(incoming){assert.equal(this.style.height,'300px');this.video=incoming;incoming.parentElement=this;},
        };
        video.parentElement=holder;
        return holder;
    };
    const authorized=player(120),firstHolder=prepare(authorized,'first.mp4');
    app.setSharedVideoAudio(authorized,false,1,true);
    const upcoming=player(900),nextHolder=prepare(upcoming,'second.mp4');
    assert.equal(app.reuseAudibleFeedVideo(upcoming),authorized);
    assert.equal(nextHolder.video,authorized);assert.equal(authorized.src,'second.mp4');
    assert.equal(firstHolder.video,upcoming);assert.equal(upcoming.src,'first.mp4');
    // Tile heights are held only while one of them is empty.
    assert.equal(firstHolder.style.height,'');assert.equal(nextHolder.style.height,'');
    const appended=player(1500),lastHolder=prepare(appended,'third.mp4');
    assert.equal(app.reuseAudibleFeedVideo(appended),authorized);
    assert.equal(lastHolder.video,authorized);assert.equal(authorized.src,'third.mp4');
    assert.equal(app.reuseAudibleFeedVideo(firstHolder.video),authorized);
    assert.equal(firstHolder.video,authorized);assert.equal(authorized.src,'first.mp4');
    const repeated=player(1800);prepare(repeated,'first.mp4');
    const writes=authorized.sourceWrites;authorized.currentTime=7;
    assert.equal(app.reuseAudibleFeedVideo(repeated),authorized);
    assert.equal(authorized.sourceWrites,writes,'repeated clips keep the loaded resource');
    assert.equal(repeated.sourceWrites,0);assert.equal(authorized.currentTime,0);
    assert.equal(repeated.preload,'none','outgoing placeholders must not start another download');
});
test('poster-backed clips buffer ahead silently, stop at the target and keep their starting position', async () => {
    const {app,player}=fixture(),active=player(120),upcoming=player(1000);
    const events={};let end=.1;
    Object.assign(upcoming,{src:'/next.mp4',preload:'none',currentTime:0,duration:10,readyState:0,
        getAttribute:()=>'/poster.jpg',querySelectorAll:()=>[],
        addEventListener:(name,fn)=>events[name]=fn,removeEventListener:name=>delete events[name],
        buffered:{length:1,start:()=>0,end:()=>end},
    });
    app.updateAutoplayVisibility(active,true);await settle();
    app.prefetchMedia(upcoming);
    assert.equal(upcoming.preload,'auto');assert.equal(upcoming.muted,true);
    assert.equal(active.paused,false);assert.equal(active.muted,false);
    upcoming.readyState=2;upcoming.currentTime=.04;events.loadeddata();
    assert.equal(upcoming.paused,true);assert.equal(upcoming.preload,'auto');
    end=2;events.progress();
    assert.equal(upcoming.preload,'metadata');assert.equal(upcoming.currentTime,0);
    assert.equal(upcoming.muted,false);assert.equal(active.paused,false);
    assert.equal(events.progress,undefined);
});
test('warmup is limited to two nearby clips and yields immediately to playback, overlays and data saving', async () => {
    const {app,player,window}=fixture();const clips=[];
    for(const top of [1000,1400,1800,4000]) {
        const v=player(top),events={};
        Object.assign(v,{src:`/${top}.mp4`,preload:'none',currentTime:0,readyState:0,
            getAttribute:()=>'/poster.jpg',querySelectorAll:()=>[],
            addEventListener:(name,fn)=>events[name]=fn,removeEventListener:name=>delete events[name]});
        clips.push(v);app.prefetchMedia(v);
    }
    assert.deepEqual(clips.map(v=>v.playCalls),[1,1,0,0]);
    clips[0].top=120;app.updateAutoplayVisibility(clips[0],true);await settle();
    assert.equal(clips[0].paused,false);assert.equal(clips[0].muted,false);
    app.showSettingsModal=true;app.autoplayNextVideo();
    assert.ok(clips.every(v=>v.paused));
    const calls=clips.map(v=>v.playCalls);
    app.showSettingsModal=false;window.navigator={connection:{saveData:true}};
    app.updateVideoWarmup();assert.deepEqual(clips.map(v=>v.playCalls),calls);
});
test('click-to-play never starts speculative decoders', () => {
    const {app,player}=fixture(),video=player(1000);app.videoPlayback='paused';
    Object.assign(video,{src:'/next.mp4',preload:'none',getAttribute:()=>'',querySelectorAll:()=>[],addEventListener() {}});
    app.prefetchMedia(video);assert.equal(video.playCalls,0);assert.equal(video.preload,'metadata');
});
test('a warmup play promise aborted by first-frame pausing keeps buffering until the timeout', async () => {
    let timeout,cleared=0;
    const {app,player}=fixture({setTimeout:fn=>{timeout=fn;return 1;},clearTimeout:()=>cleared++});
    const video=player(1000),events={};
    Object.assign(video,{src:'/slow.mp4',preload:'none',currentTime:0,duration:10,readyState:0,
        getAttribute:()=>'/poster.jpg',querySelectorAll:()=>[],
        addEventListener:(name,fn)=>events[name]=fn,removeEventListener:name=>delete events[name],
        play(){this.paused=false;return Promise.reject(Object.assign(new Error(),{name:'AbortError'}));},
        buffered:{length:1,start:()=>0,end:()=>.5}});
    app.prefetchMedia(video);video.readyState=2;events.loadeddata();await settle();
    assert.equal(video.paused,true);assert.equal(video.preload,'auto');
    timeout();assert.equal(video.preload,'metadata');assert.equal(cleared,1);
    assert.equal(events.progress,undefined);assert.equal(events.error,undefined);
});
test('a canceled warmup callback cannot finish or unmute a newer preparation on the same decoder', async () => {
    for (const rejectOld of [false, true]) {
        const {app,player,document}=fixture(),video=player(1000),events={},pending=[];
        Object.assign(video,{src:'/next.mp4',preload:'none',currentTime:0,readyState:0,
            getAttribute:()=>'/poster.jpg',querySelectorAll:()=>[],
            addEventListener:(name,fn)=>events[name]=fn,removeEventListener:name=>delete events[name],
            play(){this.paused=false;return new Promise((resolve,reject)=>pending.push({resolve,reject}));}});
        app.prefetchMedia(video);
        const oldReady=events.loadeddata;
        document.hidden=true;app.autoplayNextVideo();
        document.hidden=false;app.autoplayNextVideo();
        const newReady=events.loadeddata;
        assert.notEqual(newReady,oldReady);
        video.readyState=2;
        if(rejectOld)pending[0].reject(Object.assign(new Error(),{name:'NotAllowedError'}));
        else pending[0].resolve();
        oldReady();await settle();
        assert.equal(video.muted,true,'the newer background decoder must remain silent');
        assert.equal(video.preload,'auto');assert.equal(events.loadeddata,newReady);
        document.hidden=true;app.autoplayNextVideo();
    }
});
test('fast swipe intent stops audio before Safari moves, then resumes at the next scroll without waiting for idle', async () => {
    const frames=[],timers=new Map();let id=0;
    const {app,player}=fixture({requestAnimationFrame:fn=>{frames.push(fn);return frames.length;},
        setTimeout:fn=>{timers.set(++id,fn);return id;},clearTimeout:key=>timers.delete(key)});
    const old=player(120),next=player(1000),target={closest:()=>null};
    app.updateAutoplayVisibility(old,true,false);app.updateAutoplayVisibility(next,true);await settle();
    const touch=(type,y,t)=>app.trackVideoScrollTouch({type,timeStamp:t,target,touches:[{clientX:20,clientY:y}]});
    touch('touchstart',600,100);touch('touchmove',200,110);
    assert.equal(old.paused,true,'stop in the input task, before any geometry changes');
    app.autoplayNextVideo();await settle();assert.equal(old.paused,true,'stale geometry cannot restart the outgoing player');
    old.paused=false;app.handleVideoPlay(old);assert.equal(old.paused,true,'late play cannot escape the pending swipe');
    old.top=-12000;next.top=120;
    app.queueVideoVisibility();while(frames.length){const batch=frames.splice(0);for(const fn of batch)fn();if(!next.paused)break;}
    await settle();assert.equal(next.paused,false);assert.equal(timers.size,0,'scroll releases immediately rather than extending an idle timer');
    touch('touchmove',100,120);touch('touchmove',0,130);
    assert.equal(next.paused,false,'later samples in the same drag must not repeatedly interrupt the visible replacement');
});
test('slow drags keep playing and a fast swipe at the scroll boundary cannot leave playback blocked', async () => {
    const frames=[],timers=new Map();let id=0;
    const {app,player}=fixture({requestAnimationFrame:fn=>{frames.push(fn);return frames.length;},
        setTimeout:fn=>{timers.set(++id,fn);return id;},clearTimeout:key=>timers.delete(key)});
    const video=player(120),target={closest:()=>null};
    app.updateAutoplayVisibility(video,true);await settle();
    const touch=(type,y,t)=>app.trackVideoScrollTouch({type,timeStamp:t,target,touches:[{clientX:20,clientY:y}]});
    touch('touchstart',600,100);touch('touchmove',540,300);
    assert.equal(video.paused,false,'normal slow scrolling does not pause the visible video');
    touch('touchmove',200,310);assert.equal(video.paused,true);
    [...timers.values()][0]();const batch=frames.splice(0);batch.forEach(fn=>fn());await settle();
    assert.equal(video.paused,false,'a boundary swipe with no scroll event recovers');
});
test('small fast touch samples accumulate swipe intent in either axis without treating a scrub as a scroll', async () => {
    for (const horizontal of [false,true]) {
        const frames=[];
        const {app,player}=fixture({requestAnimationFrame:fn=>{frames.push(fn);return frames.length;}});
        const video=player(120);app.updateAutoplayVisibility(video,true);await settle();
        let scrub=true;
        const target={closest:selector=>selector==='[data-video-seek]'?scrub:horizontal?{}:null};
        const touch=(type,x,y,t)=>app.trackVideoScrollTouch({type,timeStamp:t,target,touches:[{clientX:x,clientY:y}]});
        touch('touchstart',300,600,100);touch('touchmove',200,500,110);
        assert.equal(video.paused,false,'scrubbing never transfers audio ownership');
        scrub=false;touch('touchstart',300,600,200);
        for(let i=1;i<=3;i++)touch('touchmove',300-(horizontal?i*18:0),600-(horizontal?0:i*18),200+i*12);
        assert.equal(video.paused,true,'short native move samples still identify a fast swipe');
        app.releaseVideoScrollIntent();
    }
});
test('hidden tabs cancel warmup and can prepare again after returning', () => {
    const {app,player,document}=fixture(),video=player(1000),events={};
    Object.assign(video,{src:'/next.mp4',preload:'none',currentTime:0,readyState:0,
        getAttribute:()=>'/poster.jpg',querySelectorAll:()=>[],
        addEventListener:(name,fn)=>events[name]=fn,removeEventListener:name=>delete events[name]});
    app.prefetchMedia(video);assert.equal(video.playCalls,1);
    document.hidden=true;app.autoplayNextVideo();assert.equal(video.paused,true);
    assert.equal(video.preload,'metadata');assert.equal(events.progress,undefined);
    document.hidden=false;app.autoplayNextVideo();assert.equal(video.playCalls,2);
    app.videoPlayback='paused';app.autoplayNextVideo();assert.equal(video.paused,true);
});
test('posters remain visible through loading and waiting, and hide only after frame presentation', () => {
    const {watchPoster}=fixture(),events={},frames=new Map();let id=0;
    const image={hidden:false,getAttribute:()=>'/poster.jpg'};
    const video={readyState:0,paused:true,parentElement:{querySelector:()=>image},
        addEventListener:(name,fn)=>events[name]=fn,
        requestVideoFrameCallback:fn=>{frames.set(++id,fn);return id;},
        cancelVideoFrameCallback:key=>frames.delete(key),
    };
    watchPoster(video);assert.equal(image.hidden,false);
    const holder=video.parentElement;
    video.closest=()=>holder;
    video.parentElement={querySelector:()=>null}; // The viewer's motion surface wraps the live decoder.
    video.readyState=2;events.loadeddata();assert.equal(image.hidden,false);
    frames.get(id)();assert.equal(image.hidden,true);
    events.playing();assert.equal(image.hidden,true,'playing must not reinstate the poster');
    const stale=frames.get(id);events.emptied();assert.equal(image.hidden,false);
    stale();assert.equal(image.hidden,false,'an old source callback cannot reveal a replacement');
});
test('offscreen first-frame capture stays silent and survives the narrower autoplay observer', async () => {
    const {app,player,document}=fixture(),current=player(120),upcoming=player(1800);
    const image={},events={};
    Object.assign(upcoming,{src:'http://localhost/first-frame.mp4',poster:'',videoWidth:720,videoHeight:1280,readyState:0,
        parentElement:{querySelector:()=>image},
        addEventListener:(name,fn)=>events[name]=fn,removeEventListener:name=>delete events[name],
    });
    document.createElement=()=>({getContext:()=>({drawImage() {}}),toDataURL:()=> 'data:image/jpeg;first-frame'});
    app.updateAutoplayVisibility(current,true);await settle();
    app.prepareVideoPoster(upcoming);assert.equal(upcoming.muted,true);
    app.updateAutoplayVisibility(upcoming,false);await settle();
    assert.equal(upcoming.paused,false,'offscreen visibility must not abort frame preparation');
    assert.equal(current.paused,false,'silent preparation must not interrupt the active video');
    upcoming.readyState=2;events.loadeddata();assert.equal(upcoming.paused,true);
    assert.equal(upcoming.poster,'data:image/jpeg;first-frame');
    assert.equal(image.src,upcoming.poster);assert.equal(current.paused,false);
    const repeated=player(2400),preview={};
    Object.assign(repeated,{src:upcoming.src,getAttribute:()=>'',preload:'none',
        addEventListener() {},querySelectorAll:()=>[],parentElement:{querySelector:()=>preview}});
    app.prefetchMedia(repeated);
    assert.equal(repeated.poster,upcoming.poster);
    assert.equal(repeated.playCalls,0,'cached posters stay available outside the playback warmup window');
    assert.equal(preview.hidden,false);
});

(async()=>{
    let failures=0;
    for(const [name,run] of tests){try{await run();console.log(`ok - ${name}`);}catch(error){failures++;console.error(`not ok - ${name}\n${error.stack}`);}}
    console.log(`${tests.length-failures}/${tests.length} media playback tests passed`);
    if(failures)process.exitCode=1;
})();
