// Trusted, unchanged icons.md glyphs used by the setup completion page.

function nookSetupCompletionIcon(main, badge) {
    return `<span class="setup-completion-icon">${SETUP_COMPLETION_ICONS[main].replace('<svg ', '<svg class="setup-completion-icon-main" ')}<span class="setup-completion-icon-badge">${SETUP_COMPLETION_ICONS[badge].replace('<svg ', '<svg class="setup-completion-icon-detail" ')}</span></span>`;
}

// This page describes existing jobs; opening it never starts a job.
function nookSetupCompletion(app) {
    const archive = app.setupData?.archive || {};
    const needsConnection = !app.setupData?.auth?.verified;
    const importing = archive.initialization_state === 'running';
    const importFailed = ['failed', 'interrupted'].includes(archive.initialization_state);
    const migration = archive.initialization_mode === 'migration';
    const imported = !migration && (archive.initialization_mode === 'import' || archive.imported);
    const scheduled = !!(app.setupData?.schedule?.enabled ?? app.scheduleForm?.enabled);
    const collectionLabels = { bookmarks: 'bookmarks', likes: 'likes', tweets: 'authored posts' };
    const collections = (app.setupData?.schedule?.collections || app.nookSyncCollections || ['bookmarks', 'likes'])
        .map(key => collectionLabels[key]).filter(Boolean);
    const savedItems = collections.length > 1
        ? collections.slice(0, -1).join(', ') + ' and ' + collections.at(-1)
        : collections[0] || 'posts';
    const run = app.activity || app.lastActivity;
    const enrichStep = run?.kind === 'enrich' && ['running', 'complete', 'failed'].includes(archive.enrichment_state)
        ? run.steps?.find(step => step.key === 'archive-enrich') : null;
    const enrichmentDone = imported && !importing && !importFailed && (
        enrichStep?.state === 'complete' || archive.enriched || archive.enrichment_state === 'complete'
    );
    const enrichmentFailed = !enrichmentDone && (archive.enrichment_state === 'failed' || enrichStep?.state === 'failed');
    const enrichmentRunning = imported && !enrichmentDone && !enrichmentFailed && archive.enrichment_state === 'running';
    const syncRun = !importing && !importFailed && (!imported || enrichmentDone)
        && run && (run.kind === 'sync' || (run.kind === 'enrich' && enrichmentDone));
    const syncDone = !!syncRun && run.success === true;
    const syncFailed = !!syncRun && (run.success === false || run.stopped);
    const syncRunning = !!syncRun && !!app.activity && !syncFailed && !syncDone;
    const stages = [];
    const stage = (key, title, description, state, label, main, badge) => {
        stages.push({ key, title, description, state, label, icon: nookSetupCompletionIcon(main, badge) });
    };
    if (imported || migration) {
        stage('import', migration ? 'Migration' : 'Import', migration
            ? 'Bring the posts and history from your TweetXVault archive into TweetNook.'
            : 'Bring the posts and history in your downloaded X archive into TweetNook.',
        importFailed ? 'attention' : importing ? 'active' : 'done',
        importFailed ? 'Needs attention' : importing ? 'Running' : 'Saved', 'document', 'download');
    }
    if (imported) {
        const state = importFailed || enrichmentFailed ? 'attention' : enrichmentDone ? 'done'
            : needsConnection ? 'connection' : enrichmentRunning ? 'active' : 'queued';
        stage('enrich', 'Enrichment', 'Look up imported posts on X to fill in missing text, authors, and other details.',
            state, { attention: 'Needs attention', done: 'Done', connection: 'Needs connection', active: 'Running', queued: 'Up next' }[state], 'cards', 'search');
    }
    const waiting = importing || (imported && !enrichmentDone);
    const syncState = importFailed || enrichmentFailed || syncFailed ? 'attention' : syncDone ? 'done'
        : needsConnection ? 'connection' : syncRunning ? 'active' : 'queued';
    const syncLabel = { attention: 'Needs attention', done: 'Done', connection: 'Needs connection', active: 'Running', queued: waiting ? 'Up next' : scheduled ? 'Scheduled' : 'Not started' }[syncState];
    stage('sync', 'First sync', imported
        ? `Catch up on your ${savedItems}, then save threads, media, and linked content where available.`
        : `Save your ${savedItems} from X, then fetch threads, media, and linked content where available. Older history may be incomplete without an archive import.`,
    syncState, syncLabel, 'database', 'refresh');
    let intro = 'You’re all set. TweetNook will keep working in the background to turn your X archive into a richer, searchable collection.';
    if (needsConnection && importing) intro = 'Your import is still running locally. Connect X to start enrichment and sync once it finishes.';
    else if (needsConnection) intro = imported || migration
        ? 'Connect X to fill in missing details and start your first sync. You can browse your imported posts while those steps wait.'
        : 'Setup is complete. You chose an empty archive and skipped connecting to X, so there are no posts to browse yet.';
    else if (!imported) intro = scheduled
        ? 'Your first scheduled sync will begin building your archive from X.'
        : 'Scheduled syncs are off. Start your first sync from Activity when you’re ready.';
    if (importFailed || enrichmentFailed || syncFailed) intro = 'Your setup choices are saved. A preparation step needs attention before your archive is ready.';
    else if (syncDone) intro = 'Your first sync is complete. Your saved posts are ready to browse and search.';
    else if (syncRunning) intro = `Your first sync is running. TweetNook is saving your ${savedItems} from X.`;
    else if (imported && enrichmentDone && !syncRunning) intro = 'Your imported posts are saved and enrichment is complete. The first sync will catch up with X.';
    let note = '';
    if (importFailed || enrichmentFailed || syncFailed) note = 'A preparation step needs attention. Open Activity to review what stopped; already saved data is kept.';
    else if (syncDone) note = 'Your first sync is complete. Your saved posts are ready to browse and search.';
    else if (syncRunning) note = 'Your first sync is catching up with X. Activity shows the current step and any rate-limit waits.';
    else if (importing) note = `Your ${migration ? 'migration' : 'import'} is still running. You can close this popup while the local work continues.`;
    else if (enrichmentRunning) note = 'Your import is finished. Enrichment is running; the first sync follows automatically.';
    else if (imported && enrichmentDone && !needsConnection) note = scheduled
        ? 'Sync starts at the next scheduled run.'
        : 'Open Activity to start your first sync when you’re ready.';
    else if (imported && !enrichmentDone && !needsConnection) note = archive.enrichment_state === 'pending'
        ? 'Imported posts still need enrichment. Open Activity to continue enrichment and its follow-up sync.'
        : 'Enrichment is waiting for the current task to finish; the first sync follows automatically.';
    else if (!needsConnection && !imported) note = scheduled
        ? 'Import and enrichment are skipped. Sync starts at the next scheduled run.'
        : `Nothing is running yet. Open Activity to start a sync for your ${savedItems}.`;
    const timeSubject = imported ? 'Initial enrichment and sync' : 'The first sync';
    return {
        stages, needsConnection, intro, note,
        title: imported ? 'The first run can take days' : 'The first sync can take days',
        time: `${needsConnection ? 'Once you connect, ' + timeSubject.toLowerCase() : timeSubject} can take hours or potentially days. X limits how many requests TweetNook can make, so TweetNook may spend long periods waiting before continuing.`,
        usability: syncDone ? 'Future syncs keep your archive up to date as new posts are saved.'
            : 'Your archive may have little to browse, missing details, or incomplete search results until the first sync finishes.',
        keep: needsConnection
            ? imported || migration
                ? 'Your imported data and settings stay saved locally. Connect to X in Setup when you’re ready for enrichment and sync.'
                : 'Your settings stay saved locally. Connect to X when you’re ready to start collecting posts.'
            : 'Keep TweetNook running on its computer or server. You can close this tab; check Activity whenever you want to see progress.',
        schedule: needsConnection ? 'Scheduled syncs cannot run until you connect to X.' : scheduled
            ? `After this, scheduled syncs keep your ${savedItems} up to date.`
            : 'Scheduled syncs are off. You can enable them in Settings → Schedule.',
    };
}
const SETUP_COMPLETION_ICONS = {
  "cards": "<svg data-icon=\"cards\" aria-hidden=\"true\" focusable=\"false\" xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M11 16H7v-1.8h4zm6 0h-4v-5h4zm-6-3.2H7V11h4z\"/><path d=\"M18.5 6A3.5 3.5 0 0 1 22 9.5v8a3.5 3.5 0 0 1-3.5 3.5h-13A3.5 3.5 0 0 1 2 17.5v-8A3.5 3.5 0 0 1 5.5 6zm-13 2C4.67 8 4 8.67 4 9.5v8c0 .83.67 1.5 1.5 1.5h13c.83 0 1.5-.67 1.5-1.5v-8c0-.83-.67-1.5-1.5-1.5z\"/><path d=\"M17 2.5a2 2 0 0 1 2 2H5c0-1.1.9-2 2-2z\"/></svg>",
  "database": "<svg data-icon=\"database\" aria-hidden=\"true\" focusable=\"false\" xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M20.5 18c0 .78-.4 1.42-.9 1.9a6 6 0 0 1-1.93 1.15c-1.5.6-3.5.95-5.67.95s-4.17-.35-5.67-.95a6 6 0 0 1-1.93-1.16A2.6 2.6 0 0 1 3.5 18V5.5c0-.78.48-1.37.97-1.76.5-.4 1.17-.71 1.91-.96C7.86 2.28 9.85 2 12 2s4.14.29 5.62.78c.74.25 1.4.56 1.9.96s.98.98.98 1.76zm-2-3.83q-.4.2-.83.38c-1.5.6-3.5.95-5.67.95s-4.17-.35-5.67-.95a8 8 0 0 1-.83-.38V18c0 .05.02.2.28.44q.38.37 1.29.75c1.21.49 2.95.81 4.93.81s3.72-.32 4.93-.8q.91-.39 1.3-.76c.25-.24.27-.39.27-.44zm-13-2.67c0 .05.02.2.28.44q.38.37 1.29.75c1.21.49 2.95.81 4.93.81s3.72-.32 4.93-.8q.91-.39 1.3-.76c.25-.24.27-.39.27-.44V7.87a9 9 0 0 1-.88.35c-1.48.5-3.47.78-5.62.78s-4.14-.29-5.62-.78q-.46-.15-.88-.35zM12 4c-2 0-3.76.27-4.99.68q-.93.32-1.3.63l-.19.19q.03.06.19.19.37.3 1.3.63c1.23.4 3 .68 4.99.68s3.76-.27 4.99-.68q.93-.32 1.3-.63.16-.13.19-.19l-.19-.19q-.37-.3-1.3-.63c-1.23-.4-3-.68-4.99-.68\"/></svg>",
  "document": "<svg data-icon=\"document\" aria-hidden=\"true\" focusable=\"false\" xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M15.5 2.5q1.02-.01 1.72.04c.48.04.94.12 1.37.34.66.34 1.2.87 1.53 1.53.22.43.3.89.34 1.37q.05.7.04 1.72v9q.01 1.02-.04 1.72a4 4 0 0 1-.34 1.37 3.5 3.5 0 0 1-1.53 1.53c-.43.22-.89.3-1.37.34q-.7.05-1.72.04h-7q-1.03.01-1.72-.04a4 4 0 0 1-1.37-.34 3.5 3.5 0 0 1-1.53-1.53 4 4 0 0 1-.34-1.37q-.05-.7-.04-1.72V10A7.5 7.5 0 0 1 11 2.5zm-3.5 2a1 1 0 0 0-1 1v1A3.5 3.5 0 0 1 7.5 10H7c-.83 0-1.5.67-1.5 1.5v5c0 .72 0 1.2.03 1.56s.08.52.13.62q.23.43.66.66c.1.05.26.1.62.13.37.03.84.03 1.56.03h7c.72 0 1.2 0 1.56-.03s.52-.08.62-.13q.43-.23.66-.66c.05-.1.1-.26.13-.62.03-.37.03-.84.03-1.56v-9c0-.72 0-1.2-.03-1.56a2 2 0 0 0-.13-.62 1.5 1.5 0 0 0-.66-.66c-.1-.05-.26-.1-.62-.13-.37-.03-.84-.03-1.56-.03zm-2.93.35A5.5 5.5 0 0 0 5.8 8.2 4 4 0 0 1 7 8h.5C8.33 8 9 7.33 9 6.5v-1a3 3 0 0 1 .07-.65\"/></svg>",
  "download": "<svg data-icon=\"download\" xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 24 24\" width=\"24\" height=\"24\" fill=\"currentColor\" aria-hidden=\"true\" focusable=\"false\"> <path d=\"M11.99 16l-5.7-5.7L7.7 8.88l3.29 3.3V2.59h2v9.59l3.3-3.3 1.41 1.42-5.71 5.7zM21 15l-.02 3.51c0 1.38-1.12 2.49-2.5 2.49H5.5C4.11 21 3 19.88 3 18.5V15h2v3.5c0 .28.22.5.5.5h12.98c.28 0 .5-.22.5-.5L19 15h2z\" /> </svg>",
  "refresh": "<svg data-icon=\"refresh\" aria-hidden=\"true\" focusable=\"false\" xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M21.45 13A9.5 9.5 0 0 1 5 18.42V21H3v-5.96L2.99 15H9v2H6.41a7.5 7.5 0 0 0 13.02-4zM12 2.5a9.5 9.5 0 0 1 7 3.08V3h2v5.96l.01.04H15V7h2.59a7.5 7.5 0 0 0-13.02 4H2.55A9.5 9.5 0 0 1 12 2.5\"/></svg>",
  "search": "<svg data-icon=\"search\" aria-hidden=\"true\" focusable=\"false\" xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M10.75 1.75a9 9 0 0 1 7.03 14.62l4.43 4.42-1.42 1.42-4.42-4.43a9 9 0 1 1-5.62-16.03m0 2a7 7 0 1 0 0 14 7 7 0 0 0 0-14\"/></svg>"
};
