/**
 * How the Jobs panel describes a job: durations, sizes and times,
 * step progress and states, issues, run summaries and the schedule line.
 */

function activityViewMethods() {
    return {
        activityScheduleDayText() {
            const schedule = this.activitySchedule || {};
            if (!schedule.enabled) return schedule.relative || 'Not scheduled';

            const nextRunAt = Number(schedule.next_run_at);
            if (Number.isFinite(nextRunAt) && nextRunAt > 0) {
                const now = new Date();
                const next = new Date(nextRunAt * 1000);
                const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
                const nextDay = new Date(next.getFullYear(), next.getMonth(), next.getDate());
                const dayOffset = Math.round((nextDay - today) / 86400000);
                if (dayOffset === 0) return 'Today';
                if (dayOffset === 1) return 'Tomorrow';
                return next.toLocaleDateString('en-US', {
                    weekday: 'short',
                    month: 'short',
                    day: 'numeric',
                });
            }

            const dateLabel = String(schedule.date || '').split('·')[0].trim();
            return dateLabel || schedule.relative || 'Scheduled';
        },

        activityScheduleTimeText() {
            const schedule = this.activitySchedule || {};
            if (!schedule.enabled) return '';

            const configuredTime = String(schedule.config?.time || '');
            const configuredMatch = configuredTime.match(/^(\d{2}):(\d{2})$/);
            if (configuredMatch && schedule.config?.cadence !== 'hours') {
                const hour = Number(configuredMatch[1]);
                const minute = configuredMatch[2];
                const period = hour >= 12 ? 'PM' : 'AM';
                const displayHour = hour % 12 || 12;
                const prefix = schedule.config?.randomize_time ? 'around' : 'at';
                return `${prefix} ${displayHour}:${minute} ${period}`;
            }

            const displayedTime = String(schedule.date || schedule.relative || '')
                .match(/\b\d{1,2}:\d{2}\s*[AP]M\b/i)?.[0];
            return displayedTime ? `at ${displayedTime.toUpperCase()}` : '';
        },

        formatActivityDuration(seconds) {
            const whole = Math.max(Math.round(Number(seconds) || 0), 0);
            const hours = Math.floor(whole / 3600);
            const minutes = Math.floor((whole % 3600) / 60);
            const remainder = whole % 60;
            return hours
                ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
                : `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
        },

        formatActivityBytes(value) {
            const bytes = Math.max(Number(value) || 0, 0);
            if (bytes < 1024) return `${bytes.toLocaleString()} B`;
            const units = ['KiB', 'MiB', 'GiB', 'TiB'];
            let amount = bytes / 1024;
            let index = 0;
            while (amount >= 1024 && index < units.length - 1) {
                amount /= 1024;
                index += 1;
            }
            return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${units[index]}`;
        },

        activityPercent(step) {
            if (!step || !step.total) return 0;
            return Math.max(0, Math.min(100, step.completed / step.total * 100));
        },

        activitySegmentPercent(page) {
            if (!page) return 0;
            if (page.is_summary) return this.activity ? 0 : 100;
            if (page.state === 'complete' || page.state === 'skipped') return 100;
            return this.activityPercent(page);
        },

        activitySegmentState(page) {
            if (this.displayActivity?.stopped && page?.state === 'failed') return 'cancelled';
            return page?.state || 'pending';
        },

        activityPageStateLabel(page) {
            const labels = {
                active: 'In progress',
                complete: 'Complete',
                failed: 'Failed',
                cancelled: 'Canceled',
                skipped: 'Skipped',
                pending: 'Waiting',
            };
            return labels[this.activitySegmentState(page)] || 'Waiting';
        },

        activityPageUpdateText(page) {
            const counters = String(page?.counters || '').trim();
            if (counters) return `Latest update · ${counters}`;
            const labels = {
                active: 'Waiting for first result',
                complete: 'Stage complete',
                failed: 'Stopped with an error',
                cancelled: 'Canceled',
                skipped: 'No work needed',
                pending: 'Waiting to start',
            };
            return `Latest update · ${labels[this.activitySegmentState(page)] || 'Waiting to start'}`;
        },

        activityPageTimingText(page) {
            const state = this.activitySegmentState(page);
            const eta = Number(page?.eta_seconds);
            if (state === 'active' && page?.eta_seconds != null && Number.isFinite(eta)) {
                return `Timing · About ${this.formatActivityDuration(eta)} remaining`;
            }
            const labels = {
                active: 'Estimating remaining time',
                complete: 'Completed',
                failed: 'Stopped',
                cancelled: 'Canceled',
                skipped: 'No runtime needed',
                pending: 'Available when this stage starts',
            };
            return `Timing · ${labels[state] || 'Available when this stage starts'}`;
        },

        activitySteps() {
            return this.displayActivity?.steps || [];
        },

        activityActiveStep() {
            return this.activitySteps().find(step => step.state === 'active') || null;
        },

        // The availability check shares the sync drawer; its wording follows the run.
        activityIsCheck(run = this.displayActivity) {
            return !this.activityStartPending && /^tweetnook\s+check$/i.test(String(run?.title || '').trim());
        },

        activityCommandLabel() {
            const pending = this.activityStartingKind || 'sync';
            const rawTitle = String(
                this.activityStartPending && this.activityStartingKind
                    ? `tweetnook ${pending}`
                    : this.displayActivity?.title || `tweetnook ${pending}`
            );
            const command = rawTitle.replace(/^tweetnook\s+/i, '').trim().toLowerCase();
            const labels = {
                sync: 'Archive Sync',
                enrich: 'Archive Preparation',
                import: 'Archive Import',
                'import enrich': 'Archive Preparation',
                'import x-archive': 'Archive Import',
                'threads expand': 'Thread Expansion',
                check: 'Availability Check',
                'articles refresh': 'Article Refresh',
                'media download': 'Media Download',
                unfurl: 'Link Preview Refresh',
                tag: 'Automated Tagging',
                migrate: 'Archive Migration',
            };
            return labels[command] || rawTitle.replace(/^tweetnook\s+/i, '') || 'Activity';
        },

        activityIssues() {
            return Array.isArray(this.displayActivity?.issues) ? this.displayActivity.issues : [];
        },

        activityIssueCount() {
            return this.activityIssues().reduce(
                (total, issue) => total + Math.max(Number(issue.count) || 1, 1),
                0,
            );
        },

        activityRunState() {
            if (this.activity) return 'active';
            if (this.displayActivity?.success === true) return 'complete';
            if (this.displayActivity?.stopped || this.displayActivity?.success === false) {
                return 'failed';
            }
            return this.displayActivity ? 'failed' : 'pending';
        },

        activityRunStateLabel() {
            if (this.activity) return 'In progress';
            if (this.displayActivity?.stopped) return 'Canceled';
            if (this.displayActivity?.success === false) return 'Failed';
            if (this.displayActivity?.success === true) return 'Complete';
            return this.displayActivity ? 'Interrupted' : 'Waiting';
        },

        activityRunStatusTitle() {
            if (this.activity) return 'Sync in progress';
            if (this.displayActivity?.stopped) return 'Last run stopped';
            if (this.displayActivity?.success === false) return 'Last run failed';
            if (this.displayActivity?.success === true) return 'Last run completed';
            return this.displayActivity ? 'Last run interrupted' : 'Run summary';
        },

        activityHeaderText() {
            if (this.activity || this.activityStartPending) return this.activityCommandLabel() + ' in progress…';
            const last = this.lastActivity;
            const command = String(last?.title || '').replace(/^tweetnook\s+/i, '').trim();
            const noun = this.activityIsCheck(last) ? 'Check' : !command || command === 'sync' ? 'Sync' : 'Job';
            if (last?.stopped) return `${noun} stopped — open Activity`;
            if (last?.success === false) return `${noun} failed — open Activity`;
            if (last && last.success !== true) return `${noun} interrupted — open Activity`;
            return this.lastSyncFormatted ? 'Synced ' + this.lastSyncFormatted : 'Open the Jobs panel to start a sync.';
        },

        activityRunCounts() {
            const steps = this.activitySteps();
            const complete = steps.filter(step => step.state === 'complete').length;
            const skipped = steps.filter(step => step.state === 'skipped').length;
            const failed = steps.filter(step => step.state === 'failed').length;
            return [
                `${steps.length} stage${steps.length === 1 ? '' : 's'}`,
                complete ? `${complete} completed` : '',
                skipped ? `${skipped} skipped` : '',
                failed ? `${failed} ${this.displayActivity?.stopped ? 'canceled' : 'failed'}` : '',
            ].filter(Boolean).join(' · ');
        },

        activitySummaryMetrics() {
            const steps = this.activitySteps();
            const cards = [];
            const stepFor = key => steps.find(step => step.key === key || step.key.startsWith(`${key}:`));
            const metric = (step, key) => {
                if (!step?.metrics || !Object.prototype.hasOwnProperty.call(step.metrics, key)) {
                    return null;
                }
                const value = Number(step.metrics[key]);
                return Number.isFinite(value) ? Math.max(value, 0) : null;
            };
            const count = value => Math.round(value).toLocaleString();
            const add = (key, label, value, subtitle = '') => {
                const wide = String(label).length > 20
                    || String(value).length > 12
                    || String(subtitle).length > 25;
                cards.push({ key, label, value, subtitle, wide });
            };
            const unavailableSubtitle = step => {
                const state = this.activitySegmentState(step);
                if (state === 'skipped') return 'Stage did not run';
                if (state === 'failed' || state === 'cancelled') return 'Not reported before stop';
                return this.activity ? 'Available after this stage' : 'Not reported';
            };
            const sumMetrics = (matchingSteps, key) => {
                const values = matchingSteps
                    .map(step => metric(step, key))
                    .filter(value => value !== null);
                return values.length ? values.reduce((total, value) => total + value, 0) : null;
            };
            const syncCard = (key, label) => {
                const step = stepFor(key);
                if (!step) return;
                const newTweets = metric(step, 'new_tweets');
                const tweetsSeen = metric(step, 'tweets_seen');
                const pages = metric(step, 'pages');
                const subtitle = newTweets === null
                    ? unavailableSubtitle(step)
                    : [
                        tweetsSeen === null ? '' : `${count(tweetsSeen)} checked`,
                        pages === null ? '' : `${count(pages)} page${pages === 1 ? '' : 's'}`,
                    ].filter(Boolean).join(' · ');
                add(key, label, newTweets === null ? '—' : count(newTweets), subtitle);
            };

            syncCard('sync:bookmarks', 'New bookmarks');
            syncCard('sync:likes', 'New liked tweets');
            syncCard('sync:tweets', 'New tweets');

            const syncSteps = steps.filter(step => step.key?.startsWith('sync:'));
            if (syncSteps.length) {
                const tweetsSeen = sumMetrics(syncSteps, 'tweets_seen');
                const pages = sumMetrics(syncSteps, 'pages');
                add(
                    'tweets-checked',
                    'Tweets checked',
                    tweetsSeen === null ? '—' : count(tweetsSeen),
                    tweetsSeen === null ? 'Available after stages' : 'All collections',
                );
                add(
                    'pages-fetched',
                    'Pages fetched',
                    pages === null ? '—' : count(pages),
                    pages === null ? 'Available after stages' : 'All collections',
                );
            }

            const media = stepFor('media');
            if (media) {
                const downloaded = metric(media, 'downloaded');
                const downloadedBytes = metric(media, 'downloaded_bytes');
                const alreadySaved = metric(media, 'already_saved');
                const failed = metric(media, 'failed');
                add(
                    'media-downloaded',
                    'Media downloaded',
                    downloaded === null ? '—' : count(downloaded),
                    downloaded === null
                        ? unavailableSubtitle(media)
                        : failed ? `${count(failed)} failed` : 'Saved locally',
                );
                add(
                    'media-size',
                    'Media size',
                    downloadedBytes === null ? '—' : this.formatActivityBytes(downloadedBytes),
                    downloadedBytes === null ? unavailableSubtitle(media) : 'Added this run',
                );
                add(
                    'media-saved',
                    'Media already saved',
                    alreadySaved === null ? '—' : count(alreadySaved),
                    alreadySaved === null ? unavailableSubtitle(media) : 'No download needed',
                );
                add(
                    'media-failed',
                    'Media failures',
                    failed === null ? '—' : count(failed),
                    failed === null ? unavailableSubtitle(media) : failed ? 'Review sync issues' : 'No media failures',
                );
            }

            const threads = stepFor('threads');
            if (threads) {
                const expanded = metric(threads, 'expanded');
                // The metric keeps its saved name so older runs' history still reads.
                const skipped = metric(threads, 'already_known');
                add(
                    'threads',
                    'Threads expanded',
                    expanded === null ? '—' : count(expanded),
                    expanded === null ? unavailableSubtitle(threads) : 'New context saved',
                );
                add(
                    'threads-skipped',
                    'Threads skipped',
                    skipped === null ? '—' : count(skipped),
                    skipped === null ? unavailableSubtitle(threads) : 'Saved earlier or repeated',
                );
                const failed = metric(threads, 'failed');
                add(
                    'threads-failed',
                    'Thread failures',
                    failed === null ? '—' : count(failed),
                    failed === null ? unavailableSubtitle(threads) : failed ? 'Review sync issues' : 'No thread failures',
                );
            }

            const resurrection = stepFor('resurrection');
            if (resurrection) {
                const restored = metric(resurrection, 'restored');
                const unavailable = metric(resurrection, 'still_unavailable');
                const retryLater = metric(resurrection, 'retry_later');
                const remainingDue = metric(resurrection, 'remaining_due');
                add(
                    'restored',
                    'Tweets resurrected',
                    restored === null ? '—' : count(restored),
                    restored === null ? unavailableSubtitle(resurrection) : 'Returned to the archive',
                );
                add(
                    'still-unavailable',
                    'Still unavailable',
                    unavailable === null ? '—' : count(unavailable),
                    unavailable === null
                        ? unavailableSubtitle(resurrection)
                        : [
                            retryLater ? `${count(retryLater)} retry later` : '',
                            remainingDue ? `${count(remainingDue)} still due` : '',
                        ].filter(Boolean).join(' · ') || 'Checked this run',
                );
                add(
                    'recovery-retries',
                    'Recovery retries',
                    retryLater === null ? '—' : count(retryLater),
                    retryLater === null ? unavailableSubtitle(resurrection) : 'Transient failures',
                );
                add(
                    'recovery-queue',
                    'Recovery queue',
                    remainingDue === null ? '—' : count(remainingDue),
                    remainingDue === null ? unavailableSubtitle(resurrection) : 'Still due',
                );
            }

            const urls = stepFor('urls');
            if (urls) {
                const updatedUrls = metric(urls, 'updated');
                const failedUrls = metric(urls, 'failed');
                add(
                    'urls',
                    'Link previews updated',
                    updatedUrls === null ? '—' : count(updatedUrls),
                    updatedUrls === null ? unavailableSubtitle(urls) : 'Metadata updated',
                );
                add(
                    'urls-failed',
                    'Preview failures',
                    failedUrls === null ? '—' : count(failedUrls),
                    failedUrls === null ? unavailableSubtitle(urls) : failedUrls ? 'Review sync issues' : 'No preview failures',
                );
            }

            const articles = stepFor('articles');
            if (articles) {
                const refreshedArticles = metric(articles, 'refreshed')
                    ?? (articles.state === 'skipped' ? 0 : null);
                const failedArticles = metric(articles, 'failed')
                    ?? (articles.state === 'skipped' ? 0 : null);
                add(
                    'articles',
                    'Articles refreshed',
                    refreshedArticles === null ? '—' : count(refreshedArticles),
                    refreshedArticles === null
                        ? unavailableSubtitle(articles)
                        : articles.state === 'skipped' ? 'No refresh needed' : 'Content updated',
                );
                add(
                    'articles-failed',
                    'Article failures',
                    failedArticles === null ? '—' : count(failedArticles),
                    failedArticles === null
                        ? unavailableSubtitle(articles)
                        : failedArticles ? 'Review sync issues' : 'No article failures',
                );
            }

            const tagging = stepFor('tagging');
            if (tagging) {
                const tagged = metric(tagging, 'tagged');
                const requests = metric(tagging, 'requests');
                add(
                    'tagging',
                    'Tweets tagged',
                    tagged === null ? '—' : count(tagged),
                    tagged === null
                        ? unavailableSubtitle(tagging)
                        : requests === null ? '' : `${count(requests)} request${requests === 1 ? '' : 's'}`,
                );
                add(
                    'tagging-requests',
                    'Tagging requests',
                    requests === null ? '—' : count(requests),
                    requests === null ? unavailableSubtitle(tagging) : 'Model requests',
                );
            }

            add(
                'duration',
                'Duration',
                this.formatActivityDuration(this.displayActivity?.elapsed_seconds),
                'Total runtime',
            );
            const issueCount = this.activityIssueCount();
            add('issues', 'Issues', count(issueCount), issueCount ? 'Review below' : 'No issues');
            return cards;
        },
    };
}
