// Renders a built OpenFilm project on this host. No isolation boundary.
// Usage: node openfilm_local_render.mjs <brief.json> <project dir> <output dir>
// The openfilm package must be importable from this file's directory (node_modules/openfilm).
// The result is written to <output dir>/render-result.json; stdout is left for logs.
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { renderAssets, inspectLayout } from './openfilm_render.mjs';

const [briefPath, projectDir, outputDir] = process.argv.slice(2);
const require = createRequire(import.meta.url);
if (require('openfilm/package.json').version !== '0.2.6') throw new Error('OpenFilm 0.2.6 required');
const { look } = await import('openfilm/look');
const { render } = await import('openfilm/render');
const { open } = await import('openfilm/shared');
const brief = JSON.parse(await readFile(briefPath, 'utf8'));
const result = await renderAssets(brief, projectDir, outputDir, { look, render, layout: folder => inspectLayout(folder, open) });
await writeFile(join(outputDir, 'render-result.json'), JSON.stringify(result), { flag: 'wx', mode: 0o600 });
