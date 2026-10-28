'use strict';

const assert = require('node:assert/strict');
const { appScripts, pageHtml } = require('./page_scripts.cjs');
const {
    browserContext,
    immediateComponent,
    loadScripts,
    test,
    run,
} = require('./web_harness.cjs');

test('automated tagging has a dedicated conditional Settings page', () => {
    const html = pageHtml();
    assert.match(html, /x-show="automatedTaggingInstalled"/);
    assert.match(html, /id="nook-nav-automated-tagging"[^>]*x-show="automatedTaggingInstalled"|x-show="automatedTaggingInstalled"[^>]*id="nook-nav-automated-tagging"/);
    assert.match(html, /@click="nookSettingsNavigate\(\$data, 'automated-tagging'\)"/);
    assert.match(html, /x-show="settingsTab === 'automated-tagging' && automatedTaggingInstalled"/);
    assert.match(html, /automatedTaggingContextItems/);
    assert.match(html, /handleAutomatedTaggingContextKeydown\(\$event\)/);
    const automatedTaggingStart = html.indexOf('<!-- Optional direct-Gemini automated tagging -->');
    const automatedTaggingEnd = html.indexOf('<!-- Config Section -->');
    const automatedTaggingHtml = html.slice(automatedTaggingStart, automatedTaggingEnd);
    assert.match(automatedTaggingHtml, /<fieldset[^>]*:disabled="!automatedTagging\.enabled"/);
    assert.match(automatedTaggingHtml, /<input type="checkbox" role="switch" aria-label="Enable automated tagging" x-model="automatedTagging\.enabled"/);
    assert.match(automatedTaggingHtml, /<input type="checkbox" x-model="automatedTagging\.google_search"/);
    assert.match(automatedTaggingHtml, /<select[^>]*x-model="automatedTagging\.model"/);
    assert.match(automatedTaggingHtml, /<option[^>]*:value="model\.id"[^>]*x-text="model\.name"/);
    assert.match(automatedTaggingHtml, /loadAutomatedTaggingModels\(true\)/);
    assert.match(automatedTaggingHtml, /automatedTagging\.thinking_level = level\.key/);
    assert.match(automatedTaggingHtml, /automatedTagging\.processing_tier = 'standard'/);
    assert.match(automatedTaggingHtml, /automatedTagging\.processing_tier = 'flex'/);
    assert.match(automatedTaggingHtml, /x-model="automatedTaggingSpendAmount"/);
    assert.match(automatedTaggingHtml, /<select[^>]*x-model="automatedTaggingSpendPeriod"/);
    assert.match(html, /x-model\.number="automatedTagging\.free_batch_size"/);
    assert.match(html, /x-model="automatedTaggingInstructionInput"/);
    assert.match(html, /addAutomatedTaggingInstruction\(\)/);
    assert.match(html, /removeAutomatedTaggingInstruction\(item\)/);
    assert.match(html, /x-model="automatedTaggingTweetId"/);
    assert.match(html, /lookupAutomatedTaggingTweet\(\)/);
    assert.match(html, /@click="openAutomatedTaggingSpendHistory\(\)"/);
    assert.match(html, /automatedTaggingSpendChartData\(\)/);
    assert.match(html, /automatedTaggingSpendModels\(\)/);
});

test('automated tagging spend periods normalize to the stored daily limit', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTagging = { daily_spend_limit_usd: 2 };

    app.syncAutomatedTaggingSpendState();
    assert.equal(app.automatedTaggingSpendAmount, 2);
    app.setAutomatedTaggingSpendPeriod('week');
    assert.equal(app.automatedTaggingSpendAmount, 14);

    app.setAutomatedTaggingSpendAmount('21');
    assert.equal(app.automatedTaggingSpendAmount, 21);
    assert.equal(app.automatedTagging.daily_spend_limit_usd, 3);
    app.setAutomatedTaggingSpendPeriod('month');
    assert.equal(app.automatedTaggingSpendAmount, 90);
});

