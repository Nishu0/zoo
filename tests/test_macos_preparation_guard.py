"""SSH guard ownership is scoped to a channel and survives only its verification body."""

from io import BytesIO
from unittest.mock import Mock

import pytest

from server import macos


@pytest.mark.parametrize("failure", ["body", "read", "exec", "none"])
def test_verification_guard_always_closes_its_ssh_channel(monkeypatch, failure):
    channel = Mock()
    stream = Mock(wraps=BytesIO(b"locked\n"))
    channel.makefile.return_value = stream
    transport = Mock()
    transport.open_session.return_value = channel
    client = Mock()
    client.get_transport.return_value = transport
    monkeypatch.setattr(macos, "host", lambda _: client)
    if failure == "read":
        stream.readline.side_effect = TimeoutError("host did not grant the guard")
    elif failure == "exec":
        channel.exec_command.side_effect = ConnectionError("host disconnected")
    if failure == "none":
        with macos.preparation_guard("mac") as acquired:
            assert acquired
    else:
        with pytest.raises((RuntimeError, TimeoutError, ConnectionError)), macos.preparation_guard("mac") as acquired:
            assert acquired
            raise RuntimeError("verification failed")
    channel.close.assert_called_once_with()


def test_verification_guard_reports_contention_and_closes_the_failed_channel(monkeypatch):
    channel = Mock()
    channel.makefile.return_value = BytesIO(b"")
    client = Mock()
    client.get_transport.return_value.open_session.return_value = channel
    monkeypatch.setattr(macos, "host", lambda _: client)
    with macos.preparation_guard("mac") as acquired:
        assert not acquired
    channel.close.assert_called_once_with()


def test_verification_guard_reports_a_disconnected_host_before_opening_a_channel(monkeypatch):
    client = Mock()
    client.get_transport.return_value = None
    monkeypatch.setattr(macos, "host", lambda _: client)
    with pytest.raises(RuntimeError, match="disconnected"), macos.preparation_guard("mac"):
        pytest.fail("must not enter the verification body")
