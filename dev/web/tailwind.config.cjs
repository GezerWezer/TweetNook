// Tailwind's space-x/space-y select `> :not([hidden]) ~ :not([hidden])`. A rule
// whose last step is that universal `~ :not([hidden])` makes Chromium treat every
// element's children as affected by sibling rules, so inserting or removing any
// child restyles all of its later siblings. The feed inserts and removes cards
// all the time, and each change restyled the rest of the list. Skipping the
// first child applies the same margins (no space container has a [hidden] first
// child) at the same specificity, and only a change at the front of a list
// invalidates a sibling.
const spacing = (value, axis) => {
    value = value === '0' ? '0px' : value;
    const [start, end] = axis === 'x' ? ['margin-left', 'margin-right'] : ['margin-top', 'margin-bottom'];
    return {
        '& > :not([hidden]):not(:first-child)': {
            [`--tw-space-${axis}-reverse`]: '0',
            [end]: `calc(${value} * var(--tw-space-${axis}-reverse))`,
            [start]: `calc(${value} * calc(1 - var(--tw-space-${axis}-reverse)))`,
        },
    };
};

module.exports = {
    content: {
        relative: true,
        files: ['../../tweetnook/web/index.html', '../../tweetnook/web/fragments/**/*.html', '../../tweetnook/web/static/js/**/*.js'],
    },
    corePlugins: { space: false },
    plugins: [
        // A plain function, so the harnesses can read this file without Tailwind installed.
        ({ matchUtilities, theme }) => {
            matchUtilities({
                'space-x': value => spacing(value, 'x'),
                'space-y': value => spacing(value, 'y'),
            }, { values: theme('space'), supportsNegativeValues: true });
        },
    ],
};
