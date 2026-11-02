from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from uiautomator2.exceptions import GatewayError

from GramAddict.core import decorators


def test_failed_automation_stops_without_more_device_calls(monkeypatch):
    device = Mock()
    state = SimpleNamespace(my_username="rb.coach")
    sessions = MagicMock()
    sessions.__getitem__.return_value = state
    configs = SimpleNamespace()
    restart = Mock()
    monkeypatch.setattr(decorators, "restart", restart)

    @decorators.run_safely(device, "emulator-5554", sessions, state, False, configs)
    def job():
        raise GatewayError("Uiautomator started failed.")

    with pytest.raises(SystemExit) as exc:
        job()

    assert exc.value.code == 2
    sessions.persist.assert_called_once_with(directory="rb.coach")
    restart.assert_not_called()
    device.assert_not_called()
    assert not device.mock_calls


def test_other_gateway_errors_keep_existing_recovery(monkeypatch):
    state = SimpleNamespace(my_username="rb.coach")
    sessions = MagicMock()
    sessions.__getitem__.return_value = state
    restart = Mock()
    monkeypatch.setattr(decorators, "restart", restart)

    @decorators.run_safely(Mock(), "emulator-5554", sessions, state, False, SimpleNamespace())
    def job():
        raise GatewayError("temporary gateway error")

    job()
    restart.assert_called_once()
    sessions.persist.assert_not_called()
