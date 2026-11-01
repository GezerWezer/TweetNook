'use strict';

const assert = require('node:assert/strict');
const { appScripts } = require('./page_scripts.cjs');
const {
    browserContext,
    immediateComponent,
    loadScripts,
    mountTagDialog,
    test,
    run,
} = require('./web_harness.cjs');

function pendingDialog() {
    const context = browserContext();
    const timers = new Map();
    let timerId = 0;
    context.setTimeout = callback => { timers.set(++timerId, callback); return timerId; };
    context.clearTimeout = id => timers.delete(id);
    const requests = [];
    context.fetch = (url, options) => new Promise((resolve, reject) => {
        requests.push({ url, options, reject, resolve: response => resolve({
            json: async () => ({ media_tags: JSON.parse(options.body) }),
            ...response,
        }) });
    });
    const app = immediateComponent(loadScripts(context, appScripts(), 'tweetApp')());
    app.tweets = [
        { tweet_id: '101', media_tags: { description: 'Original A', tags: ['A'] } },
        { tweet_id: '202', media_tags: { description: 'Original B', tags: ['B'] } },
    ];
    const dialog = mountTagDialog(context, app);
    const open = id => {
        app.openTagModal(id, app.tweets.find(tweet => tweet.tweet_id === id).media_tags);
        dialog.startEditingTags();
        dialog.editableTags = ['Submitted'];
        dialog.editableDescription = '  Submitted description  ';
    };
    return { app, dialog, requests, open, timers };
}

for (const method of ['saveTags', 'deleteTags']) {
    for (const reopenedId of ['101', '202']) {
        for (const outcome of ['success', 'http-error', 'network-error']) {
            test(`${method}: late ${outcome} leaves reopened post ${reopenedId}'s editor alone`, async () => {
                const { app, dialog, requests, open } = pendingDialog();
                open('101');
                const pending = dialog[method]();
                assert.equal(requests.length, 1);
                assert.equal(requests[0].url, '/api/tags/101');
                if (method === 'saveTags') {
                    assert.deepEqual(JSON.parse(requests[0].options.body), {
                        tags: ['Submitted'], description: 'Submitted description',
                    });
                }
                dialog.closeTagModal();
                open(reopenedId);
                dialog.editableTags = ['Unsaved'];
                dialog.editableDescription = 'Unsaved description';
                dialog.tagError = 'Current save error';
                dialog.tagConfirmError = 'Current delete error';
                const currentData = dialog.tagModalData;

                if (outcome === 'network-error') requests[0].reject(new Error('offline'));
                else requests[0].resolve({ ok: outcome === 'success', status: 503 });
                await pending;

                assert.equal(dialog.tagModalOpen, true);
                assert.equal(dialog.tagModalTweetId, reopenedId);
                assert.equal(dialog.tagModalData, currentData);
                assert.equal(dialog.isEditingTags, true);
                assert.deepEqual(Array.from(dialog.editableTags), ['Unsaved']);
                assert.equal(dialog.editableDescription, 'Unsaved description');
                assert.equal(dialog.tagError, 'Current save error');
                assert.equal(dialog.tagConfirmError, 'Current delete error');
                assert.equal(dialog.tagSaving, false);
                assert.deepEqual(app.tweets[1].media_tags, { description: 'Original B', tags: ['B'] });
                if (outcome === 'success') {
                    if (method === 'deleteTags') assert.equal(app.tweets[0].media_tags, null);
                    else assert.deepEqual(JSON.parse(JSON.stringify(app.tweets[0].media_tags)), {
                        description: 'Submitted description', tags: ['Submitted'],
                    });
                } else {
                    assert.deepEqual(app.tweets[0].media_tags, { description: 'Original A', tags: ['A'] });
                }
            });
        }
    }

    test(`${method}: old completion cannot clear a new post's pending save`, async () => {
        const { app, dialog, requests, open } = pendingDialog();
        open('101');
        const first = dialog[method]();
        dialog.closeTagModal();
        open('202');
        dialog.editableTags = ['Second'];
        dialog.editableDescription = 'Second description';
        const second = dialog.saveTags();
        assert.equal(dialog.tagSaving, true);

        requests[0].resolve({ ok: true });
        await first;
        assert.equal(dialog.tagSaving, true);
        assert.equal(dialog.isEditingTags, true);
        assert.equal(dialog.tagModalOpen, true);

        requests[1].resolve({ ok: true });
        await second;
        assert.equal(dialog.tagSaving, false);
        assert.equal(dialog.isEditingTags, false);
        assert.equal(dialog.tagModalOpen, true);
        assert.deepEqual(JSON.parse(JSON.stringify(app.tweets[1].media_tags)), {
            tags: ['Second'], description: 'Second description',
        });
    });

    test(`${method}: dismissed request cannot restore dialog state`, async () => {
        const { dialog, requests, open, timers } = pendingDialog();
        open('101');
        const pending = dialog[method]();
        dialog.closeTagModal();
        for (const callback of timers.values()) callback();
        requests[0].resolve({ ok: true });
        await pending;
        assert.equal(dialog.tagModalOpen, false);
        assert.equal(dialog.tagModalTweetId, null);
        assert.equal(dialog.tagModalData, null);
        assert.equal(dialog.tagError, '');
        assert.equal(dialog.tagConfirmError, '');
    });

    test(`${method}: a current failure keeps the editor and reports its error`, async () => {
        const { app, dialog, requests, open } = pendingDialog();
        open('101');
        if (method === 'deleteTags') dialog.requestDeleteTags();
        const pending = dialog[method]();
        requests[0].resolve({ ok: false, status: 503 });
        await pending;
        assert.equal(dialog.tagModalOpen, true);
        assert.equal(dialog.tagModalTweetId, '101');
        assert.equal(dialog.tagSaving, false);
        assert.deepEqual(Array.from(dialog.editableTags), ['Submitted']);
        assert.equal(dialog.editableDescription, '  Submitted description  ');
        assert.deepEqual(app.tweets[0].media_tags, { description: 'Original A', tags: ['A'] });
        if (method === 'saveTags') assert.match(dialog.tagError, /Couldn’t save/);
        else {
            assert.equal(dialog.tagDeleteConfirm, true);
            assert.match(dialog.tagConfirmError, /Couldn’t delete/);
        }
    });
}

