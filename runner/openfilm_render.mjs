/* Fixed local OpenFilm render entrypoint; the supervisor supplies isolation. */
import { createHash } from 'node:crypto';
import { lstat, readFile, readdir, realpath, writeFile } from 'node:fs/promises';
import { isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const MAX_BYTES = 100 * 1024 * 1024;
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');

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
  if (!brief || !Array.isArray(brief.assets) || brief.assets.length < 1 || brief.assets.length > 10 || !api || typeof api.look !== 'function' || typeof api.render !== 'function') throw new Error('invalid render request');
  const project = await realpath(projectRoot);
  const output = await realpath(outputRoot);
  if ((await readdir(output)).length) throw new Error('output directory must be empty');
  const outputs = [];
  for (const [index, asset] of brief.assets.entries()) {
    if (!asset || !['png', 'mp4'].includes(asset.kind)) throw new Error('unsupported asset kind');
    const folder = brief.assets.length === 1 ? project : join(project, 'projects', String(index));
    await confinedFile(project, join(folder, 'film.html'));
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
    const report = Buffer.from(JSON.stringify({ ok: true, lines: [...inspected.lines, ...(still ? still.lines : [])] }));
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
    const [{ look }, { render }] = await Promise.all([import('openfilm/look'), import('openfilm/render')]);
    const brief = JSON.parse(await readFile('/workspace/brief.json', 'utf8'));
    const result = await renderAssets(brief, '/workspace/project', '/output', { look, render });
    await writeFile('/output/render-result.json', JSON.stringify(result), { flag: 'wx', mode: 0o600 });
  } catch {
    process.stderr.write('OpenFilm render or verification failed.\n');
    process.exitCode = 1;
  }
}
