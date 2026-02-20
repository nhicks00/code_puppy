"""Tests for terminal resize (SIGWINCH/SIGCONT) handling.

Tests the functionality added to handle terminal resize and resume events
and prevent display corruption.
"""

import platform
import signal
from unittest.mock import MagicMock, patch

import pytest

from code_puppy.terminal_utils import (
    check_and_fix_terminal_size,
    get_terminal_size,
    install_sigwinch_handler,
    is_sigwinch_handler_installed,
    refresh_terminal_on_resize,
    scroll_to_bottom,
    uninstall_sigwinch_handler,
)


class TestGetTerminalSize:
    """Tests for get_terminal_size function."""

    def test_returns_tuple(self):
        """Test that get_terminal_size returns a tuple of two integers."""
        result = get_terminal_size()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], int)
        assert isinstance(result[1], int)

    def test_returns_positive_values(self):
        """Test that terminal size values are positive."""
        cols, rows = get_terminal_size()
        assert cols > 0
        assert rows > 0

    def test_fallback_on_error(self):
        """Test fallback to (80, 24) when shutil.get_terminal_size fails."""
        with patch("code_puppy.terminal_utils.shutil.get_terminal_size") as mock_size:
            mock_size.side_effect = OSError("Terminal not available")
            result = get_terminal_size()
            assert result == (80, 24)


class TestRefreshTerminalOnResize:
    """Tests for refresh_terminal_on_resize function."""

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix-only test")
    def test_writes_ansi_sequences(self):
        """Test that refresh writes ANSI escape sequences on Unix."""
        with patch("code_puppy.terminal_utils.sys.stdout") as mock_stdout:
            refresh_terminal_on_resize()
            mock_stdout.write.assert_called()
            mock_stdout.flush.assert_called()
            call_args = mock_stdout.write.call_args[0][0]
            assert "\x1b[" in call_args

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only test")
    def test_no_op_on_windows(self):
        """Test that refresh is a no-op on Windows."""
        with patch("code_puppy.terminal_utils.sys.stdout") as mock_stdout:
            refresh_terminal_on_resize()
            mock_stdout.write.assert_not_called()

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix-only test")
    def test_handles_io_errors_gracefully(self):
        """Test that I/O errors during refresh don't raise exceptions."""
        with patch("code_puppy.terminal_utils.sys.stdout") as mock_stdout:
            mock_stdout.write.side_effect = IOError("Terminal write failed")
            refresh_terminal_on_resize()


class TestScrollToBottom:
    """Tests for scroll_to_bottom function."""

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix-only test")
    def test_writes_ansi_sequences(self):
        """Test that scroll_to_bottom writes ANSI escape sequences on Unix."""
        with patch("code_puppy.terminal_utils.sys.stdout") as mock_stdout:
            with patch(
                "code_puppy.terminal_utils.shutil.get_terminal_size"
            ) as mock_size:
                mock_size.return_value = MagicMock(lines=24, columns=80)
                scroll_to_bottom()
                mock_stdout.write.assert_called()
                mock_stdout.flush.assert_called()
                call_args = mock_stdout.write.call_args[0][0]
                assert "\x1b[" in call_args
                assert "24" in call_args  # Should reference row 24

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only test")
    def test_no_op_on_windows(self):
        """Test that scroll_to_bottom is a no-op on Windows."""
        with patch("code_puppy.terminal_utils.sys.stdout") as mock_stdout:
            scroll_to_bottom()
            mock_stdout.write.assert_not_called()

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix-only test")
    def test_handles_io_errors_gracefully(self):
        """Test that I/O errors during scroll don't raise exceptions."""
        with patch("code_puppy.terminal_utils.sys.stdout") as mock_stdout:
            mock_stdout.write.side_effect = IOError("Terminal write failed")
            scroll_to_bottom()


