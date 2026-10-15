'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const events = [];
const context = {URL, Map, Set, CustomEvent: class {constructor(type, options) {this.type = type; this.detail = options.detail;}}, window: {dispatchEvent: e => events.push(e)}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root, 'tweetnook/web/static/js/articles.js'), 'utf8'), context);
const ui = context.window.TweetNookArticles;
const app = {mediaSource: m => m.download?.local_path ? '/' + m.download.local_path : null};
const media = {media_id: '123', media_key: '3_123', source: 'article_media', type: 'photo', url: 'https://example.test/image.jpg', download: {local_path: 'media/image.jpg'}};
const article = {title: 'A <saved> article', summary_text: 'An excerpt', content_text: 'First paragraph.\n\nSecond paragraph.', status: 'body_present', media: [{...media, source: 'article_cover'}, media]};
const tweet = {tweet_id: '123', article};
let cases = 0;
function test(name, run) {run(); cases++; console.log('ok - ' + name);}
test('article discovery handles normalized tweets and quote wrappers', () => {
    assert.equal(ui.data(tweet), article);
    assert.equal(ui.data({raw_json: tweet}), article);
    assert.equal(ui.data({article, availability: {placeholder: true}}), null);
    assert.equal(ui.data({text: 'ordinary long tweet'}), null);
});
test('previews show one cover, badge, title, and excerpt without the body', () => {
    const html = ui.render(app, tweet);
    assert.match(html, /Read article: A &lt;saved&gt; article/);
    assert.match(html, /data-article-id="123"/);
    assert.equal((html.match(/<img /g) || []).length, 1);
    assert.match(html, /article-badge/);
    assert.doesNotMatch(html, /Second paragraph/);
});
test('missing covers and preview-only bodies remain understandable', () => {
    const a = {title: 'Preview', summary_text: 'Saved excerpt', media: []};
    assert.match(ui.render(app, {article: a}), /article-preview-label/);
    const html = ui.render(app, {article: a}, true);
    assert.match(html, /Saved excerpt/);
    assert.match(html, /Only a preview is saved/);
    assert.doesNotMatch(html, /<img/);
});
test('plain text preserves paragraphs and escapes saved markup', () => {
    const html = ui.body(app, {content_text: 'One <script>\n\nTwo', media: []});
    assert.match(html, /<p>One &lt;script&gt;<\/p><p>Two<\/p>/);
    assert.doesNotMatch(html, /Only a preview/);
});
test('download policy never fetches remote article images implicitly', () => {
    const html = ui.render(app, {article: {...article, media: [{...media, source: 'article_cover', download: {}}]}}, true);
    assert.match(html, /Cover not downloaded/);
    assert.doesNotMatch(html, /src="https:/);
});
test('overlapping UTF-16 style ranges and links render balanced escaped HTML', () => {
    const entities = new Map([['0', {type: 'LINK', data: {url: 'https://example.test/?a=1&b=2'}}]]);
    const html = ui.inline({text: 'A😀 <B>', inlineStyleRanges: [{offset: 1, length: 2, style: 'Bold'}, {offset: 4, length: 3, style: 'Italic'}], entityRanges: [{offset: 0, length: 3, key: 0}]}, entities);
    assert.match(html, /<strong>😀<\/strong>/);
    assert.match(html, /<em>&lt;B&gt;<\/em>/);
    assert.match(html, /a=1&amp;b=2/);
});
test('unsafe entity links and invalid ranges cannot inject markup', () => {
    const entities = new Map([['0', {type: 'LINK', data: {url: 'javascript:alert(1)'}}]]);
    const html = ui.inline({text: '<img onerror=x>', inlineStyleRanges: [{offset: -1, length: 20, style: 'Bold'}], entityRanges: [{key: 0, offset: 0, length: 14}]}, entities);
    assert.equal(html, '&lt;img onerror=x&gt;');
});
test('rich bodies retain headings, grouped lists, quotations, and inline image order', () => {
    const blocks = [
        {type: 'header-two', text: 'Heading'},
        {type: 'unordered-list-item', text: 'One'},
        {type: 'unordered-list-item', text: 'Two'},
        {type: 'atomic', text: ' ', entityRanges: [{key: 0}]},
        {type: 'blockquote', text: 'Quote'},
        {type: 'ordered-list-item', text: 'Three'},
        {type: 'unstyled', text: 'After'},
    ];
    const state = {blocks, entityMap: [{key: '0', value: {type: 'MEDIA', data: {mediaItems: [{mediaId: '123'}]}}}]};
    const html = ui.body(app, {...article, content_state: state});
    assert.match(html, /<h3>Heading<\/h3><ul><li>One<\/li><li>Two<\/li><\/ul>/);
    assert.match(html, /<blockquote>Quote<\/blockquote><ol><li>Three<\/li><\/ol><p>After<\/p>/);
    assert.equal((html.match(/<figure/g) || []).length, 1);
    assert.ok(html.indexOf('<figure') < html.indexOf('<blockquote'));
});
test('object entity maps and unknown embeds preserve accessible fallbacks', () => {
    const state = {blocks: [{text:'Introduction',type:'unstyled'}, {type:'atomic',entityRanges:[{key: 2}]}],entityMap: {'2': {type:'TWEET',data:{tweetId:'987'}}}};
    assert.match(ui.body(app, {content_state:state}), /href="https:\/\/x.com\/i\/status\/987"/);
    state.entityMap['2'] = {type:'UNKNOWN',data:{url:'javascript:alert(1)'}};
    assert.match(ui.body(app, {content_state:state}), /Embedded content was not captured/);
});
test('preview activation stops the enclosing quote and carries the intended tweet ID', () => {
    let stopped = false, prevented = false;
    context.window.tweetNookOpenArticle({stopPropagation(){stopped=true;},preventDefault(){prevented=true;}},{dataset:{articleId:'123'}});
    assert.equal(stopped && prevented, true);
    assert.equal(events[0].type, 'open-article');
    assert.equal(events[0].detail.tweetId, '123');
});
test('the application renders article feeds, quotes, and full detail through the new module', () => {
    context.document = {documentElement: {classList: {add(){}}}};
    for (const file of ['themes.js', 'app.js']) vm.runInContext(fs.readFileSync(path.join(root, 'tweetnook/web/static/js', file), 'utf8'), context);
    const component = vm.runInContext('tweetApp()', context);
    assert.equal(component.formatText(tweet), '');
    assert.match(component.renderCard(tweet), /article-preview/);
    assert.match(component.renderCard(tweet, 'detail'), /Second paragraph/);
    assert.equal(component.isMediaQuotingMedia({media: [media], qt_media: [media], quoted_tweet: tweet}), false);
    assert.match(component.renderArticle({raw_json: tweet}), /article-preview/);
});
console.log(`${cases}/${cases} article renderer cases passed`);
