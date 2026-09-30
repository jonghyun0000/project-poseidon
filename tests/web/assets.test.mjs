import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const consoleHtml=fs.readFileSync('web/public/console/index.html','utf8');
const legacyHtml=fs.readFileSync('web/public/index.html','utf8');
const vendor='web/public/console/vendor/maplibre-gl-5.7.1';

test('console and legacy views use the same pinned local MapLibre bundle',()=>{
 assert.match(consoleHtml,/\/console-assets\/vendor\/maplibre-gl-5\.7\.1\/maplibre-gl\.js/);
 assert.match(consoleHtml,/\/console-assets\/vendor\/maplibre-gl-5\.7\.1\/maplibre-gl\.css/);
 assert.match(legacyHtml,/\/console-assets\/vendor\/maplibre-gl-5\.7\.1\/maplibre-gl\.js/);
 assert.match(legacyHtml,/\/console-assets\/vendor\/maplibre-gl-5\.7\.1\/maplibre-gl\.css/);
 assert.doesNotMatch(consoleHtml,/cdn\.jsdelivr\.net\/npm\/maplibre-gl/);
 assert.doesNotMatch(legacyHtml,/cdn\.jsdelivr\.net\/npm\/maplibre-gl/);
});

test('the vendored map runtime includes attribution and a license record',()=>{
 const js=fs.statSync(`${vendor}/maplibre-gl.js`);
 const css=fs.statSync(`${vendor}/maplibre-gl.css`);
 const license=fs.readFileSync(`${vendor}/LICENSE.txt`,'utf8');
 assert.ok(js.size>500_000);
 assert.ok(css.size>10_000);
 assert.match(license,/MapLibre GL JS 5\.7\.1/);
 assert.match(license,/BSD 3-Clause/);
});
