// Analytics modal helpers: collection links, tag shortcuts, scrolling.
// Reads the stats snapshot and calls app actions only.
const NOOK_COLLECTION_FEEDS = { bookmark: 'bookmarks', like: 'likes', tweet: 'tweets', Bookmarks: 'bookmarks', Likes: 'likes', 'Authored Tweets': 'tweets' };

function nookCollectionLabel(row) { return row.collection === 'Authored Tweets' ? 'Your tweets' : row.collection; }
// Stats dates read "Jan 28, 2026 3:04 pm"; the range only needs the date.
function nookDateOnly(text) { return String(text || '').replace(/\s+\d{1,2}:\d{2}\s*[ap]m$/i, ''); }
function nookCollectionRange(row) {
  return row.oldest && row.oldest !== '-' ? `${nookDateOnly(row.oldest)} – ${nookDateOnly(row.newest)}` : 'Nothing saved yet';
}
function nookCollectionBehind(row) { return ['resume older', 'incomplete'].includes(row.backfill_status); }
function nookOpenCollection(app, row) {
  const feed = NOOK_COLLECTION_FEEDS[row.raw_type] || NOOK_COLLECTION_FEEDS[row.collection];
  app.closeStatsModal();
  if (feed) app.selectFeedOption('collection', feed);
  window.scrollTo({ top: 0 });
}
function nookShowTag(app, name) {
  app.closeStatsModal();
  app.collectionFilter = 'all';
  app.searchQuery = 'tag:' + (name.includes(' ') ? `"${name}"` : name);
  app.search();
}
function nookManageTags(app) {
  app.closeStatsModal();
  app.openSettings();
  nookSettingsNavigate(app, 'tags');
}
function nookScrollTo(id) {
  const target = document.getElementById(id);
  target?.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
}
// "19 days of posts" / "8 months of posts" / "About 6 years of posts" from the overview dates.
function nookSpanLabel(oldest, newest) {
  const parse = text => { const time = Date.parse(String(text || '').replace(/\s+\d{1,2}:\d{2}\s*[ap]m$/i, '')); return Number.isNaN(time) ? null : time; };
  const start = parse(oldest), end = parse(newest);
  if (start === null || end === null || end < start) return '';
  const days = Math.max(1, Math.round((end - start) / 86400000));
  if (days < 60) return `${days} ${days === 1 ? 'day' : 'days'} of posts`;
  if (days < 730) return `${Math.round(days / 30.44)} months of posts`;
  return `About ${Math.round(days / 365.25)} years of posts`;
}
