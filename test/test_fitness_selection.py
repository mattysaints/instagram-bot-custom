"""Selezione dei profili trovati tramite luogo."""
import pytest

from GramAddict.core import filter as filter_mod
from GramAddict.core.filter import Filter, Profile, SkipReason
from GramAddict.core.views import FollowStatus


def make_filter(monkeypatch, bio="", name="Mario Rossi", category="", enabled=True):
    profile = Profile(0, FollowStatus.FOLLOW, False, False, False, 30, bio, None, name, category)
    profile.set_followers_and_following(1000, 500)
    f = Filter.__new__(Filter)
    f.conditions = {"require_fitness_for_place_likers": enabled}
    f.storage = None
    monkeypatch.setattr(f, "get_all_data", lambda device: profile)
    monkeypatch.setattr(filter_mod, "args", type("Args", (), {"disable_filters": False})(), raising=False)
    return f


@pytest.mark.parametrize("bio,name,category", [
    ("Mi alleno in palestra", "Mario", ""),
    ("Bodybuilding e preparazione", "Mario", ""),
    ("Coach", "Mario", "Personal Trainer"),
    ("", "Giulia Fitness", ""),
])
def test_luogo_con_segnali_fitness_e_ammesso(monkeypatch, bio, name, category):
    f = make_filter(monkeypatch, bio, name, category)
    _, skipped = f.check_profile(None, "mario", current_mode="place-likers-recent")
    assert not skipped


@pytest.mark.parametrize("bio", ["", "Appassionato di viaggi e fotografia", "Welfare e benefit aziendali"])
def test_luogo_senza_segnali_fitness_viene_saltato(monkeypatch, bio):
    f = make_filter(monkeypatch, bio)
    reasons = []
    monkeypatch.setattr(f, "return_check_profile", lambda u, p, reason=None: reasons.append(reason) or reason is not None)
    _, skipped = f.check_profile(None, "mario", current_mode="place-likers-recent")
    assert skipped and reasons[-1] is SkipReason.NO_FITNESS_SIGNAL


@pytest.mark.parametrize("mode", ["blogger-followers", "hashtag-likers-recent", "blogger", None])
def test_sorgenti_fitness_non_richiedono_bio_fitness(monkeypatch, mode):
    f = make_filter(monkeypatch, "Viaggi e fotografia")
    _, skipped = f.check_profile(None, "mario", current_mode=mode)
    assert not skipped


def test_regola_disattivata_non_cambia_la_selezione(monkeypatch):
    f = make_filter(monkeypatch, "Viaggi e fotografia", enabled=False)
    _, skipped = f.check_profile(None, "mario", current_mode="place-likers-recent")
    assert not skipped


def test_controllo_del_luogo_avviene_prima_delle_interazioni(monkeypatch):
    from GramAddict.core import interaction

    f = make_filter(monkeypatch, "Viaggi e fotografia")
    monkeypatch.setattr(interaction, "ProfileView", lambda device: pytest.fail("Interazione non consentita"))
    result = interaction.interact_with_user(
        device=None, username="mario", my_username="rb.coach", likes_count=1,
        likes_percentage=100, stories_percentage=0, can_follow=True,
        follow_percentage=100, comment_percentage=100, pm_percentage=0,
        profile_filter=f, args=None, session_state=None, scraping_file=None,
        current_mode="place-likers-recent",
    )
    assert result == (False, False, False, False, False, 0, 0, 0)
