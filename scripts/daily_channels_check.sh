#!/bin/bash
# Index new MCG Live, ThreadGuy and rasmr episodes every morning, on this Mac.
#
# Install (a LaunchAgent that runs daily at 09:00 local time):
#
#   scripts/daily_channels_check.sh --install
#
# Remove:
#
#   scripts/daily_channels_check.sh --uninstall
#
# Run it by hand any time:
#
#   scripts/daily_channels_check.sh --run
#
# Why here and not on GitHub. The sync workflow can list a channel from a
# runner but YouTube refuses the download from a datacentre IP, so for MCG
# it has only ever been able to say "there are new episodes" in a warning
# nobody acted on. This laptop can fetch them. Market Bubble has its own
# Friday watcher (daily_broadcast_check.sh); this is the others.
#
# 09:00 IST, from the shows: ThreadGuy's market open runs about 19:00 to
# 22:00 IST and its upload follows, and MCG's uploads land in the US day.
# rasmr streams in the US afternoon and evening, so his are up by then too.
# A morning run picks up everything from the day before. launchd runs a
# missed job when the Mac next wakes, so a closed lid delays it, it does
# not skip it.
#
# What it does: transcribes with Groq, embeds, adds each finished episode
# to its shelf (written only after the vectors are in, see ingest_mcg.py),
# writes notes for ThreadGuy's newest, then commits and pushes the shelf
# files and the notes and nothing else, so the site lists what can be
# searched. rasmr gets his shelf and nothing more: no notes or tokens yet,
# and his X-only broadcasts are read off X by hand, because that costs the
# bot's X credit (scripts/index_rasmr_x_broadcasts.py). It never posts anywhere. The headline
# totals on the front door and the link cards are not touched; those are
# redrawn by hand.

set -uo pipefail
# launchd starts this with a bare PATH (/usr/bin:/bin:/usr/sbin:/sbin), so
# Homebrew's ffmpeg and ffprobe were invisible to yt-dlp. On 2026-10-02 the
# morning run found 3 ThreadGuy and 6 MCG episodes and downloaded none of
# them: "ffprobe and ffmpeg not found". It had never worked from launchd.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.mbubblesearch.channelsync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$ROOT/.channels-check.log"

case "${1:-}" in
  --install)
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/scripts/daily_channels_check.sh</string>
    <string>--run</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>9</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLISTEOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "  installed: MCG Live, ThreadGuy and rasmr, daily at 09:00"
    echo "  log: $LOG"
    exit 0
    ;;
  --uninstall)
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "  removed"
    exit 0
    ;;
  --run)
    ;;
  *)
    echo "usage: $0 --install | --uninstall | --run" >&2
    exit 2
    ;;
esac

cd "$ROOT"
echo "== $(date "+%Y-%m-%d %H:%M %Z") =="

# Wait for the network. launchd starts this the moment the Mac wakes for
# 09:00, and Wi-Fi is not back yet: on 7 and 8 October every step failed
# with "nodename nor servname provided", nothing was indexed, and the
# archive sat two days behind with nobody told. Up to ten minutes, then
# give up loudly rather than run a whole morning against no network.
online=0
for _ in $(seq 1 30); do
  if curl -s -m 8 -o /dev/null https://www.youtube.com; then online=1; break; fi
  sleep 20
done
if [[ $online -eq 0 ]]; then
  echo "  no network after ten minutes; nothing indexed today"
  exit 1
fi

# One writer per shelf. A long backfill started by hand and this job would
# both append to the same file and both transcribe the same episode.
if pgrep -f "scripts/ingest_mcg.py" >/dev/null; then
  echo "  an ingest is already running; skipping today"
  exit 0
fi

# The shell this runs from exports ANTHROPIC_BASE_URL straight at
# Anthropic, which would override .env and bill it directly. Every model
# call in this project goes through the proxy configured in .env.
unset ANTHROPIC_BASE_URL

status=0
for archive in threadguy mcg rasmr; do
  echo "-- $archive"
  # --max-new bounds a morning after a week away: newest first, the rest
  # tomorrow. caffeinate keeps the Mac awake through the transcription.
  caffeinate -i .venv/bin/python -u scripts/ingest_mcg.py \
    --archive "$archive" --transcriber groq --chunk-workers 4 --workers 2 \
    --limit 20 --max-new 8 || status=1
done

# The X broadcast of each MCG Live show, so a citation can open on X at
# the same second. MCG streams to both at once; this finds which broadcast
# is which show and measures how far apart the two clocks are. One search
# on X, about half a cent a morning, and three minutes of audio per show.
echo "-- mcg on x"
.venv/bin/python -u scripts/pair_mcg_broadcasts.py || status=1

# Notes for ThreadGuy's newest uploads. Already-written ones are skipped,
# so a morning pays for what arrived overnight and nothing else: one model
# call per episode, through the proxy in .env.
echo "-- threadguy notes"
.venv/bin/python -u scripts/summarize_threadguy.py --latest 10 || status=1

# ThreadGuy's X broadcasts, for the browser extension's button on his
# posts: one search on X for broadcast posts from @notthreadguy since the
# newest one on file (about one a day, half a cent), then each new one is
# heard against the show indexed above. After the notes, so a show paired
# this morning already has its summary.
echo "-- threadguy on x"
.venv/bin/python -u scripts/pair_threadguy_broadcasts.py --search || status=1

