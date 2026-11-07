/**
 * Analytics highlights: turns the report's insights section into what the dialog draws
 * (activity charts, highlight tiles, people and topics). Views are built once per snapshot
 * or filter change, so rendering reads plain arrays instead of recomputing in getters.
 */

const INSIGHT_SERIES_LABELS = { all: 'All saved', like: 'Likes', bookmark: 'Bookmarks', tweet: 'Your tweets' };
const INSIGHT_SERIES_FEEDS = { all: 'all', like: 'likes', bookmark: 'bookmarks', tweet: 'tweets' };
// A Sunday, so day n of a UTC week is this date plus n days.
const INSIGHT_SUNDAY = Date.UTC(2023, 0, 1);
const READING_WORDS_PER_MINUTE = 238;
const NOVEL_WORDS = 90000;
// Matches the server's threshold for the viral count.
const VIRAL_LIKES = 100000;
// Own-tweet highlights (rhythm, streak) need someone who posts regularly: at least this
// many tweets, averaging this many a week between their first and last.
const OWN_POSTS_MINIMUM = 100;
const OWN_POSTS_PER_WEEK = 2;
// Leading years below this share of the busiest year are left off the year chart, so a
// handful of old posts doesn't squash the years that matter.
const SPARSE_YEAR_SHARE = 0.02;
// A chart spanning this many years or fewer shows months, since a few year bars leave the
// card mostly empty.
const MONTHLY_SPAN_YEARS = 6;
const DAY_MS = 86400000;

function insightNumber(value) {
    return Number(value || 0).toLocaleString();
}

function insightCompact(value) {
    const number = Number(value || 0);
    if (number < 10000) return number.toLocaleString();
    return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(number);
}

function insightPercent(part, total) {
    if (!total) return '0%';
    const percent = (part / total) * 100;
    if (percent > 0 && percent < 1) return '<1%';
    return `${Math.round(percent)}%`;
}

function insightPlural(count, one, many) {
    return `${insightNumber(count)} ${count === 1 ? one : many}`;
}

function insightWeekday(day, style = 'long') {
    return new Date(INSIGHT_SUNDAY + day * 86400000)
        .toLocaleDateString(undefined, { weekday: style, timeZone: 'UTC' });
}

function insightHour(hour) {
    return new Date(Date.UTC(2023, 0, 1, hour))
        .toLocaleTimeString(undefined, { hour: 'numeric', timeZone: 'UTC' });
}

function insightDate(value, options = { month: 'short', day: 'numeric', year: 'numeric' }) {
    const time = Date.parse(value);
    if (Number.isNaN(time)) return '';
    return new Date(time).toLocaleDateString(undefined, { ...options, timeZone: 'UTC' });
}

// The search's until: is exclusive, so a day's posts end at the next date.
function insightNextDay(isoDate) {
    return new Date(Date.parse(isoDate) + 86400000).toISOString().slice(0, 10);
}

