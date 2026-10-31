// Shared by the browser tests: where the Chromium is.

import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

// The Chromium Playwright already downloaded, whichever build that is, or
// MBS_CHROMIUM. Never a download from here.
function findChromium() {
  if (process.env.MBS_CHROMIUM) return process.env.MBS_CHROMIUM;
  const cache = join(process.env.HOME || "", "Library/Caches/ms-playwright");
  if (!existsSync(cache)) return undefined;
  for (const dir of readdirSync(cache).filter(d => /^chromium-\d+$/.test(d)).sort().reverse()) {
    for (const app of ["chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
                       "chrome-mac/Chromium.app/Contents/MacOS/Chromium", "chrome-linux/chrome"]) {
      if (existsSync(join(cache, dir, app))) return join(cache, dir, app);
    }
  }
  return undefined;
}
export const CHROMIUM = findChromium();
