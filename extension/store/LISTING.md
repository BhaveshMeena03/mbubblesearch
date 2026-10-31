# Chrome Web Store listing

Everything the developer dashboard asks for, ready to paste. Images are in this folder.
Checked against the store's own pages on 1 Nov 2026: image sizes
(developer.chrome.com/docs/webstore/images), the privacy tab
(developer.chrome.com/docs/webstore/cws-dashboard-privacy) and the user data FAQ.

## Package

Upload `marketbubblesearch-for-x-1.0.0.zip`: `manifest.json`, `lib.js`, `background.js`,
`content.js`, `icons/`, `README.md`. Not `test/` or `store/`.

## Store listing tab

**Name** (from the manifest): MarketBubbleSearch for X

**Summary** (from the manifest, 127 of 132 characters): Summaries, answers and where clips came from, for Market Bubble, MCG, ThreadGuy and rasmr on X. Every time plays at its second.

**Description** (plain text, paste as is):

```
Watching a three hour show on X and want the one part that matters? MarketBubbleSearch for X puts a summary button on the post itself.

Click it under a Market Bubble episode, one of Ansem's videos, or an MCG Live, ThreadGuy or rasmr stream that aired on X, and you get:
• the episode's summary where it has one, every topic with its time
• a box to ask anything about that recording, answered from what was actually said in it
• times you can click: each one moves X's own player to that second

On a clip, it tells you where the clip came from ("Episode 24, 1:39:53 to 1:41:12"), shows what was said just before and right after the cut, and links to the full show at that moment.

The answers come from search.lexthedev.com, which has the full transcript of every one of these recordings. A post the archive doesn't hold gets no button, so nothing is guessed.

Privacy
• No permissions. It runs on x.com and talks to one address, search.lexthedev.com.
• It sends a post's id when you click its button, and your question when you ask one. Nothing about your account, your timeline or what you scroll past leaves your browser.
• It stores nothing and sells nothing.
Full policy: https://search.lexthedev.com/extension/privacy
Source code: https://github.com/BhaveshMeena03/mbubblesearch/tree/main/extension

An independent project by @Lexx_eth. Not affiliated with X Corp. or with the shows it covers.
```

**Category**: Lifestyle, Social Networking

**Language**: English

**Graphic assets**
| field | file |
| --- | --- |
| Store icon, 128x128 | `../icons/128.png` (96px mark, 16px padding, as the store asks) |
| Screenshots, 1280x800 | `screenshot-1-summary.png`, `screenshot-2-clip.png`, `screenshot-3-ask.png` |
| Small promo tile, 440x280 | `promo-small-440x280.png` |
| Marquee promo tile, 1400x560 | `promo-marquee-1400x560.png` |

**Homepage URL**: https://search.lexthedev.com/home

**Support URL**: https://github.com/BhaveshMeena03/mbubblesearch/issues

**Mature content**: No

## Privacy practices tab

**Single purpose**:

```
Adds a button to X posts that are recordings in the MarketBubbleSearch archive, or clips of them. The button shows the recording's summary, where a clip came from, and answers to questions about that recording, with every time playing at its second in X's own player.
```

**Permission justification**: the manifest declares no permissions. If the dashboard asks about
the host access the content script implies:

```
The button and panel are added to posts on x.com, so the content script has to run there and nowhere else. On the page it reads only each post's own id and whether the post has a video, to decide which posts get a button. That decision is made in the browser; nothing about the page is sent anywhere until the user clicks.
```

**Remote code**: No, I am not using remote code. (Every script is in the package. The server
sends JSON only, which is checked field by field and shown as text, never run.)

**Data usage**, what it collects:
- [x] **Website content**: the id of the X post whose button is clicked.
- [x] **Web history**: that id also says which post the user was looking at.
- Leave every other box unticked. The question a user types is sent to answer it; it is
  covered in the privacy policy. Tick **User activity** too only if the reviewer asks for it.

**Certifications**: tick all three (not sold or transferred outside the approved use, not used
for anything unrelated to the single purpose, not used for creditworthiness or lending).

**Privacy policy URL**: https://search.lexthedev.com/extension/privacy

## Distribution tab

Free. Visibility: Public. Regions: all.

## Test instructions (for the reviewer)

```
The extension needs no account or login of its own. X itself may ask you to sign in to view posts.

1. Open https://x.com/MarketBubble/status/2115904746355851479 and let the post load.
2. A "summary" button appears at the end of the row with reply, repost and like. Click it: the episode's summary opens under the post.
3. Start the video, then click any time in the summary (for example 0:04:31). The video moves to that second.
4. Type a question in the box, for example "what did they say about Binance?", and press Enter. The answer's times are clickable too.
5. A clip: open https://x.com/blknoiz06/status/2116428709150478840 and click its button. It shows which episode the clip is from, the minute, and what was said right after it.

Posts that are not in the archive get no button.
```

## Before the first submission (Lex, on the account)

1. https://chrome.google.com/webstore/devconsole, signed in with the Google account that should
   own it. One-time $5 registration fee.
2. Turn on 2-Step Verification on that Google account; the store requires it to publish.
3. Account tab: publisher name, contact email (verify it), and the trader / non-trader
   declaration (non-trader: it's free and not a business).
4. New item, upload the zip, fill the tabs from this file, Submit for review. A first item from
   a new account usually takes a few days.
5. Once it's approved, put the store link in `extension/README.md` under Install.
