/* Play a moment where it is cited.
 *
 * An episode page lists what was covered beside the time it was said.
 * Each time used to be a link out to the broadcast on X, and X opens a
 * broadcast at a second only for somebody logged in on a desktop: on a
 * phone, in the app or logged out, the link started the show from the
 * beginning. So a time whose moment is on YouTube is played here, under
 * the line it belongs to, in the one player that can be put in a page and
 * started at a second.
 *
 * The page is whole without this file. Each time is an ordinary link to
 * the same second on YouTube; this only keeps the reader on the page. A
 * time with no YouTube copy carries no data-yt, and stays a link to X.
 */
(function () {
  "use strict";

  var ID = /^[A-Za-z0-9_-]{11}$/;
  var open = null;                    // the time being played, and its player

  function shut() {
    if (!open) return;
    open.box.remove();
    open.link.classList.remove("on");
    open.link.setAttribute("aria-expanded", "false");
    open = null;
  }

  function player(link, video, at) {
    var box = document.createElement("div");
    box.className = "player";

    var frame = document.createElement("iframe");
    // No cookies until it is played, and nothing suggested afterwards
    // but the same channel: this is a citation, not a way into YouTube.
    frame.src = "https://www.youtube-nocookie.com/embed/" + video
      + "?start=" + at + "&autoplay=1&rel=0&modestbranding=1&playsinline=1";
    frame.title = "The recording, from " + link.textContent;
    frame.setAttribute("allow", "autoplay; encrypted-media; fullscreen; picture-in-picture");
    frame.setAttribute("allowfullscreen", "");
    box.appendChild(frame);

    var bar = document.createElement("p");
    bar.className = "player-bar";
    var out = document.createElement("a");
    out.href = link.href;
    out.target = "_blank";
    out.rel = "noopener";
    out.textContent = "Open on YouTube";
    var close = document.createElement("button");
    close.type = "button";
    close.textContent = "Close";
    close.addEventListener("click", function () {
      var back = open && open.link;
      shut();
      if (back) back.focus();
    });
    bar.appendChild(out);
    bar.appendChild(close);
    box.appendChild(bar);
    return box;
  }

  document.addEventListener("click", function (event) {
    var link = event.target.closest ? event.target.closest("a.at[data-yt]") : null;
    if (!link) return;
    // A click that asks for a new tab or a new window gets one.
    if (event.defaultPrevented || event.button !== 0 || event.metaKey
        || event.ctrlKey || event.shiftKey || event.altKey) return;
    var video = link.getAttribute("data-yt");
    var at = parseInt(link.getAttribute("data-at"), 10);
    var row = link.closest(".topic");
    if (!ID.test(video || "") || !(at >= 0) || !row) return;   // leave it a link
    event.preventDefault();

    // The time that is playing, pressed again, closes it.
    var same = open && open.link === link;
    shut();
    if (same) return;

    var box = player(link, video, at);
    row.parentNode.insertBefore(box, row.nextSibling);
    link.classList.add("on");
    link.setAttribute("aria-expanded", "true");
    open = { link: link, box: box };

    // On a small screen the player can land below the fold.
    var edge = box.getBoundingClientRect();
    if (edge.bottom > window.innerHeight || edge.top < 0) {
      box.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && open) {
      var back = open.link;
      shut();
      back.focus();
    }
  });
})();
