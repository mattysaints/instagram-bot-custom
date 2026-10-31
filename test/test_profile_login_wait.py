from unittest.mock import Mock

import pytest

from GramAddict.core import views
from GramAddict.core.device_facade import DeviceFacade


def profile_with_device(monkeypatch):
    device = Mock()
    device.app_id = "com.instagram.android"
    device.nodes_from_dump.return_value = [{"package": device.app_id}]
    device.account_chooser_continue.return_value = None
    device.dismiss_anr.return_value = None
    profile = views.ProfileView.__new__(views.ProfileView)
    profile.device = device
    monkeypatch.setattr(profile, "_new_ui_profile_button", lambda: False)
    monkeypatch.setattr(profile, "_old_ui_profile_button", lambda: False)
    monkeypatch.setattr(profile, "_profile_tab_button", lambda: False)
    monkeypatch.setattr(views, "random_sleep", Mock())
    return profile, device


def test_login_waits_for_manual_access_after_six_checks(monkeypatch):
    profile, device = profile_with_device(monkeypatch)
    device.is_login_screen.return_value = True
    profile._profile_tab_button = Mock(side_effect=[False] * 7 + [True])

    profile.click_on_avatar()

    assert views.random_sleep.call_count == 7
    device.back.assert_not_called()


def test_login_timeout_uses_login_required_instead_of_crash(monkeypatch):
    profile, device = profile_with_device(monkeypatch)
    device.is_login_screen.return_value = True

    with pytest.raises(DeviceFacade.LoginRequired):
        profile.click_on_avatar()

    assert views.random_sleep.call_count == views.MINUTI_ATTESA_LOGIN
    device.back.assert_not_called()


def test_other_screens_keep_six_attempt_limit(monkeypatch):
    profile, device = profile_with_device(monkeypatch)
    device.is_login_screen.return_value = False

    with pytest.raises(DeviceFacade.JsonRpcError, match="after 6 tries"):
        profile.click_on_avatar()

    assert device.back.call_count == 6
