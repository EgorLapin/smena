// Съёмка недостающих экранов (локальный стенд, временная БД).
//环境变量: BASE (http://127.0.0.1:PORT), OUT (папка результатов).
import process from 'node:process';
import puppeteer from 'puppeteer-core';

const BASE = process.env.BASE || 'http://127.0.0.1:8012';
const OUT = process.env.OUT || '.';
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';

const browser = await puppeteer.launch({
  executablePath: EDGE,
  headless: true,
  userDataDir: process.env.PROFILE || `${OUT}/.edge-profile`,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
});

async function newPage() {
  const ctx = await browser.createBrowserContext(); // «чистый браузер» под другую роль
  const page = await ctx.newPage();
  await page.setViewport({ width: 360, height: 800, deviceScaleFactor: 2 });
  return page;
}

async function login(page, user) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  await page.type('input[name="login"]', user);
  await page.type('input[name="password"]', 'demo-pass-123');
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'domcontentloaded' }),
    page.click('form[action="/login"] button[type="submit"]'),
  ]);
}

const clickNav = (page, selector) =>
  Promise.all([
    page.waitForNavigation({ waitUntil: 'domcontentloaded' }),
    page.click(selector),
  ]);

// --- Администратор Анна: открыть смену, добавить вопрос, сдать ---------------
const anna = await newPage();
await login(anna, 'demo-anna');
console.log('anna ->', anna.url());

await clickNav(anna, 'form[action="/shifts/open"] button[type="submit"]');
console.log('смена открыта:', anna.url());

await anna.goto(`${BASE}/issues/new`, { waitUntil: 'domcontentloaded' });
await anna.type('input[name="place"]', 'Комната 12');
await anna.type('textarea[name="description"]', 'Передать дополнительное одеяло');
await anna.type('textarea[name="next_action"]', 'Принести до 20:00');
await clickNav(anna, 'form button[type="submit"].btn');
console.log('вопрос сохранён:', anna.url());
await anna.screenshot({ path: `${OUT}/20-journal-open.png`, fullPage: true });

await clickNav(anna, 'a[href$="/handover"]');
console.log('страница сдачи:', anna.url());
await anna.screenshot({ path: `${OUT}/21-handover-check.png`, fullPage: true });

await clickNav(anna, 'form[action$="/handover"] button[type="submit"]');
console.log('сдано, снимок:', anna.url());
await anna.screenshot({ path: `${OUT}/23-snapshot-submitted.png`, fullPage: true });

// --- Администратор Иван: принять, показать flash ------------------------------
const ivan = await newPage();
await login(ivan, 'demo-ivan');
await clickNav(ivan, 'a[href^="/handovers/"]');
console.log('иван на снимке:', ivan.url());
await clickNav(ivan, 'form[action$="/accept"] button[type="submit"]');
console.log('иван принял:', ivan.url());
await ivan.screenshot({ path: `${OUT}/22-journal-accepted.png`, fullPage: true });

await browser.close();
console.log('CAPTURE OK');
