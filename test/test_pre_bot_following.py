"""Old/manual following is protected; documented bot follows can be commented."""
import json
from pathlib import Path

import pytest
import yaml

from GramAddict.core import filter as filter_mod
from GramAddict.core import storage as storage_mod
from GramAddict.core.filter import Filter, Profile, SkipReason
from GramAddict.core.storage import FollowingStatus, Storage
from GramAddict.core.views import FollowStatus
from GramAddict.plugins.interact_blogger import _size_filters_suspended


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_mod, "ACCOUNTS", str(tmp_path))
    return Storage("rb.coach")


def make_filter(storage, **conditions):
    f = Filter.__new__(Filter)
    f.storage = storage
    f.conditions = {"skip_following": True, "skip_following_before_bot": True}
    f.conditions.update(conditions)
    return f


def check_profile(f, monkeypatch, status=FollowStatus.FOLLOWING, username="anna"):
    profile = Profile(0, status, False, False, False, 30, "Mi alleno", None, "Anna")
    profile.set_followers_and_following(1000, 500)
    monkeypatch.setattr(f, "get_all_data", lambda device: profile)
    monkeypatch.setattr(filter_mod, "args", type("Args", (), {"disable_filters": False})(), raising=False)
    return f.check_profile(None, username)[1]


def test_existing_following_is_protected_after_restart(storage, monkeypatch):
    f = make_filter(storage)
    assert check_profile(f, monkeypatch)
    assert storage.history_filter_users["anna"]["skip_reason"] == SkipReason.FOLLOWING_BEFORE_BOT.name
    restarted = Storage("rb.coach")
    assert restarted.following_origin("@ANNA") == "preexisting"
    assert not make_filter(restarted).can_comment_user("anna")


def test_bot_follow_remains_allowed_after_comment_only_visits(storage, monkeypatch):
    storage.add_interacted_user("anna", "first", followed=True)
    storage.add_interacted_user("anna", "second", liked=1, commented=1)
    restarted = Storage("rb.coach")
    f = make_filter(restarted)
    assert not check_profile(f, monkeypatch)
    assert f.can_comment_user("anna")
    assert restarted.interacted_users["anna"]["followed_by_bot"] is True
    assert restarted.get_following_status("anna") is FollowingStatus.FOLLOWED


@pytest.mark.parametrize("action, expected", [
    ({"followed": True}, FollowingStatus.FOLLOWED),
    ({"followed": True, "is_requested": True}, FollowingStatus.REQUESTED),
    ({"unfollowed": True}, FollowingStatus.UNFOLLOWED),
])
def test_return_visit_preserves_follow_and_unfollow_state(storage, action, expected):
    storage.add_interacted_user("anna", "relationship", **action)
    storage.add_interacted_user("anna", "visit", commented=1)
    restarted = Storage("rb.coach")
    assert restarted.get_following_status("anna") is expected
    assert restarted.was_unfollowed_before("anna") == (expected is FollowingStatus.UNFOLLOWED)


@pytest.mark.parametrize("record", [
    {"following_status": "followed"},
    {"following_status": "requested"},
    {"followed": True, "following_status": "none"},
])
def test_legacy_bot_follow_evidence_is_preserved(storage, monkeypatch, record):
    storage.interacted_users["anna"] = dict(record)
    storage.add_interacted_user("anna", "new", liked=1)
    assert not check_profile(make_filter(Storage("rb.coach")), monkeypatch)


@pytest.mark.parametrize("record", [
    {"followed": False, "following_status": "none", "liked": 20},
    {"following_status": "scraped", "commented": 5},
    {"followed": "true", "following_status": "none"},
    {},
])
def test_visit_or_comment_is_not_follow_evidence(storage, monkeypatch, record):
    storage.interacted_users["anna"] = record
    assert check_profile(make_filter(storage), monkeypatch)


def test_old_following_stays_protected_after_bot_refollow(storage, monkeypatch):
    f = make_filter(storage)
    assert check_profile(f, monkeypatch)
    storage.add_interacted_user("anna", "unfollow", unfollowed=True)
    storage.add_interacted_user("anna", "refollow", followed=True)
    assert check_profile(f, monkeypatch, FollowStatus.FOLLOW)
    assert check_profile(f, monkeypatch)
    assert not make_filter(Storage("rb.coach")).can_comment_user("anna")


def test_later_manual_follow_is_also_excluded(storage, monkeypatch):
    f = make_filter(storage)
    assert not check_profile(f, monkeypatch, FollowStatus.FOLLOW)
    assert f.can_comment_user("anna")
    assert check_profile(f, monkeypatch, FollowStatus.FOLLOWING)
    assert not f.can_comment_user("anna")


