// Optional integration check: provide Playwright and a Chromium executable.
// No real archive, credentials, provider APIs, or browser profile are used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const vm = require('node:vm');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../../tweetnook/web');
const defaults = vm.runInNewContext(
    fs.readFileSync(path.join(web, 'static/js/themes.js'), 'utf8') + '\n' +
    fs.readFileSync(path.join(web, 'static/js/app.js'), 'utf8') + '\ntweetApp()',
);
const tweet = {
    tweet_id: '100', text: 'Offline archive fixture', created_at: '2026-09-05T00:00:00Z',
    author: { id: '42', username: 'fixture', display_name: 'Offline fixture' },
    collections: ['bookmark'], media: [], raw_json: { card: {
        name: 'summary_large_image', binding_values: [
            { key: 'title', value: { string_value: 'Archived link card' } },
            { key: 'card_url', value: { string_value: 'https://example.com' } },
            { key: 'thumbnail_image_original', value: { image_value: { url: 'https://example.com/remote.jpg' } } },
        ],
    } },
};
const requests = [];
const unexpected = [];
const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const route = url.pathname;
    requests.push(req.url);
    let data;
    if (route === '/api/tweets') data = { tweets: [tweet], page: 1, pages: 1, total: 1 };
    else if (route === '/api/tweets/100') data = { main: tweet, parents: [], children: [], op_replies: [] };
    else if (route === '/api/automated-tagging') data = { installed: true, values: { ...defaults.automatedTagging, api_key: 'fixture-key' }, accounting: {} };
    else if (route === '/api/tags/stats') data = { tags: [] };
    else if (route === '/api/stats/latest-sync') data = {};
    else if (route === '/api/stats/enrichment-incomplete') data = { incomplete: 0 };
    else if (route === '/api/activity/status') data = { active: false };
    else if (route === '/api/activity/runs') data = { runs: [] };
    else if (route === '/api/activity/schedule') data = { config: { enabled: false, cadence: 'daily', hour: 12, minute: 0 } };
    else if (route === '/api/config/schema') data = {};
    else if (route === '/api/config/defaults') data = {};
    else if (route === '/api/config') data = { values: {}, explicit: {} };
    else if (route === '/api/setup') data = { required: false, completed: true, web: {password_configured: true}, auth: {values: {}}, archive: {ready: true} };
    else if (route === '/api/notices') data = {notices: []};
    else if (route.startsWith('/api/avatar/')) {
        res.writeHead(200, { 'Content-Type': 'image/svg+xml' });
        res.end('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>');
        return;
    }
    if (data !== undefined) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(data));
        return;
    }
    const file = route === '/' ? path.join(web, 'index.html') : path.resolve(web, '.' + route);
    if (file.startsWith(web + path.sep) && fs.existsSync(file) && fs.statSync(file).isFile()) {
        const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)];
        res.writeHead(200, { 'Content-Type': mime || 'application/octet-stream', 'Cache-Control': 'no-store' });
        fs.createReadStream(file).pipe(res);
    } else {
        if (route !== '/favicon.ico') unexpected.push(req.url);
        res.writeHead(404).end();
    }
});

(async () => {
    let browser;
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    try {
        browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE_PATH || undefined });
        const context = await browser.newContext({ viewport: { width: 1280, height: 1000 }, serviceWorkers: 'block' });
        const origin = `http://127.0.0.1:${server.address().port}`;
        const external = [];
        await context.route('**/*', route => {
            if (new URL(route.request().url()).origin === origin) return route.continue();
            external.push(route.request().url());
            return route.abort();
        });
        await context.addInitScript(() => {
            window.fontQueries = 0;
            window.queryLocalFonts = async () => {
                window.fontQueries++;
                return [{ family: 'Arial' }, { family: 'Georgia' }, { family: 'Georgia' }];
            };
        });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(origin);
        await page.getByText('Offline archive fixture', { exact: true }).first().waitFor();
        assert.equal(await page.evaluate(() => getComputedStyle(document.body).display), 'flex');
        assert.equal(await page.evaluate(() => window.Alpine.version), '3.13.3');
        assert.equal(await page.evaluate(() => window.fontQueries), 0);
        assert.equal(await page.locator('img[src^="https:"]').count(), 0);
        await page.evaluate(async () => {
            const app = Alpine.$data(document.querySelector('[x-data="tweetApp()"]'));
            app.searchQuery = 'fixture';
            await app.fetchTweets();
            await app.openThread('100');
            app.settingsTab = 'appearance';
            app.showSettingsModal = true;
        });
        await page.getByRole('button', { name: 'Load local fonts', exact: true }).click();
        assert.equal(await page.evaluate(() => window.fontQueries), 1);
        await page.locator('#display-font').click();
        const option = page.getByRole('option', { name: 'Georgia', exact: true });
        await option.waitFor();
        assert.match(await option.evaluate(el => getComputedStyle(el).fontFamily), /Georgia/);
        await option.click();
        assert.equal(await page.evaluate(() => localStorage.getItem('tvx-font')), 'local:Georgia');
        assert.equal(await page.locator('#custom-font-name').count(), 0);
        await page.locator('#display-font').press('ArrowDown');
        await page.keyboard.press('End');
        assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Georgia');
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#display-font').getAttribute('aria-expanded'), 'false');
        if (process.env.OFFLINE_SCREENSHOT) await page.locator('#display-font').screenshot({ path: process.env.OFFLINE_SCREENSHOT });
        await page.reload();
        await page.getByText('Offline archive fixture', { exact: true }).first().waitFor();
        assert.match(await page.evaluate(() => document.body.style.fontFamily), /Georgia/);
        assert.ok(requests.some(url => url.includes('q=fixture')));
        assert.ok(requests.includes('/api/tweets/100'));
        assert.ok(!requests.some(url => url.includes('/automated-tagging/models')));
        assert.deepEqual(external, []);
        assert.deepEqual(unexpected, []);
        assert.deepEqual(errors, []);
        console.log('Offline browser smoke passed: cold load, styles, feed/search/thread, font discovery/preview/keyboard/persistence; zero external requests.');
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
