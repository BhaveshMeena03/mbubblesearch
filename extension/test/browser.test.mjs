// node --test extension/test/browser.test.mjs
//
// The content script, unchanged, on a page built the way X builds a
// timeline, in a real Chromium. The background worker is replaced by a
// stand-in that answers each message with whatever the test sets, so the
// replies can be hostile on purpose (background.test.js checks the worker
// itself). Needs playwright-core, its Chromium and ffmpeg; skips itself
// where any is missing.

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
try { ({ chromium } = require("playwright-core")); } catch { /* skipped below */ }
const launch = options => chromium.launch({ executablePath: CHROMIUM, ...options });
let ffmpeg = true;
try { execFileSync("ffmpeg", ["-version"], { stdio: "ignore" }); } catch { ffmpeg = false; }
const skip = !chromium ? "playwright-core is not installed"
  : !CHROMIUM ? "no Chromium downloaded for Playwright" : !ffmpeg ? "ffmpeg is not installed" : false;

const OURS = "2116220650058846455";
const ALSO_OURS = "2115904746355851479";
const OTHER = "1999999999999999999";

// Two hours of one black frame a second: long enough to seek to 1:39:33.
let VIDEO = null;
function video() {
  if (VIDEO) return VIDEO;
  const out = join(mkdtempSync(join(tmpdir(), "mbs-")), "long.mp4");
  execFileSync("ffmpeg", ["-v", "error", "-f", "lavfi", "-i", "color=c=black:s=16x16:r=1",
    "-t", "7200", "-c:v", "libx264", "-g", "60", "-pix_fmt", "yuv420p", out]);
  VIDEO = readFileSync(out);
  return VIDEO;
}

// A post the way X writes one: every <a> carries role="link", as X's do,
// and a quoted post is a <div role="link"> card with its own time and,
// here, its own video. (Fixture made to match real X on 31 Oct 2026, after
// a rule that passed here missed every post there.)
function article(id, post, { video = true, quoting = null, quotedVideo = true } = {}) {
  return `
  <article data-testid="tweet" id="${id}">
    <a role="link" href="/someone"><span>someone</span></a>
    <a role="link" href="/someone/status/${post}"><time datetime="2026-10-30T17:28:15.000Z">Oct 30</time></a>
    <div data-testid="tweetText">a post <a role="link" href="https://t.co/x">t.co/x</a></div>
    ${video ? `<div data-testid="videoPlayer"><video class="own" playsinline muted></video></div>` : ""}
    <div role="link" tabindex="0" class="broadcast-title">the broadcast's title</div>
    ${quoting ? `<div role="link" tabindex="0" class="card">
      <a role="link" href="/blknoiz06/status/${quoting}"><time>Oct 29</time></a>
      ${quotedVideo ? `<div data-testid="videoPlayer"><video class="quoted" playsinline muted></video></div>` : ""}
    </div>` : ""}
    <div role="group" aria-label="1 reply"><div data-testid="reply">r</div>
      <div data-testid="retweet">rt</div><div data-testid="like">l</div></div>
  </article>`;
}

const TIMELINE = [
  article("ours", OURS),
  article("other", OTHER),
  article("novideo", OURS, { video: false }),
  // Someone's own clip, quoting one of ours: not ours, so no button.
  article("quote", OTHER, { quoting: ALSO_OURS }),
  // One of ours quoting someone else's video: ours, and only its own
  // video may move.
  article("ownquote", ALSO_OURS, { quoting: OTHER }),
].join("");