def test_new_bot_follow_from_previously_unfollowed_profile_is_allowed(storage, monkeypatch):
    f = make_filter(storage)
    assert not check_profile(f, monkeypatch, FollowStatus.FOLLOW)
    storage.add_interacted_user("anna", "follow", followed=True)
    assert not check_profile(f, monkeypatch, FollowStatus.FOLLOWING)


def test_requested_bot_follow_is_allowed_once_accepted(storage, monkeypatch):
    storage.add_interacted_user("anna", "request", followed=True, is_requested=True)
    assert not check_profile(make_filter(storage), monkeypatch, FollowStatus.FOLLOWING)


@pytest.mark.parametrize("keep_skip_following", [True, False])
def test_blogger_filter_suspension_cannot_bypass_protection(storage, monkeypatch, keep_skip_following):
    f = make_filter(storage, max_followers=4000)
    original = dict(f.conditions)
    with _size_filters_suspended(f, keep_skip_following=keep_skip_following):
        assert f.protects_pre_bot_following()
        assert check_profile(f, monkeypatch, username="old")
        storage.add_interacted_user("new", "follow", followed=True)
        assert not check_profile(f, monkeypatch, username="new")
    assert f.conditions == original


def test_disabled_policy_preserves_skip_following(storage, monkeypatch):
    storage.add_interacted_user("anna", "first", followed=True)
    f = make_filter(storage, skip_following_before_bot=False)
    assert check_profile(f, monkeypatch)
    f.conditions["skip_following"] = False
    assert not check_profile(f, monkeypatch)
    assert f.can_comment_user("anna")


def test_other_filters_still_apply_to_bot_follow(storage, monkeypatch):
    storage.add_interacted_user("anna", "first", followed=True)
    assert check_profile(make_filter(storage, max_followers=100), monkeypatch)


def test_accounts_are_isolated(storage, monkeypatch):
    storage.add_interacted_user("anna", "first", followed=True)
    personal = Storage("roberto_buonomo_ifbbpro")
    assert not check_profile(make_filter(storage), monkeypatch)
    assert check_profile(make_filter(personal), monkeypatch)
    assert storage.following_origin("anna") == "after_bot"


@pytest.mark.parametrize("payload", ["{", "[]", '{"anna": "invalid"}', '{"@": "preexisting"}'])
def test_invalid_origin_file_does_not_allow_comments(storage, monkeypatch, payload):
    Path(storage.following_origins_path).write_text(payload, encoding="utf-8")
    restarted = Storage("rb.coach")
    restarted.add_interacted_user("anna", "first", followed=True)
    f = make_filter(restarted)
    assert check_profile(f, monkeypatch)
    assert not f.can_comment_user("anna")
    assert Path(storage.following_origins_path).read_text(encoding="utf-8") == payload


def test_failed_origin_write_does_not_grant_exception(storage, monkeypatch):
    monkeypatch.setattr(storage_mod, "_resilient_write", lambda *args, **kwargs: False)
    assert check_profile(make_filter(storage), monkeypatch)
    storage.add_interacted_user("anna", "first", followed=True)
    assert not make_filter(storage).can_comment_user("anna")


def test_case_and_at_sign_normalization(storage):
    storage.observe_following("@ANNA", True)
    storage.add_interacted_user("anna", "first", followed=True)
    assert storage.following_origin("anna") == "preexisting"
    assert Storage("rb.coach").following_origin("@Anna") == "preexisting"


def test_duplicate_normalized_handles_keep_protection(storage):
    Path(storage.following_origins_path).write_text(
        json.dumps({"@ANNA": "preexisting", "anna": "after_bot"}), encoding="utf-8"
    )
    assert Storage("rb.coach").following_origin("anna") == "preexisting"


def test_comment_guard_stops_before_device_or_generator(storage):
    from GramAddict.core.interaction import _comment

    storage.observe_following("anna", True)
    assert _comment(None, "rb.coach", 100, None, None, None,
                    target_username="anna", profile_filter=make_filter(storage)) is False


def test_missing_storage_or_target_does_not_allow_comments():
    f = make_filter(None)
    assert not f.can_comment_user("anna")
    assert not f.can_comment_user(None)


@pytest.mark.parametrize("account", ["rb.coach", "roberto_buonomo_ifbbpro"])
def test_roberto_configs_enable_policy_and_preserve_source_exclusions(account):
    root = Path(__file__).resolve().parents[1]
    folder = root / "accounts" / account
    filters = yaml.safe_load((folder / "filters.yml").read_text(encoding="utf-8"))
    assert filters["skip_following_before_bot"] is True
    for filename in ("config.yml", "config-alternato.yml"):
        config = yaml.safe_load((folder / filename).read_text(encoding="utf-8"))
        assert config["blogger-followers-no-comment"] is True
        assert config["blogger-comment-only"] is True
