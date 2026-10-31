// Synthetic browser acceptance for the disposable local monitoring stack.
const { chromium } = require('playwright-core');

(async () => {
  const base = process.argv[2];
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)) throw new Error('loopback URL required');
  const browser = await chromium.launch({
    executablePath: '/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-linux64/chrome-headless-shell',
    headless: true,
    // shortcut: browser sandbox is disabled in the disposable container; upgrade for untrusted pages.
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    await page.goto(base + '/login', { waitUntil: 'domcontentloaded' });
    await page.locator('input[name="user"]').fill('workflow');
    await page.locator('input[name="password"]').fill('fixture-password');
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 30000 });
    await page.goto(base + '/d/agent-workflows-state?from=now-1m&to=now&refresh=5s', { waitUntil: 'domcontentloaded' });
    await page.getByText('Jobs by state', { exact: true }).first().waitFor({ timeout: 30000 });
    await page.getByText('Evidence gaps', { exact: true }).first().waitFor({ timeout: 30000 });
    await page.getByText('Expired leases and quota pauses', { exact: true }).first().waitFor({ timeout: 30000 });
    const queued = page.getByText('queued', { exact: true }).first();
    await queued.waitFor({ timeout: 30000 });
    await page.waitForTimeout(3000);
    const errors = page.getByText(/data source not found|failed to fetch|error querying/i);
    if (await errors.count()) throw new Error('dashboard reports a datasource error');
    await page.screenshot({ path: '/output/dashboard-desktop.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(1000);
    const evidence = page.locator('p').filter({ hasText: 'Confirm stopped workers and investigate state in the private control plane.' }).first();
    if (!(await evidence.count())) throw new Error('full evidence explanation is missing');
    const clipped = await evidence.evaluate(element => {
      const bottom = element.getBoundingClientRect().bottom;
      for (let parent = element.parentElement; parent && parent.clientWidth < innerWidth * 0.9; parent = parent.parentElement) {
        const overflow = getComputedStyle(parent).overflowY;
        if (['hidden', 'clip', 'auto', 'scroll'].includes(overflow) && bottom > parent.getBoundingClientRect().bottom + 1) return true;
      }
      return false;
    });
    if (clipped) throw new Error('mobile evidence explanation is clipped');
    await page.screenshot({ path: '/output/dashboard-mobile.png', fullPage: true });
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
