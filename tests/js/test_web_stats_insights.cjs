'use strict';

const assert = require('node:assert/strict');
const { appScripts, appScriptsWith } = require('./page_scripts.cjs');
const {
    browserContext,
    immediateComponent,
    loadScripts,
    nookMergeScope,
    test,
    run,
} = require('./web_harness.cjs');

// Values built inside the page context have that realm's prototypes; compare plain copies.
function deepEqual(actual, expected) {
    assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected);
}

function helpers() {
    return loadScripts(
        browserContext(),
        appScripts(),
        '({insightLocalWeek, insightWeekView, insightYearView, insightActivityView, insightCalendarView, insightCalendarYears, insightPostsOften, insightPostMix, insightHighlights, insightRhythm, insightNumbers, insightGone, insightPercent, statsIcon})',
    );
}

function week(entries) {
    const hours = new Array(168).fill(0);
    for (const [index, count] of entries) hours[index] = count;
    return hours;
}

test('posting hours shift from UTC into the viewer\'s zone and wrap around the week', () => {
    const { insightLocalWeek } = helpers();
    // Sunday 00:00 UTC is Sunday 02:00 at UTC+2 and Saturday 19:00 at UTC-5.
    assert.equal(insightLocalWeek(week([[0, 1]]), 120).indexOf(1), 2);
    assert.equal(insightLocalWeek(week([[0, 1]]), -300).indexOf(1), 6 * 24 + 19);
    // Half-hour zones round to the nearest hour: UTC+5:30 lands at 06:00.
    assert.equal(insightLocalWeek(week([[0, 1]]), 330).indexOf(1), 6);
    // Saturday 23:00 UTC moves into Sunday 01:00 at UTC+2.
    assert.equal(insightLocalWeek(week([[167, 4]]), 120).indexOf(4), 1);
});

test('the week grid starts on the locale\'s first day and finds the night-owl share', () => {
    const { insightWeekView } = helpers();
    // Three posts Sunday 23:00 and one Wednesday 06:00, all UTC.
    const view = insightWeekView(week([[23, 3], [3 * 24 + 6, 1]]), 1, 0);

    deepEqual(view.rows.map(row => row.day), [1, 2, 3, 4, 5, 6, 0]);
    assert.equal(view.total, 4);
    deepEqual(view.peak, { count: 3, day: 0, hour: 23 });
    assert.equal(view.nightShare, 0.75);
    assert.equal(view.morningShare, 0.25);
    assert.equal(view.weekendShare, 0.75);
    const sunday = view.rows[6].cells;
    const wednesday = view.rows[2].cells;
    assert.equal(sunday[23].level, 4);
    // With two active hours, the quieter is the lowest step.
    assert.equal(wednesday[6].level, 1);
    assert.equal(sunday[0].level, 0);
    // The phone grid: a row per hour, Monday to Sunday across, labels every three hours.
    assert.equal(view.hours.length, 24);
    deepEqual(view.hours[23].cells.map(cell => cell.level), [0, 0, 0, 0, 0, 0, 4]);
    deepEqual(view.hours[6].cells.map(cell => cell.level), [0, 0, 1, 0, 0, 0, 0]);
    deepEqual(view.hours.map(row => row.label !== ''), Array.from({ length: 24 }, (_, hour) => hour % 3 === 0));
    deepEqual(view.hours[0].cells.map(cell => cell.edge), ['start', 'start', '', '', '', 'end', 'end']);
    assert.equal(view.dayInitials.length, 7);
});

test('week grid shades by quartile, so a busy archive keeps its quiet hours light', () => {
    const { insightWeekView } = helpers();
    // Four active hours within 30% of each other. Scaled by the busiest, all four would be
    // the darkest step; by quartile, each takes its own step.
    const view = insightWeekView(week([[1, 100], [2, 110], [3, 120], [4, 130]]), 0, 0);
    deepEqual(view.rows[0].cells.slice(0, 6).map(cell => cell.level), [0, 1, 2, 3, 4, 0]);
});

