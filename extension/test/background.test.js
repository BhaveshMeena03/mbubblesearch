// node --test extension/test/background.test.js
// background.js as shipped, run in a sandbox with a fake chrome and a fake
// fetch, so every reply the server could give and every sender that could
// knock can be tried. The browser tests replace this worker with a stand-in;
// these are what check the worker itself.

const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const EXT = path.join(__dirname, "..");
const US = "ourextensionidourextensionidabcd";
const FROM_X = { id: US, origin: "https://x.com", url: "https://x.com/home" };
const POST = "2116220650058846455";

function worker(respond) {
  const listeners = [];
  const requests = [];
  const sandbox = {
    console, URL, JSON, Promise, Object, Array, Date, Error,
    AbortController,
    setTimeout: (...a) => setTimeout(...a),
    clearTimeout: (...a) => clearTimeout(...a),
    fetch: (url, init) => {
      requests.push({ url, init });
      return respond(url, init);
    },
    chrome: { runtime: { id: US, onMessage: { addListener: fn => listeners.push(fn) } } },
  };
  sandbox.importScripts = name =>
    vm.runInContext(fs.readFileSync(path.join(EXT, name), "utf8"), sandbox);
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(EXT, "background.js"), "utf8"), sandbox);
  assert.equal(listeners.length, 1, "one listener, nothing else registered");
  return {
    requests,
    send(message, sender = FROM_X) {
      // Chrome serialises a reply on its way to the page; so does this.
      return new Promise(resolve => {
        const later = listeners[0](message, sender,
          reply => resolve(JSON.parse(JSON.stringify(reply))));
        if (!later) resolve("no reply");
      });
    },
  };
}

const json = (status, body) => Promise.resolve({
  status, ok: status >= 200 && status < 300,
  text: () => Promise.resolve(typeof body === "string" ? body : JSON.stringify(body)),
});

const POST_BODY = { post: POST, archive: "podcast", archive_name: "Market Bubble",
  episode_id: "x-" + POST, title: "t", summary: "s", times: { "0:36": 36 }, seekable: true };

test("only this extension's own script on x.com gets an answer", async () => {
  const w = worker(() => json(200, { posts: {} }));
  for (const sender of [
    { id: "someotherextensionidabcdefghijkl", origin: "https://x.com" },
    { id: US, origin: "https://evil.example" },
    { id: US, origin: "https://x.com.evil.example" },
    { id: US, url: "https://twitter.com/home" },
    { id: US },
    null,
  ]) {
    assert.equal(await w.send({ type: "shelf" }, sender), "no reply", JSON.stringify(sender));
  }
  assert.equal(w.requests.length, 0, "nothing fetched for a stranger");
});

test("only the three known messages, well formed, are acted on", async () => {
  const w = worker(() => json(200, POST_BODY));
  for (const message of [
    null, "shelf", { type: "fetch", url: "https://evil.example" }, { type: "post" },
    { type: "post", post: "../../etc" }, { type: "post", post: POST + "/x" },
    { type: "ask", post: POST, question: "   " }, { type: "ask", post: POST },
    { type: "ask", post: "abc", question: "q" }, { type: 7 },
  ]) {
    assert.equal(await w.send(message), "no reply", JSON.stringify(message));
  }
  assert.equal(w.requests.length, 0);
});

test("every request goes to our origin, at a fixed path, with nothing of the user's", async () => {
  const w = worker((url) => url.endsWith("/v1/x/shelf") ? json(200, { posts: { [POST]: "podcast" } })
    : url.endsWith("/v1/x/ask") ? json(200, { answer: "a", times: {} }) : json(200, POST_BODY));
  await w.send({ type: "shelf" });
  await w.send({ type: "post", post: POST });
  await w.send({ type: "ask", post: POST, question: "  what   about gold " });
  assert.deepEqual(w.requests.map(r => r.url), [
    "https://search.lexthedev.com/v1/x/shelf",
    "https://search.lexthedev.com/v1/x/post/" + POST,
    "https://search.lexthedev.com/v1/x/ask",
  ]);
  for (const { init } of w.requests) {
    assert.equal(init.credentials, "omit");
    assert.equal(init.referrerPolicy, "no-referrer");
    assert.equal(init.redirect, "error");
  }
  assert.deepEqual(JSON.parse(w.requests[2].init.body), { post: POST, question: "what about gold" });
  assert.equal(w.requests[2].init.cache, "no-store");
});

test("the shelf is fetched once an hour, not on every page", async () => {
  const w = worker(() => json(200, { posts: { [POST]: "podcast" } }));
  for (let i = 0; i < 5; i++) {
    const reply = await w.send({ type: "shelf" });
    assert.deepEqual(reply.data.posts, { [POST]: "podcast" });
  }
  assert.equal(w.requests.length, 1);
});

test("each failure comes back as the code the panel knows", async () => {
  const cases = [
    [() => json(429, {}), "busy"],
    [() => json(503, {}), "limit"],
    [() => json(404, {}), "unknown"],
    [() => json(500, {}), "failed"],
    [() => json(302, {}), "failed"],
    [() => json(200, "not json {"), "failed"],
    [() => json(200, "x".repeat(3 * 1024 * 1024)), "failed"],
    [() => json(200, { post: "999999", archive: "podcast" }), "failed"],
    [() => json(200, { post: POST, archive: "elon" }), "failed"],
    [() => Promise.reject(new TypeError("Failed to fetch")), "offline"],
    [() => Promise.reject(Object.assign(new Error("x"), { code: 20 })), "offline"],
  ];
  for (const [respond, code] of cases) {
    const reply = await worker(respond).send({ type: "post", post: POST });
    assert.deepEqual(reply, { ok: false, code }, String(respond));
  }
});

test("a request that runs out of time says so", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const w = worker((url, init) => new Promise((_, reject) => {
    init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  }));
  const pending = w.send({ type: "ask", post: POST, question: "q" });
  t.mock.timers.tick(60001);
  assert.deepEqual(await pending, { ok: false, code: "slow" },
    "an AbortError carries code 20 of its own; it must still read as slow");
});

test("a hostile shelf is cleaned before the page sees it", async () => {
  const w = worker(() => json(200, JSON.parse(
    '{"posts": {"__proto__": "podcast", "' + POST + '": "podcast", "1": "podcast", "1234567": "<b>"}}')));
  const reply = await w.send({ type: "shelf" });
  assert.deepEqual(Object.keys(reply.data.posts), [POST]);
});
