/* Session-only Jobs playback. Counts follow representative runs; no workers are started. */
(function installDemoJobs(global) {
    'use strict';
    const copy = value => JSON.parse(JSON.stringify(value));
    const notice = 'DEMO REPLAY: representative sample runs; timings adjusted for playback. No Twitter/X or model requests.';
    const durations = {sync: 152, check: 30};
    const command = key => `tweetnook ${key}`;
    const countText = metrics => Object.entries(metrics).map(([key, value]) =>
        `${value.toLocaleString('en-US')} ${{still_available: 'still on X', still_unavailable: 'still unavailable', retry_later: 'to recheck', already_known: 'skipped', remaining_due: 'still due', downloaded_bytes: 'bytes'}[key] || key.replaceAll('_', ' ')}`).join(' · ');

    function phase(key, title, start, end, total, unit, metrics, activity, extra = {}) {
        return {key, title, start, end, total, unit, metrics, activity, ...extra};
    }

    function syncPhases(collections) {
        const selected = collections.filter(key => ['bookmarks', 'likes', 'tweets'].includes(key));
        const collectionSeconds = {bookmarks: 1, likes: 0, tweets: 1};
        let collectionStart = 1;
        const collectionSteps = selected.map(key => {
            const start = collectionStart;
            collectionStart += collectionSeconds[key];
            return phase(`sync:${key}:head`, {bookmarks: 'Bookmarks', likes: 'Likes', tweets: 'Tweets'}[key],
                start, collectionStart, 20, 'tweets', {pages: 1, tweets_seen: 20, new_tweets: 0},
                'Fetching page 1 · head pass · stop at saved archive history',
                {summary: '1 pages · 20 tweets · archive is up to date', progress: false});
        });
        const threadsEnd = collectionStart + 148, mediaEnd = threadsEnd + 2;
        return [
            phase(`preflight:${selected.join(',')}`, 'Prepare', 0, 1, 3 + selected.length, 'checks', {},
                'Connecting to Twitter/X', {summary: `Connected · ${selected.length}/${selected.length} collections ready`}),
            ...collectionSteps,
            phase('threads', 'Threads', collectionStart, threadsEnd, 7, 'candidates', {expanded: 7, already_known: 150, failed: 0},
                'Expanding replies and quotes', {summary: '7 expanded · 150 skipped', loading: 20}),
            phase('resurrection', 'Unavailable tweets', threadsEnd, threadsEnd, 0, 'tweets', {}, '', {skip: 'no due retryable unavailable tweets'}),
            phase('articles', 'Articles', threadsEnd, threadsEnd, 0, 'articles', {}, '', {skip: 'no article rows requiring refresh'}),
            phase('media', 'Media', threadsEnd, mediaEnd, 21, 'items', {downloaded: 21, downloaded_bytes: 14260634, failed: 0},
                'Downloading photo', {summary: '21 downloaded · 13.6 MiB'}),
            phase('urls', 'Link previews', mediaEnd, mediaEnd, 0, 'URLs', {}, '', {skip: 'no saved URLs requiring metadata'}),
        ];
    }

    function checkPhases() {
        return [
            phase('check:saves', 'Saved tweets', 0, 18, 113, 'tweets',
                {still_available: 98, restored: 1, unavailable: 9, retry_later: 1, protected: 3, failed: 1},
                'Checking through FxTwitter', {scenes: [
                    [0, 0, 0, 0, 0, 0, 0, 0],
                    [0.1, 11, 11, 0, 0, 0, 0, 0],
                    [0.2, 22, 21, 0, 0, 0, 1, 0],
                    [0.4, 45, 40, 1, 2, 0, 1, 1],
                    [0.6, 68, 59, 1, 5, 0, 2, 1],
                    [0.8, 90, 76, 1, 9, 1, 2, 1],
                    [1, 113, 98, 1, 9, 1, 3, 1],
                ]}),
            phase('check:unavailable', 'Unavailable tweets', 18, 20, 3, 'tweets',
                {still_available: 0, restored: 1, unavailable: 1, retry_later: 0, protected: 1, failed: 0},
                'Checking through FxTwitter', {scenes: [
                    [0, 0, 0, 0, 0, 0, 0, 0],
                    [0.3, 1, 0, 0, 1, 0, 0, 0],
                    [0.65, 2, 0, 0, 1, 0, 1, 0],
                    [1, 3, 0, 1, 1, 0, 1, 0],
                ]}),
            phase('resurrection', 'Unavailable tweets', 20, 30, 2, 'tweets',
                {restored: 0, still_unavailable: 2, remaining_due: 1},
                'Checking an unavailable tweet', {summary: '0 restored · 2 still unavailable · 1 still due'}),
        ];
    }

    function sceneFor(step, elapsed) {
        const duration = step.end - step.start;
        const loading = step.loading || 0;
        const ratio = Math.max(0, Math.min(1, (elapsed - step.start - loading) / (duration - loading || 1)));
        const completed = Math.floor(step.total * ratio);
        let metrics;
        if (step.scenes) {
            const index = Math.min(step.scenes.length - 2, step.scenes.findLastIndex(row => row[0] <= ratio));
            const from = step.scenes[index], to = step.scenes[index + 1];
            const blend = (ratio - from[0]) / (to[0] - from[0]);
            metrics = Object.fromEntries(Object.keys(step.metrics).map((key, i) => [key, Math.floor(from[i + 2] + (to[i + 2] - from[i + 2]) * blend)]));
            // The buckets are exhaustive; rounding must not leave uncounted checked posts.
            return {metrics, completed: Object.values(metrics).reduce((sum, n) => sum + n, 0)};
        }
        metrics = Object.fromEntries(Object.entries(step.metrics).map(([key, value]) => [key,
            key === 'already_known' ? value : key === 'remaining_due' ? (ratio === 1 ? value : 0) : Math.floor(value * ratio)]));
        return {completed, metrics};
    }

    function createReplay({now, specs, schedule, collections, seedLastActivity = false}) {
        let active = null, lastActivity = null, serial = 0;
        const queue = [], history = [], logs = new Map();
        const specFor = key => specs.find(job => job.key === key);
        const phasesFor = key => key === 'check' ? checkPhases() : syncPhases(collections());

        function makeRun(key, startedAt, runId) {
            const phases = phasesFor(key);
            return {key, runId, startedAt, phases, duration: phases.at(-1).end, stoppedAt: null};
        }

        function snapshot(run, at) {
            const elapsed = Math.max(0, Math.min(run.duration, (run.stoppedAt ?? at) - run.startedAt));
            const stopped = run.stoppedAt !== null;
            const complete = elapsed >= run.duration || stopped;
            const steps = run.phases.map(step => {
                const began = elapsed >= step.start;
                const state = step.skip ? (began || complete ? 'skipped' : 'pending')
                    : elapsed >= step.end ? 'complete' : began ? (stopped ? 'failed' : 'active') : stopped ? 'skipped' : 'pending';
                const seconds = Math.max(0, Math.min(step.end - step.start, elapsed - step.start));
                const scene = sceneFor(step, elapsed);
                const completed = ['pending', 'skipped'].includes(state) ? 0 : state === 'complete' ? step.total : scene.completed;
                const metrics = ['pending', 'skipped'].includes(state) ? {} : state === 'complete' ? copy(step.metrics) : scene.metrics;
                const rate = state === 'active' && completed > 0 && seconds > 0 && step.progress !== false ? completed / seconds : null;
                const counters = step.key.startsWith('sync:') ? `${completed ? 1 : 0} pages · ${completed} tweets saved`
                    : step.key.startsWith('preflight:') ? `${completed}/${step.total} checks`
                    : step.key === 'media' ? `${metrics.downloaded || 0} downloaded · ${((metrics.downloaded_bytes || 0) / 1048576).toFixed(1)} MiB`
                    : countText(metrics);
                const activity = step.key === 'threads' && seconds < step.loading ? 'Loading archived thread expansion state…' : step.activity;
                const skipReason = step.skip && began ? step.skip : 'an earlier failure stopped the command';
                return {
                    key: step.key, title: step.title, state, completed, total: step.total, unit: step.unit,
                    activity: state === 'active' ? activity : '',
                    summary: state === 'complete' ? (step.summary || countText(metrics)) : state === 'failed' ? 'Stopped by user'
                        : state === 'skipped' ? `skipped due to ${skipReason}` : '',
                    metrics, counters: state === 'active' ? counters : '',
                    reason: state === 'skipped' ? skipReason : null,
                    detail: 'Demo replay · representative sample run · adjusted playback timing',
                    elapsed_seconds: seconds, rate, rate_unit: `${step.unit}/s`,
                    eta_seconds: rate ? Math.max(0, (step.total - completed) / rate) : null,
                    show_progress: step.progress !== false, paused: false,
                };
            });
            const summary = run.key === 'check'
                ? 'checked 116 tweets · 2 restored · 4 protected · 1 failed · login lookups: 2 checked, 0 restored'
                : run.phases.filter(step => step.key.startsWith('sync:')).map(step => `${step.title.toLowerCase()}: 1 pages, 20 tweets`).join('; ');
            const failedStep = steps.find(step => step.state === 'failed');
            return {
                version: 1, run_id: run.runId, origin: 'demo', pid: 1, title: command(run.key),
                running: !complete, success: complete ? !stopped : null, stopped,
                summary: stopped ? 'Stopped by user' : complete ? summary : '',
                started_at: run.startedAt, updated_at: run.startedAt + elapsed,
                completed_at: complete ? run.startedAt + elapsed : null, elapsed_seconds: elapsed,
                active_step: steps.find(step => step.state === 'active')?.key || null, steps,
                issues: stopped ? [{level: 'error', message: 'Stopped by user', count: 1, step: failedStep?.key || null}] : [],
            };
        }

        function record(snapshot) {
            return {run_id: snapshot.run_id, title: snapshot.title, origin: 'demo',
                status: snapshot.running ? 'running' : snapshot.stopped ? 'stopped' : 'completed',
                success: snapshot.success, started_at: snapshot.started_at, completed_at: snapshot.completed_at,
                exit_code: snapshot.running ? null : snapshot.stopped ? 1 : 0, summary: snapshot.summary};
        }

        function log(snapshot) {
            const lines = logs.get(snapshot.run_id) || [], seen = new Set(lines);
            const append = line => { if (!seen.has(line)) { lines.push(line); seen.add(line); } };
            append(notice); append(`${snapshot.title}: started`);
            for (const step of snapshot.steps) {
                if (step.state === 'pending') continue;
                if (step.state === 'skipped') { append(`${step.title}: ${step.summary}`); continue; }
                append(`${step.title}: starting · total=${step.total} ${step.unit}`);
                if (step.state === 'active') append(`${step.title}: progress · ${step.completed}/${step.total} ${step.unit} · ${step.activity}${step.counters ? ' · ' + step.counters : ''}`);
                else append(`${step.title}: ${step.state} · ${step.summary} · elapsed ${step.elapsed_seconds.toFixed(0)}s`);
            }
            if (!snapshot.running) append(`${snapshot.title}: ${snapshot.stopped ? 'stopped' : 'complete'} · ${snapshot.summary}`);
            logs.set(snapshot.run_id, lines);
            return lines.join('\n') + '\n';
        }

        // Every visible state is derived from the same clock, including delayed/background polls.
        function advance() {
            while (active) {
                const current = snapshot(active, now());
                log(current);
                if (current.running) return current;
                history.unshift(current); lastActivity = current; active = null;
                if (queue.length) active = makeRun(queue.shift(), current.completed_at, `demo-${++serial}-${current.completed_at}`);
            }
            return null;
        }

        const seedEnd = now() - 600;
        const syncSeed = snapshot(makeRun('sync', seedEnd - durations.sync, 'demo-archive-replay'), seedEnd);
        const checkSeed = snapshot(makeRun('check', seedEnd - durations.sync - 600 - durations.check, 'demo-check-replay'), seedEnd);
        history.push(syncSeed, checkSeed);
        history.forEach(log);
        if (seedLastActivity) lastActivity = syncSeed;

        function status() {
            const current = advance();
            return {active: Boolean(current), snapshot: current, last_snapshot: lastActivity};
        }

        function jobs() {
            const current = advance(), syncSchedule = schedule();
            return {
                jobs: specs.filter(job => job.key !== 'tagging').map(spec => {
                    const saved = history.find(run => run.title === spec.command) || null;
                    const available = ['sync', 'check'].includes(spec.key);
                    const position = queue.indexOf(spec.key);
                    const inSync = active?.key === 'sync' && current?.steps.some(step => step.key === spec.sync_step && step.state === 'active');
                    return {...copy(spec), state: active?.key === spec.key ? 'running' : position >= 0 ? 'queued' : inSync ? 'in_sync' : saved ? 'ok' : 'never',
                        available, unavailable_reason: available ? null : 'Demo replay is available for Sync and Availability check',
                        last: saved ? record(saved) : null, last_snapshot: saved,
                        next: {at: spec.key === 'sync' && syncSchedule.enabled ? syncSchedule.next_run_at : null,
                            label: spec.scheduled ? (spec.key === 'sync' && syncSchedule.enabled ? null : 'Off') : spec.sync_step ? 'With sync' : null},
                        queue: position >= 0 ? {position: position + 1, retry_at: null, removable: true} : null};
                }),
                running: active ? {key: active.key, run_id: active.runId, origin: 'demo', started_at: active.startedAt} : null,
                runs_this_week: history.filter(run => run.started_at >= now() - 7 * 86400).length + (current ? 1 : 0),
                now: now(), archive: {uploaded: false, filename: null, size: null},
            };
        }

        function start(key, options = {}) {
            advance();
            if (!specFor(key)) return {error: 'Unknown job.', status: 404};
            if (!['sync', 'check'].includes(key)) return {error: 'Only Sync and Availability check have demo replays.', status: 409};
            if (!options || typeof options !== 'object' || Array.isArray(options) || Object.keys(options).length) return {error: 'This replay does not accept run options.', status: 422};
            if (active?.key === key || queue.includes(key)) return {error: 'That job is already running or queued.', status: 409};
            if (active) { queue.push(key); return {queued: true, key, position: queue.length, command: command(key)}; }
            active = makeRun(key, now(), `demo-${++serial}-${now()}`);
            return {started: true, kind: key, run_id: active.runId, command: command(key)};
        }

        function stop() {
            advance();
            if (!active) return {error: 'No task is running.', status: 409};
            const runId = active.runId;
            active.stoppedAt = now(); advance();
            return {stopping: true, run_id: runId};
        }

        function unqueue(key) {
            advance();
            if (!specFor(key)) return {error: 'Unknown job.', status: 404};
            const index = queue.indexOf(key);
            if (index < 0) return {error: "That job isn't waiting.", status: 409};
            queue.splice(index, 1); return {removed: true, key};
        }

        function runs() {
            const current = advance();
            return [...(current ? [current] : []), ...history].map(record);
        }

        function findRun(runId) {
            const current = advance();
            const found = current?.run_id === runId ? current : history.find(run => run.run_id === runId);
            return found ? {run: {...record(found), snapshot: copy(found)}, log: log(found)} : null;
        }

        return {status, jobs, start, stop, unqueue, runs, findRun};
    }
    global.TweetNookDemoJobs = {createReplay};
})(window);