function insightDuration(seconds) {
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return insightPlural(minutes, 'minute', 'minutes');
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${insightNumber(hours)} h ${minutes % 60} min`;
    return `${insightNumber(Math.round(hours / 24 * 10) / 10)} days`;
}

// The locale's first weekday, 0 for Sunday; Intl reports 7 for Sunday where it knows.
function insightFirstWeekday() {
    try {
        const info = new Intl.Locale(navigator.language).weekInfo
            || new Intl.Locale(navigator.language).getWeekInfo?.();
        if (info?.firstDay) return info.firstDay % 7;
    } catch (error) {
        // Older browsers have no weekInfo; Sunday matches the server's buckets.
    }
    return 0;
}

// Shift UTC hour-of-week buckets into local time. The current offset stands in for every
// date, so posts from the other side of a daylight-saving change land an hour off, and
// half-hour zones round to the nearest hour.
function insightLocalWeek(weekHours, offsetMinutes = -new Date().getTimezoneOffset()) {
    const shift = Math.round(offsetMinutes / 60);
    const local = new Array(168).fill(0);
    (weekHours || []).forEach((count, index) => {
        local[(((index + shift) % 168) + 168) % 168] += count || 0;
    });
    return local;
}

// Five steps of one hue: zero, then quartiles of the busiest hour.

// Shade steps at the quartiles of the nonzero counts, so each step holds about a quarter of
// the active cells. Scaling by the busiest cell instead flattens a busy archive: every hour
// sits within a few times of the peak, so the quietest hours already reach the middle steps.
function insightLevels(counts) {
    const sorted = counts.filter(count => count > 0).sort((a, b) => a - b);
    const at = share => sorted[Math.floor((sorted.length - 1) * share)] || 0;
    const steps = [at(0.25), at(0.5), at(0.75)];
    return count => (!count ? 0 : 1 + steps.filter(step => count > step).length);
}

function insightWeekView(weekHours, firstDay = insightFirstWeekday(), offsetMinutes) {
    const local = insightLocalWeek(weekHours, offsetMinutes);
    const level = insightLevels(local);
    const total = local.reduce((sum, count) => sum + count, 0);
    const days = Array.from({ length: 7 }, (_, step) => (firstDay + step) % 7);
    const rows = days.map(day => ({
        day,
        label: insightWeekday(day, 'short'),
        cells: Array.from({ length: 24 }, (_, hour) => {
            const count = local[day * 24 + hour];
            return {
                key: `${day}-${hour}`,
                count,
                level: level(count),
                label: `${insightWeekday(day)}s, ${insightHour(hour)} · ${insightPlural(count, 'post', 'posts')}`,
            };
        }),
    }));
    let peak = null;
    local.forEach((count, index) => {
        if (count > 0 && (!peak || count > peak.count)) peak = { count, day: Math.floor(index / 24), hour: index % 24 };
    });
    const dayTotals = Array.from({ length: 7 }, (_, day) => local.slice(day * 24, day * 24 + 24).reduce((a, b) => a + b, 0));
    const hourTotals = Array.from({ length: 24 }, (_, hour) => dayTotals.reduce((sum, _total, day) => sum + local[day * 24 + hour], 0));
    const night = [22, 23, 0, 1, 2, 3].reduce((sum, hour) => sum + hourTotals[hour], 0);
    const morning = [5, 6, 7, 8].reduce((sum, hour) => sum + hourTotals[hour], 0);
    const day = hourTotals.slice(9, 17).reduce((sum, count) => sum + count, 0);
    const weekend = dayTotals[0] + dayTotals[6];
    return {
        rows,
        total,
        peak,
        nightShare: total ? night / total : 0,
        morningShare: total ? morning / total : 0,
        dayShare: total ? day / total : 0,
        weekendShare: total ? weekend / total : 0,
        hourLabels: [0, 6, 12, 18].map(hour => ({ hour, label: insightHour(hour) })),
        // The phone layout turns the grid on its side: a row per hour, a column per day.
        hours: Array.from({ length: 24 }, (_, hour) => ({
            hour,
            label: hour % 3 === 0 ? insightHour(hour) : '',
            cells: rows.map((row, column) => ({ ...row.cells[hour], edge: column < 2 ? 'start' : column > 4 ? 'end' : '' })),
        })),
        dayInitials: rows.map(row => ({ key: row.day, label: insightWeekday(row.day, 'narrow') })),
    };
}

// The first year worth charting: leading years far below the busiest one are dropped.
function insightFirstActiveYear(byYear) {
    const rows = (byYear || []).filter(row => row.count > 0);
    if (!rows.length) return null;
    const floor = Math.max(...rows.map(row => row.count)) * SPARSE_YEAR_SHARE;
    return rows.find(row => row.count >= floor).year;
}

function insightYearView(byYear, first = insightFirstActiveYear(byYear)) {
    if (first === null) return [];
    const counts = new Map(byYear.filter(row => row.year >= first).map(row => [row.year, row.count]));
    const last = Math.max(...counts.keys());
    const max = Math.max(...counts.values());
    const bars = [];
    for (let year = first; year <= last; year += 1) {
        const count = counts.get(year) || 0;
        bars.push({
            key: String(year),
            year,
            count,
            height: max ? Math.max(count ? 3 : 0, (count / max) * 100) : 0,
            label: `${year} · ${insightPlural(count, 'post', 'posts')}`,
            // Long spans label every other year so labels never collide.
            showLabel: last - first < 10 || (year - first) % 2 === 0 || year === last,
            since: `${year}-01-01`,
            until: `${year + 1}-01-01`,
        });
    }
    return bars;
}

function insightMonthISO(year, month) {
    return `${year}-${String(month + 1).padStart(2, '0')}-01`;
}

// Monthly bars from January of the first charted year to the last month with posts.
// Each January carries its year as the axis label.
function insightMonthView(days, first) {
    const counts = new Map();
    for (const [day, count] of days || []) {
        const date = new Date(day * DAY_MS);
        const index = date.getUTCFullYear() * 12 + date.getUTCMonth();
        if (date.getUTCFullYear() >= first) counts.set(index, (counts.get(index) || 0) + count);
    }
    if (!counts.size) return [];
    const last = Math.max(...counts.keys());
    const max = Math.max(...counts.values());
    const bars = [];
    for (let index = first * 12; index <= last; index += 1) {
        const year = Math.floor(index / 12);
        const month = index % 12;
        const count = counts.get(index) || 0;
        const name = new Date(Date.UTC(year, month, 1)).toLocaleDateString(undefined, { month: 'short', year: 'numeric', timeZone: 'UTC' });
        bars.push({
            key: insightMonthISO(year, month),
            year,
            count,
            height: Math.max(count ? 3 : 0, (count / max) * 100),
            label: `${name} · ${insightPlural(count, 'post', 'posts')}`,
            showLabel: month === 0,
            since: insightMonthISO(year, month),
            until: insightMonthISO(Math.floor((index + 1) / 12), (index + 1) % 12),
        });
    }
    return bars;
}

// Saved posts and the conversations around them as one activity series.
function insightMergeSeries(saved, context) {
    if (!context?.total) return saved;
    const years = new Map();
    for (const row of [...(saved?.by_year || []), ...context.by_year]) years.set(row.year, (years.get(row.year) || 0) + row.count);
    const days = new Map();
    for (const [day, count] of [...(saved?.days || []), ...context.days]) days.set(day, (days.get(day) || 0) + count);
    return {
        total: (saved?.total || 0) + context.total,
        by_year: [...years].sort((a, b) => a[0] - b[0]).map(([year, count]) => ({ year, count })),
        week_hours: (context.week_hours || []).map((count, index) => count + (saved?.week_hours?.[index] || 0)),
        days: [...days].sort((a, b) => a[0] - b[0]),
    };
}

// The activity chart: years, or months when only a few years are charted. `first` lets
// the saved posts set the range when conversations are added, so the chart keeps its shape.
function insightActivityView(series, first = insightFirstActiveYear(series?.by_year)) {
    const years = insightYearView(series?.by_year, first);
    if (!years.length || years.length > MONTHLY_SPAN_YEARS || !series?.days?.length) return { unit: 'year', bars: years };
    return { unit: 'month', bars: insightMonthView(series.days, years[0].year) };
}

function insightDayISO(day) {
    return new Date(day * DAY_MS).toISOString().slice(0, 10);
}

// Calendar levels follow the year's own spread (quartiles of its active days), so one
// unusually busy day doesn't wash every other day out to the lightest step.
function insightCalendarYears(days) {
    return [...new Set((days || []).map(([day]) => new Date(day * DAY_MS).getUTCFullYear()))].sort((a, b) => a - b);
}

// One year of posting days as weeks of seven, GitHub style. Days are UTC dates, as the
// server buckets them; a post's local date can differ near midnight.
function insightCalendarView(days, year, firstDay = insightFirstWeekday(), today = Date.now()) {
    const start = Date.UTC(year, 0, 1) / DAY_MS;
    const end = Date.UTC(year + 1, 0, 1) / DAY_MS;
    const counts = new Map((days || []).filter(([day]) => day >= start && day < end));
    const level = insightLevels([...counts.values()]);
    const lead = (new Date(start * DAY_MS).getUTCDay() - firstDay + 7) % 7;
    const weekCount = Math.ceil((lead + end - start) / 7);
    const todayDay = Math.floor(today / DAY_MS);
    const cells = [];
    const months = [];
    let total = 0;
    let busiest = null;
    for (let index = 0; index < weekCount * 7; index += 1) {
        const day = start - lead + index;
        const week = Math.floor(index / 7);
        if (day < start || day >= end) {
            cells.push({ key: `pad-${index}`, outside: true });
            continue;
        }
        const date = new Date(day * DAY_MS);
        if (date.getUTCDate() === 1) {
            months.push({
                key: date.getUTCMonth(),
                // A month starting late in a week gets the next column, where most of it is.
                column: week + (index % 7 > 3 ? 1 : 0) + 1,
                label: date.toLocaleDateString(undefined, { month: 'short', timeZone: 'UTC' }),
            });
        }
        const count = counts.get(day) || 0;
        total += count;
        if (count && (!busiest || count > busiest.count)) busiest = { day, count };
        const iso = insightDayISO(day);
        cells.push({
            key: iso,
            date: iso,
            count,
            level: level(count),
            future: day > todayDay,
            edge: week < 6 ? 'start' : week > weekCount - 7 ? 'end' : '',
            label: `${insightDate(iso)} · ${insightPlural(count, 'post', 'posts')}`,
        });
    }
    const labelled = new Set([1, 3, 5]);
    // The narrow layout shows the same days as twelve small months, each a week per row.
    const monthGrid = Array.from({ length: 12 }, (_, month) => {
        const first = Date.UTC(year, month, 1) / DAY_MS;
        const length = Date.UTC(year, month + 1, 1) / DAY_MS - first;
        const offset = (new Date(first * DAY_MS).getUTCDay() - firstDay + 7) % 7;
        return {
            key: month,
            label: new Date(first * DAY_MS).toLocaleDateString(undefined, { month: 'short', timeZone: 'UTC' }),
            offset,
            cells: Array.from({ length }, (_, date) => {
                const column = (offset + date) % 7;
                return { ...cells[first - start + lead + date], edge: column < 2 ? 'start' : column > 4 ? 'end' : '' };
            }),
        };
    });
    return {
        year,
        weeks: weekCount,
        cells,
        monthGrid,
        months: months.filter((month, index) => month.column <= weekCount
            && (index === 0 || month.column - months[index - 1].column >= 3)),
        weekdays: Array.from({ length: 7 }, (_, step) => ({
            key: step,
            label: labelled.has(step) ? insightWeekday((firstDay + step) % 7, 'short') : '',
        })),
        total,
        activeDays: counts.size,
        busiest: busiest && { date: insightDayISO(busiest.day), count: busiest.count },
    };
}

// Whether the owner posts often enough for highlights about their own posting habits.
function insightPostsOften(series) {
    const total = series?.total || 0;
    const days = series?.days || [];
    if (total < OWN_POSTS_MINIMUM || !days.length) return false;
    const weeks = Math.max(1, (days[days.length - 1][0] - days[0][0] + 1) / 7);
    return total / weeks >= OWN_POSTS_PER_WEEK;
}

function insightRanked(rows, key) {
    const max = Math.max(1, ...(rows || []).map(row => row.count || 0));
    return (rows || []).map(row => ({ ...row, key: row[key], width: Math.max(2, ((row.count || 0) / max) * 100) }));
}

// A count from the conversations around saved posts, as a quieter line beside a saved figure.
function insightContextLine(amount) {
    return amount ? `+${amount} from conversations` : '';
}

// Headline numbers: one big figure each. With conversations included, the cards that have
// conversation totals state the same facts over the combined counts, so the page changes its
// figures without changing shape.
function insightNumbers(data, conversations = false) {
    const total = data.post_mix?.total || 0;
    const context = conversations ? data.context || {} : {};
    const numbers = [];
    if (data.words > 0) {
        const words = data.words + (context.words || 0);
        const novels = words / NOVEL_WORDS;
        const reread = `${insightDuration(words / READING_WORDS_PER_MINUTE * 60)} to reread`;
        numbers.push({
            id: 'words', icon: 'book', label: 'Words saved', value: insightCompact(words),
            detail: novels >= 1 ? `About ${insightPlural(Math.round(novels), 'novel', 'novels')}, ${reread}` : reread,
        });
    }
    if (data.likes_on_x > 0) {
        numbers.push({
            id: 'likes', icon: 'heart', label: 'Likes on X', value: insightCompact(data.likes_on_x),
            detail: `Combined, about ${insightCompact(Math.round(data.likes_on_x / Math.max(1, total)))} per saved post`,
        });
    }
    if (data.video_seconds > 0) {
        const videos = (data.media_types?.video || 0) + (context.media_types?.video || 0);
        const gifs = (data.media_types?.animated_gif || 0) + (context.media_types?.animated_gif || 0);
        numbers.push({
            id: 'video', icon: 'video', label: 'Video saved',
            value: insightDuration(data.video_seconds + (context.video_seconds || 0)),
            detail: gifs ? `${insightPlural(videos, 'video', 'videos')} and ${insightPlural(gifs, 'GIF', 'GIFs')}` : insightPlural(videos, 'video', 'videos'),
            // Search finds saved posts only, so the card searches only when it counts them alone.
            action: context.video_seconds ? null : { kind: 'search', query: 'has:video' },
        });
    }
    if (data.viral > 0) {
        numbers.push({
            id: 'viral', icon: 'starRising', label: 'Viral posts', value: insightNumber(data.viral),
            detail: `With ${insightCompact(VIRAL_LIKES)}+ likes on X`,
            action: { kind: 'search', query: `min_faves:${VIRAL_LIKES}` },
        });
    }
    return numbers;
}

// Posts X no longer serves that the archive kept in full, as is:missing finds them. Reasons
// are recorded for saved posts only, so they keep describing those.
function insightGone(gone, conversations = false) {
    if (!gone || !(gone.saved > 0 || gone.context > 0)) return null;
    const saved = gone.saved || 0;
    const context = gone.context || 0;
    return {
        saved,
        context,
        total: saved + (conversations ? context : 0),
        resurrected: gone.resurrected || 0,
        reasons: (gone.reasons || []).map(row => ({
            ...row, share: insightPercent(row.count, saved), width: Math.max(2, (row.count / Math.max(1, saved)) * 100),
        })),
    };
}

// The day-part a set of posts leans toward, judged per hour so the six night hours and
// the four morning hours compare fairly. Prefers the owner's own tweets, which describe
// their habits; saved posts describe when other people posted.
function insightRhythm(activity, offsetMinutes) {
    const own = insightPostsOften(activity.tweet);
    const week = insightWeekView((own ? activity.tweet : activity.all)?.week_hours, undefined, offsetMinutes);
    if (!week.peak) return null;
    const hourly = 1 / 24;
    const night = week.nightShare / 6;
    const morning = week.morningShare / 4;
    const whose = own ? 'of your tweets' : 'of the posts you saved';
    const peak = `busiest ${insightWeekday(week.peak.day)}s around ${insightHour(week.peak.hour)}`;
    const tile = { id: 'rhythm', label: own ? 'Your posting rhythm' : 'When your saved posts went out' };
    if (night >= morning && night > hourly * 1.2) {
        return { ...tile, icon: 'moon', value: 'Night owl', detail: `${insightPercent(week.nightShare, 1)} ${whose} between ${insightHour(22)} and ${insightHour(4)} · ${peak}` };
    }
    if (morning > night && morning > hourly * 1.2) {
        return { ...tile, icon: 'sun', value: 'Early bird', detail: `${insightPercent(week.morningShare, 1)} ${whose} between ${insightHour(5)} and ${insightHour(9)} · ${peak}` };
    }
    return { ...tile, icon: 'clock', value: 'Daytime regular', detail: `${insightPercent(week.dayShare, 1)} ${whose} between ${insightHour(9)} and ${insightHour(17)} · ${peak}` };
}

function insightHighlights(data, totals) {
    const tiles = [];
    const total = data.post_mix?.total || 0;
    const top = data.top_authors?.[0];
    if (top) {
        tiles.push({
            id: 'favorite-account', label: 'Most saved account',
            avatar: { id: top.author_id, name: top.display_name || top.username },
            value: top.display_name || `@${top.username}`,
            detail: `${top.username ? '@' + top.username + ' · ' : ''}${insightPlural(top.count, 'post', 'posts')}, ${insightPercent(top.count, total)} of everything you saved`,
            action: top.username ? { kind: 'search', query: `from:${top.username}` } : null,
        });
    }
    const rhythm = insightRhythm(data.activity || {});
    if (rhythm) tiles.push(rhythm);
    if (data.busiest_day) {
        tiles.push({
            id: 'busiest-day', icon: 'calendar', label: 'Busiest day on your timeline',
            value: insightDate(data.busiest_day.date),
            detail: `${insightPlural(data.busiest_day.count, 'post', 'posts')} you saved were posted that day`,
            action: { kind: 'search', query: `since:${data.busiest_day.date} until:${insightNextDay(data.busiest_day.date)}` },
        });
    }
    if (data.longest_streak?.days > 1 && insightPostsOften(data.activity?.tweet)) {
        tiles.push({
            id: 'streak', icon: 'fire', label: 'Longest posting streak',
            value: `${insightNumber(data.longest_streak.days)} days in a row`,
            detail: `${insightDate(data.longest_streak.start)} – ${insightDate(data.longest_streak.end)}`,
            action: {
                kind: 'search',
                query: `since:${data.longest_streak.start} until:${insightNextDay(data.longest_streak.end)}`,
                collection: 'tweet',
            },
        });
    }
    if (data.saved_twice > 0) {
        tiles.push({
            id: 'saved-twice', icon: 'bookmarkCheck', label: 'Liked and bookmarked',
            value: insightNumber(data.saved_twice),
            detail: `Posts you couldn't just like once, ${insightPercent(data.saved_twice, totals.bookmark || 0)} of your bookmarks`,
        });
    }
    if (data.authors?.total > 0) {
        tiles.push({
            id: 'regulars', icon: 'people', label: 'Accounts you keep coming back to',
            value: insightNumber(data.authors.regulars),
            detail: `Saved 10 or more times · ${insightNumber(data.authors.one_time)} of ${insightNumber(data.authors.total)} accounts appear just once`,
        });
    }
    return tiles;
}

