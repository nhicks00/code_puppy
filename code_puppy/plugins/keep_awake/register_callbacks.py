"""Keep system awake while agent runs are active.

This plugin prevents sleep/lock interruptions during active Code Puppy work:
- macOS: uses `caffeinate` subprocess
- Windows: uses SetThreadExecutionState

Behavior is reference-counted by session_id so concurrent runs don't fight each
other. Keep-awake is released only when the last active run ends.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import threading
from typing import Any

from code_puppy.callbacks import register_callback

logger = logging.getLogger(__name__)

_DEFAULT_SESSION_KEY = "default"

# Windows SetThreadExecutionState flags
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


class _KeepAwakeManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_sessions: set[str] = set()
        self._caffeinate_process: subprocess.Popen[Any] | None = None

    def start(self, session_id: str | None) -> None:
        """Enable keep-awake for a session.

        Starts OS-level keep-awake only for the first active session.
        """
        key = session_id or _DEFAULT_SESSION_KEY

        should_enable = False
        with self._lock:
            was_empty = len(self._active_sessions) == 0
            self._active_sessions.add(key)
            if was_empty:
                should_enable = True

        if should_enable:
            self._enable_keep_awake()

    def stop(self, session_id: str | None) -> None:
        """Disable keep-awake for a session.

        Releases OS-level keep-awake only when the last session ends.
        """
        key = session_id or _DEFAULT_SESSION_KEY

        should_disable = False
        with self._lock:
            self._active_sessions.discard(key)
            if len(self._active_sessions) == 0:
                should_disable = True

        if should_disable:
            self._disable_keep_awake()

    def _enable_keep_awake(self) -> None:
        if sys.platform == "darwin":
            self._enable_macos_keep_awake()
            return

        if sys.platform.startswith("win"):
            self._enable_windows_keep_awake()
            return

        logger.debug("Keep-awake not supported on this platform: %s", sys.platform)

    def _disable_keep_awake(self) -> None:
        if sys.platform == "darwin":
            self._disable_macos_keep_awake()
            return

        if sys.platform.startswith("win"):
            self._disable_windows_keep_awake()
            return

    def _enable_macos_keep_awake(self) -> None:
        with self._lock:
            if (
                self._caffeinate_process is not None
                and self._caffeinate_process.poll() is None
            ):
                return

            try:
                self._caffeinate_process = subprocess.Popen(
                    ["caffeinate", "-dims"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                self._caffeinate_process = None
                logger.debug("'caffeinate' command not found; keep-awake disabled")
            except Exception as exc:
                self._caffeinate_process = None
                logger.debug("Failed to start macOS keep-awake: %s", exc)

    def _disable_macos_keep_awake(self) -> None:
        process: subprocess.Popen[Any] | None = None

        with self._lock:
            process = self._caffeinate_process
            self._caffeinate_process = None

        if process is None:
            return

        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _enable_windows_keep_awake(self) -> None:
        try:
            flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
            result = ctypes.windll.kernel32.SetThreadExecutionState(flags)
            if result == 0:
                logger.debug("SetThreadExecutionState failed while enabling")
        except Exception as exc:
            logger.debug("Failed to enable Windows keep-awake: %s", exc)

    def _disable_windows_keep_awake(self) -> None:
        try:
            result = ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
            if result == 0:
                logger.debug("SetThreadExecutionState failed while disabling")
        except Exception as exc:
            logger.debug("Failed to disable Windows keep-awake: %s", exc)


_KEEP_AWAKE_MANAGER = _KeepAwakeManager()


async def _on_agent_run_start(
    agent_name: str,
    model_name: str,
    session_id: str | None = None,
) -> None:
    """Enable keep-awake when an agent run starts."""
    _ = agent_name
    _ = model_name
    _KEEP_AWAKE_MANAGER.start(session_id)


async def _on_agent_run_end(
    agent_name: str,
    model_name: str,
    session_id: str | None = None,
    success: bool = True,
    error: Exception | None = None,
    response_text: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Release keep-awake when an agent run ends."""
    _ = agent_name
    _ = model_name
    _ = success
    _ = error
    _ = response_text
    _ = metadata
    _KEEP_AWAKE_MANAGER.stop(session_id)


register_callback("agent_run_start", _on_agent_run_start)
register_callback("agent_run_end", _on_agent_run_end)
