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
    if (y <= header.offsetHeight || header.contains(document.activeElement)) { header.classList.remove('tabs-hidden'); travel = 0; return; }
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
    // Caption: the step on the left, its number on the right; a second line only for problems or skips.
    describe(step) {
      const state = this.stepState(step), summary = String(step.summary || '');
      const first = summary.split(' · ')[0];
      if (state === 'active') return { left: step.activity || step.title, right: this.indeterminate(step) ? '' : `${Number(step.completed || 0).toLocaleString()} / ${Number(step.total).toLocaleString()}` };
      const reason = String(step.reason || '').replace(/^./, c => c.toUpperCase());
      if (state === 'skipped') return { left: step.title, right: 'Skipped', sub: reason || summary.replace(/^skipped due to /i, '').replace(/^./, c => c.toUpperCase()) };
      if (state === 'pending') return { left: step.title, right: 'Waiting' };
      // Paused (e.g. the daily tagging budget is used up) isn't a failure; the step picks up on a later run.
      if (state === 'paused') return { left: step.title, right: 'Paused', sub: reason || summary };
      if (state === 'failed' || state === 'cancelled') return { left: step.title, right: state === 'cancelled' ? 'Canceled' : 'Failed', danger: true, sub: reason || summary };
      return { left: step.title, right: first };
    },
    caption() {
      const steps = this.steps();
      if (this.selected !== null && steps[this.selected]) return this.describe(steps[this.selected]);
      const active = steps.find(step => step.state === 'active');
      if (active) return this.describe(active);
      if (this.activityStartPending) return { left: 'Connecting to X…', right: '' };
      if (!this.displayActivity) return { left: 'Your archive hasn’t been synced yet', right: '' };
      const metric = label => Number(this.activitySummaryMetrics().find(item => item.label === label)?.value || 0);
      const bookmarks = metric('New bookmarks'), likes = metric('New liked tweets'), tweets = metric('New tweets');
      const parts = [bookmarks && `${bookmarks.toLocaleString()} bookmark${bookmarks === 1 ? '' : 's'}`, likes && `${likes.toLocaleString()} like${likes === 1 ? '' : 's'}`, tweets && `${tweets.toLocaleString()} tweet${tweets === 1 ? '' : 's'}`].filter(Boolean);
      const issues = this.activityIssueCount();
      return { left: parts.length ? `${parts.join(' and ')} added` : 'Nothing new', right: issues ? `${issues} issue${issues === 1 ? '' : 's'}` : 'No issues', danger: Boolean(issues) };
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
