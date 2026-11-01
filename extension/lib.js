/* The parts of the extension that are plain logic: reading post ids out of
   X's links, checking every reply from the server before anything uses it,
   splitting text into words and seekable times, and the little bit of
   markdown the summaries use.

   Loaded three ways: by background.js (importScripts), as the first
   content script on x.com, and by node for the tests in test/. It touches
   no DOM and no chrome API, which is what lets the tests run it as is. */

var MBS = (function () {
  "use strict";

  var API = "https://search.lexthedev.com";
  var ARCHIVES = ["podcast", "mcg", "rasmr", "threadguy"];
  var POST_ID = /^[0-9]{5,25}$/;
  // "/blknoiz06/status/2116220650058846455", relative or absolute, with
  // anything after it. X's handles are 1 to 15 of letters, digits and _.
  var STATUS_PATH = /^(?:https:\/\/(?:x|twitter)\.com)?\/[A-Za-z0-9_]{1,15}\/status\/([0-9]{5,25})(?:[/?#]|$)/;
  // The server's own pattern (app/x_posts.py TIME): h:mm:ss or m:ss with
  // two digits after every colon, so a ratio like 3:1 is not a time.
  var TIME = /(?<![\d:])(\d{1,2}:\d{2}(?::\d{2})?)(?![\d:])/g;
  var TOKEN = /^\d{1,2}:\d{2}(?::\d{2})?$/;
  // The whole show at a second, in the three shapes the site links to it:
  // X's broadcast player, an X post, a YouTube video. The server's own
  // pattern (app/x_posts.py LINK); a link of any other shape is dropped,
  // so the panel opens nothing it was merely sent.
  var LINK = /^https:\/\/(?:x\.com\/i\/broadcasts\/[A-Za-z0-9]{8,20}\?t=[0-9]{1,6}|x\.com\/[A-Za-z0-9_]{1,15}\/status\/[0-9]{5,25}\?t=[0-9]{1,6}|www\.youtube\.com\/watch\?v=[A-Za-z0-9_-]{11}&t=[0-9]{1,6}s)$/;

  var MAX_TEXT = 20000;   // a summary is about 3,500 characters
  var MAX_TITLE = 300;
  var MAX_QUESTION = 500;

  function isPostId(value) {
    return typeof value === "string" && POST_ID.test(value);
  }

  function postIdFromHref(href) {
    if (typeof href !== "string") return null;
    var found = STATUS_PATH.exec(href);
    return found ? found[1] : null;
  }

  function str(value, max) {
    return typeof value === "string" ? value.slice(0, max) : "";
  }

  // Seconds by time-as-written. Anything that is not a whole, non-negative,
  // plausible second is dropped rather than trusted: a time button sets the
  // player's clock and nothing else, so a bad value can only mis-seek, but
  // there is no reason to let it do even that.
  function cleanTimes(value) {
    var out = {};
    if (!value || typeof value !== "object" || Array.isArray(value)) return out;
    Object.keys(value).slice(0, 500).forEach(function (key) {
      var seconds = value[key];
      if (TOKEN.test(key) &&
          typeof seconds === "number" && Number.isInteger(seconds) &&
          seconds >= 0 && seconds < 24 * 3600) {
        out[key] = seconds;
      }
    });
    return out;
  }

  // Time as written to the whole show at that second, for a clip, whose
  // own player only holds its own minute.
  // The two of those that open at the second. X opens a post's video at
  // its start whatever ?t= says, so a time is never a link to one.
  var SEEKS = /^https:\/\/(?:x\.com\/i\/broadcasts\/[A-Za-z0-9]{8,20}\?t=[0-9]{1,6}|www\.youtube\.com\/watch\?v=[A-Za-z0-9_-]{11}&t=[0-9]{1,6}s)$/;

  function seeks(url) {
    return typeof url === "string" && SEEKS.test(url);
  }

  function cleanLinks(value) {
    var out = {};
    if (!value || typeof value !== "object" || Array.isArray(value)) return out;
    Object.keys(value).slice(0, 500).forEach(function (key) {
      if (TOKEN.test(key) && seeks(value[key])) {
        out[key] = value[key];
      }
    });
    return out;
  }

  function link(value) {
    return typeof value === "string" && LINK.test(value) ? value : "";
  }

  // Where a clip is from in the show, and what was said either side of
  // it. No valid start, no clip panel: the post then shows as what it
  // is, with the episode's summary and the ask box.
  function cleanClip(value) {
    if (!value || typeof value !== "object" || !TOKEN.test(value.at)) return null;
    return {
      at: value.at,
      until: TOKEN.test(value.until) ? value.until : "",
      watch: link(value.watch),
      before: str(value.before, 1500),
      after: str(value.after, 3000),
      afterAt: TOKEN.test(value.after_at) ? value.after_at : "",
      afterWatch: seeks(value.after_watch) ? value.after_watch : ""
    };
  }

  // The moment a link post of ours points to: its time in the show and
  // the broadcast there. Only a link that opens at the second counts; a
  // post's own card cannot start at one, so without it there is nothing
  // to point at.
  function cleanMoment(value) {
    if (!value || typeof value !== "object" || !TOKEN.test(value.at) || !seeks(value.watch)) {
      return null;
    }
    return { at: value.at, watch: value.watch };
  }

  function cleanShelf(value) {
    var posts = {};
    var given = value && value.posts;
    if (!given || typeof given !== "object" || Array.isArray(given)) return null;
    Object.keys(given).slice(0, 50000).forEach(function (id) {
      if (isPostId(id) && ARCHIVES.indexOf(given[id]) !== -1) posts[id] = given[id];
    });
    return posts;
  }

  // The panel's data. The site link is rebuilt here from the archive name,
  // never taken from the reply, so the panel opens no address it was sent.
  var PAGES = { podcast: "/demo/podcast.html", mcg: "/demo/mcg.html",
                rasmr: "/demo/rasmr.html", threadguy: "/demo/threadguy.html" };

  function pageFor(archive, episodeId) {
    if (archive === "podcast" && /^[A-Za-z0-9_-]{1,40}$/.test(episodeId)) {
      return API + PAGES.podcast + "#ep-" + episodeId;
    }
    return API + (PAGES[archive] || "/");
  }

  function cleanPost(value) {
    if (!value || typeof value !== "object") return null;
    if (!isPostId(value.post) || ARCHIVES.indexOf(value.archive) === -1) return null;
    var episodeId = str(value.episode_id, 40);
    var clip = value.kind === "clip" ? cleanClip(value.clip) : null;
    var moment = value.kind === "link" ? cleanMoment(value.link) : null;
    return {
      post: value.post,
      kind: clip ? "clip" : moment ? "link" : "recording",
      clip: clip,
      link: moment,
      archive: value.archive,
      archiveName: str(value.archive_name, 40),
      title: str(value.title, MAX_TITLE),
      show: str(value.show, 60),
      publishedAt: /^\d{4}-\d{2}-\d{2}$/.test(value.published_at || "") ? value.published_at : "",
      summary: str(value.summary, MAX_TEXT),
      times: cleanTimes(value.times),
      links: cleanLinks(value.links),
      seekable: value.seekable === true,
      page: pageFor(value.archive, episodeId)
    };
  }

  function cleanAnswer(value) {
    if (!value || typeof value !== "object") return null;
    return {
      answer: str(value.answer, MAX_TEXT),
      refused: value.refused === true,
      times: cleanTimes(value.times),
      links: cleanLinks(value.links)
    };
  }

  // A line of text as pieces: plain words, bold words, and times. A time
  // is only a time if the server gave its X second, or for a clip a link
  // to the whole show at it; the rest stay words, because a time with
  // neither is one nothing can play.
  function pieces(line, times, links) {
    links = links || {};
    var out = [];
    String(line).split(/(\*\*[^*]+\*\*)/).forEach(function (chunk) {
      if (!chunk) return;
      var bold = /^\*\*[^*]+\*\*$/.test(chunk);
      var text = bold ? chunk.slice(2, -2) : chunk;
      var last = 0;
      text.replace(TIME, function (match, token, offset) {
        var own = Object.prototype.hasOwnProperty.call(times, token);
        if (own || Object.prototype.hasOwnProperty.call(links, token)) {
          if (offset > last) out.push({ text: text.slice(last, offset), bold: bold });
          out.push(own ? { time: token, seconds: times[token], bold: bold }
                       : { time: token, link: links[token], bold: bold });
          last = offset + match.length;
        }
        return match;
      });
      if (last < text.length) out.push({ text: text.slice(last), bold: bold });
    });
    return out;
  }

  // No em or en dashes in anything the panel shows: the account never
  // writes them, and 76 of the 79 summaries had one. A dash between words
  // reads as a comma; one between two numbers is a range, so a hyphen.
  function undash(line) {
    return line
      .replace(/(\d)\s*[–—]\s*(\d)/g, "$1-$2")
      .replace(/\s*[–—]+\s*/g, ", ")
      .replace(/^,\s*|,\s*$/g, "")
      .replace(/,\s*([,.;:!?])/g, "$1");
  }

  // The summaries' markdown, as blocks: a heading on its own line, a list
  // item, or a paragraph. Square brackets around a time are dropped, the
  // way the site shows them. A summary's opening "**TL;DR** — ..." becomes
  // a heading over its paragraph, the same as "Topics" below it.
  function blocks(markdown, times, links) {
    var out = [];
    String(markdown || "").split(/\r?\n/).forEach(function (raw) {
      var line = raw.trim().replace(/\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g, "$1");
      var label = /^\*\*(TL;DR)\*\*\s*[–—:-]?\s*(.+)$/i.exec(line);
      if (label) {
        out.push({ kind: "heading", parts: [{ text: label[1], bold: true }] });
        line = label[2];
      }
      line = undash(line);
      if (!line) return;
      var heading = /^\*\*([^*]+)\*\*$/.exec(line) || /^#{1,4}\s+(.+)$/.exec(line);
      if (heading) {
        out.push({ kind: "heading", parts: [{ text: heading[1], bold: true }] });
      } else if (/^[-*]\s+/.test(line)) {
        out.push({ kind: "item", parts: pieces(line.replace(/^[-*]\s+/, ""), times, links) });
      } else {
        out.push({ kind: "para", parts: pieces(line, times, links) });
      }
    });
    return out;
  }

  // Every time the pattern finds, as written. The server runs the same
  // pattern; tests/test_x_extension.py checks the two agree.
  function timeTokens(text) {
    var out = [];
    String(text || "").replace(TIME, function (match, token) { out.push(token); return match; });
    return out;
  }

  function cleanQuestion(value) {
    if (typeof value !== "string") return "";
    // Cut at 500, never between the two halves of an emoji: half of one is
    // not text, and the server refuses it.
    return value.replace(/\s+/g, " ").trim().slice(0, MAX_QUESTION).replace(/[\uD800-\uDBFF]$/, "");
  }

  function longDate(iso) {
    var months = ["january", "february", "march", "april", "may", "june", "july",
                  "august", "september", "october", "november", "december"];
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
    if (!m || +m[2] < 1 || +m[2] > 12 || +m[3] < 1 || +m[3] > 31) return "";
    return months[parseInt(m[2], 10) - 1] + " " + parseInt(m[3], 10) + ", " + m[1];
  }

  return {
    API: API, ARCHIVES: ARCHIVES, MAX_QUESTION: MAX_QUESTION,
    isPostId: isPostId, postIdFromHref: postIdFromHref,
    cleanShelf: cleanShelf, cleanPost: cleanPost, cleanAnswer: cleanAnswer,
    cleanTimes: cleanTimes, cleanLinks: cleanLinks, cleanClip: cleanClip,
    cleanQuestion: cleanQuestion, undash: undash, LINK: LINK, seeks: seeks,
    pieces: pieces, blocks: blocks, pageFor: pageFor, longDate: longDate,
    timeTokens: timeTokens
  };
})();

if (typeof module === "object" && module.exports) module.exports = MBS;