function page(body) {
  return `<!doctype html><html><head><meta charset="utf-8"></head>
<body style="background-color: rgb(0, 0, 0); color: white">
<main>${body}</main>
<script>
  // What X does: a click anywhere in a post opens it, and its shortcuts
  // listen on the whole page.
  window.opened = [];
  window.shortcuts = [];
  document.addEventListener("click", e => {
    const a = e.target.closest && e.target.closest("article");
    if (a) window.opened.push(a.id);
  });
  document.addEventListener("keydown", e => window.shortcuts.push(e.key));
</script>
</body></html>`;
}

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
    id: "mbs-test",
    lastError: undefined,
    sendMessage(message, callback) {
      window.__sent.push(message);
      const reply = window.__replies[message.type];
      setTimeout(() => callback(typeof reply === "function" ? reply(message) : reply), 30);
    }
  } };`;

async function open(browser, body, replies) {
  const tab = await browser.newPage();
  await tab.route("https://x.com/**", route => {
    if (route.request().url().endsWith("/long.mp4")) {
      return route.fulfill({ body: video(), contentType: "video/mp4" });
    }
    return route.fulfill({ body: page(body), contentType: "text/html" });
  });
  await tab.addInitScript(CHROME_SHIM);
  await tab.goto("https://x.com/home");
  await tab.evaluate(async replies => {
    for (const [k, v] of Object.entries(replies)) window.__replies[k] = v;
    // Blob URLs are seekable without a range-capable server.
    const blob = await (await fetch("/long.mp4")).blob();
    const src = URL.createObjectURL(blob);
    document.querySelectorAll("video").forEach(v => { v.src = src; });
  }, replies);
  await tab.addScriptTag({ path: join(EXT, "lib.js") });
  await tab.addScriptTag({ path: join(EXT, "content.js") });
  return tab;
}

const SHELF = { ok: true, data: { posts: { [OURS]: "podcast", [ALSO_OURS]: "podcast" } } };
const at = (tab, selector) => tab.evaluate(s => document.querySelector(s).currentTime, selector);

test("the content script on an X-shaped page", { skip }, async t => {
  const browser = await launch({ headless: true });
  try {
    const tab = await open(browser, TIMELINE, { shelf: SHELF });
    const button = id => tab.locator(`#${id} [data-mbs-button] .btn`);

    await t.test("a button on our own video posts only", async () => {
      await button("ours").waitFor();
      await button("ownquote").waitFor();
      for (const id of ["other", "novideo", "quote"]) {
        assert.equal(await tab.locator(`#${id} [data-mbs-button]`).count(), 0, id);
      }
      assert.equal(await tab.locator("[data-mbs-button]").count(), 2);
    });

    await t.test("a quote post of ours never borrows our summary", async () => {
      // Found in review: it used to get our button, and our times played
      // the quoting post's own clip.
      assert.equal(await tab.locator("#quote [data-mbs-button]").count(), 0);
    });

    await t.test("the shelf is the only thing asked for until a click", async () => {
      assert.deepEqual(await tab.evaluate(() => window.__sent.map(m => m.type)), ["shelf"]);
    });

    await t.test("a click from a script on the page does nothing", async () => {
      await tab.evaluate(() => document.querySelector("#ours [data-mbs-button]")
        .shadowRoot.querySelector(".btn").click());
      await tab.waitForTimeout(300);
      assert.equal(await tab.locator("#ours .panel").count(), 0);
      assert.deepEqual(await tab.evaluate(() => window.__sent.map(m => m.type)), ["shelf"]);
    });

    await t.test("opening the panel neither opens the post nor runs any markup", async () => {
      await tab.evaluate(r => { window.__replies.post = r; }, postReply(OURS));
      await button("ours").click();
      await tab.locator("#ours .panel .title").waitFor();
      assert.deepEqual(await tab.evaluate(() => window.opened), []);
      assert.equal(await tab.evaluate(() => window.pwned), undefined);
      const title = await tab.locator("#ours .panel .title").textContent();
      assert.ok(title.includes("<img src=x onerror="), "shown as the characters it is");
      assert.equal(await tab.locator("#ours .panel img").count(), 0);
      assert.deepEqual(await tab.evaluate(() => window.__sent.at(-1)), { type: "post", post: OURS });
    });

    await t.test("the button wears the site's mark at X's icon size", async () => {
      const icon = await tab.locator("#ours [data-mbs-button] .btn svg").evaluate(svg => ({
        shapes: [...svg.children].map(n => n.tagName),
        size: [getComputedStyle(svg).width, getComputedStyle(svg).height],
      }));
      assert.deepEqual(icon.shapes, ["circle", "path", "path"], "lens, handle, play");
      assert.deepEqual(icon.size, ["18.75px", "18.75px"]);
    });

    await t.test("the panel is named as the site writes it, with its mark and a way there", async () => {
      const name = tab.locator("#ours .panel .src");
      assert.equal(await name.textContent(), "MarketBubbleSearch");
      assert.equal(await name.locator("svg > *").count(), 4, "the store icon's disc, handle, lens and play");
      assert.equal(await name.getAttribute("href"), "https://search.lexthedev.com/home");
      assert.equal(await name.getAttribute("rel"), "noopener noreferrer");
    });

    await t.test("the summary opens under a TL;DR heading, with no dash in it", async () => {
      const headings = await tab.locator("#ours .panel h4").allTextContents();
      assert.deepEqual(headings, ["TL;DR", "Topics"]);
      assert.doesNotMatch(await tab.locator("#ours .panel").textContent(), /[–—]/);
    });

    await t.test("only times the server gave a second become buttons", async () => {
      const times = await tab.locator("#ours .panel .t").allTextContents();
      assert.deepEqual(times, ["0:01:40", "1:39:33", "9:59:59"]);
      const text = await tab.locator("#ours .panel").textContent();
      assert.ok(text.includes("0:05:00 no second given"));
    });

    await t.test("a time moves the post's own player to that second", async () => {
      await tab.locator("#ours .panel .t", { hasText: "1:39:33" }).click();
      await tab.waitForFunction(() => Math.abs(document.querySelector("#ours video").currentTime - 5973) < 2,
        null, { timeout: 8000 });
      assert.equal(await at(tab, "#other video"), 0, "no other post's player moves");
      assert.deepEqual(await tab.evaluate(() => window.opened), []);
    });

    await t.test("a time past the end says so and moves nothing", async () => {
      const before = await at(tab, "#ours video");
      await tab.locator("#ours .panel .t", { hasText: "9:59:59" }).click();
      await tab.locator("#ours .panel .note", { hasText: "past the end" }).waitFor();
      assert.ok(Math.abs((await at(tab, "#ours video")) - before) < 3);
    });

    await t.test("typing a question never reaches X's shortcuts", async () => {
      const input = tab.locator("#ours .panel input");
      await input.click();
      await tab.keyboard.type("j k l r what about gold");
      assert.deepEqual(await tab.evaluate(() => window.shortcuts), []);
      assert.equal(await input.inputValue(), "j k l r what about gold");
    });

    await t.test("an answer renders as text with its own seekable times", async () => {
      await tab.evaluate(h => {
        window.__replies.ask = () => ({ ok: true, data: { answer: {
          answer: "Around 0:01:40 he said " + h + " the top. **Bold** at 1:39:33.",
          refused: false, times: { "0:01:40": 100 } } } });
      }, HOSTILE);
      await tab.locator("#ours .panel .go").click();
      await tab.locator("#ours .panel .answer").waitFor();
      const sent = await tab.evaluate(() => window.__sent.at(-1));
      assert.deepEqual(sent, { type: "ask", post: OURS, question: "j k l r what about gold" });
      assert.equal(await tab.evaluate(() => window.pwned), undefined);
      assert.deepEqual(await tab.locator("#ours .panel .answer .t").allTextContents(), ["0:01:40"]);
      assert.equal(await tab.locator("#ours .panel .answer strong").first().textContent(), "Bold");
      assert.deepEqual(await tab.evaluate(() => window.opened), []);
    });

    await t.test("a question submitted by a script on the page is not sent", async () => {
      const before = await tab.evaluate(() => window.__sent.length);
      await tab.evaluate(() => {
        const root = document.querySelector("#ours [data-mbs-panel]").shadowRoot;
        root.querySelector("input").value = "spend it for me";
        root.querySelector("form").requestSubmit();
      });
      await tab.waitForTimeout(300);
      assert.equal(await tab.evaluate(() => window.__sent.length), before);
    });

    await t.test("each failure reads as what happened", async () => {
      const cases = { busy: "Busy right now", limit: "resets at midnight UTC",
                      slow: "took too long", offline: "Couldn't reach", nonsense: "Something went wrong" };
      for (const [code, words] of Object.entries(cases)) {
        await tab.evaluate(c => { window.__replies.ask = { ok: false, code: c }; }, code);
        await tab.locator("#ours .panel input").fill("again " + code);
        await tab.locator("#ours .panel .go").click();
        await tab.locator("#ours .panel .err", { hasText: words }).waitFor({ timeout: 4000 });
      }
    });

    await t.test("a blank question sends nothing", async () => {
      const before = await tab.evaluate(() => window.__sent.length);
      await tab.locator("#ours .panel input").fill("    ");
      await tab.locator("#ours .panel .go").click();
      await tab.waitForTimeout(300);
      assert.equal(await tab.evaluate(() => window.__sent.length), before);
    });

    await t.test("the panel closes, and the button opens it again", async () => {
      await tab.locator("#ours .panel .x").click();
      assert.equal(await tab.locator("#ours .panel").count(), 0);
      await button("ours").click();
      await tab.locator("#ours .panel .title").waitFor();
      await button("ours").click();
      assert.equal(await tab.locator("#ours .panel").count(), 0);
    });

    await t.test("only a post's own video moves, never the one it quotes", async () => {
      await tab.evaluate(r => { window.__replies.post = r; }, postReply(ALSO_OURS));
      await button("ownquote").click();
      await tab.locator("#ownquote .panel .t", { hasText: "0:01:40" }).click();
      await tab.waitForFunction(() => Math.abs(document.querySelector("#ownquote video.own").currentTime - 100) < 2,
        null, { timeout: 8000 });
      assert.equal(await at(tab, "#ownquote video.quoted"), 0);
      await tab.locator("#ownquote .panel .x").click();
    });

    await t.test("the button comes back when X re-renders the post", async () => {
      await tab.evaluate(() => document.querySelector("#ours [data-mbs-button]").remove());
      await button("ours").waitFor({ timeout: 4000 });
      assert.equal(await tab.locator("#ours [data-mbs-button]").count(), 1);
    });

    await t.test("a post X recycles for another post loses the old button", async () => {
      await tab.evaluate(r => { window.__replies.post = r; }, postReply(OURS));
      await button("ours").click();
      await tab.locator("#ours .panel").waitFor();
      // X reuses the nodes: same article, now another post.
      await tab.evaluate(id => {
        document.querySelector("#ours a[href*='/status/']").setAttribute("href", "/x/status/" + id);
      }, OTHER);
      await tab.waitForFunction(() => !document.querySelector("#ours [data-mbs-button]")
        && !document.querySelector("#ours [data-mbs-panel]"), null, { timeout: 4000 });
      await tab.evaluate(id => {
        document.querySelector("#ours a[href*='/status/']").setAttribute("href", "/x/status/" + id);
      }, ALSO_OURS);
      await tab.waitForFunction(id => document.querySelector("#ours [data-mbs-button]")
        ?.getAttribute("data-mbs-button") === id, ALSO_OURS, { timeout: 4000 });
    });

    await t.test("with no player on the post, nothing on the page is clicked", async () => {
      await tab.evaluate(html => document.querySelector("main").insertAdjacentHTML("beforeend", html),
        article("bare", OURS, { video: false }));
      await tab.evaluate(() => {
        // Where X's play button sits, something that is not one.
        const box = document.querySelector("#bare [role='group']");
        box.insertAdjacentHTML("beforebegin", '<div data-testid="videoPlayer"><div role="button" id="bait">x</div></div>');
        window.baited = 0;
        document.getElementById("bait").addEventListener("click", () => { window.baited++; });
      });
      await tab.evaluate(r => { window.__replies.post = r; }, postReply(OURS));
      await button("bare").click();
      await tab.locator("#bare .panel .t", { hasText: "0:01:40" }).click();
      await tab.locator("#bare .panel .note", { hasText: "Start the video" }).waitFor();
      assert.equal(await tab.evaluate(() => window.baited), 0);
    });

    await t.test("a summary-less post still offers the ask box", async () => {
      await tab.evaluate(r => { window.__replies.post = r; },
        postReply(ALSO_OURS, { summary: "", times: {}, seekable: false }));
      await button("ownquote").click();
      await tab.locator("#ownquote .panel input").waitFor();
      const text = await tab.locator("#ownquote .panel").textContent();
      assert.ok(text.includes("No summary for this one yet"));
      assert.ok(text.includes("can't be matched"));
      await tab.locator("#ownquote .panel .x").click();
    });

    await t.test("an unknown post or a dead worker shows a message, not a crash", async () => {
      await tab.evaluate(() => { window.__replies.post = { ok: false, code: "unknown" }; });
      await button("ownquote").click();
      await tab.locator("#ownquote .panel .err", { hasText: "isn't in the archive" }).waitFor();
      await tab.locator("#ownquote .panel .x").click();
      await tab.evaluate(() => { window.__replies.post = undefined; });
      await button("ownquote").click();
      await tab.locator("#ownquote .panel .err", { hasText: "Something went wrong" }).waitFor();
      await tab.locator("#ownquote .panel .x").click();
    });

    await t.test("after the extension updates, the old script says to refresh", async () => {
      // What an update leaves in a tab that was open: no id, and a
      // sendMessage that throws.
      await tab.evaluate(() => {
        window.__send = window.chrome.runtime.sendMessage;
        window.chrome.runtime.id = undefined;
        window.chrome.runtime.sendMessage = () => {
          throw new Error("Extension context invalidated.");
        };
      });
      await button("ownquote").click();
      await tab.locator("#ownquote .panel .err", { hasText: "Refresh this page" }).waitFor();
      assert.equal(await tab.locator("#ownquote .panel .err", { hasText: "Something went wrong" }).count(), 0);
      await tab.locator("#ownquote .panel .x").click();
      await tab.evaluate(() => {
        window.chrome.runtime.id = "mbs-test";
        window.chrome.runtime.sendMessage = window.__send;
      });
    });

    await t.test("the site link is the one built from the archive", async () => {
      await tab.evaluate(r => { window.__replies.post = r; }, postReply(ALSO_OURS));
      await button("ownquote").click();
      const href = await tab.locator("#ownquote .panel .foot a").getAttribute("href");
      assert.equal(href, "https://search.lexthedev.com/demo/podcast.html#ep-x-" + ALSO_OURS);
      assert.equal(await tab.locator("#ownquote .panel .foot a").getAttribute("rel"), "noopener noreferrer");
    });

    await tab.close();
  } finally {
    await browser.close();
  }
});

