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

  function drawSvg(shapes) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    shapes.forEach(function (shape) {
      var node = document.createElementNS(ns, shape[0]);
      Object.keys(shape[1]).forEach(function (name) { node.setAttribute(name, shape[1][name]); });
      svg.appendChild(node);
    });
    return svg;
  }

  // The site's own mark, drawn in X's style: a lens with a play button in
  // it and a handle, one colour, the size of X's own icons. The sound-wave
  // lens it replaced was three bars too small to read at 18px.
  function svgIcon() {
    return drawSvg([
      ["circle", { cx: "10.25", cy: "10.25", r: "7", fill: "none",
                   stroke: "currentColor", "stroke-width": "2" }],
      ["path", { d: "M15.5 15.5 20.75 20.75", stroke: "currentColor",
                 "stroke-width": "2.5", "stroke-linecap": "round" }],
      ["path", { d: "M8.6 7.3v5.9l5-2.95z", fill: "currentColor", stroke: "currentColor",
                 "stroke-width": "1", "stroke-linejoin": "round" }]
    ]);
  }

  // The mark in its own colours, the store icon at the size of a line of
  // text, for the panel's name. Drawn here rather than loaded: an image
  // file would have to be exposed to x.com to show on it.
  function brandMark() {
    return drawSvg([
      ["circle", { cx: "12", cy: "12", r: "12", fill: "#16c784" }],
      ["path", { d: "M15.6 15.4 18.8 18.6", stroke: "#0b0e11",
                 "stroke-width": "2.4", "stroke-linecap": "round" }],
      ["circle", { cx: "11.1", cy: "10.9", r: "6.2", fill: "#0b0e11" }],
      ["path", { d: "M9.6 8.3v5.7l4.9-2.85z", fill: "#2fe6a6", stroke: "#2fe6a6",
                 "stroke-width": "0.8", "stroke-linejoin": "round" }]
    ]);
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
    ".btn svg{width:18.75px;height:18.75px;flex:none}",
    ".panel{margin:6px 0 10px;border-radius:14px;padding:12px 14px;max-height:460px;overflow:auto;",
    "  font:400 14px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;",
    "  background:#0b0e11;color:#e7e9ea;border:1px solid #2f3336}",
    ".light .panel{background:#f7f9f9;color:#0f1419;border-color:#cfd9de}",
    ".head{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:8px}",
    ".src{display:inline-flex;align-items:center;gap:7px;color:#16c784;font-weight:700;",
    "  font-size:14px;text-decoration:none}",
    ".src:hover{text-decoration:underline}.src:focus-visible{outline:2px solid #16c784}",
    ".src svg{width:20px;height:20px;flex:none}",
    // The brand green on X's light page is 2.2:1, under the 4.5:1 text
    // needs; this one is 4.9:1.
    ".light .src,.light .t,.light .watch{color:#0b7d53}",
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
    ".from{margin:0 0 2px;font-size:12px}",
    ".at{margin:6px 0 4px}",
    "a.t{text-decoration:none}",
    ".watch{all:unset;cursor:pointer;color:#16c784;font-weight:600}",
    ".watch:hover{text-decoration:underline}.watch:focus-visible{outline:2px solid #16c784}",
    ".before{color:rgb(113,118,123)}",
    "details{margin:10px 0 4px}",
    "summary{cursor:pointer;color:rgb(113,118,123);font-size:13px;font-weight:600}",
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
  //
  // A div, specifically. X puts role="link" on its ordinary <a> tags too,
  // the post's own timestamp among them, and a rule on [role='link'] alone
  // matched the timestamp itself: no post had an id of its own and no
  // button appeared anywhere on real X. Checked against a real quote of
  // @cz_binance on 31 Oct 2026: the card is a <div role="link">.
  function ownPart(node) {
    return !node.closest("div[role='link']");
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

  // Is this still the post the button or panel was made for? X reuses a
  // post's nodes for another as you scroll, and the next post may not be a
  // video at all, an ad, or a text post, so it is never a candidate and was
  // never looked at again: the old button stayed, and its panel's times
  // moved the new post's video (found in review on 1 Nov 2026).
  function still(article, post) {
    return !!article && article.isConnected && ownPostId(article) === post && !!ownVideo(article);
  }

  function sweep() {
    document.querySelectorAll("[" + BUTTON + "],[data-mbs-panel]").forEach(function (host) {
      var post = host.getAttribute(BUTTON) || host.getAttribute("data-mbs-panel");
      if (!still(host.closest("article"), post)) host.remove();
    });
  }

  function scan() {
    sweep();
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
      if (!still(article, post)) { sweep(); return; }
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
  function render(target, markdown, times, links, article, status, post) {
    MBS.blocks(markdown, times, links).forEach(function (block) {
      var node = block.kind === "heading" ? el("h4") : block.kind === "item" ? el("li") : el("p");
      block.parts.forEach(function (part) {
        if (part.link) {
          var a = showLink(part.time, part.link);
          node.appendChild(part.bold ? wrapBold(a) : a);
        } else if (part.time) {
          var t = el("button", "t", part.time);
          t.type = "button";
          t.title = "Play from " + part.time + " on this post";
          t.addEventListener("click", function (e) {
            if (!e.isTrusted || !still(article, post)) return;
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

  // A time that opens the whole show at that second, in a new tab: a
  // clip's own player only holds its own minute. A real link, so the
  // reader sees where it goes. The address passed MBS.LINK in the worker
  // and passes it again here, where it becomes an href; one that doesn't
  // is shown as the words it is.
  function showLink(text, href) {
    if (!MBS.LINK.test(href)) return el("span", null, text);
    var a = el("a", "t", text);
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.title = "Open the whole show at " + text;
    return a;
  }

  // Where a clip sits in the show, and what the clipper left out either
  // side of it. The archive's own words, shown as text.
  //
  // A time is a link only where the link opens at it: X's broadcast player
  // and YouTube do. A post's own video starts at 0:00 whatever the link
  // says, so for one of those the time stays words and the link just
  // opens the full video.
  function renderClip(target, clip) {
    var seeks = MBS.seeks(clip.watch);
    var at = el("p", "at");
    at.appendChild(seeks ? showLink(clip.at, clip.watch) : el("strong", null, clip.at));
    at.appendChild(document.createTextNode(
      (clip.until && clip.until !== clip.at ? " to " + clip.until : "") + " in the show"));
    if (clip.watch) {
      at.appendChild(document.createTextNode(". "));
      var full = showLink(seeks ? "Watch it in the full show" : "Open the full video", clip.watch);
      full.className = "watch";
      full.title = seeks ? "Open the whole show at " + clip.at
                         : "Open the full video on X. It starts at the beginning.";
      at.appendChild(full);
    }
    target.appendChild(at);
    if (clip.before) {
      target.appendChild(el("h4", null, "Just before the clip"));
      target.appendChild(el("p", "said before", "\u2026" + MBS.undash(clip.before)));
    }
    if (clip.after) {
      target.appendChild(el("h4", null, "Right after the clip"));
      var after = el("p", "said");
      if (clip.afterAt) {
        after.appendChild(clip.afterWatch ? showLink(clip.afterAt, clip.afterWatch)
                                          : el("strong", null, clip.afterAt));
        after.appendChild(document.createTextNode(" "));
      }
      after.appendChild(document.createTextNode(MBS.undash(clip.after) + "\u2026"));
      target.appendChild(after);
    }
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
    // The name as it is written, with its mark, and a way to the site.
    var name = el("a", "src");
    name.href = MBS.API + "/home";
    name.target = "_blank";
    name.rel = "noopener noreferrer";
    name.appendChild(brandMark());
    name.appendChild(document.createTextNode("MarketBubbleSearch"));
    head.appendChild(name);
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
      if (data.clip) body.appendChild(el("div", "note from", "This clip is from"));
      body.appendChild(el("div", "title", data.title || data.archiveName));
      var line = [data.show || data.archiveName, MBS.longDate(data.publishedAt)]
        .filter(Boolean).join(", ");
      if (line) body.appendChild(el("div", "date", line));
      if (data.clip) {
        renderClip(body, data.clip);
        if (data.summary) {
          // The episode's summary, folded: the clip is what was asked
          // about, the rest of the show is one click away.
          var whole = el("details");
          whole.appendChild(el("summary", null, "The whole episode"));
          render(whole, data.summary, data.times, data.links, article, status, post);
          body.appendChild(whole);
        }
      } else if (data.summary) {
        render(body, data.summary, data.times, data.links, article, status, post);
      } else {
        body.appendChild(el("div", "note",
          "No summary for this one yet. Ask anything about it below."));
      }
      if (!data.seekable) {
        body.appendChild(el("div", "note",
          "Times here can't be matched to this post's player, so they show as text."));
      }
      body.appendChild(askBox(post, article, status,
        data.clip ? "Ask about the episode" : "Ask about this recording"));
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

  function askBox(post, article, status, prompt) {
    var wrap = el("div");
    var form = el("form");
    var input = el("input");
    input.type = "text";
    input.maxLength = MBS.MAX_QUESTION;
    input.placeholder = prompt;
    input.setAttribute("aria-label", prompt);
    input.autocomplete = "off";
    var typed = "";
    input.addEventListener("input", function (e) { if (e.isTrusted) typed = input.value; });
    var go = el("button", "go", "Ask");
    go.type = "button";
    form.appendChild(input);
    form.appendChild(go);
    var out = el("div");
    wrap.appendChild(form);
    wrap.appendChild(out);

    function ask() {
      // Only what the reader typed. The shadow root is open, so a script on
      // the page can set the box's value and lay the Ask button over X's
      // own buttons; a real click would then have sent its question
      // (found in review on 1 Nov 2026). The value is held to the last one
      // a real keystroke or paste produced.
      if (input.value !== typed || !still(article, post)) return;
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
        render(answer, reply.data.answer.answer, reply.data.answer.times,
               reply.data.answer.links, article, status, post);
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
