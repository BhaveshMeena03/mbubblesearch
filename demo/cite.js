/* Clickable citations inside an answer.
 *
 * The model cites like a paper: "[29:23]", "[42:06] to [42:31]", "around
 * 16:16 in the May 22 stream", "[2026-05-23 at 8:16]". The pages printed
 * those as plain text, so the one thing an answer promises, that you can
 * go and hear it, needed the reader to find the right card and scrub.
 *
 * A time becomes a link only when a returned passage actually covers it,
 * the same rule the bot follows (app/x_bot.py, cited_hit). Two recordings
 * can both have a line at 8:16, and on ThreadGuy they did: a hot seat
 * episode and the May 23 stream. When more than one passage covers the
 * time, the recording the answer names next to it wins, by its date. A
 * time nothing covers stays plain text: a real timestamp welded to the
 * wrong episode looks checkable and is not, which is worse than no link.
 *
 * Used by /home and /threadguy. Exposed as window.Cite in the browser and
 * as a CommonJS module so the resolver can be tested under node.
 */
(function (root) {
  "use strict";

  // A cited line can round to a second or two either side of where the
  // passage's lines start, and end_seconds is where its LAST line starts,
  // so the line itself runs on past it.
  var BEFORE = 5, AFTER = 30;
  // Vectors written before per-line stamps carry no end. A passage is a
  // few minutes of speech, so a time this far past its start is the most
  // it can plausibly cover.
  var REACH = 300;

  var MONTHS = ["january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december"];
  var MONTH_RE = "(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    + "june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|"
    + "dec(?:ember)?)";

  // "[...]" groups and bare times, in one pass so positions agree.
  var TOKEN = /\[([^\[\]\n]{1,160})\]|(\d{1,2}:\d{2}(?::\d{2})?)/g;
  var STAMP = /(\d{1,2}:\d{2}(?::\d{2})?)/g;
  var CLOCK = /^\s*(?:a\.?m\.?|p\.?m\.?)(?![a-z])/i;
  var ISO = /\b(20\d\d)-(\d\d)-(\d\d)\b/g;
  var WORDY = new RegExp("\\b" + MONTH_RE + "\\.?\\s+(\\d{1,2})(?:st|nd|rd|th)?"
                         + "(?:,?\\s+(20\\d\\d))?\\b", "gi");

  function seconds(stamp) {
    var parts = String(stamp).split(":"), total = 0;
    for (var i = 0; i < parts.length; i++) {
      var n = parseInt(parts[i], 10);
      if (isNaN(n)) return null;
      if (i > 0 && n > 59) return null;
      total = total * 60 + n;
    }
    return total;
  }

  function ytId(u) {
    var m = /(?:[?&]v=|youtu\.be\/|\/embed\/)([A-Za-z0-9_-]{6,})/.exec(u || "");
    return m ? m[1] : null;
  }

  // The same link the card uses, moved to the cited second.
  function linkAt(hit, sec) {
    var base = String(hit.deep_link || hit.url || "").replace(/[?&]t=\d+s?/g, "");
    // Only a web address becomes a link. A passage is data from the index,
    // and a "javascript:" or "data:" value in it must not become an href.
    if (!/^https?:\/\//i.test(base)) return "";
    var join = base.indexOf("?") > -1 ? "&" : "?";
    var isX = /(^|\/\/)(www\.)?(x|twitter)\.com\//.test(base);
    return base + join + "t=" + sec + (isX ? "" : "s");
  }

  function monthIndex(word) {
    var w = String(word).toLowerCase().slice(0, 3);
    for (var i = 0; i < 12; i++) if (MONTHS[i].slice(0, 3) === w) return i + 1;
    return 0;
  }

  // Every date the answer writes, with where it sits.
  function datesIn(text) {
    var out = [], m;
    ISO.lastIndex = 0;
    while ((m = ISO.exec(text))) {
      out.push({at: m.index, end: m.index + m[0].length,
                y: +m[1], mo: +m[2], d: +m[3]});
    }
    WORDY.lastIndex = 0;
    while ((m = WORDY.exec(text))) {
      var mo = monthIndex(m[1]), d = +m[2];
      if (!mo || d < 1 || d > 31) continue;
      out.push({at: m.index, end: m.index + m[0].length,
                y: m[3] ? +m[3] : 0, mo: mo, d: d});
    }
    return out;
  }

  function sameDay(mention, iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
    if (!m) return false;
    return +m[2] === mention.mo && +m[3] === mention.d
        && (!mention.y || +m[1] === mention.y);
  }

  function covers(hit, sec) {
    var start = Number(hit.start_seconds);
    if (!isFinite(start)) return false;
    if (hit.episode_seconds && sec > Number(hit.episode_seconds) + 1) return false;
    var end = hit.end_seconds;
    var last = (end === null || end === undefined || !isFinite(Number(end)))
      ? start + REACH : Number(end) + AFTER;
    return sec >= start - BEFORE && sec <= last;
  }

  // Of the passages that cover this time, the one whose date the answer
  // writes nearest to it: before it in the same paragraph ("in the
  // December 13, 2024 episode, at [29:23]"), or just after ("29:23 in
  // the May 22 stream"). No date near it, the best ranked one, as the bot.
  function resolve(hits, sec, pos, para, dates) {
    var cands = hits.filter(function (h) { return covers(h, sec); });
    if (!cands.length) return null;
    if (cands.length === 1) return cands[0];
    var best = null, gap = Infinity;
    cands.forEach(function (h) {
      dates.forEach(function (dm) {
        if (dm.at < para.from || dm.end > para.to) return;
        if (!sameDay(dm, h.published_at)) return;
        var g = dm.end <= pos ? pos - dm.end
              : (dm.at - pos <= 90 ? (dm.at - pos) * 1.5 : Infinity);
        if (g < gap) { gap = g; best = h; }
      });
    });
    return best || cands[0];
  }

  function paragraphAt(text, pos) {
    var from = text.lastIndexOf("\n\n", pos);
    var to = text.indexOf("\n\n", pos);
    return {from: from < 0 ? 0 : from, to: to < 0 ? text.length : to};
  }

  function readableDates(s) {
    return s.replace(/\b(20\d\d)-(\d\d)-(\d\d)\b/g, function (all, y, mo, d) {
      var i = +mo;
      if (i < 1 || i > 12) return all;
      var name = MONTHS[i - 1];
      return name.charAt(0).toUpperCase() + name.slice(1) + " " + (+d) + ", " + y;
    });
  }

  // The model writes Markdown whether or not it is asked to, and a page
  // that prints text as text showed "**On the product itself:**",
  // asterisks and all. Headings and list markers become plain lines here;
  // bold is carried through as a flag on the text (see emphasis()).
  function unmark(text) {
    return String(text || "")
      .replace(/(^|\n)[ \t]*#{1,4}[ \t]+/g, "$1")
      .replace(/(^|\n)[ \t]*[-*\u2022][ \t]+/g, "$1\u2022 ");
  }

  // A "**" between two letters is not formatting. Episode titles bleep
  // swearing that way ("Korean Stocks Are F**KED"), the model quotes the
  // title, and read as a marker it turned the rest of the answer bold.
  // Swapped for a same-length placeholder while markers are counted, so
  // positions still line up, and put back after.
  var IN_WORD = /([A-Za-z0-9])\*\*(?=[A-Za-z0-9])/g, HELD = "\u0001\u0001";
  function shield(s) { return s.replace(IN_WORD, "$1" + HELD); }
  function unshield(s) { return s.split(HELD).join("**"); }

  // "**...**" -> {text, bold: true}. Across tokens, because a bold run
  // can have a citation inside it. An unpaired marker is just dropped.
  function emphasis(tokens) {
    var marks = 0;
    tokens.forEach(function (t) {
      if (!t.stamp) marks += shield(t.text).split("**").length - 1;
    });
    var out = [], bold = false;
    tokens.forEach(function (t) {
      if (t.stamp) { out.push(t); return; }
      var text = shield(t.text);
      if (marks % 2) { out.push({text: unshield(text.split("**").join(""))}); return; }
      text.split("**").forEach(function (part, i) {
        if (i > 0) bold = !bold;
        part = unshield(part);
        if (part) out.push(bold ? {text: part, bold: true} : {text: part});
      });
    });
    return out;
  }

  // text -> [{text, bold?}] and [{stamp, sec, hit, href}] in reading order.
  function find(text, hits) {
    text = unmark(text);
    hits = (hits || []).filter(function (h) { return h && linkAt(h, 0); });
    var dates = datesIn(text), out = [], last = 0, m, drop = {};

    // Characters to leave out: the brackets around a citation whose own
    // text has brackets in it, which the group pattern cannot match.
    function plain(s, from) {
      var kept = "";
      for (var i = 0; i < s.length; i++) if (!drop[from + i]) kept += s.charAt(i);
      if (kept) out.push({text: readableDates(kept)});
    }
    // '[15:33, "This FOMC Changes Everything... [Stream Recap]", May 20]':
    // the title's own brackets stop the group pattern, so the time is
    // found bare. Find the bracket that closes the one it opens.
    function closer(open) {
      for (var i = open + 1, depth = 0; i < text.length && i < open + 240; i++) {
        var ch = text.charAt(i);
        if (ch === "\n") return -1;
        if (ch === "[") depth++;
        else if (ch === "]") { if (!depth) return i; depth--; }
      }
      return -1;
    }
    function stampAt(stamp, pos) {
      var sec = seconds(stamp);
      if (sec === null) return null;
      var hit = resolve(hits, sec, pos, paragraphAt(text, pos), dates);
      return hit ? {stamp: stamp, sec: sec, hit: hit, href: linkAt(hit, sec)} : null;
    }
    function isClock(end) { return CLOCK.test(text.slice(end, end + 6)); }

    TOKEN.lastIndex = 0;
    while ((m = TOKEN.exec(text))) {
      var at = m.index, whole = m[0];
      if (m[2]) {
        // A bare time. Not part of a longer number, a price, or a clock.
        var prev = text.charAt(at - 1);
        if (/[\d:.$]/.test(prev) || isClock(at + whole.length)) continue;
        var tok = stampAt(m[2], at);
        if (!tok) continue;
        if (prev === "[") {
          var close = closer(at - 1);
          if (close > 0) { drop[at - 1] = true; drop[close] = true; }
        }
        plain(text.slice(last, at), last); out.push(tok); last = at + whole.length;
        continue;
      }
      // A bracketed group: link every time inside it that resolves, and
      // drop the brackets, which around a link read as noise.
      var inner = m[1], parts = [], linked = false, cut = 0, s;
      STAMP.lastIndex = 0;
      while ((s = STAMP.exec(inner))) {
        var ipos = at + 1 + s.index;
        var before = inner.charAt(s.index - 1);
        if (/[\d:.$]/.test(before) || isClock(ipos + s[0].length)) continue;
        var t = stampAt(s[1], ipos);
        if (!t) continue;
        parts.push({text: readableDates(inner.slice(cut, s.index))});
        parts.push(t); cut = s.index + s[0].length; linked = true;
      }
      if (!linked) continue;
      parts.push({text: readableDates(inner.slice(cut))});
      plain(text.slice(last, at), last);
      parts.forEach(function (p) { if (p.stamp || p.text) out.push(p); });
      last = at + whole.length;
    }
    plain(text.slice(last), last);
    return emphasis(out);
  }

  // Cited passages first, in the order the answer cites them, then the
  // rest by rank. The cards under an answer were the top six by score,
  // and on a real question the three passages it quoted sat at 7 to 9.
  function order(hits, tokens) {
    var seen = [], rest = [];
    (tokens || []).forEach(function (t) {
      if (t.hit && seen.indexOf(t.hit) < 0) seen.push(t.hit);
    });
    (hits || []).forEach(function (h) { if (seen.indexOf(h) < 0) rest.push(h); });
    return seen.concat(rest);
  }

  function node(tok, pick) {
    if (!tok.stamp) {
      if (!tok.bold) return document.createTextNode(tok.text);
      var b = document.createElement("strong");
      b.textContent = tok.text;
      return b;
    }
    var a = document.createElement("a");
    a.className = "cite";
    a.href = tok.href;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = tok.stamp;
    a.setAttribute("aria-label", "Play " + tok.stamp
      + (tok.hit.title ? ", " + tok.hit.title : ""));
    a.onclick = function (e) {
      // cmd, ctrl, shift and middle click keep their meaning: a new tab.
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
      e.preventDefault();
      pick(tok);
    };
    return a;
  }

  // Types the answer out a few words at a time with the links already in
  // it, so nothing jumps when it finishes. A hidden tab gets it whole:
  // timers are throttled in the background. Returns the interval, or null.
  function write(el, tokens, pick, instant) {
    el.textContent = "";
    var units = [];
    tokens.forEach(function (t) {
      if (t.stamp) { units.push(t); return; }
      t.text.split(/(\s+)/).forEach(function (w) {
        if (w) units.push(t.bold ? {text: w, bold: true} : {text: w});
      });
    });
    if (instant) {
      units.forEach(function (u) { el.appendChild(node(u, pick)); });
      return null;
    }
    var i = 0;
    var timer = setInterval(function () {
      var stop = Math.min(units.length, i + 6);
      for (; i < stop; i++) el.appendChild(node(units[i], pick));
      if (i >= units.length) clearInterval(timer);
    }, 16);
    return timer;
  }

  // Draws an answer. While it is still arriving (`done` false), an
  // opening "**" whose closing one has not come yet would make every bold
  // marker in the answer look unpaired, so what follows it is drawn bold
  // until the pair completes. A finished answer gets no such allowance:
  // an unpaired marker there is dropped. Returns the tokens drawn.
  function paint(el, text, hits, pick, done) {
    var tokens = tokensFor(text, hits, done);
    write(el, tokens, pick, true);
    return tokens;
  }

  // POSTs `body` and calls on(event, payload) for each server-sent event.
  // Resolves when the stream ends. Rejects with .status on a refused
  // request, so the page can say "too many questions" rather than
  // "could not reach the archive", and with no status when the browser
  // cannot read a stream at all, so the page can fall back to the plain
  // endpoint. A throw inside on() ends the stream the same way.
  function stream(url, body, signal, on) {
    return fetch(url, {method: "POST", signal: signal,
                       headers: {"Content-Type": "application/json"},
                       body: JSON.stringify(body)})
      .then(function (res) {
        if (!res.ok) {
          var refused = new Error("status " + res.status);
          refused.status = res.status;
          throw refused;
        }
        if (!res.body || !res.body.getReader) throw new Error("no stream");
        var reader = res.body.getReader(), dec = new TextDecoder(), buf = "";
        function frame(chunk) {
          var event = "message", data = "";
          chunk.split("\n").forEach(function (line) {
            if (line.indexOf("event:") === 0) event = line.slice(6).trim();
            else if (line.indexOf("data:") === 0) data += line.slice(5).trim();
          });
          var payload = null;
          if (data) { try { payload = JSON.parse(data); } catch (e) { return; } }
          on(event, payload);
        }
        function pump() {
          return reader.read().then(function (step) {
            if (step.done) {
              if (buf.trim()) frame(buf);
              return;
            }
            buf += dec.decode(step.value, {stream: true});
            var parts = buf.split("\n\n");
            buf = parts.pop();
            parts.forEach(frame);
            return pump();
          });
        }
        return pump();
      });
  }

  // What paint() draws, without a page to draw it on.
  function tokensFor(text, hits, done) {
    text = String(text || "");
    var tail = "", held = shield(text);
    if (!done && (held.split("**").length - 1) % 2) {
      var at = held.lastIndexOf("**");
      tail = unshield(held.slice(at + 2));
      text = unshield(held.slice(0, at));
    }
    var tokens = find(text, hits);
    if (tail) tokens.push({text: tail, bold: true});
    return tokens;
  }

  var api = {find: find, order: order, write: write, paint: paint,
             tokensFor: tokensFor,
             stream: stream, ytId: ytId, seconds: seconds, linkAt: linkAt};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Cite = api;
})(typeof window !== "undefined" ? window : this);