function insightPostMix(mix) {
    const total = mix?.total || 0;
    if (!total) return [];
    const original = Math.max(0, total - (mix.replies || 0) - (mix.quotes || 0) - (mix.reposts || 0));
    return [
        { key: 'original', icon: 'chat', label: 'Original posts', count: original, query: '-is:reply -is:quote -is:retweet' },
        { key: 'replies', icon: 'reply', label: 'Replies', count: mix.replies || 0, query: 'is:reply' },
        { key: 'quotes', icon: 'quotes', label: 'Quote posts', count: mix.quotes || 0, query: 'is:quote' },
        { key: 'reposts', icon: 'repost', label: 'Reposts', count: mix.reposts || 0, query: 'is:retweet' },
        { key: 'media', icon: 'photo', label: 'With photos or video', count: mix.with_media || 0, query: 'has:media' },
        { key: 'links', icon: 'link', label: 'With links', count: mix.with_links || 0, query: 'has:links' },
        { key: 'verified', icon: 'verified', label: 'From verified accounts', count: mix.verified || 0, query: 'is:verified' },
    ].filter(row => row.count > 0).sort((a, b) => b.count - a.count).map(row => ({
        ...row, share: insightPercent(row.count, total), width: Math.max(2, (row.count / total) * 100),
    }));
}