test('automated tagging spend history summarizes ranges, chart groups, and models', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTaggingAccounting = {
        spend_history: [
            { date: '2026-08-14', estimated_cost_usd: '0.001', requests: 1 },
            { date: '2026-08-15', estimated_cost_usd: '0.029', requests: 2 },
        ],
        lifetime_spend_history: [
            { date: '2026-07-01', interval: 'month', estimated_cost_usd: '0.20', requests: 5 },
            { date: '2026-08-01', interval: 'month', estimated_cost_usd: '0.30', requests: 6 },
        ],
        models: {
            'gemini-flash': { estimated_cost_usd: '0.01', requests: 4 },
            'gemini-pro': { estimated_cost_usd: '0.02', requests: 1 },
        },
        tweet_types: {
            text_only: {
                estimated_cost_usd: '0.01', requests: 2, priced_requests: 2,
                tweets: 4, priced_tweets: 4, media_items: 0,
            },
            video: {
                estimated_cost_usd: '0.02', requests: 1, priced_requests: 1,
                tweets: 1, priced_tweets: 1, media_items: 2, video_items: 2,
            },
        },
    };
    app.automatedTaggingSpendRange = '7d';

    assert.equal(app.automatedTaggingSpendRangeDays(), 7);
    assert.equal(app.automatedTaggingSpendRangeTotal(), 0.03);
    assert.equal(app.automatedTaggingSpendRangeRequests(), 3);
    assert.equal(app.automatedTaggingSpendChartData().length, 2);
    assert.equal(app.automatedTaggingSpendBarHeight(app.automatedTaggingSpendChartData()[1]), '100%');
    assert.equal(app.automatedTaggingSpendModels()[0].model, 'gemini-pro');
    assert.equal(app.automatedTaggingSpendTweetTypes()[0].label, 'Text only');
    assert.equal(app.automatedTaggingSpendTweetTypes()[0].average_tweet_usd, 0.0025);
    assert.equal(app.automatedTaggingSpendTweetTypes()[1].average_request_usd, 0.02);
    assert.equal(app.automatedTaggingTweetTypeMediaLabel(app.automatedTaggingSpendTweetTypes()[1]), '2 videos');
    assert.equal(app.formatAutomatedTaggingUsd(0.001), '$0.0010');
    assert.equal(app.formatAutomatedTaggingSpendDate('2026-08-15'), 'Aug 15');
    assert.match(app.automatedTaggingSpendBarLabel(app.automatedTaggingSpendChartData()[0]), /\$0\.0010 across 1 requests/);

    app.automatedTaggingSpendRange = 'lifetime';
    assert.equal(app.automatedTaggingSpendRangeDays(), null);
    assert.equal(app.automatedTaggingSpendRangeTotal(), 0.5);
    assert.equal(app.automatedTaggingSpendRangeRequests(), 11);
    assert.equal(app.automatedTaggingSpendChartData()[0].interval, 'month');
    assert.equal(app.formatAutomatedTaggingSpendDate('2026-08-01', true, true), 'Aug 2026');
    assert.match(app.automatedTaggingSpendBarLabel(app.automatedTaggingSpendChartData()[0]), /Jul 2026/);

    let closeFocused = false;
    let buttonFocused = false;
    app.$refs = {
        automatedTaggingSpendClose: { focus() { closeFocused = true; } },
        automatedTaggingSpendButton: { focus() { buttonFocused = true; } },
    };
    app.openAutomatedTaggingSpendHistory();
    assert.equal(app.showAutomatedTaggingSpendModal, true);
    assert.equal(app.automatedTaggingSpendRange, '30d');
    assert.equal(closeFocused, true);
    app.closeAutomatedTaggingSpendHistory();
    assert.equal(app.showAutomatedTaggingSpendModal, false);
    assert.equal(buttonFocused, true);
});

