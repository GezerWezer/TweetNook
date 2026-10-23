'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..', '..');
const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests', 'fixtures', 'demo-synthetic.json'), 'utf8'));
const demoConfig = JSON.parse(fs.readFileSync(path.join(ROOT, 'demo', 'config.json'), 'utf8'));
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
    TextEncoder,
    TextDecoder,
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
    const publicFixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'demo', 'data.json'), 'utf8'));
    const publicStore = api.createStore(publicFixture);
    const publicStats = (await request(publicStore, 'https://example.test/api/stats/snapshot')).body;
    assert.equal(publicStats.summary.unique_posts, 366);
    assert.equal(publicStats.summary.owner_user_id, publicFixture.owner_user_id);
    assert.equal(publicStats.summary.profiles, publicFixture.authors.length);
    assert.ok(!publicFixture.tweets.some(tweet => tweet.author_id === '1818507989307080707'));
    assert.equal(publicStats.summary.media_rows, 122);
    assert.equal(publicStats.summary.articles, 1);
    assert.equal(publicStats.summary.missing_archive_tweets, 0);
    assert.equal(publicStats.health.threads_expanded, 18);
    assert.equal(publicStats.health.enrichment.resurrected, 0);
    assert.deepEqual(publicStats.collections.map(row => row.count), [3, 11, 1]);
    const measured = publicFixture.demo_metadata;
    assert.equal(publicStats.storage.total_bytes,
        measured.fixture_bytes + Object.values(measured.asset_bytes).reduce((sum, bytes) => sum + bytes, 0));
    assert.equal(publicStats.storage.segments.reduce((sum, segment) => sum + segment.bytes, 0), publicStats.storage.total_bytes);
    assert.equal(publicStats.storage.segments.reduce((sum, segment) => sum + (segment.group === 'media' ? segment.count : 0), 0), Object.keys(measured.asset_bytes).length);
    assert.equal(publicStats.storage.media_sizes_measured, true);
    const likedPosts = publicFixture.tweets.filter(tweet => tweet.collections.includes('like'));
    assert.equal(publicStats.tags.eligible_tweets, likedPosts.length);
    assert.equal(publicStats.tags.tagged_tweets, 11);
    assert.equal(publicStats.tags.coverage_pct, 100);
    assert.equal((await request(publicStore, 'https://example.test/api/stats/latest-sync')).body.latest_sync, publicStats.summary.latest_sync);
    const taggingMetadata = measured.tagging;
    const pricedStore = api.createStore(publicFixture, {now: () => Date.parse(taggingMetadata.completed_at) / 1000});
    const paid = (await request(pricedStore, 'https://example.test/api/automated-tagging')).body.accounting;
    assert.equal(paid.lifetime_requests, 14);
    assert.equal(paid.today_requests, 14);
    assert.equal(paid.month_requests, 14);
    assert.equal(paid.lifetime_estimated_cost_usd, 0.02654775);
    assert.equal(paid.models['gemini-3.6-flash'].requests, 14);
    assert.equal(Object.values(paid.tweet_types).reduce((sum, row) => sum + row.tweets, 0), 11);
    assert.equal(Object.values(paid.tweet_types).reduce((sum, row) => sum + row.requests, 0), 14);
    assert.equal(paid.search_call_count_known, false);
    assert.match(paid.pricing_note, /Search cost is excluded/);
    assert.equal(publicStats.tags.total_tag_instances, likedPosts.reduce((sum, tweet) => sum + tweet.tags.length, 0));
    const directTags = new Map();
    for (const tweet of likedPosts) for (const tag of new Set(tweet.tags)) directTags.set(tag, (directTags.get(tag) || 0) + 1);
    assert.equal(publicStats.tags.unique_tags, directTags.size);
    assert.deepEqual(publicStats.tags.top_tags, [...directTags].map(([tag, count]) => ({tag, count}))
        .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag)).slice(0, 20));
    // Public history/progress counts come from this actual fixture, not the old synthetic archive.
    let publicReplayClock = 1000;
    const publicReplayStore = api.createStore(publicFixture, {now: () => publicReplayClock});
    await request(publicReplayStore, 'https://example.test/api/activity/sync', {method: 'POST'});
    publicReplayClock += 180;
    const publicReplay = (await request(publicReplayStore, 'https://example.test/api/activity/status')).body.last_snapshot;
    const publicStep = key => publicReplay.steps.find(step => step.key === key);
    assert.equal(publicStep('sync:bookmarks:head').total, publicFixture.tweets.filter(tweet => tweet.collections.includes('bookmark')).length);
    assert.equal(publicStep('sync:likes:head').total, publicFixture.tweets.filter(tweet => tweet.collections.includes('like')).length);
    assert.equal(publicStep('threads').total, publicFixture.tweets.filter(tweet => !tweet.collections.length).length);
    assert.equal(publicStep('articles').total, publicFixture.tweets.filter(tweet => tweet.article).length);
    assert.equal(publicStep('tagging').total, publicFixture.tweets.filter(tweet => tweet.collections.includes('like') && tweet.tags.length).length);
    assert.equal(publicStep('media').metrics.downloaded, 0);
    assert.equal(publicStep('tagging').metrics.requests, 0);
    const publicReplayLog = await (await publicReplayStore.handle('https://example.test/api/activity/runs/demo-archive-replay/log')).text();
    assert.match(publicReplayLog, /^DEMO REPLAY:/);
    assert.ok(publicReplayLog.includes(`${publicStep('threads').total} saved context tweets`));
    const publicFeed = await request(publicStore, 'https://example.test/api/tweets?collection=all&limit=100');
    assert.equal(publicFeed.body.total, publicFixture.tweets.filter(tweet => tweet.collections.length).length);
    assert.ok(publicFeed.body.tweets.some(tweet => tweet.tweet_id === '2108702633980575995'));
    const savedReplies = await request(publicStore, 'https://example.test/api/tweets?collection=bookmarks&limit=100');
    assert.equal(savedReplies.body.total, 3);
    assert.deepEqual(savedReplies.body.tweets.map(tweet => tweet.tweet_id).sort(),
        ['2097587669198934196', '2107978256003510696', '2108662185232679271']);
    assert.ok(!publicFixture.tweets.some(tweet => tweet.tweet_id === '2097889944408588708'));
    assert.ok(savedReplies.body.tweets.every(tweet => tweet.raw_json.legacy.in_reply_to_status_id_str));
    const yourPosts = await request(publicStore, 'https://example.test/api/tweets?collection=tweets&limit=100');
    assert.equal(yourPosts.body.total, 1);
    assert.equal(yourPosts.body.tweets[0].author.display_name, 'You');
    assert.equal(yourPosts.body.tweets[0].author.id, publicFixture.owner_user_id);
    const guides = publicFixture.tweets.filter(tweet => tweet.tags.includes('guide tweet'));
    assert.equal(new Set(guides.map(tweet => tweet.author_id)).size, 1);
    assert.equal(publicStore.avatarUrl(guides[0].author_id), 'demo/media/avatars/default.svg');
    const newThread = await request(publicStore, 'https://example.test/api/tweets/2108702633980575995');
    assert.equal(newThread.body.main.author.username, 'BlSCUlTS');
    assert.equal(newThread.body.main.text, 'you just freed her of the curse');
    assert.equal(newThread.body.main.quoted_tweet.tweet_id, '2108567572597911816');
    assert.equal(newThread.body.main.quoted_tweet.media.length, 3);
    const qrThread = await request(publicStore, 'https://example.test/api/tweets/2095945046193234399');
    assert.equal(qrThread.body.main.author.username, 'CornDogs27USPS');
    assert.equal(qrThread.body.main.text, 'Don’t trust QR codes. They’re hiding something.');
    assert.equal(qrThread.body.main.media.length, 0);
    assert.equal(qrThread.body.main.raw_json.birdwatch_pivot.subtitle.text, 'this is true');
    assert.ok(publicFeed.body.tweets.some(tweet => tweet.tweet_id === '2095945046193234399'));
    const neuroscienceThread = await request(publicStore, 'https://example.test/api/tweets/2097653960811094318');
    assert.equal(neuroscienceThread.body.main.author.username, 'jbohnslav');
    assert.equal(neuroscienceThread.body.main.quoted_tweet.tweet_id, '2097527368919470162');
    assert.equal(neuroscienceThread.body.main.quoted_tweet.media[0].type, 'video');
    assert.ok(neuroscienceThread.body.main.quoted_tweet.media[0].thumbnail_url.endsWith('.webp'));
    assert.ok(publicFeed.body.tweets.some(tweet => tweet.tweet_id === '2097653960811094318'));
    assert.ok(!neuroscienceThread.body.children.some(reply => reply.tweet_id === '2097683428334252331'));
    assert.ok(!publicFixture.tweets.some(tweet => tweet.tweet_id === '2097670044427706495'));
    const flyBrain = await request(publicStore, 'https://example.test/api/tweets/2097527368919470162');
    assert.ok(flyBrain.body.children.length > 0, 'embedded quoted posts have their own expanded replies');
    assert.equal(flyBrain.body.main.collections.length, 0);
    assert.ok(flyBrain.body.children.every(reply => reply.raw_json.legacy.in_reply_to_status_id_str === '2097527368919470162'));

    const practicalShotThread = await request(publicStore, 'https://example.test/api/tweets/2074192621291856126');
    assert.equal(practicalShotThread.body.main.author.username, 'YourAvgBellman');
    assert.equal(practicalShotThread.body.main.quoted_tweet.tweet_id, '2073881116818510069');
    assert.equal(practicalShotThread.body.main.quoted_tweet.media[0].type, 'animated_gif');
    assert.equal(practicalShotThread.body.main.quoted_tweet.quoted_tweet.tweet_id, '2073859461853192678');
    const deepQuote = await request(publicStore, 'https://example.test/api/tweets/2073859461853192678');
    assert.equal(deepQuote.body.main.quoted_tweet.tweet_id, '2073758292766064870');
    assert.equal(deepQuote.body.main.quoted_tweet.media[0].type, 'animated_gif');
    assert.ok(practicalShotThread.body.main.quoted_tweet.media[0].thumbnail_url.endsWith('.webp'));
    assert.ok(publicFeed.body.tweets.some(tweet => tweet.tweet_id === '2074192621291856126'));
    const klarnaThread = await request(publicStore, 'https://example.test/api/tweets/2090954221445149180');
    assert.equal(klarnaThread.body.main.author.username, 'slothy_420');
    assert.equal(klarnaThread.body.main.media[0].type, 'photo');
    assert.equal(klarnaThread.body.main.quoted_tweet.tweet_id, '2089485925503881494');
    assert.equal(klarnaThread.body.main.quoted_tweet.media[0].type, 'photo');
    const runeThread = await request(publicStore, 'https://example.test/api/tweets/2087274031749927040');
    assert.equal(runeThread.body.main.author.username, 'Zasorimm');
    assert.equal(runeThread.body.main.media[0].type, 'photo');
    assert.equal(runeThread.body.main.quoted_tweet.tweet_id, '2087170761823781046');
    assert.equal(runeThread.body.main.quoted_tweet.media.length, 0);
    assert.ok(publicFeed.body.tweets.some(tweet => tweet.tweet_id === '2090954221445149180'));
    assert.ok(publicFeed.body.tweets.some(tweet => tweet.tweet_id === '2087274031749927040'));
    const electronics = await request(publicStore, 'https://example.test/api/tweets/2012988611570933773');
    assert.equal(electronics.body.main.author.username, 'i2cjak');
    assert.equal(electronics.body.main.article.title, "i2cjak's authoritative guide to learning electronics");
    assert.equal(electronics.body.main.article.status, 'body_present');
    assert.equal(electronics.body.main.article.content_state.blocks.length, 110);
    assert.equal(electronics.body.main.article.media.length, 20);
    assert.equal(electronics.body.main.media.length, 0);
    assert.equal(electronics.body.children.length, 20);
    assert.ok(publicFeed.body.tweets.some(tweet => tweet.tweet_id === '2012988611570933773'));
    const realThread = await request(publicStore, 'https://example.test/api/tweets/1794948623257440377');
    assert.equal(realThread.body.main.author.username, 'aleksliving');
    assert.equal(realThread.body.main.media.length, 3);
    assert.equal(realThread.body.children.length, 23);
    assert.equal(realThread.body.main.raw_json.core.user_results.result.legacy.description, 'whimsy maxxing');
    const realProfile = await request(publicStore, 'https://example.test/api/authors/profile?username=aleksliving');
    assert.equal(realProfile.body.snapshots[0].description, 'whimsy maxxing');
    assert.notEqual(realProfile.body.snapshots[0].fetched_at, publicFixture.generated_at);
    const historyAuthor = publicFixture.authors.find(author => author.profile_snapshots?.length > 1);
    assert.ok(historyAuthor, 'real captured profile history is published');
    const history = await request(publicStore, `https://example.test/api/authors/profile?author_id=${historyAuthor.id}`);
    assert.deepEqual(history.body.snapshots, historyAuthor.profile_snapshots);
    const guide = await request(publicStore, `https://example.test/api/tweets/${ids.welcomeGuide}`);
    assert.equal(guide.body.main.text, publicFixture.tweets[0].text);
    assert.deepEqual(guide.body.children, []);

    const store = api.createStore(fixture);

    const allTweets = await request(
        store,
        'https://example.test/api/tweets?collection=all&sort=default&page=1&limit=100',
    );
    assert.equal(allTweets.body.total, 35);
    assert.equal(allTweets.body.has_more, false);
    assert.ok(allTweets.body.tweets.every(tweet => tweet.collections.length > 0));
    const articleRows = await request(store, 'https://example.test/api/tweets?collection=all&q=has:article&limit=100');
    assert.equal(articleRows.body.total, 3);
    const article = await request(store, 'https://example.test/api/tweets/202602160001');
    assert.equal(article.body.main.article.content_state.blocks[0].type, 'unstyled');
    assert.equal(article.body.main.media.length, 0);
    const articleQuote = await request(store, 'https://example.test/api/tweets/202602160003');
    assert.equal(articleQuote.body.main.media[0].type, 'video');
    assert.equal(articleQuote.body.main.quoted_tweet.article.title, article.body.main.article.title);
    assert.equal(articleQuote.body.main.qt_media.length, 0);
    const nestedArticle = await request(store, 'https://example.test/api/tweets/202602160007');
    assert.equal(nestedArticle.body.main.quoted_tweet.quoted_tweet.article.status, 'body_present');
    const previewArticle = await request(store, 'https://example.test/api/tweets/202602160005');
    assert.equal(previewArticle.body.main.article.content_text, null);
    assert.equal(previewArticle.body.main.article.status, 'preview_only');
    const mixedSearch = await request(store, 'https://example.test/api/tweets?collection=all&q=gif%20is:reply');
    const mixedCount = await request(store, 'https://example.test/api/tweets/count?collection=all&q=gif%20is:reply');
    assert.deepEqual(mixedSearch.body.highlight_terms, ['gif']);
    assert.equal(mixedCount.body.total, mixedSearch.body.total);
    assert.equal(
        allTweets.body.tweets.some(tweet => tweet.tweet_id === '202602050028'),
        false,
    );

    assert.deepEqual(await resultIds(store, '"something I tweeted"'), [ids.ownerRoot]);
    assert.deepEqual(await resultIds(store, 'tag:anything'), []);
    const fishDescriptions = await resultIds(store, 'description:"Board fish"');
    assert.deepEqual(fishDescriptions, [ids.gifQuotesVideo]);
    assert.deepEqual(await resultIds(store, 'description:fis*'), fishDescriptions);
    assert.deepEqual(await resultIds(store, 'description:"fish cat"'), []);
    assert.equal((await resultIds(store, '-description:fish')).includes(ids.gifQuotesVideo), false);
    assert.deepEqual(await resultIds(store, '#anything'), []);
    assert.deepEqual(await resultIds(store, 'has:links'), []);

    assert.ok((await resultIds(store, 'has:note')).includes(ids.quotedStandalone));
    const filterFixture = JSON.parse(JSON.stringify(fixture));
    const filterPost = id => filterFixture.tweets.find(tweet => tweet.tweet_id === id);
    filterPost(ids.quotedStandalone).community_note = null;
    filterPost(ids.quotedStandalone).note_history = true;
    filterPost(ids.ownerRoot).uncaptured = true;
    const filterStore = api.createStore(filterFixture);
    const pastNotes = await resultIds(filterStore, 'has:note');
    assert.ok(pastNotes.includes(ids.quotedStandalone));
    const quotingPost = filterFixture.tweets.find(tweet => tweet.quoted_tweet_id === ids.quotedStandalone && tweet.collections.length);
    assert.ok(quotingPost);
    assert.ok(pastNotes.includes(quotingPost.tweet_id));
    assert.ok(!(await resultIds(filterStore, '-has:note')).includes(quotingPost.tweet_id));
    assert.ok(!(await resultIds(filterStore, '')).includes(ids.ownerRoot));
    assert.deepEqual(await resultIds(filterStore, 'is:uncaptured'), [ids.ownerRoot]);
    assert.ok((await resultIds(filterStore, 'is:uncaptured OR has:note')).includes(ids.ownerRoot));
    assert.ok(!(await resultIds(filterStore, '-is:uncaptured')).includes(ids.ownerRoot));
    const uncapturedCount = await request(filterStore, 'https://example.test/api/tweets/count?q=is:uncaptured');
    assert.equal(uncapturedCount.body.total, 1);

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
    assert.equal(quoteTweets.length, 18);
    assert.ok(quoteTweets.includes(ids.textQuotesGallery));
    assert.ok(quoteTweets.includes(ids.textQuotesGif));
    assert.ok(quoteTweets.includes(ids.textQuotesVideo));
    assert.ok(quoteTweets.includes(ids.textQuotesImage));
    assert.ok(quoteTweets.includes(ids.imageQuotesText));
    assert.ok(quoteTweets.includes(ids.gifQuotesText));
    assert.ok(quoteTweets.includes(ids.videoQuotesText));
    // A quote's own quote attaches one level deeper, with its media; no further.
    const nestedQuote = allTweets.body.tweets.find(tweet => tweet.tweet_id === '202602130101').quoted_tweet;
    assert.equal(nestedQuote.tweet_id, '202602050027');
    assert.equal(nestedQuote.quoted_tweet.tweet_id, '202602100013');
    assert.deepEqual(nestedQuote.qt_media.map(media => media.type), ['animated_gif']);
    assert.equal(nestedQuote.quoted_tweet.quoted_tweet, undefined);
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
    assert.equal(likes.body.total, 18);
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
        title: 'Readers added context they thought people might want to know',
        shorttitle: 'Readers added context',
        subtitle: {
            text: 'community notes work too!',
            entities: [],
        },
    });

    const videoNote = await request(store, `https://example.test/api/tweets/${ids.video}`);
    assert.equal(videoNote.body.main.raw_json.birdwatch_pivot, undefined);

    const rootTweets = fixture.tweets.filter(tweet => tweet.reply_to_id === null);
    assert.equal(rootTweets.length, 31);
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
    assert.equal(likedTweets.length, 18);
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
    const profile = await request(store, 'https://example.test/api/authors/profile?username=@Demo_User1');
    assert.equal(profile.body.author_id, '910000000000000002');
    assert.deepEqual(
        profile.body.snapshots.map(snapshot => [snapshot.username, snapshot.followers_count]),
        [['demo_user1', 842]],
    );
    const missingProfile = await request(store, 'https://example.test/api/authors/profile?author_id=0');
    assert.deepEqual(missingProfile.body, {author_id: null, snapshots: []});
    const initialTags = await request(store, 'https://example.test/api/tags/autocomplete?q=guide');
    assert.deepEqual(initialTags.body.tags, [{tag: 'guide tweet', count: 3}]);

    await request(store, `https://example.test/api/tags/${ids.ownerRoot}`, {
        method: 'PUT',
        body: JSON.stringify({tags: ['REM (Deadlock)', 'REM (Sleep)', '100%_literal [tag]']}),
    });
    for (const query of ['rem D', ' D\tREM ', 'em dead']) {
        const matches = await request(store, `https://example.test/api/tags/autocomplete?q=${encodeURIComponent(query)}`);
        assert.deepEqual(matches.body.tags, [{tag: 'REM (Deadlock)', count: 1}]);
    }
    const literalTags = await request(store, `https://example.test/api/tags/autocomplete?q=${encodeURIComponent('%_ [')}`);
    assert.deepEqual(literalTags.body.tags, [{tag: '100%_literal [tag]', count: 1}]);
    const absentTags = await request(store, 'https://example.test/api/tags/autocomplete?q=rem%20absent');
    assert.deepEqual(absentTags.body.tags, []);

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

    const hideUrl = `https://example.test/api/tweets/${ids.ownerRoot}/hidden`;
    assert.deepEqual((await request(store, hideUrl, {method: 'PUT'})).body, {hidden: true});
    assert.equal((await resultIds(store, '')).includes(ids.ownerRoot), false);
    assert.deepEqual(await resultIds(store, 'is:hidden'), [ids.ownerRoot]);
    assert.equal((await resultIds(store, '-is:hidden')).includes(ids.ownerRoot), false);
    assert.equal(
        (await request(store, `https://example.test/api/tweets/${ids.ownerRoot}`)).body.main.hidden,
        true,
    );
    await request(store, hideUrl, {method: 'DELETE'});
    assert.equal((await resultIds(store, '')).includes(ids.ownerRoot), true);
    assert.deepEqual(await resultIds(store, 'is:hidden'), []);

    const freshStore = api.createStore(fixture);
    assert.deepEqual(
        (await request(freshStore, 'https://example.test/api/tags/autocomplete?q=guide')).body.tags,
        [{tag: 'guide tweet', count: 3}],
    );
    const initialStats = await request(freshStore, 'https://example.test/api/stats/snapshot');
    assert.equal(initialStats.body.summary.unique_posts, 99);
    assert.equal(initialStats.body.summary.articles, 3);
    assert.equal(initialStats.body.summary.missing_archive_tweets, 0);
    assert.deepEqual(initialStats.body.health.enrichment.unavailable.reasons, []);
    assert.equal(initialStats.body.health.enrichment.resurrected, 0);
    assert.equal(initialStats.body.storage.media_sizes_measured, false);
    const likedFixture = fixture.tweets.filter(tweet => tweet.collections.includes('like'));
    assert.equal(initialStats.body.tags.eligible_tweets, likedFixture.length);
    assert.equal(initialStats.body.tags.tagged_tweets, likedFixture.length);
    assert.equal(initialStats.body.tags.coverage_pct, 100);
    assert.equal(initialStats.body.tags.scope, 'Liked demo posts');
    assert.equal((await request(freshStore, `https://example.test/api/tags/${ids.ownerRoot}`, {method: 'DELETE'})).response.status, 200);
    assert.equal(freshStore.hydrate(ids.ownerRoot).media_tags, null);

    const stats = await request(freshStore, 'https://example.test/api/stats/snapshot');
    assert.equal(stats.body.summary.unique_posts, 99);
    assert.equal(stats.body.health.enrichment.incomplete, 0);
    assert.equal(stats.body.tags.tagged_tweets, likedFixture.length);
    assert.equal(stats.body.tags.coverage_pct, 100);

    const tagging = await request(freshStore, 'https://example.test/api/automated-tagging/test', {
        method: 'POST',
        body: JSON.stringify({tweet_id: ids.ownerImageReply, values: {}}),
    });
    assert.equal(tagging.body.success, true);
    assert.deepEqual(tagging.body.result.tags, ['demo', 'archive', 'photo']);

    const startedAt = 1_000_000;
    let clock = startedAt;
    const seededActivityStore = api.createStore(fixture, {
        now: () => clock,
        seedLastActivity: true,
    });
    const seededStatus = await request(
        seededActivityStore,
        'https://example.test/api/activity/status',
    );
    assert.equal(seededStatus.body.active, false);
    assert.equal(seededStatus.body.last_snapshot.success, true);
    assert.equal(seededStatus.body.last_snapshot.steps.find(step => step.key === 'media').metrics.downloaded, 0);

    const activityStore = api.createStore(fixture, {now: () => clock});
    assert.equal((await request(activityStore, 'https://example.test/api/activity/status')).body.active, false);
    assert.equal((await request(activityStore, 'https://example.test/api/activity/sync', {method: 'POST'})).response.status, 202);

    const replayCounts = {
        bookmarks: fixture.tweets.filter(tweet => tweet.collections.includes('bookmark')).length,
        likes: fixture.tweets.filter(tweet => tweet.collections.includes('like')).length,
        context: fixture.tweets.filter(tweet => !tweet.collections.length).length,
        articles: fixture.tweets.filter(tweet => tweet.article).length,
        taggedLikes: fixture.tweets.filter(tweet => tweet.collections.includes('like') && tweet.tags.length).length,
        media: new Set(fixture.tweets.flatMap(tweet => [...(tweet.media || []), ...(tweet.article?.media || [])])
            .flatMap(media => [media.url, media.thumbnail_url, media.poster_url]).filter(Boolean)).size,
    };
    const expectedPhases = [
        [0, 'preflight:bookmarks,likes', 'Prepare'],
        [4, 'sync:bookmarks:head', 'Bookmarks'],
        [10, 'sync:likes:head', 'Likes'],
        [20, 'threads', 'Threads'],
        [80, 'articles', 'Articles'],
        [100, 'media', 'Media'],
        [175, 'tagging', 'Automated tags'],
    ];
    const expectedStepKeys = [
        'preflight:bookmarks,likes', 'sync:bookmarks:head', 'sync:likes:head',
        'threads', 'resurrection', 'articles', 'media', 'urls', 'tagging',
    ];
    for (const [offset, key, title] of expectedPhases) {
        clock = startedAt + offset;
        const status = await request(activityStore, 'https://example.test/api/activity/status');
        assert.equal(status.body.active, true);
        assert.equal(status.body.snapshot.active_step, key);
        assert.equal(status.body.snapshot.steps.find(step => step.state === 'active').title, title);
        assert.deepEqual(status.body.snapshot.steps.map(step => step.key), expectedStepKeys);
        assert.equal(status.body.snapshot.origin, 'demo');
    }

    clock = startedAt + 4;
    const bookmarks = await request(activityStore, 'https://example.test/api/activity/status');
    const bookmarkStep = bookmarks.body.snapshot.steps.find(step => step.key === 'sync:bookmarks:head');
    assert.equal(bookmarkStep.activity, 'Reading saved bookmarks');
    assert.equal(bookmarkStep.total, replayCounts.bookmarks);
    assert.match(bookmarkStep.detail, /Demo replay.*no network requests/);

    clock = startedAt + 100;
    const media = await request(activityStore, 'https://example.test/api/activity/status');
    assert.equal(media.body.snapshot.steps.find(step => step.key === 'threads').summary,
        `${replayCounts.context} saved context tweets`);
    const recoveryStep = media.body.snapshot.steps.find(step => step.key === 'resurrection');
    assert.equal(recoveryStep.state, 'skipped');
    assert.equal(recoveryStep.reason, 'no unavailable tweets in the demo');
    assert.equal(media.body.snapshot.steps.find(step => step.key === 'articles').metrics.already_saved,
        replayCounts.articles);

    clock = startedAt + 180;
    const completed = await request(activityStore, 'https://example.test/api/activity/status');
    assert.equal(completed.body.active, false);
    assert.equal(completed.body.last_snapshot.elapsed_seconds, 180);
    assert.equal(completed.body.last_snapshot.success, true);
    assert.equal(completed.body.last_snapshot.steps.filter(step => step.state === 'complete').length, 7);
    assert.equal(completed.body.last_snapshot.summary,
        `Demo replay · ${replayCounts.bookmarks} bookmarks · ${replayCounts.likes} likes · ${replayCounts.context} context tweets`);
    assert.equal(completed.body.last_snapshot.steps.find(step => step.key === 'sync:bookmarks:head').metrics.new_tweets, 0);
    assert.equal(completed.body.last_snapshot.steps.find(step => step.key === 'sync:likes:head').metrics.tweets_seen, replayCounts.likes);
    const completedMedia = completed.body.last_snapshot.steps.find(step => step.key === 'media');
    assert.equal(completedMedia.metrics.downloaded, 0);
    assert.equal(completedMedia.metrics.downloaded_bytes, 0);
    assert.equal(completedMedia.metrics.already_saved, replayCounts.media);
    const completedTags = completed.body.last_snapshot.steps.find(step => step.key === 'tagging');
    assert.equal(completedTags.metrics.already_tagged, replayCounts.taggedLikes);
    assert.equal(completedTags.metrics.requests, 0);
    assert.equal(completed.body.last_snapshot.steps.every(step => step.paused === false), true);

    clock += 10;
    assert.equal((await request(activityStore, 'https://example.test/api/activity/sync', {method: 'POST'})).response.status, 202);
    clock += 50;
    assert.equal((await request(activityStore, 'https://example.test/api/activity/stop', {method: 'POST'})).response.status, 202);
    const stopped = await request(activityStore, 'https://example.test/api/activity/status');
    assert.equal(stopped.body.active, false);
    assert.equal(stopped.body.last_snapshot.stopped, true);
    assert.equal(stopped.body.last_snapshot.elapsed_seconds, 50);
    assert.equal(stopped.body.last_snapshot.steps.find(step => step.state === 'failed').key, 'threads');
    assert.equal(stopped.body.last_snapshot.steps.find(step => step.state === 'failed').completed,
        Math.floor(replayCounts.context * 37 / 62));
    assert.equal(stopped.body.last_snapshot.steps.filter(step => step.state === 'skipped').length, 5);
    assert.equal(stopped.body.last_snapshot.issues[0].message, 'Stopped by user');
    assert.equal(stopped.body.last_snapshot.issues[0].step, 'threads');

    // Replay logs grow monotonically, preserving byte offsets between polls.
    clock += 10;
    const logStore = api.createStore(fixture, {now: () => clock});
    const logRun = (await request(logStore, 'https://example.test/api/activity/sync', {method: 'POST'})).body.run_id;
    const readLive = async offset => {
        const response = await logStore.handle(`https://example.test/api/activity/runs/${logRun}/log?offset=${offset}`);
        return {text: await response.text(), next: Number(response.headers.get('X-Log-Offset'))};
    };
    clock += 20;
    const early = await readLive(0);
    clock += 40;
    const later = await readLive(early.next);
    const whole = await (await logStore.handle(`https://example.test/api/activity/runs/${logRun}/log`)).text();
    assert.equal(early.text + later.text, whole);
    assert.match(early.text, /^DEMO REPLAY:.*no Twitter\/X or model requests/m);
    assert.match(early.text, new RegExp(`^Threads: progress · \\d+/${replayCounts.context} context tweets`, 'm'));
    assert.match(later.text, new RegExp(`^Threads: progress · \\d+/${replayCounts.context} context tweets`, 'm'));

    // History contains honest fixture replays, with no fabricated failed/import runs.
    const runs = await request(activityStore, 'https://example.test/api/activity/runs?limit=100');
    assert.equal(runs.body.runs[0].run_id, stopped.body.last_snapshot.run_id);
    assert.equal(runs.body.runs[0].status, 'stopped');
    assert.equal(runs.body.runs.length, 3);
    assert.ok(runs.body.runs.every(run => run.run_id && run.title.startsWith('tweetnook ') && run.started_at));
    assert.ok(runs.body.runs.every(run => run.origin === 'demo'));
    const sample = runs.body.runs.find(run => run.run_id === 'demo-archive-replay');
    const detail = await request(activityStore, `https://example.test/api/activity/runs/${sample.run_id}`);
    assert.equal(detail.body.status, sample.status);
    const fullLog = await (await activityStore.handle(`https://example.test/api/activity/runs/${sample.run_id}/log`)).text();
    assert.match(fullLog, /^DEMO REPLAY:/);
    assert.match(fullLog, /^tweetnook sync: started$/m);
    assert.ok(!/4815|4,815|Connection timed out|Rate limited/.test(fullLog));
    const first = await activityStore.handle(`https://example.test/api/activity/runs/${sample.run_id}/log?offset=0`);
    const next = Number(first.headers.get('X-Log-Offset'));
    assert.equal(await first.text(), fullLog);
    assert.equal(next, new TextEncoder().encode(fullLog).length);
    const rest = await activityStore.handle(`https://example.test/api/activity/runs/${sample.run_id}/log?offset=${next}`);
    assert.equal(await rest.text(), '');
    assert.equal(rest.headers.get('X-Log-Offset'), String(next));
    const restarted = await activityStore.handle(`https://example.test/api/activity/runs/${sample.run_id}/log?offset=${next + 100}`);
    assert.equal(await restarted.text(), fullLog);
    const liveLog = await (await activityStore.handle(`https://example.test/api/activity/runs/${runs.body.runs[0].run_id}/log`)).text();
    assert.match(liveLog, /^Threads: failed · Stopped by user$/m);
    assert.match(liveLog, /^tweetnook sync: stopped · Stopped by user$/m);
    assert.equal((await activityStore.handle('https://example.test/api/activity/runs/missing')).status, 404);

    // New runs pick up session tag edits instead of retaining stale cached counts.
    const mutableReplay = api.createStore(fixture, {now: () => clock});
    const taggedLike = fixture.tweets.find(tweet => tweet.collections.includes('like') && tweet.tags.length);
    await request(mutableReplay, `https://example.test/api/tags/${taggedLike.tweet_id}`, {method: 'DELETE'});
    await request(mutableReplay, 'https://example.test/api/activity/sync', {method: 'POST'});
    clock += 180;
    const changedReplay = (await request(mutableReplay, 'https://example.test/api/activity/status')).body.last_snapshot;
    assert.equal(changedReplay.steps.find(step => step.key === 'tagging').total, replayCounts.taggedLikes - 1);

    // Notices: a sample that can be dismissed.
    const notices = await request(activityStore, 'https://example.test/api/notices');
    assert.equal(notices.body.notices.filter(notice => !notice.dismissed).length, 1);
    await request(activityStore, `https://example.test/api/notices/${notices.body.notices[0].id}/dismiss`, {method: 'POST'});
    assert.equal((await request(activityStore, 'https://example.test/api/notices')).body.notices[0].dismissed, true);
    assert.deepEqual((await request(activityStore, 'https://example.test/api/notices/dismiss-all', {method: 'POST'})).body, {dismissed: 0});
    assert.deepEqual((await request(activityStore, 'https://example.test/api/notices/dismissed', {method: 'DELETE'})).body, {cleared: 1});
    assert.deepEqual((await request(activityStore, 'https://example.test/api/notices')).body.notices, []);

    // Configuration: served only when the schema is provided; saves stay in this session.
    assert.equal((await activityStore.handle('https://example.test/api/config')).status, 404);
    const configStore = api.createStore(fixture, {config: demoConfig});
    assert.deepEqual((await request(configStore, 'https://example.test/api/config/schema')).body, demoConfig.schema);
    assert.deepEqual((await request(configStore, 'https://example.test/api/config/defaults')).body, demoConfig.defaults);
    const config = await request(configStore, 'https://example.test/api/config');
    assert.deepEqual(config.body, {values: demoConfig.defaults, explicit: []});
    const savedCollections = await request(configStore, 'https://example.test/api/config', {
        method: 'POST',
        body: JSON.stringify({changes: {'sync.collections': ['tweets', 'bookmarks'], 'web.port': 9000}}),
    });
    assert.deepEqual(savedCollections.body.values.sync.collections, ['bookmarks', 'tweets']);
    assert.equal(savedCollections.body.values.web.port, 9000);
    assert.deepEqual(savedCollections.body.explicit, ['sync.collections', 'web.port']);
    const reset = await request(configStore, 'https://example.test/api/config', {
        method: 'POST',
        body: JSON.stringify({changes: {'web.port': null}}),
    });
    assert.equal(reset.body.values.web.port, demoConfig.defaults.web.port);
    assert.deepEqual(reset.body.explicit, ['sync.collections']);
    const empty = await configStore.handle('https://example.test/api/config', {
        method: 'POST',
        body: JSON.stringify({changes: {'sync.collections': []}}),
    });
    assert.equal(empty.status, 400);

    // Schedule: the same short label and description the scheduler gives.
    const daily = await request(freshStore, 'https://example.test/api/activity/schedule');
    assert.equal(daily.body.label, 'Daily at 3:00 AM');
    assert.equal(daily.body.description, 'Every day · randomized delay');
    assert.ok(daily.body.next_run_at > Date.now() / 1000);
    const weekly = await request(freshStore, 'https://example.test/api/activity/schedule', {
        method: 'PUT',
        body: JSON.stringify({cadence: 'weekly', weekday: 0, time: '00:30'}),
    });
    assert.equal(weekly.body.label, 'Mondays at 12:30 AM');
    const hourly = await request(freshStore, 'https://example.test/api/activity/schedule', {
        method: 'PUT',
        body: JSON.stringify({cadence: 'hours', every_hours: 1}),
    });
    assert.equal(hourly.body.label, 'Every hour');

    const schedule = await request(freshStore, 'https://example.test/api/activity/schedule', {
        method: 'PUT',
        body: JSON.stringify({enabled: false}),
    });
    assert.equal(schedule.body.config.enabled, false);
    assert.equal(schedule.body.configured, false);

    console.log('demo API tests passed');
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
