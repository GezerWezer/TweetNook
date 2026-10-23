/**
 * Search autocomplete component for the TweetNook Web UI.
 */

// Suggestions are requested once typing pauses this long.
const SUGGESTION_DELAY_MS = 100;

// Filters whose value is edited inside its capsule, so spaces stay part of the
// value without typing quotes.
const BOX_FILTER_KEYS = ['description', 'tag'];

// Private-use markers for the caret and capsule edges while the editor is
// read back and re-rendered.
const SEARCH_CARET = '\uE000';
const BOX_START = '\uE001';
const BOX_END = '\uE002';

// Suggestion glyphs: X design system icons (iconXDS.md), plus the app's own
// verified seal, which XDS lacks. Values are [viewBox, inner SVG markup].
const SUGGESTION_GLYPHS = {
    "community-notes-outline": ["0 0 24 24", "<path d=\"M19 16h2.6c-.23-1.2-.66-2.12-1.2-2.77-.67-.8-1.55-1.23-2.65-1.23-.51 0-.96.09-1.36.26l-.78-1.84c.67-.28 1.38-.42 2.14-.42 1.7 0 3.14.7 4.19 1.95 1.03 1.24 1.63 2.95 1.81 4.96l.09 1.09H19v-2zM5 16H2.4c.23-1.2.66-2.12 1.2-2.77.67-.8 1.55-1.23 2.65-1.23.51 0 .96.09 1.36.26l.78-1.84c-.67-.28-1.38-.42-2.14-.42-1.7 0-3.14.7-4.19 1.95C1.032 13.19.433 14.9.254 16.91L.157 18H5v-2zM15.5 6c0-1.66 1.34-3 3-3s3 1.34 3 3-1.34 3-3 3-3-1.34-3-3zm2 0c0 .55.45 1 1 1s1-.45 1-1-.45-1-1-1-1 .45-1 1zm-12-3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 2c-.55 0-1 .45-1 1s.45 1 1 1 1-.45 1-1-.45-1-1-1zm6.5 6.5c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm0-2c.55 0 1-.45 1-1s-.45-1-1-1-1 .45-1 1 .45 1 1 1zm0 3c1.78 0 3.29.75 4.37 2.1 1.07 1.32 1.69 3.15 1.88 5.31l.09 1.09H5.66l.09-1.09c.19-2.16.81-3.99 1.88-5.31 1.08-1.35 2.59-2.1 4.37-2.1zm-2.82 3.35c-.59.74-1.05 1.79-1.29 3.15h8.22c-.24-1.36-.7-2.41-1.29-3.15-.72-.88-1.66-1.35-2.82-1.35s-2.1.47-2.82 1.35z\" />"],
    "square-dashed": ["0 0 24 24", "<path d=\"M14.42 21.5H9.58v-2h4.84zm-9.84-4.58c.05.44.13.72.25.94a3 3 0 0 0 1.3 1.31c.23.12.5.2.95.25v2.01a5 5 0 0 1-1.85-.47 5 5 0 0 1-2.19-2.19 5 5 0 0 1-.47-1.85zm16.85 0a5 5 0 0 1-.47 1.85 5 5 0 0 1-2.19 2.19c-.56.28-1.17.4-1.85.47v-2.01c.44-.05.72-.13.94-.25a3 3 0 0 0 1.31-1.3c.12-.23.2-.5.25-.95zM4.5 9.9v4.52h-2V9.58h2zm17 0v4.52h-2V9.58h2zM7.08 4.58a3 3 0 0 0-.94.25 3 3 0 0 0-1.31 1.3c-.12.23-.2.5-.25.95H2.57q.07-1.01.47-1.85a5 5 0 0 1 2.19-2.19 5 5 0 0 1 1.85-.47zm9.84-2.01q1.02.07 1.85.47a5 5 0 0 1 2.19 2.19c.28.56.4 1.17.47 1.85h-2.01a3 3 0 0 0-.25-.94 3 3 0 0 0-1.3-1.31c-.23-.12-.5-.2-.95-.25zm-2.5-.07v2H9.58v-2z\"/>"],
    "person": ["0 0 24 24", "<path d=\"M12 12.25a8 8 0 0 1 8 8V22H4v-1.75a8 8 0 0 1 8-8m0 2A6 6 0 0 0 6 20h12a6 6 0 0 0-6-5.75M12 2a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9m0 2a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5\"/>"],
    "reply": ["0 0 24 24", "<path d=\"M1.75 10a8 8 0 0 1 8-8h4.37a8.13 8.13 0 0 1 3.93 15.24L10 21.7V18h-.07a8 8 0 0 1-8.18-8m2 0a6 6 0 0 0 6.14 6H12v2.3l5.09-2.8A6.13 6.13 0 0 0 14.12 4H9.75a6 6 0 0 0-6 6\"/>"],
    "attachment": ["0 0 24 24", "<path d=\"M5 15V8h2v7a5 5 0 0 0 10 0V7a3 3 0 1 0-6 0v8a1 1 0 1 0 2 0V8h2v7a3 3 0 1 1-6 0V7a5 5 0 0 1 10 0v8a7 7 0 1 1-14 0\"/>"],
    "timeline": ["0 0 24 24", "<path d=\"M15 17h-4v-2h4zm-7.5-6.25a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3M18 13h-7v-2h7z\"/><path d=\"M18.5 7a3.5 3.5 0 0 1 3.5 3.5v7a3.5 3.5 0 0 1-3.5 3.5h-13A3.5 3.5 0 0 1 2 17.5v-7A3.5 3.5 0 0 1 5.5 7zm-13 2C4.67 9 4 9.67 4 10.5v7c0 .83.67 1.5 1.5 1.5h13c.83 0 1.5-.67 1.5-1.5v-7c0-.83-.67-1.5-1.5-1.5z\"/><path d=\"M17 3a2 2 0 0 1 2 2H5c0-1.1.9-2 2-2z\"/>"],
    "calendar": ["0 0 24 24", "<path d=\"M8 3.5h8V2h2v1.5h3.5V16a5 5 0 0 1-5 5h-9a5 5 0 0 1-5-4.74V3.5H6V2h2zM4.5 16v.15a3 3 0 0 0 3 2.85h9a3 3 0 0 0 3-3v-6h-15zm0-8h15V5.5h-15z\"/>"],
    "link": ["0 0 24 24", "<path d=\"M1 12a7 7 0 0 1 7-7h2v2H8a5 5 0 1 0 0 10h2v2H8a7 7 0 0 1-7-7m20 0a5 5 0 0 0-5-5h-2V5h2a7 7 0 1 1 0 14h-2v-2h2a5 5 0 0 0 5-5m-4-1v2H7v-2z\"/>"],
    "sticker": ["0 0 24 24", "<path d=\"M12 2.5q.57 0 1.12.07l.34.04.25.24 7.44 7.44.24.25.04.34q.07.55.07 1.12A9.5 9.5 0 1 1 12 2.5m0 2a7.5 7.5 0 1 0 7.5 7.5A8.44 8.44 0 0 1 12 4.5\"/>"],
    "edit": ["0 0 24 24", "<path d=\"M13.54 4.04a4.54 4.54 0 0 1 6.42 6.42l-8.73 8.72a5 5 0 0 1-3.08 1.45l-5.25.47.47-5.25a5 5 0 0 1 1.45-3.08zm-7.3 10.14a3 3 0 0 0-.87 1.85L5.1 18.9l2.87-.26a3 3 0 0 0 1.85-.86L16.59 11 13 7.4zm12.3-8.72a2.54 2.54 0 0 0-3.58 0L14.4 6 18 9.58l.54-.54c1-.99 1-2.6 0-3.58\"/>"],
    "grid": ["0 0 24 24", "<path d=\"M3 5.5A2.5 2.5 0 0 1 5.5 3h3A2.5 2.5 0 0 1 11 5.5v3A2.5 2.5 0 0 1 8.5 11h-3A2.5 2.5 0 0 1 3 8.5zM5.5 5a.5.5 0 0 0-.5.5v3a.5.5 0 0 0 .5.5h3a.5.5 0 0 0 .5-.5v-3a.5.5 0 0 0-.5-.5zm7.5.5A2.5 2.5 0 0 1 15.5 3h3A2.5 2.5 0 0 1 21 5.5v3a2.5 2.5 0 0 1-2.5 2.5h-3A2.5 2.5 0 0 1 13 8.5zm2.5-.5a.5.5 0 0 0-.5.5v3a.5.5 0 0 0 .5.5h3a.5.5 0 0 0 .5-.5v-3a.5.5 0 0 0-.5-.5zM3 15.5A2.5 2.5 0 0 1 5.5 13h3a2.5 2.5 0 0 1 2.5 2.5v3A2.5 2.5 0 0 1 8.5 21h-3A2.5 2.5 0 0 1 3 18.5zm2.5-.5a.5.5 0 0 0-.5.5v3a.5.5 0 0 0 .5.5h3a.5.5 0 0 0 .5-.5v-3a.5.5 0 0 0-.5-.5zm7.5.5a2.5 2.5 0 0 1 2.5-2.5h3a2.5 2.5 0 0 1 2.5 2.5v3a2.5 2.5 0 0 1-2.5 2.5h-3a2.5 2.5 0 0 1-2.5-2.5zm2.5-.5a.5.5 0 0 0-.5.5v3a.5.5 0 0 0 .5.5h3a.5.5 0 0 0 .5-.5v-3a.5.5 0 0 0-.5-.5z\"/>"],
    "photo": ["0 0 24 24", "<path d=\"M15 7a2 2 0 1 1 0 4 2 2 0 0 1 0-4\"/><path d=\"M14.1 2.5q1.65-.02 2.7.06c.74.06 1.38.18 1.97.48a5 5 0 0 1 2.19 2.19c.3.6.42 1.23.48 1.96q.08 1.06.06 2.71v4.2q.02 1.65-.06 2.7a5 5 0 0 1-.48 1.97 5 5 0 0 1-2.19 2.19c-.6.3-1.23.42-1.96.48q-1.06.08-2.71.06H9.9q-1.65.02-2.7-.06a5 5 0 0 1-1.97-.48 5 5 0 0 1-2.19-2.19c-.3-.6-.42-1.23-.48-1.96q-.07-1.06-.06-2.71V9.9q-.02-1.65.06-2.7c.06-.74.18-1.38.48-1.97a5 5 0 0 1 2.19-2.19c.6-.3 1.23-.42 1.96-.48q1.06-.07 2.71-.06zM4.5 15.44q.01.7.05 1.2c.05.61.14.96.28 1.22a3 3 0 0 0 1.3 1.31c.27.14.62.23 1.22.28.62.05 1.41.05 2.55.05h5.18l-7.12-7.13zM9.9 4.5c-1.14 0-1.93 0-2.55.05-.6.05-.95.14-1.21.28a3 3 0 0 0-1.31 1.3 3 3 0 0 0-.28 1.22c-.05.62-.05 1.41-.05 2.55v2.87l3.54-3.14 9.63 9.63.2-.09a3 3 0 0 0 1.3-1.3c.14-.27.23-.62.28-1.22.05-.62.05-1.41.05-2.55V9.9c0-1.14 0-1.93-.05-2.55a3 3 0 0 0-.28-1.21 3 3 0 0 0-1.3-1.31 3 3 0 0 0-1.22-.28c-.62-.05-1.41-.05-2.55-.05z\"/>"],
    "play-circle": ["0 0 24 24", "<path d=\"m16.5 12-7 4.45v-8.9z\"/><path d=\"M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20m0 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16\"/>"],
    "news": ["0 0 24 24", "<path d=\"M18 17H6v-2h12z\"/><path d=\"M12 13H6V7h6zm-4-2h2V9H8z\"/><path d=\"M18 13h-4v-2h4zm0-4h-4V7h4z\"/><path d=\"M17 3q1.02-.01 1.72.04c.48.04.94.12 1.37.34.66.34 1.2.87 1.53 1.53.22.43.3.89.34 1.37q.05.7.04 1.72v8q.01 1.02-.04 1.72a4 4 0 0 1-.34 1.37 3.5 3.5 0 0 1-1.53 1.53c-.43.22-.89.3-1.37.34q-.7.05-1.72.04H7q-1.03.01-1.72-.04a4 4 0 0 1-1.37-.34 3.5 3.5 0 0 1-1.53-1.53 4 4 0 0 1-.34-1.37Q1.99 17.02 2 16V8q-.01-1.03.04-1.72c.04-.48.12-.94.34-1.37a3.5 3.5 0 0 1 1.53-1.53c.43-.22.89-.3 1.37-.34Q5.98 2.99 7 3zM7 5c-.72 0-1.2 0-1.56.03s-.52.08-.62.13a1.5 1.5 0 0 0-.66.66c-.05.1-.1.26-.13.62S4 7.28 4 8v8c0 .72 0 1.2.03 1.56s.08.52.13.62q.23.43.66.66c.1.05.26.1.62.13.37.03.84.03 1.56.03h10c.72 0 1.2 0 1.56-.03s.52-.08.62-.13q.43-.23.66-.66c.05-.1.1-.26.13-.62.03-.37.03-.84.03-1.56V8c0-.72 0-1.2-.03-1.56a2 2 0 0 0-.13-.62 1.5 1.5 0 0 0-.66-.66c-.1-.05-.26-.1-.62-.13S17.72 5 17 5z\"/>"],
    "quotes": ["0 0 24 24", "<path d=\"M6.8 5.01a4.6 4.6 0 0 1 3.87 2.37c.78 1.37.96 3.11.75 4.75a10 10 0 0 1-1.82 4.6A5.3 5.3 0 0 1 5.45 19h-1v-4.58C2.9 13.62 1.5 12 1.5 10v-.25A4.96 4.96 0 0 1 6.43 5zm-.52 2a2.97 2.97 0 0 0-2.78 3c0 1.18 1.03 2.36 2.29 2.82l.66.23v3.74q.86-.36 1.56-1.28a8 8 0 0 0 1.42-3.65c.18-1.36 0-2.62-.5-3.5C8.47 7.55 7.7 7 6.43 7zm11.52-2a4.6 4.6 0 0 1 3.87 2.37c.78 1.37.96 3.11.75 4.75a10 10 0 0 1-1.82 4.6A5.3 5.3 0 0 1 16.45 19h-1v-4.58C13.9 13.62 12.5 12 12.5 10v-.25A4.96 4.96 0 0 1 17.43 5zm-.52 2a2.97 2.97 0 0 0-2.78 3c0 1.18 1.03 2.36 2.29 2.82l.66.23v3.74q.86-.36 1.56-1.28a8 8 0 0 0 1.42-3.65c.18-1.36 0-2.62-.5-3.5C19.47 7.55 18.7 7 17.43 7z\"/>"],
    "repost": ["0 0 24 24", "<path d=\"M13.1 4q1.65-.01 2.71.06c.73.06 1.37.18 1.96.48a5 5 0 0 1 2.19 2.19 5 5 0 0 1 .48 1.96q.08 1.06.06 2.71v5.05l2.07-1.93 1.36 1.46-4.43 4.14-4.43-4.14 1.36-1.46 2.07 1.93V11.4c0-1.14 0-1.93-.05-2.55a3 3 0 0 0-.27-1.21 3 3 0 0 0-1.32-1.31 3 3 0 0 0-1.21-.28C15.03 6 14.24 6 13.1 6H11V4zM8.93 8.02 7.57 9.48 5.5 7.55v5.05c0 1.14 0 1.93.05 2.55.05.6.15.95.28 1.21a3 3 0 0 0 1.31 1.31c.26.14.61.23 1.22.28s1.4.05 2.54.05H13v2h-2.1q-1.64.02-2.7-.06a5 5 0 0 1-1.97-.48 5 5 0 0 1-2.18-2.19 5 5 0 0 1-.49-1.96q-.08-1.06-.06-2.7V7.54L1.43 9.48.07 8.02 4.5 3.88z\"/>"],
    "bulleted-list": ["0 0 24 24", "<path d=\"M5.5 13.5C3.6 13.5 2 15.1 2 17s1.6 3.5 3.5 3.5S9 18.9 9 17s-1.6-3.5-3.5-3.5m0 5.2c-1 0-1.8-.8-1.8-1.8s.8-1.8 1.8-1.8 1.8.8 1.8 1.8-.8 1.8-1.8 1.8M22 8H12V6h10zM5.5 3.5C3.6 3.5 2 5.1 2 7s1.6 3.5 3.5 3.5S9 8.9 9 7 7.4 3.5 5.5 3.5m0 5.2c-1 0-1.8-.8-1.8-1.8s.8-1.8 1.8-1.8 1.8.8 1.8 1.8-.8 1.8-1.8 1.8M22 18H12v-2h10z\"/>"],
    "verified": ["0 0 22 22", "<path d=\"M20.396 11c-.018-.646-.215-1.275-.57-1.816-.354-.54-.852-.972-1.438-1.246.223-.607.27-1.264.14-1.897-.131-.634-.437-1.218-.882-1.687-.47-.445-1.053-.75-1.687-.882-.633-.13-1.29-.083-1.897.14-.273-.587-.704-1.086-1.245-1.44S11.647 1.62 11 1.604c-.646.017-1.273.213-1.813.568s-.969.854-1.24 1.44c-.608-.223-1.267-.272-1.902-.14-.635.13-1.22.436-1.69.882-.445.47-.749 1.055-.878 1.688-.13.633-.08 1.29.144 1.896-.587.274-1.087.705-1.443 1.245-.356.54-.555 1.17-.574 1.817.02.647.218 1.276.574 1.817.356.54.856.972 1.443 1.245-.224.606-.274 1.263-.144 1.896.13.634.433 1.218.877 1.688.47.443 1.054.747 1.687.878.633.132 1.29.084 1.897-.136.274.586.705 1.084 1.246 1.439.54.354 1.17.551 1.816.569.647-.016 1.276-.213 1.817-.567s.972-.854 1.245-1.44c.604.239 1.266.296 1.903.164.636-.132 1.22-.447 1.68-.907.46-.46.776-1.044.908-1.681s.075-1.299-.165-1.903c.586-.274 1.084-.705 1.439-1.246.354-.54.551-1.17.569-1.816zM9.662 14.85l-3.429-3.428 1.293-1.302 2.072 2.072 4.4-4.794 1.347 1.246z\" />"],
    "warning": ["0 0 24 24", "<path d=\"M12 15.2a1.2 1.2 0 1 1 0 2.4 1.2 1.2 0 0 1 0-2.4m0-6.45c.8 0 1.4.7 1.29 1.48L12.75 14h-1.5l-.54-3.77A1.3 1.3 0 0 1 12 8.75\"/><path d=\"M9.4 3.5a3 3 0 0 1 5.2 0L22.4 17a3 3 0 0 1-2.6 4.5H4.2A3 3 0 0 1 1.6 17zm3.47 1a1 1 0 0 0-1.74 0L3.34 18a1 1 0 0 0 .87 1.5h15.58a1 1 0 0 0 .87-1.5z\"/>"],
    "history": ["0 0 24 24", "<path d=\"M12 2.5a9.5 9.5 0 1 1-9.43 10.62l1.98-.24A7.5 7.5 0 1 0 6.41 7H9.5v2h-6V3h2v2.07A9.5 9.5 0 0 1 12 2.5\"/><path d=\"m13 11.59 3.2 3.2-1.4 1.42-3.8-3.8V8h2z\"/>"],
    "eye-off": ["0 0 24 24", "<path fill-rule=\"evenodd\" d=\"M20.4 16.63c1.14-1.4 2-2.96 2.67-4.63l-.02-.06q-.91-2.34-2.44-4.3l-1.43 1.42A16 16 0 0 1 20.91 12q-.12.3-.37.79c-.37.7-.93 1.64-1.7 2.58C17.33 17.25 15.09 19 12 19q-1.29-.01-2.38-.37l-1.55 1.54A10 10 0 0 0 12 21c3.92 0 6.68-2.25 8.4-4.37M2.7 22.7l20-20-1.4-1.4-3.57 3.55A10 10 0 0 0 12 3C8.08 3 5.32 5.25 3.6 7.37A17 17 0 0 0 .93 12a17 17 0 0 0 3.8 5.86L1.28 21.3zM12 5C8.92 5 6.68 6.75 5.15 8.63A16 16 0 0 0 3.08 12l.38.79a16 16 0 0 0 2.68 3.66l2.41-2.42a4 4 0 0 1 5.48-5.48L16.3 6.3A8 8 0 0 0 12 5m0 5a2 2 0 0 0-1.93 2.52l2.45-2.45A2 2 0 0 0 12 10\"/>"],
};

