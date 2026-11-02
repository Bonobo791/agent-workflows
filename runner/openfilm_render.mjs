/* Fixed local OpenFilm render entrypoint; the supervisor supplies isolation. */
import { createHash } from 'node:crypto';
import { lstat, readFile, readdir, realpath, writeFile } from 'node:fs/promises';
import { isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const MAX_BYTES = 100 * 1024 * 1024;
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');

// Inspects rendered text boxes in the same isolated browser used by OpenFilm.
export async function inspectLayout(folder, open) {
  const session = await open(folder, { offline: true, network: false, playable: true });
  try {
    const { film } = session;
    const duration = film.meta.duration;
    if (!Number.isFinite(duration) || duration <= 0) throw new Error('layout duration unavailable');
    const findings = new Set();
    let inspected = 0;
    for (let i = 0; i < 12; i++) {
      const time = (duration - Math.min(0.001, duration / 2)) * i / 11;
      await film.seek(time);
      for (const frame of film.page.frames()) {
        if (session.isFilm && frame === film.page.mainFrame()) continue;
        const owner = await frame.frameElement().catch(() => null);
        if (owner && !(await owner.isVisible())) continue;
        const result = await frame.evaluate(() => {
          if (!window.film || typeof window.film.frame !== 'function') return null;
          const rects = [], owners = new Map();
          const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) {
            const node = walker.currentNode, el = node.parentElement;
            if (!node.textContent.trim() || !el) continue;
            let visible = true;
            for (let p = el; p; p = p.parentElement) {
              const s = getComputedStyle(p);
              if (s.display === 'none' || s.visibility !== 'visible' || Number(s.opacity) < 0.05) { visible = false; break; }
            }
            if (!visible) continue;
            if (!owners.has(el)) owners.set(el, owners.size);
            const range = document.createRange(); range.selectNodeContents(node);
            for (const r of range.getClientRects()) if (r.width > 1 && r.height > 1) rects.push({x:r.x,y:r.y,right:r.right,bottom:r.bottom,owner:owners.get(el),text:node.textContent.trim().slice(0,80)});
            if (rects.length > 2000) throw new Error('too many text rectangles');
          }
          const problems = [];
          for (let a = 0; a < rects.length; a++) {
            const r = rects[a];
            if (r.x < -1 || r.y < -1 || r.right > innerWidth + 1 || r.bottom > innerHeight + 1) problems.push(`text outside frame: ${r.text}`);
            for (let b = 0; b < a; b++) {
              const q = rects[b];
              if (r.owner === q.owner) continue;
              if (Math.min(r.right,q.right)-Math.max(r.x,q.x)>2 && Math.min(r.bottom,q.bottom)-Math.max(r.y,q.y)>2) problems.push(`overlapping text: ${q.text} / ${r.text}`);
            }
          }
          return { count: rects.length, problems: problems.slice(0,20) };
        });
        if (result) { inspected += result.count; for (const problem of result.problems) findings.add(`${time.toFixed(2)}s: ${problem}`); }
      }
    }
    if (!inspected) throw new Error('layout text inspection unavailable');
    return [...findings];
  } finally { await session.close(); }
}

async function confinedFile(root, path) {
  const resolvedRoot = await realpath(root);
  const resolvedPath = await realpath(path);
  const rel = relative(resolvedRoot, resolvedPath);
  if (!rel || rel.startsWith('..') || isAbsolute(rel)) throw new Error('preview outside project');
  const info = await lstat(path);
  if (!info.isFile() || info.isSymbolicLink() || info.size > MAX_BYTES) throw new Error('invalid output file');
  return resolvedPath;
}

