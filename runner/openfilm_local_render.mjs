// Renders a built OpenFilm project on this host. No isolation boundary.
// Usage: node openfilm_local_render.mjs <brief.json> <project dir> <output dir>
// The openfilm package must be importable from this file's directory (node_modules/openfilm).
import { readFile } from 'node:fs/promises';
import { renderAssets, inspectLayout } from './openfilm_render.mjs';

const [briefPath, projectDir, outputDir] = process.argv.slice(2);
const { look } = await import('openfilm/look');
const { render } = await import('openfilm/render');
const { open } = await import('openfilm/shared');
const brief = JSON.parse(await readFile(briefPath, 'utf8'));
const result = await renderAssets(brief, projectDir, outputDir, { look, render, layout: folder => inspectLayout(folder, open) });
process.stdout.write(JSON.stringify(result));
