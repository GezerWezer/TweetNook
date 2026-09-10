from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "tweetnook" / "web"
NODE_TEST = ROOT / "tests" / "js" / "test_web_assets.cjs"


def test_browser_javascript_unit_suite():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the deterministic browser-asset unit harness")

    result = subprocess.run(
        [node, str(NODE_TEST)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "browser asset tests passed" in result.stdout


def test_index_loads_local_assets_in_dependency_order():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    themes = html.index('<script src="/static/js/themes.js"></script>')
    autocomplete = html.index('<script src="/static/js/autocomplete.js"></script>')
    app = html.index('<script src="/static/js/app.js"></script>')

    assert themes < autocomplete < app
    assert '<link rel="stylesheet" href="/static/css/styles.css">' in html
    assert 'x-data="tweetApp()"' in html
    assert 'x-data="searchAutocomplete()"' in html
    dropdown_start = html.index('x-ref="dropdownMenu"')
    dropdown_end = html.index('<template x-if="opt.isAuthor"', dropdown_start)
    search_dropdown = html[dropdown_start:dropdown_end]
    assert "z-50 py-2" not in search_dropdown
    assert "'px-4 py-3 cursor-pointer flex transition" not in search_dropdown
    assert 'class="transition">' not in search_dropdown
    assert 'aria-label="Scroll to top"' in html
    assert 'x-show="archiveEnrichmentIncomplete > 0' in html
    assert "Continue enrichment" in html
    assert "tweetnook import enrich" not in html
    assert ">Archive status<" in html
    assert 'x-text="statsHealth.enrichment.done.toLocaleString()"' in html
    assert 'x-text="statsHealth.enrichment.resurrected.toLocaleString()"' in html
    assert 'x-text="statsHealth.enrichment.incomplete.toLocaleString()"' in html
    assert 'x-model="showEmptyUnavailableReasons"' in html
    assert 'aria-label="Command activity"' in html
    assert '<section class="activity-drawer hidden sm:block" x-cloak' in html
    assert "requestAnimationFrame(() => $el.classList.add('activity-drawer-ready'))" in html
    assert 'class="activity-spinner activity-spinner-icon' in html
    assert 'class="activity-spinner activity-spinner-small"' in html
    assert '<span class="activity-spinner">' not in html
    assert "activity-page-state-icon" not in html
    assert "activity-page-state-badge" in html
    floating_actions = html[
        html.index("<!-- Stats Button -->") : html.index("<!-- Twitter/X-style activity drawer -->")
    ]
    assert floating_actions.count("hover:bg-[var(--bg-secondary)]") == 2
    assert "hover:bg-[var(--hover-bg)]" not in floating_actions
    assert "Scheduled Syncs" in html
    assert "Notifications" in html
    assert "Activity Logs" in html
    assert "Connect your Twitter/X account" in html
    assert (
        "Paste the two session cookies from a browser where you're signed in to Twitter/X." in html
    )
    assert "Saved Twitter/X credentials found. Test the connection to continue." in html
    assert "setupData.auth.verified" in html
    assert "https://github.com/gezerwezer/tweetnook/blob/main/docs/getting-started.md" in html
    assert "/static/help/getting-started.md" not in html
    assert 'x-model="setupAuth.browser"' not in html
    assert 'x-model="setupAuth.browser_profile"' not in html
    assert 'x-model="setupAuth.browser_profile_path"' not in html
    assert "Start local import" in html
    assert "activity-stage-progress" in html
    assert "activity-stage-page" in html
    assert "activity-summary-page" in html
    assert "activity-page-nav" in html
    assert "activity-completed-stack" not in html
    assert "activityShowCompleted" not in html
    assert "activityShowPending" not in html
    assert "activityPendingSteps" not in html
    assert "Show ' +" not in html
    assert 'x-text="activityCommandLabel()">Archive Sync</span>' in html
    assert "margin-bottom: 1rem" not in html
    assert "activity-sync-schedule-primary" in html
    assert 'x-show="!(activity || activityStartPending)" class="activity-sync-schedule"' in html
    assert "activity-sync-action is-primary" in html
    assert "activity-sync-action is-danger" in html
    assert 'x-text="activityScheduleDayText()"' in html
    assert 'x-text="activityScheduleTimeText()"' in html
    assert 'x-text="statsHealth.enrichment.available.toLocaleString()"' not in html

    app_js = (WEB_DIR / "static" / "js" / "app.js").read_text(encoding="utf-8")
    styles = (WEB_DIR / "static" / "css" / "styles.css").read_text(encoding="utf-8")
    assert "activityScheduleDayText()" in app_js
    assert "activityScheduleTimeText()" in app_js
    assert "fetchArchiveEnrichmentStatus" in app_js
    assert "/api/stats/enrichment-incomplete" in app_js
    assert "d.incomplete" in app_js
    assert "/api/activity/status" in app_js
    assert "`/api/activity/${kind}`" in app_js
    assert "/api/activity/stop" in app_js
    assert "/api/setup/archive/import" in app_js
    assert "/api/setup/auth/test" not in app_js
    assert "noticesOpen" not in app_js
    assert "notice-control" not in html
    assert "notice-inbox" not in html
    assert "unread notifications" in html
    assert "setActivityScrollPosition" not in app_js
    assert "reverseActivityScroll" not in app_js
    assert "activityCompletedStackHeight" not in app_js
    assert ".activity-step-viewport" not in styles
    assert ".activity-completed-card" not in styles
    assert "activityShowPending" not in app_js
    assert "activityPendingSteps" not in app_js
    assert "formatActivityStatusTitle" not in app_js
    assert "activityCommandLabel" in app_js
    assert "var(--bg-primary) 97%" in styles
    assert ".activity-sync-action:focus-visible" in styles
    assert ".activity-drawer.activity-drawer-ready" in styles
    assert (
        ".activity-drawer-panel .activity-drawer-tab:hover { background: var(--bg-secondary); }"
        in styles
    )
    assert "min-height: 40px;" in styles
    assert "activity-drawer-controls-has-stack" not in html
    assert "activity-drawer-controls-divider" not in html
    assert "activity-page-issues" in html
    assert "activity-issues-count" in html
    assert "No issues reported" not in html
    assert "activityIssueCount() ? 'Issues' : 'No issues'" in html
    assert "activity-drawer-bar-segments" in html
    assert "activitySegmentPercent(page)" in html
    assert "activity-drawer-bar-heading" in styles
    assert ".activity-drawer-bar-segments" in styles
    assert ".activity-drawer-bar-segment.is-current" in styles
    collapsed_current = styles.split(".activity-drawer-bar-segment.is-current {", 1)[1].split(
        "}", 1
    )[0]
    assert "flex-grow: 3;" in collapsed_current
    assert ".activity-stage-progress-track" in styles
    assert ".activity-stage-progress-fill" in styles
    assert (
        ".activity-stage-progress-segment:is(:hover, :focus-visible) "
        ".activity-stage-progress-track" in styles
    )
    hovered_track = styles.split(
        ".activity-stage-progress-segment:is(:hover, :focus-visible) "
        ".activity-stage-progress-track {",
        1,
    )[1].split("}", 1)[0]
    assert "transform: scaleY(1.65);" in hovered_track
    progress_bar = styles.split(".activity-stage-progress {", 1)[1].split("}", 1)[0]
    assert "height: 26px;" in progress_bar
    assert "padding: 6px 16px 0;" in progress_bar
    assert ".activity-stage-progress-segment.is-active .activity-stage-progress-track" in styles
    assert ".activity-stage-progress-segment.is-cancelled .activity-stage-progress-track" in styles
    assert ".activity-stage-progress-segment.is-cancelled .activity-stage-progress-fill" in styles
    cancelled_track = styles.split(
        ".activity-stage-progress-segment.is-cancelled .activity-stage-progress-track,", 1
    )[1].split("}", 1)[0]
    assert "background: var(--danger-color);" in cancelled_track
    assert "min-width: 3px;" not in styles
    assert ".activity-stage-progress-segment.is-selected" in styles
    assert ".activity-stage-progress-segment.is-selected::after" not in styles
    selected_segment = styles.split(".activity-stage-progress-segment.is-selected {", 1)[1].split(
        "}", 1
    )[0]
    assert "flex-grow: var(--activity-selected-grow, 2);" in selected_segment
    assert "transform" not in selected_segment
    assert "box-shadow" not in selected_segment
    base_segment = styles.split(".activity-stage-progress-segment {", 1)[1].split("}", 1)[0]
    assert "flex: 1 1 0;" in base_segment
    assert "transition: flex-grow 220ms ease-in-out;" in base_segment
    assert "activitySelectedSegmentGrow()" in html
    assert "activitySegmentPercent(page)" in html
    assert "activitySegmentState(page)" in html
    progress_nav = html.split('class="activity-stage-progress"', 1)[1].split(">", 1)[0]
    assert "grid-template-columns" not in progress_nav
    assert ':style="`--activity-selected-grow:' not in progress_nav
    assert (
        'class="activity-drawer-content"\n                     :style="`--activity-selected-grow:'
        in html
    )
    assert ".activity-page-layout" in styles
    assert ".activity-page-hero" in styles
    hero_card = styles.split(".activity-page-hero {", 1)[1].split("}", 1)[0]
    assert "background: var(--bg-secondary);" in hero_card
    assert "linear-gradient" not in hero_card
    assert ".activity-page-hero::before" not in styles
    assert ".activity-page-state-badge::before" in styles
    assert ".activity-stage-hero > .activity-page-state-badge" in styles
    assert ".activity-stage-hero > .activity-page-title" in styles
    assert ".activity-summary-hero > .activity-page-state-badge" in styles
    assert ".activity-summary-hero > .activity-page-title" in styles
    summary_title = styles.rsplit(".activity-summary-hero > .activity-page-title {", 1)[1].split(
        "}", 1
    )[0]
    assert "font-size: 22px;" in summary_title
    assert "white-space: nowrap;" in summary_title
    assert ".activity-page-progress-card" in styles
    assert ".activity-page-detail-grid" in styles
    summary_grid = styles.split(".activity-summary-metrics {", 1)[1].split("}", 1)[0]
    assert "grid-auto-flow: dense;" in summary_grid
    assert ".activity-page-result-strip.is-empty" not in styles
    assert ".activity-page-progress-eta.is-empty" not in styles
    assert ".activity-stage-page .activity-page-status" in styles
    result_strip = styles.split(".activity-page-result-strip {", 1)[1].split("}", 1)[0]
    assert "height: calc(1.35em + 14px);" in result_strip
    assert "white-space: nowrap;" in result_strip
    assert ".activity-page-state-icon" not in styles
    assert "activityPageStateLabel(page)" in html
    assert "activityRunStateLabel()" in html
    assert ".activity-stage-page.setup-flow-enter" in styles
    assert "transition-duration: 500ms;" in styles
    assert ".activity-summary-metrics" in styles
    assert "activity-summary-metric" in html
    assert ":class=\"{ 'is-wide': metric.wide }\"" in html
    summary_metric = styles.split(".activity-summary-metric.stats-card {", 1)[1].split("}", 1)[0]
    assert "height: 112px;" in summary_metric
    assert ".activity-summary-metric.is-wide" in styles
    assert "activitySummaryMetrics()" in html
    assert "Stage outcomes" not in html
    assert ".activity-summary-stage" not in styles
    assert ".activity-page-nav" in styles
    assert "activityPages" in app_js
    assert "activitySelectedPage" in app_js
    assert "moveActivityPage" in app_js
    assert "openActivityDrawer" in app_js
    assert "activityPageDirection" in app_js
    activity_pages = html.split('class="activity-page-scroll"', 1)[1].split(
        'class="activity-page-nav"', 1
    )[0]
    assert "activityPageUpdateText(page)" in activity_pages
    assert "activityPageTimingText(page)" in activity_pages
    assert 'x-show="page.elapsed_seconds"' not in activity_pages
    assert 'x-for="page in activityPages()"' in activity_pages
    assert "Stage ${pageIndex + 1}" not in activity_pages
    assert "activity-page-hero activity-stage-hero" in activity_pages
    assert "activity-page-meta-row is-status-only" not in activity_pages
    assert "activity-page-eyebrow" not in activity_pages
    assert 'class="setup-flow-panel"' in activity_pages
    assert ':class="[page.is_summary ?' in activity_pages
    assert 'x-transition:enter="setup-flow-enter"' in activity_pages
    assert 'x-transition:enter-start="setup-flow-enter-start"' in activity_pages
    assert 'x-transition:enter-end="setup-flow-enter-end"' in activity_pages
    assert "bottom: 80px;" in styles
    assert "height: 3px;" in styles
    assert "margin-inline: 4px;" not in styles
    assert "border-inline: 1px solid var(--border-color);" not in styles
    assert 'aria-label="Pipeline stages"' in html
    assert 'aria-label="Previous pipeline page"' in html
    assert 'aria-label="Next pipeline page"' in html
    assert 'd="m12.5 15-5-5 5-5"' in html
    assert 'd="m7.5 5 5 5-5 5"' in html
    assert ".activity-page-nav button svg" in styles


@pytest.mark.parametrize(
    "relative_path",
    [
        "static/js/themes.js",
        "static/js/autocomplete.js",
        "static/js/app.js",
        "static/css/styles.css",
    ],
)
def test_index_referenced_assets_are_packaged(relative_path: str):
    asset = WEB_DIR / relative_path
    assert asset.is_file()
    assert asset.stat().st_size > 0
