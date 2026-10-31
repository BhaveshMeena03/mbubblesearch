/* The only part of the extension that talks to the network, and it talks
   to one place: search.lexthedev.com, at three fixed paths.

   The content script on x.com asks for one of three things by name. It
   never hands over a URL, so nothing on a page, however it got there, can
   point this at another address. Every reply is checked by lib.js before
   it goes back, and no cookies or referrer go out with any request. */

importScripts("lib.js");

var SHELF_TTL_MS = 15 * 60 * 1000;
var shelf = null;           // { posts, at }, kept while the worker is awake

function fetchJson(path, options, timeoutMs) {
  var controller = new AbortController();
  var timer = setTimeout(function () { controller.abort(); }, timeoutMs);
  var init = Object.assign({
    credentials: "omit",
    referrerPolicy: "no-referrer",
    cache: "no-store",
    redirect: "error",
    signal: controller.signal
  }, options || {});
  return fetch(MBS.API + path, init).then(function (response) {
    if (response.status === 429) throw { code: "busy" };
    if (response.status === 503) throw { code: "limit" };
    if (response.status === 404) throw { code: "unknown" };
    if (!response.ok) throw { code: "failed" };
    return response.json();
  }).catch(function (error) {
    if (error && error.code) throw error;
    throw { code: error && error.name === "AbortError" ? "slow" : "offline" };
  }).finally(function () { clearTimeout(timer); });
}

function getShelf() {
  if (shelf && Date.now() - shelf.at < SHELF_TTL_MS) return Promise.resolve(shelf.posts);
  return fetchJson("/v1/x/shelf", null, 10000).then(function (body) {
    var posts = MBS.cleanShelf(body);
    if (!posts) throw { code: "failed" };
    shelf = { posts: posts, at: Date.now() };
    return posts;
  });
}

function getPost(post) {
  return fetchJson("/v1/x/post/" + post, null, 10000).then(function (body) {
    var clean = MBS.cleanPost(body);
    if (!clean || clean.post !== post) throw { code: "failed" };
    return clean;
  });
}

function ask(post, question) {
  return fetchJson("/v1/x/ask", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ post: post, question: question })
  }, 60000).then(function (body) {
    var clean = MBS.cleanAnswer(body);
    if (!clean) throw { code: "failed" };
    return clean;
  });
}

// Only this extension's own content script, on x.com, is answered. A
// message from anywhere else is not an error worth reporting; it is
// dropped without a reply.
function trusted(sender) {
  if (!sender || sender.id !== chrome.runtime.id) return false;
  var origin = sender.origin || (sender.url ? new URL(sender.url).origin : "");
  return origin === "https://x.com";
}

chrome.runtime.onMessage.addListener(function (message, sender, reply) {
  if (!trusted(sender) || !message || typeof message.type !== "string") return false;

  var work;
  if (message.type === "shelf") {
    work = getShelf().then(function (posts) { return { posts: posts }; });
  } else if (message.type === "post" && MBS.isPostId(message.post)) {
    work = getPost(message.post).then(function (post) { return { post: post }; });
  } else if (message.type === "ask" && MBS.isPostId(message.post)) {
    var question = MBS.cleanQuestion(message.question);
    if (!question) return false;
    work = ask(message.post, question).then(function (answer) { return { answer: answer }; });
  } else {
    return false;
  }

  work.then(function (data) { reply({ ok: true, data: data }); },
            function (error) { reply({ ok: false, code: (error && error.code) || "failed" }); });
  return true;    // the reply comes after this returns
});