test('year bars fill missing years and thin labels on long spans', () => {
    const { insightYearView } = helpers();
    const bars = insightYearView([{ year: 2019, count: 4 }, { year: 2021, count: 2 }]);
    deepEqual(bars.map(bar => [bar.year, bar.count, bar.height]), [[2019, 4, 100], [2020, 0, 0], [2021, 2, 50]]);
    assert.ok(bars.every(bar => bar.showLabel));

    const long = insightYearView([{ year: 2010, count: 3 }, { year: 2022, count: 100 }]);
    const labelled = long.filter(bar => bar.showLabel).map(bar => bar.year);
    deepEqual(labelled, [2010, 2012, 2014, 2016, 2018, 2020, 2022]);
    // A charted year with posts never disappears, however small next to the busiest one.
    assert.equal(long[0].height, 3);
    deepEqual(insightYearView([]), []);
});

test('a few charted years show months instead', () => {
    const { insightActivityView } = helpers();
    // Days since 1970-01-01: 19691 is 2023-11-30, 19722 2023-12-31, 19723 2024-01-01 and
    // 19782 2024-02-29.
    const short = insightActivityView({
        by_year: [{ year: 2023, count: 6 }, { year: 2024, count: 3 }],
        days: [[19691, 2], [19722, 4], [19723, 1], [19782, 2]],
    });
    assert.equal(short.unit, 'month');
    deepEqual(short.bars.map(bar => [bar.key, bar.count, Math.round(bar.height)]).slice(10), [
        ['2023-11-01', 2, 50], ['2023-12-01', 4, 100], ['2024-01-01', 1, 25], ['2024-02-01', 2, 50],
    ]);
    assert.equal(short.bars.length, 14);
    // December's search runs to the next January; only Januaries carry a year label.
    deepEqual([short.bars[11].since, short.bars[11].until], ['2023-12-01', '2024-01-01']);
    deepEqual(short.bars.filter(bar => bar.showLabel).map(bar => bar.key), ['2023-01-01', '2024-01-01']);
    assert.equal(short.bars[0].height, 0);

    // Seven charted years stay yearly.
    const long = insightActivityView({
        by_year: [{ year: 2018, count: 5 }, { year: 2024, count: 5 }],
        days: [[17532, 5], [19723, 5]],
    });
    assert.equal(long.unit, 'year');
    deepEqual([long.bars.length, long.bars[0].since, long.bars[0].until], [7, '2018-01-01', '2019-01-01']);
});

test('a few early posts are left off the year chart', () => {
    const { insightYearView, insightActivityView } = helpers();
    // The busiest year has 3,013 posts, so years under 60 before the first busy one drop.
    const years = [
        { year: 2007, count: 2 }, { year: 2009, count: 1 }, { year: 2021, count: 5 },
        { year: 2022, count: 1007 }, { year: 2023, count: 3013 }, { year: 2024, count: 40 },
    ];
    // A quiet year after the archive gets going is still a real dip, so it stays.
    deepEqual(insightYearView(years).map(bar => bar.year), [2022, 2023, 2024]);
    // A caller can fix the first year, so adding conversations keeps the saved posts' range
    // even where the extra posts would make earlier years busy enough to chart.
    deepEqual(insightYearView(years, 2023).map(bar => bar.year), [2023, 2024]);
    const days = [[19358, 4], [19723, 6]]; // 2023-01-01 and 2024-01-01
    const months = insightActivityView({ by_year: years, days }, 2023).bars;
    deepEqual([months.length, months[0].key, months[12].key], [13, '2023-01-01', '2024-01-01']);
});