test('save completion reflects the saved response even if editor state changes in flight', async () => {
    const { app, dialog, requests, open } = pendingDialog();
    open('101');
    const pending = dialog.saveTags();
    dialog.editableTags.push('Later edit');
    requests[0].resolve({ ok: true });
    await pending;
    assert.deepEqual(Array.from(app.tweets[0].media_tags.tags), ['Submitted']);
    assert.deepEqual(Array.from(dialog.tagModalData.tags), ['Submitted']);
    assert.equal(dialog.tagSaving, false);
});

test('save displays the backend’s casefolded tags throughout the loaded post', async () => {
    const { app, dialog, requests, open } = pendingDialog();
    open('101');
    dialog.editableTags = ['Straße', 'STRASSE', 'Σ', 'ς'];
    const pending = dialog.saveTags();
    requests[0].resolve({
        ok: true,
        json: async () => ({ media_tags: { tags: ['Straße', 'Σ'], description: 'Saved description' } }),
    });
    await pending;
    assert.deepEqual(Array.from(app.tweets[0].media_tags.tags), ['Straße', 'Σ']);
    assert.deepEqual(Array.from(dialog.tagModalData.tags), ['Straße', 'Σ']);
    assert.deepEqual(Array.from(dialog.editableTags), ['Straße', 'Σ']);
    assert.equal(dialog.editableDescription, 'Saved description');
    assert.equal(dialog.tagIsDirty(), false);
    assert.equal(dialog.isEditingTags, false);
});

test('an empty saved response clears the loaded tags and closes the dialog', async () => {
    const { app, dialog, requests, open } = pendingDialog();
    open('101');
    dialog.editableTags = [];
    dialog.editableDescription = '';
    const pending = dialog.saveTags();
    requests[0].resolve({ok: true, json: async () => ({media_tags: null})});
    await pending;
    assert.equal(app.tweets[0].media_tags, null);
    assert.equal(dialog.tagModalOpen, false);
});

run();