class TestCheckAndFixTerminalSize:
    """Tests for check_and_fix_terminal_size function."""

    def setup_method(self):
        """Reset state before each test."""
        import code_puppy.terminal_utils as tu

        self._saved_size = tu._last_terminal_size

    def teardown_method(self):
        """Restore state after each test."""
        import code_puppy.terminal_utils as tu

        tu._last_terminal_size = self._saved_size

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix-only test")
    def test_detects_significant_resize(self):
        """Test that significant size changes are detected."""
        import code_puppy.terminal_utils as tu

        tu._last_terminal_size = (80, 24)

        with patch.object(tu, "get_terminal_size", return_value=(120, 40)):
            with patch.object(tu, "scroll_to_bottom") as mock_scroll:
                result = check_and_fix_terminal_size()
                assert result is True
                mock_scroll.assert_called_once()

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix-only test")
    def test_ignores_small_resize(self):
        """Test that small size changes (jitter) are ignored."""
        import code_puppy.terminal_utils as tu

        tu._last_terminal_size = (80, 24)

        with patch.object(tu, "get_terminal_size", return_value=(81, 25)):
            with patch.object(tu, "scroll_to_bottom") as mock_scroll:
                result = check_and_fix_terminal_size()
                assert result is False
                mock_scroll.assert_not_called()

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix-only test")
    def test_returns_false_when_no_prior_size(self):
        """Test returns False on first call (no prior size to compare)."""
        import code_puppy.terminal_utils as tu

        tu._last_terminal_size = None
        result = check_and_fix_terminal_size()
        assert result is False


@pytest.mark.skipif(
    platform.system() == "Windows", reason="SIGWINCH not available on Windows"
)
class TestSigwinchHandler:
    """Tests for SIGWINCH handler installation/uninstallation."""

    def setup_method(self):
        """Ensure handler is uninstalled before each test."""
        uninstall_sigwinch_handler()

    def teardown_method(self):
        """Clean up handler after each test."""
        uninstall_sigwinch_handler()

    def test_install_handler_returns_true(self):
        """Test that installing handler returns True on Unix."""
        result = install_sigwinch_handler()
        assert result is True

    def test_is_installed_after_install(self):
        """Test that is_sigwinch_handler_installed returns True after install."""
        assert is_sigwinch_handler_installed() is False
        install_sigwinch_handler()
        assert is_sigwinch_handler_installed() is True

    def test_uninstall_handler(self):
        """Test that uninstalling handler works."""
        install_sigwinch_handler()
        assert is_sigwinch_handler_installed() is True
        result = uninstall_sigwinch_handler()
        assert result is True
        assert is_sigwinch_handler_installed() is False

    def test_install_idempotent(self):
        """Test that installing handler multiple times is safe."""
        result1 = install_sigwinch_handler()
        result2 = install_sigwinch_handler()
        assert result1 is True
        assert result2 is True
        assert is_sigwinch_handler_installed() is True

    def test_uninstall_idempotent(self):
        """Test that uninstalling handler multiple times is safe."""
        install_sigwinch_handler()
        result1 = uninstall_sigwinch_handler()
        result2 = uninstall_sigwinch_handler()
        assert result1 is True
        assert result2 is True
        assert is_sigwinch_handler_installed() is False

    def test_callback_is_called_on_resize(self):
        """Test that callback is invoked when SIGWINCH is received."""
        import os
        import time

        callback = MagicMock()
        install_sigwinch_handler(callback=callback)

        os.kill(os.getpid(), signal.SIGWINCH)
        time.sleep(0.1)

        callback.assert_called()

    def test_callback_errors_dont_crash(self):
        """Test that callback errors don't crash the signal handler."""
        import os
        import time

        callback = MagicMock(side_effect=ValueError("Callback error"))
        install_sigwinch_handler(callback=callback)

        os.kill(os.getpid(), signal.SIGWINCH)
        time.sleep(0.1)

        assert is_sigwinch_handler_installed() is True

    def test_sigcont_handler_installed(self):
        """Test that SIGCONT handler is also installed."""
        install_sigwinch_handler()

        # Verify SIGCONT handler is our handler
        import code_puppy.terminal_utils as tu

        current_handler = signal.getsignal(signal.SIGCONT)
        assert current_handler == tu._handle_sigcont

    def test_sigcont_handler_uninstalled(self):
        """Test that SIGCONT handler is restored on uninstall."""
        original_handler = signal.getsignal(signal.SIGCONT)
        install_sigwinch_handler()
        uninstall_sigwinch_handler()

        current_handler = signal.getsignal(signal.SIGCONT)
        assert current_handler == original_handler


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-specific test")
class TestWindowsSigwinch:
    """Tests for SIGWINCH behavior on Windows."""

    def test_install_returns_false_on_windows(self):
        """Test that install returns False on Windows."""
        result = install_sigwinch_handler()
        assert result is False

    def test_is_installed_false_on_windows(self):
        """Test that is_installed returns False on Windows."""
        assert is_sigwinch_handler_installed() is False

    def test_uninstall_returns_false_on_windows(self):
        """Test that uninstall returns False on Windows."""
        result = uninstall_sigwinch_handler()
        assert result is False

    def test_check_and_fix_returns_false_on_windows(self):
        """Test that check_and_fix returns False on Windows."""
        result = check_and_fix_terminal_size()
        assert result is False
