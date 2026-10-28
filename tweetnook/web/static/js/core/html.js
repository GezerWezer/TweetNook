/**
 * Auto-escaping HTML templates for markup built as strings (feed cards, quotes, media).
 *
 * html`<a href="${url}">${name}</a>` escapes every interpolated value, so archived text can't
 * add markup by being left out of an escapeHTML call. Values that are already markup pass
 * through unchanged: the result of another html`` template, or rawHTML() for trusted
 * constants such as icon SVGs. Arrays are joined, each item escaped on its own. URLs still
 * need safeURL: escaping keeps a value inside its attribute, not a javascript: URL out of href.
 */

// Same escaping as the app's escapeHTML method, which calls this; like it, empty values
// (0 included) give an empty string.
function escapeHTML(value) {
    if (!value) return '';
    return String(value).replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

class SafeHTML {
    constructor(markup) {
        this.markup = markup;
    }

    toString() {
        return this.markup;
    }
}

function rawHTML(markup) {
    return markup instanceof SafeHTML ? markup : new SafeHTML(String(markup ?? ''));
}

// null, undefined and false render nothing, so `${condition && html`...`}` works; 0 renders "0".
function htmlValue(value) {
    if (value === null || value === undefined || value === false) return '';
    if (value instanceof SafeHTML) return value.markup;
    if (Array.isArray(value)) return value.map(htmlValue).join('');
    return escapeHTML(String(value));
}

function html(strings, ...values) {
    let markup = strings[0];
    values.forEach((value, index) => { markup += htmlValue(value) + strings[index + 1]; });
    return new SafeHTML(markup);
}