test('the posting-days calendar lays out a year in weeks', () => {
    const { insightCalendarView, insightCalendarYears } = helpers();
    // Days since 1970-01-01: 2024-01-01 (a Monday) is 19723, Feb 29 19782, Mar 1 19783,
    // Jul 4 19908; 19482 is in 2023 and 20240 in 2025.
    const days = [[19482, 7], [19723, 1], [19782, 2], [19783, 3], [19908, 10], [20240, 4]];
    const view = insightCalendarView(days, 2024, 0, Date.UTC(2024, 6, 4, 12));

    // Sunday-first weeks: one blank before Monday Jan 1, 366 days, 53 columns.
    assert.equal(view.weeks, 53);
    assert.equal(view.cells.length, 53 * 7);
    assert.equal(view.cells[0].outside, true);
    assert.equal(view.cells[1].date, '2024-01-01');
    assert.equal(view.cells[60].date, '2024-02-29');
    assert.equal(view.cells[366].date, '2024-12-31');
    assert.ok(view.cells.slice(367).every(cell => cell.outside));
    // Levels split the year's four active days into quartiles; the busiest is the darkest.
    deepEqual([1, 60, 61, 186].map(index => [view.cells[index].count, view.cells[index].level]), [[1, 1], [2, 2], [3, 3], [10, 4]]);
    assert.equal(view.cells[2].level, 0);
    assert.equal(view.cells[186].future, false);
    assert.equal(view.cells[187].future, true);
    assert.equal(view.total, 16);
    assert.equal(view.activeDays, 4);
    deepEqual(view.busiest, { date: '2024-07-04', count: 10 });
    // February starts on a Thursday, so its label sits over the following week.
    deepEqual(view.months.slice(0, 3).map(month => month.column), [1, 6, 10]);
    deepEqual(view.weekdays.map(day => day.label !== ''), [false, true, false, true, false, true, false]);
    deepEqual(insightCalendarYears(days), [2023, 2024, 2025]);
    // The narrow layout's months: 2024 starts on a Monday, February on a Thursday and
    // March on a Friday, so in Sunday-first weeks they start one, four and five days in.
    deepEqual(view.monthGrid.map(month => month.cells.length), [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]);
    deepEqual(view.monthGrid.slice(0, 3).map(month => month.offset), [1, 4, 5]);
    const february = view.monthGrid[1].cells;
    deepEqual([february[0].date, february[28].date, february[28].count, february[28].level], ['2024-02-01', '2024-02-29', 2, 2]);
    // Tooltips anchor to the side of the small month a day sits on: Thursday is central,
    // Friday March 1 is near the right edge.
    deepEqual([february[28].edge, view.monthGrid[2].cells[0].edge], ['', 'end']);
    deepEqual([view.monthGrid[6].cells[3].future, view.monthGrid[6].cells[4].future], [false, true]);
});

test('own-posting highlights need someone who posts every week', () => {
    const { insightPostsOften } = helpers();
    // 120 tweets in four weeks.
    assert.equal(insightPostsOften({ total: 120, days: [[19723, 60], [19750, 60]] }), true);
    // 197 tweets over about four years is under one a week.
    assert.equal(insightPostsOften({ total: 197, days: [[19000, 100], [20500, 97]] }), false);
    // Busy for a week, but too few tweets to describe a habit.
    assert.equal(insightPostsOften({ total: 60, days: [[19723, 60]] }), false);
    assert.equal(insightPostsOften(undefined), false);
});

test('the post mix leaves out empty rows and counts originals as the remainder', () => {
    const { insightPostMix } = helpers();
    const rows = insightPostMix({ total: 10, replies: 3, quotes: 2, reposts: 0, with_media: 4, with_links: 0, verified: 1 });
    // Largest first.
    deepEqual(rows.map(row => [row.key, row.count, row.share]), [
        ['original', 5, '50%'],
        ['media', 4, '40%'],
        ['replies', 3, '30%'],
        ['quotes', 2, '20%'],
        ['verified', 1, '10%'],
    ]);
    assert.equal(rows[0].query, '-is:reply -is:quote -is:retweet');
    deepEqual(insightPostMix({ total: 0 }), []);
});

test('story tiles search the right days and skip what does not apply', () => {
    const { insightHighlights, insightPercent } = helpers();
    const data = {
        post_mix: { total: 4 },
        busiest_day: { date: '2024-02-28', count: 3 },
        longest_streak: { days: 3, start: '2024-12-30', end: '2025-01-01' },
        saved_twice: 0,
        top_authors: [{ author_id: '7', username: 'alice', display_name: 'Alice', count: 2 }],
        activity: { tweet: { total: 120, days: [[19723, 60], [19750, 60]], week_hours: week([]) } },
    };
    const byId = Object.fromEntries(insightHighlights(data, {}).map(tile => [tile.id, tile]));

    // The day after Feb 28 in a leap year is Feb 29.
    assert.equal(byId['busiest-day'].action.query, 'since:2024-02-28 until:2024-02-29');
    deepEqual(byId.streak.action, { kind: 'search', query: 'since:2024-12-30 until:2025-01-02', collection: 'tweet' });
    deepEqual(byId['favorite-account'].avatar, { id: '7', name: 'Alice' });
    assert.equal(byId['saved-twice'], undefined);
    assert.equal(insightHighlights({ ...data, longest_streak: { days: 1 } }, {}).some(tile => tile.id === 'streak'), false);
    // Someone who rarely posts gets no streak, however long their best run was.
    const rare = { ...data, activity: { tweet: { total: 197, days: [[19000, 100], [20500, 97]], week_hours: week([]) } } };
    assert.equal(insightHighlights(rare, {}).some(tile => tile.id === 'streak'), false);
    assert.equal(insightPercent(1, 400), '<1%');
});

