from unittest.mock import Mock

import pytest

from apps.server import runtime, tools
from apps.server.errors import ZooError
from apps.server.registry import TOOLS
from tests.tool_args import SAMPLE_ARGS


@pytest.mark.parametrize(
    "name",
    [
        "windows_list",
        "window_focus",
        "window_close",
        "installed_apps",
        "open_app",
        "close_app",
        "execute_command",
        "list_files",
        "get_file_info",
        "read_file",
        "write_file",
        "create_directory",
        "delete_file",
        "move_file",
        "copy_file",
    ],
)
@pytest.mark.parametrize("connected", [False, True])
def test_linux_tools_require_the_guest_service(monkeypatch, name, connected):
    agent = Mock()
    agent.has.return_value = False
    monkeypatch.setattr(tools.hub, "for_runtime", lambda runtime_id: agent if connected else None)
    execute = Mock(side_effect=AssertionError("tool reached the runtime"))
    monkeypatch.setattr(runtime, "exec_command", execute)
    monkeypatch.setattr(runtime, "read_archive", execute)
    monkeypatch.setattr(runtime, "write_archive", execute)

    with pytest.raises(ZooError) as error:
        TOOLS[name].fn("runtime-id", **SAMPLE_ARGS[name])

    assert error.value.code == "guest_unavailable"
    assert error.value.status_code == 503
    execute.assert_not_called()
    agent.exec_run.assert_not_called()
    agent.call.assert_not_called()
    agent.read.assert_not_called()
    agent.write.assert_not_called()


def test_stream_command_requires_a_guest(monkeypatch):
    monkeypatch.setattr(tools.hub, "for_runtime", lambda runtime_id: None)
    with pytest.raises(ZooError) as error:
        next(tools.ShellTools.stream_command("runtime-id", "echo hello"))
    assert error.value.code == "guest_unavailable"


def test_file_transfer_requires_a_guest(monkeypatch, tmp_path):
    monkeypatch.setattr(tools.hub, "for_runtime", lambda runtime_id: None)
    source = tmp_path / "hello.txt"
    source.write_text("hello")
    with pytest.raises(ZooError) as upload:
        tools.FileSystem.upload_file("runtime-id", str(source), ".")
    assert upload.value.code == "guest_unavailable"
    with pytest.raises(ZooError) as download:
        tools.FileSystem.download_file("runtime-id", "hello.txt")
    assert download.value.code == "guest_unavailable"
