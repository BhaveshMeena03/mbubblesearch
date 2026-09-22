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

const CARDS = {
  default: { html: "og-card.html", png: "og-image.png" },
  mcg: { html: "og-mcg-card.html", png: "og-mcg.png" },
};

const which = process.argv[2] || "default";
const card = CARDS[which];
if (!card) {
  console.error(`unknown card ${which}; known: ${Object.keys(CARDS).join(", ")}`);
  process.exit(2);
}

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const browser = await chromium.launch({ executablePath: CHROME });
const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
await page.goto("file://" + join(root, "demo", card.html));
await page.waitForTimeout(400);
await page.screenshot({ path: join(root, "demo", card.png) });
await browser.close();
console.log(`wrote demo/${card.png}`);