test("a page with no video posts never asks for the shelf", { skip }, async () => {
  const browser = await launch({ headless: true });
  try {
    const tab = await open(browser, article("text", OURS, { video: false }), { shelf: SHELF });
    await tab.waitForTimeout(1000);
    assert.deepEqual(await tab.evaluate(() => window.__sent), []);
    // A video post scrolls in: now it asks, once.
    await tab.evaluate(html => document.querySelector("main").insertAdjacentHTML("beforeend", html),
      article("late", OURS));
    await tab.locator("#late [data-mbs-button]").waitFor({ timeout: 4000 });
    assert.deepEqual(await tab.evaluate(() => window.__sent.map(m => m.type)), ["shelf"]);
  } finally {
    await browser.close();
  }
});

test("a shelf that never arrives leaves the page untouched, and is not asked for in a loop", { skip }, async () => {
  const browser = await launch({ headless: true });
  try {
    const tab = await open(browser, TIMELINE, { shelf: { ok: false, code: "offline" } });
    await tab.waitForTimeout(800);
    // Keep the page busy: every change re-scans, and none may re-ask.
    for (let i = 0; i < 5; i++) {
      await tab.evaluate(i => document.querySelector("main")
        .insertAdjacentHTML("beforeend", `<div>${i}</div>`), i);
      await tab.waitForTimeout(350);
    }
    assert.equal(await tab.locator("[data-mbs-button]").count(), 0);
    assert.deepEqual(await tab.evaluate(() => window.__sent.map(m => m.type)), ["shelf"]);
  } finally {
    await browser.close();
  }
});

