/* Types an answer out on screen, the way the Market Bubble page always has.
 *
 * The model's words arrive in bursts: nothing for a moment, then forty
 * words at once. The other pages drew each burst as it landed, so their
 * answers jumped a paragraph at a time. This keeps a queue and lets out a
 * few characters every frame, more when it falls behind, so the answer
 * reads as one steady line of writing however the network delivers it.
 * It is podcast.html's Typer, moved here so every page types at the same
 * pace.
 *
 *   var t = Typer(function (text) { ...draw text... });
 *   t.push(piece);               // as each piece arrives
 *   t.finish(function (all) {}); // stream over; runs once the queue is drawn
 *   t.set(message);              // say something else instead
 *   t.stop();                    // a newer question replaced this one
 *
 * An answer that arrives whole (The Record, the posts page: both check
 * the finished answer before showing it) is pushed in one go and typed
 * out the same way.
 *
 * Also the caret: give the element the answer is drawn in the class
 * "typing" while it is being written. Its colour is the page's own,
 * from --caret, and it sits at the end of the last line of text.
 */
(function (root) {
  "use strict";

  // Two rules, not one with a comma: a browser without :has() drops a
  // whole rule it cannot parse, and would lose the caret on every page.
  var CSS = [
    ".typing > p:last-child::after{content:'';display:inline-block;width:.45em;height:.95em;",
    "margin-left:3px;vertical-align:-.12em;background:var(--caret,currentColor);",
    "animation:typer-blink 1s step-end infinite}",
    ".typing:not(:has(> p))::after{content:'';display:inline-block;width:.45em;height:.95em;",
    "margin-left:3px;vertical-align:-.12em;background:var(--caret,currentColor);",
    "animation:typer-blink 1s step-end infinite}",
    "@keyframes typer-blink{0%,100%{opacity:1}50%{opacity:0}}",
    "@media (prefers-reduced-motion:reduce){.typing > p:last-child::after,",
    ".typing::after{animation:none}}"
  ].join("");

  function addStyle() {
    if (typeof document === "undefined" || document.getElementById("typer-css")) return;
    var tag = document.createElement("style");
    tag.id = "typer-css";
    tag.textContent = CSS;
    (document.head || document.documentElement).appendChild(tag);
  }
  addStyle();

  function stillMotion() {
    try {
      return !!(root.matchMedia &&
                root.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (e) { return false; }
  }

  // How many characters one frame lets out: a twelfth of what is waiting,
  // never fewer than 2 or more than 14, so a big burst catches up without
  // landing in one jump and a trickle still moves.
  function step(waiting) {
    return Math.min(14, Math.max(2, Math.ceil(waiting / 12)));
  }

  // A frame is 16ms. A tick that comes later than that (the timer below
  // standing in for frames that never came) lets out as many frames'
  // worth, so the answer types at the same speed however it is driven.
  function owed(elapsed) {
    return Math.max(1, Math.min(60, Math.round(elapsed / 16)));
  }

  function Typer(paint) {
    var shown = "", queue = "", done = false, stopped = false;
    var frame = null, backup = null, last = 0, onDone = null;

    function hidden() {
      return typeof document !== "undefined" && document.hidden;
    }
    function waiting() { return frame !== null || backup !== null; }
    // The two kinds of id are separate counters: cancelling a frame id as
    // a timer could cancel somebody else's timer.
    function clear() {
      if (frame !== null) { root.cancelAnimationFrame(frame); frame = null; }
      if (backup !== null) { clearTimeout(backup); backup = null; }
    }
    function run() { clear(); tick(); }

    // Animation frames, with a timer behind every one. Browsers pause
    // frames in a background tab, and a loop that depended on them alone
    // never finished there: the answer stopped halfway and the Ask button
    // stayed disabled until a reload. A page can also say it is visible
    // and still get no frames (an occluded window, a preview pane, a phone
    // saving battery), which document.hidden cannot tell; the timer covers
    // that too. Whichever fires first cancels the other.
    function schedule() {
      var away = hidden();
      if (!away && typeof root.requestAnimationFrame === "function") {
        frame = root.requestAnimationFrame(run);
      }
      backup = setTimeout(run, away ? 100 : 120);
    }

    function tick() {
      if (stopped) return;
      var now = Date.now(), elapsed = last ? now - last : 16;
      last = now;
      if (queue.length) {
        // Nobody is watching a hidden tab, and someone who asked for
        // reduced motion asked not to watch it type: both get it whole.
        var n = hidden() || stillMotion() ? queue.length
              : Math.min(queue.length, step(queue.length) * owed(elapsed));
        shown += queue.slice(0, n);
        queue = queue.slice(n);
        paint(shown);
      }
      if (queue.length || !done) { schedule(); return; }
      if (onDone) { var cb = onDone; onDone = null; cb(shown); }
    }

    return {
      push: function (piece) {
        if (stopped || !piece) return;
        queue += piece;
        if (!waiting()) { last = 0; schedule(); }
      },
      finish: function (cb) {
        if (stopped) return;
        done = true;
        onDone = cb || null;
        if (!waiting()) {
          if (queue) { shown += queue; queue = ""; paint(shown); }
          if (onDone) { var call = onDone; onDone = null; call(shown); }
        }
      },
      stop: function () {
        stopped = true; onDone = null;
        clear();
      },
      // Replaces whatever is there, for a message rather than an answer.
      set: function (text) { queue = ""; shown = String(text || ""); paint(shown); },
      text: function () { return shown + queue; }
    };
  }

  Typer.step = step;
  Typer.owed = owed;
  if (typeof module !== "undefined" && module.exports) module.exports = Typer;
  else root.Typer = Typer;
})(typeof window !== "undefined" ? window : this);
