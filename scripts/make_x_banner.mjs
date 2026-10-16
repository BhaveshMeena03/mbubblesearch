// Render demo/x-banner.html to demo/x-banner.png, the @mbubbleSearch header.
//   node scripts/make_x_banner.mjs
// or the DexScreener header for $MBS, demo/dex-banner.html, the same size:
//   node scripts/make_x_banner.mjs dex-banner
//
// X wants 1500x500. Rendered at 3x and handed over at 4500x1500: X
// downscales on its own, a 1x upload comes back soft on retina, and the
// extra pass costs nothing here.
import { chromium } from "playwright-core";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const name = process.argv[2] || "x-banner";
const browser = await chromium.launch({ executablePath: CHROME });
const page = await browser.newPage({
  viewport: { width: 1500, height: 500 },
  deviceScaleFactor: 3,
});
await page.goto("file://" + join(root, "demo", `${name}.html`));
await page.waitForTimeout(500);
await page.screenshot({ path: join(root, "demo", `${name}.png`) });
await browser.close();
console.log(`wrote demo/${name}.png (4500x1500, for a 1500x500 slot)`);
