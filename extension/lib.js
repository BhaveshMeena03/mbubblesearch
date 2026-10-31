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
  var ARCHIVES = ["podcast", "mcg", "rasmr"];
  var POST_ID = /^[0-9]{5,25}$/;
  // "/blknoiz06/status/2116220650058846455", relative or absolute, with
  // anything after it. X's handles are 1 to 15 of letters, digits and _.
  var STATUS_PATH = /^(?:https:\/\/(?:x|twitter)\.com)?\/[A-Za-z0-9_]{1,15}\/status\/([0-9]{5,25})(?:[/?#]|$)/;
  // The server's own pattern (app/x_posts.py TIME): h:mm:ss or m:ss with
  // two digits after every colon, so a ratio like 3:1 is not a time.
  var TIME = /(?<![\d:])(\d{1,2}:\d{2}(?::\d{2})?)(?![\d:])/g;

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
      if (/^\d{1,2}:\d{2}(?::\d{2})?$/.test(key) &&
          typeof seconds === "number" && Number.isInteger(seconds) &&
          seconds >= 0 && seconds < 24 * 3600) {
        out[key] = seconds;
      }
    });
    return out;
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
                rasmr: "/demo/rasmr.html" };

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
    return {
      post: value.post,
      archive: value.archive,
      archiveName: str(value.archive_name, 40),
      title: str(value.title, MAX_TITLE),
      show: str(value.show, 60),
      publishedAt: /^\d{4}-\d{2}-\d{2}$/.test(value.published_at || "") ? value.published_at : "",
      summary: str(value.summary, MAX_TEXT),
      times: cleanTimes(value.times),
      seekable: value.seekable === true,
      page: pageFor(value.archive, episodeId)
    };
  }

  function cleanAnswer(value) {
    if (!value || typeof value !== "object") return null;
    return {
      answer: str(value.answer, MAX_TEXT),
      refused: value.refused === true,
      times: cleanTimes(value.times)
    };
  }

  // A line of text as pieces: plain words, bold words, and times. A time
  // is only a time if the server gave its X second; the rest stay words,
  // because a time with no second on X is one the player cannot reach.
  function pieces(line, times) {
    var out = [];
    String(line).split(/(\*\*[^*]+\*\*)/).forEach(function (chunk) {
      if (!chunk) return;
      var bold = /^\*\*[^*]+\*\*$/.test(chunk);
      var text = bold ? chunk.slice(2, -2) : chunk;
      var last = 0;
      text.replace(TIME, function (match, token, offset) {
        if (Object.prototype.hasOwnProperty.call(times, token)) {
          if (offset > last) out.push({ text: text.slice(last, offset), bold: bold });
          out.push({ time: token, seconds: times[token], bold: bold });
          last = offset + match.length;
        }
        return match;
      });
      if (last < text.length) out.push({ text: text.slice(last), bold: bold });
    });
    return out;
  }

  // The summaries' markdown, as blocks: a heading on its own line, a list
  // item, or a paragraph. Square brackets around a time are dropped, the
  // way the site shows them.
  function blocks(markdown, times) {
    var out = [];
    String(markdown || "").split(/\r?\n/).forEach(function (raw) {
      var line = raw.trim().replace(/\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g, "$1");
      if (!line) return;
      var heading = /^\*\*([^*]+)\*\*$/.exec(line) || /^#{1,4}\s+(.+)$/.exec(line);
      if (heading) {
        out.push({ kind: "heading", parts: [{ text: heading[1], bold: true }] });
      } else if (/^[-*]\s+/.test(line)) {
        out.push({ kind: "item", parts: pieces(line.replace(/^[-*]\s+/, ""), times) });
      } else {
        out.push({ kind: "para", parts: pieces(line, times) });
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
    return value.replace(/\s+/g, " ").trim().slice(0, MAX_QUESTION);
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
    cleanTimes: cleanTimes, cleanQuestion: cleanQuestion,
    pieces: pieces, blocks: blocks, pageFor: pageFor, longDate: longDate,
    timeTokens: timeTokens
  };
})();

if (typeof module === "object" && module.exports) module.exports = MBS;