test("a light X gets a light panel", { skip }, async () => {
  const browser = await launch({ headless: true });
  try {
    const tab = await open(browser, TIMELINE, { shelf: SHELF, post: postReply(OURS) });
    await tab.evaluate(() => { document.body.style.backgroundColor = "rgb(255, 255, 255)"; });
    await tab.locator("#ours [data-mbs-button] .btn").click();
    await tab.locator("#ours .panel .title").waitFor();
    const bg = await tab.locator("#ours .panel").evaluate(n => getComputedStyle(n).backgroundColor);
    assert.equal(bg, "rgb(247, 249, 249)");
    const green = await tab.locator("#ours .panel .src").evaluate(n => getComputedStyle(n).color);
    assert.equal(green, "rgb(11, 125, 83)", "a green that can be read on a light page");
  } finally {
    await browser.close();
  }
});

// A clip of a show: its own minute of video, and the whole show elsewhere.
const CLIP = "2116428709150478840";
const SHOW = "https://x.com/i/broadcasts/1dxYlaZbXWQJX";

function clipReply(extra = {}) {
  return postReply(CLIP, {
    kind: "clip", title: "Market Bubble Episode 24", show: "", publishedAt: "2026-10-29",
    clip: { at: "1:39:53", until: "1:41:12", watch: SHOW + "?t=5993",
            before: "lab diamonds " + HOSTILE, after: "How does MicroStrategy look?",
            afterAt: "1:41:18", afterWatch: SHOW + "?t=6078" },
    summary: "**TL;DR** — the hack\n\n**Topics**\n- [0:04:31] macro\n- [1:40:05] the clip's moment\n" +
             "- [2:00:00] a link that is not ours",
    times: { "1:40:05": 12 },
    links: { "0:04:31": SHOW + "?t=271", "1:40:05": SHOW + "?t=6005", "2:00:00": "javascript:window.pwned=1" },
    ...extra });
}

