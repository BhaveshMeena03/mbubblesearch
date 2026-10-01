// Render a social preview card to its PNG (1200x630).
//   node scripts/make_og_image.mjs          -> og-card.html  -> og-image.png
//   node scripts/make_og_image.mjs mcg      -> og-mcg-card.html -> og-mcg.png
//
// The MCG card used to be a PNG with no source in the repo, so its numbers
// could only go stale, and did: it was still claiming 458 episodes and 444
// hours under a reply that said 634 and 1,002. A card with a source is one
// that can be re-rendered when the archive grows.
import { chromium } from "playwright-core";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

// Every archive page renders from ONE template, og-universal-card.html,
// which carries nothing that goes stale: no counts, no dates, no latest
// recordings. Cards with numbers went wrong within days (The Record's said
// 30 recordings with 35 live), and an archive that grows every morning
// cannot have its card redrawn every morning. `?p=` picks the page.
const universal = (p, png) => ({ html: "og-universal-card.html", query: `?p=${p}`, png });
const PAGES = {
  home: universal("home", "og-home.png"),
  podcast: universal("podcast", "og-broadcast.png"),
  mcg: universal("mcg", "og-mcg.png"),
  elon: universal("elon", "og-musk.png"),
  finance: universal("finance", "og-finance.png"),
  threadguy: universal("threadguy", "og-threadguy.png"),
};
const CARDS = {
  default: { html: "og-card.html", png: "og-image.png" },
  llms: { html: "og-llms-card.html", png: "og-llms.png" },
  rewards: { html: "og-rewards-card.html", png: "og-rewards.png" },
  ...PAGES,
};

//   node scripts/make_og_image.mjs pages   -> all six archive pages at once
const which = process.argv[2] || "default";
const todo = which === "pages" ? Object.values(PAGES) : [CARDS[which]];
if (!todo[0]) {
  console.error(`unknown card ${which}; known: pages, ${Object.keys(CARDS).join(", ")}`);
  process.exit(2);
}

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const browser = await chromium.launch({ executablePath: CHROME });
// 2x. The card is 1200x630 in CSS pixels because that is the size every
// social scraper expects, but capturing it at 1x produces exactly 1200
// device pixels, which is soft on any retina screen and softer again
// after a platform re-encodes it. Rendering at deviceScaleFactor 2 keeps
// the layout identical and hands over 2400x1260 of actual detail.
const page = await browser.newPage({
  viewport: { width: 1200, height: 630 },
  deviceScaleFactor: 2,
});
for (const card of todo) {
  await page.goto("file://" + join(root, "demo", card.html) + (card.query || ""));
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(root, "demo", card.png) });
  console.log(`wrote demo/${card.png}`);
}
await browser.close();