test('the rhythm prefers the owner\'s own tweets and names the day-part they lean toward', () => {
    const { insightRhythm } = helpers();
    const series = (entries, days = []) => {
        const hours = week(entries);
        return { total: hours.reduce((a, b) => a + b, 0), week_hours: hours, days };
    };
    // 120 own tweets at 06:00 UTC within four weeks outweigh saved posts mostly late at night.
    const often = [[19723, 60], [19750, 60]];
    const bird = insightRhythm({ tweet: series([[6, 120]], often), all: series([[23, 500], [6, 120]]) }, 0);
    assert.deepEqual([bird.value, bird.icon, bird.label], ['Early bird', 'sun', 'Your posting rhythm']);
    assert.match(bird.detail, /^100% of your tweets between/);

    // 197 own tweets spread over four years say little, so the saved posts decide:
    // 1,000 of 1,200 between 22:00 and 04:00.
    const rarely = [[19000, 100], [20500, 97]];
    const owl = insightRhythm({ tweet: series([[6, 197]], rarely), all: series([[23, 1000], [12, 200]]) }, 0);
    assert.deepEqual([owl.value, owl.icon, owl.label], ['Night owl', 'moon', 'When your saved posts went out']);
    assert.match(owl.detail, /^83% of the posts you saved between/);

    // Six night hours and four morning hours compare per hour: 3 posts at night (0.5 an hour)
    // lose to 3 in the morning (0.75 an hour) even though the shares match.
    const fair = insightRhythm({ all: series([[22, 1], [0, 1], [2, 1], [5, 1], [6, 1], [7, 1]]) }, 0);
    assert.equal(fair.value, 'Early bird');

    const office = insightRhythm({ all: series([[10, 4], [14, 4]]) }, 0);
    assert.deepEqual([office.value, office.icon], ['Daytime regular', 'clock']);
    assert.equal(insightRhythm({ all: series([]) }, 0), null);
});

test('headline numbers appear only when there is something to count', () => {
    const { insightNumbers } = helpers();
    deepEqual(insightNumbers({ post_mix: { total: 0 } }), []);
    const numbers = insightNumbers({
        post_mix: { total: 10 }, words: 500, likes_on_x: 1000, video_seconds: 90,
        media_types: { video: 2 }, viral: 3,
    });
    deepEqual(numbers.map(number => number.id), ['words', 'likes', 'video', 'viral']);
    deepEqual(numbers[3].action, { kind: 'search', query: 'min_faves:100000' });
    assert.equal(numbers[2].detail, '2 videos');
    assert.equal(numbers[0].detail, '2 minutes to reread');

    // Including conversations states the same facts over the combined counts, so the cards
    // keep their shape: 500 + 179,500 words is 2 novels and 756 minutes of reading; 90 s +
    // 3,720 s of video is 63.5 minutes over 2 + 5 videos. Likes and viral posts have no
    // conversation totals and stay as they are.
    const data = {
        post_mix: { total: 10 }, words: 500, likes_on_x: 1000, video_seconds: 90,
        media_types: { video: 2 }, viral: 3,
        context: { words: 179500, video_seconds: 3720, media_types: { video: 5 } },
    };
    const combined = insightNumbers(data, true);
    deepEqual(combined.map(number => [number.id, number.label, number.value, number.detail]), [
        ['words', 'Words saved', '180K', 'About 2 novels, 12 h 36 min to reread'],
        ['likes', 'Likes on X', '1,000', 'Combined, about 100 per saved post'],
        ['video', 'Video saved', '1 h 4 min', '7 videos'],
        ['viral', 'Viral posts', '3', 'With 100K+ likes on X'],
    ]);
    // Search covers saved posts only, so the combined video card doesn't search.
    assert.equal(combined[2].action, null);
});

