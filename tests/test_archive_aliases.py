"""One mangled string, two projects, two archives.

Whisper writes Jito as "GTO" on the broadcast, where Lucas from Jito Labs
was a guest. It also writes Jatevo as "GTO" on MCG, where Luca runs a
decentralised compute project that is mentioned fourteen times. Aliasing
the string globally would merge one founder's project into another's,
which is a worse outcome than leaving the mangling alone: a search would
then assert something nobody said.
"""
import json
from pathlib import Path

from app.assets import ARCHIVE_ALIASES, aggregate, canonical

ROOT = Path(__file__).resolve().parent.parent


class TestTheSameManglingMeansDifferentThings:
    def test_gto_is_jito_on_the_broadcast(self):
        assert canonical("GTO", "GTO Labs token", "podcast") == "JITO"

    def test_gto_is_jatevo_on_mcg(self):
        assert canonical("GTO", "GTO (GTVO)", "mcg") == "JTVO"

    def test_gto_is_left_alone_without_an_archive(self):
        """Refusing to guess is the safe direction."""
        assert canonical("GTO", "GTO") == "GTO"

    def test_no_archive_alias_is_also_a_global_one(self):
        """A term that needs archive context must not be in both tables.

        If it were, the global table would answer first for callers that
        pass no archive and quietly pick one project over the other.
        """
        from app.assets import ALIASES
        for archive, table in ARCHIVE_ALIASES.items():
            clash = set(table) & set(ALIASES)
            assert not clash, f"{archive} aliases also global: {clash}"


class TestTheGlobalTableStaysGlobal:
    def test_dregg_needs_no_archive(self):
        """Confirmed twice: the MCG guest index caught the host saying
        "let's pull up Ember and talk a little bit about drag", and MCG's
        own post about that segment names @ember_arlynx and $DREGG."""
        assert canonical("DRAG", "Drag") == "DREGG"
        assert canonical("DRAG", "Drag", "mcg") == "DREGG"

    def test_the_existing_manglings_still_resolve(self):
        assert canonical("SOUL", "soul") == "SOL"
        assert canonical("BUNK", "bunk") == "BONK"
        assert canonical("ANOM", "anom") == "ANSEM"


class TestAggregatePassesItThrough:
    def test_a_broadcast_hit_resolves_to_jito(self):
        hit = {"symbol": "GTO", "name": "GTO Labs token", "confidence": "high",
               "kind": "mention", "start_seconds": 1.0, "note": "n",
               "episode_id": "e", "episode_title": "t", "url": "u"}
        out = aggregate([hit], archive="podcast")
        assert [a["symbol"] for a in out["assets"]] == ["JITO"]

    def test_the_same_hit_resolves_to_jatevo_on_mcg(self):
        hit = {"symbol": "GTO", "name": "GTO (GTVO)", "confidence": "high",
               "kind": "mention", "start_seconds": 1.0, "note": "n",
               "episode_id": "e", "episode_title": "t", "url": "u"}
        out = aggregate([hit], archive="mcg")
        assert [a["symbol"] for a in out["assets"]] == ["JTVO"]


class TestTheShippedReportsAgree:
    def test_no_report_still_carries_a_corrected_mangling(self):
        """Regenerating is what applies an alias, so this catches a table
        that was updated and a report that was never rebuilt."""
        for name, archive in (("assets.json", "podcast"),
                              ("mcg_assets.json", "mcg")):
            path = ROOT / "data" / name
            if not path.exists():
                continue
            symbols = {a["symbol"].upper()
                       for a in json.loads(path.read_text()).get("assets", [])}
            stale = symbols & {k.upper() for k in ARCHIVE_ALIASES[archive]}
            assert not stale, (
                f"{name} still carries {stale}; re-run its extract to apply "
                f"the alias table")
