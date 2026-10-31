# MarketBubbleSearch for X

A button on the X posts that are recordings in [search.lexthedev.com](https://search.lexthedev.com):
Market Bubble's broadcasts, Ansem's own videos, and the MCG and rasmr streams that went out on X.

Click it and you get:

- the episode's summary, where it has one
- a box to ask anything about that recording, answered from it alone
- every time in either one plays at that second, in X's own player on the post

X can't open a posted video at a time from a link. The extension sets the player's clock directly,
so 1:39:33 lands on 1:39:33.

## Install

Until it is on the Chrome Web Store:

1. Download this folder (or the zip from the releases).
2. Open `chrome://extensions` and turn on **Developer mode**.
3. Click **Load unpacked** and pick the folder with `manifest.json` in it.
4. Open any Market Bubble broadcast on x.com. The button sits in the row with reply and like.

## Privacy

What it sends, and what it doesn't:

- **On a page with a video post**, it downloads the list of post ids the archive holds
  (`/v1/x/shelf`), at most once an hour. Which posts get a button is decided in your browser against
  that list. Nothing about what you scroll past is sent anywhere, and on a page with no video post
  it sends nothing at all.
- **When you click the button**, it sends that one post's id, to get its summary.
- **When you ask a question**, it sends the post id and the question. Questions are answered by a
  model and logged the way questions on the site are, without your name or your X account.
- It reads nothing else on the page: not your account, your timeline, your messages or your cookies.
  It sends no cookies, no referrer and no identifiers with any request, and stores nothing.
- Like every request to the site, the server sees your IP address, and uses it only to keep any one
  address from using up everyone's daily limit.
- A script on the page can't make it ask anything: only a real click or a real Enter does.

## Permissions

None. It runs on `https://x.com/*`, and the only address it talks to is
`https://search.lexthedev.com`, which answers any origin, so it needs no host permission for that
either. There is no remote code: everything that runs is in this folder.

## Files

| file | what it does |
| --- | --- |
| `manifest.json` | what the extension is allowed to do |
| `lib.js` | plain logic: reading post ids, checking every reply, splitting text into words and times |
| `background.js` | the only part that talks to the network, to three fixed paths on one origin |
| `content.js` | the button and the panel on x.com, built from text nodes only |

## Tests

```bash
node --test extension/test/lib.test.js extension/test/background.test.js
node --test extension/test/browser.test.mjs
MBS_E2E=1 node --test extension/test/extension.e2e.mjs
```

- The first runs the logic and the background worker as shipped, with every reply the server could
  give.
- The second runs the content script on an X-shaped page in a real Chromium, with hostile replies on
  purpose. It needs `playwright-core`, a Chromium it has downloaded, and `ffmpeg`, and skips itself
  without them.
- The third loads the packaged extension into Chromium against the live site. It spends one model
  call.

The server side is in `tests/test_x_extension.py`, which also runs the first two.
