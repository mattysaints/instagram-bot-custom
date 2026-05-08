"""Smoke test for ai_dm + dm_followback plugin (no real device, no API call)."""
from GramAddict.plugins.dm_followback import DmFollowback
from GramAddict.core.ai_dm import (
    generate_dm,
    is_enabled,
    _build_dm_prompt,
    _validate_dm_output,
    _to_bool,
)
from GramAddict.core.plugin_loader import PluginLoader


def test_loader_sees_plugin():
    plugins = PluginLoader("GramAddict.plugins", first_run=False).plugins
    names = [p.__class__.__name__ for p in plugins]
    assert "DmFollowback" in names, f"DmFollowback missing! Found: {names}"
    print(f"OK PluginLoader sees {len(plugins)} plugins, DmFollowback present")


def test_prompt_build():
    p = _build_dm_prompt(
        target_username="test_user",
        full_name="Mario Rossi",
        bio="Powerlifter natural. Modena.",
        last_post_caption="Squat 180kg PR oggi",
        hint="personal trainer / coach a Fiorano Modenese",
        language="Italian",
        allow_emoji=True,
    )
    assert "Mario Rossi" in p
    assert "Powerlifter" in p
    assert "Squat 180kg" in p
    assert "Fiorano" in p
    assert "ASSOLUTAMENTE NIENTE hashtag" in p
    print("OK prompt builds with all context fields")
    print("--- PROMPT (first 600 chars) ---")
    print(p[:600])
    print("--- END ---")


def test_prompt_no_context():
    p = _build_dm_prompt(
        target_username=None,
        full_name=None,
        bio=None,
        last_post_caption=None,
        hint=None,
        language="Italian",
        allow_emoji=False,
    )
    assert "Nessuna informazione sul profilo disponibile" in p
    assert "ASSOLUTAMENTE NESSUNA emoji" in p
    print("OK prompt degrades gracefully with no context")


def test_guardrails():
    # ok
    assert (
        _validate_dm_output(
            "Ciao, grazie per il follow. Posso chiederti come stai gestendo la programmazione di forza?",
            allow_emoji=True,
        )
        is not None
    )
    # sell
    assert _validate_dm_output("Ciao! Scrivimi per coaching online", allow_emoji=True) is None
    # link
    assert _validate_dm_output("Ciao, visita https://wa.me/12345", allow_emoji=True) is None
    # multi-!
    assert _validate_dm_output("Ciao! Ottimo!! Davvero!!", allow_emoji=True) is None
    # hashtag
    assert _validate_dm_output("Ciao #fitness", allow_emoji=True) is None
    # emoji forbidden
    assert _validate_dm_output("Ciao 👋", allow_emoji=False) is None
    # emoji allowed (1)
    assert _validate_dm_output("Ciao 👋 grazie del follow", allow_emoji=True) is not None
    # emoji burst (3+)
    assert _validate_dm_output("Ciao 👋 🙌 💪 🔥", allow_emoji=True) is None
    print("OK all guardrails pass")


def test_to_bool():
    assert _to_bool("true") is True
    assert _to_bool("false") is False
    assert _to_bool("1") is True
    assert _to_bool("0") is False
    assert _to_bool(None, default=True) is True
    assert _to_bool("garbage", default=False) is False
    print("OK _to_bool")


if __name__ == "__main__":
    test_loader_sees_plugin()
    test_prompt_build()
    test_prompt_no_context()
    test_guardrails()
    test_to_bool()
    print("\nALL OK")