test("a clip says where it is from and what came after the cut", { skip }, async t => {
  const browser = await launch({ headless: true });
  try {
    const tab = await open(browser, article("clip", CLIP), {
      shelf: { ok: true, data: { posts: { [CLIP]: "podcast" } } }, post: clipReply() });
    await tab.locator("#clip [data-mbs-button] .btn").click();
    await tab.locator("#clip .panel .title").waitFor();
    const panel = tab.locator("#clip .panel");

    await t.test("it names the show and the moment, linked to the whole show there", async () => {
      assert.equal(await panel.locator(".from").textContent(), "This clip is from");
      assert.equal(await panel.locator(".title").textContent(), "Market Bubble Episode 24");
      const at = panel.locator(".at a.t");
      assert.equal(await at.textContent(), "1:39:53");
      assert.equal(await at.getAttribute("href"), SHOW + "?t=5993");
      assert.equal(await at.getAttribute("target"), "_blank");
      assert.equal(await at.getAttribute("rel"), "noopener noreferrer");
      assert.match(await panel.locator(".at").textContent(), /^1:39:53 to 1:41:12 in the show\. /);
      assert.equal(await panel.locator(".at a.watch").getAttribute("href"), SHOW + "?t=5993");
    });

    await t.test("what was said either side is shown as text", async () => {
      assert.deepEqual((await panel.locator("h4").allTextContents()).slice(0, 2),
        ["Just before the clip", "Right after the clip"]);
      assert.ok((await panel.locator(".before").textContent()).includes("<img src=x"));
      assert.equal(await tab.evaluate(() => window.pwned), undefined);
      const after = panel.locator("p.said:not(.before)");
      assert.equal(await after.locator("a.t").getAttribute("href"), SHOW + "?t=6078");
      assert.equal(await after.textContent(), "1:41:18 How does MicroStrategy look?…");
    });

    await t.test("the episode's summary is folded away until asked for", async () => {
      const whole = panel.locator("details");
      assert.equal(await whole.evaluate(d => d.open), false);
      await whole.locator("summary").click();
      assert.equal(await whole.evaluate(d => d.open), true);
      assert.deepEqual(await tab.evaluate(() => window.opened), []);
    });

    await t.test("a time inside the clip plays in the clip", async () => {
      await panel.locator("details button.t", { hasText: "1:40:05" }).click();
      await tab.waitForFunction(() => Math.abs(document.querySelector("#clip video").currentTime - 12) < 2,
        null, { timeout: 8000 });
    });

    await t.test("a time outside it is a link to the show, and a bad link is only words", async () => {
      assert.equal(await panel.locator("details a.t", { hasText: "0:04:31" }).getAttribute("href"),
        SHOW + "?t=271");
      assert.equal(await panel.locator("a[href^='javascript']").count(), 0);
      assert.ok((await panel.locator("details").textContent()).includes("2:00:00 a link that is not ours"));
    });

    await t.test("a clip of an uploaded video opens it, and claims no second", async () => {
      const upload = "https://x.com/blknoiz06/status/2115418459765113280?t=1719";
      await tab.evaluate(r => { window.__replies.post = r; }, clipReply({
        clip: { at: "0:28:39", until: "0:30:05", watch: upload, before: "b", after: "a",
                afterAt: "0:30:09", afterWatch: "" }, links: {} }));
      await tab.locator("#clip [data-mbs-button] .btn").click();
      await tab.locator("#clip [data-mbs-button] .btn").click();
      await tab.locator("#clip .panel .at").waitFor();
      const at = tab.locator("#clip .panel .at");
      assert.equal(await at.locator("a.t").count(), 0, "the time is words");
      assert.equal(await at.locator("strong").textContent(), "0:28:39");
      assert.equal(await at.locator("a.watch").textContent(), "Open the full video");
      assert.equal(await at.locator("a.watch").getAttribute("href"), upload);
      assert.equal(await tab.locator("#clip .panel p.said:not(.before) a").count(), 0);
      await tab.evaluate(r => { window.__replies.post = r; }, clipReply());
      await tab.locator("#clip [data-mbs-button] .btn").click();
      await tab.locator("#clip [data-mbs-button] .btn").click();
      await tab.locator("#clip .panel .at a.t").waitFor();
    });

    await t.test("the ask box asks about the episode", async () => {
      assert.equal(await panel.locator("input").getAttribute("placeholder"), "Ask about the episode");
      await tab.evaluate(show => {
        window.__replies.ask = { ok: true, data: { answer: {
          answer: "He said it at 1:40:05 and again at 2:41:30.", refused: false,
          times: { "1:40:05": 12 }, links: { "2:41:30": show + "?t=9690" } } } };
      }, SHOW);
      await panel.locator("input").fill("what about bitcoin");
      await panel.locator(".go").click();
      await panel.locator(".answer").waitFor();
      assert.deepEqual(await panel.locator(".answer button.t").allTextContents(), ["1:40:05"]);
      assert.equal(await panel.locator(".answer a.t").getAttribute("href"), SHOW + "?t=9690");
    });
    await tab.close();
  } finally {
    await browser.close();
  }
});

