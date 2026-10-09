"""Whose voice each line of the Musk archive is in.

    .venv/bin/python scripts/label_elon_voices.py --fingerprint
    .venv/bin/python scripts/label_elon_voices.py --report
    .venv/bin/python scripts/label_elon_voices.py
    .venv/bin/python scripts/label_elon_voices.py --apply [--dry-run]

The archive's claim is "Elon said this", and until now nothing in it knew
which lines were his. A transcript is words; Rogan's question and Musk's
answer are the same kind of line. On a two-person interview the model
could usually tell from the sense of it. On the newer recordings it could
not be asked to: All-In is four hosts, one episode has Gwynne Shotwell
beside him, and eight hours of the Neuralink episode are other people.

Who is speaking is a fact about the sound, and the Market Bubble pipeline
already reads it: a voice fingerprint for every line long enough to have
one (label_speakers.py), compared within the recording. What is different
here is how a voice gets a name, and it is easier. One person is in all of
these recordings and nobody else is in more than six. So:

  Elon Musk     the one voice found in every recording.
  Joe Rogan     the voice that recurs across the six JRE recordings.
  Lex Fridman   the voice that recurs across the Lex Fridman recordings.
  Other speaker a voice that is clearly not his, and that nothing here
                can name. An interviewer who appears once is in a title,
                and a title is how "Austin Federa said" ended up over
                somebody else's words on the other archive. A person can
                name them later; this does not guess.

A line that is too short to fingerprint, or that sits between two voices,
gets no label at all. Unlabelled is the honest state and the answer prompt
is told what it means.

A line is heard twice, at its start and at its end, and named only when
both are the same voice. The transcriber does not break a line where the
speaker changes, and the first version of this listened to the first 2.6
seconds alone: on the March 2026 Diamandis recording a whole cluster of
Musk's answers ("I'd say the economy is 10 times its current size in 10
years") came out as somebody else's, because each began on the last word
of the question. The other direction is the one that matters more, a
question that begins on the tail of his answer, and it is the same fault.

Short lines are the third hearing. The fourteen older recordings were
transcribed on the laptop and their lines run about a second: 68% of the
2023 Lex Fridman conversation is lines too short to fingerprint alone, and
naming only the long ones named a quarter of it. A short line is heard
with the second on either side of it, which is his voice when it is a
fragment of him talking and a blur when it sits at a change of speaker,
and it has to be clearer than a long line does before it gets a name. An
interviewer's "right" in the middle of his sentence will still come out
as his. That is the price of the resolution, and it is a word nobody
quotes; a sentence of somebody else's is long enough to be heard as
theirs.

--fingerprint reads the audio (kept in /tmp/elon_audio by ingest_elon.py,
about twenty lines a second). The default builds data/elon_speaker_map.json
from the fingerprints and prints what it found. --apply writes the names
into the passages the model reads, in the `elon` namespace, the way
apply_speaker_labels.py does for the broadcast: `text_ts` and `speakers`
only, never the embedded text, so retrieval is the same after as before.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import warnings
from collections import Counter
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402

EPISODES = ROOT / "data" / "elon_episodes.json"
AUDIO = Path("/tmp/elon_audio")
FINGERPRINTS = ROOT / "data" / "elon_speakers"
OUT = ROOT / "data" / "elon_speaker_map.json"
NAMESPACE = "elon"

ELON = "Elon Musk"
OTHER = "Other speaker"
# A voice that recurs across every recording a channel made with him is
# that channel's host. Recurrence is the evidence; the channel only says
# what to call it.
HOSTS = {"PowerfulJRE": "Joe Rogan", "Lex Fridman": "Lex Fridman"}

# Voices inside one recording. Looser than the 0.55 the broadcast uses:
# that show has a dozen voices to keep apart, and here the first question
# is only which of two or three is the one that is everywhere.
WITHIN = 0.8
# A cluster smaller than this is crosstalk and backchannel, and too little
# to call a voice.
BIG = 12
# Set from what the first thirteen recordings measured, not from taste.
# His own voice in each recording sat at 0.85 to 0.95 against his voice
# everywhere else; the nearest other person at 0.39 (a second interviewer)
# and everyone else under 0.25. Between them were small clusters at 0.48
# to 0.66 that read, by their words, as him laughing or trailing off, and
# one or two that could be either. So a cluster is his above HIS, somebody
# else's below NOT_HIS, and in between it is neither: its lines are heard
# one at a time against the voices that are certain.
HIS = 0.75
NOT_HIS = 0.44
# A second cluster is added to his outright only when it is as certain as
# the first usually is. The 2018 Rogan episode has one of 52 lines at 0.75
# beside the main one at 0.81; it reads as him, and so does every cluster
# found above 0.6, but "reads as" is the judgement this exists to replace.
# Below this its lines are heard one at a time like any other.
ALSO_HIS = 0.85
SAME_VOICE = 0.55       # a host's cluster against that host elsewhere
CLEAR_BY = 0.12         # how much closer a line must be to one side
AT_LEAST = 0.30         # and how close it must be at all
# A short line is heard with its surroundings, so it is asked for more.
CLEAR_BY_SHORT = 0.25
SHORT_WINDOW = 2.0      # seconds, centred on the line


def unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


def load_recordings() -> list[dict]:
    """Each recording with its fingerprints, where they exist."""
    out = []
    for episode in json.loads(EPISODES.read_text()):
        cache = FINGERPRINTS / f"{episode['episode_id']}.npz"
        if not cache.exists():
            continue
        held = np.load(cache)
        if "tail" not in held or "short" not in held:
            continue                    # half heard: --fingerprint finishes it
        out.append({"id": episode["episode_id"], "title": episode["title"],
                    "channel": episode.get("channel", ""),
                    "segments": episode["segments"],
                    "vectors": unit(held["vectors"]),
                    # NaN where the line was short enough that the first
                    # hearing already covered all of it.
                    "tail": unit(held["tail"]),
                    "kept": held["kept"].tolist(),
                    "short": unit(held["short"]),
                    "short_kept": held["short_kept"].tolist()})
    return out


def voices(vectors: np.ndarray) -> dict[int, np.ndarray]:
    """The recording's voices: cluster number -> rows of `vectors`."""
    from label_speakers import cluster

    labels = cluster(vectors, WITHIN)
    return {int(v): np.flatnonzero(labels == v) for v in set(labels)}


