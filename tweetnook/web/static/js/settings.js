// Settings: categories, detail pages, and the pages for Appearance, Schedule, Tags, Configuration, and Activity logs.
let nookSettingsTrigger = null;
const nookInitialSettings = new URLSearchParams(location.search).get('settings');
function nookSettingsRememberFocus() { nookSettingsTrigger = document.activeElement; }
function nookSettingsRestoreFocus() {
  const target = nookSettingsTrigger?.isConnected && nookSettingsTrigger !== document.body
    ? nookSettingsTrigger : document.querySelector('.nook-utilities button[title="Settings"]');
  target?.focus();
}
function nookSettingsTitle(key) {
  return {appearance:'Appearance',schedule:'Schedule',tags:'Tags',
    'automated-tagging':'Automated tagging',notifications:'Notifications',
    setup:'Setup',config:'Configuration',logs:'Activity logs'}[key] || 'Settings';
}
function nookSettingsFocus(app) {
  if (app.nookSettingsDetail || window.matchMedia('(min-width:800px)').matches)
    app.$refs.nookSettingsHeading?.focus();
  else document.getElementById('nook-nav-' + app.settingsTab)?.focus();
}
function nookSettingsNavigate(app, key) {
  app.nookLogRun = null;
  app.nookDetailScrolled = false;
  app.settingsTab = key;
  app.nookSettingsDetail = true;
  if (key === 'setup' && !app.isDemo) app.fetchSetup();
  if (key === 'schedule') app.fetchScheduleSettings();
  if (key === 'automated-tagging') app.fetchAutomatedTagging();
  if (key === 'notifications') app.fetchNotices();
  if (key === 'config') app.fetchConfig();
  if (key === 'logs') app.fetchActivityRuns();
  app.$nextTick(() => {
    document.querySelectorAll('.nook-setting-body').forEach(body => { body.scrollTop = 0; });
    if (window.matchMedia('(max-width:799px)').matches) app.$refs.nookSettingsHeading?.focus();
  });
}
function nookSettingsBack(app) {
  if (app.settingsTab === 'logs' && app.nookLogRun) {
    const runId = app.nookLogRun.run_id;
    app.nookLogRun = null;
    app.$nextTick(() => document.querySelector(`.nook-log-row[data-run="${CSS.escape(runId)}"]`)?.focus());
    return;
  }
  app.nookSettingsDetail = false;
  app.$nextTick(() => document.getElementById('nook-nav-' + app.settingsTab)?.focus());
}
function nookSettingsInit(app) {
  const requested = nookInitialSettings;
  if (!requested) return;
  app.openSettings();
  if (['appearance','schedule','tags','automated-tagging','notifications','config','logs','setup'].includes(requested))
    nookSettingsNavigate(app, requested);
}
function nookSettingsTrap(event, shell) {
  const focusable = [...shell.querySelectorAll('button, input, select, textarea, a[href], summary, [tabindex="0"]')]
    .filter(element => !element.disabled && element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden');
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (!first) return;
  const outside = !shell.contains(document.activeElement);
  if (event.shiftKey && (document.activeElement === first || outside)) {
    event.preventDefault(); last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || outside)) {
    event.preventDefault(); first.focus();
  }
}
function nookFontSizeLabel(key) {
  return {small:'Small',default:'Default',large:'Large',xl:'Extra large'}[key] || 'Default';
}
function nookSetFontSize(app, value) {
  const sizes = [['small','14px'],['default','15px'],['large','16px'],['xl','18px']];
  const choice = sizes[Number(value)];
  if (choice) app.setFontSize(...choice);
}
function nookCopy(value) { return JSON.parse(JSON.stringify(value)); }
// Configuration: config.toml values grouped by task. The server schema supplies the groups, labels,
// help, units, controls, and dependencies; fields it doesn't group still appear under their section.
function nookConfigPath(path) { const index = path.indexOf('.'); return [path.slice(0, index), path.slice(index + 1)]; }
function nookConfigAt(source, path) { const [section, key] = nookConfigPath(path); return source?.[section]?.[key]; }
function nookConfigSentence(label) {
  return String(label).split(' ').map((word, index) => index && /^[A-Z][a-z]+$/.test(word) ? word.toLowerCase() : word).join(' ');
}
function nookConfigField(app, path) {
  const [section, key] = nookConfigPath(path);
  const schema = app.configSchema || {};
  const control = schema.controls?.[path];
  const type = control === 'time' ? 'time' : control === 'switch' ? 'toggle01' : app.getFieldType(section, key);
  return {
    path, section, key, type,
    label: schema.labels?.[path] || nookConfigSentence(key.replace(/_/g, ' ')),
    unit: schema.units?.[path] || '',
    help: schema.descriptions?.[path] || '',
    bytes: schema.byte_units?.[path] || 0,
    requires: schema.requires?.[path] || [],
  };
}
function nookConfigSections(app, tier) {
  if (!app.configData || !app.configSchema) return [];
  if (tier === 'advanced' && !app.showAdvancedConfig) return [];
  const schema = app.configSchema;
  const inTier = path => {
    const [section, key] = nookConfigPath(path);
    if (!app.configData[section] || !(key in app.configData[section]) || schema.blacklist.includes(path)) return false;
    return schema.whitelist.includes(path) === (tier === 'basic');
  };
  const grouped = new Set((schema.groups || []).flatMap(group => group.fields));
  const groups = (schema.groups || []).map(group => ({ ...group, fields: group.fields.filter(inTier).map(path => nookConfigField(app, path)) }));
  // Settings config.toml gains later still appear, under their schema section.
  for (const [section, fields] of Object.entries(app.configData)) {
    const rest = Object.keys(fields).map(key => `${section}.${key}`).filter(path => inTier(path) && !grouped.has(path));
    if (rest.length) groups.push({ title: nookConfigSentence(schema.labels?.[section] || section), fields: rest.map(path => nookConfigField(app, path)) });
  }
  return groups.filter(group => group.fields.length);
}
function nookConfigSet(app, field, value) { app.configData[field.section][field.key] = value; app.nookConfigSaved = false; }
function nookConfigSame(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
function nookConfigDisabled(app, field) { return app.configSaving || field.requires.some(path => !nookConfigAt(app.configData, path)); }
function nookConfigOn(app, field) { const value = nookConfigAt(app.configData, field.path); return field.type === 'toggle01' ? Number(value) >= 1 : !!value; }
function nookConfigToggle(app, field) { const on = nookConfigOn(app, field); nookConfigSet(app, field, field.type === 'toggle01' ? (on ? 0 : 1) : !on); }
function nookConfigState(app, field) {
  const value = nookConfigAt(app.configData, field.path), fallback = nookConfigAt(app.configDefaults, field.path);
  if (!nookConfigSame(value, nookConfigAt(app.configOriginal, field.path))) return 'dirty';
  return fallback === undefined || nookConfigSame(value, fallback) ? 'default' : 'custom';
}
function nookConfigBytes(bytes) {
  const units = ['bytes', 'KiB', 'MiB', 'GiB', 'TiB'];
  let value = Number(bytes), unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${Number.isInteger(value) ? value : value.toFixed(1)}\u00a0${units[unit]}`;
}
function nookConfigFormat(field, value) {
  if (field.type === 'boolean' || field.type === 'toggle01') return (field.type === 'toggle01' ? Number(value) >= 1 : value) ? 'On' : 'Off';
  if (field.type === 'time') return nookTimeLabel(value);
  if (field.type === 'password') return value ? 'set' : 'empty';
  if (value === null || value === undefined || value === '') return 'empty';
  if (field.bytes) return nookConfigBytes(Number(value) * field.bytes);
  return field.unit ? `${value} ${field.unit}` : String(value);
}
function nookConfigHelp(app, field) {
  const value = Number(nookConfigAt(app.configData, field.path));
  const size = field.bytes && Number.isFinite(value) && value > 0 && field.unit !== nookConfigBytes(value * field.bytes).split('\u00a0')[1] ? ` Currently ${nookConfigBytes(value * field.bytes)}.` : '';
  return (field.help + size).trim();
}
function nookConfigStateText(app, field) {
  return nookConfigState(app, field) === 'dirty' ? 'Unsaved change' : `Default: ${nookConfigFormat(field, nookConfigAt(app.configDefaults, field.path))}`;
}
function nookConfigStateAction(app, field) {
  if (nookConfigState(app, field) === 'dirty') return nookConfigSet(app, field, nookCopy(nookConfigAt(app.configOriginal, field.path)));
  nookConfigSet(app, field, nookCopy(nookConfigAt(app.configDefaults, field.path)));
  // Saving a reset field removes its explicit value from config.toml instead of writing the default.
  if (!app.nookConfigResets.includes(field.path)) app.nookConfigResets.push(field.path);
}
function nookConfigTimeOptions(current) {
  const times = Array.from({ length: 96 }, (_, index) => `${String(Math.floor(index / 4)).padStart(2, '0')}:${String(index % 4 * 15).padStart(2, '0')}`);
  if (current && !times.includes(current)) times.push(current), times.sort();
  return times.map(time => ({ value: time, label: nookTimeLabel(time) }));
}
async function nookSaveConfig(app) {
  const changes = app.changedConfigValues();
  for (const path of app.nookConfigResets) {
    if (path in changes && nookConfigSame(nookConfigAt(app.configData, path), nookConfigAt(app.configDefaults, path))) changes[path] = null;
  }
  app.nookConfigResets = [];
  if (await app.submitConfigChanges(changes)) app.nookConfigSaved = true;
}
// Same scope as restoreConfigDefaults(): every explicit value except credentials and keys.
function nookConfigResettable(app) {
  const preserved = new Set(['tagging.api_key', 'web.password_hash']);
  return (app.configExplicit || []).filter(path => !path.startsWith('auth.') && !preserved.has(path));
}
function nookConfigCanRestore(app) { return nookConfigResettable(app).length > 0 || Object.keys(app.changedConfigValues()).length > 0; }
function nookConfigRestoreQuestion(app) {
  const count = nookConfigResettable(app).length, unsaved = Object.keys(app.changedConfigValues()).length;
  const saved = count ? (count === 1 ? 'Reset 1 saved setting to its default' : `Reset ${count} saved settings to their defaults`) : 'Nothing saved differs from the defaults';
  return `${saved}${unsaved ? `${count ? ' and discard' : '. Discard'} ${unsaved} unsaved change${unsaved === 1 ? '' : 's'}` : ''}?`;
}
async function nookRestoreDefaults(app) {
  app.configData = nookCopy(app.configOriginal);
  app.nookConfigResets = [];
  const changes = Object.fromEntries(nookConfigResettable(app).map(path => [path, null]));
  if (await app.submitConfigChanges(changes)) app.nookConfigSaved = true;
}


// Appearance: every theme is visible, grouped by light and dark backgrounds.
function nookThemeGroups() {
  const groups = { Light: [], Dark: [] };
  for (const [key, theme] of Object.entries(THEMES)) {
    const hex = theme['--bg-primary'] || '#000000';
    const luma = parseInt(hex.slice(1, 3), 16) * .299 + parseInt(hex.slice(3, 5), 16) * .587 + parseInt(hex.slice(5, 7), 16) * .114;
    groups[luma < 128 ? 'Dark' : 'Light'].push({ key, theme });
  }
  return Object.entries(groups).map(([label, themes]) => ({ label, themes }));
}
function nookThemeTileStyle(key, theme, app) {
  const current = app.currentTheme === key && app.currentAccent;
  const accent = current || theme['--accent-color'];
  const accentText = (current && theme._accents?.find(item => item.color === current)?.text) || theme['--accent-text'] || '#fff';
  return `--t-bg:${theme['--bg-primary']};--t-header:${theme['--bg-header'] || theme['--bg-primary']};--t-like:${theme['--danger-color']};--t-surface:${theme['--bg-secondary']};--t-text:${theme['--text-primary']};--t-muted:${theme['--text-secondary']};--t-border:${theme['--border-color']};--t-accent:${accent};--t-accent-text:${accentText}`;
}

function nookCountLabel(count) {
  return count === 1 ? '1 tweet' : `${Number(count || 0).toLocaleString()} tweets`;
}
function nookTrapFocus(event, container) {
  const items = [...container.querySelectorAll('button, input, textarea, [tabindex="0"]')].filter(item => !item.disabled && item.getClientRects().length);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

// Tags: browse, search, sort; select to merge; per-tag menu for search and delete.
function nookTagsPage() {
  return {
    query: '', sort: 'count', selecting: false, selected: [], keep: '', merge: false, confirmDelete: null,
    menu: null, menuTop: 0, menuRight: 0, menuTrigger: null, busy: false, status: '', returnFocus: null,
    rows() {
      const term = this.query.trim().toLowerCase();
      const tags = term ? this.globalTags.filter(tag => tag.tag.toLowerCase().includes(term)) : [...this.globalTags];
      return tags.sort((a, b) => this.sort === 'name' ? a.tag.localeCompare(b.tag) : (b.count - a.count) || a.tag.localeCompare(b.tag));
    },
    summary() {
      const total = this.globalTags.reduce((sum, tag) => sum + (tag.count || 0), 0);
      return this.globalTags.length ? `${this.globalTags.length} tags · used ${total.toLocaleString()} times` : 'Tag a tweet from its menu to start.';
    },
    countLabel: nookCountLabel,
    countOf(name) { return this.globalTags.find(tag => tag.tag === name)?.count || 0; },
    share(count) {
      const max = Math.max(...this.globalTags.map(tag => tag.count || 0), 1);
      return Math.max(4, Math.round((count / max) * 100));
    },
    isSelected(name) { return this.selected.includes(name); },
    toggle(name) { this.selected = this.isSelected(name) ? this.selected.filter(item => item !== name) : [...this.selected, name]; },
    toggleSelect() { this.selecting = !this.selecting; this.selected = []; this.status = ''; this.closeMenu(false); },
    openMenu(name, button) {
      if (this.menu === name) return this.closeMenu(true);
      const page = button.closest('.nook-tags-page');
      const anchor = button.getBoundingClientRect(), frame = page.getBoundingClientRect();
      const below = anchor.bottom + 160 < frame.bottom;
      this.menuTop = (below ? anchor.bottom + 4 : anchor.top - 156) - frame.top + page.scrollTop;
      this.menuRight = frame.right - anchor.right;
      this.menu = name; this.menuTrigger = button;
      this.$nextTick(() => page.querySelector('.nook-menu [role="menuitem"]')?.focus());
    },
    closeMenu(restoreFocus) {
      const trigger = this.menuTrigger;
      this.menu = null; this.menuTrigger = null;
      if (restoreFocus) trigger?.focus();
    },
    moveMenu(step) {
      const items = [...document.querySelectorAll('.nook-tags-page .nook-menu [role="menuitem"]')];
      const index = items.indexOf(document.activeElement);
      items[(index + step + items.length) % items.length]?.focus();
    },
    showTweets(name) {
      this.closeMenu(false);
      this.closeSettings();
      this.collectionFilter = 'all';
      this.searchQuery = 'tag:' + (name.includes(' ') ? `"${name}"` : name);
      this.search();
    },
    startMerge(name) {
      this.closeMenu(false);
      this.selecting = true; this.selected = [name];
      this.status = `Select the tags to merge with “${name}”.`;
    },
    openMerge() {
      this.keep = [...this.selected].sort((a, b) => this.countOf(b) - this.countOf(a))[0];
      this.returnFocus = document.activeElement;
      this.merge = true;
      // The sheet is displayed after this tick; focus it on the next frame.
      this.$nextTick(() => requestAnimationFrame(() => document.querySelector('.nook-tags-page .nook-sheet [role="radio"][aria-checked="true"]')?.focus()));
    },
    mergeOutcome() {
      const others = this.selected.filter(name => name !== this.keep);
      if (!this.keep || !others.length) return '';
      const total = this.selected.reduce((sum, name) => sum + this.countOf(name), 0);
      return `${others.map(name => `“${name}”`).join(', ')} will become “${this.keep}” on up to ${nookCountLabel(total)}. This can’t be undone.`;
    },
    async submitMerge() {
      const others = this.selected.filter(name => name !== this.keep);
      if (!this.keep || !others.length || this.busy) return;
      this.busy = true;
      try {
        const response = await fetch('/api/tags/merge', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ primary_tag: this.keep, merge_tags: others }) });
        if (!response.ok) throw new Error('The merge didn’t go through. Your tags are unchanged.');
        this.status = `Merged ${others.length} ${others.length === 1 ? 'tag' : 'tags'} into “${this.keep}”.`;
        this.merge = false; this.selecting = false; this.selected = [];
        await this.fetchGlobalTags();
      } catch (error) { this.status = error.message; }
      finally { this.busy = false; }
    },
    askDelete(name) {
      this.returnFocus = this.menuTrigger;
      this.closeMenu(false);
      this.confirmDelete = name;
      this.$nextTick(() => requestAnimationFrame(() => document.querySelector('.nook-tags-page .nook-confirm .nook-secondary-button')?.focus()));
    },
    async deleteTag() {
      const name = this.confirmDelete;
      if (!name || this.busy) return;
      this.busy = true;
      try {
        const response = await fetch(`/api/tags/global/${encodeURIComponent(name)}`, { method: 'DELETE' });
        if (!response.ok) throw new Error(`“${name}” couldn’t be deleted. Try again.`);
        this.globalTags = this.globalTags.filter(tag => tag.tag !== name);
        this.status = `Deleted “${name}”.`;
        this.confirmDelete = null;
        this.$nextTick(() => document.querySelector('.nook-tags-page input[type="search"]')?.focus());
      } catch (error) { this.status = error.message; }
      finally { this.busy = false; }
    },
    trap(event) { nookTrapFocus(event, event.currentTarget); },
    escape(event) {
      if (this.merge) { this.merge = false; this.returnFocus?.focus(); }
      else if (this.confirmDelete) { this.confirmDelete = null; this.returnFocus?.focus(); }
      else if (this.menu) this.closeMenu(true);
      else if (this.selecting) this.toggleSelect();
      else return;
      event.stopPropagation();
    },
  };
}

// Activity logs: runs grouped by day; a run opens as a nested page in the detail pane.
// Activity log lines (tweetnook/pipeline.py): "Step: event · fields", "WARNING: …", "ERROR: …", and
// "tweetnook <command>: started|complete". output.log lines carry an ISO timestamp; console.log lines don't.
const NOOK_LOG_RUN = /^tweetnook\b[^:]*: (started|complete|failed|stopped)\b/;
const NOOK_LOG_STEP = /^[A-Z][\w '\/&-]*?: (starting|progress|status|complete|failed|skipped|stopped)\b/;
function nookLogParse(text) {
  return String(text || '').split('\n').filter(line => line.trim()).map((raw, index) => {
    const stamp = raw.match(/^(\d{4}-\d\d-\d\dT[\d:.]+Z) (.*)$/);
    const text = stamp ? stamp[2] : raw;
    const run = text.match(NOOK_LOG_RUN), step = text.match(NOOK_LOG_STEP);
    const event = (run || step)?.[1] || '';
    // Same signals the original log view colored, using theme colors only.
    const tone = /^(ERROR|CRITICAL):|^Traceback/.test(text) || event === 'failed' ? 'error'
      : /^WARNING:/.test(text) ? 'warning'
      : event === 'progress' || event === 'status' ? 'quiet'
      : event ? 'milestone' : 'plain';
    return { index, raw, text, tone, quiet: !!step && tone === 'quiet',
      time: stamp ? new Date(stamp[1]).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }) : '' };
  });
}

// Runs are titled with their command ("tweetnook sync"); the list shows a plain name instead.
const NOOK_RUN_TITLES = {
  sync: 'Archive sync', 'sync all': 'Archive sync', 'sync bookmarks': 'Bookmark sync', 'sync likes': 'Like sync', 'sync tweets': 'Tweet sync',
  tag: 'Automated tagging', rehydrate: 'Archive enrichment', 'import enrich': 'Archive enrichment', 'import x-archive': 'Archive import',
};
function nookRunTitle(title) {
  const command = String(title || '').replace(/^tweetnook\s+/, '').trim();
  return NOOK_RUN_TITLES[command] || (command ? command[0].toUpperCase() + command.slice(1) : 'Activity');
}
function nookWhenLabel(seconds) {
  const date = new Date(seconds * 1000), today = new Date();
  const startOf = value => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const days = Math.round((startOf(today) - startOf(date)) / 86400000);
  const time = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (days === 0) return `Today, ${time}`;
  if (days === 1) return `Yesterday, ${time}`;
  return `${date.toLocaleDateString([], { month: 'short', day: 'numeric', year: date.getFullYear() === today.getFullYear() ? undefined : 'numeric' })}, ${time}`;
}
// Notices store ISO timestamps; older ones may have none.
function nookNoticeWhen(notice) {
  const time = Date.parse(notice?.created_at || '');
  return Number.isFinite(time) ? nookWhenLabel(time / 1000) : '';
}
function nookLogsPage() {
  return {
    copied: false, openFolds: [], flash: null, hasTime: false, liveTimer: null, logOffset: 0,
    originLabel(origin) { return { schedule: 'Scheduled', web: 'Web app', cli: 'Command line' }[origin] || origin || 'Command line'; },
    statusKind(run) { return { completed: 'ok', failed: 'failed', interrupted: 'failed', stopped: 'stopped', starting: 'running', running: 'running' }[run.status] || 'failed'; },
    statusLabel(run) { return { completed: 'Completed', failed: 'Failed', interrupted: 'Interrupted', stopped: 'Stopped', starting: 'Starting', running: 'Running' }[run.status] || 'Result unavailable'; },
    whenLabel(seconds) { return nookWhenLabel(seconds); },
    fullTime(seconds) { return new Date(seconds * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }); },
    durationLabel(run) {
      if (!run.completed_at) return '';
      const total = Math.max(0, Math.round(run.completed_at - run.started_at));
      if (total < 60) return `${total}s`;
      if (total < 3600) return `${Math.floor(total / 60)}m ${total % 60}s`;
      return `${Math.floor(total / 3600)}h ${Math.floor((total % 3600) / 60)}m`;
    },
    metaText(run) { return [this.originLabel(run.origin), this.statusLabel(run), this.whenLabel(run.started_at), this.durationLabel(run)].filter(Boolean).join(', '); },
    // Opened from the sync panel: show that run's log, not just the list.
    init() {
      const openPending = () => {
        const id = this.nookLogOpenRunId;
        if (!id || this.activityRunsLoading) return;
        const run = this.activityRuns.find(item => item.run_id === id);
        if (!run) return;
        this.nookLogOpenRunId = null;
        this.open(run);
      };
      this.$watch('nookLogOpenRunId', openPending);
      this.$watch('activityRunsLoading', openPending);
      this.$watch('activityRuns', openPending);
    },
    async open(run) {
      clearTimeout(this.liveTimer);
      this.nookLogRun = run; this.nookLogLines = null; this.copied = false; this.openFolds = []; this.flash = null; this.hasTime = false; this.logOffset = 0;
      this.$nextTick(() => document.querySelector('.nook-detail-header h2')?.focus());
      const [details, lines] = await Promise.all([this.readDetails(run.run_id), this.readLog(run.run_id, 0)]);
      if (this.nookLogRun?.run_id !== run.run_id) return;
      if (details) this.nookLogRun = { ...run, ...details };
      this.nookLogLines = lines || [];
      this.hasTime = this.nookLogLines.some(line => line.time);
      if (['starting', 'running'].includes(this.nookLogRun.status)) this.liveTimer = setTimeout(() => this.refreshLive(run.run_id), 2000);
    },
    async readDetails(runId) {
      try {
        const response = await fetch(`/api/activity/runs/${encodeURIComponent(runId)}`);
        return response.ok ? await response.json() : null;
      } catch (_) {
        return null;
      }
    },
    // Complete lines written since logOffset; X-Log-Offset says where to continue.
    async readLog(runId, offset) {
      try {
        const response = await fetch(`/api/activity/runs/${encodeURIComponent(runId)}/log?offset=${offset}`);
        if (!response.ok) return null;
        const text = await response.text();
        const next = Number(response.headers.get('X-Log-Offset'));
        const restarted = Number.isFinite(next) && next < offset;
        this.logOffset = Number.isFinite(next) ? next : offset;
        const base = offset && !restarted ? (this.nookLogLines || []).length : 0;
        const lines = nookLogParse(text).map(line => ({ ...line, index: line.index + base }));
        return offset && !restarted ? [...(this.nookLogLines || []), ...lines] : lines;
      } catch (_) {
        return null;
      }
    },
    // While a run is in progress, read its new log lines every two seconds; stop when it ends or the view closes.
    async refreshLive(runId) {
      if (this.nookLogRun?.run_id !== runId || !this.showSettingsModal || this.settingsTab !== 'logs') return;
      const [details, lines] = await Promise.all([this.readDetails(runId), this.readLog(runId, this.logOffset)]);
      if (this.nookLogRun?.run_id !== runId) return;
      if (details) this.nookLogRun = { ...this.nookLogRun, ...details };
      if (lines) { this.nookLogLines = lines; this.hasTime = lines.some(line => line.time); }
      if (['starting', 'running'].includes(this.nookLogRun?.status)) this.liveTimer = setTimeout(() => this.refreshLive(runId), 2000);
    },
    back() { nookSettingsBack(this); },
    plural(count, noun) { return `${count.toLocaleString()} ${noun}${count === 1 ? '' : 's'}`; },
    countOf(tone) { return (this.nookLogLines || []).filter(line => line.tone === tone).length; },
    lineCountLabel() { return this.nookLogLines?.length ? this.plural(this.nookLogLines.length, 'line') : ''; },
    // Runs of 4+ progress/status lines collapse behind their latest line; everything else stays visible.
    items() {
      const items = [], push = line => items.push({ type: 'line', key: 'l' + line.index, line });
      let run = [];
      const flush = () => {
        if (run.length >= 4) {
          const id = run[0].index;
          items.push({ type: 'fold', key: 'f' + id, id, count: run.length - 1 });
          if (this.openFolds.includes(id)) run.slice(0, -1).forEach(push);
          push(run[run.length - 1]);
        } else run.forEach(push);
        run = [];
      };
      for (const line of this.nookLogLines || []) {
        if (line.quiet && (!run.length || run[0].text.split(':')[0] === line.text.split(':')[0])) run.push(line);
        else { flush(); line.quiet ? run.push(line) : push(line); }
      }
      flush();
      return items;
    },
    toggleFold(id) { this.openFolds = this.openFolds.includes(id) ? this.openFolds.filter(item => item !== id) : [...this.openFolds, id]; },
    foldLabel(item) { return this.openFolds.includes(item.id) ? `Hide ${this.plural(item.count, 'update')}` : `${this.plural(item.count, 'earlier update')}`; },
    jumpTo(tone) {
      const index = (this.nookLogLines || []).findIndex(line => line.tone === tone);
      if (index < 0) return;
      this.flash = index;
      this.$nextTick(() => document.querySelector(`.nook-log-line[data-line="${index}"]`)?.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }));
      setTimeout(() => { if (this.flash === index) this.flash = null; }, 2000);
    },
    rawText() { return (this.nookLogLines || []).map(line => line.raw).join('\n') + '\n'; },
    async copy() {
      try { await navigator.clipboard.writeText(this.rawText()); this.copied = true; setTimeout(() => { this.copied = false; }, 2000); }
      catch (_) { this.copied = false; }
    },
    download() {
      const url = URL.createObjectURL(new Blob([this.rawText()], { type: 'text/plain' }));
      const link = Object.assign(document.createElement('a'), { href: url, download: `${this.nookLogRun.run_id}.log` });
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  };
}

// Custom dropdown (listbox). TweetNook uses these instead of native selects; see design.md.
function nookSelect({ label, options, value, choose, searchable = false }) {
  return {
    label, searchable, placeholder: 'Search', open: false, up: false, query: '', active: 0, typed: '', typedAt: 0,
    uid: 'nook-select-' + Math.random().toString(36).slice(2, 8),
    all() { return options() || []; },
    filtered() {
      const normalize = text => String(text).toLowerCase().replace(/[\s_/]+/g, ' ').trim();
      const term = normalize(this.query);
      return term ? this.all().filter(option => normalize(`${option.label} ${option.detail || ''}`).includes(term)) : this.all();
    },
    isSelected(option) { return String(option.value) === String(value()); },
    currentLabel() { return this.all().find(option => this.isSelected(option))?.label ?? String(value() ?? ''); },
    toggle() { this.open ? this.close(true) : this.openMenu(); },
    openMenu() {
      if (this.$el.closest('fieldset')?.disabled) return;
      this.query = ''; this.open = true;
      this.active = Math.max(0, this.filtered().findIndex(option => this.isSelected(option)));
      // Open upward when the scrolling pane has more room above the trigger than below it.
      const trigger = this.$refs.trigger.getBoundingClientRect();
      const pane = this.$el.closest('.nook-setting-body')?.getBoundingClientRect() || { top: 0, bottom: innerHeight };
      const below = pane.bottom - trigger.bottom, above = trigger.top - pane.top;
      this.up = below < 320 && above > below;
      // Focus the list on the next frame, unless it was already closed (e.g. a quick Escape).
      this.$nextTick(() => requestAnimationFrame(() => { if (!this.open) return; (this.searchable ? this.$refs.search : this.$refs.list)?.focus(); this.reveal(); }));
    },
    close(restoreFocus) { this.open = false; if (restoreFocus) this.$refs.trigger.focus(); },
    pick(option) { choose(option.value); this.close(true); },
    move(step) {
      const count = this.filtered().length;
      if (!count) return;
      this.active = Math.min(count - 1, Math.max(0, this.active + step));
      this.reveal();
    },
    reveal() { this.$nextTick(() => this.$refs.list?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })); },
    key(event) {
      const list = this.filtered();
      if (event.key === 'ArrowDown') { event.preventDefault(); this.move(1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); this.move(-1); }
      else if (event.key === 'PageDown') { event.preventDefault(); this.move(8); }
      else if (event.key === 'PageUp') { event.preventDefault(); this.move(-8); }
      else if ((event.key === 'Home' || event.key === 'End') && !this.searchable) { event.preventDefault(); this.active = event.key === 'Home' ? 0 : list.length - 1; this.reveal(); }
      else if (event.key === 'Enter' || (event.key === ' ' && !this.searchable)) { event.preventDefault(); if (list[this.active]) this.pick(list[this.active]); }
      else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); this.close(true); }
      else if (event.key === 'Tab') this.close(false);
      else if (!this.searchable && event.key.length === 1 && !event.metaKey && !event.ctrlKey) {
        // Type-to-jump, like a native select.
        const now = Date.now();
        this.typed = (now - this.typedAt < 700 ? this.typed : '') + event.key.toLowerCase();
        this.typedAt = now;
        const index = list.findIndex(option => String(option.label).toLowerCase().startsWith(this.typed));
        if (index >= 0) { this.active = index; this.reveal(); }
      }
    },
  };
}

// Schedule options and plain-language summary, built from scheduleForm.
const NOOK_WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
function nookOrdinal(n) { const suffix = ['th', 'st', 'nd', 'rd'], v = n % 100; return n + (suffix[(v - 20) % 10] || suffix[v] || suffix[0]); }
function nookEvery(hours) {
  if (hours >= 24 && hours % 24 === 0) { const days = hours / 24; return days === 7 ? 'Every week' : days === 1 ? 'Every day' : `Every ${days} days`; }
  return hours === 1 ? 'Every hour' : `Every ${hours} hours`;
}
function nookWithCurrent(values, current) {
  const number = Number(current);
  return Number.isFinite(number) && !values.includes(number) ? [...values, number].sort((a, b) => a - b) : values;
}
function nookIntervalOptions(current) { return nookWithCurrent([1, 2, 3, 4, 6, 8, 12, 24, 48, 72, 168], current).map(hours => ({ value: hours, label: nookEvery(hours) })); }
function nookDayOptions() { return Array.from({ length: 31 }, (_, index) => ({ value: index + 1, label: nookOrdinal(index + 1) })); }
function nookHourOptions() { return Array.from({ length: 12 }, (_, index) => ({ value: String(index + 1), label: String(index + 1) })); }
function nookMinuteOptions() { return Array.from({ length: 60 }, (_, index) => { const minute = String(index).padStart(2, '0'); return { value: minute, label: minute }; }); }
function nookDelayLabel(hours) {
  if (hours === 0) return 'No delay';
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  return hours === 1 ? '1 hour' : `${hours} hours`;
}
function nookDelayOptions(current) { return nookWithCurrent([0, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 12, 18, 24], current).map(hours => ({ value: hours, label: nookDelayLabel(hours) })); }
const nookZoneOffsets = new Map();
function nookZoneOffset(zone) {
  if (zone === 'local') return '';
  if (!nookZoneOffsets.has(zone)) {
    let offset = '';
    try { offset = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'shortOffset' }).formatToParts(new Date()).find(part => part.type === 'timeZoneName')?.value || ''; }
    catch (_) { offset = ''; }
    nookZoneOffsets.set(zone, offset);
  }
  return nookZoneOffsets.get(zone);
}
function nookZoneOptions(app) {
  return app.scheduleTimezoneOptions().map(zone => ({ value: zone.value, label: zone.label.replace(/_/g, ' '), detail: nookZoneOffset(zone.value) }));
}
function nookZoneNow(zone) {
  if (!zone || zone === 'local') return 'Uses the time zone of the computer running TweetNook.';
  try { return `It’s ${new Date().toLocaleTimeString('en-US', { timeZone: zone, hour: 'numeric', minute: '2-digit' })} there now.`; }
  catch (_) { return ''; }
}
function nookTimeLabel(time) {
  const [hour, minute] = String(time || '03:00').split(':').map(Number);
  return `${hour % 12 || 12}:${String(minute || 0).padStart(2, '0')} ${hour >= 12 ? 'PM' : 'AM'}`;
}
// details: false leaves out the random start window and time zone (used by Setup).
function nookScheduleSentence(app, { details = true } = {}) {
  const form = app.scheduleForm;
  if (!form) return '';
  if (form.cadence === 'hours') return nookEvery(Number(form.every_hours));
  let sentence = form.cadence === 'daily' ? 'Every day' : form.cadence === 'weekly' ? `Every ${NOOK_WEEKDAYS[form.weekday] || 'week'}` : `On the ${nookOrdinal(Number(form.day_of_month))} of every month`;
  sentence += ` at ${nookTimeLabel(form.time)}`;
  if (!details) return sentence;
  const earliest = Number(form.random_offset_min_hours), latest = Number(form.random_offset_max_hours);
  if (form.randomize_time && app.scheduleRandomWindowValid() && latest > 0) {
    sentence += earliest > 0 ? `, starting ${nookDelayLabel(earliest)} to ${nookDelayLabel(latest)} later` : `, starting up to ${nookDelayLabel(latest)} later`;
  }
  if (form.timezone && form.timezone !== 'local') sentence += ` (${form.timezone.replace(/_/g, ' ')})`;
  return sentence;
}
function nookScheduleHeadline(app) {
  if (!app.scheduleForm?.enabled) return 'Scheduled syncs are off';
  const sentence = nookScheduleSentence(app);
  return sentence ? 'Syncs ' + sentence[0].toLowerCase() + sentence.slice(1) : '';
}
function nookScheduleStatusLine(app) {
  const form = app.scheduleForm, status = app.scheduleSettings;
  if (app.scheduleHasChanges()) return form.enabled ? 'Not saved yet. Save to update the next sync.' : 'Not saved yet. Save to turn scheduled syncs off.';
  if (!form.enabled) return 'Turn on automatic syncs to keep your archive current.';
  if (status?.configured && status.date) return `Next sync ${status.date}${status.relative ? ` (${status.relative})` : ''}`;
  return 'Save to schedule the next sync.';
}
