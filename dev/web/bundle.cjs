// Concatenate the scripts and stylesheets index.html lists between <!-- bundle --> and
// <!-- /bundle --> into one minified script and one stylesheet, so a cold page load makes
// two requests instead of fifty. bundle.json records a digest of the sources; the server
// serves the bundle only while that digest matches, so rerun this after editing them.
// Usage: node bundle.cjs PATH_TO_ESBUILD
'use strict';
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const web = path.resolve(__dirname, '../../tweetnook/web');
const staticRoot = path.join(web, 'static');
const esbuild = process.argv[2];
if (!esbuild) throw new Error('usage: node bundle.cjs PATH_TO_ESBUILD');
const shell = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
const block = /^[ \t]*<!-- bundle -->\n([\s\S]*?)^[ \t]*<!-- \/bundle -->\n/m.exec(shell);
if (!block) throw new Error('index.html has no <!-- bundle --> block');
const scripts = [...block[1].matchAll(/^[ \t]*<script src="\/static\/(js\/[^"]+)"><\/script>$/gm)].map(m => m[1]);
const styles = [...block[1].matchAll(/^[ \t]*<link rel="stylesheet" href="\/static\/(css\/[^"]+)">$/gm)].map(m => m[1]);
const read = name => fs.readFileSync(path.join(staticRoot, name));

// Must match tweetnook.web.page.sources_digest.
function digest(names) {
    const hash = crypto.createHash('sha256');
    for (const name of names) hash.update(Buffer.concat([Buffer.from(`${name}\0`), read(name), Buffer.from('\0')]));
    return hash.digest('hex');
}

// Fed on stdin, esbuild transforms and writes the result to stdout.
function minify(source, loader, out) {
    const args = [`--loader=${loader}`, '--minify', ...(loader === 'js' ? ['--target=es2020'] : [])];
    fs.writeFileSync(out, execFileSync(esbuild, args, { input: source, stdio: ['pipe', 'pipe', 'inherit'], maxBuffer: 1 << 28 }));
}

// Each classic script ends its own statement list; a newline and semicolon keep them apart.
minify(scripts.map(name => `/* ${name} */\n${read(name)}\n;`).join('\n'), 'js', path.join(staticRoot, 'js/bundle.min.js'));
minify(styles.map(name => read(name).toString()).join('\n'), 'css', path.join(staticRoot, 'css/bundle.min.css'));
fs.writeFileSync(path.join(staticRoot, 'bundle.json'), `${JSON.stringify({ js: digest(scripts), css: digest(styles) })}\n`);
console.log(`${scripts.length} scripts, ${styles.length} stylesheets`);