def centre(vectors: np.ndarray, rows: np.ndarray) -> np.ndarray:
    return unit(vectors[rows].mean(axis=0))


def everywhere(recordings: list[dict]) -> np.ndarray:
    """The fingerprint of the one voice that is in every recording.

    Every sizeable voice in every recording is a candidate. Each is scored
    by how well its best match in each OTHER recording fits, and the worst
    of those is what counts: a host matches himself across his own
    episodes and nobody in the rest, so his worst is poor. Only the voice
    that is actually in all of them has a good worst case.
    """
    big = []
    for rec in recordings:
        rec["voices"] = voices(rec["vectors"])
        rec["big"] = {v: centre(rec["vectors"], rows)
                      for v, rows in rec["voices"].items() if len(rows) >= BIG}
        big.append(np.stack(list(rec["big"].values())))
    best, best_score = None, -1.0
    for i, mine in enumerate(big):
        for candidate in mine:
            worst = min(float((theirs @ candidate).max())
                        for j, theirs in enumerate(big) if j != i)
            if worst > best_score:
                best, best_score = candidate, worst
    # Then settle it: the average of whoever matched him in each recording.
    for _ in range(3):
        best = unit(np.mean([theirs[(theirs @ best).argmax()] for theirs in big],
                            axis=0))
    return best


