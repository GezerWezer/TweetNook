/**
 * Notices the server raises, listed under Settings → Notifications: opening and dismissing them.
 */

function noticeMethods() {
    return {
        notices: [],

        get noticeCount() {
            return this.notices.filter(n => !n.dismissed).length;
        },

        async fetchNotices() {
            try {
                const response = await fetch('/api/notices');
                if (response.ok) this.notices = (await response.json()).notices || [];
            } catch (_) { /* Retry on the next status poll. */ }
        },

        async dismissNotice(id) {
            const response = await fetch(`/api/notices/${encodeURIComponent(id)}/dismiss`, { method: 'POST' });
            if (response.ok) this.notices = this.notices.map(n => n.id === id ? {...n, dismissed: true} : n);
        },

        async dismissAllNotices() {
            const response = await fetch('/api/notices/dismiss-all', { method: 'POST' });
            if (response.ok) this.notices = this.notices.map(n => ({...n, dismissed: true}));
        },

        async clearDismissedNotices() {
            const response = await fetch('/api/notices/dismissed', { method: 'DELETE' });
            if (response.ok) this.notices = this.notices.filter(n => !n.dismissed);
        },

        // Notices raised by a run open that run's log in Settings → Activity logs.
        openNoticeLog(notice) {
            nookSettingsNavigate(this, 'logs');
            this.nookLogOpenRunId = notice.run_id;
        },

        openNotice(notice) {
            if (notice.action === 'activity') {
                if (notice.run_id) this.openNoticeLog(notice);
                else this.openActivityDrawer();
                return;
            }
            if (notice.action !== 'schedule') {
                this.openSetup();
                return;
            }
            this.openSettings();
            nookSettingsNavigate(this, 'schedule');
        },
    };
}