function searchAutocomplete() {
    return {
        suggestionRequest: 0,
        suggestionTimer: null,
        showDropdown: false,
        selectedIndex: 0,
        options: [],
        // Shown in place of suggestions when a people or tag lookup finds nothing.
        emptyNote: null,
        cursorPos: 0,
        isComposing: false,
        dpYear: new Date().getFullYear(),
        dpMonth: new Date().getMonth(),
        dpHoverDate: null,
        
        init() {
            this.$watch('searchQuery', (val) => {
                const input = this.$refs.searchInput;
                if (input && !this.isComposing && this.editorSearchQuery(input) !== (val || '')) {
                    this.formatRichText(val || '');
                }
            });
            this.$nextTick(() => {
                if (this.$refs.searchInput) {
                    this.formatRichText(this.searchQuery || '');
                }
            });
            // Arrow keys and clicks move the caret without input events.
            document.addEventListener('selectionchange', () => {
                this.syncEditingState();
                this.syncDatePickerToCaret();
            });
        },
        
        allFilters: [
            // excludeDesc describes the negated filter; -since: keeps tweets before
            // the date and -until: keeps those on or after it (search.py).
            { prefix: 'from:', glyph: 'person', desc: 'Sent from a specific user', excludeDesc: 'Hide tweets from a specific user' },
            { prefix: 'to:', glyph: 'reply', desc: 'Replying to a specific user', excludeDesc: 'Hide replies to a specific user' },
            { prefix: 'has:', glyph: 'attachment', desc: 'Includes specific attached content', excludeDesc: 'Hide tweets with specific attached content' },
            { prefix: 'is:', glyph: 'timeline', desc: 'Matches a tweet type or state', excludeDesc: 'Hide a tweet type or state' },
            { prefix: 'since:', glyph: 'calendar', desc: 'After a specific date (YYYY-MM-DD)', excludeDesc: 'Hide tweets on or after a date (YYYY-MM-DD)' },
            { prefix: 'until:', glyph: 'calendar', desc: 'Before a specific date (YYYY-MM-DD)', excludeDesc: 'Hide tweets before a date (YYYY-MM-DD)' },
            { prefix: 'url:', glyph: 'link', desc: 'Contains a specific URL', excludeDesc: 'Hide tweets containing a specific URL' },
            { prefix: 'tag:', glyph: 'sticker', desc: 'Matches media tag', excludeDesc: 'Hide tweets with a media tag' },
            { prefix: 'description:', glyph: 'edit', desc: 'Words in a tag description', excludeDesc: 'Hide tweets with these words in a tag description' }
        ],
        
        hasOptions: [
            { prefix: 'has:', value: 'media', glyph: 'grid', desc: 'Images, videos, or GIFs', excludeDesc: 'Hide tweets with images, videos, or GIFs' },
            { prefix: 'has:', value: 'image', glyph: 'photo', desc: 'Only images', excludeDesc: 'Hide tweets with images' },
            { prefix: 'has:', value: 'video', glyph: 'play-circle', desc: 'Videos or GIFs', excludeDesc: 'Hide tweets with videos or GIFs' },
            { prefix: 'has:', value: 'links', glyph: 'link', desc: 'External links', excludeDesc: 'Hide tweets with external links' },
            { prefix: 'has:', value: 'article', glyph: 'news', desc: 'Attached archived article', excludeDesc: 'Hide tweets with an archived article' },
            { prefix: 'has:', value: 'note', glyph: 'community-notes-outline', desc: 'Current or past Community Notes, including quoted posts', excludeDesc: 'Hide tweets with current or past Community Notes, including quoted posts' }
        ],
        
        isOptions: [
            { prefix: 'is:', value: 'reply', glyph: 'reply', desc: 'Replies to other tweets', excludeDesc: 'Hide replies to other tweets' },
            { prefix: 'is:', value: 'quote', glyph: 'quotes', desc: 'Quote tweets', excludeDesc: 'Hide quote tweets' },
            { prefix: 'is:', value: 'retweet', glyph: 'repost', desc: 'Native reposts', excludeDesc: 'Hide native reposts' },
            { prefix: 'is:', value: 'thread', glyph: 'bulleted-list', desc: 'Self-reply threads', excludeDesc: 'Hide self-reply threads' },
            { prefix: 'is:', value: 'verified', glyph: 'verified', desc: 'From verified users', excludeDesc: 'Hide tweets from verified users' },
            { prefix: 'is:', value: 'resurrected', glyph: 'history', desc: 'Previously unavailable and recovered', excludeDesc: 'Hide recovered tweets' },
            { prefix: 'is:', value: 'missing', glyph: 'warning', desc: 'Archived but no longer on X', excludeDesc: 'Hide tweets no longer on X' },
            { prefix: 'is:', value: 'uncaptured', glyph: 'square-dashed', desc: 'Posts whose content was never captured', excludeDesc: 'Hide posts whose content was never captured' },
            { prefix: 'is:', value: 'hidden', glyph: 'eye-off', desc: 'Tweets you hid from lists', excludeDesc: 'Hide tweets you hid from lists' }
        ],

        currentSearchTokenStart(text) {
            let tokenStart = 0;
            let inQuotes = false;
            for (let index = 0; index < text.length; index += 1) {
                if (text[index] === '"') {
                    inQuotes = !inQuotes;
                } else if (/\s/.test(text[index]) && !inQuotes) {
                    tokenStart = index + 1;
                }
            }
            return tokenStart;
        },

        // A picked filter value replaces the rest of the value being edited:
        // up to the closing quote, or to the end of an unquoted value.
        currentSearchTokenEnd(text, cursor, tokenStart) {
            const token = text.substring(tokenStart, cursor);
            if (!token.includes(':')) return cursor;
            if ((token.match(/"/g) || []).length % 2 === 1) {
                const closingQuote = text.indexOf('"', cursor);
                return closingQuote === -1 ? cursor : closingQuote + 1;
            }
            return cursor + text.substring(cursor).match(/^[^\s"]*/)[0].length;
        },

        // A response that arrives after newer input is discarded instead of
        // replacing the current suggestions.
        fetchSuggestions(url, toOptions, emptyNote = null) {
            const request = ++this.suggestionRequest;
            clearTimeout(this.suggestionTimer);
            this.suggestionTimer = setTimeout(() => {
                fetch(url)
                    .then(res => res.ok ? res.json() : {})
                    .then(data => {
                        if (request !== this.suggestionRequest) return;
                        this.options = toOptions(data || {});
                        if (this.options.length === 0) {
                            this.emptyNote = emptyNote;
                            if (!emptyNote) this.showDropdown = false;
                        }
                    })
                    .catch(err => {
                        if (request !== this.suggestionRequest) return;
                        console.error(err);
                        this.options = [];
                        this.showDropdown = false;
                    });
            }, SUGGESTION_DELAY_MS);
        },

        handleInput() {
            this.suggestionRequest++;
            clearTimeout(this.suggestionTimer);
            const input = this.$refs.searchInput;
            
            // The rich input is read back with its caret marked, so the query and
            // caret position stay correct when capsules hide their quotes.
            if (input.isContentEditable) {
                const parts = this.parseSearchParts(this.extractMarkedSearch(input));
                const { query, caret } = this.serializeSearchParts(parts);
                this.searchQuery = query;
                this.cursorPos = caret === -1 ? query.length : caret;

                if (!this.isComposing) {
                    this.renderSearchParts(parts);
                }
            } else {
                this.cursorPos = input.selectionStart;
            }
            
            const textToCursor = this.searchQuery.substring(0, this.cursorPos);
            const tokenStart = this.currentSearchTokenStart(textToCursor);
            const currentWord = textToCursor.substring(tokenStart);
            
            this.showDropdown = true;
            this.selectedIndex = 0;
            this.emptyNote = null;
            
            if (currentWord.includes(':')) {
                const parts = currentWord.split(':');
                const rawPrefix = parts[0] + ':';
                const basePrefix = rawPrefix.startsWith('-') ? rawPrefix.substring(1) : rawPrefix;
                const val = parts.slice(1).join(':').replace(/^"/, '').replace(/"$/, '').toLowerCase();
                const negated = rawPrefix.startsWith('-');
                const valueOption = o => ({ ...o, prefix: rawPrefix, negated, desc: negated ? o.excludeDesc : o.desc });
                
                if (basePrefix === 'has:') {
                    this.options = this.hasOptions.filter(o => o.value.startsWith(val)).map(valueOption);
                } else if (basePrefix === 'is:') {
                    this.options = this.isOptions.filter(o => o.value.startsWith(val)).map(valueOption);
                } else if (basePrefix === 'since:' || basePrefix === 'until:') {
                    // The whole date, so a caret inside it keeps the same day shown.
                    const tokenEnd = this.currentSearchTokenEnd(this.searchQuery, this.cursorPos, tokenStart);
                    const dateVal = this.searchQuery.substring(tokenStart + rawPrefix.length, tokenEnd)
                        .replace(/^"/, '').replace(/"$/, '').toLowerCase();
                    this.initDatePicker(dateVal);
                    this.options = [{
                        isDatePicker: true,
                        prefix: rawPrefix,
                        val: dateVal
                    }];
                } else if (basePrefix === 'from:' || basePrefix === 'to:') {
                    if (val.length > 0) {
                        this.fetchSuggestions(`/api/authors/search?q=${encodeURIComponent(val)}`, data => {
                            const authorsList = data.authors || [];
                            if (!this.knownAuthors) this.knownAuthors = new Set();
                            authorsList.forEach(a => this.knownAuthors.add(a.username.toLowerCase()));
                            return authorsList.map(a => ({
                                isAuthor: true,
                                prefix: rawPrefix,
                                value: a.username,
                                desc: a.display_name,
                                id: a.id,
                                query: val
                            }));
                        }, { noun: 'people', query: val });
                    } else {
                        this.options = [];
                        this.showDropdown = false;
                    }
                } else if (basePrefix === 'tag:') {
                    this.fetchSuggestions(`/api/tags/autocomplete?q=${encodeURIComponent(val)}`, data => {
                        const tags = data.tags || [];
                        if (!this.knownTags) this.knownTags = new Set();
                        tags.forEach(t => this.knownTags.add(t.tag.toLowerCase()));
                        return tags.map(t => ({
                            isTag: true,
                            prefix: rawPrefix,
                            value: t.tag,
                            count: t.count.toLocaleString(),
                            total: t.count,
                            query: val
                        }));
                    }, { noun: 'tags', query: val });
                } else {
                    this.options = [];
                    this.showDropdown = false;
                }
            } else {
                if (currentWord.startsWith('-')) {
                    const cleanWord = currentWord.substring(1).toLowerCase();
                    this.options = this.allFilters.filter(o => o.prefix.startsWith(cleanWord))
                        .map(o => ({ ...o, prefix: '-' + o.prefix, negated: true, desc: o.excludeDesc }));
                } else {
                    const lowerWord = currentWord.toLowerCase();
                    this.options = this.allFilters.filter(o => o.prefix.startsWith(lowerWord));
                }
            }
        },
        
        moveDown() {
            if (!this.showDropdown || this.options.length === 0) return;
            this.selectedIndex = (this.selectedIndex + 1) % this.options.length;
            this.scrollToSelected();
        },
        
        moveUp() {
            if (!this.showDropdown || this.options.length === 0) return;
            this.selectedIndex = (this.selectedIndex - 1 + this.options.length) % this.options.length;
            this.scrollToSelected();
        },

        scrollToSelected() {
            this.$nextTick(() => {
                const dropdown = this.$refs.dropdownMenu;
                if (!dropdown) return;
                const items = dropdown.querySelectorAll('[role="option"]');
                if (items && items[this.selectedIndex]) {
                    items[this.selectedIndex].scrollIntoView({ block: 'nearest' });
                }
            });
        },
        
        selectOptionIndex(idx) {
            this.selectedIndex = idx;
            this.selectOption();
        },
        
        selectOption() {
            if (!this.showDropdown || this.options.length === 0) {
                // With nothing to pick, Tab finishes the capsule being edited.
                const current = this.caretFilterBox();
                if (current && current.inValue) this.leaveFilterBox(current.box);
                return;
            }

            const opt = this.options[this.selectedIndex];
            if (opt.isDatePicker) return; // Prevent enter key from closing if it's the date picker itself
            this.insertOption(opt);
        },

        insertOption(opt) {
            const textToCursor = this.searchQuery.substring(0, this.cursorPos);
            const tokenStart = this.currentSearchTokenStart(textToCursor);
            const textAfterCursor = this.searchQuery.substring(this.currentSearchTokenEnd(this.searchQuery, this.cursorPos, tokenStart));
            
            let valueToInsert = opt.value;
            if (valueToInsert && valueToInsert.includes(' ')) {
                valueToInsert = `"${valueToInsert}"`;
            }
            
            let insertion = opt.prefix + (valueToInsert ? valueToInsert + ' ' : '');
            
            const newTextToCursor = textToCursor.substring(0, tokenStart) + insertion;
            this.searchQuery = newTextToCursor + textAfterCursor;
            
            this.showDropdown = false;
            const refreshSuggestions = Boolean(opt.value || opt.prefix.endsWith(':'));

            const input = this.$refs.searchInput;
            if (input && input.isContentEditable) {
                // Rendered now, before the searchQuery watcher runs, so keys typed
                // right after picking an option land at the caret.
                input.focus();
                this.formatRichText(newTextToCursor + SEARCH_CARET + textAfterCursor);
                if (refreshSuggestions) this.handleInput();
                return;
            }

            this.$nextTick(() => {
                const input = this.$refs.searchInput;
                input.focus();
                input.setSelectionRange(newTextToCursor.length, newTextToCursor.length);

                if (refreshSuggestions) {
                    this.handleInput();
                }
            });
        },
        
        suggestionGlyph(name) {
            const glyph = SUGGESTION_GLYPHS[name];
            if (!glyph) return '';
            return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${glyph[0]}" fill="currentColor" aria-hidden="true" focusable="false">${glyph[1]}</svg>`;
        },

        highlightMatch(text, query) {
            if (!text) return '';
            if (!query) return this.escapeHtml(text);
            const cleanQuery = query.startsWith('@') ? query.substring(1) : query;
            if (!cleanQuery) return this.escapeHtml(text);
            const idx = text.toLowerCase().indexOf(cleanQuery.toLowerCase());
            if (idx === -1) return this.escapeHtml(text);
            return this.escapeHtml(text.substring(0, idx))
                + '<b>' + this.escapeHtml(text.substring(idx, idx + cleanQuery.length)) + '</b>'
                + this.escapeHtml(text.substring(idx + cleanQuery.length));
        },

        highlightTagMatch(text, query) {
            if (!text) return '';
            const folded = text.toLowerCase();
            const ranges = (query || '').toLowerCase().split(/\s+/).filter(Boolean)
                .map(fragment => [folded.indexOf(fragment), fragment.length])
                .filter(([start]) => start !== -1)
                .map(([start, length]) => [start, start + length])
                .sort((a, b) => a[0] - b[0]);
            // Merge overlapping fragments before adding markup, and escape
            // every slice of the original label.
            const merged = [];
            for (const [start, end] of ranges) {
                const previous = merged[merged.length - 1];
                if (previous && start <= previous[1]) previous[1] = Math.max(previous[1], end);
                else merged.push([start, end]);
            }
            let html = '';
            let cursor = 0;
            for (const [start, end] of merged) {
                html += this.escapeHtml(text.substring(cursor, start))
                    + '<b>' + this.escapeHtml(text.substring(start, end)) + '</b>';
                cursor = end;
            }
            return html + this.escapeHtml(text.substring(cursor));
        },

        escapeHtml(unsafe) {
            return (unsafe || '').replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
        },

        validBooleanOperatorOffsets(text) {
            const tokens = [];
            const tokenRegex = /-?[\w_]+:(?:"[^"]*"|[^\s]+)|-?"[^"]*"|[^\s]+/g;
            let tokenMatch;
            while ((tokenMatch = tokenRegex.exec(text)) !== null) {
                tokens.push({ value: tokenMatch[0], offset: tokenMatch.index });
            }

            const hasFollowingClause = index => {
                let next = index + 1;
                if (tokens[next] && tokens[next].value === 'NOT') next += 1;
                return Boolean(tokens[next] && !['AND', 'OR', 'NOT'].includes(tokens[next].value));
            };
            const validOffsets = new Set();
            let hasClause = false;
            let expectingClause = false;
            let joinWithOr = false;
            let negateNext = false;

            tokens.forEach((token, index) => {
                if (token.value === 'OR') {
                    if (hasClause && !joinWithOr && !expectingClause && hasFollowingClause(index)) {
                        validOffsets.add(token.offset);
                        joinWithOr = true;
                        expectingClause = true;
                    }
                    return;
                }
                if (token.value === 'AND') {
                    if (hasClause && !joinWithOr && !expectingClause && hasFollowingClause(index)) {
                        validOffsets.add(token.offset);
                        expectingClause = true;
                    }
                    return;
                }
                if (token.value === 'NOT') {
                    if (!negateNext) {
                        negateNext = true;
                        expectingClause = true;
                    }
                    return;
                }

                hasClause = true;
                expectingClause = false;
                joinWithOr = false;
                negateNext = false;
            });
            return validOffsets;
        },

        // Renders query text. Text containing SEARCH_CARET also gets the caret there.
        formatRichText(text) {
            const input = this.$refs.searchInput;
            if (!input) return;

            if (text.length === 0) {
                input.innerHTML = '';
                return;
            }

            input.innerHTML = this.searchPartsHtml(this.parseSearchParts(text));
            if (text.includes(SEARCH_CARET)) this.placeMarkedCaret(input);
        },

        // Re-renders the editor from its own content, keeping the caret.
        refreshEditor() {
            const input = this.$refs.searchInput;
            if (input && input.isContentEditable && document.activeElement === input) {
                this.renderSearchParts(this.parseSearchParts(this.extractMarkedSearch(input)));
            } else {
                this.formatRichText(this.searchQuery || '');
            }
        },

        renderSearchParts(parts) {
            const input = this.$refs.searchInput;
            input.innerHTML = this.searchPartsHtml(parts);
            this.placeMarkedCaret(input);
            this.syncEditingState();
        },

        // Editor content as text: capsules wrapped in BOX_START/BOX_END and the
        // caret marked, ready for parseSearchParts.
        extractMarkedSearch(input, withCaret = true) {
            const sel = withCaret ? window.getSelection() : null;
            const anchor = sel && sel.rangeCount && input.contains(sel.anchorNode)
                ? { node: sel.anchorNode, offset: sel.anchorOffset } : null;
            const walk = node => {
                if (node.nodeType === 3) {
                    let data = node.data;
                    if (anchor && anchor.node === node) {
                        data = data.slice(0, anchor.offset) + SEARCH_CARET + data.slice(anchor.offset);
                    }
                    return data.replace(/\u00A0/g, ' ');
                }
                if (node.nodeType !== 1) return '';
                let text = '';
                node.childNodes.forEach((child, index) => {
                    if (anchor && anchor.node === node && anchor.offset === index) text += SEARCH_CARET;
                    text += walk(child);
                });
                if (anchor && anchor.node === node && anchor.offset === node.childNodes.length) text += SEARCH_CARET;
                return node !== input && node.classList.contains('filter-box') ? BOX_START + text + BOX_END : text;
            };
            return walk(input);
        },

        // The query the editor currently shows.
        editorSearchQuery(input) {
            if (!input.isContentEditable) return (input.textContent || '').replace(/\u00A0/g, ' ');
            return this.serializeSearchParts(this.parseSearchParts(this.extractMarkedSearch(input, false))).query;
        },

        // Splits query text (optionally marked) into text parts and capsules
        // ({ neg, key, value, caret }), where caret is an offset in value or -1.
        parseSearchParts(marked) {
            const keys = BOX_FILTER_KEYS.join('|');
            const raw = [];
            for (let index = 0; index < marked.length;) {
                if (marked[index] === BOX_START) {
                    let end = marked.indexOf(BOX_END, index);
                    if (end === -1) end = marked.length;
                    raw.push({ box: marked.slice(index + 1, end) });
                    index = end + 1;
                } else {
                    let end = marked.indexOf(BOX_START, index);
                    if (end === -1) end = marked.length;
                    raw.push({ text: marked.slice(index, end).split(BOX_END).join('') });
                    index = end;
                }
            }

            // A capsule whose key was edited away goes back to plain text.
            const merged = [];
            const keyPattern = new RegExp(`^(-?)(${keys}):`, 'i');
            for (const part of raw) {
                let box = null;
                if (part.box !== undefined) {
                    const plain = part.box.replace(SEARCH_CARET, '');
                    const match = plain.match(keyPattern);
                    if (match) {
                        const caretAt = part.box.indexOf(SEARCH_CARET);
                        const keyLength = match[0].length;
                        let rawValue = plain.slice(keyLength);
                        if (caretAt >= keyLength) {
                            rawValue = rawValue.slice(0, caretAt - keyLength) + SEARCH_CARET + rawValue.slice(caretAt - keyLength);
                        }
                        box = this.filterBoxPart(match[1], match[2], rawValue);
                        // A caret inside the key (from arrowing or clicking) stays there.
                        if (caretAt !== -1 && caretAt < keyLength) box.keyCaret = caretAt;
                    }
                }
                const last = merged[merged.length - 1];
                if (box) merged.push(box);
                else if (last && last.text !== undefined) last.text += part.text ?? part.box;
                else merged.push({ text: part.text ?? part.box });
            }

            // Typed, pasted, or loaded filters in plain text become capsules. A caret
            // right after the colon opens the new capsule for editing.
            const parts = [];
            const tokenPattern = new RegExp(`(^|\\s|${SEARCH_CARET})(-?)(${keys}):("[^"]*(?:"|$)|[^\\s"]*)`, 'gi');
            for (const part of merged) {
                if (part.text === undefined) {
                    parts.push(part);
                    continue;
                }
                let last = 0;
                let match;
                tokenPattern.lastIndex = 0;
                while ((match = tokenPattern.exec(part.text)) !== null) {
                    const start = match.index + match[1].length;
                    parts.push({ text: part.text.slice(last, start) });
                    parts.push(this.filterBoxPart(match[2], match[3], match[4]));
                    last = match.index + match[0].length;
                }
                parts.push({ text: part.text.slice(last) });
            }
            return this.joinGluedSearchText(parts.filter(part => part.text !== ''));
        },

        // A capsule from its raw value, which may hold quotes and the caret marker.
        filterBoxPart(neg, key, rawValue) {
            const caretAt = rawValue.indexOf(SEARCH_CARET);
            return {
                neg,
                key: key.toLowerCase(),
                value: rawValue.replace(SEARCH_CARET, '').replace(/"/g, ''),
                caret: caretAt === -1 ? -1 : rawValue.slice(0, caretAt).replace(/"/g, '').length,
                keyCaret: -1,
            };
        },

        // A capsule's key and value as marked text, keeping its caret.
        filterBoxText(part) {
            let key = `${part.neg}${part.key}:`;
            if (part.keyCaret !== -1) key = key.slice(0, part.keyCaret) + SEARCH_CARET + key.slice(part.keyCaret);
            const value = part.caret === -1
                ? part.value
                : part.value.slice(0, part.caret) + SEARCH_CARET + part.value.slice(part.caret);
            return { key, value };
        },

        // Capsules follow the same rules as plain query text. Text typed directly
        // against the end of a capsule is part of its value (so a caret on that edge
        // belongs to the capsule), and text directly against its start means it is
        // no longer a filter.
        joinGluedSearchText(parts) {
            for (let index = 0; index < parts.length; index += 1) {
                const box = parts[index];
                if (box.text !== undefined) continue;
                const next = parts[index + 1];
                const glued = next && next.text !== undefined ? next.text.match(/^\S+/) : null;
                if (glued) {
                    const caretAt = glued[0].indexOf(SEARCH_CARET);
                    if (caretAt !== -1) {
                        box.caret = box.value.length + glued[0].slice(0, caretAt).replace(/"/g, '').length;
                    }
                    box.value += glued[0].replace(SEARCH_CARET, '').replace(/"/g, '');
                    next.text = next.text.slice(glued[0].length);
                }
                const prev = parts[index - 1];
                if (prev && prev.text !== undefined && /\S$/.test(prev.text.replace(SEARCH_CARET, ''))) {
                    const { key, value } = this.filterBoxText(box);
                    prev.text += key + value;
                    parts.splice(index, 1);
                    index -= 1;
                }
            }
            return parts.reduce((joined, part) => {
                const last = joined[joined.length - 1];
                if (part.text !== undefined && last && last.text !== undefined) last.text += part.text;
                else if (part.text !== '') joined.push(part);
                return joined;
            }, []);
        },

        // A capsule's query token: key:word or key:"several words". Values are
        // trimmed here, so spaces still being typed never reach the server.
        filterBoxToken(part) {
            const prefix = `${part.neg}${part.key}:`;
            const value = part.value.replace(/\s+/g, ' ').trim();
            const quoted = value.includes(' ');
            let caret = part.keyCaret ?? -1;
            if (part.caret >= 0) {
                const before = part.value.slice(0, part.caret).replace(/\s+/g, ' ').trimStart();
                caret = prefix.length + (quoted ? 1 : 0) + Math.min(before.length, value.length);
            }
            return { token: prefix + (quoted ? `"${value}"` : value), caret };
        },

        // Query text for parts, with the caret's offset in it (-1 if none) and
        // where each part starts.
        serializeSearchParts(parts) {
            let query = '';
            let caret = -1;
            const starts = [];
            parts.forEach(part => {
                starts.push(query.length);
                if (part.text !== undefined) {
                    const caretAt = part.text.indexOf(SEARCH_CARET);
                    if (caretAt !== -1) caret = query.length + caretAt;
                    query += part.text.replace(SEARCH_CARET, '');
                } else {
                    const token = this.filterBoxToken(part);
                    if (token.caret !== -1) caret = query.length + token.caret;
                    query += token.token;
                }
            });
            return { query, caret, starts };
        },

        searchPartsHtml(parts) {
            const { query, starts } = this.serializeSearchParts(parts);
            const validBooleanOffsets = this.validBooleanOperatorOffsets(query);
            return parts.map((part, index) => (
                part.text !== undefined
                    ? this.plainSearchHtml(part.text, starts[index], validBooleanOffsets)
                    : this.filterBoxHtml(part)
            )).join('');
        },

        // A description: or tag: capsule. Its value is plain text, so the caret moves
        // through it like any other text. Quotes are left out of the display.
        filterBoxHtml(part) {
            const { key, value } = this.filterBoxText(part);
            let classes = 'filter-box';
            if (part.neg) classes += ' negative';
            if (!part.value.trim()) {
                classes += ' is-empty';
            } else if (part.key === 'tag' && !this.isKnownTag(part.value)) {
                classes += ' is-unknown';
                this.validateTagAsync(part.value.trim());
            }
            // An empty value has no text node, so its caret goes right after the colon.
            const keyCaret = part.caret !== -1 && !part.value ? SEARCH_CARET : '';
            return `<span class="${classes}"><span class="capsule-key">${key}${keyCaret}</span>`
                + `<span class="capsule-value">${part.value ? this.escapeHtml(value).replace(/ /g, '&nbsp;') : ''}</span></span>`;
        },

        // Plain query text starting at `start` in the full query, with capsules for
        // the other filters and valid boolean operators.
        plainSearchHtml(text, start, validBooleanOffsets) {
            let html = '';

            // Regex to parse operators vs normal text, preserving quotes and whitespace
            const regex = /(\s+)|(?:(-?(?:from|to|has|is|filter|since|until|url):)("[^"]*(?:"|$)|[^\s]*))|([^\s]+)/gi;
            let match;

            while ((match = regex.exec(text)) !== null) {
                if (match[1]) {
                    // whitespace (preserve spaces for caret using non-breaking space)
                    html += match[1].replace(/ /g, '&nbsp;');
                } else if (match[2]) {
                    const prefix = match[2];
                    let value = match[3] || '';
                    // A caret at the end of the value stays after the capsule.
                    let trailingCaret = '';
                    if (value.endsWith(SEARCH_CARET)) {
                        value = value.slice(0, -1);
                        trailingCaret = SEARCH_CARET;
                    }

                    if (value) {
                        let isValid = true;
                        let cleanValue = value.replace(SEARCH_CARET, '');
                        if (cleanValue.startsWith('"') && cleanValue.endsWith('"') && cleanValue.length >= 2) {
                            cleanValue = cleanValue.substring(1, cleanValue.length - 1);
                        }

                        if (prefix.toLowerCase().endsWith('has:')) {
                            isValid = this.hasOptions.some(option => option.value === cleanValue.toLowerCase());
                        } else if (prefix.toLowerCase().endsWith('is:')) {
                            isValid = this.isOptions.some(option => option.value === cleanValue.toLowerCase());
                        } else if (prefix.toLowerCase().endsWith('filter:')) {
                            isValid = ['articles', 'media', 'images', 'videos', 'native_video', 'links', 'replies', 'quote', 'nativeretweets', 'self_threads', 'threads', 'verified'].includes(cleanValue.toLowerCase());
                        } else if (prefix.toLowerCase().endsWith('from:') || prefix.toLowerCase().endsWith('to:')) {
                            if (this.knownAuthors && this.knownAuthors.has(cleanValue.toLowerCase())) {
                                isValid = true;
                            } else {
                                isValid = false;
                                if (cleanValue.length > 0) {
                                    this.validateAuthorAsync(cleanValue);
                                }
                            }
                        } else if (prefix.toLowerCase().endsWith('since:') || prefix.toLowerCase().endsWith('until:')) {
                            isValid = /^\d{4}-\d{2}-\d{2}$/.test(cleanValue);
                        }
                        // url, etc are dynamic, so assume valid if there is a value

                        const isNegative = prefix.startsWith('-');
                        const negativeClass = isNegative ? ' negative-capsule' : '';

                        if (isValid) {
                            if (value.startsWith('"') && value.endsWith('"') && value.length >= 2) {
                                const innerValue = value.substring(1, value.length - 1);
                                html += `<span class="search-capsule valid-capsule${negativeClass}"><span class="capsule-key">${prefix}</span><span class="hidden-quote">"</span><span class="capsule-value">${this.escapeHtml(innerValue).replace(/ /g, '&nbsp;')}</span><span class="hidden-quote">"</span></span>`;
                            } else {
                                html += `<span class="search-capsule valid-capsule${negativeClass}"><span class="capsule-key">${prefix}</span><span class="capsule-value">${this.escapeHtml(value)}</span></span>`;
                            }
                        } else {
                            html += `<span class="search-capsule invalid-capsule${negativeClass}"><span class="capsule-key">${prefix}</span></span><span class="capsule-invalid-value">${this.escapeHtml(value)}</span>`;
                        }
                    } else {
                        const isNegative = prefix.startsWith('-');
                        const negativeClass = isNegative ? ' negative-capsule' : '';
                        html += `<span class="search-capsule incomplete-capsule${negativeClass}"><span class="capsule-key">${prefix}</span></span>`;
                    }
                    html += trailingCaret;
                } else if (match[4]) {
                    const caretBefore = text.slice(0, match.index).includes(SEARCH_CARET) ? 1 : 0;
                    if (validBooleanOffsets.has(start + match.index - caretBefore)) {
                        html += `<span class="search-capsule boolean-capsule">${this.escapeHtml(match[4])}</span>`;
                    } else {
                        html += this.escapeHtml(match[4]);
                    }
                }
            }
            return html;
        },

        // Moves the caret to the SEARCH_CARET marker left in the rendered editor.
        placeMarkedCaret(input) {
            const walker = document.createTreeWalker(input, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                const index = node.data.indexOf(SEARCH_CARET);
                if (index === -1) continue;
                node.data = node.data.slice(0, index) + node.data.slice(index + 1);
                const range = document.createRange();
                range.setStart(node, index);
                range.collapse(true);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                return;
            }
        },

        // The capsule holding the caret, whether the caret is in its value (after
        // the colon), and whether it is at the end of the value.
        caretFilterBox() {
            const input = this.$refs.searchInput;
            if (!input || !input.isContentEditable) return null;
            const sel = window.getSelection();
            if (!sel || !sel.rangeCount || !input.contains(sel.anchorNode)) return null;
            const element = sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement;
            const box = element && element.closest('.filter-box');
            if (!box || !input.contains(box)) return null;
            const before = document.createRange();
            before.selectNodeContents(box);
            before.setEnd(sel.anchorNode, sel.anchorOffset);
            const keyLength = (box.querySelector('.capsule-key')?.textContent || '').length;
            const offset = before.toString().length - keyLength;
            const valueLength = (box.querySelector('.capsule-value')?.textContent || '').length;
            return { box, inValue: offset >= 0, atEnd: sel.isCollapsed && offset === valueLength };
        },

        // Finishing a capsule moves the caret past the space after it, adding one
        // if needed. An empty capsule stays, like an unfinished from:.
        leaveFilterBox(box) {
            let next = box.nextSibling;
            if (!next || next.nodeType !== 3 || !/^\s/.test(next.data)) {
                next = document.createTextNode('\u00A0');
                box.after(next);
            }
            const range = document.createRange();
            range.setStart(next, 1);
            range.collapse(true);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            this.handleInput();
        },

        // A capsule shows its editing style while the caret is in its value.
        syncEditingState() {
            const input = this.$refs.searchInput;
            if (!input || !input.isContentEditable) return;
            const current = document.activeElement === input ? this.caretFilterBox() : null;
            input.querySelectorAll('.filter-box').forEach(box => {
                box.classList.toggle('is-editing', Boolean(current && current.inValue && box === current.box));
            });
        },

        // The query and caret the editor shows; caret is -1 outside the input.
        readEditorCaret(input) {
            return this.serializeSearchParts(this.parseSearchParts(this.extractMarkedSearch(input)));
        },

        // Moving the caret into a since:/until: value reopens the calendar, and
        // moving it away from an open calendar refreshes the suggestions. Typing
        // already updated cursorPos through handleInput, so it is skipped here.
        syncDatePickerToCaret() {
            const input = this.$refs.searchInput;
            if (!input || !input.isContentEditable || this.isComposing) return;
            if (document.activeElement !== input || !window.getSelection().isCollapsed) return;
            const { query, caret } = this.readEditorCaret(input);
            if (caret === -1 || query !== this.searchQuery || caret === this.cursorPos) return;
            const textToCursor = query.substring(0, caret);
            const word = textToCursor.substring(this.currentSearchTokenStart(textToCursor));
            const pickerOpen = this.showDropdown && this.options[0]?.isDatePicker;
            if (pickerOpen || /^-?(since|until):/.test(word)) this.handleInput();
        },

        handleEscape() {
            const current = this.caretFilterBox();
            if (current && current.inValue) this.leaveFilterBox(current.box);
            else this.showDropdown = false;
        },

        // At the very end of the search, → has nowhere to go, so it finishes the capsule.
        handleArrowRight(event) {
            const current = this.caretFilterBox();
            if (!current || !current.inValue || !current.atEnd) return;
            const next = current.box.nextSibling;
            if (next && next.textContent.replace(/\u00A0/g, ' ').length > 0) return;
            event.preventDefault();
            this.leaveFilterBox(current.box);
        },

        // Quotes would end a capsule's value early, so they are not typed into one.
        handleBeforeInput(event) {
            if (!event.data || !event.data.includes('"')) return;
            const current = this.caretFilterBox();
            if (!current || !current.inValue) return;
            event.preventDefault();
            const text = event.data.replace(/"/g, '');
            if (text) document.execCommand('insertText', false, text);
        },

        // Copying gives query text, so capsules with spaces come out quoted.
        handleCopy(event) {
            const sel = window.getSelection();
            if (!sel.rangeCount || sel.isCollapsed) return;
            event.preventDefault();
            event.clipboardData.setData('text/plain', this.fragmentSearchQuery(sel.getRangeAt(0).cloneContents()).trim());
            if (event.type === 'cut') document.execCommand('delete');
        },

        fragmentSearchQuery(root) {
            let text = '';
            root.childNodes.forEach(node => {
                if (node.nodeType === 3) {
                    text += node.data.replace(/\u00A0/g, ' ');
                } else if (node.nodeType === 1 && node.classList.contains('filter-box') && node.querySelector('.capsule-key')) {
                    text += this.parseSearchParts(node.textContent.replace(/\u00A0/g, ' '))
                        .map(part => (part.text !== undefined ? part.text : this.filterBoxToken(part).token))
                        .join('');
                } else if (node.nodeType === 1) {
                    text += this.fragmentSearchQuery(node);
                }
            });
            return text;
        },

        // Tags are known from the loaded tag list or from tag suggestions.
        isKnownTag(value) {
            const tag = value.trim().toLowerCase();
            if (this.knownTags && this.knownTags.has(tag)) return true;
            return Array.isArray(this.globalTags) && this.globalTags.some(t => t.tag.toLowerCase() === tag);
        },

        validateTagAsync(tag) {
            if (!this.knownTags) this.knownTags = new Set();
            if (!this.pendingTagValidations) this.pendingTagValidations = new Set();

            const lower = tag.toLowerCase();
            if (this.pendingTagValidations.has(lower)) return;
            this.pendingTagValidations.add(lower);

            fetch(`/api/tags/autocomplete?q=${encodeURIComponent(tag)}`)
                .then(res => res.ok ? res.json() : null)
                .then(data => {
                    const tags = (data && data.tags) || [];
                    tags.forEach(t => this.knownTags.add(t.tag.toLowerCase()));
                    if (this.knownTags.has(lower)) this.refreshEditor();
                })
                .catch(() => {})
                .finally(() => {
                    this.pendingTagValidations.delete(lower);
                });
        },

        validateAuthorAsync(username) {
            if (!this.knownAuthors) this.knownAuthors = new Set();
            if (!this.pendingAuthorValidations) this.pendingAuthorValidations = new Set();

            const lower = username.toLowerCase();
            if (this.pendingAuthorValidations.has(lower)) return;
            this.pendingAuthorValidations.add(lower);

            fetch(`/api/authors/search?q=${encodeURIComponent(username)}`)
                .then(res => res.ok ? res.json() : null)
                .then(data => {
                    let found = false;
                    if (data && data.authors) {
                        data.authors.forEach(a => {
                            this.knownAuthors.add(a.username.toLowerCase());
                            if (a.username.toLowerCase() === lower) found = true;
                        });
                    }
                    if (found) {
                        this.refreshEditor();
                    }
                })
                .catch(() => {})
                .finally(() => {
                    this.pendingAuthorValidations.delete(lower);
                });
        },

        initDatePicker(val) {
            let d = new Date();
            if (val && /^\d{4}-\d{2}-\d{2}$/.test(val)) {
                const parsed = new Date(val + 'T00:00:00');
                if (!isNaN(parsed.getTime())) d = parsed;
            } else if (val) {
                const parts = val.split('-');
                if (parts[0] && parts[0].length === 4) {
                    const y = parseInt(parts[0], 10);
                    if (!isNaN(y)) d.setFullYear(y);
                    if (parts[1] && parts[1].length === 2) {
                        const m = parseInt(parts[1], 10) - 1;
                        if (!isNaN(m) && m >= 0 && m <= 11) {
                            d.setMonth(m);
                        }
                    }
                }
            }
            this.dpYear = d.getFullYear();
            this.dpMonth = d.getMonth();
        },
        
        dpPrevMonth() {
            if (this.dpMonth === 0) {
                this.dpMonth = 11;
                this.dpYear--;
            } else {
                this.dpMonth--;
            }
        },
        
        dpNextMonth() {
            if (this.dpMonth === 11) {
                this.dpMonth = 0;
                this.dpYear++;
            } else {
                this.dpMonth++;
            }
        },
        
        dpGetDays() {
            const days = [];
            const firstDay = new Date(this.dpYear, this.dpMonth, 1).getDay();
            const daysInMonth = new Date(this.dpYear, this.dpMonth + 1, 0).getDate();
            const prevMonthDays = new Date(this.dpYear, this.dpMonth, 0).getDate();
            const now = new Date();
            const todayStr = this.formatDateStr(now.getFullYear(), now.getMonth() + 1, now.getDate());
            
            // Previous month
            for (let i = firstDay - 1; i >= 0; i--) {
                const dateStr = this.formatDateStr(this.dpMonth === 0 ? this.dpYear - 1 : this.dpYear, this.dpMonth === 0 ? 12 : this.dpMonth, prevMonthDays - i);
                days.push({ 
                    day: prevMonthDays - i, 
                    isCurrentMonth: false, 
                    dateStr: dateStr,
                    isToday: dateStr === todayStr
                });
            }
            // Current month
            for (let i = 1; i <= daysInMonth; i++) {
                const dateStr = this.formatDateStr(this.dpYear, this.dpMonth + 1, i);
                days.push({ 
                    day: i, 
                    isCurrentMonth: true, 
                    dateStr: dateStr,
                    isToday: dateStr === todayStr
                });
            }
            // Next month
            const remaining = 42 - days.length;
            for (let i = 1; i <= remaining; i++) {
                const dateStr = this.formatDateStr(this.dpMonth === 11 ? this.dpYear + 1 : this.dpYear, this.dpMonth === 11 ? 1 : this.dpMonth + 2, i);
                days.push({ 
                    day: i, 
                    isCurrentMonth: false, 
                    dateStr: dateStr,
                    isToday: dateStr === todayStr
                });
            }
            return days;
        },
        
        // Like X, today stands in as the selection until a full date is typed.
        dpIsSelected(dayObj, opt) {
            // Today is marked separately; nothing is selected until a date is entered.
            return /^\d{4}-\d{2}-\d{2}$/.test(opt.val) && dayObj.dateStr === opt.val;
        },

        formatDateStr(y, m, d) {
            return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        },
        
        dpSelectDate(dateStr, opt) {
            this.selectedIndex = 0;
            // Leave the picker option untouched: the insert replaces the options,
            // and mutating the old row afterwards kept the calendar rendered.
            this.insertOption({ prefix: opt.prefix, value: dateStr });
        }
    };
}
