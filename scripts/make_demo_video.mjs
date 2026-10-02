// A 90-second walkthrough of the live site, recorded in a real browser.
//
//     node scripts/make_demo_video.mjs            -> ~/Desktop/mbubblesearch-demo.mp4
//     node scripts/make_demo_video.mjs --local    against localhost:8765 instead
//
// Made for the AnsemHack judges, who will not explore the site themselves:
// a question, the archive that answers, the second it was said, the video
// playing from there, ThreadGuy's notes, the token dashboard, and the bot
// answering on X. Everything on screen is the real site, live, recorded as
// it happens; nothing is mocked except the title and end cards.
//
// Your installed Chrome rather than Playwright's Chromium, for the same
// reason make_og_image.mjs uses it, plus one more: Chromium has no H.264,
// and the YouTube players in the answer would not play.

import { chromium } from "playwright-core";
import { execFileSync } from "child_process";
import { mkdtempSync, readdirSync } from "fs";
import { homedir, tmpdir } from "os";
import { join } from "path";

const SITE = process.argv.includes("--local") ? "http://localhost:8765"
                                              : "https://search.lexthedev.com";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const OUT = join(homedir(), "Desktop", "mbubblesearch-demo.mp4");
const QUESTION = "what is ansem's price target for zcash";
// The bot's answer to a question asked on X, 2026-10-01. Real, and public.
const BOT_REPLY = "2105631726513061904";
const W = 1920, H = 1080;
// Headless Chrome's capture comes back about 86 pixels shorter than the
// page, and Playwright pads the gap with grey: every frame of the first take
// had a grey band along the bottom. So the page is made this much taller,
// and the top 1920x1080 of each frame is kept.
const PAD = 88;

const FONTS = `
  @font-face{font-family:"Instrument Serif";src:url("${SITE}/demo/fonts/InstrumentSerif-Regular.ttf")}
  @font-face{font-family:"InterV";src:url("${SITE}/demo/fonts/Inter-Variable.ttf")}`;

