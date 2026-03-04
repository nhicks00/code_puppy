from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from code_puppy.plugins.keep_awake import register_callbacks as plugin


class TestKeepAwakeManager:
    def test_macos_refcount_starts_once_stops_on_last(self, monkeypatch):
        manager = plugin._KeepAwakeManager()
        monkeypatch.setattr(plugin.sys, "platform", "darwin")

        process = MagicMock()
        process.poll.return_value = None

        popen_calls = []

        def fake_popen(*args, **kwargs):
            popen_calls.append((args, kwargs))
            return process

        monkeypatch.setattr(plugin.subprocess, "Popen", fake_popen)

        manager.start("s1")
        manager.start("s2")
        assert len(popen_calls) == 1

        manager.stop("s1")
        process.terminate.assert_not_called()

        manager.stop("s2")
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=2)

    def test_macos_missing_caffeinate_is_safe(self, monkeypatch):
        manager = plugin._KeepAwakeManager()
        monkeypatch.setattr(plugin.sys, "platform", "darwin")

        def fake_popen(*_args, **_kwargs):
            raise FileNotFoundError("caffeinate not installed")

        monkeypatch.setattr(plugin.subprocess, "Popen", fake_popen)

        manager.start("s1")
        manager.stop("s1")

    def test_windows_execution_state_enable_disable(self, monkeypatch):
        manager = plugin._KeepAwakeManager()
        monkeypatch.setattr(plugin.sys, "platform", "win32")

        set_state = MagicMock(return_value=1)
        windll_stub = SimpleNamespace(
            kernel32=SimpleNamespace(SetThreadExecutionState=set_state)
        )

        monkeypatch.setattr(plugin.ctypes, "windll", windll_stub, raising=False)

        manager.start("s1")
        manager.stop("s1")

        expected_enable = (
            plugin._ES_CONTINUOUS
            | plugin._ES_SYSTEM_REQUIRED
            | plugin._ES_DISPLAY_REQUIRED
        )
        assert set_state.call_args_list[0].args == (expected_enable,)
        assert set_state.call_args_list[1].args == (plugin._ES_CONTINUOUS,)


class TestKeepAwakeCallbacks:
    @pytest.mark.asyncio
    async def test_callbacks_forward_session_id(self, monkeypatch):
        start = MagicMock()
        stop = MagicMock()

        monkeypatch.setattr(plugin._KEEP_AWAKE_MANAGER, "start", start)
        monkeypatch.setattr(plugin._KEEP_AWAKE_MANAGER, "stop", stop)

        await plugin._on_agent_run_start("code-puppy", "model-x", "session-123")
        await plugin._on_agent_run_end(
            "code-puppy",
            "model-x",
            "session-123",
            success=True,
            error=None,
            response_text="ok",
            metadata={"k": "v"},
        )

        start.assert_called_once_with("session-123")
        stop.assert_called_once_with("session-123")