def host_voice(recordings: list[dict], channel: str,
               elon: np.ndarray) -> np.ndarray | None:
    """The voice that recurs across a channel's recordings and is not his.

    None unless it really recurs: the largest other voice in each of the
    channel's recordings has to be the same voice as in the rest.
    """
    picks = []
    for rec in recordings:
        if rec["channel"] != channel:
            continue
        others = {v: c for v, c in rec["big"].items()
                  if float(c @ elon) <= NOT_HIS}
        if not others:
            continue
        largest = max(others, key=lambda v: len(rec["voices"][v]))
        picks.append(others[largest])
    if len(picks) < 3:
        return None
    middle = unit(np.mean(picks, axis=0))
    if min(float(p @ middle) for p in picks) < SAME_VOICE:
        return None
    return middle


def decide(to_elon: float, to_other: float,
           clear_by: float = CLEAR_BY) -> str | None:
    """One line: his, clearly somebody else's, or not clear enough to say.

    Both distances are to voices in the same recording, the same room and
    microphone, which is the comparison a short clip can actually win. A
    line has to be near one side and plainly nearer it than the other.
    """
    if to_elon >= AT_LEAST and to_elon - to_other >= clear_by:
        return ELON
    if to_other >= AT_LEAST and to_other - to_elon >= clear_by:
        return OTHER
    return None


def label(rec: dict, elon: np.ndarray, hosts: dict[str, np.ndarray]) -> dict:
    """segment index -> name, for the lines of one recording that are clear."""
    fits = sorted(((float(c @ elon), v) for v, c in rec["big"].items()),
                  reverse=True)
    his = [v for n, (fit, v) in enumerate(fits)
           if fit >= (HIS if n == 0 else ALSO_HIS)]
    if not his:
        # Take the nearest voice or label nothing? Nothing. A recording
        # where his voice cannot be found is one where every label would
        # be a guess, and it is reported so somebody looks.
        return {}
    rows = np.concatenate([rec["voices"][v] for v in his])
    here = centre(rec["vectors"], rows)
    others = {v: c for v, c in rec["big"].items()
              if float(c @ elon) <= NOT_HIS}
    names = {}
    for v, c in others.items():
        names[v] = OTHER
        for name, voice in hosts.items():
            if float(c @ voice) >= SAME_VOICE and float(c @ voice) > float(c @ elon):
                names[v] = name
    stack = np.stack(list(others.values())) if others else None
    order = list(others)

    def hear(vector: np.ndarray, clear_by: float = CLEAR_BY) -> str | None:
        to_elon = float(vector @ here)
        if stack is None:
            # Nobody else in the room is sizeable enough to compare with.
            return ELON if to_elon >= AT_LEAST + clear_by else None
        scores = stack @ vector
        nearest = int(scores.argmax())
        verdict = decide(to_elon, float(scores[nearest]), clear_by)
        return names[order[nearest]] if verdict == OTHER else verdict

    out = {}
    for row, index in enumerate(rec["kept"]):
        start = hear(rec["vectors"][row])
        if start is None:
            continue
        tail = rec["tail"][row]
        # Two hearings of a long line, and they have to say the same name.
        if not np.isnan(tail[0]) and hear(tail) != start:
            continue
        out[str(index)] = start
    for row, index in enumerate(rec["short_kept"]):
        who = hear(rec["short"][row], CLEAR_BY_SHORT)
        if who is not None:
            out[str(index)] = who
    return out


def fingerprint() -> int:
    import torch
    from label_speakers import fingerprints
    from speechbrain.inference.speaker import EncoderClassifier

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"  loading the voice encoder on {device}")
    encoder = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=str(ROOT / ".models" / "ecapa"), run_opts={"device": device})
    FINGERPRINTS.mkdir(parents=True, exist_ok=True)
    missing = 0
    for episode in json.loads(EPISODES.read_text()):
        vid = episode["episode_id"]
        cache = FINGERPRINTS / f"{vid}.npz"
        held = dict(np.load(cache)) if cache.exists() else {}
        if "tail" in held and "short" in held:
            continue
        audio = AUDIO / f"{vid}.m4a"
        if not audio.exists():
            print(f"  no audio yet: {episode['title'][:56]}")
            missing += 1
            continue
        print(f"\n  {episode['title'][:60]}", flush=True)
        if "vectors" not in held:
            vectors, kept = fingerprints(episode, audio, encoder, device)
            if vectors is None:
                print("     nothing usable")
                continue
            held = {"vectors": vectors, "kept": np.array(kept)}
            np.savez_compressed(cache, **held)
        if "tail" not in held:
            held["tail"] = tails(episode, audio, held["kept"].tolist(),
                                 encoder, device, held["vectors"].shape[1])
            np.savez_compressed(cache, **held)
        held["short"], held["short_kept"] = shorts(
            episode, audio, set(held["kept"].tolist()), encoder, device,
            held["vectors"].shape[1])
        np.savez_compressed(cache, **held)
    return 1 if missing else 0


