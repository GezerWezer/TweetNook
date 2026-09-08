'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..', '..');
const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'demo', 'data.json'), 'utf8'));
const source = fs.readFileSync(path.join(ROOT, 'demo', 'demo-api.js'), 'utf8');

const window = {
    TWEETNOOK_DEMO: false,
    document: {baseURI: 'https://example.test/tweetnook/'},
    location: {href: 'https://example.test/tweetnook/'},
    fetch: global.fetch,
};
const context = vm.createContext({
    window,
    URL,
    Request,
    Response,
    JSON,
    Math,
    Date,
    Promise,
    setTimeout,
    clearTimeout,
});
vm.runInContext(source, context, {filename: 'demo-api.js'});

const api = window.TweetNookDemo;
const ids = {
    welcomeGuide: '202602150001',
    browseGuide: '202602150002',
    settingsGuide: '202602150003',
    ownerRoot: '202602140001',
    likedTextReply: '202602140002',
    ownerImageReply: '202602140003',
    quotedStandalone: '202602130004',
    gifReply: '202602130005',
    textQuote: '202602120007',
    gallery: '202602110010',
    fourImageGallery: '202602150004',
    likedVideoReply: '202602110011',
    gif: '202602100013',
    video: '202602090016',
    gifQuotesVideo: '202602080019',
    imageQuotesGif: '202602070022',
    textQuotesGallery: '202602060025',
    likedPlainReply: '202602060026',
    textQuotesGif: '202602050027',
    textQuotesVideo: '202602040029',
    textQuotesImage: '202602030031',
    imageQuotesText: '202602020033',
    gifQuotesText: '202602010035',
    videoQuotesText: '202601310037',
    secondOwnerText: '202601300039',
    textQuotesNestedMedia: '202601280043',
};

async function request(store, url, init) {
    const response = await store.handle(url, init);
    const body = await response.json();
    return {response, body};
}

async function resultIds(store, query, extra = '') {
    const params = new URLSearchParams({collection: 'all', sort: 'default', q: query});
    const {body} = await request(store, `https://example.test/api/tweets?${params}${extra}`);
    return body.tweets.map(tweet => tweet.tweet_id);
}