test('the gone card shows each reason\'s share of the missing saved posts', () => {
    const { insightGone } = helpers();
    assert.equal(insightGone(undefined), null);
    assert.equal(insightGone({ saved: 0, context: 0, reasons: [] }), null);
    const gone = insightGone({
        saved: 4, context: 2, resurrected: 1,
        reasons: [
            { reason: 'deleted_by_author', label: 'Deleted by author', count: 3 },
            { reason: 'not_found', label: 'Not found', count: 1 },
        ],
    });
    deepEqual(gone.reasons.map(row => [row.reason, row.share, row.width]), [
        ['deleted_by_author', '75%', 75],
        ['not_found', '25%', 25],
    ]);
    // Conversation posts count toward the total only when they're included; reasons stay
    // shares of the saved posts, the only ones with recorded reasons.
    assert.equal(gone.total, 4);
    const combined = insightGone({ saved: 4, context: 2, reasons: [{ reason: 'not_found', label: 'Not found', count: 1 }] }, true);
    deepEqual([combined.total, combined.saved, combined.reasons[0].share], [6, 4, '25%']);
    // Only context posts are gone: the card still explains them, with nothing to browse.
    assert.equal(insightGone({ saved: 0, context: 3, reasons: [] }).saved, 0);
});

test('every icon the analytics markup names exists in the icon set', () => {
    const fs = require('node:fs');
    const path = require('node:path');
    const { statsIcon } = helpers();
    const root = path.resolve(__dirname, '..', '..', 'tweetnook', 'web');
    const sources = [
        fs.readFileSync(path.join(root, 'fragments', 'stats.html'), 'utf8'),
        fs.readFileSync(path.join(root, 'static', 'js', 'features', 'archive-insights.js'), 'utf8'),
    ].join('\n');
    const names = new Set([
        ...[...sources.matchAll(/statsIcon\('(\w+)'\)/g)].map(match => match[1]),
        ...[...sources.matchAll(/icon: '(\w+)'/g)].map(match => match[1]),
    ]);
    assert.ok(names.size > 20);
    for (const name of names) assert.match(statsIcon(name), /^<svg [^>]*aria-hidden="true"[^>]*>.*<path /, name);
});