// Found in review on 1 Nov 2026, both reproduced before they were fixed.
test("a post X reuses for something else loses the button and panel it had", { skip }, async () => {
  const browser = await launch({ headless: true });
  try {
    const tab = await open(browser, TIMELINE, { shelf: SHELF, post: postReply(OURS) });
    await tab.locator("#ours [data-mbs-button] .btn").click();
    await tab.locator("#ours .panel .title").waitFor();
    // The same nodes, now someone's text post: another id, no video.
    await tab.evaluate(other => {
      const post = document.querySelector("#ours");
      post.querySelector("a[href*='/status/']").setAttribute("href", "/someone/status/" + other);
      post.querySelector("[data-testid='videoPlayer']").remove();
    }, OTHER);
    await tab.waitForFunction(() => !document.querySelector("#ours [data-mbs-button],#ours [data-mbs-panel]"),
      null, { timeout: 4000 });
    // And an ad: the same post, its timestamp link gone.
    await tab.evaluate(() => document.querySelector("#ownquote a[href*='/status/'] time").parentElement.remove());
    await tab.waitForFunction(() => !document.querySelector("#ownquote [data-mbs-button]"), null, { timeout: 4000 });
  } finally {
    await browser.close();
  }
});

test("a page that steers the reader's clicks can't send a question they didn't type", { skip }, async () => {
  const browser = await launch({ headless: true });
  try {
    const tab = await open(browser, TIMELINE, { shelf: SHELF, post: postReply(OURS),
      ask: { ok: true, data: { answer: { answer: "a", refused: false, times: {}, links: {} } } } });
    await tab.locator("#ours [data-mbs-button] .btn").click();
    await tab.locator("#ours .panel input").waitFor();
    // The page writes its own question and lays Ask over everything.
    await tab.evaluate(() => {
      const root = document.querySelector("#ours [data-mbs-panel]").shadowRoot;
      root.querySelector("input").value = "question chosen by the page";
      Object.assign(root.querySelector(".go").style, { position: "fixed", inset: "0", zIndex: "2147483647" });
    });
    await tab.mouse.click(200, 200);                        // the reader's own click
    await tab.keyboard.press("Enter");
    await tab.waitForTimeout(400);
    assert.deepEqual(await tab.evaluate(() => window.__sent.map(m => m.type)), ["shelf", "post"]);
    // A question the reader typed still goes, on the same real click.
    await tab.evaluate(() => {
      const root = document.querySelector("#ours [data-mbs-panel]").shadowRoot;
      root.querySelector(".go").removeAttribute("style");
      root.querySelector("input").value = "";
    });
    await tab.locator("#ours .panel input").fill("what about gold");
    await tab.locator("#ours .panel .go").click();
    await tab.waitForFunction(() => window.__sent.some(m => m.type === "ask"), null, { timeout: 4000 });
    assert.equal((await tab.evaluate(() => window.__sent.at(-1))).question, "what about gold");
    // Typed, then rewritten by the page: refused.
    await tab.evaluate(() => {
      document.querySelector("#ours [data-mbs-panel]").shadowRoot.querySelector("input").value = "swapped";
    });
    const before = await tab.evaluate(() => window.__sent.length);
    await tab.locator("#ours .panel .go").click();
    await tab.waitForTimeout(300);
    assert.equal(await tab.evaluate(() => window.__sent.length), before);
  } finally {
    await browser.close();
  }
});

