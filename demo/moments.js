/* Question cards for the archive pages: a question, the sentence that
 * answers it, the frame at the second it was said, and the moment itself,
 * played under the cards.
 *
 * The front door and The Record carry their own. This is the one the four
 * channel pages share, so a fifth archive is a list of four entries here
 * and a section on its page:
 *
 *   <section class="mm" data-moments="threadguy"
 *            style="--mm-accent:#6aa9ff; ...">...</section>
 *
 * Every sentence was read off the stored transcript at the second given
 * and the speaker checked against the picture. Market Bubble's and the
 * Musk archive's are held to their transcripts by tests/test_moments.py;
 * MCG's and ThreadGuy's transcripts live only in the index, so those were
 * checked there when they were chosen. The frames are served from here,
 * demo/moments/<video>-<second>.webp, not taken from YouTube's thumbnail,
 * which shows whatever the uploader picked.
 */
(function () {
  "use strict";

  var DATA = {
    podcast: [
      {vid: "F4OhqZjtVkY", at: 1556, who: "Ansem",
       q: "Why does Ansem think Ethereum is done?",
       said: "I said it the first episode, I said it this year, I said it last year, I said the year before that, Ethereum is going to [expletive] zero.",
       where: "Market Bubble #4", when: "22 May 2026"},
      {vid: "cfjK9E7MHzI", at: 4585, who: "Kendrick Perkins",
       q: "How did Kendrick Perkins' NBA career end?",
       said: "I didn't retire from the NBA on my own terms. I was forced into retirement, meaning that damn phone stopped ringing.",
       where: "Market Bubble #12", when: "24 Jul 2026"},
      {vid: "TYX2FuacIhE", at: 6263, who: "Lucas Bruder, Jito Labs",
       q: "Why did Jito do its airdrop?",
       said: "We really took the approach like let's give all these like little guys a ton of money and kind of just see what happens.",
       where: "Market Bubble #11", when: "17 Jul 2026"},
      {vid: "wksdB2BgW3g", at: 979, who: "Tristan Thompson",
       q: "What did BlackRock tell Tristan Thompson about Bitcoin?",
       said: "Telling them I want to buy BTC and they're like, 'Nah, it's too volatile. Don't [expletive] with it.'",
       where: "Market Bubble", when: "14 May 2026"}
    ],
    mcg: [
      {vid: "cHos0BsSoFM", at: 1609, who: "Bunny, ClawPump",
       q: "Why does ClawPump buy back its token but not burn it?",
       said: "So that is the reason why we're doing programmatic buybacks, but we're not doing programmatic burns.",
       where: "MCG Live", when: "6 Oct 2026"},
      {vid: "oSkWYs5rAGk", at: 334, who: "Bunny, ClawPump",
       q: "Where does agentic finance fall short?",
       said: "I think that anyone can tweet about agentic finance, but as soon as you start trying out a lot of the products, that is where things fall short.",
       where: "MCG Live", when: "21 Aug 2026"},
      {vid: "SHaUpI7qAUE", at: 947, who: "Ratspeak",
       q: "Why can a mesh network not hold a message for later?",
       said: "I can't send a message to a friend offline and they'll just get it when they're online later. There's no cloud to store it.",
       where: "MCG Live", when: "28 Aug 2026"}
    ],
    threadguy: [
      {vid: "QH2d-awq0No", at: 734, who: "ThreadGuy",
       q: "What is the Sweetgreen chud wrap thesis?",
       said: "A new investment thesis that is based off of a 1200 calorie slop wrap from Sweet Green and it's up 45% since that moment.",
       where: "Stream recap", when: "23 May 2026"},
      {vid: "xNn_oX8zRJU", at: 3940, who: "ThreadGuy",
       q: "Why does ThreadGuy wish he held HYPE?",
       said: "If you buy iPhones every year, you should probably take the amount of money you put into an iPhone every year and like buy Apple stock.",
       where: "Live stream", when: "28 Jan 2026"},
      {vid: "2tMMfjBqgvo", at: 563, who: "Blurr",
       q: "What does Papertrade's builder think of other 1000x exchanges?",
       said: "I've yet to see a proper 1000x exchange where it wasn't just, internally, under the hood, the bucket shop.",
       where: "Papertrade Launch", when: "29 Sep 2026"}
    ],
    elon: [
      {vid: "sSOxPJD-VNo", at: 6301, who: "Elon Musk",
       q: "What did Elon say about heat shield tiles?",
       said: "No one has ever developed a fully reusable orbital heat shield.",
       where: "Joe Rogan Experience #2281", when: "28 Feb 2025"},
      {vid: "JN3KPFbWCy8", at: 2663, who: "Elon Musk",
       q: "What does Elon think consciousness is?",
       said: "Sometimes I wonder is, you know, either perhaps everything's conscious or nothing is conscious.",
       where: "Lex Fridman Podcast", when: "9 Nov 2023"},
      {vid: "Gbb2rV7Vpnw", at: 2154, who: "Elon Musk",
       q: "Why does Elon want life to be multi-planetary?",
       said: "A species that does not become multi-planetary is simply waiting around until there is some extinction event.",
       where: "Joe Rogan Experience #1609", when: "11 Feb 2021"},
      {vid: "tAJUwiAqW38", at: 9294, who: "Elon Musk",
       q: "Does Elon think AI will turn out well?",
       said: "I think the most likely outcome to be specific about it is a good outcome. Most likely a good outcome. But it's not for sure.",
       where: "Joe Rogan Experience #2054", when: "1 Nov 2023"}
    ]
  };

  var CSS = [
    "@font-face{font-family:'Instrument Serif';src:url('/demo/fonts/InstrumentSerif-Regular.ttf') format('truetype');font-display:swap}",
    ".mm{--mm-accent:#16c784;--mm-card:#12161c;--mm-line:#232a33;--mm-line2:#33404d;",
    "--mm-ink:#e6e8ea;--mm-ink2:#a9b2bc;--mm-ink3:#7d8692;--mm-hover:#161b23;",
    "margin:56px 0 0;padding-top:22px;border-top:1px solid var(--mm-line);color:var(--mm-ink)}",
    ".mm-h{font-family:'Instrument Serif',Georgia,serif;font-weight:400;font-size:36px;",
    "line-height:1.08;letter-spacing:-.01em;margin:0 0 8px;color:var(--mm-ink)}",
    ".mm-note{color:var(--mm-ink2);font-size:14.5px;margin:0 0 20px;max-width:64ch}",
    // Laid out by how many there are, so none is ever left alone on a row:
    // four go four across or two by two, three go three across.
    ".mm-grid{display:grid;gap:14px;grid-template-columns:minmax(0,1fr)}",
    "@media(min-width:620px){.mm-grid[data-n='4']{grid-template-columns:repeat(2,minmax(0,1fr))}}",
    "@media(min-width:860px){.mm-grid[data-n='3']{grid-template-columns:repeat(3,minmax(0,1fr))}}",
    "@media(min-width:1100px){.mm-grid[data-n='4']{grid-template-columns:repeat(4,minmax(0,1fr))}}",
    ".mm-card{appearance:none;font:inherit;color:inherit;text-align:left;cursor:pointer;",
    "display:flex;flex-direction:column;padding:0;overflow:hidden;position:relative;",
    "background:var(--mm-card);border:1px solid var(--mm-line);border-radius:3px;",
    "transition:border-color .22s,background .22s,transform .22s}",
    ".mm-card::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;",
    "background:var(--mm-accent);opacity:.6;z-index:2;transition:opacity .22s}",
    ".mm-card:hover{border-color:var(--mm-line2);background:var(--mm-hover);transform:translateY(-2px)}",
    ".mm-card:hover::before,.mm-card.on::before{opacity:1}",
    ".mm-card.on{border-color:var(--mm-accent)}",
    ".mm-card:focus-visible{outline:2px solid var(--mm-accent);outline-offset:3px}",
    ".mm-thumb{position:relative;aspect-ratio:16/9;background:#000;overflow:hidden}",
    ".mm-thumb img{display:block;width:100%;height:100%;object-fit:cover;opacity:.84;",
    "transition:opacity .22s,transform .5s}",
    ".mm-thumb::after{content:'';position:absolute;inset:0;pointer-events:none;",
    "background:linear-gradient(180deg,transparent 55%,rgba(0,0,0,.6))}",
    ".mm-card:hover .mm-thumb img,.mm-card.on .mm-thumb img{opacity:1;transform:scale(1.025)}",
    ".mm-play{position:absolute;left:50%;top:50%;z-index:1;width:46px;height:46px;margin:-23px 0 0 -23px;",
    "border-radius:50%;background:rgba(0,0,0,.6);border:1px solid rgba(255,255,255,.3);",
    "display:grid;place-items:center;transition:background .22s,border-color .22s}",
    ".mm-play::before{content:'';margin-left:3px;border-style:solid;border-width:7px 0 7px 12px;",
    "border-color:transparent transparent transparent #fff}",
    ".mm-card:hover .mm-play{background:var(--mm-accent);border-color:var(--mm-accent)}",
    ".mm-card:hover .mm-play::before{border-left-color:#000}",
    ".mm-at{position:absolute;right:10px;bottom:9px;z-index:1;font:11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;",
    "letter-spacing:.05em;color:#fff;background:rgba(0,0,0,.72);padding:3px 7px;border-radius:2px}",
    ".mm-body{display:flex;flex-direction:column;flex:1;padding:18px 20px 18px 22px}",
    ".mm-q{font-size:14px;font-weight:600;line-height:1.4;color:var(--mm-ink);padding-bottom:13px;",
    "margin-bottom:14px;border-bottom:1px solid var(--mm-line)}",
    ".mm-q::before{content:'Q';display:block;font:10px ui-monospace,SFMono-Regular,Menlo,monospace;",
    "letter-spacing:.16em;color:var(--mm-accent);margin-bottom:6px}",
    ".mm-said{font-family:'Instrument Serif',Georgia,serif;font-size:21px;line-height:1.3;flex:1;color:var(--mm-ink)}",
    ".mm-who{margin-top:16px;font-size:13.5px;font-weight:600;color:var(--mm-ink2)}",
    ".mm-where{margin-top:5px;display:flex;flex-wrap:wrap;gap:2px 0;",
    "font:11.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--mm-ink3)}",
    ".mm-where > *{white-space:nowrap}",
    ".mm-where > * + *::before{content:'\\00b7';margin:0 8px;color:var(--mm-ink3)}",
    ".mm-where b{color:var(--mm-accent);font-weight:500}",
    // The day on a line of its own, so every card's last line reads the same.
    ".mm-when{margin-top:2px;font:11.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--mm-ink3)}",
    ".mm-stage{margin-top:16px}",
    ".mm-stage iframe{display:block;width:100%;aspect-ratio:16/9;border:0;border-radius:3px;background:#000}",
    ".mm-cap{display:flex;justify-content:space-between;gap:12px;margin-top:8px;",
    "font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--mm-ink3)}",
    ".mm-cap button{appearance:none;background:none;border:0;font:inherit;color:var(--mm-ink2);cursor:pointer}",
    ".mm-cap button:hover{color:var(--mm-accent)}",
    "@media(max-width:640px){.mm-h{font-size:29px}.mm-said{font-size:19px}}",
    "@media(prefers-reduced-motion:reduce){.mm-card,.mm-thumb img{transition:none}.mm-card:hover{transform:none}}"
  ].join("");

  function stamp(sec) {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
    var two = function (n) { return (n < 10 ? "0" : "") + n; };
    return h ? h + ":" + two(m) + ":" + two(s) : m + ":" + two(s);
  }

  function build(section) {
    var list = DATA[section.getAttribute("data-moments")];
    var grid = section.querySelector(".mm-grid");
    var stage = section.querySelector(".mm-stage");
    if (!list || !grid || !stage) return;
    grid.setAttribute("data-n", String(list.length));
    var open = null;

    function close() {
      stage.innerHTML = "";
      stage.hidden = true;
      if (open) open.classList.remove("on");
      open = null;
    }

    list.forEach(function (m) {
      var card = document.createElement("button");
      card.type = "button";
      card.className = "mm-card";
      card.setAttribute("aria-label", m.q + " Play " + m.who + " at " + stamp(m.at) + ", " + m.where);
      card.innerHTML =
        '<span class="mm-thumb"><img alt="" loading="lazy" decoding="async">' +
        '<span class="mm-play"></span><span class="mm-at"></span></span>' +
        '<span class="mm-body"><span class="mm-q"></span><span class="mm-said"></span>' +
        '<span class="mm-who"></span><span class="mm-where"><span></span><b></b></span><span class="mm-when"></span></span>';
      var img = card.querySelector("img");
      // A missing frame takes its box with it rather than leaving a black one.
      img.onerror = function () { var t = card.querySelector(".mm-thumb"); if (t) t.remove(); };
      img.src = "/demo/moments/" + m.vid + "-" + m.at + ".webp";
      card.querySelector(".mm-at").textContent = stamp(m.at);
      card.querySelector(".mm-q").textContent = m.q;
      card.querySelector(".mm-said").textContent = "“" + m.said + "”";
      card.querySelector(".mm-who").textContent = m.who;
      var where = card.querySelectorAll(".mm-where span");
      where[0].textContent = m.where;
      card.querySelector(".mm-where b").textContent = stamp(m.at);
      card.querySelector(".mm-when").textContent = m.when;

      card.addEventListener("click", function () {
        if (open === card) { close(); return; }
        close();
        open = card;
        card.classList.add("on");
        var frame = document.createElement("iframe");
        frame.src = "https://www.youtube-nocookie.com/embed/" + m.vid + "?start=" + m.at +
                    "&autoplay=1&rel=0&modestbranding=1";
        frame.allow = "accelerometer; autoplay; encrypted-media; picture-in-picture; fullscreen";
        frame.setAttribute("allowfullscreen", "");
        frame.title = m.who + ", " + m.where + ", from " + stamp(m.at);
        var cap = document.createElement("div");
        cap.className = "mm-cap";
        cap.innerHTML = "<span></span><button type=button>Stop</button>";
        cap.firstChild.textContent = m.who + ", " + m.where + ", at " + stamp(m.at);
        cap.lastChild.addEventListener("click", close);
        stage.appendChild(frame);
        stage.appendChild(cap);
        stage.hidden = false;
        var box = stage.getBoundingClientRect();
        var over = box.bottom - window.innerHeight + 24;
        if (over > 0) window.scrollBy({top: over, behavior: "smooth"});
      });
      grid.appendChild(card);
    });
  }

  function start() {
    var sections = document.querySelectorAll("[data-moments]");
    if (!sections.length) return;
    if (!document.getElementById("mm-style")) {
      var style = document.createElement("style");
      style.id = "mm-style";
      style.textContent = CSS;
      document.head.appendChild(style);
    }
    Array.prototype.forEach.call(sections, build);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
