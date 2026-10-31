// node --test extension/test/browser.test.mjs
//
// The content script, unchanged, on a page built the way X builds a
// timeline, in a real Chromium. The background worker is replaced by a
// stand-in that answers each message with whatever the test sets, so the
// replies can be hostile on purpose. Needs playwright-core, its Chromium
// and ffmpeg; skips itself where any is missing.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, existsSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const here = dirname(fileURLToPath(import.meta.url));
const EXT = join(here, "..");
const require = createRequire(import.meta.url);

let chromium = null;
try { ({ chromium } = require("playwright-core")); } catch { /* skipped below */ }
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
const launch = options => chromium.launch({ executablePath: CHROMIUM, ...options });

let ffmpeg = true;
try { execFileSync("ffmpeg", ["-version"], { stdio: "ignore" }); } catch { ffmpeg = false; }
const skip = !chromium ? "playwright-core is not installed" : !ffmpeg ? "ffmpeg is not installed" : false;

const OURS = "2116220650058846455";
const QUOTED = "2115904746355851479";
const OTHER = "1999999999999999999";

// Two hours of one black frame a second: long enough to seek to 1:39:33.
function makeVideo() {
  const dir = mkdtempSync(join(tmpdir(), "mbs-"));
  const out = join(dir, "long.mp4");
  execFileSync("ffmpeg", ["-v", "error", "-f", "lavfi", "-i", "color=c=black:s=16x16:r=1",
    "-t", "7200", "-c:v", "libx264", "-g", "60", "-pix_fmt", "yuv420p", out]);
  return readFileSync(out);
}

function article(id, post, { video = true, quoted = null } = {}) {
  return `
  <article data-testid="tweet" id="${id}">
    <a href="/someone/status/${post}"><time datetime="2026-10-30T17:28:15.000Z">Oct 30</time></a>
    <div data-testid="tweetText">a post</div>
    ${quoted ? `<div role="link"><a href="/blknoiz06/status/${quoted}"><time>Oct 29</time></a>
      <div data-testid="videoPlayer"><video playsinline muted></video></div></div>` : ""}
    ${video && !quoted ? `<div data-testid="videoPlayer"><video playsinline muted></video></div>` : ""}
    <div role="group" aria-label="1 reply"><div data-testid="reply">r</div>
      <div data-testid="retweet">rt</div><div data-testid="like">l</div></div>
  </article>`;
}

const PAGE = `<!doctype html><html><head><meta charset="utf-8"></head>
<body style="background-color: rgb(0, 0, 0); color: white">
<main>
  ${article("ours", OURS)}
  ${article("other", OTHER)}
  ${article("novideo", OURS, { video: false })}
  ${article("quote", OTHER, { quoted: QUOTED })}
</main>
<script>
  // What X does: a click anywhere in a post opens it, and its shortcuts
  // listen on the whole page.
  window.opened = [];
  window.shortcuts = [];
  document.querySelectorAll("article").forEach(a =>
    a.addEventListener("click", () => window.opened.push(a.id)));
  document.addEventListener("keydown", e => window.shortcuts.push(e.key));
</script>
</body></html>`;

const HOSTILE = '<img src=x onerror="window.pwned=1">';

function postReply(post, extra = {}) {
  return {
    ok: true, data: { post: {
      post, archive: "podcast", archiveName: "Market Bubble",
      title: "Ansem on X: gold " + HOSTILE, show: "Ansem on X", publishedAt: "2026-10-30",
      summary: "**TL;DR** — " + HOSTILE + " a top\n\n**Topics**\n- [0:01:40] blow off top\n" +
               "- [1:39:33] the worst chart\n- [9:59:59] past the end\n- [0:05:00] no second given",
      times: { "0:01:40": 100, "1:39:33": 5973, "9:59:59": 35999 },
      seekable: true, page: "https://search.lexthedev.com/demo/podcast.html#ep-x-" + post,
      ...extra } }
  };
}

// The stand-in for background.js: chrome.runtime.sendMessage answered from
// window.__replies, keyed by message type.
const CHROME_SHIM = `
  window.__sent = [];
  window.__replies = {};
  window.chrome = { runtime: {
    lastError: undefined,
    sendMessage(message, callback) {
      window.__sent.push(message);
      const reply = window.__replies[message.type];
      setTimeout(() => callback(typeof reply === "function" ? reply(message) : reply), 30);
    }
  } };`;

async function open(browser, video, replies) {
  const page = await browser.newPage();
  await page.route("https://x.com/**", route => {
    const url = route.request().url();
    if (url.endsWith("/long.mp4")) return route.fulfill({ body: video, contentType: "video/mp4" });
    return route.fulfill({ body: PAGE, contentType: "text/html" });
  });
  await page.addInitScript(CHROME_SHIM);
  await page.goto("https://x.com/home");
  await page.evaluate(async replies => {
    for (const [k, v] of Object.entries(replies)) window.__replies[k] = v;
    // Blob URLs are seekable without a range-capable server.
    const blob = await (await fetch("/long.mp4")).blob();
    const src = URL.createObjectURL(blob);
    document.querySelectorAll("video").forEach(v => { v.src = src; });
  }, replies);
  await page.addScriptTag({ path: join(EXT, "lib.js") });
  await page.addScriptTag({ path: join(EXT, "content.js") });
  return page;
}