# rasmr's broadcasts on X, the same way: his new broadcast posts (one a
# stream), then the last week's unpaired ones heard against his indexed
# streams, so a stream whose YouTube copy came late is paired tomorrow.
echo "-- rasmr on x"
.venv/bin/python -u scripts/pair_rasmr_broadcasts.py --search || status=1

# What Market Bubble and Ansem post on X that the archive doesn't hold:
# his own videos, and the broadcasts and uploads that aren't the Thursday
# show the Friday watcher indexes. One X search (half a cent a post, a few a
# day); a cut of a show already held is left to the clips below, and each
# new recording goes through add_broadcast.py on Groq, minutes apiece.
# Before the clips, so a clip of one indexed now finds it this morning.
echo "-- recordings on x"
caffeinate -i .venv/bin/python -u scripts/add_x_recordings.py --search || status=1

# Clips of the shows posted on X by Ansem, Market Bubble, MCG Live,
# ThreadGuy, us and the clippers in x_posts.CLIP_ACCOUNTS,
# placed in the archive so the extension can say where each is from and
# what was said after it. One X search for their new video posts (about
# half a cent each, around 35 a day), then X's own captions, which are
# free. After the MCG and ThreadGuy ingests above, so a clip of this
# morning's show finds it; each new show's lines are read back from
# Pinecone once, about half a megabyte.
echo "-- clips on x"
.venv/bin/python -u scripts/place_x_clips.py --search || status=1

# Our own posts that link a show at a second, on X or YouTube, so the
# extension can name the moment on them. Our timeline since yesterday's
# newest post, replies included (about twenty a day, a tenth of a cent
# each); the second is read off each link.
echo "-- our link posts"
.venv/bin/python -u scripts/place_x_links.py --search || status=1

# Notes for MCG's newest, the daily live show included: the extension
# shows them on MCG Live's posts on X. Already-written ones are skipped.
echo "-- mcg notes"
.venv/bin/python -u scripts/summarize_mcg.py --latest 5 || status=1

# Market Bubble's summaries, which the site serves from this file rather
# than reading them back from Pinecone. A show added since yesterday has its
# summary in the index first; this fetches just that one.
echo "-- market bubble summaries"
.venv/bin/python -u scripts/pack_summaries.py || status=1

# Tokens from the newest uploads of both channels, into each dashboard's
# store. Episodes already extracted are cached and skipped; two at a time,
# because the proxy is shared with the live site and the bot, and a run
# that floods it gets rate limited and slows them down.
for archive in threadguy mcg; do
  echo "-- $archive tokens"
  EXTRACT_CONCURRENCY=4 .venv/bin/python -u scripts/extract_mcg_assets.py \
    --archive "$archive" --episodes 10 --store --workers 2 || status=1
done

# Sundays: the exact-word indexes, read back off Pinecone. Weekly, not
# daily, because each rebuild commits a few megabytes and a daily one would
# add a gigabyte a year to the repo. Until then a new episode is still
# found by meaning, just not yet by an exact rare word.
terms=(data/terms_threadguy.json.gz data/terms_mcg.json.gz)
if [ "$(date +%u)" = "7" ]; then
  for archive in threadguy mcg; do
    echo "-- exact-word index: $archive"
    .venv/bin/python -u scripts/build_term_index.py --archive "$archive" || status=1
  done
fi

# Only the shelves, the indexes and the two token reports, whatever else is
# uncommitted here. The reports are what the dashboards serve: the site
# used to rebuild them from Pinecone at every boot and every half hour,
# which is most of how a month's read cap went in nineteen days.
shelves=(data/threadguy_index.json data/mcg_index.json data/rasmr_index.json
         data/threadguy_summaries.json.gz "${terms[@]}"
         data/mcg_assets.json data/threadguy_assets.json)
# Only once it is in the repo: git will not commit a path it has never
# seen, and the refusal would take the shelves down with it.
# Market Bubble's own files, for what add_x_recordings.py indexed. Not
# data/speaker_map.json: rebuilding it moves other episodes' labels, and
# a person merges the new entry by hand.
for kept in data/mcg_broadcast_links.json data/threadguy_broadcast_links.json \
            data/threadguy_x_broadcasts.json data/mcg_summaries.json.gz \
            data/x_clips.json data/x_clips_search.json \
            data/x_links.json data/x_links_search.json \
            data/x_recordings.json data/x_copies.json \
            data/episodes.json.gz data/broadcast_links.json \
            data/rasmr_broadcast_links.json data/rasmr_x_broadcasts.json \
            data/term_index.json data/highlights.json; do
  if git ls-files --error-unmatch "$kept" >/dev/null 2>&1; then
    shelves+=("$kept")
  fi
done
if git ls-files --error-unmatch data/summaries.json.gz >/dev/null 2>&1; then
  shelves+=(data/summaries.json.gz)
fi
if git diff --quiet -- "${shelves[@]}"; then
  echo "  no new episodes"
  exit $status
fi
git commit -q -m "Add the episodes indexed this morning" \
  -- "${shelves[@]}" && git push -q origin main \
  && echo "  pushed: $(git log --oneline -1)" \
  || { echo "  could not commit or push the shelves; they are indexed and will go up with the next push"; status=1; }
exit $status