test('automated tagging model refresh caches until forced', async () => {
    const context = browserContext();
    let requests = 0;
    context.fetch = async () => {
        requests += 1;
        return {
            ok: true,
            async json() {
                return { models: [{ id: 'gemini-3.6-flash', name: 'Gemini 3.6 Flash' }] };
            },
        };
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTagging = {
        api_key: '********',
        api_mode: 'free',
        processing_tier: 'standard',
        model: 'gemini-3.6-flash',
    };

    await app.loadAutomatedTaggingModels();
    await app.loadAutomatedTaggingModels();
    assert.equal(requests, 1);
    await app.loadAutomatedTaggingModels(true);
    assert.equal(requests, 2);
});

test('automated tagging context commits on Enter, comma, and Tab', () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTaggingContextInput = 'Deadlock';
    let prevented = false;
    app.handleAutomatedTaggingContextKeydown({
        key: 'Enter',
        preventDefault() {
            prevented = true;
        },
    });
    assert.equal(prevented, true);
    assert.deepEqual(Array.from(app.automatedTaggingContextItems), ['Deadlock']);
    assert.equal(app.automatedTaggingContextInput, '');

    app.automatedTaggingContextInput = 'Guilty Gear';
    prevented = false;
    app.handleAutomatedTaggingContextKeydown({
        key: ',',
        preventDefault() {
            prevented = true;
        },
    });
    assert.equal(prevented, true);
    assert.deepEqual(Array.from(app.automatedTaggingContextItems), ['Deadlock', 'Guilty Gear']);

    app.automatedTaggingContextInput = 'Rust';
    let tabPrevented = false;
    app.handleAutomatedTaggingContextKeydown({
        key: 'Tab',
        preventDefault() {
            tabPrevented = true;
        },
    });
    assert.equal(tabPrevented, false);
    assert.deepEqual(Array.from(app.automatedTaggingContextItems), ['Deadlock', 'Guilty Gear', 'Rust']);
});

test('saving automated tagging includes a context hint still in the input', async () => {
    const context = browserContext();
    const requests = [];
    context.fetch = async (url, options = {}) => {
        requests.push([url, JSON.parse(options.body)]);
        return {
            ok: true,
            async json() {
                return {
                    values: {
                        enabled: false,
                        api_key: '********',
                        tagging_context: ['Deadlock'],
                    },
                };
            },
        };
    };
    const { tweetApp } = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTagging = { enabled: false, api_key: '********', tagging_context: [] };
    app.automatedTaggingContextInput = 'Deadlock';

    await app.saveAutomatedTagging();

    assert.equal(requests.length, 1);
    assert.deepEqual(requests[0][1].values.tagging_context, ['Deadlock']);
    assert.deepEqual(Array.from(app.automatedTaggingContextItems), ['Deadlock']);
});

test('loading tagging settings never discovers provider models implicitly', async () => {
    const context = browserContext();
    const { tweetApp } = loadScripts(context, appScripts(), '({ tweetApp })');
    const component = immediateComponent(tweetApp());
    let requests = 0;
    context.fetch = async url => {
        assert.equal(url, '/api/automated-tagging');
        requests++;
        return { ok: true, json: async () => ({ installed: true, values: {
            ...component.automatedTagging, api_key: 'test-key',
        }, accounting: {} }) };
    };
    let modelRequests = 0;
    component.loadAutomatedTaggingModels = async () => { modelRequests++; };
    await component.fetchAutomatedTagging();
    assert.equal(requests, 1);
    assert.equal(modelRequests, 0);
});

test('automated tagging saves uncertainty policies and exposes their controls', async () => {
    const context = browserContext();
    const values = {
        enabled: true, api_mode: 'paid', api_key: '********', tagging_context: [],
        daily_spend_limit_usd: 0.1, unknown_billing_policy: 'stop',
        unknown_request_reserve_usd: 0.03, max_unknown_requests_per_day: 2,
        search_unknown_policy: 'reserve', max_unknown_search_requests: 4,
    };
    let submitted;
    context.fetch = async (_url, options) => {
        submitted = JSON.parse(options.body).values;
        return {ok: true, async json() {return {values: submitted};}};
    };
    const {tweetApp} = loadScripts(context, appScripts(), '({tweetApp})');
    const app = immediateComponent(tweetApp());
    app.automatedTagging = {...values};
    await app.saveAutomatedTagging();
    const html = pageHtml();
    for (const field of [
        'unknown_billing_policy', 'unknown_request_reserve_usd',
        'max_unknown_requests_per_day', 'search_unknown_policy', 'max_unknown_search_requests',
    ]) {
        assert.equal(submitted[field], values[field]);
        assert.equal(app.automatedTagging[field], values[field]);
        assert.ok(html.includes(`automatedTagging.${field}"`));
    }
    assert.ok(html.includes('actual charges may differ'));
    assert.ok(html.includes('Five is not a provider limit'));
});

run();
