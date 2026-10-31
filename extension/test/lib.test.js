// node --test extension/test/
// The extension's logic, run as shipped: lib.js is loaded unchanged.

const test = require("node:test");
const assert = require("node:assert/strict");
const MBS = require("../lib.js");

const OURS = "2116220650058846455";

test("a post id comes out of every shape of status link X writes", () => {
  for (const href of [
    "/blknoiz06/status/" + OURS,
    "/blknoiz06/status/" + OURS + "/analytics",
    "/blknoiz06/status/" + OURS + "/photo/1",
    "/blknoiz06/status/" + OURS + "?s=20",
    "https://x.com/blknoiz06/status/" + OURS,
    "https://twitter.com/blknoiz06/status/" + OURS + "#m",
  ]) {
    assert.equal(MBS.postIdFromHref(href), OURS, href);
  }
});

test("anything that is not an x.com status link has no post id", () => {
  for (const href of [
    "/i/broadcasts/1dxYlaZbXWQJX",
    "https://evil.example/blknoiz06/status/" + OURS,
    "https://x.com.evil.example/blknoiz06/status/" + OURS,
    "javascript:alert(1)//x.com/a/status/" + OURS,
    "/thishandleiswaytoolong/status/" + OURS,
    "/blknoiz06/status/1234",
    "/blknoiz06/status/" + "9".repeat(26),
    "/blknoiz06/status/" + OURS + "abc",
    "",
    null,
    42,
  ]) {
    assert.equal(MBS.postIdFromHref(href), null, String(href));
  }
});

test("the shelf keeps only post ids and known archives", () => {
  const given = JSON.parse('{"posts": {"' + OURS + '": "podcast", "12345": "mcg",' +
    ' "abc": "podcast", "123456789": "elon", "1234567": "<b>",' +
    ' "__proto__": "podcast", "7654321": ["podcast"]}}');
  const posts = MBS.cleanShelf(given);
  assert.deepEqual(Object.keys(posts).sort(), [OURS, "12345"].sort());
  assert.equal(Object.getPrototypeOf(posts), Object.prototype, "no prototype pollution");
  assert.equal(MBS.cleanShelf(null), null);
  assert.equal(MBS.cleanShelf({ posts: [] }), null);
  assert.equal(MBS.cleanShelf({ posts: "x" }), null);
});

test("the panel's link is rebuilt from the archive, never taken from the reply", () => {
  const post = MBS.cleanPost({
    post: OURS, archive: "podcast", episode_id: "x-" + OURS,
    page: "javascript:alert(document.cookie)", title: "t",
  });
  assert.equal(post.page, "https://search.lexthedev.com/demo/podcast.html#ep-x-" + OURS);
  const odd = MBS.cleanPost({ post: OURS, archive: "podcast", episode_id: "x-1\"><img src=x>" });
  assert.equal(odd.page, "https://search.lexthedev.com/demo/podcast.html");
  const mcg = MBS.cleanPost({ post: OURS, archive: "mcg", episode_id: "2NK_MBlFvtM" });
  assert.equal(mcg.page, "https://search.lexthedev.com/demo/mcg.html");
});

test("a reply that is not ours, or not this post, is refused", () => {
  assert.equal(MBS.cleanPost(null), null);
  assert.equal(MBS.cleanPost({ post: "12x45", archive: "podcast" }), null);
  assert.equal(MBS.cleanPost({ post: OURS, archive: "elon" }), null);
  assert.equal(MBS.cleanPost({ post: OURS, archive: "constructor" }), null);
});

test("every string is capped and every field has its type", () => {
  const post = MBS.cleanPost({
    post: OURS, archive: "rasmr", title: "x".repeat(5000), summary: { not: "text" },
    show: 7, published_at: "2026-10-30T00:00", seekable: "yes",
  });
  assert.equal(post.title.length, 300);
  assert.equal(post.summary, "");
  assert.equal(post.show, "");
  assert.equal(post.publishedAt, "");
  assert.equal(post.seekable, false);
});

test("only whole, sane seconds keyed by a written time survive", () => {
  const times = MBS.cleanTimes({
    "1:39:33": 5973, "0:00": 0, "14:25": 865.5, "2:00": -1, "3:00": "180",
    "99:00:00": 999999, "javascript": 5, "4:00": 1e9, "__proto__": 1,
  });
  assert.deepEqual(times, { "1:39:33": 5973, "0:00": 0 });
  assert.deepEqual(MBS.cleanTimes([1, 2]), {});
  assert.deepEqual(MBS.cleanTimes("1:00"), {});
});

test("a time is a button only when the server gave its X second", () => {
  const parts = MBS.pieces("at 1:39:33 and 2:00:00, ratio 3:1", { "1:39:33": 5973 });
  assert.deepEqual(parts, [
    { text: "at ", bold: false },
    { time: "1:39:33", seconds: 5973, bold: false },
    { text: " and 2:00:00, ratio 3:1", bold: false },
  ]);
});

test("splitting into pieces never loses or invents a character", () => {
  const lines = [
    "Around 10:25 to 14:20 in the video, QQQ went 157 to 689.",
    "**TL;DR** — gold at 5600, bottom 3,100 to 3,500",
    "<img src=x onerror=alert(1)> at 0:36",
    "nothing here",
    "**bold 1:00** tail 2:00",
  ];
  for (const line of lines) {
    const parts = MBS.pieces(line, { "10:25": 625, "0:36": 36, "1:00": 60, "2:00": 120 });
    const joined = parts.map((p) => p.text || p.time).join("");
    assert.equal(joined, line.replace(/\*\*/g, ""), line);
  }
});

test("markup in a summary stays text", () => {
  const blocks = MBS.blocks("<script>alert(1)</script> at [0:36]\n- <b>x</b> [1:00]", { "0:36": 36 });
  assert.equal(blocks[0].parts[0].text, "<script>alert(1)</script> at ");
  assert.equal(blocks[0].parts[1].time, "0:36");
  assert.equal(blocks[1].kind, "item");
  assert.equal(blocks[1].parts[0].text, "<b>x</b> 1:00");
});

test("the summaries' own markdown becomes headings, items and paragraphs", () => {
  const md = "**TL;DR** — short\n\n**Topics**\n- [0:00:36] gold history\n- [0:01:40] blow off top\n\nplain";
  const blocks = MBS.blocks(md, { "0:00:36": 36, "0:01:40": 100 });
  assert.deepEqual(blocks.map((b) => b.kind), ["para", "heading", "item", "item", "para"]);
  assert.equal(blocks[1].parts[0].text, "Topics");
  assert.deepEqual(blocks[2].parts[0], { time: "0:00:36", seconds: 36, bold: false });
});

test("a question is trimmed, flattened and capped", () => {
  assert.equal(MBS.cleanQuestion("  what   did\n ansem say "), "what did ansem say");
  assert.equal(MBS.cleanQuestion("q".repeat(900)).length, 500);
  assert.equal(MBS.cleanQuestion(null), "");
  assert.equal(MBS.cleanQuestion("   "), "");
});

test("dates read the way the account writes them", () => {
  assert.equal(MBS.longDate("2026-10-30"), "october 30, 2026");
  assert.equal(MBS.longDate("30 oct"), "");
});

test("the extension talks to one origin, over https", () => {
  assert.equal(MBS.API, "https://search.lexthedev.com");
});