function card(eyebrow, head, sub, foot) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>${FONTS}
  *{margin:0;box-sizing:border-box}
  html{background:#0b0d10}
  body{width:${W}px;height:${H}px;background:#0b0d10;color:#eef0f3;display:flex;
    flex-direction:column;align-items:center;justify-content:center;text-align:center;
    font-family:"InterV",sans-serif;overflow:hidden}
  body::before{content:"";position:fixed;left:50%;top:-520px;width:1700px;height:1000px;
    transform:translateX(-50%);border-radius:50%;
    background:radial-gradient(circle,rgba(255,162,58,.18),transparent 65%)}
  .e{font:600 20px ui-monospace,Menlo,monospace;letter-spacing:.3em;text-transform:uppercase;
    color:#ffa23a;position:relative}
  h1{font-family:"Instrument Serif",serif;font-weight:400;font-size:112px;line-height:1.02;
    letter-spacing:-2px;margin-top:30px;position:relative;max-width:1500px}
  h1 em{color:#ffa23a;font-style:italic;padding-right:.06em}
  p{font-size:32px;color:#a7adb8;margin-top:30px;max-width:1300px;line-height:1.4;position:relative}
  .f{font:500 22px ui-monospace,Menlo,monospace;color:#6e7582;margin-top:56px;position:relative;
    letter-spacing:.06em}
  </style></head><body><div class="e">${eyebrow}</div><h1>${head}</h1><p>${sub}</p>
  <div class="f">${foot}</div></body></html>`;
}

// One caption at a time, low and centred, over whatever page is showing.
const CAPTION = (pad) => {
  window.__cap = (text) => {
    let el = document.getElementById("__cap");
    if (!el) {
      el = document.createElement("div");
      el.id = "__cap";
      el.style.cssText = `position:fixed;left:50%;bottom:${54 + pad}px;transform:translateX(-50%);`
        + "z-index:2147483647;background:rgba(8,10,14,.92);color:#fff;padding:20px 34px;"
        + "border-radius:14px;font:600 34px/1.3 -apple-system,Helvetica,sans-serif;"
        + "box-shadow:0 18px 50px rgba(0,0,0,.5);border:1px solid rgba(255,255,255,.12);"
        + "max-width:1500px;text-align:center;transition:opacity .25s";
      document.documentElement.appendChild(el);
    }
    el.style.opacity = text ? "1" : "0";
    if (text) el.textContent = text;
  };
};

const pause = (page, s) => page.waitForTimeout(s * 1000);
const cap = (page, text) => page.evaluate((t) => window.__cap && window.__cap(t), text);

async function smoothScrollTo(page, selector, offset = 140) {
  await page.evaluate(([sel, off]) => {
    const el = document.querySelector(sel);
    if (el) window.scrollTo({ top: el.getBoundingClientRect().top + scrollY - off,
                              behavior: "smooth" });
  }, [selector, offset]);
  await pause(page, 1.2);
}

const work = mkdtempSync(join(tmpdir(), "mbs-demo-"));
const browser = await chromium.launch({
  executablePath: CHROME,
  args: ["--autoplay-policy=no-user-gesture-required"],
});
const context = await browser.newContext({
  viewport: { width: W, height: H + PAD },
  recordVideo: { dir: work, size: { width: W, height: H + PAD } },
});
await context.addInitScript(CAPTION, PAD);
const page = await context.newPage();
// Seconds into the recording, for cutting dead time afterwards: the video
// starts when the page does.
const t0 = Date.now();
const now = () => (Date.now() - t0) / 1000;
const cuts = [];

try {
  // 1. What it is.
  await page.setContent(card(
    "Market Bubble Search",
    "Ask any show. Land on <em>the exact second</em>.",
    "Market Bubble, MCG Live, ThreadGuy, Elon Musk and The Record. 1,900+ hours, searchable by meaning.",
    "search.lexthedev.com"));
  await pause(page, 4);

  // 2. One question, every archive, the one that knows answers.
  await page.goto(`${SITE}/home`, { waitUntil: "networkidle" });
  await cap(page, "One question goes to all five archives.");
  await pause(page, 1.5);
  await page.click("#q");
  await page.type("#q", QUESTION, { delay: 55 });
  await pause(page, 0.4);
  await page.click("#go");
  await smoothScrollTo(page, "#verdict", 200);
  await cap(page, "The archive that knows answers, while you watch.");
  await page.waitForFunction(() => {
    const go = document.getElementById("go");
    return go && !go.disabled && document.querySelector("#answer .cite");
  }, null, { timeout: 90_000 });
  await pause(page, 1);
  await cap(page, "Every time in the answer is the second it was said.");
  await pause(page, 3.5);

  // 3. A time in the answer plays from that second.
  await cap(page, "Tap one and the video plays from there.");
  await page.click("#answer .cite");
  await page.waitForSelector("#citeplay iframe", { timeout: 15_000 });
  await smoothScrollTo(page, "#citeplay", 120);
  await pause(page, 8);

  // 4. ThreadGuy's notes.
  await page.goto(`${SITE}/threadguy`, { waitUntil: "networkidle" });
  await cap(page, "ThreadGuy: every stream since 2023, and notes on the newest.");
  await pause(page, 2);
  await smoothScrollTo(page, ".ep.noted", 160);
  await page.click(".ep.noted");
  await page.waitForSelector(".ep.noted .notes li button", { timeout: 15_000 });
  await pause(page, 1);
  await smoothScrollTo(page, ".ep.playing .notes", 120);
  await cap(page, "Each topic is a time. Tap it and the video jumps.");
  await pause(page, 2.5);
  await page.click(".ep.playing .notes li:nth-child(3) button");
  await smoothScrollTo(page, ".ep.playing .pw", 100);
  await pause(page, 5);

  // 5. The token dashboard.
  await page.goto(`${SITE}/threadguy/tokens`, { waitUntil: "networkidle" });
  await page.waitForSelector(".row .head", { timeout: 30_000 });
  await cap(page, "Every token a show discussed, by the day, with the live price.");
  await pause(page, 3);
  await smoothScrollTo(page, "#list", 120);
  await page.click(".row .head");
  await page.waitForSelector(".row.open .mo .ts", { timeout: 15_000 });
  await pause(page, 2.5);
  await page.click(".row.open .mo .ts");
  await page.waitForSelector(".row.open .mo iframe", { timeout: 15_000 });
  await cap(page, "Each moment plays where it was said.");
  await smoothScrollTo(page, ".row.open .mo .pw", 220);
  await pause(page, 5);

  // 6. The same archive, on X. X's embed takes a few seconds to arrive, and
  // those seconds are cut out of the finished video.
  const loading = now();
  await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><style>${FONTS}
    body{margin:0;width:${W}px;height:${H}px;background:#0b0d10;display:flex;align-items:center;
      justify-content:center;overflow:hidden}
    .wrap{transform-origin:center;opacity:0}</style></head><body>
    <div class="wrap"><blockquote class="twitter-tweet" data-theme="dark" data-dnt="true"
      data-cards="hidden">
    <a href="https://twitter.com/mbubbleSearch/status/${BOT_REPLY}"></a></blockquote></div>
    <script async src="https://platform.twitter.com/widgets.js"></script></body></html>`);
  await page.waitForSelector("iframe[id^='twitter-widget']", { timeout: 20_000 });
  await pause(page, 2.5);
  // As large as the whole thread allows: a fixed zoom cut off the question
  // at the top, which is the half that shows somebody asked.
  await page.evaluate((h) => {
    const wrap = document.querySelector(".wrap");
    const tall = wrap.getBoundingClientRect().height || 1;
    wrap.style.transform = `scale(${Math.min(1.7, (h - 230) / tall)})`;
    wrap.style.opacity = "1";
  }, H);
  cuts.push([loading + 0.3, now()]);
  await pause(page, 0.8);
  await cap(page, "Or ask on X. Tag @mbubbleSearch and it answers with the second.");
  await pause(page, 6);

  // 7. Where to find it.
  await page.setContent(card(
    "The AnsemHack search engine that pays you Ansem's coin",
    "search.lexthedev.com",
    "Free, no login. Or tag <b style='color:#eef0f3'>@mbubbleSearch</b> with a question.",
    "Market Bubble · MCG Live · ThreadGuy · Elon Musk · The Record"));
  await pause(page, 4.5);
} finally {
  await context.close();
  await browser.close();
}

const webm = readdirSync(work).find((f) => f.endsWith(".webm"));
// Crop to the real frame, fix the rate, then drop the cut stretches.
const drop = cuts.map(([a, b]) => `between(t,${a.toFixed(2)},${b.toFixed(2)})`).join("+");
const filters = [`crop=${W}:${H}:0:0`, "fps=30"]
  .concat(drop ? [`select='not(${drop})'`, "setpts=N/30/TB"] : []);
execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-i", join(work, webm),
  "-vf", filters.join(","),
  "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
  "-movflags", "+faststart", "-an", OUT]);
console.log(`wrote ${OUT}`);