def shorts(episode: dict, audio: Path, heard: set[int], encoder, device: str,
           width: int) -> tuple[np.ndarray, np.ndarray]:
    """A fingerprint for each line too short to have had one, taken from
    SHORT_WINDOW seconds centred on it."""
    import torch
    from label_speakers import load_audio

    segments = episode["segments"]
    wanted = []
    for i, segment in enumerate(segments):
        if i in heard or not segment.get("text", "").strip():
            continue
        nxt = segments[i + 1]["t"] if i + 1 < len(segments) else segment["t"] + 1
        middle = (segment["t"] + nxt) / 2
        wanted.append((i, max(0.0, middle - SHORT_WINDOW / 2)))
    vectors, kept = [], []
    for start in range(0, len(wanted), 64):
        batch = wanted[start:start + 64]
        waves = [(i, load_audio(audio, at, SHORT_WINDOW)) for i, at in batch]
        good = [(i, w) for i, w in waves if w is not None and len(w) > 16000]
        if not good:
            continue
        span = min(len(w) for _, w in good)
        stack = torch.tensor(np.stack([w[:span] for _, w in good])).to(device)
        with torch.no_grad():
            vectors.append(encoder.encode_batch(stack).squeeze(1).cpu().numpy())
        kept.extend(i for i, _ in good)
        print(f"\r     {min(start + 64, len(wanted))}/{len(wanted)} short lines heard",
              end="", flush=True)
    print()
    if not vectors:
        return np.zeros((0, width), dtype="float32"), np.array([], dtype=int)
    return np.vstack(vectors), np.array(kept)


def tails(episode: dict, audio: Path, kept: list[int], encoder,
          device: str, width: int) -> np.ndarray:
    """The end of each line long enough to have one apart from its start.

    The same 2.6 seconds label_speakers takes from the front, taken from
    the back, stopping a beat short of the next line.
    """
    import torch
    from label_speakers import SAMPLE_SECONDS, load_audio

    segments = episode["segments"]
    out = np.full((len(kept), width), np.nan, dtype="float32")
    wanted = []
    for row, i in enumerate(kept):
        nxt = segments[i + 1]["t"] if i + 1 < len(segments) else segments[i]["t"] + 4
        if nxt - segments[i]["t"] > SAMPLE_SECONDS + 0.4:
            wanted.append((row, nxt - SAMPLE_SECONDS - 0.15))
    for start in range(0, len(wanted), 64):
        batch = wanted[start:start + 64]
        waves = [(row, load_audio(audio, at, SAMPLE_SECONDS)) for row, at in batch]
        good = [(row, w) for row, w in waves if w is not None and len(w) > 8000]
        if not good:
            continue
        span = min(len(w) for _, w in good)
        stack = torch.tensor(np.stack([w[:span] for _, w in good])).to(device)
        with torch.no_grad():
            vectors = encoder.encode_batch(stack).squeeze(1).cpu().numpy()
        for (row, _), vector in zip(good, vectors, strict=True):
            out[row] = vector
        print(f"\r     {min(start + 64, len(wanted))}/{len(wanted)} line ends heard",
              end="", flush=True)
    print()
    return out


