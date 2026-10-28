// Optional real iOS Safari regression. Start safaridriver and boot an iPhone
// simulator first; this serves only synthetic posts and public demo videos.
// SAFARI_DRIVER_URL defaults to http://127.0.0.1:4444. SAFARI_DEVICE_UDID selects
// a simulator. SAFARI_SESSION_ID reuses a session without closing it afterward.
// SAFARI_SCROLL_CSS can select a saved stylesheet for a before/after comparison.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const web = path.join(root, 'tweetnook/web');
const { appScripts, readScripts } = require('../../tests/js/page_scripts.cjs');
const defaults = vm.runInNewContext(readScripts(appScripts()) + '\ntweetApp()', {window: {}});
const tweets = Array.from({length: 12}, (_, i) => ({
    tweet_id: String(100 + i), text: `Safari scroll fixture ${i + 1}`,
    created_at: '2026-09-05T00:00:00Z', collections: ['like'], raw_json: {},
    author: {id: '42', username: 'fixture', display_name: 'Offline fixture'},
    media: [{type: 'video', width: i % 2 ? 1280 : 720, height: i % 2 ? 720 : 1280,
        download: {local_path: `fixture/video${i % 3 + 1}.mp4`}}],
}));
const server = http.createServer((req, res) => {
    const route = new URL(req.url, 'http://localhost').pathname;
    let data;
    if (route === '/api/tweets') data = {tweets, page: 1, pages: 1, total: tweets.length};
    else if (route === '/api/automated-tagging') data = {installed: true, values: defaults.automatedTagging, accounting: {}};
    else if (route === '/api/setup') data = {required: false, completed: true, web: {password_configured: true}, archive: {ready: true}, auth: {values: {}}};
    else if (route === '/api/config') data = {values: {}, explicit: {}};
    else if (route.startsWith('/api/')) data = {};
    if (data !== undefined) {
        res.writeHead(200, {'Content-Type': 'application/json'});
        res.end(JSON.stringify(data));
        return;
    }
    const file = route === '/' ? path.join(web, 'index.html')
        : route === '/static/css/styles.css' && process.env.SAFARI_SCROLL_CSS ? path.resolve(process.env.SAFARI_SCROLL_CSS)
        : route.startsWith('/fixture/') ? path.join(root, 'demo/demo media', path.basename(route))
        : path.resolve(web, '.' + route);
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404).end(); return; }
    const size = fs.statSync(file).size;
    const headers = {'Cache-Control': 'no-store', 'Accept-Ranges': 'bytes', 'Content-Type':
        {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.mp4': 'video/mp4'}[path.extname(file)] || 'application/octet-stream'};
    if (req.headers.range) {
        const [from, to] = req.headers.range.replace('bytes=', '').split('-');
        const start = Number(from), end = to ? Number(to) : size - 1;
        res.writeHead(206, {...headers, 'Content-Length': end - start + 1, 'Content-Range': `bytes ${start}-${end}/${size}`});
        fs.createReadStream(file, {start, end}).pipe(res);
    } else {
        res.writeHead(200, {...headers, 'Content-Length': size});
        fs.createReadStream(file).pipe(res);
    }
});
const driver = process.env.SAFARI_DRIVER_URL || 'http://127.0.0.1:4444';
let session = process.env.SAFARI_SESSION_ID;
async function command(route, body, method = 'POST') {
    const response = await fetch(driver + route, {method, signal: AbortSignal.timeout(60000),
        headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
    const {value} = await response.json();
    if (!response.ok || value?.error) throw new Error(JSON.stringify(value));
    return value;
}
const execute = (fn, ...args) => command(`/session/${session}/execute/sync`, {script: `return (${fn})(...arguments)`, args});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
    const evidence = fs.mkdtempSync(path.join(os.tmpdir(), 'tweetnook-safari-scroll-'));
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    try {
        if (!session) {
            const capabilities = {browserName: 'safari', platformName: 'iOS', 'safari:useSimulator': true,
                'safari:deviceType': 'iPhone', 'webkit:alwaysAllowAutoplay': true};
            if (process.env.SAFARI_DEVICE_UDID) capabilities['safari:deviceUDID'] = process.env.SAFARI_DEVICE_UDID;
            session = (await command('/session', {capabilities: {alwaysMatch: capabilities}})).sessionId;
        }
        await command(`/session/${session}/timeouts`, {script: 45000, pageLoad: 30000});
        await command(`/session/${session}/url`, {url: `http://127.0.0.1:${server.address().port}/`});
        let loaded = false;
        for (let i = 0; i < 100 && !loaded; i++) {
            loaded = await execute(() => document.querySelectorAll('.feed-card video').length === 12);
            if (!loaded) await sleep(100);
        }
        assert.ok(loaded, 'Fixture feed did not load');
        await execute(() => {
            window.fixtureApp = Alpine.$data(document.querySelector('[x-data="tweetApp()"]'));
            window.handoffs = [];
            const reuse = fixtureApp.reuseAudibleFeedVideo.bind(fixtureApp);
            fixtureApp.reuseAudibleFeedVideo = next => {
                const y = scrollY;
                const result = reuse(next);
                if (next && result !== next) handoffs.push({before: y, after: scrollY});
                return result;
            };
            const video = document.querySelector('.feed-card video');
            // The driver permits every decoder to autoplay. Exercise Safari's
            // per-element denial explicitly so the fallback source/DOM handoff
            // still runs when the app first tries the prepared decoder.
            const nativePlay = HTMLMediaElement.prototype.play;
            HTMLMediaElement.prototype.play = function () {
                if (!this.muted && this !== video) return Promise.reject(new DOMException('Fixture requires the authorized decoder', 'NotAllowedError'));
                return nativePlay.call(this);
            };
            scrollBy(0, video.getBoundingClientRect().top - 130);
            // Test policy allows audible playback without a native tap. It still
            // uses real Safari decoding, DOM moves, layout and asynchronous scrolling.
            fixtureApp.setSharedVideoAudio(video, false, 1, true);
            video.play();
        });
        await sleep(1000);
        assert.ok(await execute(() => [...document.querySelectorAll('video')].some(v => !v.paused && !v.muted && v.currentTime > 0)), 'No decoded audible playback');
        const result = await command(`/session/${session}/execute/async`, {args: [], script: `
            const done = arguments[arguments.length - 1];
            (async () => {
                let overlaps = 0, audibleFrames = 0;
                const max = document.documentElement.scrollHeight - innerHeight;
                for (const [from, to] of [[scrollY, max], [max, 0], [0, max], [max, 0]]) {
                    const start = performance.now(), duration = 2500;
                    while (performance.now() - start < duration) {
                        scrollTo(0, from + (to - from) * Math.min(1, (performance.now() - start) / duration));
                        await new Promise(requestAnimationFrame);
                        const audible = [...document.querySelectorAll('video')].filter(v => !v.paused && !v.muted);
                        if (audible.length > 1) overlaps++;
                        if (audible.length) audibleFrames++;
                    }
                    scrollTo(0, to);
                    await new Promise(resolve => setTimeout(resolve, 300));
                }
                done({handoffs, overlaps, audibleFrames, viewport: [innerWidth, innerHeight], userAgent: navigator.userAgent});
            })().catch(error => done({error: error.message}));`});
        fs.writeFileSync(path.join(evidence, 'scroll.json'), JSON.stringify(result, null, 2));
        const screenshot = await command(`/session/${session}/screenshot`, undefined, 'GET');
        fs.writeFileSync(path.join(evidence, 'scroll.png'), Buffer.from(screenshot, 'base64'));
        console.log(JSON.stringify({evidence, viewport: result.viewport, userAgent: result.userAgent,
            handoffs: result.handoffs?.length, overlaps: result.overlaps, audibleFrames: result.audibleFrames,
            maxHandoffShift: Math.max(0, ...(result.handoffs || []).map(h => Math.abs(h.after - h.before)))}));
        assert.equal(result.error, undefined);
        assert.ok(result.handoffs.length >= 20, 'Too few handoffs to exercise scrolling');
        assert.equal(result.overlaps, 0, 'Multiple audible decoders');
        assert.ok(result.audibleFrames > 20, 'Audio did not follow scrolling');
        assert.ok(result.handoffs.every(h => Math.abs(h.after - h.before) <= 1), 'Safari jumped during a handoff');
    } finally {
        if (session && !process.env.SAFARI_SESSION_ID) await command(`/session/${session}`, undefined, 'DELETE');
        await new Promise(resolve => server.close(resolve));
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
