/* Optional in-browser regression runner for the isolated mobile media demo.
 * Load this script after app.js in a COPY of the demo index, then press Run.
 * Uses only synthetic/public fixture posts; never load it in a real archive.
 * Results explicitly distinguish a simulated per-element sound policy from an
 * actual iOS device test. Run at a phone viewport (for example 390 x 844).
 */
(() => {
    const button = document.createElement('button');
    button.textContent = 'Run media regression checks';
    button.style.cssText = 'position:fixed;z-index:1000;bottom:8px;left:8px;background:#1267a3;color:white;padding:8px;border-radius:8px';
    const output = document.createElement('pre');
    output.id = 'media-check-results';
    output.style.cssText = 'position:fixed;z-index:1001;inset:65px 8px auto;max-height:65vh;overflow:auto;background:#111;color:#eee;padding:12px;white-space:pre-wrap;font-size:12px;display:none';
    document.body.append(button, output);
    const wait = async (predicate, label, timeout = 8000) => {
        const start = performance.now();
        while (!predicate()) {
            if (performance.now() - start > timeout) throw new Error(`Timeout: ${label}`);
            await new Promise(resolve => setTimeout(resolve, 25));
        }
    };
    const assert = (condition, message) => { if (!condition) throw new Error(message); };
    const card = number => [...document.querySelectorAll('.feed-card')].find(node => node.textContent.includes(`Mobile verification ${number}`));
    const player = number => card(number)?.querySelector('video[data-video-player]');
    const position = number => scrollY + player(number).getBoundingClientRect().top - 100;
    button.onclick = async () => {
        button.disabled = true;
        output.style.display = 'none';
        const app = Alpine.$data(document.querySelector('[x-data="tweetApp()"]'));
        const originalPlay = HTMLMediaElement.prototype.play;
        const authorized = new WeakSet();
        const ids = new WeakMap(); let serial = 0;
        const id = video => { if (!ids.has(video)) ids.set(video, ++serial); return ids.get(video); };
        const authorize = event => { if (!event.detail.muted) authorized.add(event.detail.video); };
        const result = { policy: 'Simulated per-element audible permission; actual browser decoding/rendering', viewport: [innerWidth, innerHeight], handoffs: [], denied: [] };
        let sampling = true, audioOverlap = 0, blankFrames = 0, staleAudio = 0;
        let coldQuery = false, coldQueryFrames = 0;
        result.blankSamples = [];
        const sample = () => {
            if (!sampling) return;
            const videos = [...document.querySelectorAll('.feed-card video[data-video-player]')];
            const audible = videos.filter(video => !video.paused && !video.muted && video.volume > 0);
            if (audible.length > 1) audioOverlap++;
            if (audible.some(video => feedVideoVisibility(video).ratio < VIDEO_AUTOPLAY_VISIBLE_RATIO)) staleAudio++;
            for (const video of videos) {
                const poster = video.parentElement.querySelector('[data-video-poster]');
                if (feedVideoVisibility(video).ratio >= .6 && video.readyState < 2 && poster?.hidden) {
                    if (coldQuery) { coldQueryFrames++; continue; }
                    blankFrames++;
                    if (result.blankSamples.length < 10) result.blankSamples.push({post:video.closest('.feed-card').textContent.match(/verification \d+/)?.[0],src:video.getAttribute('src'),poster:poster.getAttribute('src')?.slice(0,30),ready:video.readyState});
                }
            }
            requestAnimationFrame(sample);
        };
        try {
            assert(innerWidth <= 640, 'Use a phone viewport');
            assert(app.isDemo, 'Requires the isolated public demo');
            app.searchQuery = '"Mobile verification 04"';
            await app.fetchTweets();
            await wait(() => player('046'), 'fixture 046');
            // Model Safari permission per element, including videos appended later.
            HTMLMediaElement.prototype.play = function () {
                if (this.hasAttribute('data-video-player') && !this.muted && !authorized.has(this)) {
                    result.denied.push({decoder: id(this), src: this.getAttribute('src')});
                    return Promise.reject(new DOMException('Per-element gesture required', 'NotAllowedError'));
                }
                return originalPlay.call(this);
            };
            window.addEventListener('video-audio-intent', authorize, true);
            scrollTo(0, position('041'));
            await wait(() => !player('041').paused && player('041').readyState >= 2, 'first playback');
            const decoder = player('041');
            result.decoder = id(decoder);
            authorized.add(decoder); // The following explicit unmute is the modeled gesture.
            decoder.parentElement.querySelector('[data-video-action="mute"]').click();
            await wait(() => !decoder.muted, 'unmute');
            requestAnimationFrame(sample);
            // The screenshot's bare video must already have a poster before it enters.
            scrollTo(0, position('046') - innerHeight - 200);
            await wait(() => player('046').poster.startsWith('data:image/'), 'offscreen generated poster');
            result.prepared046 = { belowViewport: Math.round(player('046').getBoundingClientRect().top - innerHeight), generated: true };
            for (const number of ['042', '046', '049', '041']) {
                const from = scrollY, to = position(number), start = performance.now();
                const duration = number === '046' ? 700 : 100;
                while (performance.now() - start < duration) {
                    scrollTo(0, from + (to - from) * Math.min(1, (performance.now() - start) / duration));
                    await new Promise(requestAnimationFrame);
                }
                scrollTo(0, position(number));
                await wait(() => !player(number).paused && !player(number).muted && player(number).readyState >= 2, `audible ${number}`);
                assert(player(number) === decoder, `Lost authorized decoder at ${number}`);
                await wait(() => player(number).parentElement.querySelector('[data-video-poster]').hidden, `presented ${number}`);
                result.handoffs.push({ number, authorizedDecoder: true, muted: decoder.muted });
            }
            // Fetching a new search starts cold; measure scrolling again after
            // its first page is mounted and the appended page is prepared.
            coldQuery = true;
            app.searchQuery = 'tag:"mobile verification"';
            await app.fetchTweets();
            await app.fetchTweets(true);
            await wait(() => player('021'), 'appended page');
            scrollTo(0, position('021') - innerHeight - 200);
            await wait(() => player('021').parentElement.querySelector('[data-video-poster]')?.complete, 'appended poster');
            coldQuery = false;
            scrollTo(0, position('021'));
            await wait(() => !player('021').paused && !player('021').muted && player('021').readyState >= 2, 'audible appended page');
            assert(player('021') === decoder, 'Appended page lost authorized decoder');
            result.newPage = true;
            // The live decoder must survive viewer transfer, controls and return.
            decoder.click();
            await wait(() => app.lightboxOpen && document.querySelector('.lightbox-stage video') === decoder, 'viewer transfer');
            assert(!decoder.paused && !decoder.muted, 'Viewer lost sound/playback');
            app.closeLightbox();
            await wait(() => !app.lightboxOpen && player('021') === decoder && !decoder.paused, 'viewer return');
            result.viewer = true;
            assert(audioOverlap === 0, `Audio overlapped for ${audioOverlap} frames`);
            assert(blankFrames === 0, `Missing poster and frame for ${blankFrames} visible frames`);
            assert(staleAudio <= 8, `Stale offscreen audio for ${staleAudio} frames`);
            Object.assign(result, { pass: true, audioOverlap, blankFrames, staleAudio, coldQueryFrames });
        } catch (error) {
            Object.assign(result, { pass: false, error: error.message, audioOverlap, blankFrames, staleAudio });
            result.players = [...document.querySelectorAll('.feed-card video[data-video-player]')].map(video => ({decoder:id(video),authorized:authorized.has(video),muted:video.muted,paused:video.paused,top:Math.round(video.getBoundingClientRect().top)}));
        } finally {
            sampling = false;
            HTMLMediaElement.prototype.play = originalPlay;
            window.removeEventListener('video-audio-intent', authorize, true);
            output.textContent = JSON.stringify(result, null, 2);
            output.style.display = 'block';
            button.disabled = false;
            button.textContent = result.pass ? 'Checks passed — hide results' : 'Retry media regression checks';
            if (result.pass) button.onclick = () => { output.style.display = 'none'; button.style.display = 'none'; };
        }
    };
})();