function statsInsightMethods() {
    return {
        statsInsights: null,
        insightSeries: 'all',
        insightSeriesOptions: [],
        insightYears: [],
        insightYearUnit: 'year',
        insightCalendarYears: [],
        insightCalendarYear: null,
        insightCalendar: null,
        insightWeek: null,
        insightTiles: [],
        insightNumbers: [],
        insightGone: null,
        insightMix: [],
        insightAuthors: [],
        insightHashtags: [],
        insightMentions: [],
        insightRepliers: [],
        insightSparklines: {},
        // One choice, shown in each section it changes, adds the conversations kept around
        // saved posts to the figures that have conversation totals.
        insightConversations: false,
        insightHasConversations: false,

        applyInsights(insights) {
            this.statsInsights = insights || null;
            const data = this.statsInsights;
            if (!data) {
                this.insightSeriesOptions = [];
                this.insightTiles = [];
                this.insightNumbers = [];
                this.insightGone = null;
                this.insightMix = [];
                this.insightAuthors = [];
                this.insightHashtags = [];
                this.insightMentions = [];
                this.insightRepliers = [];
                this.insightYears = [];
                this.insightYearUnit = 'year';
                this.insightCalendarYears = [];
                this.insightCalendar = null;
                this.insightWeek = null;
                this.insightSparklines = {};
                this.insightHasConversations = false;
                this.insightConversations = false;
                return;
            }
            this.insightHasConversations = (data.context?.posts || 0) > 0;
            if (!this.insightHasConversations) this.insightConversations = false;
            const activity = data.activity || {};
            this.insightSeriesOptions = ['all', 'like', 'bookmark', 'tweet']
                .filter(id => (activity[id]?.total || 0) > 0)
                .map(id => ({ id, label: INSIGHT_SERIES_LABELS[id], total: activity[id].total }));
            if (!activity[this.insightSeries]) this.insightSeries = 'all';
            this.buildInsightActivity();
            this.insightSparklines = Object.fromEntries(
                Object.entries(activity).map(([id, series]) => [id, insightYearView(series.by_year)]));
            const totals = Object.fromEntries(Object.entries(activity).map(([id, series]) => [id, series.total || 0]));
            this.insightTiles = insightHighlights(data, totals);
            this.insightMix = insightPostMix(data.post_mix);
            this.insightAuthors = insightRanked(data.top_authors, 'author_id');
            this.insightHashtags = data.hashtags || [];
            this.insightMentions = insightRanked(data.mentions, 'username');
            this.insightRepliers = insightRanked(data.context?.reply_authors, 'author_id');
            this.buildInsightConversations();
        },

        // Rebuilds the figures that have conversation totals for the current switch state.
        buildInsightConversations() {
            const data = this.statsInsights;
            const on = this.insightConversations;
            this.insightNumbers = insightNumbers(data, on);
            this.insightGone = insightGone(data.gone, on);
        },

        // With conversations included, the charts cover every saved post and the conversations
        // around them; conversations belong to no collection, so the collection filter steps aside.
        insightActivitySeries() {
            const activity = this.statsInsights?.activity;
            if (this.insightConversations) return insightMergeSeries(activity?.all, this.statsInsights?.context?.activity);
            return activity?.[this.insightSeries];
        },

        setInsightConversations(on) {
            this.insightConversations = Boolean(on) && this.insightHasConversations;
            this.buildInsightConversations();
            this.buildInsightActivity();
        },

        buildInsightActivity() {
            const series = this.insightActivitySeries();
            const saved = this.statsInsights?.activity?.all;
            const chart = this.insightConversations
                ? insightActivityView(series, insightFirstActiveYear(saved?.by_year))
                : insightActivityView(series);
            this.insightYears = chart.bars;
            this.insightYearUnit = chart.unit;
            this.insightWeek = insightWeekView(series?.week_hours);
            this.insightCalendarYears = insightCalendarYears(series?.days);
            const years = this.insightCalendarYears;
            // Keep the year being looked at when switching collections, if it has posts there.
            if (!years.includes(this.insightCalendarYear)) this.insightCalendarYear = years[years.length - 1] ?? null;
            this.buildInsightCalendar();
        },

        buildInsightCalendar() {
            const days = this.insightActivitySeries()?.days;
            this.insightCalendar = this.insightCalendarYear === null ? null : insightCalendarView(days, this.insightCalendarYear);
        },

        // On narrow screens the calendar scrolls sideways; bring its latest past day into
        // view. Waits a frame because the dialog has no layout until it is shown.
        stepInsightCalendar(step) {
            const years = this.insightCalendarYears;
            const next = years[years.indexOf(this.insightCalendarYear) + step];
            if (next === undefined) return;
            this.insightCalendarYear = next;
            this.buildInsightCalendar();
        },

        insightCalendarCanStep(step) {
            const years = this.insightCalendarYears;
            const index = years.indexOf(this.insightCalendarYear);
            return index >= 0 && years[index + step] !== undefined;
        },

        insightCalendarSummary() {
            const calendar = this.insightCalendar;
            if (!calendar?.total) return '';
            const parts = [`${insightPlural(calendar.total, 'post', 'posts')} on ${insightPlural(calendar.activeDays, 'day', 'days')}`];
            if (calendar.busiest) parts.push(`busiest ${insightDate(calendar.busiest.date, { month: 'short', day: 'numeric' })} with ${insightNumber(calendar.busiest.count)}`);
            return parts.join(' · ');
        },

        // Search finds saved posts only, so chart selections search nothing while the charts
        // include conversations.
        insightDaySearch(cell) {
            if (!cell?.count || this.insightConversations) return;
            this.runInsightAction({
                kind: 'search',
                query: `since:${cell.date} until:${insightNextDay(cell.date)}`,
                collection: this.insightSeries,
            });
        },

        selectInsightSeries(id) {
            if (this.insightSeries === id) return;
            this.insightSeries = id;
            this.buildInsightActivity();
        },

        insightActivitySummary() {
            const week = this.insightWeek;
            if (!week?.peak) return '';
            return `Busiest around ${insightHour(week.peak.hour)} on ${insightWeekday(week.peak.day)}s · `
                + `${insightPercent(week.weekendShare, 1)} on weekends · your local time`;
        },

        insightYearSearch(bar) {
            if (this.insightConversations) return;
            this.runInsightAction({
                kind: 'search',
                query: `since:${bar.since} until:${bar.until}`,
                collection: this.insightSeries,
            });
        },

        runInsightAction(action) {
            if (!action) return;
            nookSearch(this, action.query, INSIGHT_SERIES_FEEDS[action.collection || 'all']);
        },

        insightCollectionIcon(rawType) {
            return { like: 'heart', bookmark: 'bookmark', tweet: 'person' }[rawType] || 'layers';
        },

        insightAuthorLabel(author) {
            return author.display_name || (author.username ? `@${author.username}` : 'Unknown account');
        },

        insightContextNote() {
            const count = this.statsSummary?.context_posts || 0;
            return insightContextLine(count && insightPlural(count, 'post', 'posts'));
        },

        insightCollectionsLabel(author) {
            const parts = [];
            if (author.likes) parts.push(insightPlural(author.likes, 'like', 'likes'));
            if (author.bookmarks) parts.push(insightPlural(author.bookmarks, 'bookmark', 'bookmarks'));
            return parts.join(' · ');
        },

        insightNumber,
        insightCompact,
        insightDate,
    };
}