async function main() {
    const store = api.createStore(fixture);

    const allTweets = await request(
        store,
        'https://example.test/api/tweets?collection=all&sort=default&page=1&limit=100',
    );
    assert.equal(allTweets.body.total, 26);
    assert.ok(allTweets.body.tweets.every(tweet => tweet.collections.length > 0));
    assert.equal(
        allTweets.body.tweets.some(tweet => tweet.tweet_id === '202602050028'),
        false,
    );

    assert.deepEqual(await resultIds(store, '"something I tweeted"'), [ids.ownerRoot]);
    assert.deepEqual(await resultIds(store, 'tag:anything'), []);
    assert.deepEqual(await resultIds(store, '#anything'), []);
    assert.deepEqual(await resultIds(store, 'has:links'), []);

    const userOneTweets = await resultIds(store, 'from:demo_user1');
    assert.equal(userOneTweets.length, 4);
    assert.ok(userOneTweets.includes(ids.welcomeGuide));
    assert.ok(userOneTweets.includes(ids.likedTextReply));
    assert.ok(userOneTweets.includes(ids.textQuote));
    assert.ok(userOneTweets.includes(ids.textQuotesImage));

    const images = await resultIds(store, 'has:image');
    assert.ok(images.includes(ids.ownerImageReply));
    assert.ok(images.includes(ids.gallery));
    assert.ok(images.includes(ids.fourImageGallery));
    assert.ok(images.includes(ids.imageQuotesGif));
    assert.ok(images.includes(ids.imageQuotesText));

    const videos = await resultIds(store, 'has:video');
    assert.ok(videos.includes(ids.gif));
    assert.ok(videos.includes(ids.video));
    assert.ok(videos.includes(ids.gifQuotesVideo));
    assert.ok(videos.includes(ids.gifQuotesText));
    assert.ok(videos.includes(ids.videoQuotesText));

    const replies = await resultIds(store, 'is:reply');
    assert.deepEqual(replies, [
        ids.ownerImageReply,
        ids.likedTextReply,
        ids.likedVideoReply,
        ids.likedPlainReply,
    ]);
    const quoteTweets = await resultIds(store, 'is:quote');
    assert.equal(quoteTweets.length, 12);
    assert.ok(quoteTweets.includes(ids.textQuotesGallery));
    assert.ok(quoteTweets.includes(ids.textQuotesGif));
    assert.ok(quoteTweets.includes(ids.textQuotesVideo));
    assert.ok(quoteTweets.includes(ids.textQuotesImage));
    assert.ok(quoteTweets.includes(ids.imageQuotesText));
    assert.ok(quoteTweets.includes(ids.gifQuotesText));
    assert.ok(quoteTweets.includes(ids.videoQuotesText));
    assert.deepEqual(await resultIds(store, 'is:thread'), []);
    assert.deepEqual(
        await resultIds(store, 'min_faves:100'),
        [
            ids.welcomeGuide,
            ids.browseGuide,
            ids.gallery,
            ids.video,
            ids.gifQuotesVideo,
            ids.imageQuotesGif,
            ids.textQuotesVideo,
            ids.imageQuotesText,
            ids.gifQuotesText,
            ids.videoQuotesText,
            ids.textQuotesNestedMedia,
        ],
    );

    const likes = await request(store, 'https://example.test/api/tweets?collection=likes&sort=liked_latest&page=1&limit=6');
    assert.equal(likes.body.total, 15);
    assert.deepEqual(
        likes.body.tweets.map(tweet => tweet.tweet_id),
        [
            ids.welcomeGuide,
            ids.browseGuide,
            ids.settingsGuide,
            ids.gallery,
            ids.video,
            ids.fourImageGallery,
        ],
    );
    assert.equal(likes.body.pages, 3);

    const conversation = await request(store, `https://example.test/api/tweets/${ids.ownerRoot}`);
    assert.equal(conversation.body.main.tweet_id, ids.ownerRoot);
    assert.deepEqual(conversation.body.children.map(tweet => tweet.tweet_id), [ids.likedTextReply]);
    assert.deepEqual(
        conversation.body.children[0].op_replies.map(tweet => tweet.tweet_id),
        [ids.ownerImageReply],
    );

    const nestedReply = await request(store, `https://example.test/api/tweets/${ids.ownerImageReply}`);
    assert.deepEqual(
        nestedReply.body.parents.map(tweet => tweet.tweet_id),
        [ids.ownerRoot, ids.likedTextReply],
    );

    const notedTweet = await request(store, `https://example.test/api/tweets/${ids.quotedStandalone}`);
    assert.deepEqual(notedTweet.body.main.raw_json.birdwatch_pivot, {
        shorttitle: 'Readers added context',
        subtitle: {
            text: 'community notes work too!',
            entities: [],
        },
    });

    const rootTweets = fixture.tweets.filter(tweet => tweet.reply_to_id === null);
    assert.equal(rootTweets.length, 22);
    for (const [index, root] of rootTweets.entries()) {
        const detail = await request(store, `https://example.test/api/tweets/${root.tweet_id}`);
        assert.ok(detail.body.children.length >= 1);
        const opReplies = detail.body.children.flatMap(child => child.op_replies);
        if (index < 8) {
            assert.equal(opReplies.length, 1);
            assert.equal(opReplies[0].author.id, root.author_id);
        } else {
            assert.deepEqual(opReplies, []);
        }
    }

    const likedTweets = fixture.tweets.filter(tweet => tweet.collections.includes('like'));
    assert.equal(likedTweets.length, 15);
    for (const likedTweet of likedTweets) {
        const detail = await request(store, `https://example.test/api/tweets/${likedTweet.tweet_id}`);
        assert.ok(detail.body.children.some(child => (
            child.text === 'This is a text-only reply that also quotes another tweet.'
            && child.quoted_tweet
            && child.media.length === 0
        )));
        assert.ok(detail.body.children.some(child => (
            child.text === 'This is a plain-text reply without media or a quote tweet.'
            && !child.quoted_tweet
            && child.media.length === 0
        )));
    }

    const quotes = await request(store, `https://example.test/api/tweets/${ids.quotedStandalone}/quotes`);
    assert.deepEqual(
        quotes.body.tweets.map(tweet => tweet.tweet_id),
        [ids.textQuote, ids.imageQuotesText, ids.videoQuotesText],
    );
    const videoQuotes = await request(store, `https://example.test/api/tweets/${ids.video}/quotes`);
    assert.deepEqual(
        videoQuotes.body.tweets.map(tweet => tweet.tweet_id),
        [ids.gifQuotesVideo, ids.textQuotesVideo],
    );

    const authors = await request(store, 'https://example.test/api/authors/search?q=user');
    assert.deepEqual(
        authors.body.authors.map(author => author.username),
        ['demo_user1', 'demo_user2', 'demo_user3', 'demo_user4', 'demo_user5', 'demo_user6'],
    );
    const owner = await request(store, 'https://example.test/api/authors/search?q=you');
    assert.deepEqual(owner.body.authors.map(author => author.username), ['demo_you']);
    const initialTags = await request(store, 'https://example.test/api/tags/autocomplete?q=guide');
    assert.deepEqual(initialTags.body.tags, [{tag: 'guide tweet', count: 3}]);

    await request(store, `https://example.test/api/tags/${ids.ownerRoot}`, {
        method: 'PUT',
        body: JSON.stringify({tags: ['example', 'fresh'], description: 'Changed for this session.'}),
    });
    const changedTags = await request(store, 'https://example.test/api/tags/stats');
    assert.equal(changedTags.body.tags.find(tag => tag.tag === 'example').count, 1);
    await request(store, 'https://example.test/api/tags/merge', {
        method: 'POST',
        body: JSON.stringify({primary_tag: 'example', merge_tags: ['fresh']}),
    });
    assert.equal((await request(store, 'https://example.test/api/tags/stats')).body.tags.some(tag => tag.tag === 'fresh'), false);
    await request(store, 'https://example.test/api/tags/global/example', {method: 'DELETE'});
    assert.equal((await request(store, 'https://example.test/api/tags/stats')).body.tags.some(tag => tag.tag === 'example'), false);

    const freshStore = api.createStore(fixture);
    assert.deepEqual(
        (await request(freshStore, 'https://example.test/api/tags/autocomplete?q=guide')).body.tags,
        [{tag: 'guide tweet', count: 3}],
    );
    const initialStats = await request(freshStore, 'https://example.test/api/stats/snapshot');
    assert.equal(initialStats.body.summary.unique_posts, 78);
    assert.equal(initialStats.body.tags.tagged_tweets, 78);
    assert.equal(initialStats.body.tags.coverage_pct, 100);
    assert.equal((await request(freshStore, `https://example.test/api/tags/${ids.ownerRoot}`, {method: 'DELETE'})).response.status, 200);
    assert.equal(freshStore.hydrate(ids.ownerRoot).media_tags, null);

    const stats = await request(freshStore, 'https://example.test/api/stats/snapshot');
    assert.equal(stats.body.summary.unique_posts, 78);
    assert.equal(stats.body.health.enrichment.incomplete, 0);
    assert.equal(stats.body.tags.tagged_tweets, 77);
    assert.equal(stats.body.tags.coverage_pct, 99);

    const tagging = await request(freshStore, 'https://example.test/api/automated-tagging/test', {
        method: 'POST',
        body: JSON.stringify({tweet_id: ids.ownerImageReply, values: {}}),
    });
    assert.equal(tagging.body.success, true);
    assert.deepEqual(tagging.body.result.tags, ['demo', 'archive', 'photo']);

    const startedAt = 1_000_000;
    let clock = startedAt;
    const activityStore = api.createStore(fixture, {now: () => clock});
    assert.equal((await request(activityStore, 'https://example.test/api/activity/status')).body.active, false);
    assert.equal((await request(activityStore, 'https://example.test/api/activity/sync', {method: 'POST'})).response.status, 202);

    const expectedPhases = [
        [0, 'preflight:bookmarks,likes', 'Prepare'],
        [4, 'sync:bookmarks:head', 'Bookmarks'],
        [6, 'sync:likes:head', 'Likes'],
        [20, 'threads', 'Threads'],
        [90, 'resurrection', 'Resurrection'],
        [173, 'media', 'Media'],
        [178, 'urls', 'URLs'],
    ];
    const expectedStepKeys = [
        'preflight:bookmarks,likes',
        'sync:bookmarks:head',
        'sync:likes:head',
        'threads',
        'resurrection',
        'articles',
        'media',
        'urls',
    ];
    for (const [offset, key, title] of expectedPhases) {
        clock = startedAt + offset;
        const status = await request(activityStore, 'https://example.test/api/activity/status');
        assert.equal(status.body.active, true);
        assert.equal(status.body.snapshot.active_step, key);
        assert.equal(status.body.snapshot.steps.find(step => step.state === 'active').title, title);
        assert.deepEqual(status.body.snapshot.steps.map(step => step.key), expectedStepKeys);
    }

    clock = startedAt + 172;
    const afterArticles = await request(activityStore, 'https://example.test/api/activity/status');
    const articleStep = afterArticles.body.snapshot.steps.find(step => step.key === 'articles');
    assert.equal(articleStep.state, 'skipped');
    assert.equal(articleStep.summary, 'skipped due to no article rows requiring refresh');

    clock = startedAt + 180;
    const completed = await request(activityStore, 'https://example.test/api/activity/status');
    assert.equal(completed.body.active, false);
    assert.equal(completed.body.last_snapshot.elapsed_seconds, 180);
    assert.equal(completed.body.last_snapshot.success, true);
    assert.equal(completed.body.last_snapshot.steps.filter(step => step.state === 'complete').length, 7);
    assert.equal(completed.body.last_snapshot.steps.find(step => step.key === 'articles').state, 'skipped');
    assert.equal(completed.body.last_snapshot.summary, 'bookmarks: 1 pages, 20 tweets; likes: 1 pages, 20 tweets');

    clock += 10;
    assert.equal((await request(activityStore, 'https://example.test/api/activity/sync', {method: 'POST'})).response.status, 202);
    clock += 40;
    assert.equal((await request(activityStore, 'https://example.test/api/activity/stop', {method: 'POST'})).response.status, 202);
    const stopped = await request(activityStore, 'https://example.test/api/activity/status');
    assert.equal(stopped.body.active, false);
    assert.equal(stopped.body.last_snapshot.stopped, true);
    assert.equal(stopped.body.last_snapshot.elapsed_seconds, 40);
    assert.equal(stopped.body.last_snapshot.steps.find(step => step.state === 'failed').key, 'threads');
    assert.equal(stopped.body.last_snapshot.steps.filter(step => step.state === 'skipped').length, 4);
    assert.equal(
        stopped.body.last_snapshot.steps.find(step => step.state === 'skipped').summary,
        'skipped due to an earlier failure stopped the command',
    );
    assert.equal(stopped.body.last_snapshot.issues[0].message, 'Stopped by user');

    const schedule = await request(freshStore, 'https://example.test/api/activity/schedule', {
        method: 'PUT',
        body: JSON.stringify({enabled: false}),
    });
    assert.equal(schedule.body.config.enabled, false);

    console.log('demo API tests passed');
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
