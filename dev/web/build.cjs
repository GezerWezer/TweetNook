// Generated assets ship in the Python package. Node is only needed by maintainers.
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '../../tweetnook/web/static');
const vendor = path.join(root, 'vendor');
fs.mkdirSync(vendor, { recursive: true });
for (const [pkg, name] of [
    ['alpinejs', 'alpine'],
    ['@alpinejs/intersect', 'intersect'],
    ['@alpinejs/collapse', 'collapse'],
]) {
    const base = path.join(__dirname, 'node_modules', pkg);
    fs.copyFileSync(path.join(base, 'dist/cdn.min.js'), path.join(vendor, `${name}.min.js`));
}
// Alpine's npm archives omit the license; retain the upstream v3.13.3 license.
fs.copyFileSync(path.join(__dirname, 'alpine.LICENSE.md'), path.join(vendor, 'alpine.LICENSE.md'));
fs.copyFileSync(path.join(__dirname, 'node_modules/tailwindcss/LICENSE'), path.join(vendor, 'tailwind.LICENSE'));
execFileSync(process.execPath, [
    require.resolve('tailwindcss/lib/cli.js'),
    '-c', path.join(__dirname, 'tailwind.config.cjs'),
    '-i', path.join(__dirname, 'input.css'),
    '-o', path.join(root, 'css/tailwind.css'), '--minify',
], { cwd: __dirname, stdio: 'inherit', env: { ...process.env, BROWSERSLIST_IGNORE_OLD_DATA: 'true' } });
