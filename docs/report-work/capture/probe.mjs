// Мини-тест: можно ли запустить headless Edge через puppeteer-core в этой среде.
import puppeteer from 'puppeteer-core';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--window-size=383,900'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 360, height: 800, deviceScaleFactor: 2 });
  await page.goto('data:text/html,<h1 style="font-family:sans-serif">probe 360 ok</h1>');
  await page.screenshot({ path: process.argv[2] || 'probe.png' });
  await browser.close();
  console.log('PROBE OK');
})().catch((e) => {
  console.error('PROBE FAIL:', e.message);
  process.exit(1);
});
