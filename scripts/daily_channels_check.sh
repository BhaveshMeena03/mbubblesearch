#!/bin/bash
# Index new MCG Live and ThreadGuy episodes every morning, on this Mac.
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
# Friday watcher (daily_broadcast_check.sh); this is the other two.
#
# 09:00 IST, from the shows: ThreadGuy's market open runs about 19:00 to
# 22:00 IST and its upload follows, and MCG's uploads land in the US day.
# A morning run picks up everything from the day before. launchd runs a
# missed job when the Mac next wakes, so a closed lid delays it, it does
# not skip it.
#
# What it does: transcribes with Groq, embeds, adds each finished episode
# to its shelf (written only after the vectors are in, see ingest_mcg.py),
# then commits and pushes the two shelf files and nothing else, so the
# site lists what can be searched. It never posts anywhere. The headline
# totals on the front door and the link cards are not touched; those are
# redrawn by hand.

set -uo pipefail

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
    echo "  installed: MCG Live and ThreadGuy, daily at 09:00"
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
for archive in threadguy mcg; do
  echo "-- $archive"
  # --max-new bounds a morning after a week away: newest first, the rest
  # tomorrow. caffeinate keeps the Mac awake through the transcription.
  caffeinate -i .venv/bin/python -u scripts/ingest_mcg.py \
    --archive "$archive" --transcriber groq --chunk-workers 4 --workers 2 \
    --limit 20 --max-new 8 || status=1
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

# Only the shelves and the indexes, whatever else is uncommitted here.
shelves=(data/threadguy_index.json data/mcg_index.json "${terms[@]}")
if git diff --quiet -- "${shelves[@]}"; then
  echo "  no new episodes"
  exit $status
fi
git commit -q -m "Add the MCG Live and ThreadGuy episodes indexed this morning" \
  -- "${shelves[@]}" && git push -q origin main \
  && echo "  pushed: $(git log --oneline -1)" \
  || { echo "  could not commit or push the shelves; they are indexed and will go up with the next push"; status=1; }
exit $status