test('analytics loads highlights and searches a year in the selected collection', async () => {
    const context = browserContext();
    const snapshot = {
        generated_at: '2026-08-10T00:00:00+00:00',
        summary: { unique_posts: 3, missing_archive_tweets: 5, context_posts: 1 },
        collections: [],
        insights: {
            activity: {
                // 2024-07-04 is day 19908 and 2025-06-01 day 20240.
                all: { total: 3, by_year: [{ year: 2024, count: 1 }, { year: 2025, count: 2 }], week_hours: week([[30, 3]]), days: [[19908, 1], [20240, 2]] },
                like: { total: 2, by_year: [{ year: 2025, count: 2 }], week_hours: week([[30, 2]]), days: [[20240, 2]] },
                bookmark: { total: 0, by_year: [], week_hours: week([]) },
            },
            top_authors: [{ author_id: '7', username: 'alice', display_name: 'Alice', count: 2, likes: 2, bookmarks: 0 }],
            post_mix: { total: 3, replies: 1 },
            hashtags: [{ tag: 'cats', count: 2 }],
            words: 500,
            gone: { saved: 2, context: 3, resurrected: 0, reasons: [] },
            // 2023-05-23 is day 19500.
            context: {
                posts: 8, words: 31000, media_types: {}, video_seconds: 0,
                activity: { total: 2, by_year: [{ year: 2023, count: 2 }], week_hours: week([[30, 2]]), days: [[19500, 2]] },
                reply_authors: [{ author_id: '9', username: 'dana', display_name: 'Dana', count: 4 }, { author_id: '8', username: 'eve', display_name: 'Eve', count: 2 }],
            },
        },
    };
    context.fetch = async () => ({ ok: true, async json() { return snapshot; } });
    const { tweetApp, statsDialog } = loadScripts(context, appScriptsWith('archive-summary.js'), '({tweetApp, statsDialog})');
    const app = immediateComponent(tweetApp());
    const watchers = new Map();
    app.$watch = (key, callback) => watchers.set(key, callback);
    const stats = nookMergeScope(app, statsDialog());
    stats.init();
    let searches = 0;
    app.search = () => { searches += 1; };

    app.openStatsModal();
    watchers.get('showStatsModal')(app.showStatsModal);
    await new Promise(resolve => setTimeout(resolve, 0));

    // Collections with no posts get no filter button.
    deepEqual(stats.insightSeriesOptions.map(option => option.id), ['all', 'like']);
    // Two years of posts chart by month, January 2024 through June 2025.
    assert.equal(stats.insightYearUnit, 'month');
    assert.equal(stats.insightYears.length, 18);
    deepEqual(stats.insightYears.filter(bar => bar.count).map(bar => [bar.key, bar.count]), [['2024-07-01', 1], ['2025-06-01', 2]]);
    assert.equal(stats.insightAuthors[0].key, '7');
    deepEqual(stats.insightRepliers.map(row => [row.username, row.width]), [['dana', 100], ['eve', 50]]);
    deepEqual(stats.insightMix.map(row => [row.key, row.count]), [['original', 2], ['replies', 1]]);

    assert.equal(stats.insightContextNote(), '+1 post from conversations');
    // Saved posts only until conversations are included.
    deepEqual(stats.insightNumbers.map(number => number.value), ['500']);
    assert.equal(stats.insightGone.total, 2);

    // Including conversations charts every saved post plus the conversations over the saved
    // posts' range, so the May 2023 conversation posts stay off the chart and it keeps its 18
    // months. Chart selections stop searching, since search covers saved posts.
    assert.equal(stats.insightHasConversations, true);
    stats.selectInsightSeries('like');
    stats.setInsightConversations(true);
    assert.equal(stats.insightConversations, true);
    // Figures with conversation totals switch to them: 500 + 31,000 words, 2 + 3 gone posts.
    deepEqual(stats.insightNumbers.map(number => number.value), ['31.5K']);
    assert.equal(stats.insightGone.total, 5);
    assert.equal(stats.insightYears.length, 18);
    deepEqual(stats.insightYears.filter(bar => bar.count).map(bar => [bar.key, bar.count]), [['2024-07-01', 1], ['2025-06-01', 2]]);
    deepEqual(stats.insightCalendarYears, [2023, 2024, 2025]);
    assert.equal(stats.insightWeek.total, 5);
    stats.insightYearSearch(stats.insightYears[4]);
    stats.insightDaySearch({ date: '2023-05-23', count: 2 });
    assert.equal(searches, 0);
    stats.setInsightConversations(false);
    deepEqual(stats.insightNumbers.map(number => number.value), ['500']);
    stats.selectInsightSeries('all');
    assert.equal(stats.insightYears.length, 18);

    // The calendar opens on the latest year and steps only between years with posts.
    deepEqual(stats.insightCalendarYears, [2024, 2025]);
    assert.equal(stats.insightCalendar.year, 2025);
    assert.equal(stats.insightCalendarCanStep(1), false);
    stats.stepInsightCalendar(-1);
    assert.equal(stats.insightCalendar.year, 2024);
    assert.equal(stats.insightCalendarSummary(), '1 post on 1 day · busiest Jul 4 with 1');

    stats.selectInsightSeries('like');
    // Likes have no 2024 posts, so the calendar moves to a year they do have.
    assert.equal(stats.insightCalendar.year, 2025);
    assert.equal(stats.insightYears.length, 6);
    stats.insightYearSearch(stats.insightYears[5]);

    assert.equal(app.showStatsModal, false);
    assert.equal(app.collectionFilter, 'likes');
    assert.equal(app.searchQuery, 'since:2025-06-01 until:2025-07-01');
    assert.equal(searches, 1);

    app.showStatsModal = true;
    stats.insightDaySearch(stats.insightCalendar.cells.find(cell => cell.date === '2025-06-01'));
    assert.equal(app.searchQuery, 'since:2025-06-01 until:2025-06-02');
    assert.equal(app.collectionFilter, 'likes');
    // Empty days have nothing to search.
    stats.insightDaySearch(stats.insightCalendar.cells.find(cell => cell.date === '2025-06-02'));
    assert.equal(searches, 2);

    // A report from before highlights existed clears the previous ones.
    stats.applyStatsSnapshot({ ...snapshot, insights: undefined });
    deepEqual(stats.insightSeriesOptions, []);
    assert.equal(stats.insightWeek, null);
});

run();
