"""Tests verifying session history is saved in all exit/error paths.

Regression tests for the bug where auto_save_session_if_enabled() was skipped
when the interactive loop exited via `continue` or `break` before reaching the
normal save call site. Affected paths:
  - result is None (agent task cancelled or API error swallowed internally)
  - EOFError (Ctrl+D)
  - /exit and /quit commands
  - Wiggum loop KeyboardInterrupt
  - Wiggum loop Exception
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirror the pattern in test_cli_runner_full_coverage.py)
# ---------------------------------------------------------------------------


def _mock_renderer():
    r = MagicMock()
    r.console = MagicMock()
    r.console.file = MagicMock()
    r.console.file.flush = MagicMock()
    r.start = MagicMock()
    r.stop = MagicMock()
    return r


def _mock_parse_result(prompt="hello"):
    m = MagicMock()
    m.prompt = prompt
    m.warnings = []
    m.attachments = []
    m.link_attachments = []
    return m


def _base_patches():
    """Minimal patches required to run interactive_mode without real infra."""
    return {
        "code_puppy.cli_runner.print_truecolor_warning": MagicMock(),
        "code_puppy.cli_runner.get_cancel_agent_display_name": MagicMock(
            return_value="Ctrl+C"
        ),
        "code_puppy.cli_runner.reset_windows_terminal_ansi": MagicMock(),
        "code_puppy.cli_runner.reset_windows_terminal_full": MagicMock(),
        "code_puppy.cli_runner.save_command_to_history": MagicMock(),
        "code_puppy.cli_runner.finalize_autosave_session": MagicMock(
            return_value="session-1"
        ),
        "code_puppy.cli_runner.COMMAND_HISTORY_FILE": "/tmp/test_history",
        "code_puppy.command_line.motd.print_motd": MagicMock(),
        "code_puppy.command_line.onboarding_wizard.should_show_onboarding": MagicMock(
            return_value=False
        ),
    }


async def _run_interactive(input_fn, extra_patches=None):
    """Run interactive_mode with a mocked save function; return the mock."""
    mock_save = MagicMock()
    agent = MagicMock()
    agent.get_user_prompt.return_value = "task:"

    patches = _base_patches()
    patches["code_puppy.cli_runner.auto_save_session_if_enabled"] = mock_save

    with ExitStack() as stack:
        for target, value in patches.items():
            stack.enter_context(patch(target, value))
        stack.enter_context(
            patch(
                "code_puppy.command_line.prompt_toolkit_completion"
                ".get_input_with_combined_completion",
                side_effect=input_fn,
            )
        )
        stack.enter_context(
            patch(
                "code_puppy.command_line.prompt_toolkit_completion"
                ".get_prompt_with_active_model",
                return_value="> ",
            )
        )
        stack.enter_context(
            patch(
                "code_puppy.agents.agent_manager.get_current_agent",
                return_value=agent,
            )
        )
        if extra_patches:
            for target, value in extra_patches.items():
                stack.enter_context(patch(target, value))

        from code_puppy.cli_runner import interactive_mode

        await interactive_mode(_mock_renderer())

    return mock_save


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSessionSavedOnCancellation:
    """Session must be saved when the agent task returns None (cancellation / swallowed error)."""

    @pytest.mark.anyio
    async def test_save_called_when_result_is_none(self):
        """auto_save_session_if_enabled() must be called before `continue` when result is None."""
        call_count = 0

        async def fake_input(*a, **kw):
            nonlocal call_count
            call_count += 1
            return "do something" if call_count == 1 else "/exit"

        mock_save = await _run_interactive(
            fake_input,
            extra_patches={
                # Simulate a cancelled / error-swallowed agent run returning None
                "code_puppy.cli_runner.run_prompt_with_attachments": AsyncMock(
                    return_value=(None, MagicMock())
                ),
                "code_puppy.cli_runner.parse_prompt_attachments": MagicMock(
                    return_value=_mock_parse_result("do something")
                ),
                "code_puppy.command_line.wiggum_state.is_wiggum_active": MagicMock(
                    return_value=False
                ),
            },
        )

        # One save for cancelled prompt + one save for /exit
        assert mock_save.call_count == 2


class TestSessionSavedOnExit:
    """Session must be saved on every graceful exit path."""

    @pytest.mark.anyio
    async def test_save_called_on_eof(self):
        """auto_save_session_if_enabled() must be called when Ctrl+D (EOFError) exits the loop."""
        mock_save = await _run_interactive(
            AsyncMock(side_effect=EOFError),
        )
        mock_save.assert_called_once()

    @pytest.mark.anyio
    async def test_save_called_on_exit_command(self):
        """/exit command must trigger a save before breaking out of the loop."""
        mock_save = await _run_interactive(
            AsyncMock(return_value="/exit"),
            extra_patches={
                "code_puppy.command_line.wiggum_state.is_wiggum_active": MagicMock(
                    return_value=False
                ),
            },
        )
        mock_save.assert_called_once()

    @pytest.mark.anyio
    async def test_save_called_on_quit_command(self):
        """/quit command must trigger a save before breaking out of the loop."""
        mock_save = await _run_interactive(
            AsyncMock(return_value="/quit"),
            extra_patches={
                "code_puppy.command_line.wiggum_state.is_wiggum_active": MagicMock(
                    return_value=False
                ),
            },
        )
        mock_save.assert_called_once()


class TestSessionSavedOnWiggumError:
    """Session must be saved when the Wiggum loop exits due to an error or interrupt."""

    @pytest.mark.anyio
    async def test_save_called_on_wiggum_keyboard_interrupt(self):
        """Save must be called when Wiggum loop is interrupted by KeyboardInterrupt."""
        # Track call counts for input and agent run separately
        input_count = 0
        run_count = 0

        async def fake_input(*a, **kw):
            nonlocal input_count
            input_count += 1
            if input_count == 1:
                return "do something"
            # After the wiggum loop breaks, exit cleanly
            raise EOFError

        result_mock = MagicMock()
        result_mock.output = "ok"
        result_mock.all_messages = MagicMock(return_value=[])

        # wiggum_active: True on first check (triggers loop), False after
        wiggum_calls = 0

        def fake_wiggum_active():
            nonlocal wiggum_calls
            wiggum_calls += 1
            return wiggum_calls == 1

        async def fake_run(*a, **kw):
            nonlocal run_count
            run_count += 1
            if run_count == 1:
                # First call: normal prompt succeeds
                return (result_mock, MagicMock())
            # Second call (inside Wiggum loop): simulate interrupt
            raise KeyboardInterrupt

        mock_save = await _run_interactive(
            fake_input,
            extra_patches={
                "code_puppy.cli_runner.run_prompt_with_attachments": AsyncMock(
                    side_effect=fake_run
                ),
                "code_puppy.cli_runner.parse_prompt_attachments": MagicMock(
                    return_value=_mock_parse_result("do something")
                ),
                "code_puppy.command_line.wiggum_state.is_wiggum_active": fake_wiggum_active,
                "code_puppy.command_line.wiggum_state.get_wiggum_prompt": MagicMock(
                    return_value="do something"
                ),
                "code_puppy.command_line.wiggum_state.increment_wiggum_count": MagicMock(
                    return_value=1
                ),
                "code_puppy.command_line.wiggum_state.stop_wiggum": MagicMock(),
                "code_puppy.messaging.emit_warning": MagicMock(),
                "code_puppy.messaging.emit_system_message": MagicMock(),
                "code_puppy.messaging.get_message_bus": MagicMock(
                    return_value=MagicMock()
                ),
            },
        )

        mock_save.assert_called()

    @pytest.mark.anyio
    async def test_save_called_on_wiggum_exception(self):
        """Save must be called when Wiggum loop raises a generic Exception."""
        input_count = 0
        run_count = 0

        async def fake_input(*a, **kw):
            nonlocal input_count
            input_count += 1
            if input_count == 1:
                return "do something"
            raise EOFError

        result_mock = MagicMock()
        result_mock.output = "ok"
        result_mock.all_messages = MagicMock(return_value=[])

        wiggum_calls = 0

        def fake_wiggum_active():
            nonlocal wiggum_calls
            wiggum_calls += 1
            return wiggum_calls == 1

        async def fake_run(*a, **kw):
            nonlocal run_count
            run_count += 1
            if run_count == 1:
                return (result_mock, MagicMock())
            raise RuntimeError("simulated wiggum tool failure")

        mock_save = await _run_interactive(
            fake_input,
            extra_patches={
                "code_puppy.cli_runner.run_prompt_with_attachments": AsyncMock(
                    side_effect=fake_run
                ),
                "code_puppy.cli_runner.parse_prompt_attachments": MagicMock(
                    return_value=_mock_parse_result("do something")
                ),
                "code_puppy.command_line.wiggum_state.is_wiggum_active": fake_wiggum_active,
                "code_puppy.command_line.wiggum_state.get_wiggum_prompt": MagicMock(
                    return_value="do something"
                ),
                "code_puppy.command_line.wiggum_state.increment_wiggum_count": MagicMock(
                    return_value=1
                ),
                "code_puppy.command_line.wiggum_state.stop_wiggum": MagicMock(),
                "code_puppy.messaging.emit_warning": MagicMock(),
                "code_puppy.messaging.emit_system_message": MagicMock(),
                "code_puppy.messaging.emit_error": MagicMock(),
                "code_puppy.messaging.get_message_bus": MagicMock(
                    return_value=MagicMock()
                ),
            },
        )

        mock_save.assert_called()
