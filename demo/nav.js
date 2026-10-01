/* One archive switcher, on every page.
 *
 * Each page used to hand-write its own nav, so they disagreed: the Musk
 * page offered Market Bubble, MCG and Method; the MCG page offered
 * Market Bubble, Musk, Tokens and Search; the finance archive was in
 * none of them and was reachable only by typing the URL. Adding a fifth
 * archive meant editing five files and forgetting one.
 *
 * So the list lives here. Adding an archive is one row.
 *
 * Built as <details>/<summary> rather than a scripted menu: it opens
 * with a click or the keyboard, closes on Escape, and works before this
 * file has loaded because the page's own markup is the fallback.
 * Palette-agnostic on purpose, since the four pages are four different
 * rooms: it inherits currentColor and reads a --card / --paper variable
 * when the page defines one.
 */
(function () {
  "use strict";

  var ARCHIVES = [
    { href: "/",        name: "Market Bubble", note: "the broadcast" },
    { href: "/mcg",     name: "MCG Live",      note: "one project per episode" },
    { href: "/elon",    name: "Elon Musk",     note: "long-form interviews" },
    { href: "/finance", name: "The Record",    note: "finance, on tape" },
    { href: "/threadguy", name: "ThreadGuy",   note: "the morning show" }
  ];

  var TOOLS = [
    { href: "/demo/assets.html", name: "Market Bubble tokens" },
    { href: "/mcg/assets",       name: "MCG tokens" },
    { href: "/threadguy/tokens", name: "ThreadGuy tokens" }
  ];

  function here(href) {
    var path = location.pathname.replace(/\/+$/, "") || "/";
    if (href === "/") return path === "/" || /podcast\.html$/.test(path);
    if (href === "/finance") return /finance/.test(path);
    if (href === "/elon") return /elon|musk/.test(path);
    if (href === "/mcg") return path === "/mcg" || /\/mcg\.html$/.test(path);
    if (href === "/threadguy") return /threadguy/.test(path);
    return path === href;
  }

  function style() {
    if (document.getElementById("nav-switch-style")) return;
    var css = document.createElement("style");
    css.id = "nav-switch-style";
    css.textContent = [
      ".archsw{position:relative;display:inline-block}",
      ".archsw>summary{list-style:none;cursor:pointer;user-select:none}",
      ".archsw>summary::-webkit-details-marker{display:none}",
      ".archsw>summary::after{content:'';display:inline-block;width:.42em;",
      "height:.42em;margin-left:.5em;vertical-align:.12em;",
      "border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;",
      "transform:rotate(45deg);opacity:.6}",
      ".archsw[open]>summary::after{transform:rotate(225deg);opacity:1}",
      ".archsw .menu{position:absolute;z-index:60;top:calc(100% + 9px);left:0;",
      "min-width:236px;max-width:calc(100vw - 16px);padding:6px;border-radius:8px;",
      "background:var(--card,var(--paper,#14161a));",
      "border:1px solid color-mix(in srgb, currentColor 22%, transparent);",
      "box-shadow:0 14px 34px rgba(0,0,0,.28)}",
      ".archsw .menu a{display:block;padding:8px 10px;border-radius:5px;",
      "text-decoration:none;color:inherit;line-height:1.25}",
      ".archsw .menu a:hover{background:color-mix(in srgb, currentColor 10%, transparent)}",
      ".archsw .menu a[aria-current=page]{",
      "background:color-mix(in srgb, currentColor 14%, transparent)}",
      ".archsw .menu .n{display:block;font-size:14px}",
      ".archsw .menu .s{display:block;font-size:11.5px;opacity:.62;margin-top:1px}",
      ".archsw .menu hr{border:0;height:1px;margin:6px 4px;",
      "background:color-mix(in srgb, currentColor 16%, transparent)}",
      /* The way home, on every archive. Each page's corner carries its
         own name, so the parent sits in front of it rather than replacing
         it, and it inherits the page's colour like the menu does. */
      ".brandline{display:inline-flex;align-items:baseline;gap:10px;",
      "flex-wrap:wrap;min-width:0}",
      /* Set in the monospace every page already uses for its small
         labels. Left to inherit, it fell through to the browser's default
         serif on the pages whose corner is a bold sans. */
      ".home-crumb{color:inherit;text-decoration:none;opacity:.62;",
      "font-family:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace;",
      "font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;",
      "font-weight:500;white-space:nowrap;transition:opacity .2s}",
      ".home-crumb:hover,.home-crumb:focus-visible{opacity:1}",
      ".home-sep{opacity:.3}",
      /* On a phone the header stays: scrolled past the top there was no
         way to switch archives at all. The page's own background runs
         edge to edge behind it, and it slims once the page is scrolled. */
      "@media (max-width:760px){",
      ".navbar{position:sticky;top:0;z-index:50;transition:padding .2s ease}",
      /* A layer the width of the screen behind the bar. Not a shadow
         spread and a clip, which also clipped the open menu to the bar. */
      ".navbar::before{content:'';position:absolute;top:0;bottom:0;",
      "left:var(--navx,0);width:100vw;background:var(--navbg);z-index:-1}",
      ".archsw>summary{white-space:nowrap}",
      ".navbar.stuck{padding-top:12px!important;padding-bottom:12px!important}",
      "}"
    ].join("");
    document.head.appendChild(css);
  }

  function build() {
    var d = document.createElement("details");
    d.className = "archsw";
    var current = ARCHIVES.filter(function (a) { return here(a.href); })[0];

    var s = document.createElement("summary");
    s.textContent = current ? current.name : "Archives";
    d.appendChild(s);

    var menu = document.createElement("div");
    menu.className = "menu";
    ARCHIVES.forEach(function (a) {
      var el = document.createElement("a");
      el.href = a.href;
      if (here(a.href)) el.setAttribute("aria-current", "page");
      el.innerHTML = '<span class="n"></span><span class="s"></span>';
      el.firstChild.textContent = a.name;
      el.lastChild.textContent = a.note;
      menu.appendChild(el);
    });
    menu.appendChild(document.createElement("hr"));
    TOOLS.forEach(function (tool) {
      var el = document.createElement("a");
      el.href = tool.href;
      el.innerHTML = '<span class="n"></span>';
      el.firstChild.textContent = tool.name;
      menu.appendChild(el);
    });
    d.appendChild(menu);

    // Some navs sit hard right (MCG, the token pages), where a menu
    // opening leftward runs off the viewport and clips its own text.
    // Measured on open rather than guessed from a breakpoint, because
    // it depends on where this nav happens to be, not how wide the
    // screen is.
    // On a narrow phone neither edge fits: the menu is wider than the
    // room on either side of the button, and it opened off screen with
    // nothing in it reachable. So it is moved by exactly as much as it
    // overhangs, whichever side that is.
    d.addEventListener("toggle", function () {
      if (!d.open) return;
      menu.style.left = "0";
      menu.style.right = "auto";
      var box = menu.getBoundingClientRect();
      var room = document.documentElement.clientWidth - 8;
      var shift = 0;
      if (box.right > room) shift = room - box.right;
      if (box.left + shift < 8) shift = 8 - box.left;
      menu.style.left = shift + "px";
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") d.removeAttribute("open");
    });
    document.addEventListener("click", function (e) {
      if (d.hasAttribute("open") && !d.contains(e.target)) {
        d.removeAttribute("open");
      }
    });
    return d;
  }

  // The bar a phone keeps on screen: the page's header, or failing that
  // the block the menu sits in.
  function pin(host) {
    // The page's header, or on a page without one around the menu (The
    // Record keeps it in a small nav at the end of its top line) the
    // first block that spans the page, so the whole row stays rather
    // than a button floating at the right.
    var bar = host.closest("header");
    if (!bar) {
      bar = host.parentElement;
      while (bar && bar.parentElement && bar.parentElement !== document.body &&
             bar.getBoundingClientRect().width < 0.7 * window.innerWidth) {
        bar = bar.parentElement;
      }
    }
    if (!bar) return;
    var bg = getComputedStyle(document.body).backgroundColor;
    if (!bg || bg === "rgba(0, 0, 0, 0)" || bg === "transparent") {
      bg = getComputedStyle(document.documentElement).backgroundColor;
    }
    bar.style.setProperty("--navbg", bg);
    bar.classList.add("navbar");
    // The background layer starts at the screen's left edge, wherever
    // the bar sits in its column.
    function place() {
      bar.style.setProperty("--navx", -bar.getBoundingClientRect().left + "px");
    }
    place();
    window.addEventListener("resize", place);
    var ticking = false;
    function check() {
      ticking = false;
      bar.classList.toggle("stuck", window.scrollY > 24);
    }
    window.addEventListener("scroll", function () {
      if (!ticking) { ticking = true; requestAnimationFrame(check); }
    }, {passive: true});
    check();
  }

  function mount() {
    var host = document.querySelector("[data-archives]");
    if (!host) return;
    style();
    host.innerHTML = "";
    host.appendChild(build());
    pin(host);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