export async function renderAssets(brief, projectRoot, outputRoot, api) {
  if (!brief || !Array.isArray(brief.assets) || brief.assets.length < 1 || brief.assets.length > 10 || !api || typeof api.look !== 'function' || typeof api.render !== 'function' || typeof api.layout !== 'function') throw new Error('invalid render request');
  const project = await realpath(projectRoot);
  const output = await realpath(outputRoot);
  if ((await readdir(output)).length) throw new Error('output directory must be empty');
  const outputs = [];
  for (const [index, asset] of brief.assets.entries()) {
    if (!asset || !['png', 'mp4'].includes(asset.kind)) throw new Error('unsupported asset kind');
    const folder = brief.assets.length === 1 ? project : join(project, 'projects', String(index));
    await confinedFile(project, join(folder, 'film.html'));
    let layout;
    try { layout = await api.layout(folder); }
    catch { layout = ['layout inspection unavailable']; }
    if (!Array.isArray(layout) || layout.some(x => typeof x !== 'string')) layout = ['layout inspection unavailable'];
    if (layout.length) {
      await writeFile(join(output, 'look-failure.json'), JSON.stringify({ok:false,lines:layout.map(x => `asset ${index}: ${x}`)}), {flag:'wx',mode:0o600});
      throw new Error('layout inspection failed');
    }
    const check = async (args) => {
      const inspected = await api.look(args, { count: 12 });
      if (inspected && inspected.ok === false && Array.isArray(inspected.lines) && inspected.lines.every((line) => typeof line === 'string')) {
        const report = Buffer.from(JSON.stringify({ ok: false, lines: inspected.lines }));
        if (report.length <= 1024 * 1024) await writeFile(join(output, 'look-failure.json'), report, { flag: 'wx', mode: 0o600 });
      }
      if (!inspected || inspected.ok !== true || !Array.isArray(inspected.files) || !inspected.files.length || !Array.isArray(inspected.lines) || inspected.lines.some((line) => typeof line !== 'string') || !inspected.lines.some((line) => line.startsWith('✓ ') && line.includes('same picture'))) throw new Error('OpenFilm visual check failed');
      return inspected;
    };
    const inspected = await check([folder]);
    const still = asset.kind === 'png' ? await check([folder, '0']) : null;
    const preview = await confinedFile(project, inspected.files[0]);
    const previewName = `preview-${index}.png`;
    const previewBytes = await readFile(preview);
    await writeFile(join(output, previewName), previewBytes, { flag: 'wx', mode: 0o600 });
    const name = `asset-${index}.${asset.kind}`;
    if (asset.kind === 'png') await writeFile(join(output, name), await readFile(await confinedFile(project, still.files[0])), { flag: 'wx', mode: 0o600 });
    else {
      const rendered = await api.render([folder], { out: join(output, name), fps: 30, workers: 1 });
      if (!rendered || resolve(rendered.out) !== join(output, name)) throw new Error('OpenFilm output path mismatch');
    }
    const assetPath = await confinedFile(output, join(output, name));
    const bytes = await readFile(assetPath);
    const reportName = `look-${index}.json`;
    const report = Buffer.from(JSON.stringify({ ok: true, lines: ['✓ sampled text layout inspected', ...inspected.lines, ...(still ? still.lines : [])] }));
    if (report.length > 1024 * 1024) throw new Error('visual report too large');
    await writeFile(join(output, reportName), report, { flag: 'wx', mode: 0o600 });
    outputs.push({ path: name, sha256: hash(bytes), previews: [{ path: previewName, sha256: hash(previewBytes) }], report: { path: reportName, sha256: hash(report) } });
  }
  return { outputs };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const status = await readFile('/proc/self/status', 'utf8');
    for (const name of ['CapEff', 'CapBnd']) {
      const value = status.match(new RegExp(`^${name}:\\s*([0-9a-fA-F]+)$`, 'm'));
      if (!value || BigInt(`0x${value[1]}`) !== 0n) throw new Error('render capabilities must be dropped');
    }
    const require = createRequire(import.meta.url);
    if (require('openfilm/package.json').version !== '0.2.6') throw new Error('OpenFilm 0.2.6 required');
    const [{ look }, { render }, { open }] = await Promise.all([import('openfilm/look'), import('openfilm/render'), import('openfilm/shared')]);
    const brief = JSON.parse(await readFile('/workspace/brief.json', 'utf8'));
    const result = await renderAssets(brief, '/workspace/project', '/output', { look, render, layout: folder => inspectLayout(folder, open) });
    await writeFile('/output/render-result.json', JSON.stringify(result), { flag: 'wx', mode: 0o600 });
  } catch {
    process.stderr.write('OpenFilm render or verification failed.\n');
    process.exitCode = 1;
  }
}