// Our link posts: a quote and the show's broadcast at the second, which X
// draws as a card (card.wrapper) holding the broadcast's link, with no
// <video> until it is played. Shaped from x.com on 2 Nov 2026.
function linkPost(id, post, { quoted = false } = {}) {
  const card = `<div data-testid="card.wrapper"><a role="link" href="/i/broadcasts/1pJdRDqeOYnKW">
    <span>threadguy</span><span>MARKET OPEN: Binance after BIGGEST HACK EVER</span></a></div>`;
  return `
  <article data-testid="tweet" id="${id}">
    <a role="link" href="/mbubbleSearch/status/${post}"><time datetime="2026-11-01T17:32:19.000Z">Nov 1</time></a>
    <div data-testid="tweetText">mando can't see $150k bitcoin before quantum gets sorted.</div>
    ${quoted ? `<div role="link" tabindex="0" class="card"><a role="link" href="/someone/status/1888888888888888888"><time>Nov 1</time></a>${card}</div>` : card}
    <div role="group" aria-label="1 reply"><div data-testid="reply">r</div><div data-testid="like">l</div></div>
  </article>`;
}

test("our link post gets the button and points at its moment", { skip }, async () => {
  const LINKED = "2116946451729432772";
  const QUOTER = "2116946451729439999";
  const browser = await launch({ headless: true });
  try {
    const tab = await open(browser, linkPost("link", LINKED) + linkPost("quoter", QUOTER, { quoted: true }), {
      shelf: { ok: true, data: { posts: { [LINKED]: "threadguy", [QUOTER]: "threadguy" } } },
      post: postReply(LINKED, {
        kind: "link", archive: "threadguy", archiveName: "ThreadGuy", title: "MARKET OPEN: Binance",
        show: "", link: { at: "1:40:10", watch: "https://x.com/i/broadcasts/1pJdRDqeOYnKW?t=6007" },
        summary: "**Topics**\n- [0:10:00] binance", times: {},
        links: { "0:10:00": "https://x.com/i/broadcasts/1pJdRDqeOYnKW?t=597" } }) });
    await tab.locator("#link [data-mbs-button] .btn").waitFor();
    assert.equal(await tab.locator("#quoter [data-mbs-button]").count(), 0,
      "a broadcast inside a quoted post is not the post's own");
    await tab.locator("#link [data-mbs-button] .btn").click();
    const panel = tab.locator("#link .panel");
    await panel.locator(".title").waitFor();
    assert.equal(await panel.locator(".from").textContent(), "This post points to");
    assert.equal(await panel.locator(".at a.t").getAttribute("href"),
      "https://x.com/i/broadcasts/1pJdRDqeOYnKW?t=6007");
    assert.match(await panel.locator(".at").textContent(), /^1:40:10 in the show\. Watch it there$/);
    assert.equal(await panel.locator("li a.t").getAttribute("href"),
      "https://x.com/i/broadcasts/1pJdRDqeOYnKW?t=597");
    assert.equal(await panel.locator("input").getAttribute("placeholder"), "Ask about the episode");
    assert.deepEqual(await tab.evaluate(() => window.opened), []);
  } finally {
    await browser.close();
  }
});

