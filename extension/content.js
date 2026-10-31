/* The button and the panel, on x.com.

   A post gets a button when its own video is on it and its own id is on
   the shelf (the list of X posts these archives hold, which background.js
   downloads at most once an hour, and only on a page that has a video
   post). That decision is made here, in the browser, so nothing about
   what you scroll past is sent anywhere. Only a click sends the post id,
   and only a question sends the question.

   Everything is built with createElement and textContent. No markup from
   the server, or from anywhere, is ever parsed: a summary that somehow
   carried <img onerror> would show up as those characters.

   The panel lives in a shadow root, so X's stylesheet cannot reach in and
   ours cannot leak out. An open one: closing it would hide the panel from
   tests and from accessibility tools while keeping nothing from X, whose
   page sees every key pressed in it either way. Nothing in the panel is
   secret; it is the archive's public answer to a public question. */

(function () {
  "use strict";
  if (window.__mbsLoaded) return;
  window.__mbsLoaded = true;

  var posts = null;          // post id -> archive, once the shelf arrives
  var shelfAsked = 0;
  var RETRY_SHELF_MS = 5 * 60 * 1000;
  var BUTTON = "data-mbs-button";
  var MESSAGES = {
    busy: "Busy right now. Try again in a minute.",
    limit: "Today's limit is used up. It resets at midnight UTC.",
    unknown: "This post isn't in the archive.",
    slow: "That took too long. Try again.",
    offline: "Couldn't reach search.lexthedev.com.",
    failed: "Something went wrong. Try again."
  };

  function send(message) {
    return new Promise(function (resolve) {
      try {
        chrome.runtime.sendMessage(message, function (reply) {
          // A reloaded or removed extension leaves this script running with
          // no one to talk to; that is a failure, not an exception.
          if (chrome.runtime.lastError || !reply) resolve({ ok: false, code: "failed" });
          else resolve(reply);
        });
      } catch (e) {
        resolve({ ok: false, code: "failed" });
      }
    });
  }

  function loadShelf() {
    if (posts || Date.now() - shelfAsked < RETRY_SHELF_MS) return;
    shelfAsked = Date.now();
    send({ type: "shelf" }).then(function (reply) {
      if (reply.ok && reply.data && reply.data.posts) {
        posts = reply.data.posts;
        scan();
      }
    });
  }

  // ── small DOM helpers ────────────────────────────────────────────────
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  // X opens the post when anything inside it is clicked, and its keyboard
  // shortcuts listen on the whole page. Neither may see what happens in
  // the button, the panel, or the box a question is typed into.
  function contain(node) {
    ["click", "mousedown", "mouseup", "pointerdown", "pointerup", "touchstart",
     "keydown", "keyup", "keypress"].forEach(function (type) {
      node.addEventListener(type, function (e) { e.stopPropagation(); });
    });
  }

  function svgIcon() {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    var path = document.createElementNS(ns, "path");
    // A magnifier with a sound wave in the lens: search what was said.
    path.setAttribute("d", "M10.5 3a7.5 7.5 0 0 1 5.96 12.05l4.25 4.24-1.42 1.42-4.24-4.25A7.5 7.5 0 1 1 10.5 3zm0 2a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11zM7 9h1.5v3H7V9zm2.75-1.5h1.5v6h-1.5v-6zM12.5 9.5H14v2h-1.5v-2z");
    svg.appendChild(path);
    return svg;
  }

  function isLight() {
    var bg = getComputedStyle(document.body).backgroundColor || "";
    var rgb = bg.match(/\d+/g);
    if (!rgb || rgb.length < 3) return false;
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) > 160;
  }

  var STYLE = [
    ":host{all:initial;display:block}",
    ".btn{all:unset;display:inline-flex;align-items:center;gap:4px;cursor:pointer;",
    "  color:rgb(113,118,123);font:500 13px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;",
    "  padding:0 8px;height:34px;border-radius:999px}",
    ".btn:hover,.btn[aria-expanded=true]{color:#16c784;background:rgba(22,199,132,.1)}",
    ".btn:focus-visible{outline:2px solid #16c784}",
    ".btn svg{width:18px;height:18px;fill:currentColor}",
    ".panel{margin:6px 0 10px;border-radius:14px;padding:12px 14px;max-height:460px;overflow:auto;",
    "  font:400 14px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;",
    "  background:#0b0e11;color:#e7e9ea;border:1px solid #2f3336}",
    ".light .panel{background:#f7f9f9;color:#0f1419;border-color:#cfd9de}",
    ".head{display:flex;justify-content:space-between;gap:10px;align-items:baseline;margin-bottom:6px}",
    ".src{color:#16c784;font-weight:600;font-size:12px;letter-spacing:.02em;text-transform:lowercase}",
    ".x{all:unset;cursor:pointer;color:rgb(113,118,123);font-size:16px;padding:0 4px}",
    ".title{font-weight:700;margin:0 0 2px}",
    ".date{color:rgb(113,118,123);font-size:12px;margin-bottom:8px}",
    "h4{margin:10px 0 4px;font-size:13px;color:rgb(113,118,123);text-transform:lowercase;letter-spacing:.02em}",
    "p{margin:0 0 8px}ul{margin:0 0 8px;padding-left:18px}li{margin:0 0 5px}",
    ".t{all:unset;cursor:pointer;color:#16c784;font-variant-numeric:tabular-nums;font-weight:600;",
    "  border-radius:4px;padding:0 2px}",
    ".t:hover{text-decoration:underline}.t:focus-visible{outline:2px solid #16c784}",
    ".t.on{background:rgba(22,199,132,.18)}",
    ".note{color:rgb(113,118,123);font-size:13px;margin:6px 0}",
    ".err{color:#f4212e;font-size:13px;margin:6px 0}",
    "form{display:flex;gap:8px;margin:10px 0 6px}",
    "input{flex:1;min-width:0;border-radius:999px;border:1px solid #2f3336;background:transparent;",
    "  color:inherit;font:inherit;padding:7px 12px;outline:none}",
    ".light input{border-color:#cfd9de}",
    "input:focus{border-color:#16c784}",
    ".go{all:unset;cursor:pointer;background:#16c784;color:#0b0e11;font-weight:700;border-radius:999px;",
    "  padding:7px 14px;font-size:13px}",
    ".go[disabled]{opacity:.5;cursor:default}",
    ".answer{border-top:1px solid #2f3336;margin-top:8px;padding-top:8px}",
    ".light .answer{border-color:#cfd9de}",
    ".foot{margin-top:8px;font-size:12px}",
    ".foot a{color:rgb(113,118,123);text-decoration:none}.foot a:hover{color:#16c784}"
  ].join("\n");

  function shadowHost(tag) {
    var host = document.createElement(tag || "div");
    var root = host.attachShadow({ mode: "open" });
    var style = document.createElement("style");
    style.textContent = STYLE;
    root.appendChild(style);
    var wrap = el("div", isLight() ? "light" : "");
    root.appendChild(wrap);
    contain(host);
    return { host: host, root: wrap };
  }

  // ── finding our posts ────────────────────────────────────────────────
  //
  // Only the post's own id, and only its own video. A quoted post sits in
  // a card X marks role="link"; anything inside one belongs to the quoted
  // post. Giving the button to a quote post would put the archive's
  // summary over somebody else's clip, and its times would play that clip:
  // a way to pass a cut-up video off as the archive's. Found in review on
  // 31 Oct 2026, before it shipped.
  function ownPart(node) {
    return !node.closest("[role='link']");
  }

  function ownPostId(article) {
    var links = article.querySelectorAll("a[href*='/status/']");
    for (var i = 0; i < links.length; i++) {
      if (links[i].querySelector("time") && ownPart(links[i])) {
        return MBS.postIdFromHref(links[i].getAttribute("href"));
      }
    }
    return null;
  }

  function actionBar(article) {
    var bars = article.querySelectorAll("[role='group']");
    for (var i = bars.length - 1; i >= 0; i--) {
      if (bars[i].querySelector("[data-testid='reply'],[data-testid='like'],[data-testid='unlike']")) {
        return bars[i];
      }
    }
    return null;
  }

  function ownVideo(article) {
    var found = article.querySelectorAll("video,[data-testid='videoPlayer'],[data-testid='videoComponent']");
    for (var i = 0; i < found.length; i++) if (ownPart(found[i])) return found[i];
    return null;
  }

  // Posts that could be ours: their own video, an action bar, an id. The
  // shelf is not even asked for on a page without one.
  function candidates() {
    var out = [];
    document.querySelectorAll("article").forEach(function (article) {
      var bar = actionBar(article);
      var post = bar && ownVideo(article) ? ownPostId(article) : null;
      if (post) out.push({ article: article, bar: bar, post: post });
    });
    return out;
  }

  function scan() {
    var found = candidates();
    if (!found.length) return;
    if (!posts) { loadShelf(); return; }
    found.forEach(function (c) {
      var had = c.bar.querySelector("[" + BUTTON + "]");
      // X reuses a post's nodes for another post as you scroll. A button
      // left from the last one would open the wrong summary.
      if (had && had.getAttribute(BUTTON) === c.post) return;
      if (had) {
        had.remove();
        c.article.querySelectorAll("[data-mbs-panel]").forEach(function (n) { n.remove(); });
      }
      if (Object.prototype.hasOwnProperty.call(posts, c.post)) addButton(c.article, c.bar, c.post);
    });
  }

  // ── the button ───────────────────────────────────────────────────────
  function addButton(article, bar, post) {
    var made = shadowHost("div");
    made.host.setAttribute(BUTTON, post);
    made.host.style.display = "flex";
    made.host.style.alignItems = "center";
    var button = el("button", "btn");
    button.type = "button";
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", "Summary and ask, from MarketBubbleSearch");
    button.title = "Summary and ask (MarketBubbleSearch)";
    button.appendChild(svgIcon());
    button.appendChild(el("span", null, "summary"));
    made.root.appendChild(button);
    bar.appendChild(made.host);

    var panel = null;
    button.addEventListener("click", function (e) {
      // A person, not a script on the page: an open shadow root lets the
      // page reach this button, and a scripted click must not spend a
      // question from the reader's address.
      if (!e.isTrusted) return;
      if (panel && panel.host.isConnected) {
        panel.host.remove();
        panel = null;
        button.setAttribute("aria-expanded", "false");
        return;
      }
      panel = openPanel(article, bar, post, function () {
        panel = null;
        button.setAttribute("aria-expanded", "false");
      });
      button.setAttribute("aria-expanded", "true");
    });
  }

  // ── the panel ────────────────────────────────────────────────────────
  function render(target, markdown, times, article, status) {
    MBS.blocks(markdown, times).forEach(function (block) {
      var node = block.kind === "heading" ? el("h4") : block.kind === "item" ? el("li") : el("p");
      block.parts.forEach(function (part) {
        if (part.time) {
          var t = el("button", "t", part.time);
          t.type = "button";
          t.title = "Play from " + part.time + " on this post";
          t.addEventListener("click", function (e) {
            if (!e.isTrusted) return;
            target.querySelectorAll(".t.on").forEach(function (n) { n.classList.remove("on"); });
            t.classList.add("on");
            seek(article, part.seconds, status);
          });
          node.appendChild(part.bold ? wrapBold(t) : t);
        } else {
          node.appendChild(part.bold ? el("strong", null, part.text) : document.createTextNode(part.text));
        }
      });
      if (block.kind === "item") {
        var list = target.lastElementChild;
        if (!list || list.tagName !== "UL") { list = el("ul"); target.appendChild(list); }
        list.appendChild(node);
      } else {
        target.appendChild(node);
      }
    });
  }

  function wrapBold(node) {
    var b = el("strong");
    b.appendChild(node);
    return b;
  }

  function openPanel(article, bar, post, onClose) {
    var made = shadowHost("div");
    made.host.setAttribute("data-mbs-panel", post);
    var root = made.root;
    var panel = el("div", "panel");
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-label", "MarketBubbleSearch");
    root.appendChild(panel);

    var head = el("div", "head");
    head.appendChild(el("span", "src", "marketbubblesearch"));
    var close = el("button", "x", "×");
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    close.addEventListener("click", function (e) {
      if (!e.isTrusted) return;
      made.host.remove();
      onClose();
    });
    head.appendChild(close);
    panel.appendChild(head);

    var body = el("div");
    var status = el("div", "note");
    body.appendChild(el("div", "note", "Loading…"));
    panel.appendChild(body);
    panel.appendChild(status);

    bar.parentNode.insertBefore(made.host, bar.nextSibling);

    send({ type: "post", post: post }).then(function (reply) {
      body.textContent = "";
      if (!reply.ok) {
        body.appendChild(el("div", "err", MESSAGES[reply.code] || MESSAGES.failed));
        return;
      }
      var data = reply.data.post;
      body.appendChild(el("div", "title", data.title || data.archiveName));
      var line = [data.show || data.archiveName, MBS.longDate(data.publishedAt)]
        .filter(Boolean).join(", ");
      if (line) body.appendChild(el("div", "date", line));
      if (data.summary) {
        render(body, data.summary, data.times, article, status);
      } else {
        body.appendChild(el("div", "note",
          "No summary for this one yet. Ask anything about it below."));
      }
      if (!data.seekable) {
        body.appendChild(el("div", "note",
          "Times here can't be matched to this post's player, so they show as text."));
      }
      body.appendChild(askBox(post, article, status));
      var foot = el("div", "foot");
      var link = el("a", null, "open on search.lexthedev.com");
      link.href = data.page;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      foot.appendChild(link);
      body.appendChild(foot);
    });
    return made;
  }

  function askBox(post, article, status) {
    var wrap = el("div");
    var form = el("form");
    var input = el("input");
    input.type = "text";
    input.maxLength = MBS.MAX_QUESTION;
    input.placeholder = "Ask about this recording";
    input.setAttribute("aria-label", "Ask about this recording");
    input.autocomplete = "off";
    var go = el("button", "go", "Ask");
    go.type = "button";
    form.appendChild(input);
    form.appendChild(go);
    var out = el("div");
    wrap.appendChild(form);
    wrap.appendChild(out);

    function ask() {
      var question = MBS.cleanQuestion(input.value);
      if (!question || go.disabled) return;
      go.disabled = true;
      out.textContent = "";
      out.appendChild(el("div", "note", "Searching this recording\u2026"));
      send({ type: "ask", post: post, question: question }).then(function (reply) {
        go.disabled = false;
        out.textContent = "";
        if (!reply.ok) {
          out.appendChild(el("div", "err", MESSAGES[reply.code] || MESSAGES.failed));
          return;
        }
        var answer = el("div", "answer");
        render(answer, reply.data.answer.answer, reply.data.answer.times, article, status);
        out.appendChild(answer);
      });
    }

    // Asked only by a person: a real click on Ask, or a real Enter in the
    // box. Not on "submit": a script calling form.requestSubmit() gets a
    // submit event the browser marks as trusted, which is how a page could
    // have spent a question from the reader's address. Caught by the
    // browser tests on 31 Oct 2026.
    form.addEventListener("submit", function (e) { e.preventDefault(); });
    go.addEventListener("click", function (e) { if (e.isTrusted) ask(); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && e.isTrusted) {
        e.preventDefault();
        ask();
      }
    });
    return wrap;
  }

  // ── moving X's own player ────────────────────────────────────────────
  // The post's own <video>, never a quoted one's.
  function findVideo(article) {
    var videos = article.querySelectorAll("video");
    for (var i = 0; i < videos.length; i++) if (ownPart(videos[i])) return videos[i];
    return null;
  }

  function seek(article, seconds, status) {
    status.textContent = "";
    // Nothing is clicked on the page to make a player appear: whatever
    // sits where a play button usually is might be something else.
    var found = findVideo(article);
    if (!found) {
      status.textContent = "Start the video, then pick the time again.";
      return;
    }
    if (isFinite(found.duration) && found.duration > 0 && seconds > found.duration) {
      status.textContent = "That moment is past the end of this video.";
      return;
    }
    found.currentTime = seconds;
    var playing = found.play();
    if (playing && playing.catch) playing.catch(function () {});
    var box = found.getBoundingClientRect();
    if (box.bottom < 0 || box.top > window.innerHeight) {
      found.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  // ── watching the page ────────────────────────────────────────────────
  var pending = false;
  function soon() {
    if (pending) return;
    pending = true;
    setTimeout(function () {
      pending = false;
      scan();
    }, 300);
  }

  new MutationObserver(soon).observe(document.documentElement, { childList: true, subtree: true });
  scan();
})();
