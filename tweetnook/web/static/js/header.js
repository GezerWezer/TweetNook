// Header, collection tabs, and the sync panel.

function nookTabKey(event, app) {
  const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
  if (!keys.includes(event.key)) return;
  event.preventDefault();
  const tabs = [...event.currentTarget.closest('[role="tablist"]').querySelectorAll('[role="tab"]')];
  const current = tabs.indexOf(event.currentTarget);
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  app.selectFeedOption('collection', app.feedOptions('collection')[next].value);
  tabs[next].focus();
  nookRevealTab(tabs[next]);
}
// Sync activity: anchor the drawer under its top-bar button, close it on outside clicks and Escape.
function nookAnchorActivity() {
  const button = document.querySelector('.nook-activity-button');
  if (!button) return;
  const rect = button.getBoundingClientRect(), root = document.documentElement.style;
  root.setProperty('--nook-activity-top', `${Math.round(rect.bottom + 8)}px`);
  // Same widths as the drawer styles; keep the whole panel on screen on narrow viewports.
  const width = window.innerWidth <= 640 ? window.innerWidth - 16 : Math.min(380, window.innerWidth - 32);
  root.setProperty('--nook-activity-right', `${Math.max(8, Math.round(Math.min(window.innerWidth - rect.right, window.innerWidth - width - 8)))}px`);
}
function nookToggleActivity(app) { nookAnchorActivity(); app.toggleActivityDrawer(); }
// The first click outside only closes the panel, so it can't also open a post underneath.
document.addEventListener('click', event => {
  const root = document.querySelector('[x-data="tweetApp()"]'), app = root && window.Alpine?.$data(root);
  if (!app?.activityDrawerOpen || event.target.closest('#nook-activity-drawer, .nook-activity-button')) return;
  event.preventDefault();
  event.stopPropagation();
  app.activityDrawerOpen = false;
}, true);
document.addEventListener('keydown', event => {
  const root = document.querySelector('[x-data="tweetApp()"]'), app = root && window.Alpine?.$data(root);
  if (event.key !== 'Escape' || !app?.activityDrawerOpen || app.showSettingsModal || app.showStatsModal) return;
  event.stopPropagation();
  nookCloseActivity(app);
});
function nookCloseActivity(app) {
  if (!app.activityDrawerOpen) return;
  app.activityDrawerOpen = false;
  document.querySelector('.nook-activity-button')?.focus({ preventScroll: true });
}
document.addEventListener('DOMContentLoaded', nookAnchorActivity);
window.addEventListener('resize', nookAnchorActivity, { passive: true });
// Tuck the tabs away after 24px of downward scrolling past the header; any 24px upward scroll, focus
// inside the header, or returning to the top brings them back. The title row always stays.
function nookHeaderAutoHide() {
  const header = document.querySelector('.nook-header');
  if (!header) return;
  let last = window.scrollY, travel = 0, queued = false;
  const update = () => {
    queued = false;
    const y = Math.max(0, window.scrollY), delta = y - last;
    last = y;
    // Pointer clicks leave tabs focused too. Only keyboard focus should keep
    // the strip visible while the reader scrolls through a collection.
    const keyboardFocus = header.contains(document.activeElement) && document.activeElement?.matches(':focus-visible');
    if (y <= header.offsetHeight || keyboardFocus) { header.classList.remove('tabs-hidden'); travel = 0; return; }
    travel = Math.sign(delta) === Math.sign(travel) ? travel + delta : delta;
    if (travel > 24) header.classList.add('tabs-hidden');
    else if (travel < -24) header.classList.remove('tabs-hidden');
  };
  window.addEventListener('scroll', () => { if (!queued) { queued = true; requestAnimationFrame(update); } }, { passive: true });
  header.addEventListener('focusin', () => header.classList.remove('tabs-hidden'));
}
document.addEventListener('DOMContentLoaded', nookHeaderAutoHide);
// Scroll the strip itself (never the page) so the chosen tab clears the edge fade.
function nookRevealTab(tab) {
  const nav = tab.closest('.nook-tabs');
  if (!nav || nav.scrollWidth <= nav.clientWidth) return;
  const left = tab.offsetLeft - 32, right = tab.offsetLeft + tab.offsetWidth + 32;
  if (left < nav.scrollLeft) nav.scrollLeft = Math.max(0, left);
  else if (right > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = right - nav.clientWidth;
}
function nookTabsInit(frame) {
  const nav = frame.querySelector('.nook-tabs');
  const update = () => {
    const max = nav.scrollWidth - nav.clientWidth;
    frame.classList.toggle('is-clipped-start', nav.scrollLeft > 1);
    frame.classList.toggle('is-clipped-end', nav.scrollLeft < max - 1);
  };
  nav.addEventListener('scroll', update, { passive: true });
  const observer = new ResizeObserver(() => {
    const selected = nav.querySelector('[aria-selected="true"]');
    if (selected) nookRevealTab(selected);
    update();
  });
  observer.observe(nav);
  // Text-size changes resize the labels without resizing the strip.
  requestAnimationFrame(() => nav.querySelectorAll('.nook-tab span').forEach(label => observer.observe(label)));
}

// Sync panel. Runs inside the app scope, so activity, schedule, and actions are the app's own.
// What to sync (sync.collections): one server setting shared by the sync panel and Settings → Schedule.
// Sync now and scheduled syncs both read it; at least one collection stays selected.
const NOOK_COLLECTION_ORDER = ['bookmarks', 'likes', 'tweets'];
const NOOK_COLLECTION_NAMES = { bookmarks: 'bookmarks', likes: 'likes', tweets: 'your tweets' };
async function nookLoadSyncCollections(app) {
  try {
    const response = await fetch('/api/config');
    if (!response.ok) return;
    const collections = (await response.json()).values?.sync?.collections;
    if (Array.isArray(collections) && collections.length) app.nookSyncCollections = collections;
  } catch (_) {
    // Keep the default; the panel still works with bookmarks and likes.
  }
}
async function nookToggleSyncCollection(app, key) {
  const previous = app.nookSyncCollections || [];
  const picked = previous.includes(key) ? previous.filter(item => item !== key) : [...previous, key];
  if (!picked.length) return;
  const next = NOOK_COLLECTION_ORDER.filter(item => picked.includes(item));
  app.nookSyncCollections = next;
  // During first-run Setup the config API is gated; the Schedule step saves this on Continue.
  if (app.showSetupModal && !app.setupData?.completed) return;
  try {
    const response = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ changes: { 'sync.collections': next } }),
    });
    if (!response.ok) throw new Error('Could not save what to sync');
    const saved = await response.json();
    const collections = saved.values?.sync?.collections || next;
    app.nookSyncCollections = collections;
    // Keep a loaded Configuration page in step without marking anything unsaved.
    if (app.configData?.sync) app.configData.sync.collections = [...collections];
    if (app.configOriginal?.sync) app.configOriginal.sync.collections = [...collections];
    if (Array.isArray(saved.explicit)) app.configExplicit = saved.explicit;
  } catch (_) {
    app.nookSyncCollections = previous;
  }
}
function nookSyncCollectionsLabel(app) {
  const list = NOOK_COLLECTION_ORDER.filter(key => (app.nookSyncCollections || []).includes(key)).map(key => NOOK_COLLECTION_NAMES[key]);
  if (!list.length) return 'Nothing selected';
  const text = list.length > 1 ? `${list.slice(0, -1).join(', ')} and ${list[list.length - 1]}` : list[0];
  return text[0].toUpperCase() + text.slice(1);
}
// Shared presentation for live steps and saved run snapshots; all counts come from the pipeline.
function nookSyncStepCopy(step, state) {
  const key = step.key || '', collection = { bookmarks: 'Bookmarks', likes: 'Likes', tweets: 'your posts' }[key.split(':')[1]];
  const copies = {
    'archive-auth': ['Connect to X', 'connect your Twitter/X account', 'Connecting your Twitter/X account.', 'Connected your Twitter/X account.'],
    'archive-enrich': ['Enrich saved posts', 'fill in missing details for posts in your archive', 'Filling in missing details for posts in your archive.', 'Filled in missing details for posts in your archive.'],
    threads: ['Expand conversations', 'find replies and quotes for saved posts', 'Finding replies and quotes for saved posts.', 'Found replies and quotes for saved posts.'],
    resurrection: ['Retry unavailable posts', 'retry posts that were unavailable', 'Retrying posts that were unavailable.', 'Retried unavailable posts.'],
    'check:saves': ['Check saved posts', 'check that saved posts are still on X', 'Checking that saved posts are still on X.', 'Checked saved posts.'],
    'check:unavailable': ['Recheck unavailable posts', 'see whether unavailable posts are back', 'Checking whether unavailable posts are back.', 'Rechecked unavailable posts.'],
    'check:context': ['Check context posts', 'check this run’s share of quoted and thread posts', 'Checking this run’s share of quoted and thread posts.', 'Checked this run’s share of context posts.'],
    articles: ['Refresh article details', 'update article details in saved posts', 'Updating article details in saved posts.', 'Updated article details in saved posts.'],
    media: ['Download media', 'download photos, GIFs, and videos', 'Downloading photos, GIFs, and videos.', 'Downloaded media attached to saved posts.'],
    urls: ['Refresh link previews', 'refresh previews for saved links', 'Refreshing previews for saved links.', 'Refreshed previews for saved links.'],
    tagging: ['Tag saved posts', 'tag saved posts', 'Tagging saved posts.', 'Tagged saved posts.'],
  };
  let copy = copies[key];
  if (key.startsWith('preflight:')) copy = ['Check collection access', 'check access to your selected collections', 'Checking access to your selected collections.', 'Confirmed access to your selected collections.'];
  if (key.startsWith('sync:') && collection) copy = [`Sync ${collection}`, `save ${collection === 'Likes' ? 'posts you’ve liked' : collection} to the archive`, `Saving ${collection === 'Likes' ? 'posts you’ve liked' : collection} to the archive.`, `Saved ${collection === 'Likes' ? 'posts you’ve liked' : collection} to the archive.`];
  if (!copy) return { title: step.title || 'Activity step', explanation: { pending: 'This step will run after earlier steps finish.', active: 'Processing this step.', complete: 'This step finished.', skipped: 'This step was not needed.', paused: 'This step is paused.', failed: 'This step could not finish.', cancelled: 'This step stopped before completion.' }[state] || 'Processing this step.' };
  const [title, action, active, complete] = copy;
  return { title, explanation: { pending: `Will ${action}.`, active, complete, skipped: `Would ${action}.`, paused: 'This step is paused.', failed: `Could not ${action}.`, cancelled: 'This step stopped before completion.' }[state] || active };
}
function nookSyncStats(text) {
  return String(text || '').split(' · ').map(part => {
    const match = part.trim().match(/^([\d,]+(?:\.\d+)?(?:\s*\/\s*[\d,]+)?)\s+(.+)$/);
    if (!match) return null;
    const raw = match[1], label = match[2].replace(/\btweets\b/g, 'posts').replace(/\btweet\b/g, 'post');
    const num = raw.includes('/') ? null : Number(raw.replace(/,/g, ''));
    if (num !== null && !Number.isFinite(num)) return null;
    if (label === 'MiB') return { value: num >= 1024 ? `${(num / 1024).toFixed(1)} GiB` : `${num.toLocaleString()} MiB`, label: 'size', num: null };
    return { value: num === null ? raw : num.toLocaleString(), label, num };
  }).filter(Boolean);
}
function nookSyncBreakdown(model) {
  const counts = model.stats.filter(stat => stat.num !== null), plain = model.stats.filter(stat => stat.num === null);
  const aggregate = counts.length > 2 ? counts.find(stat => Math.abs(counts.filter(other => other !== stat).reduce((sum, other) => sum + other.num, 0) - stat.num) <= 1) : null;
  const rank = { done: 0, neutral: 1, failed: 2, rest: 3, plain: 4 };
  const kindOf = label => /fail|error|cancel/i.test(label) ? 'failed' : /not run/i.test(label) ? 'rest' : /known|unavailable|skip|transient/i.test(label) ? 'neutral' : 'done';
  let slots, bars = [], doneSeen = false;
  if (!(model.total > 0)) slots = counts.filter(stat => stat !== aggregate).concat(plain).map(stat => ({ ...stat, kind: 'plain' })).slice(0, 4);
  else {
    const parts = counts.filter(stat => stat !== aggregate).map(stat => {
      let kind = kindOf(stat.label);
      if (kind === 'done') { if (doneSeen) kind = 'neutral'; doneSeen = true; }
      return { ...stat, kind };
    }).sort((a, b) => rank[a.kind] - rank[b.kind]);
    const sum = parts.reduce((total, stat) => total + stat.num, 0), base = Math.max(model.total, sum), rest = base - sum;
    bars = parts.filter(stat => stat.kind !== 'rest' && stat.num > 0).map(stat => ({ kind: stat.kind, width: stat.num / base * 100 }));
    slots = [...parts];
    if (parts.length && rest > 0 && model.state !== 'complete') slots.push({ value: rest.toLocaleString(), label: 'remaining', num: rest, kind: 'rest' });
    slots.push(...plain.map(stat => ({ ...stat, kind: 'plain' })));
    while (slots.length > 4) { const zero = slots.findIndex(stat => stat.num === 0 && stat.kind !== 'done'); slots.splice(zero >= 0 ? zero : slots.length - 1, 1); }
  }
  if (!bars.length && model.state === 'complete') bars = [{ kind: 'done', width: 100 }];
  if (!bars.length && model.state === 'active') bars = [{ kind: 'done is-indeterminate', width: 30 }];
  const label = slots.length ? slots.map(slot => `${slot.value} ${slot.label}`).join(', ') : model.status;
  if (!slots.length) slots = [{ value: '—', label: { pending: 'Not started', skipped: 'Skipped', active: 'Starting', paused: 'Paused', cancelled: 'Nothing counted', failed: 'Nothing counted', complete: 'Nothing to count' }[model.state] || 'Nothing to count', num: null, kind: 'plain' }];
  return { ...model, slots: slots.map(slot => ({ ...slot, label: ({ 'already known': 'Known', 'posts saved': 'Saved' }[slot.label] || slot.label.replace(/^./, c => c.toUpperCase())) })), bars, label };
}
// Jobs list: every job the CLI can run. The server (/api/jobs) owns state and option specs;
// the panel shows the selected job's run on top and the rest as rows.
const NOOK_JOB_ICONS = {
  options: 'M15 13a4 4 0 0 1 3.87 3H21v2h-2.13a4 4 0 0 1-7.74 0H3v-2h8.13A4 4 0 0 1 15 13m0 2a2 2 0 1 0 0 4 2 2 0 0 0 0-4M9 3a4 4 0 0 1 3.87 3H21v2h-8.13a4 4 0 0 1-7.74 0H3V6h2.13A4 4 0 0 1 9 3m0 2a2 2 0 1 0 0 4 2 2 0 0 0 0-4',
  sync: 'M5 16c0 .72 0 1.2.03 1.56s.08.52.13.62q.23.43.66.66c.1.05.26.1.62.13.37.03.84.03 1.56.03h8c.72 0 1.2 0 1.56-.03s.52-.08.62-.13q.43-.23.66-.66c.05-.1.1-.26.13-.62.03-.37.03-.84.03-1.56v-2h2v2q.01 1.02-.04 1.72a4 4 0 0 1-.34 1.37 3.5 3.5 0 0 1-1.53 1.53c-.43.22-.89.3-1.37.34q-.7.05-1.72.04H8q-1.03.01-1.72-.04a4 4 0 0 1-1.37-.34 3.5 3.5 0 0 1-1.53-1.53 4 4 0 0 1-.34-1.37Q2.99 17.02 3 16v-2h2z M13 12.34l3.3-3.3 1.4 1.42-5.7 5.7-5.7-5.7 1.4-1.42 3.3 3.3V3h2z',
  check: 'M12 2c2.4 0 4.6.85 6.33 2.26l.96-.97 1.42 1.42-8 8a1 1 0 1 1-1.42-1.42l2.38-2.37a3.5 3.5 0 1 0 1.73 3.93l1.98-1.99A5.52 5.52 0 0 1 12 17.5a5.5 5.5 0 1 1 3.12-10.03l1.78-1.79a8 8 0 1 0 2.46 3.2l1.5-1.5A10 10 0 1 1 12 2',
  threads: 'M1.75 10a8 8 0 0 1 8-8h4.37a8.13 8.13 0 0 1 3.93 15.24L10 21.7V18h-.07a8 8 0 0 1-8.18-8m2 0a6 6 0 0 0 6.14 6H12v2.3l5.09-2.8A6.13 6.13 0 0 0 14.12 4H9.75a6 6 0 0 0-6 6',
  articles: 'M18.07 12.7a3 3 0 0 1 4.23 4.23l-4.47 4.47a5 5 0 0 1-3.08 1.45l-2.85.25.25-2.85a5 5 0 0 1 1.45-3.08zm2.81 1.42a1 1 0 0 0-1.4 0l-4.47 4.46a3 3 0 0 0-.86 1.85l-.05.46.47-.04A3 3 0 0 0 16.4 20l4.47-4.47a1 1 0 0 0 0-1.4 M16 2q1.02-.01 1.72.04c.48.04.94.12 1.37.34.66.34 1.2.87 1.53 1.53.22.43.3.89.34 1.37q.05.7.04 1.72v2h-2V7c0-.72 0-1.2-.03-1.56a2 2 0 0 0-.13-.62 1.5 1.5 0 0 0-.66-.66c-.1-.05-.26-.1-.62-.13S16.72 4 16 4H8c-.72 0-1.2 0-1.56.03s-.52.08-.62.13a1.5 1.5 0 0 0-.66.66c-.05.1-.1.26-.13.62S5 6.28 5 7v10c0 .72 0 1.2.03 1.56s.08.52.13.62q.23.43.66.66c.1.05.26.1.62.13.37.03.84.03 1.56.03h1v2H8q-1.03.01-1.72-.04a4 4 0 0 1-1.37-.34 3.5 3.5 0 0 1-1.53-1.53 4 4 0 0 1-.34-1.37Q2.99 18.02 3 17V7q-.01-1.03.04-1.72c.04-.48.12-.94.34-1.37a3.5 3.5 0 0 1 1.53-1.53c.43-.22.89-.3 1.37-.34Q6.98 1.99 8 2z M13 13H8v-2h5zm3-4H8V7h8z',
  media: 'M15 7a2 2 0 1 1 0 4 2 2 0 0 1 0-4 M14.1 2.5q1.65-.02 2.7.06c.74.06 1.38.18 1.97.48a5 5 0 0 1 2.19 2.19c.3.6.42 1.23.48 1.96q.08 1.06.06 2.71v4.2q.02 1.65-.06 2.7a5 5 0 0 1-.48 1.97 5 5 0 0 1-2.19 2.19c-.6.3-1.23.42-1.96.48q-1.06.08-2.71.06H9.9q-1.65.02-2.7-.06a5 5 0 0 1-1.97-.48 5 5 0 0 1-2.19-2.19c-.3-.6-.42-1.23-.48-1.96q-.07-1.06-.06-2.71V9.9q-.02-1.65.06-2.7c.06-.74.18-1.38.48-1.97a5 5 0 0 1 2.19-2.19c.6-.3 1.23-.42 1.96-.48q1.06-.07 2.71-.06zM4.5 15.44q.01.7.05 1.2c.05.61.14.96.28 1.22a3 3 0 0 0 1.3 1.31c.27.14.62.23 1.22.28.62.05 1.41.05 2.55.05h5.18l-7.12-7.13zM9.9 4.5c-1.14 0-1.93 0-2.55.05-.6.05-.95.14-1.21.28a3 3 0 0 0-1.31 1.3 3 3 0 0 0-.28 1.22c-.05.62-.05 1.41-.05 2.55v2.87l3.54-3.14 9.63 9.63.2-.09a3 3 0 0 0 1.3-1.3c.14-.27.23-.62.28-1.22.05-.62.05-1.41.05-2.55V9.9c0-1.14 0-1.93-.05-2.55a3 3 0 0 0-.28-1.21 3 3 0 0 0-1.3-1.31 3 3 0 0 0-1.22-.28c-.62-.05-1.41-.05-2.55-.05z',
  urls: 'M1 12a7 7 0 0 1 7-7h2v2H8a5 5 0 1 0 0 10h2v2H8a7 7 0 0 1-7-7m20 0a5 5 0 0 0-5-5h-2V5h2a7 7 0 1 1 0 14h-2v-2h2a5 5 0 0 0 5-5m-4-1v2H7v-2z',
  tagging: 'M9 7a2 2 0 1 0 0 4 2 2 0 0 0 0-4 M12 2H7.04a3.5 3.5 0 0 0-2.48 1.03L3.03 4.56A3.5 3.5 0 0 0 2 7.04v4.92c0 .93.37 1.82 1.03 2.48l6.43 6.44a5 5 0 0 0 7.08 0l4.34-4.34A5 5 0 0 0 22.24 12h-2.07a3 3 0 0 1-.71 3.12l-4.34 4.34a3 3 0 0 1-4.24 0l-6.44-6.43A1.5 1.5 0 0 1 4 11.96V7.04q.01-.62.44-1.07l1.53-1.53A1.5 1.5 0 0 1 7.04 4H12z M18.57 2c0 1.9 1.54 3.43 3.43 3.43v1.14A3.43 3.43 0 0 0 18.57 10h-1.14c0-1.9-1.54-3.43-3.43-3.43V5.43c1.9 0 3.43-1.54 3.43-3.43z',
  import: 'M12 16h-2v-2h2zm2-2h-2v-2h2zm-2-2h-2v-2h2zm2-2h-2V8h2zm-2-2h-2V6h2z M14.1 2.5q1.65-.02 2.7.06c.74.06 1.38.18 1.97.48a5 5 0 0 1 2.19 2.19c.3.6.42 1.23.48 1.96q.08 1.06.06 2.71v4.2q.02 1.65-.06 2.7a5 5 0 0 1-.48 1.97 5 5 0 0 1-2.19 2.19c-.6.3-1.23.42-1.96.48q-1.06.08-2.71.06H9.9q-1.65.02-2.7-.06a5 5 0 0 1-1.97-.48 5 5 0 0 1-2.19-2.19c-.3-.6-.42-1.23-.48-1.96q-.07-1.06-.06-2.71V9.9q-.02-1.65.06-2.7c.06-.74.18-1.38.48-1.97a5 5 0 0 1 2.19-2.19c.6-.3 1.23-.42 1.96-.48q1.06-.07 2.71-.06zm-4.2 2c-1.14 0-1.93 0-2.55.05-.6.05-.95.14-1.21.28a3 3 0 0 0-1.31 1.3 3 3 0 0 0-.28 1.22c-.05.62-.05 1.41-.05 2.55v4.2c0 1.14 0 1.93.05 2.55.05.6.14.95.28 1.21a3 3 0 0 0 1.3 1.31c.27.14.62.23 1.22.28.62.05 1.41.05 2.55.05h4.2c1.14 0 1.93 0 2.55-.05.6-.05.95-.14 1.21-.28a3 3 0 0 0 1.31-1.3c.14-.27.23-.62.28-1.22.05-.62.05-1.41.05-2.55V9.9c0-1.14 0-1.93-.05-2.55a3 3 0 0 0-.28-1.21 3 3 0 0 0-1.3-1.31 3 3 0 0 0-1.22-.28c-.62-.05-1.41-.05-2.55-.05H14V6h-2V4.5z',
  enrich: 'M21.45 13A9.5 9.5 0 0 1 5 18.42V21H3v-5.96L2.99 15H9v2H6.41a7.5 7.5 0 0 0 13.02-4zM12 2.5a9.5 9.5 0 0 1 7 3.08V3h2v5.96l.01.04H15V7h2.59a7.5 7.5 0 0 0-13.02 4H2.55A9.5 9.5 0 0 1 12 2.5',
  queued: 'M9.33 4.46a8 8 0 1 0 8.9 12.57l1.55 1.26A9.98 9.98 0 0 1 2 12a10 10 0 0 1 6.67-9.43zm10.64 9.98a1 1 0 1 1 .75 1.86 1 1 0 0 1-.75-1.86 M13 11.32l4.37 1.75-.74 1.86L11 12.68V6.5h2zm7.63-.25a1 1 0 1 1 .74 1.86 1 1 0 0 1-.74-1.86m-.66-3.37a1 1 0 1 1 .75 1.86 1 1 0 0 1-.75-1.86m-2.31-2.77a1 1 0 1 1 1.41 1.41 1 1 0 0 1-1.41-1.41M14.52 3.3a1 1 0 1 1 1.85.77 1 1 0 0 1-1.85-.77m-3.44-.68a1 1 0 1 1 1.85.76 1 1 0 0 1-1.85-.76',
  failed: 'M12 15.2a1.2 1.2 0 1 1 0 2.4 1.2 1.2 0 0 1 0-2.4m0-6.45c.8 0 1.4.7 1.29 1.48L12.75 14h-1.5l-.54-3.77A1.3 1.3 0 0 1 12 8.75 M9.4 3.5a3 3 0 0 1 5.2 0L22.4 17a3 3 0 0 1-2.6 4.5H4.2A3 3 0 0 1 1.6 17zm3.47 1a1 1 0 0 0-1.74 0L3.34 18a1 1 0 0 0 .87 1.5h15.58a1 1 0 0 0 .87-1.5z',
  stop: 'M5 7c0-1.1.9-2 2-2h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z',
  play: 'M6 17.93V6.07a2 2 0 0 1 3.05-1.7l9.67 5.92a2 2 0 0 1 0 3.42l-9.67 5.92A2 2 0 0 1 6 17.93',
  remove: 'M18.7 6.7 13.42 12l5.3 5.3-1.42 1.4-5.3-5.29-5.28 5.3-1.42-1.42L10.6 12 5.3 6.7l1.42-1.4L12 10.58l5.3-5.3z',
  history: 'M12 2.5a9.5 9.5 0 1 1-9.43 10.62l1.98-.24A7.5 7.5 0 1 0 6.41 7H9.5v2h-6V3h2v2.07A9.5 9.5 0 0 1 12 2.5 M13 11.59l3.2 3.2-1.4 1.42-3.8-3.8V8h2z',
};
const NOOK_JOB_VERBS = { sync: 'Syncing', check: 'Checking', threads: 'Expanding conversations', articles: 'Refreshing articles', media: 'Downloading media', urls: 'Refreshing link previews', tagging: 'Tagging posts', import: 'Importing archive', enrich: 'Enriching archive' };
// A run's headline is computed by the panel's own caption logic against that run.
let nookRunOverride;
function nookClock(ts) { return new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
// When a past run happened: its time today, then Yesterday, then the date.
function nookJobWhen(ts) {
  if (!ts) return '';
  const date = new Date(ts * 1000), today = new Date(), yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return nookClock(ts);
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}
// When a future run happens: its time within a day, then the weekday, then the date.
function nookJobNextTime(ts) {
  const seconds = ts - Date.now() / 1000;
  if (seconds < 86400) return nookClock(ts);
  if (seconds < 6 * 86400) return new Date(ts * 1000).toLocaleDateString([], { weekday: 'short' });
  return new Date(ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' });
}
function nookDuration(seconds) {
  const whole = Math.max(Math.round(Number(seconds) || 0), 0);
  if (whole < 60) return `${whole}s`;
  const minutes = Math.round(whole / 60);
  return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function nookJobsSheet(panel, app) {
  let gesture = null, resetTimer = null, shadeFrame = null, blockClickUntil = 0;
  const scrim = document.querySelector('.nook-sync-scrim');
  const mobile = () => window.innerWidth <= 640;
  const blocksBackground = () => mobile() && (app.activityDrawerOpen || app.activityShadeProgress > 0);
  const wheel = event => {
    if (blocksBackground() && !panel.contains(event.target) && event.cancelable) event.preventDefault();
  };
  const keydown = event => {
    if (!blocksBackground() || panel.contains(event.target) || event.target.closest('input, textarea, select, [contenteditable="true"], button, a')) return;
    if ([' ', 'ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End'].includes(event.key)) event.preventDefault();
  };
  const anchor = () => {
    if (app.activityDrawerOpen && mobile()) {
      document.documentElement.style.setProperty('--nook-jobs-page-top', `${window.scrollY}px`);
      const height = window.innerHeight;
      const outerHeight = window.outerHeight || window.screen?.height || height;
      document.documentElement.style.setProperty('--nook-jobs-bottom-bleed', `${Math.max(0, outerHeight - height)}px`);
    }
  };
  const animateShade = locked => {
    if (shadeFrame !== null) cancelAnimationFrame(shadeFrame);
    shadeFrame = null;
    if (!mobile() || !scrim) {
      document.documentElement.classList.remove('nook-jobs-shading');
      app.updateBrowserTheme?.(locked ? 1 : 0);
      return;
    }
    const from = app.activityShadeProgress || 0, target = locked ? 1 : 0;
    const start = performance.now(), duration = 220 * Math.abs(target - from);
    const paint = progress => {
      document.documentElement.classList.toggle('nook-jobs-shading', progress > 0);
      app.updateBrowserTheme?.(progress);
    };
    if (!duration || window.matchMedia('(prefers-reduced-motion: reduce)').matches) { paint(target); return; }
    const tick = now => {
      const time = Math.min(1, Math.max(0, (now - start) / duration));
      const eased = time * time * (3 - 2 * time);
      paint(from + (target - from) * eased);
      shadeFrame = time < 1 ? requestAnimationFrame(tick) : null;
    };
    shadeFrame = requestAnimationFrame(tick);
  };
  const reset = () => {
    gesture = null;
    panel.classList.remove('cs-dragging', 'cs-settling');
    panel.style.removeProperty('--cs-sheet-y');
  };
  const sync = () => {
    clearTimeout(resetTimer);
    const locked = app.activityDrawerOpen && mobile();
    anchor();
    document.documentElement.classList.toggle('nook-jobs-open', locked);
    animateShade(locked);
    if (locked || !mobile()) { reset(); panel.style.animation = ''; }
    else resetTimer = setTimeout(() => { reset(); panel.style.animation = ''; }, 240);
  };
  const start = event => {
    blockClickUntil = 0;
    if (!mobile() || !app.activityDrawerOpen) return;
    if (event.touches.length !== 1) { end({ type: 'touchcancel', timeStamp: event.timeStamp }); return; }
    clearTimeout(resetTimer);
    reset();
    const touch = event.touches[0];
    // Native editors and range sliders keep their own gestures.
    if (event.target.closest('input, textarea, select, [contenteditable="true"]')) return;
    gesture = { x: touch.clientX, y: touch.clientY, lastY: touch.clientY, at: event.timeStamp,
      velocity: 0, dy: 0, dragging: false, horizontal: false,
      handle: Boolean(event.target.closest('.cs-grab')), atTop: panel.scrollTop <= 0 };
  };
  const move = event => {
    if (!blocksBackground()) return;
    // The scrim and underlying feed must never pan, including on iOS.
    if (!panel.contains(event.target)) { if (event.cancelable) event.preventDefault(); return; }
    if (!gesture) return;
    if (event.touches.length !== 1) { end({ type: 'touchcancel', timeStamp: event.timeStamp }); return; }
    const touch = event.touches[0], dx = touch.clientX - gesture.x, dy = touch.clientY - gesture.y;
    if (!gesture.dragging) {
      if (gesture.horizontal || Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
      if (Math.abs(dx) >= Math.abs(dy)) { gesture.horizontal = true; return; }
      if (dy <= 0 || (!gesture.handle && (!gesture.atTop || panel.scrollTop > 0))) { gesture = null; return; }
      gesture.dragging = true;
      panel.style.animation = 'none';
      panel.classList.add('cs-dragging');
    }
    if (event.cancelable) event.preventDefault();
    const elapsed = event.timeStamp - gesture.at;
    if (elapsed > 0) gesture.velocity = (touch.clientY - gesture.lastY) / elapsed;
    gesture.lastY = touch.clientY;
    gesture.at = event.timeStamp;
    gesture.dy = Math.max(0, dy);
    panel.style.setProperty('--cs-sheet-y', `${gesture.dy}px`);
  };
  const end = event => {
    if (!gesture) return;
    const current = gesture;
    gesture = null;
    if (!current.dragging) return;
    blockClickUntil = Date.now() + 450;
    panel.classList.remove('cs-dragging');
    const velocity = event.timeStamp - current.at < 100 ? current.velocity : 0;
    if (event.type !== 'touchcancel' && (current.dy > Math.min(100, panel.offsetHeight * .22) || (current.dy >= 24 && velocity > .5))) {
      nookCloseActivity(app);
    } else {
      panel.classList.add('cs-settling');
      panel.style.removeProperty('--cs-sheet-y');
      resetTimer = setTimeout(reset, 240);
    }
  };
  const click = event => {
    if (Date.now() < blockClickUntil) { event.preventDefault(); event.stopImmediatePropagation(); }
  };
  panel.addEventListener('touchstart', start, { passive: true });
  document.addEventListener('touchmove', move, { passive: false });
  document.addEventListener('wheel', wheel, { passive: false });
  document.addEventListener('keydown', keydown);
  panel.addEventListener('touchend', end);
  panel.addEventListener('touchcancel', end);
  panel.addEventListener('click', click, true);
  window.addEventListener('resize', sync);
  window.addEventListener('scroll', anchor, { passive: true });
  sync();
  return { sync, destroy() {
    clearTimeout(resetTimer);
    if (shadeFrame !== null) cancelAnimationFrame(shadeFrame);
    reset();
    panel.style.animation = '';
    document.documentElement.classList.remove('nook-jobs-open');
    document.documentElement.classList.remove('nook-jobs-shading');
    document.documentElement.style.removeProperty('--nook-jobs-page-top');
    document.documentElement.style.removeProperty('--nook-jobs-bottom-bleed');
    app.updateBrowserTheme?.(false);
    panel.removeEventListener('touchstart', start);
    document.removeEventListener('wheel', wheel);
    document.removeEventListener('keydown', keydown);
    document.removeEventListener('touchmove', move);
    panel.removeEventListener('touchend', end);
    panel.removeEventListener('touchcancel', end);
    panel.removeEventListener('click', click, true);
    window.removeEventListener('resize', sync);
    window.removeEventListener('scroll', anchor);
  } };
}
function nookSyncPanel() {
  return {
    view: 'main', viewDir: '', selected: null, lastActiveStep: null, tabsResizeObserver: null,
    jobs: [], jobsRunning: null, jobsWeekRuns: 0, jobsTimer: null, focusKey: null, moreOpen: false,
    optKey: null, optValues: {}, jobError: '', jobBusy: false, archiveInfo: null, uploadPct: null,
    history: [], historyLoaded: false,
    // The top block shows the focused job: the one tapped below, else whatever
    // runs, else a failed one, else the most recent run.
    get displayActivity() {
      if (nookRunOverride !== undefined) return nookRunOverride;
      if (this.live()) return this.activity;
      const shown = this.jobBy(this.shownKey());
      if (shown) return this.jobSnapshot(shown);
      // Once the jobs are known, a job that never ran shows as not run yet.
      return this.jobs.length ? null : this.lastActivity;
    },
    jobBy(key) { return this.jobs.find(job => job.key === key) || null; },
    jobSnapshot(job) {
      const saved = job.last_snapshot || null, latest = this.lastActivity;
      // Activity can finish before the jobs refresh. Keep that result through the handoff.
      return latest?.title === job.command && (!saved || (latest.started_at || 0) >= (saved.started_at || 0)) ? latest : saved;
    },
    // A worker that's still starting reports a short title ("tweetnook media"); match it too.
    runningKey() {
      const title = String(this.activity?.title || '');
      // The activity stream can move to Setup's next job before the Jobs response does.
      return (title && this.jobs.find(job => job.command === title || job.command.startsWith(`${title} `))?.key) || this.jobsRunning?.key || null;
    },
    defaultFocus() {
      if (this.activity && this.runningKey()) return this.runningKey();
      const runs = this.jobs.map(job => ({job, run: this.jobSnapshot(job) || job.last})).filter(item => item.run)
        .sort((a, b) => (b.run.completed_at || b.run.started_at || 0) - (a.run.completed_at || a.run.started_at || 0));
      const failed = runs.find(({run}) => !run.stopped && run.status !== 'stopped' && run.success !== true);
      return (failed || runs[0])?.job.key || 'sync';
    },
    shownKey() { return this.focusKey || this.defaultFocus(); },
    // Live when the top block follows the running job rather than a past run.
    live() {
      if (!this.activity) return false;
      return !this.focusKey || this.focusKey === this.runningKey() || (!this.runningKey() && !this.jobs.length);
    },
    running() { return this.live() || (this.activityStartPending && !this.focusKey); },
    steps() { return this.activitySteps(); },
    escape(text) { return String(text ?? ''); },
    headTitle() {
      if (this.activityStartPending && !this.activity && !this.focusKey) return 'Starting…';
      const check = this.activityIsCheck();
      if (this.live()) return NOOK_JOB_VERBS[this.runningKey()] || (check ? 'Checking' : 'Syncing');
      const last = this.displayActivity;
      const job = this.jobBy(this.shownKey());
      if (job && !job.scheduled) {
        const name = {import: 'Import', enrich: 'Enrichment'}[job.key] || job.name;
        return !last ? `${name} not run yet` : `${name} ${last.stopped ? 'stopped' : last.success === false ? 'failed' : last.success === true ? 'finished' : 'interrupted'}`;
      }
      if (!last) return this.shownKey() === 'check' ? 'Not checked yet' : 'Not synced yet';
      if (last.stopped) return check ? 'Check stopped' : 'Sync stopped';
      if (last.success === false) return check ? 'Check failed' : 'Sync failed';
      if (last.success === true) return check ? 'Checked' : 'Synced';
      return check ? 'Check interrupted' : 'Sync interrupted';
    },
    headTime() {
      if (this.live() && this.activity?.started_at) return this.formatActivityDuration(this.activityNowSeconds - this.activity.started_at);
      const done = this.displayActivity?.completed_at;
      return !this.running() && done ? this.formatRelativeDate(new Date(done * 1000)) : '';
    },
    stepState(step) { return step?.paused && step.state === 'failed' ? 'paused' : this.activitySegmentState(step); },
    stepStateLabel(step) { return { active: 'in progress', complete: 'complete', failed: 'failed', paused: 'paused', cancelled: 'canceled', skipped: 'skipped', pending: 'waiting' }[this.stepState(step)] || 'waiting'; },
    indeterminate(step) {
      return this.stepState(step) === 'active' && (step.show_progress === false || !Number.isFinite(Number(step.total)) || Number(step.total) <= 0);
    },
    tabPages() { return (this.running() ? [] : [null]).concat(this.steps().map((_, index) => index)); },
    tabLabel(index) {
      if (index === null) return 'Overview';
      return this.tabLabelFor(this.steps()[index]);
    },
    tabLabelFor(step) {
      const labels = { 'archive-auth': 'Connect', 'archive-enrich': 'Enrichment', threads: 'Conversations', resurrection: 'Retry', articles: 'Articles', media: 'Media', urls: 'Links', 'check:saves': 'Saved', 'check:unavailable': 'Unavailable', 'check:context': 'Context' };
      if (step.key?.startsWith('preflight:')) return 'Access';
      return labels[step.key] || step.title;
    },
    focusIndex() {
      if (this.selected !== null && this.steps()[this.selected]) return this.selected;
      if (!this.running()) return null;
      const active = this.steps().findIndex(step => step.state === 'active');
      // Stage completion and the next stage's start arrive as separate updates.
      // Keep the finished stage visible through that gap while the run continues.
      const previous = this.steps().findIndex(step => step.key === this.lastActiveStep);
      return active >= 0 ? active : previous >= 0 ? previous : 0;
    },
    updateTabFades() {
      const nav = this.$refs.stepTabs, frame = this.$refs.stepTabsFrame;
      if (!nav || !frame) return;
      frame.classList.toggle('is-clipped-start', nav.scrollLeft > 2);
      frame.classList.toggle('is-clipped-end', nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 2);
    },
    revealStepTab() {
      const nav = this.$refs.stepTabs, tab = nav?.querySelector('[aria-selected="true"]');
      if (!nav?.clientWidth || !tab) return;
      // Scroll only the strip, keeping the popup and the archive underneath in place.
      const left = Math.max(0, Math.min(nav.scrollWidth - nav.clientWidth, tab.offsetLeft + tab.offsetWidth / 2 - nav.clientWidth / 2));
      nav.scrollTo({ left, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
      this.updateTabFades();
    },
    pick(index) { this.selected = index; this.$nextTick(() => this.revealStepTab()); },
    segKey(event, index) {
      const pages = this.tabPages(), current = pages.indexOf(index), count = pages.length;
      if (event.key === 'Escape' && this.selected !== null) {
        event.stopPropagation();
        this.pick(null);
        this.$nextTick(() => this.$refs.stepTabs.querySelector('[aria-selected="true"]')?.focus({ preventScroll: true }));
        return;
      }
      const next = { ArrowRight: current + 1, ArrowLeft: current - 1, Home: 0, End: count - 1 }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      this.pick(pages[(next + count) % count]);
      this.$nextTick(() => this.$refs.stepTabs.querySelector('[aria-selected="true"]')?.focus({ preventScroll: true }));
    },
    selectedStep() {
      const index = this.focusIndex();
      return index === null ? null : this.steps()[index] || null;
    },
    eta() {
      const step = this.selectedStep(), seconds = Number(step?.eta_seconds);
      if (!step || this.stepState(step) !== 'active' || step.eta_seconds == null || !Number.isFinite(seconds) || seconds <= 0) return '';
      if (seconds < 60) return '<1m left';
      const minutes = Math.round(seconds / 60);
      if (minutes < 60) return `~${minutes}m left`;
      const hours = Math.floor(minutes / 60), rest = minutes % 60;
      return rest ? `~${hours}h ${rest}m left` : `~${hours}h left`;
    },
    // The explanation keeps its own row; live counters and outcomes belong in the breakdown.
    describe(step) {
      const state = this.stepState(step), copy = nookSyncStepCopy(step, state), summary = String(step.summary || ''), counters = String(step.counters || ''), activity = String(step.activity || '');
      const count = value => Number(value || 0).toLocaleString();
      const pageCounts = counters.match(/^([\d,]+) pages? · ([\d,]+) tweets? saved$/), pageActivity = activity.match(/page\s+([\d,]+)/i);
      let detail = state === 'complete' ? summary || counters : counters;
      if (state === 'complete') detail = detail.split(' · ').filter(part => !/^0\s/.test(part)).join(' · ');
      if (pageCounts) detail = `${pageCounts[2]} posts saved · ${pageCounts[1]} pages`;
      const model = { left: copy.title, sub: copy.explanation, right: '', detail, status: '', detailLabel: state === 'complete' ? 'Result' : 'Progress' };
      if (state === 'active') return { ...model, right: pageActivity ? `Page ${count(pageActivity[1].replace(/,/g, ''))}` : pageCounts ? `${pageCounts[2]} posts` : this.indeterminate(step) ? 'Working' : `${count(step.completed)} / ${count(step.total)}`, status: activity.replace(/^Fetching live details for tweet\b.*$/i, 'Fetching live post details').replace(/^Fetching thread context$/i, 'Fetching conversation context').replace(/^Downloading animated gif$/i, 'Downloading GIF') || 'Working…' };
      if (state === 'pending') return { ...model, right: 'Waiting', detail: '', status: 'Waiting for earlier steps to finish.', detailLabel: 'Status' };
      if (state === 'skipped') return { ...model, right: 'Skipped', detail: '', status: String(step.reason || summary || 'Not needed for this run.').replace(/^skipped due to /i, '').replace(/^./, c => c.toUpperCase()), detailLabel: 'Skipped' };
      if (state === 'paused' || state === 'failed' || state === 'cancelled') return { ...model, right: { paused: 'Paused', failed: 'Failed', cancelled: 'Canceled' }[state], danger: state === 'failed', status: String(step.reason || summary || (state === 'paused' ? 'This step will resume on a later run.' : 'Stopped before this step finished.')), detailLabel: 'Status' };
      if (step.key === 'archive-auth') return { ...model, right: 'Ready', detail: '', status: 'Signed in and ready to sync.' };
      if (step.key?.startsWith('preflight:')) return { ...model, right: 'Ready', status: 'Your selected collections are ready to sync.' };
      if (pageCounts) return { ...model, right: step.metrics?.new_tweets == null ? 'Complete' : `${count(step.metrics.new_tweets)} new`, status: /returned no tweets/i.test(summary) ? 'Reached the end of this collection.' : 'Finished.' };
      return { ...model, right: 'Complete', status: /\d/.test(summary) ? 'Finished.' : summary || 'Finished.' };
    },
    caption() {
      const step = this.selectedStep();
      if (step) return this.describe(step);
      return this.overview();
    },
    // The run summary shown on the Overview tab, also used as a job row's last result.
    overview() {
      if (this.activityStartPending && !this.focusKey && nookRunOverride === undefined) return { left: 'Connecting to X…', right: '', sub: 'Preparing to sync your selected collections.', detailLabel: 'Status', status: 'Waiting for the sync to start.', detail: '' };
      if (!this.displayActivity) {
        const job = this.jobBy(this.shownKey());
        if (job && !job.scheduled) return { left: `${job.name} hasn’t run yet`, right: '', sub: job.description || '', detailLabel: 'Status', status: 'Choose run options to get started.', detail: '' };
        return { left: 'Your archive hasn’t been synced yet', right: '', sub: 'A sync will check your collections and update the archive.', detailLabel: 'Status', status: 'Choose Sync now to get started.', detail: '' };
      }
      const metrics = this.activitySummaryMetrics();
      const metric = label => Number(String(metrics.find(item => item.label === label)?.value || 0).replace(/,/g, '')) || 0;
      const bookmarks = metric('New bookmarks'), likes = metric('New liked tweets'), tweets = metric('New tweets');
      const parts = [bookmarks && `${bookmarks.toLocaleString()} bookmark${bookmarks === 1 ? '' : 's'}`, likes && `${likes.toLocaleString()} like${likes === 1 ? '' : 's'}`, tweets && `${tweets.toLocaleString()} tweet${tweets === 1 ? '' : 's'}`].filter(Boolean);
      const issues = this.activityIssueCount();
      const tally = {};
      this.steps().forEach(item => { const state = this.stepState(item); tally[state] = (tally[state] || 0) + 1; });
      const detail = [['complete', 'complete'], ['skipped', 'skipped'], ['failed', 'failed'], ['cancelled', 'canceled'], ['paused', 'paused'], ['pending', 'not run']].filter(([key]) => tally[key]).map(([key, label]) => `${tally[key]} ${label}`).join(' · ');
      const stopped = this.displayActivity.stopped || this.displayActivity.success !== true;
      const job = this.jobs.find(item => item.command === this.displayActivity.title);
      if (job && !job.scheduled) {
        return { left: this.displayActivity.summary || `${job.name} ${stopped ? 'stopped before all steps finished.' : 'finished.'}`, right: issues ? `${issues} issue${issues === 1 ? '' : 's'}` : 'No issues', danger: Boolean(issues), sub: `${job.name} ${stopped ? 'stopped before all steps finished.' : 'finished.'}`, detailLabel: 'Run result', detail, status: stopped ? this.displayActivity.summary || 'Stopped before all steps finished.' : 'All steps finished.' };
      }
      if (this.activityIsCheck()) {
        const total = (keys, label) => this.steps().filter(item => keys.includes(item.key))
          .reduce((sum, item) => sum + (nookSyncStats(item.summary || item.counters).find(stat => stat.label === label)?.num || 0), 0);
        const missing = total(['check:saves', 'check:context'], 'unavailable');
        const restored = total(['check:saves', 'check:unavailable', 'check:context'], 'restored');
        const changes = [missing && `${missing.toLocaleString()} missing`, restored && `${restored.toLocaleString()} restored`].filter(Boolean);
        parts.splice(0, parts.length, ...changes);
      }
      return { left: parts.length ? `${parts.join(' and ')}${this.activityIsCheck() ? '' : ' added'}` : this.activityIsCheck() ? 'Everything still on X' : 'Nothing new', right: issues ? `${issues} issue${issues === 1 ? '' : 's'}` : 'No issues', danger: Boolean(issues), sub: `${this.activityIsCheck() ? 'Check' : 'Sync'} ${stopped ? 'stopped before all steps finished.' : 'finished.'}`, detailLabel: 'Run result', detail, status: stopped ? this.displayActivity.summary || 'Stopped before all steps finished.' : 'All steps finished.' };
    },
    detail() {
      const step = this.selectedStep(), caption = this.caption(), last = this.displayActivity;
      const state = step ? this.stepState(step) : last?.stopped ? 'cancelled' : last?.success === false ? 'failed' : last?.success === true ? 'complete' : this.running() ? 'active' : 'pending';
      const total = step ? step.show_progress === false ? 0 : Math.max(0, Number(step.total) || 0) : caption.detailLabel === 'Run result' ? this.steps().length : 0;
      const stats = nookSyncStats(caption.detail);
      if (!stats.length && step && total > 1 && !['pending', 'skipped'].includes(state)) stats.push({ value: Number(step.completed || 0).toLocaleString(), label: 'processed', num: Number(step.completed || 0) });
      return nookSyncBreakdown({ state, total, stats, status: caption.status || '', heading: caption.detailLabel || 'Details' });
    },
    detailGlyph() {
      const paths = {
        complete: 'M16.28 9.62l-5.41 6.84-3.54-3.22 1.34-1.48 1.96 1.78 4.09-5.16z',
        pending: 'M13 11.32l4.37 1.75-.74 1.86L11 12.68V6.5h2z',
        skipped: 'M16 13H8v-2h8z', paused: 'M9 8h2v8H9zm4 0h2v8h-2z',
        cancelled: 'M15 15H9V9h6z', failed: 'M 12 14.2 a 1.2 1.2 0 1 1 0 2.4 1.2 1.2 0 0 1 0 -2.4 m 0 -6.45 c 0.8 0 1.4 0.7 1.29 1.48 L 12.75 13 h -1.5 l -0.54 -3.77 A 1.3 1.3 0 0 1 12 7.75',
      };
      return 'M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20m0 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16' + (paths[this.detail().state] || '');
    },
    scheduleLabel() {
      const form = this.scheduleSettings || null;
      if (form?.enabled === false || this.activitySchedule?.configured === false) return 'Scheduled syncs off';
      return this.activitySchedule?.label || this.activitySchedule?.description || 'Scheduled sync';
    },
    scheduleNext() {
      const day = this.activityScheduleDayText(), time = this.activityScheduleTimeText();
      return [day, time].filter(Boolean).join(' ');
    },
    openSettingsView() { this.go('settings'); this.$nextTick(() => this.$refs.settingsBack?.focus()); },
    go(view) { this.viewDir = 'is-push'; this.view = view; this.jobError = ''; },
    back() {
      const from = this.view;
      this.viewDir = 'is-pop'; this.view = 'main'; this.jobError = '';
      this.$nextTick(() => this.$root.querySelector(from === 'history' ? '[data-view="history"]' : from === 'options' ? `[data-job="${this.optKey}"] .cs-job-body` : '[data-view="settings"]')?.focus());
    },
    openRunLog(runId) {
      this.activityDrawerOpen = false; this.openSettings();
      this.$nextTick(() => { nookSettingsNavigate(this, 'logs'); this.nookLogOpenRunId = runId; });
    },
    // --- jobs ---
    async loadJobs() {
      try {
        const response = await fetch('/api/jobs');
        if (!response.ok) return;
        const data = await response.json();
        this.jobs = data.jobs || [];
        this.jobsRunning = data.running || null;
        this.jobsWeekRuns = data.runs_this_week || 0;
        if (this.uploadPct === null) this.archiveInfo = data.archive || null;
      } catch (_) {
        // Keep the last list; the top block still follows the activity stream.
      }
    },
    scheduledJobs() { return this.jobs.filter(job => job.scheduled); },
    onDemandJobs() { return this.jobs.filter(job => !job.scheduled); },
    // Only job-level failures need attention while On demand is collapsed.
    onDemandShown() { return this.moreOpen ? this.onDemandJobs() : this.onDemandJobs().filter(job => job.state === 'failed'); },
    moreLabel() { return this.moreOpen ? 'Show less' : `${this.onDemandJobs().length - this.onDemandShown().length} more`; },
    isFocused(job) { return job.key === this.shownKey(); },
    focus(key) { this.focusKey = key === this.defaultFocus() && !this.activity ? null : key; this.selected = null; this.$nextTick(() => this.revealStepTab()); },
    jobGlyph(job) { return NOOK_JOB_ICONS[job.state === 'queued' ? 'queued' : job.state === 'failed' ? 'failed' : job.key] || NOOK_JOB_ICONS.sync; },
    headline(job) {
      if (!job.last_snapshot) return '';
      nookRunOverride = job.last_snapshot;
      try { return this.overview().left; } finally { nookRunOverride = undefined; }
    },
    liveProgress() {
      const step = (this.activity?.steps || []).find(item => item.state === 'active');
      if (!step) return 'Starting';
      const index = this.activity.steps.indexOf(step), label = this.tabLabelFor(step, index);
      return step.show_progress === false || !(Number(step.total) > 0) ? label : `${label} ${Number(step.completed || 0).toLocaleString()} / ${Number(step.total).toLocaleString()}`;
    },
    startedBy() {
      const origin = this.jobsRunning?.origin, at = this.jobsRunning?.started_at;
      const who = origin === 'schedule' ? 'Scheduled' : origin === 'cli' ? 'Started from the command line' : 'Started by you';
      return at ? `${who} · ${nookClock(at)}` : who;
    },
    jobSub(job) {
      if (!job.available) return job.unavailable_reason || 'Unavailable';
      if (job.state === 'running') return this.isFocused(job) ? this.startedBy() : `Running · ${this.liveProgress()}`;
      if (job.state === 'in_sync') return 'Running now as part of Sync';
      if (job.state === 'queued') {
        const running = this.jobBy(this.runningKey());
        return running ? `Waiting for ${running.name} to finish` : 'Waiting for another command to finish';
      }
      if (!job.last) return 'Never run';
      const when = nookJobWhen(job.last.completed_at || job.last.started_at);
      if (job.last.status === 'stopped') return `${when} · Stopped`;
      const result = job.scheduled ? this.headline(job) : '';
      if (job.state === 'failed') return `${when} · ${String(job.last.summary || 'Failed').replace(/\.$/, '')}`;
      return result ? `${when} · ${result}` : `${when} · Finished`;
    },
    jobNext(job) {
      if (job.state === 'running') return 'Now';
      if (job.state === 'queued') return job.queue?.retry_at ? nookClock(job.queue.retry_at) : 'Next';
      if (job.next?.at) return nookJobNextTime(job.next.at);
      return job.next?.label || '';
    },
    jobActionLabel(job) {
      if (job.state === 'running') return `Stop ${job.name}`;
      if (job.state === 'queued') return job.queue?.removable === false ? `${job.name} is waiting` : `Remove ${job.name} from the queue`;
      if (job.key === 'import') return `Choose an archive to import`;
      return this.activity ? `Queue ${job.name} after the current job` : `Run ${job.name} now`;
    },
    jobActionGlyph(job) { return NOOK_JOB_ICONS[job.state === 'running' ? 'stop' : job.state === 'queued' ? 'remove' : 'play']; },
    jobActionDisabled(job) {
      if (job.state === 'running') return false;
      if (job.state === 'queued') return job.queue?.removable === false;
      return this.jobBusy || !job.available || job.state === 'in_sync' || this.archiveReady === false || this.setupForced;
    },
    jobAction(job) {
      if (job.state === 'running') return this.stopActivity();
      if (job.state === 'queued') return this.unqueue(job.key);
      if (job.key === 'import') return this.openOptions(job.key);
      return this.runJob(job.key);
    },
    async runJob(key, options = {}) {
      this.jobBusy = true; this.jobError = '';
      try {
        const response = await fetch(`/api/jobs/${key}/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ options }) });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Could not start the job');
        if (data.started) { this.focusKey = this.jobBy(key)?.scheduled ? key : null; this.activityStartPending = true; }
        await new Promise(resolve => setTimeout(resolve, 250));
        await Promise.all([this.fetchActivityStatus(true), this.loadJobs()]);
        return true;
      } catch (error) {
        this.jobError = error.message;
        return false;
      } finally {
        this.jobBusy = false;
      }
    },
    async unqueue(key) {
      this.jobError = '';
      const response = await fetch(`/api/jobs/${key}/queue`, { method: 'DELETE' }).catch(() => null);
      if (!response?.ok) this.jobError = 'Could not remove it from the queue';
      await this.loadJobs();
    },
    // --- run options: one control per CLI flag ---
    optJob() { return this.jobBy(this.optKey); },
    openOptions(key) {
      this.optKey = key;
      const job = this.jobBy(key);
      this.optValues = { ...(job?.defaults || {}), ...(this.optValues.__key === key ? this.optValues : {}), __key: key };
      this.go('options');
      if (key === 'import') this.loadJobs();
      this.$nextTick(() => this.$refs.optionsBack?.focus());
    },
    optValue(option) { return this.optValues[option.key]; },
    optDisabled(option) { return (option.disabled_when || []).some(([other, value]) => this.optValues[other] === value); },
    optToggle(option) { this.optValues[option.key] = !this.optValues[option.key]; },
    optSet(option, raw) {
      const text = String(raw ?? '').trim();
      this.optValues[option.key] = text === '' ? null : option.kind === 'int' ? Number(text) : text;
    },
    optReset() { this.optValues = { ...(this.optJob()?.defaults || {}), __key: this.optKey }; this.jobError = ''; },
    // Only options that differ from their default are sent, as the CLI would be called.
    optChanges() {
      const job = this.optJob(), changes = {};
      for (const option of job?.options || []) {
        if (option.kind === 'archive' || this.optDisabled(option)) continue;
        const value = this.optValues[option.key], fallback = job.defaults?.[option.key];
        if (value === fallback || value == null || (typeof value === 'number' && !Number.isFinite(value))) continue;
        changes[option.key] = value;
      }
      return changes;
    },
    optCommand() {
      const job = this.optJob();
      if (!job) return '';
      const changes = this.optChanges(), parts = [job.command], positional = [];
      for (const option of job.options) {
        if (option.kind === 'archive') { positional.push(this.archiveInfo?.filename || 'archive.zip'); continue; }
        if (!(option.key in changes)) continue;
        const value = changes[option.key];
        if (option.kind === 'flag') parts.push(option.flag);
        else if (option.kind === 'negflag') parts.push(option.flag);
        else if (option.kind === 'level') parts.push(`${option.flag} ${value ? 1 : 0}`);
        else if (!option.flag) positional.push(/\s/.test(value) ? `"${value}"` : value);
        else parts.push(`${option.flag} ${/\s/.test(value) ? `"${value}"` : value}`);
      }
      return [...parts, ...positional].join(' ');
    },
    optNote() {
      const job = this.optJob();
      if (!job) return '';
      if (job.state === 'in_sync') return 'Running now as part of Sync';
      const running = this.jobBy(this.runningKey());
      return this.activity ? `Starts after ${running?.name || 'the current job'}` : '';
    },
    optRunLabel() { const job = this.optJob(); return job?.state === 'in_sync' ? 'Running in Sync' : job?.state === 'queued' ? 'Queued' : this.activity ? 'Queue run' : 'Run'; },
    optRunDisabled() {
      const job = this.optJob();
      if (!job || this.jobBusy || this.uploadPct !== null || ['in_sync', 'queued', 'running'].includes(job.state)) return true;
      if (this.archiveReady === false && job.key !== 'import') return true;
      return job.key === 'import' && !this.archiveInfo?.uploaded;
    },
    async runOptions() {
      if (await this.runJob(this.optKey, this.optChanges())) this.back();
    },
    archiveLabel() {
      if (this.uploadPct !== null) return `Uploading… ${this.uploadPct}%`;
      const info = this.archiveInfo;
      if (!info?.uploaded) return 'No archive chosen';
      return info.size ? `${info.filename} · ${this.formatActivityBytes(info.size)}` : info.filename;
    },
    uploadArchive(file) {
      if (!file) return;
      this.jobError = ''; this.uploadPct = 0;
      const request = new XMLHttpRequest();
      request.open('PUT', '/api/setup/archive');
      request.upload.onprogress = event => { if (event.lengthComputable) this.uploadPct = Math.floor(event.loaded / event.total * 100); };
      request.onload = () => {
        this.uploadPct = null;
        let data = {};
        try { data = JSON.parse(request.responseText || '{}'); } catch (_) { /* keep empty */ }
        if (request.status >= 200 && request.status < 300) this.archiveInfo = data;
        else this.jobError = data.detail || 'Could not upload the archive';
      };
      request.onerror = () => { this.uploadPct = null; this.jobError = 'Could not upload the archive'; };
      request.send(file);
    },
    // --- run history ---
    async openHistory() {
      this.go('history');
      this.$nextTick(() => this.$refs.historyBack?.focus());
      try {
        const response = await fetch('/api/activity/runs?limit=30');
        if (response.ok) this.history = (await response.json()).runs || [];
      } catch (_) {
        // Leave the previous list.
      }
      this.historyLoaded = true;
    },
    runJobFor(run) { return this.jobs.find(job => job.command === run.title) || null; },
    runName(run) { return this.runJobFor(run)?.name || String(run.title || 'Command').replace(/^tweetnook\s+/, ''); },
    runGlyph(run) { return run.success === false && run.status !== 'stopped' ? NOOK_JOB_ICONS.failed : NOOK_JOB_ICONS[this.runJobFor(run)?.key] || NOOK_JOB_ICONS.history; },
    runResult(run) {
      if (['running', 'starting'].includes(run.status)) return 'Running';
      if (run.status === 'stopped') return 'Stopped';
      const summary = String(run.summary || '').trim();
      return summary || (run.success ? 'Finished' : run.success === false ? 'Failed' : 'Interrupted');
    },
    runTone(run) { return ['running', 'starting'].includes(run.status) ? '' : run.success === false && run.status !== 'stopped' ? 'is-danger' : ''; },
    runDuration(run) { return run.completed_at && run.started_at ? nookDuration(run.completed_at - run.started_at) : ''; },
    // Mouse dragging on the grabber; touch dragging covers the whole sheet.
    grab(event) {
      if (event.pointerType !== 'mouse' || window.innerWidth > 640) return;
      const panel = this.$root, start = event.clientY;
      let dy = 0;
      event.currentTarget.setPointerCapture(event.pointerId);
      panel.style.transition = 'none';
      const move = moveEvent => { dy = Math.max(0, moveEvent.clientY - start); panel.style.transform = `translateY(${dy}px)`; };
      const up = upEvent => {
        upEvent.currentTarget.removeEventListener('pointermove', move);
        upEvent.currentTarget.removeEventListener('pointerup', up);
        upEvent.currentTarget.removeEventListener('pointercancel', up);
        panel.style.transition = 'transform .22s ease-out';
        if (upEvent.type !== 'pointercancel' && dy > Math.min(100, panel.offsetHeight / 4)) {
          panel.style.transform = 'translateY(100%)';
          setTimeout(() => { this.activityDrawerOpen = false; panel.style.transform = ''; panel.style.transition = ''; }, 220);
        } else {
          panel.style.transform = '';
          setTimeout(() => { panel.style.transition = ''; }, 220);
        }
      };
      event.currentTarget.addEventListener('pointermove', move);
      event.currentTarget.addEventListener('pointerup', up, { once: true });
      event.currentTarget.addEventListener('pointercancel', up, { once: true });
    },
    // Desktop: arrows page through the step tabs when they overflow.
    scrollTabs(direction) { const nav = this.$refs.stepTabs; nav?.scrollBy({ left: direction * nav.clientWidth * 0.7, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); },
    openScheduleSettings() { this.activityDrawerOpen = false; this.openSettings(); this.$nextTick(() => nookSettingsNavigate(this, 'schedule')); },
    init() {
      this.mobileSheet = nookJobsSheet(this.$root, this);
      this.lastActiveStep = this.activity?.steps?.find(step => step.state === 'active')?.key || null;
      // Follow each newly active step, while allowing inspection of other steps between transitions.
      this.$watch('activityDrawerOpen', open => {
        this.mobileSheet.sync();
        clearInterval(this.jobsTimer);
        if (!open) return;
        this.view = 'main'; this.viewDir = ''; this.selected = null; this.focusKey = null; this.moreOpen = false; this.jobError = '';
        this.loadJobs().then(() => this.$nextTick(() => this.revealStepTab()));
        // Times and queue positions drift while the panel stays open.
        this.jobsTimer = setInterval(() => this.loadJobs(), 30000);
        this.$nextTick(() => this.revealStepTab());
      });
      // A job starting or finishing changes every row.
      this.$watch('activity?.run_id', () => { if (this.activityDrawerOpen) this.loadJobs(); });
      this.$watch('activity', (now, before) => {
        const active = now?.steps?.find(step => step.state === 'active')?.key || null;
        const newRun = now?.run_id !== before?.run_id;
        if (newRun || Boolean(now) !== Boolean(before) || (active && active !== this.lastActiveStep)) {
          this.selected = null;
          this.$nextTick(() => this.revealStepTab());
        }
        if (newRun || !now || active) this.lastActiveStep = active;
        this.$nextTick(() => this.updateTabFades());
      });
      this.$watch('view', view => { if (view === 'main') this.$nextTick(() => this.revealStepTab()); });
      this.$nextTick(() => {
        this.tabsResizeObserver = new ResizeObserver(() => this.revealStepTab());
        this.tabsResizeObserver.observe(this.$refs.stepTabs);
        this.revealStepTab();
      });
    },
    destroy() { this.mobileSheet?.destroy(); this.tabsResizeObserver?.disconnect(); clearInterval(this.jobsTimer); },
  };
}