// Our older link posts point at a show's YouTube copy. X draws the link as
// a card whose <a> goes to t.co and is labelled "youtube.com <title>", or,
// when it is not the post's card, as text reading youtube.com/watch?v=….
// Shaped from x.com on 2 Nov 2026.
function youTubePost(id, post, { label = "youtube.com BlackRock Told Tristan Thompson",
                                 text = "", quoted = false } = {}) {
  const card = label ? `<div data-testid="card.wrapper"><a role="link" href="https://t.co/VK7RKsYs4S"
    aria-label="${label}"><span>BlackRock Told Tristan Thompson</span></a></div>
    <a role="link" href="https://t.co/VK7RKsYs4S">From youtube.com</a>` : "";
  return `
  <article data-testid="tweet" id="${id}">
    <a role="link" href="/mbubbleSearch/status/${post}"><time datetime="2026-09-26T18:46:00.000Z">Sep 26</time></a>
    <div data-testid="tweetText">around 16:16 a guest recalled BlackRock ${text}</div>
    ${quoted ? `<div role="link" tabindex="0" class="card"><a role="link" href="/someone/status/1888888888888888888"><time>Sep 26</time></a>${card}</div>` : card}
    <div role="group" aria-label="1 reply"><div data-testid="reply">r</div><div data-testid="like">l</div></div>
  </article>`;
}

test("our older posts that link YouTube get the button too", { skip }, async () => {
  const CARD = "2103556826294079702";
  const TEXT = "2103226841083564495";
  const QUOTER = "2103556826294079999";
  const ELSEWHERE = "2103556826294078888";
  const browser = await launch({ headless: true });
  try {
    const tab = await open(browser,
      youTubePost("card", CARD) +
      youTubePost("text", TEXT, { label: "", text: '<a role="link" href="https://t.co/a">youtube.com/watch?v=FFFnEnXzgEY…</a>' }) +
      youTubePost("quoter", QUOTER, { quoted: true }) +
      youTubePost("elsewhere", ELSEWHERE, { label: "search.lexthedev.com MarketBubbleSearch" }), {
        shelf: { ok: true, data: { posts: { [CARD]: "podcast", [TEXT]: "podcast",
                                            [QUOTER]: "podcast", [ELSEWHERE]: "podcast" } } },
        post: postReply(CARD, {
          kind: "link", title: "Market Bubble Episode 12", show: "",
          link: { at: "0:16:16", watch: "https://x.com/i/broadcasts/1dxYlaZbXWQJX?t=976" },
          summary: "**Topics**\n- [0:10:00] macro", times: {},
          links: { "0:10:00": "https://www.youtube.com/watch?v=wksdB2BgW3g&t=600s" } }) });
    await tab.locator("#card [data-mbs-button] .btn").waitFor();
    await tab.locator("#text [data-mbs-button] .btn").waitFor();
    assert.equal(await tab.locator("#quoter [data-mbs-button]").count(), 0,
      "a YouTube card inside a quoted post is not the post's own");
    assert.equal(await tab.locator("#elsewhere [data-mbs-button]").count(), 0,
      "a card to anywhere but YouTube is not a show");
    await tab.locator("#card [data-mbs-button] .btn").click();
    const panel = tab.locator("#card .panel");
    await panel.locator(".title").waitFor();
    assert.equal(await panel.locator(".from").textContent(), "This post points to");
    assert.equal(await panel.locator(".at a.t").getAttribute("href"),
      "https://x.com/i/broadcasts/1dxYlaZbXWQJX?t=976");
    assert.equal(await panel.locator("li a.t").getAttribute("href"),
      "https://www.youtube.com/watch?v=wksdB2BgW3g&t=600s");
    assert.deepEqual(await tab.evaluate(() => window.opened), []);
  } finally {
    await browser.close();
  }
});
