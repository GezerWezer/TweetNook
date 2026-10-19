'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const jsDir = path.resolve(__dirname, '../../tweetnook/web/static/js');

function fixture({ supported = true, demo = false } = {}) {
    const timers = new Map();
    const events = new Map();
    const sources = [];
    const requests = [];
    let timerId = 0;
    class EventSource {
        readyState = 0;
        closed = false;
        constructor(url) { this.url = url; sources.push(this); }
        addEventListener(name, callback) { this[name] = callback; }
        close() { this.closed = true; this.readyState = 2; }
        emit(data) { this.readyState = 1; this.activity({ data: JSON.stringify(data) }); }
    }
    const context = {
        window: {
            TWEETNOOK_DEMO: demo,
            ...(supported ? { EventSource } : {}),
            addEventListener(name, callback) { events.set(`window:${name}`, callback); },
            removeEventListener(name) { events.delete(`window:${name}`); },
        },
        document: {
            hidden: false,
            addEventListener(name, callback) { events.set(`document:${name}`, callback); },
            removeEventListener(name) { events.delete(`document:${name}`); },
        },
        AbortController,
        setTimeout(callback, delay) { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
        clearTimeout(id) { timers.delete(id); },
        setInterval() { return 0; },
        clearInterval() {},
        fetch(url, options) {
            let resolve;
            const promise = new Promise(done => { resolve = done; });
            requests.push({ url, options, resolve: data => resolve({ ok: true, json: async () => data }) });
            return promise;
        },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(jsDir, 'themes.js'), 'utf8') + '\n' +
        fs.readFileSync(path.join(jsDir, 'app.js'), 'utf8') + '\nwindow.app=tweetApp();', context);
    const app = context.window.app;
    app.fetchArchiveEnrichmentStatus = () => {};
    return { app, context, timers, events, sources, requests };
}

const idle = { active: false, snapshot: null, last_snapshot: null };
const running = count => ({ active: true, snapshot: {
    run_id: 'stream-run', running: true, steps: [{ key: 'media', state: 'active', completed: count }],
} });
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

(async () => {
    {
        const f = fixture();
        f.app.startActivityPolling();
        assert.equal(f.sources.length, 1);
        assert.equal(f.sources[0].url, '/api/activity/events');
        f.sources[0].emit(running(1));
        const snapshot = f.app.activity;
        assert.equal(f.requests[0].options.signal.aborted, true);
        f.requests[0].resolve(idle);
        await flush();
        assert.equal(f.app.activity.steps[0].completed, 1, 'late poll must not replace newer stream');
        assert.equal(f.app.activityPollTimer, null);
        f.sources[0].emit(running(2));
        assert.equal(f.app.activity, snapshot, 'renderer preserves reactive snapshot identity');
        assert.equal(f.app.activity.steps[0].completed, 2);
        const originalWatchdog = [...f.timers.entries()].find(([,timer]) => timer.delay === 45000);
        assert.ok(originalWatchdog, 'open streams need an inactivity watchdog');
        f.sources[0].heartbeat({data:'{}'});
        assert.equal(f.timers.has(originalWatchdog[0]), false, 'heartbeat renews watchdog');
        f.sources[0].emit({ active: false, last_snapshot: {
            ...running(2).snapshot, running: false, success: true,
            steps: [{ key: 'media', state: 'complete' }],
        } });
        assert.equal(f.app.activity, null);
        assert.equal(f.app.lastActivity.success, true);
        assert.equal(f.app.feedContentRevision, 1);
        f.app.stopActivityPolling();
        assert.equal(f.sources[0].closed, true);
        assert.equal(f.events.size, 0);
        assert.equal(f.timers.size, 0);
    }
    {
        const f = fixture();
        f.app.startActivityPolling();
        f.requests[0].resolve(idle);
        await flush();
        f.sources[0].emit(idle);
        const watchdog = [...f.timers.values()].find(timer => timer.delay === 45000);
        watchdog.callback();
        assert.equal(f.sources[0].closed, true, 'silently stalled source closes');
        assert.ok(f.app.activityPollTimer, 'silent stalls restore polling');
        assert.ok([...f.timers.values()].some(timer => timer.delay === 15000), 'silent stalls reconnect');
        f.app.stopActivityPolling();
    }
    {
        const f = fixture();
        f.app.startActivityPolling();
        f.requests[0].resolve(idle);
        await flush();
        f.sources[0].emit(idle);
        f.sources[0].readyState = 0;
        f.sources[0].onerror();
        assert.ok(f.app.activityPollTimer, 'polling must resume while EventSource reconnects');
        assert.equal(f.sources[0].closed, false, 'native reconnect remains active');
        f.sources[0].emit(running(3));
        assert.equal(f.app.activityPollTimer, null);
        f.sources[0].readyState = 2;
        f.sources[0].onerror();
        const retry = [...f.timers.values()].find(timer => timer.delay === 15000);
        assert.ok(retry, 'terminal stream failure should retry after a backoff');
        retry.callback();
        assert.equal(f.sources.length, 2);
        f.app.stopActivityPolling();
    }
    {
        const f = fixture();
        f.app.startActivityPolling();
        f.requests[0].resolve(idle);
        await flush();
        f.sources[0].emit(idle);
        f.context.document.hidden = true;
        f.events.get('document:visibilitychange')();
        assert.equal(f.sources[0].closed, true);
        assert.equal(f.app.activityPollTimer, null);
        f.context.document.hidden = false;
        f.events.get('document:visibilitychange')();
        assert.equal(f.sources.length, 2);
        f.sources[0].emit(running(99));
        assert.equal(f.app.activity, null, 'closed source events are ignored');
        f.sources[1].emit(running(4));
        assert.equal(f.app.activity.steps[0].completed, 4);
        f.requests[1].resolve(idle);
        await flush();
        f.app.stopActivityPolling();
    }
    for (const options of [{ supported: false }, { demo: true }]) {
        const f = fixture(options);
        f.app.startActivityPolling();
        f.requests[0].resolve(idle);
        await flush();
        assert.equal(f.sources.length, 0);
        assert.ok(f.app.activityPollTimer, 'unsupported browsers/demo retain polling');
        f.app.stopActivityPolling();
    }
    console.log('activity stream browser tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