const SHELF = { ok: true, data: { posts: { [OURS]: "podcast", [QUOTED]: "podcast" } } };

test("the content script on an X-shaped page", { skip }, async t => {
  const video = makeVideo();
  const browser = await launch({ headless: true });
  try {
    const page = await open(browser, video, { shelf: SHELF });
    const button = id => page.locator(`#${id} [data-mbs-button] .btn`);

    await t.test("a button on our video posts only, quoted ones included", async () => {
      await button("ours").waitFor();
      await button("quote").waitFor();
      assert.equal(await page.locator("#other [data-mbs-button]").count(), 0);
      assert.equal(await page.locator("#novideo [data-mbs-button]").count(), 0);
      assert.equal(await page.locator("#quote [data-mbs-button]").getAttribute("data-mbs-button"), QUOTED);
      assert.equal(await page.locator("[data-mbs-button]").count(), 2);
    });

    await t.test("the shelf is the only thing asked for until a click", async () => {
      assert.deepEqual(await page.evaluate(() => window.__sent.map(m => m.type)), ["shelf"]);
    });

    await t.test("opening the panel neither opens the post nor runs any markup", async () => {
      await page.evaluate(r => { window.__replies.post = r; }, postReply(OURS));
      await button("ours").click();
      await page.locator("#ours .panel .title").waitFor();
      assert.deepEqual(await page.evaluate(() => window.opened), []);
      assert.equal(await page.evaluate(() => window.pwned), undefined);
      const title = await page.locator("#ours .panel .title").textContent();
      assert.ok(title.includes("<img src=x onerror="), "shown as the characters it is");
      assert.equal(await page.locator("#ours .panel img").count(), 0);
      assert.deepEqual(await page.evaluate(() => window.__sent.at(-1)), { type: "post", post: OURS });
    });

    await t.test("only times the server gave a second become buttons", async () => {
      const times = await page.locator("#ours .panel .t").allTextContents();
      assert.deepEqual(times, ["0:01:40", "1:39:33", "9:59:59"]);
      const text = await page.locator("#ours .panel").textContent();
      assert.ok(text.includes("0:05:00 no second given"));
    });

    await t.test("a time moves the post's own player to that second", async () => {
      await page.locator("#ours .panel .t", { hasText: "1:39:33" }).click();
      await page.waitForFunction(() => {
        const v = document.querySelector("#ours video");
        return v && Math.abs(v.currentTime - 5973) < 2;
      }, null, { timeout: 8000 });
      assert.equal(await page.evaluate(() => document.querySelector("#other video").currentTime), 0,
        "no other post's player moves");
      assert.deepEqual(await page.evaluate(() => window.opened), []);
    });

    await t.test("a time past the end says so and moves nothing", async () => {
      const before = await page.evaluate(() => document.querySelector("#ours video").currentTime);
      await page.locator("#ours .panel .t", { hasText: "9:59:59" }).click();
      await page.locator("#ours .panel .note", { hasText: "past the end" }).waitFor();
      const after = await page.evaluate(() => document.querySelector("#ours video").currentTime);
      assert.ok(Math.abs(after - before) < 3);
    });

    await t.test("typing a question never reaches X's shortcuts", async () => {
      const input = page.locator("#ours .panel input");
      await input.click();
      await page.keyboard.type("j k l r what about gold");
      assert.deepEqual(await page.evaluate(() => window.shortcuts), []);
      assert.equal(await input.inputValue(), "j k l r what about gold");
    });

    await t.test("an answer renders as text with its own seekable times", async () => {
      await page.evaluate(h => {
        window.__replies.ask = m => ({ ok: true, data: { answer: {
          answer: "Around 0:01:40 he said " + h + " the top. **Bold** at 1:39:33.",
          refused: false, times: { "0:01:40": 100 } } } });
      }, HOSTILE);
      await page.locator("#ours .panel .go").click();
      await page.locator("#ours .panel .answer").waitFor();
      const sent = await page.evaluate(() => window.__sent.at(-1));
      assert.deepEqual(sent, { type: "ask", post: OURS, question: "j k l r what about gold" });
      assert.equal(await page.evaluate(() => window.pwned), undefined);
      assert.deepEqual(await page.locator("#ours .panel .answer .t").allTextContents(), ["0:01:40"]);
      assert.equal(await page.locator("#ours .panel .answer strong").first().textContent(), "Bold");
      assert.deepEqual(await page.evaluate(() => window.opened), []);
    });

    await t.test("each failure reads as what happened", async () => {
      const cases = { busy: "Busy right now", limit: "resets at midnight UTC",
                      slow: "took too long", offline: "Couldn't reach", nonsense: "Something went wrong" };
      for (const [code, words] of Object.entries(cases)) {
        await page.evaluate(c => { window.__replies.ask = { ok: false, code: c }; }, code);
        await page.locator("#ours .panel input").fill("again " + code);
        await page.locator("#ours .panel .go").click();
        await page.locator("#ours .panel .err", { hasText: words }).waitFor({ timeout: 4000 });
      }
    });

    await t.test("a blank question sends nothing", async () => {
      const before = await page.evaluate(() => window.__sent.length);
      await page.locator("#ours .panel input").fill("    ");
      await page.locator("#ours .panel .go").click();
      await page.waitForTimeout(300);
      assert.equal(await page.evaluate(() => window.__sent.length), before);
    });

    await t.test("the panel closes, and the button opens it again", async () => {
      await page.locator("#ours .panel .x").click();
      assert.equal(await page.locator("#ours .panel").count(), 0);
      await button("ours").click();
      await page.locator("#ours .panel .title").waitFor();
      await button("ours").click();
      assert.equal(await page.locator("#ours .panel").count(), 0);
    });

    await t.test("the button comes back when X re-renders the post", async () => {
      await page.evaluate(() => document.querySelector("#ours [data-mbs-button]").remove());
      await button("ours").waitFor({ timeout: 4000 });
      assert.equal(await page.locator("#ours [data-mbs-button]").count(), 1);
    });

    await t.test("a post scrolled in later gets its button", async () => {
      await page.evaluate(html => document.querySelector("main").insertAdjacentHTML("beforeend", html),
        article("late", OURS).replace(/<video playsinline muted><\/video>/, ""));
      await page.evaluate(() => {
        const box = document.querySelector("#late [role='group']");
        box.insertAdjacentHTML("beforebegin", '<div data-testid="videoPlayer"></div>');
      });
      await button("late").waitFor({ timeout: 4000 });
    });

    await t.test("a summary-less post still offers the ask box", async () => {
      await page.evaluate(r => { window.__replies.post = r; },
        postReply(QUOTED, { summary: "", times: {}, seekable: false }));
      await button("quote").click();
      await page.locator("#quote .panel input").waitFor();
      const text = await page.locator("#quote .panel").textContent();
      assert.ok(text.includes("No summary for this one yet"));
      assert.ok(text.includes("can't be matched"));
    });

    await t.test("an unknown post or a dead worker shows a message, not a crash", async () => {
      await page.evaluate(() => { window.__replies.post = { ok: false, code: "unknown" }; });
      await page.locator("#quote .panel .x").click();
      await button("quote").click();
      await page.locator("#quote .panel .err", { hasText: "isn't in the archive" }).waitFor();
      await page.evaluate(() => { window.__replies.post = undefined; });
      await page.locator("#quote .panel .x").click();
      await button("quote").click();
      await page.locator("#quote .panel .err", { hasText: "Something went wrong" }).waitFor();
    });

    await t.test("the site link is the one built from the archive", async () => {
      await page.evaluate(r => { window.__replies.post = r; }, postReply(OURS));
      await button("ours").click();
      const href = await page.locator("#ours .panel .foot a").getAttribute("href");
      assert.equal(href, "https://search.lexthedev.com/demo/podcast.html#ep-x-" + OURS);
      assert.equal(await page.locator("#ours .panel .foot a").getAttribute("rel"), "noopener noreferrer");
    });

    await page.close();
  } finally {
    await browser.close();
  }
});

test("a shelf that never arrives leaves the page untouched", { skip }, async () => {
  const browser = await launch({ headless: true });
  try {
    const page = await open(browser, Buffer.from(""), { shelf: { ok: false, code: "offline" } });
    await page.waitForTimeout(800);
    assert.equal(await page.locator("[data-mbs-button]").count(), 0);
    assert.deepEqual(await page.evaluate(() => window.__sent.map(m => m.type)), ["shelf"],
      "asked once, not in a loop");
  } finally {
    await browser.close();
  }
});

test("a light X gets a light panel", { skip }, async () => {
  const browser = await launch({ headless: true });
  try {
    const page = await open(browser, makeVideo(), { shelf: SHELF, post: postReply(OURS) });
    await page.evaluate(() => { document.body.style.backgroundColor = "rgb(255, 255, 255)"; });
    await page.locator("#ours [data-mbs-button] .btn").click();
    await page.locator("#ours .panel .title").waitFor();
    const bg = await page.locator("#ours .panel").evaluate(n => getComputedStyle(n).backgroundColor);
    assert.equal(bg, "rgb(247, 249, 249)");
  } finally {
    await browser.close();
  }
});
