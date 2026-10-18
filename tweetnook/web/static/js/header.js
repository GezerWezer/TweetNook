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
  document.querySelector('.nook-activity-button')?.focus();
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
    'archive-enrich': ['Refresh saved post details', 'refresh details for posts in your archive', 'Refreshing details for posts in your archive.', 'Refreshed details for posts in your archive.'],
    threads: ['Expand conversations', 'find replies and quotes for saved posts', 'Finding replies and quotes for saved posts.', 'Found replies and quotes for saved posts.'],
    resurrection: ['Retry unavailable posts', 'retry posts that were unavailable', 'Retrying posts that were unavailable.', 'Retried unavailable posts.'],
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
function nookSyncPanel() {
  return {
    view: 'main', selected: null, lastActiveStep: null, tabsResizeObserver: null,
    running() { return Boolean(this.activity || this.activityStartPending); },
    steps() { return this.activitySteps(); },
    escape(text) { return String(text ?? ''); },
    headTitle() {
      if (this.activityStartPending && !this.activity) return 'Starting sync…';
      if (this.activity) return 'Syncing';
      const last = this.displayActivity;
      if (!last) return 'Not synced yet';
      if (last.stopped) return 'Sync stopped';
      if (last.success === false) return 'Sync failed';
      if (last.success === true) return 'Synced';
      return 'Sync interrupted';
    },
    headTime() {
      if (this.activity?.started_at) return this.formatActivityDuration(this.activityNowSeconds - this.activity.started_at);
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
      const step = this.steps()[index];
      const labels = { 'archive-auth': 'Connect', 'archive-enrich': 'Refresh', threads: 'Conversations', resurrection: 'Retry', articles: 'Articles', media: 'Media', urls: 'Links' };
      if (step.key?.startsWith('preflight:')) return 'Access';
      return labels[step.key] || step.title;
    },
    focusIndex() {
      if (this.selected !== null && this.steps()[this.selected]) return this.selected;
      if (!this.running()) return null;
      const active = this.steps().findIndex(step => step.state === 'active');
      return active >= 0 ? active : 0;
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
      const steps = this.steps();
      return this.selected !== null && steps[this.selected] ? steps[this.selected] : steps.find(step => step.state === 'active') || null;
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
      if (this.activityStartPending) return { left: 'Connecting to X…', right: '', sub: 'Preparing to sync your selected collections.', detailLabel: 'Status', status: 'Waiting for the sync to start.', detail: '' };
      if (!this.displayActivity) return { left: 'Your archive hasn’t been synced yet', right: '', sub: 'A sync will check your collections and update the archive.', detailLabel: 'Status', status: 'Choose Sync now to get started.', detail: '' };
      const metric = label => Number(String(this.activitySummaryMetrics().find(item => item.label === label)?.value || 0).replace(/,/g, '')) || 0;
      const bookmarks = metric('New bookmarks'), likes = metric('New liked tweets'), tweets = metric('New tweets');
      const parts = [bookmarks && `${bookmarks.toLocaleString()} bookmark${bookmarks === 1 ? '' : 's'}`, likes && `${likes.toLocaleString()} like${likes === 1 ? '' : 's'}`, tweets && `${tweets.toLocaleString()} tweet${tweets === 1 ? '' : 's'}`].filter(Boolean);
      const issues = this.activityIssueCount();
      const tally = {};
      this.steps().forEach(item => { const state = this.stepState(item); tally[state] = (tally[state] || 0) + 1; });
      const detail = [['complete', 'complete'], ['skipped', 'skipped'], ['failed', 'failed'], ['cancelled', 'canceled'], ['paused', 'paused'], ['pending', 'not run']].filter(([key]) => tally[key]).map(([key, label]) => `${tally[key]} ${label}`).join(' · ');
      const stopped = this.displayActivity.stopped || this.displayActivity.success !== true;
      return { left: parts.length ? `${parts.join(' and ')} added` : 'Nothing new', right: issues ? `${issues} issue${issues === 1 ? '' : 's'}` : 'No issues', danger: Boolean(issues), sub: stopped ? 'Sync stopped before all steps finished.' : 'Sync finished.', detailLabel: 'Run result', detail, status: stopped ? this.displayActivity.summary || 'Stopped before all steps finished.' : 'All steps finished.' };
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
        complete: 'M8.4 12.2l2.4 2.4 4.8-4.8 1.4 1.4-6.2 6.2-3.8-3.8z',
        pending: 'M11 7h2v5.4l3.6 2.1-1 1.7L11 13.6z',
        skipped: 'M8 11h8v2H8z', paused: 'M9 8h2v8H9zm4 0h2v8h-2z',
        cancelled: 'M9 9h6v6H9z', failed: 'M11 7h2v6h-2zm0 8h2v2h-2z',
      };
      return 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 2a8 8 0 1 1 0 16 8 8 0 0 1 0-16z' + (paths[this.detail().state] || '');
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
    openSettingsView() { this.view = 'settings'; this.$nextTick(() => this.$refs.settingsBack?.focus()); },
    back() { this.view = 'main'; this.$nextTick(() => this.$root.querySelector('.cs-menu li:last-child .cs-row')?.focus()); },
    openLogs() {
      const runId = this.displayActivity?.run_id || null;
      this.activityDrawerOpen = false; this.openSettings();
      this.$nextTick(() => { nookSettingsNavigate(this, 'logs'); this.nookLogOpenRunId = runId; });
    },
    openScheduleSettings() { this.activityDrawerOpen = false; this.openSettings(); this.$nextTick(() => nookSettingsNavigate(this, 'schedule')); },
    init() {
      this.lastActiveStep = this.activity?.steps?.find(step => step.state === 'active')?.key || null;
      // Follow each newly active step, while allowing inspection of other steps between transitions.
      this.$watch('activityDrawerOpen', open => {
        if (open) { this.view = 'main'; this.selected = null; this.$nextTick(() => this.revealStepTab()); }
      });
      this.$watch('activity', (now, before) => {
        const active = now?.steps?.find(step => step.state === 'active')?.key || null;
        if (Boolean(now) !== Boolean(before) || (active && active !== this.lastActiveStep)) {
          this.selected = null;
          this.$nextTick(() => this.revealStepTab());
        }
        this.lastActiveStep = active;
        this.$nextTick(() => this.updateTabFades());
      });
      this.$watch('view', view => { if (view === 'main') this.$nextTick(() => this.revealStepTab()); });
      this.$nextTick(() => {
        this.tabsResizeObserver = new ResizeObserver(() => this.revealStepTab());
        this.tabsResizeObserver.observe(this.$refs.stepTabs);
        this.revealStepTab();
      });
    },
    destroy() { this.tabsResizeObserver?.disconnect(); },
  };
}