def build(report_only: bool) -> int:
    recordings = load_recordings()
    total = len(json.loads(EPISODES.read_text()))
    if len(recordings) < total:
        print(f"  {total - len(recordings)} recording(s) have no fingerprints "
              f"yet; run --fingerprint")
    if len(recordings) < 3:
        return 1
    elon = everywhere(recordings)
    hosts = {}
    for channel, name in HOSTS.items():
        voice = host_voice(recordings, channel, elon)
        if voice is not None:
            hosts[name] = voice
    print(f"  named by recurrence: {ELON}"
          + "".join(f", {n}" for n in hosts) + "\n")

    mapping, unfound = {}, []
    for rec in recordings:
        fits = sorted(((float(c @ elon), len(rec["voices"][v]))
                       for v, c in rec["big"].items()), reverse=True)
        labels = label(rec, elon, hosts)
        if not labels:
            unfound.append(rec["title"])
        mapping[rec["id"]] = labels
        said = Counter(labels.values())
        lines = len(rec["segments"])
        asks = {name: sum(rec["segments"][int(i)]["text"].strip().endswith("?")
                          for i, who in labels.items() if who == name)
                for name in said}
        parts = [f"{name} {100 * n / lines:.0f}% ({100 * asks[name] / n:.0f}% questions)"
                 for name, n in said.most_common()]
        second = f"{fits[1][0]:.2f}" if len(fits) > 1 else "none"
        print(f"  {rec['title'][:46]:46} his voice {fits[0][0]:.2f}, next "
              f"{second} | {' · '.join(parts)} · unlabelled "
              f"{100 * (lines - len(labels)) / lines:.0f}%")
    if unfound:
        print("\n  HIS VOICE WAS NOT FOUND IN: " + "; ".join(unfound))
        return 1
    if not report_only:
        OUT.write_text(json.dumps(mapping, separators=(",", ":")))
        print(f"\n  wrote {OUT.relative_to(ROOT)}: "
              f"{sum(len(m) for m in mapping.values()):,} lines named "
              f"across {len(mapping)} recordings")
    return 0


async def apply(dry_run: bool) -> int:
    from apply_speaker_labels import stamped_with_speakers, vector_id
    from pinecone import Pinecone

    from app.config import get_settings
    from app.podcast import _windows
    from app.schemas import Episode

    settings = get_settings()
    mapping = json.loads(OUT.read_text())
    episodes = {e["episode_id"]: e for e in json.loads(EPISODES.read_text())}
    updates = []
    for episode_id, speakers in mapping.items():
        row = {k: v for k, v in episodes[episode_id].items() if k != "channel"}
        episode = Episode(**row)
        windows = list(_windows(episode.segments, settings.chunk_max_chars,
                                overlap_segments=2))
        for n, (start_t, _text, _stamped) in enumerate(windows):
            end_t = windows[n + 1][0] if n + 1 < len(windows) else float("inf")
            text_ts, present = stamped_with_speakers(episode, speakers,
                                                     start_t, end_t)
            if text_ts and present:
                updates.append((vector_id(episode_id, start_t), text_ts, present))
    print(f"  {len(updates):,} passages carry at least one named line")
    if dry_run:
        for _id, text_ts, present in updates[:2]:
            print("\n" + "\n".join(text_ts.splitlines()[:6]), "\n  speakers:", present)
        print("\n  dry run: nothing written")
        return 0
    index = Pinecone(api_key=settings.pinecone_api_key).Index(settings.pinecone_index)
    for n, (vector, text_ts, present) in enumerate(updates, 1):
        await asyncio.to_thread(
            index.update, id=vector, namespace=NAMESPACE,
            set_metadata={"text_ts": text_ts, "speakers": present})
        if n % 100 == 0:
            print(f"     {n:,} of {len(updates):,}", flush=True)
    print(f"  wrote names into {len(updates):,} passages of {NAMESPACE!r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fingerprint", action="store_true",
                    help="read the audio; slow, cached, and the only step "
                         "that needs the recordings on disk")
    ap.add_argument("--report", action="store_true",
                    help="say who was found where and write nothing")
    ap.add_argument("--apply", action="store_true",
                    help="write the names into the passages the model reads")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.fingerprint:
        return fingerprint()
    if args.apply:
        return asyncio.run(apply(args.dry_run))
    return build(args.report)


if __name__ == "__main__":
    raise SystemExit(main())
