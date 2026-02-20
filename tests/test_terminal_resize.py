"""Tests for terminal resize (SIGWINCH) handling.

Tests the functionality added to handle terminal resize events
and prevent display corruption.
"""

import platform
import signal
from unittest.mock import MagicMock, patch

import pytest

from code_puppy.terminal_utils import (
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
            # Verify ANSI sequences were written
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
            # Should not raise
            refresh_terminal_on_resize()


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
        callback = MagicMock()
        install_sigwinch_handler(callback=callback)

        # Manually trigger SIGWINCH to test the handler
        # We need to patch _last_terminal_size to force a "significant" change
        import code_puppy.terminal_utils as tu

        old_size = tu._last_terminal_size
        tu._last_terminal_size = (80, 24)  # Set a baseline

        # Simulate a significant resize
        with patch.object(tu, "get_terminal_size", return_value=(120, 40)):
            # Send SIGWINCH to ourselves
            import os

            os.kill(os.getpid(), signal.SIGWINCH)

        # Give the signal a moment to be processed
        import time

        time.sleep(0.1)

        # Callback should have been called
        callback.assert_called()

        # Restore
        tu._last_terminal_size = old_size

    def test_callback_errors_dont_crash(self):
        """Test that callback errors don't crash the signal handler."""
        callback = MagicMock(side_effect=ValueError("Callback error"))
        install_sigwinch_handler(callback=callback)

        import code_puppy.terminal_utils as tu

        tu._last_terminal_size = (80, 24)

        with patch.object(tu, "get_terminal_size", return_value=(120, 40)):
            # Should not raise despite callback error
            import os

            os.kill(os.getpid(), signal.SIGWINCH)

        import time

        time.sleep(0.1)

        # Handler should have survived
        assert is_sigwinch_handler_installed() is True

    def test_small_size_change_ignored(self):
        """Test that small size changes (1-2 chars) don't trigger refresh."""
        callback = MagicMock()
        install_sigwinch_handler(callback=callback)

        import code_puppy.terminal_utils as tu

        tu._last_terminal_size = (80, 24)

        # Simulate a tiny resize (within threshold)
        with patch.object(tu, "get_terminal_size", return_value=(81, 25)):
            import os

            os.kill(os.getpid(), signal.SIGWINCH)

        import time

        time.sleep(0.1)

        # Callback should NOT have been called for small change
        callback.assert_not_called()


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
                # Verify ANSI sequences include cursor positioning
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
            # Should not raise
            scroll_to_bottom()
