/**
 * Motion for the phone post layer and the media viewer: spring curves fitted to X,
 * swipe-back decisions and the still snapshot shown beneath a sliding post.
 */

// Phones open a post on a layer over the feed, as X's app pushes a screen. The
// numbers are measured frame by frame from X on an iPhone: the post springs in
// from the right (critically damped, settled in about 150ms) while the view it
// covers drifts left by 30% of the post's travel under a shade reaching 40%
// black. A rightward drag pulls the post back with the finger; a release springs
// it back the same way, or carries the finger's speed off screen in about 150ms.
const DETAIL_SPRING = 51; // rad/s, opening
const DETAIL_RETURN_SPRING = 58; // rad/s, springing back from a drag
const DETAIL_LEAVE_MS = 150;
const DETAIL_PARALLAX = 0.3;
const DETAIL_SHADE = 0.4;
// X's media viewer pages and springs back with one critically damped spring that
// starts at the finger's speed (fitted to its spring-back, slow-drag finish and
// flick on an iPhone recording). A slow release pages past half the width; a
// flick at 0.3 px/ms pages in its direction.
const LIGHTBOX_SPRING = 34; // rad/s
const LIGHTBOX_FLICK = 0.3; // px/ms

// A touch becomes a back swipe once it travels 10px mostly rightward; a
// vertical or leftward start is left to scrolling. null means undecided.
function swipeBackIntent(dx, dy) {
    if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return null;
    return dx > 0 && dx > Math.abs(dy) * 1.2;
}

// A flick (px/ms) decides by its direction; a slow release by how far the post
// moved. X springs a post let go 56% of the way across back, and lets one at 63% go.
function swipeBackCompletes(offset, width, velocity) {
    if (velocity > 0.3) return true;
    if (velocity < -0.3) return false;
    return offset > width * 0.6;
}

// The viewer's offset `t` ms after release from `from` px moving at `velocity` px/ms.
function lightboxSpring(from, velocity, t) {
    const w = LIGHTBOX_SPRING / 1000;
    return (from + (velocity + w * from) * t) * Math.exp(-w * t);
}

// iOS keeps an animation's clock running while its first frames stall (as when
// the full-size picture decodes), so the motion looked finished on its first
// visible frame. Step the clock at most one display frame per frame instead.
function stepAnimationClock(animations, duration) {
    if (typeof requestAnimationFrame !== 'function' || !animations.every(animation => animation.pause && animation.finish)) return;
    animations.forEach(animation => animation.pause());
    let clock = 0, last = null;
    const step = now => {
        if (animations.some(animation => animation.playState === 'idle')) return;
        clock += last === null ? 0 : Math.min(now - last, 1000 / 60);
        last = now;
        if (clock >= duration) {
            animations.forEach(animation => animation.finish());
            return;
        }
        animations.forEach(animation => { animation.currentTime = clock; });
        requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
}

// The post's offset `t` ms into a settle toward `to`, starting from `from` at
// `velocity` px/ms, and whether it has arrived. Settling back to open is a
// critically damped spring that snaps home over the last 2.5px, as X does;
// leaving is a cubic that starts at the finger's speed and eases out exactly at
// the edge.
function detailSettle(from, to, velocity, t, leaving, width) {
    if (leaving) {
        const distance = Math.abs(to - from);
        const duration = DETAIL_LEAVE_MS * Math.min(1.3, Math.max(0.55, Math.sqrt(distance / (0.85 * width || 1))));
        const p = Math.min(1, t / duration);
        const m = Math.max(0, velocity) * duration / (distance || 1);
        const progress = (p ** 3 - 2 * p ** 2 + p) * m + (3 * p ** 2 - 2 * p ** 3);
        return { offset: from + (to - from) * progress, done: p >= 1 };
    }
    const w = (from >= width ? DETAIL_SPRING : DETAIL_RETURN_SPRING) / 1000;
    const delta = from - to;
    const offset = to + (delta + (velocity + w * delta) * t) * Math.exp(-w * t);
    return { offset, done: Math.abs(offset - to) < 2.5 };
}

// Where each piece sits with the post `offset` px from its open position.
function detailTransitionStyles(offset, width) {
    const covered = width ? 1 - offset / width : 1;
    return {
        layer: `translate3d(${offset}px, 0, 0)`,
        under: `translate3d(${-DETAIL_PARALLAX * width * covered}px, 0, 0)`,
        shade: String(DETAIL_SHADE * covered),
    };
}

// How visible the post's top bar and the bar beneath it are, measured from X:
// pulling a post back, its bar is half gone 9% of the way and gone by 32%; the
// one beneath stays hidden until 60% and is in by 80%. An opening post's bar
// shows from its first frame.
function detailBarOpacities(offset, width, opening) {
    if (opening) return { top: 1, beneath: 0 };
    const pulled = width ? offset / width : 0;
    return {
        top: Math.max(0, 1 - pulled / 0.32) ** 2,
        beneath: Math.min(1, Math.max(0, (pulled - 0.6) / 0.2)),
    };
}

// Carousels and other sideways scrollers keep their own horizontal drags.
function startsInSideScroller(target, layer) {
    for (let node = target; node && node !== layer; node = node.parentElement) {
        if (node.matches?.('input, textarea, select, .video-control-bar')) return true;
        if (node.scrollWidth > node.clientWidth + 1 && /(auto|scroll)/.test(getComputedStyle(node).overflowX)) return true;
    }
    return false;
}

// A still copy of a post view, shown beneath the next post while it slides.
// It keeps the classes and inline styles Alpine produced, but nothing Alpine,
// the thread pinning or the players would recognise, and its videos never load.
function detailSnapshot(source) {
    const copy = source.cloneNode(true);
    copy.querySelectorAll('template, source').forEach(node => node.remove());
    for (const element of [copy, ...copy.querySelectorAll('*')]) {
        for (const {name} of [...element.attributes]) {
            if (/^(x-|:|@|on|id$|data-thread-)/.test(name)) element.removeAttribute(name);
        }
        if (element.tagName === 'VIDEO') {
            element.removeAttribute('src');
            element.removeAttribute('autoplay');
            element.preload = 'none';
        }
    }
    return copy;
}
