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
    assert.match(html, /<span class="article-badge">Article<\/span>/);
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
test('X block URL and mention annotations preserve UTF-16 styling without linking handles inside URLs', () => {
    const text = '😀 https://youtube.com/@jack @jack';
    const url = 'https://youtube.com/@jack';
    const html = ui.inline({text, data: {urls: [{fromIndex: 3, toIndex: 3 + url.length, text: url}],
        mentions: [{fromIndex: 23, toIndex: 28, text: 'jack'}, {fromIndex: 29, toIndex: 34, text: 'jack'}]},
        inlineStyleRanges: [{offset: 29, length: 5, style: 'Bold'}]}, new Map());
    assert.equal((html.match(/<a /g) || []).length, 2);
    assert.match(html, /href="https:\/\/youtube.com\/@jack"/);
    assert.match(html, /href="https:\/\/x.com\/jack"[^>]*><strong>@jack<\/strong><\/a>/);
    assert.ok(html.startsWith('😀 '));
});
test('invalid block annotations cannot create active links or unescaped labels', () => {
    const html = ui.inline({text: '<img> @bad', entityRanges: [null, false, {}, {offset:'0', length:5}], data: {
        urls: [null, false, {}, {fromIndex: 0, toIndex: 5, text: 'javascript:alert(1)'}, {fromIndex: -1, toIndex: 5, text: 'https://example.test'}],
        mentions: [null, false, {}, {fromIndex: 6, toIndex: 10, text: 'bad\" onclick=evil'}, {fromIndex: 999, toIndex: 1004, text: 'valid'}],
    }}, new Map());
    assert.equal(html, '&lt;img&gt; @bad');
});
test('captured image captions remain attached, escaped, and linked in reading and focus modes', () => {
    const a = {...article, content_state: {blocks: [{type:'unstyled', text:'Before'},
        {type:'atomic', entityRanges:[{key:0}]}], entityMap: {'0': {type:'MEDIA', data:{
        mediaItems:[{mediaId:'123'}], caption:'See example.com <img onerror=x>'}}}}};
    for (const interactive of [true, false]) {
        const html = ui.body(app, a, interactive);
        assert.match(html, /<figcaption>See <a href="https:\/\/example.com\/"/);
        assert.match(html, /&lt;img onerror=x&gt;<\/figcaption><\/figure>/);
        assert.equal((html.match(/<figure/g) || []).length, 1);
        assert.doesNotMatch(html, /<img onerror/);
    }
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
test('the real electronics article renders all compressed images and native dividers', () => {
    const fixture = JSON.parse(fs.readFileSync(path.join(root, 'demo/data.json'), 'utf8'));
    const saved = fixture.tweets.find(t => t.tweet_id === '2012988611570933773');
    const component = vm.runInContext('tweetApp()', context);
    component.isDemo = true;
    const html = ui.render(component, saved, true, false);
    assert.equal((html.match(/<img /g) || []).length, 20);
    assert.ok(html.includes('<hr>'));
    assert.ok(html.includes('REQUIRED Tools:'));
    assert.equal((html.match(/<figcaption>/g) || []).length, 4);
    assert.match(html, /href="https:\/\/adafruit.com\/"/);
    assert.match(html, /href="https:\/\/x.com\/dailydigikey"/);
    assert.match(html, /href="https:\/\/jlcmc.com\/"/);
    assert.ok(html.includes('This also works as a USB-&gt;UART bridge :D'));
    assert.ok(html.includes('The Next 100 Projects'));
    assert.doesNotMatch(html, /Draft·Last saved|toolbar-styling-buttons/);
    assert.doesNotMatch(html, /Embedded (media|content) was not captured/);
    assert.doesNotMatch(html, /src="https?:/);
    assert.doesNotMatch(html, /Only a preview is saved/);
    for (const item of saved.article.media) assert.ok(html.includes(item.url));
});
function focusDocument(run) {
    const previous = context.document;
    const dialogs = [], pendingClose = [], focusCalls = [];
    const opener = {isConnected: true, focus(options) {focusCalls.push(options); document.activeElement = this;}};
    const document = {activeElement: opener, body: {style: {overflow: 'clip'}, appendChild(dialog) {dialog.isConnected = true;}},
        createElement(tag) {
            assert.equal(tag, 'dialog');
            const listeners = new Map(), buttonListeners = new Map();
            const button = {isConnected: true, addEventListener(type, listener) {buttonListeners.set(type, listener);}};
            const dialog = {open: false, isConnected: false,
                setAttribute() {}, addEventListener(type, listener) {listeners.set(type, listener);},
                querySelector() {return button;},
                showModal() {this.open = true; document.activeElement = button;},
                close() {this.open = false; pendingClose.push(() => listeners.get('close')?.());},
                remove() {this.isConnected = false; button.isConnected = false;},
                clickClose() {buttonListeners.get('click')();},
                escape() {let prevented = false; listeners.get('cancel')({preventDefault() {prevented = true;}}); assert.equal(prevented, true);},
            };
            dialogs.push(dialog);
            return dialog;
        }};
    context.document = document;
    try {run({document, dialogs, focusCalls, flush: () => {while (pendingClose.length) pendingClose.shift()();}});}
    finally {ui.closeFocus({restoreFocus: false}); context.document = previous;}
}
test('route close removes focus synchronously without late overflow or focus restoration', () => {
    focusDocument(({document, dialogs, focusCalls, flush}) => {
        ui.focus(app, tweet);
        assert.equal(dialogs[0].open, true);
        assert.equal(document.body.style.overflow, 'hidden');
        ui.closeFocus({restoreFocus: false});
        assert.equal(dialogs[0].open, false);
        assert.equal(dialogs[0].isConnected, false);
        assert.equal(document.body.style.overflow, 'clip');
        document.body.style.overflow = 'auto'; // Route restoration owns subsequent state.
        flush();
        ui.closeFocus();
        assert.equal(document.body.style.overflow, 'auto');
        assert.equal(focusCalls.length, 0);
    });
});
test('close button and Escape restore focus without scrolling and allow reopening', () => {
    focusDocument(({document, dialogs, focusCalls, flush}) => {
        ui.focus(app, tweet);
        dialogs[0].clickClose();
        assert.equal(dialogs[0].isConnected, false);
        assert.equal(document.body.style.overflow, 'clip');
        assert.equal(focusCalls[0].preventScroll, true);
        ui.focus(app, tweet);
        flush(); // The first native close event must not clear the second reader.
        assert.equal(dialogs[1].open, true);
        assert.equal(document.body.style.overflow, 'hidden');
        dialogs[1].escape();
        assert.equal(dialogs[1].isConnected, false);
        assert.equal(document.body.style.overflow, 'clip');
        assert.equal(focusCalls.length, 2);
        flush();
        assert.equal(focusCalls.length, 2);
    });
});
test('reopening focus replaces the reader and native close still cleans up once', () => {
    focusDocument(({document, dialogs, focusCalls, flush}) => {
        ui.focus(app, tweet);
        ui.focus(app, tweet);
        assert.equal(dialogs[0].isConnected, false);
        assert.equal(dialogs[1].open, true);
        flush();
        assert.equal(document.body.style.overflow, 'hidden');
        dialogs[1].close();
        flush();
        assert.equal(dialogs[1].isConnected, false);
        assert.equal(document.body.style.overflow, 'clip');
        assert.equal(focusCalls.length, 1);
    });
});
test('article focus controls are in both sticky headers and absent from body markup', () => {
    const html = fs.readFileSync(path.join(root, 'tweetnook/web/index.html'), 'utf8');
    assert.equal((html.match(/aria-label="Open article focus mode"/g) || []).length, 2);
    const mainHeader = html.slice(html.indexOf('<header data-video-occluder'), html.indexOf('</header>'));
    const panelHeader = html.slice(html.indexOf('<!-- Panel Header -->'), html.indexOf('<!-- Panel Thread View -->'));
    assert.match(mainHeader, /openArticleFocus\(threadData.main\)/);
    assert.match(panelHeader, /openArticleFocus\(panelThreadData.main\)/);
    assert.match(mainHeader, /viewMode === 'thread' &amp;&amp;|viewMode === 'thread' &&/);
    assert.match(panelHeader, /panelMode === 'thread' &amp;&amp;|panelMode === 'thread' &&/);
});
console.log(`${cases}/${cases} article renderer cases passed`);
