"""The dry run behind the daily "ask the bot" post: it has to answer the
way the live account would, and it must never reach X."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ask_bot  # noqa: E402


def test_it_runs_on_the_settings_production_runs_on():
    found = ask_bot.production_settings()
    assert found["X_BOT_INCLUDE_LINKS"] == "always"
    assert found["X_BOT_SEARCH_MODEL"]
    assert found["X_BOT_SITE"] == "search.lexthedev.com"


def test_no_secret_is_read_from_the_blueprint():
    """Secrets are "sync: false" in render.yaml, with no value to read.
    Checked against the file itself: every variable it marks that way,
    whatever it is called, is left out."""
    import yaml
    spec = yaml.safe_load((ROOT / "render.yaml").read_text())
    secret = {v["key"] for service in spec["services"] for v in service.get("envVars") or []
              if v.get("sync") is False}
    assert "X_BOT_ENABLED" in secret and len(secret) >= 5
    found = ask_bot.production_settings()
    assert found and not set(found) & secret
    assert all("value" in v for service in spec["services"]
               for v in service.get("envVars") or [] if v["key"] in found)


def test_the_stand_in_client_refuses_x():
    client = ask_bot.NoX("123")
    assert client.bot_user_id == "123" and client.own_threads == set()
    with pytest.raises(AttributeError, match="never calls X"):
        _ = client.post_by_id
    with pytest.raises(AttributeError):
        _ = client.reply
    assert getattr(client, "anything", "fallback") == "fallback"


def test_the_post_is_paragraphs_and_the_handle_goes_on_the_question():
    assert ask_bot.post_text(["zec is at $907 right now", "what price did ansem say he'd buy zcash"]) == (
        "zec is at $907 right now\n\n@mbubbleSearch what price did ansem say he'd buy zcash")
    assert ask_bot.post_text(["@mbubbleSearch  who said it   first"]) == "@mbubbleSearch who said it first"
    with pytest.raises(SystemExit):
        ask_bot.post_text(["  "])
