// node --test extension/test/extension.e2e.mjs
//
// The extension as it ships, loaded into Chromium: its own manifest, its
// own background worker talking to the live search.lexthedev.com, its own
// content script on a page served as x.com. The only thing faked is X's
// page, which is the markup X builds and a local two-hour video.
//
// Not part of the default run: it spends one model call on the live site.
// Run it after a deploy, and set MBS_E2E=1 to say you mean it.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { CHROMIUM } from "./chromium.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const EXT = join(here, "..");
const require = createRequire(import.meta.url);
let chromium = null;
try { ({ chromium } = require("playwright-core")); } catch { /* skipped */ }
const skip = !process.env.MBS_E2E ? "set MBS_E2E=1 to run against the live site"
  : !chromium || !CHROMIUM ? "needs playwright-core and a Chromium" : false;

const GOLD = "2116220650058846455";       // Ansem's gold video, Market Bubble shelf
const MCG = "2115115665753518109";        // an MCG stream paired with X, in sections
const NOT_OURS = "1999999999999999999";

function page(posts) {
  const article = (id, post) => `
  <article data-testid="tweet" id="${id}">
    <a href="/someone/status/${post}"><time datetime="2026-10-30T17:28:15.000Z">Oct 30</time></a>
    <div data-testid="videoPlayer"><video playsinline muted></video></div>
    <div role="group"><div data-testid="reply">r</div><div data-testid="like">l</div></div>
  </article>`;
  return `<!doctype html><html><body style="background-color: rgb(0,0,0)"><main>
    ${posts.map(([id, post]) => article(id, post)).join("")}
  </main><script>window.opened = [];
    document.querySelectorAll("article").forEach(a => a.addEventListener("click", () => opened.push(a.id)));
  </script></body></html>`;
}

function video() {
  const out = join(mkdtempSync(join(tmpdir(), "mbs-")), "long.mp4");
  execFileSync("ffmpeg", ["-v", "error", "-f", "lavfi", "-i", "color=c=black:s=16x16:r=1",
    "-t", "7200", "-c:v", "libx264", "-g", "60", "-pix_fmt", "yuv420p", out]);
  return readFileSync(out);
}

test("the packaged extension against the live site", { skip, timeout: 180000 }, async t => {
  const profile = mkdtempSync(join(tmpdir(), "mbs-profile-"));
  const context = await chromium.launchPersistentContext(profile, {
    executablePath: CHROMIUM,
    headless: true,
    args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`],
  });
  const clip = video();
  try {
    await context.route("https://x.com/**", route => {
      if (route.request().url().endsWith("/long.mp4")) {
        return route.fulfill({ body: clip, contentType: "video/mp4" });
      }
      return route.fulfill({ contentType: "text/html",
        body: page([["gold", GOLD], ["mcg", MCG], ["other", NOT_OURS]]) });
    });
    await context.route("https://example.com/**", route => route.fulfill({
      contentType: "text/html", body: page([["gold", GOLD]]) }));

    let [worker] = context.serviceWorkers();
    if (!worker) worker = await context.waitForEvent("serviceworker", { timeout: 15000 });

    await t.test("the worker that loads is ours, with no extra permissions", async () => {
      const manifest = await worker.evaluate(() => chrome.runtime.getManifest());
      assert.equal(manifest.name, "MarketBubbleSearch for X");
      assert.equal(manifest.permissions, undefined);
    });

    const tab = await context.newPage();
    await tab.goto("https://x.com/home");
    await tab.evaluate(async () => {
      const blob = await (await fetch("/long.mp4")).blob();
      const src = URL.createObjectURL(blob);
      document.querySelectorAll("video").forEach(v => { v.src = src; });
    });
    const button = id => tab.locator(`#${id} [data-mbs-button] .btn`);

    await t.test("buttons come from the live shelf, only on our posts", async () => {
      await button("gold").waitFor({ timeout: 20000 });
      await button("mcg").waitFor({ timeout: 5000 });
      assert.equal(await tab.locator("#other [data-mbs-button]").count(), 0);
    });

    await t.test("a page cannot talk to the extension", async () => {
      const reachable = await tab.evaluate(() => typeof (window.chrome && chrome.runtime &&
        chrome.runtime.sendMessage));
      assert.equal(reachable, "undefined");
    });

    await t.test("the live summary opens and a time moves the player", async () => {
      await button("gold").click();
      await tab.locator("#gold .panel .title", { hasText: "gold" }).waitFor({ timeout: 20000 });
      const first = tab.locator("#gold .panel .t").nth(2);
      const label = await first.textContent();
      const want = label.split(":").map(Number).reduce((a, b) => a * 60 + b, 0);
      await first.click();
      await tab.waitForFunction(w => Math.abs(document.querySelector("#gold video").currentTime - w) < 2,
        want, { timeout: 8000 });
      assert.deepEqual(await tab.evaluate(() => window.opened), []);
    });

    await t.test("a paired MCG post shows its title and the ask box", async () => {
      await button("mcg").click();
      await tab.locator("#mcg .panel input").waitFor({ timeout: 20000 });
      assert.ok((await tab.locator("#mcg .panel .title").textContent()).length > 5);
    });

    await t.test("one live question, answered from that recording", async () => {
      await tab.locator("#gold .panel input").fill("what did he say about the qqq since 2017");
      await tab.locator("#gold .panel .go").click();
      await tab.locator("#gold .panel .answer, #gold .panel .err").first().waitFor({ timeout: 90000 });
      const err = tab.locator("#gold .panel .err");
      // The message is read only when there is one: reading a missing
      // element waits out Playwright's 30 second default, which once made
      // a 0.3 second answer look like a 30 second one.
      if (await err.count()) assert.fail(await err.first().textContent());
      const answer = await tab.locator("#gold .panel .answer").textContent();
      assert.match(answer, /283|340|689|nasdaq|qqq/i);
    });

    await t.test("on any other site the extension does nothing", async () => {
      const other = await context.newPage();
      await other.goto("https://example.com/");
      await other.waitForTimeout(2500);
      assert.equal(await other.locator("[data-mbs-button]").count(), 0);
      await other.close();
    });
  } finally {
    await context.close();
  }
});
