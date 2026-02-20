"""Terminal utilities for cross-platform terminal state management.

Handles Windows console mode resets, Unix terminal sanity restoration,
and terminal resize (SIGWINCH) handling.
"""

import os
import platform
import shutil
import signal
import subprocess
import sys
from typing import TYPE_CHECKING, Callable, Optional, Tuple

if TYPE_CHECKING:
    from rich.console import Console

# Store the original console ctrl handler so we can restore it if needed
_original_ctrl_handler: Optional[Callable] = None

# Terminal resize handling state
_resize_handler_installed: bool = False
_last_terminal_size: Optional[Tuple[int, int]] = None
_resize_callback: Optional[Callable[[], None]] = None
_original_sigwinch_handler: Optional[Callable] = None


def reset_windows_terminal_ansi() -> None:
    """Reset ANSI formatting on Windows stdout/stderr.

    This is a lightweight reset that just clears ANSI escape sequences.
    Use this for quick resets after output operations.
    """
    if platform.system() != "Windows":
        return

    try:
        sys.stdout.write("\x1b[0m")  # Reset ANSI formatting
        sys.stdout.flush()
        sys.stderr.write("\x1b[0m")
        sys.stderr.flush()
    except Exception:
        pass  # Silently ignore errors - best effort reset


def reset_windows_console_mode() -> None:
    """Full Windows console mode reset using ctypes.

    This resets both stdout and stdin console modes to restore proper
    terminal behavior after interrupts (Ctrl+C, Ctrl+D). Without this,
    the terminal can become unresponsive (can't type characters).
    """
    if platform.system() != "Windows":
        return

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32

        # Reset stdout
        STD_OUTPUT_HANDLE = -11
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)

        # Enable virtual terminal processing and line input
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))

        # Console mode flags for stdout
        ENABLE_PROCESSED_OUTPUT = 0x0001
        ENABLE_WRAP_AT_EOL_OUTPUT = 0x0002
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

        new_mode = (
            mode.value
            | ENABLE_PROCESSED_OUTPUT
            | ENABLE_WRAP_AT_EOL_OUTPUT
            | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        )
        kernel32.SetConsoleMode(handle, new_mode)

        # Reset stdin
        STD_INPUT_HANDLE = -10
        stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)

        # Console mode flags for stdin
        ENABLE_LINE_INPUT = 0x0002
        ENABLE_ECHO_INPUT = 0x0004
        ENABLE_PROCESSED_INPUT = 0x0001

        stdin_mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(stdin_handle, ctypes.byref(stdin_mode))

        new_stdin_mode = (
            stdin_mode.value
            | ENABLE_LINE_INPUT
            | ENABLE_ECHO_INPUT
            | ENABLE_PROCESSED_INPUT
        )
        kernel32.SetConsoleMode(stdin_handle, new_stdin_mode)

    except Exception:
        pass  # Silently ignore errors - best effort reset


def flush_windows_keyboard_buffer() -> None:
    """Flush the Windows keyboard buffer.

    Clears any pending keyboard input that could interfere with
    subsequent input operations after an interrupt.
    """
    if platform.system() != "Windows":
        return

    try:
        import msvcrt

        while msvcrt.kbhit():
            msvcrt.getch()
    except Exception:
        pass  # Silently ignore errors - best effort flush


def reset_windows_terminal_full() -> None:
    """Perform a full Windows terminal reset (ANSI + console mode + keyboard buffer).

    Combines ANSI reset, console mode reset, and keyboard buffer flush
    for complete terminal state restoration after interrupts.
    """
    if platform.system() != "Windows":
        return

    reset_windows_terminal_ansi()
    reset_windows_console_mode()
    flush_windows_keyboard_buffer()


def reset_unix_terminal() -> None:
    """Reset Unix/Linux/macOS terminal to sane state.

    Uses the `reset` command to restore terminal sanity.
    Silently fails if the command isn't available.
    """
    if platform.system() == "Windows":
        return

    try:
        subprocess.run(["reset"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass  # Silently fail if reset command isn't available


def reset_terminal() -> None:
    """Cross-platform terminal reset.

    Automatically detects the platform and performs the appropriate
    terminal reset operation.
    """
    if platform.system() == "Windows":
        reset_windows_terminal_full()
    else:
        reset_unix_terminal()


def disable_windows_ctrl_c() -> bool:
    """Disable Ctrl+C processing at the Windows console input level.

    This removes ENABLE_PROCESSED_INPUT from stdin, which prevents
    Ctrl+C from being interpreted as a signal at all. Instead, it
    becomes just a regular character (^C) that gets ignored.

    This is more reliable than SetConsoleCtrlHandler because it
    prevents Ctrl+C from being processed before it reaches any handler.

    Returns:
        True if successfully disabled, False otherwise.
    """
    global _original_ctrl_handler

    if platform.system() != "Windows":
        return False

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32

        # Get stdin handle
        STD_INPUT_HANDLE = -10
        stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)

        # Get current console mode
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(stdin_handle, ctypes.byref(mode)):
            return False

        # Save original mode for potential restoration
        _original_ctrl_handler = mode.value

        # Console mode flags
        ENABLE_PROCESSED_INPUT = 0x0001  # This makes Ctrl+C generate signals

        # Remove ENABLE_PROCESSED_INPUT to disable Ctrl+C signal generation
        new_mode = mode.value & ~ENABLE_PROCESSED_INPUT

        if kernel32.SetConsoleMode(stdin_handle, new_mode):
            return True
        return False

    except Exception:
        return False


def enable_windows_ctrl_c() -> bool:
    """Re-enable Ctrl+C at the Windows console level.

    Restores the original console mode saved by disable_windows_ctrl_c().

    Returns:
        True if successfully re-enabled, False otherwise.
    """
    global _original_ctrl_handler

    if platform.system() != "Windows":
        return False

    if _original_ctrl_handler is None:
        return True  # Nothing to restore

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32

        # Get stdin handle
        STD_INPUT_HANDLE = -10
        stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)

        # Restore original mode
        if kernel32.SetConsoleMode(stdin_handle, _original_ctrl_handler):
            _original_ctrl_handler = None
            return True
        return False

    except Exception:
        return False


# Flag to track if we should keep Ctrl+C disabled
_keep_ctrl_c_disabled: bool = False


def set_keep_ctrl_c_disabled(value: bool) -> None:
    """Set whether Ctrl+C should be kept disabled.

    When True, ensure_ctrl_c_disabled() will re-disable Ctrl+C
    even if something else (like prompt_toolkit) re-enables it.
    """
    global _keep_ctrl_c_disabled
    _keep_ctrl_c_disabled = value


def ensure_ctrl_c_disabled() -> bool:
    """Ensure Ctrl+C is disabled if it should be.

    Call this after operations that might restore console mode
    (like prompt_toolkit input).

    Returns:
        True if Ctrl+C is now disabled (or wasn't needed), False on error.
    """
    if not _keep_ctrl_c_disabled:
        return True

    if platform.system() != "Windows":
        return True

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32

        # Get stdin handle
        STD_INPUT_HANDLE = -10
        stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)

        # Get current console mode
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(stdin_handle, ctypes.byref(mode)):
            return False

        # Console mode flags
        ENABLE_PROCESSED_INPUT = 0x0001

        # Check if Ctrl+C processing is enabled
        if mode.value & ENABLE_PROCESSED_INPUT:
            # Disable it
            new_mode = mode.value & ~ENABLE_PROCESSED_INPUT
            return bool(kernel32.SetConsoleMode(stdin_handle, new_mode))

        return True  # Already disabled

    except Exception:
        return False


def detect_truecolor_support() -> bool:
    """Detect if the terminal supports truecolor (24-bit color).

    Checks multiple indicators:
    1. COLORTERM environment variable (most reliable)
    2. TERM environment variable patterns
    3. Rich's Console color_system detection as fallback

    Returns:
        True if truecolor is supported, False otherwise.
    """
    # Check COLORTERM - this is the most reliable indicator
    colorterm = os.environ.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return True

    # Check TERM for known truecolor-capable terminals
    term = os.environ.get("TERM", "").lower()
    truecolor_terms = (
        "xterm-direct",
        "xterm-truecolor",
        "iterm2",
        "vte-256color",  # Many modern terminals set this
    )
    if any(t in term for t in truecolor_terms):
        return True

    # Some terminals like iTerm2, Kitty, Alacritty set specific env vars
    if os.environ.get("ITERM_SESSION_ID"):
        return True
    if os.environ.get("KITTY_WINDOW_ID"):
        return True
    if os.environ.get("ALACRITTY_SOCKET"):
        return True
    if os.environ.get("WT_SESSION"):  # Windows Terminal
        return True

    # Use Rich's detection as a fallback
    try:
        from rich.console import Console

        console = Console(force_terminal=True)
        color_system = console.color_system
        return color_system == "truecolor"
    except Exception:
        pass

    return False


def print_truecolor_warning(console: Optional["Console"] = None) -> None:
    """Print a big fat red warning if truecolor is not supported.

    Args:
        console: Optional Rich Console instance. If None, creates a new one.
    """
    if detect_truecolor_support():
        return  # All good, no warning needed

    if console is None:
        try:
            from rich.console import Console

            console = Console()
        except ImportError:
            # Rich not available, fall back to plain print
            print("\n" + "=" * 70)
            print("⚠️  WARNING: TERMINAL DOES NOT SUPPORT TRUECOLOR (24-BIT COLOR)")
            print("=" * 70)
            print("Code Puppy looks best with truecolor support.")
            print("Consider using a modern terminal like:")
            print("  • iTerm2 (macOS)")
            print("  • Windows Terminal (Windows)")
            print("  • Kitty, Alacritty, or any modern terminal emulator")
            print("")
            print("You can also try setting: export COLORTERM=truecolor")
            print("")
            print("Note: The built-in macOS Terminal.app does not support truecolor")
            print("(Sequoia and earlier). You'll need a different terminal app.")
            print("=" * 70 + "\n")
            return

    # Get detected color system for diagnostic info
    color_system = console.color_system or "unknown"

    # Build the warning box
    warning_lines = [
        "",
        "[bold bright_red on red]" + "━" * 72 + "[/]",
        "[bold bright_red on red]┃[/][bold bright_white on red]"
        + " " * 70
        + "[/][bold bright_red on red]┃[/]",
        "[bold bright_red on red]┃[/][bold bright_white on red]  ⚠️   WARNING: TERMINAL DOES NOT SUPPORT TRUECOLOR (24-BIT COLOR)  ⚠️   [/][bold bright_red on red]┃[/]",
        "[bold bright_red on red]┃[/][bold bright_white on red]"
        + " " * 70
        + "[/][bold bright_red on red]┃[/]",
        "[bold bright_red on red]" + "━" * 72 + "[/]",
        "",
        f"[yellow]Detected color system:[/] [bold]{color_system}[/]",
        "",
        "[bold white]Code Puppy uses rich colors and will look degraded without truecolor.[/]",
        "",
        "[cyan]Consider using a modern terminal emulator:[/]",
        "  [green]•[/] [bold]iTerm2[/] (macOS) - https://iterm2.com",
        "  [green]•[/] [bold]Windows Terminal[/] (Windows) - Built into Windows 11",
        "  [green]•[/] [bold]Kitty[/] - https://sw.kovidgoyal.net/kitty",
        "  [green]•[/] [bold]Alacritty[/] - https://alacritty.org",
        "  [green]•[/] [bold]Warp[/] (macOS) - https://warp.dev",
        "",
        "[cyan]Or try setting the COLORTERM environment variable:[/]",
        "  [dim]export COLORTERM=truecolor[/]",
        "",
        "[dim italic]Note: The built-in macOS Terminal.app does not support truecolor (Sequoia and earlier).[/]",
        "[dim italic]Setting COLORTERM=truecolor won't help - you'll need a different terminal app.[/]",
        "",
        "[bold bright_red]" + "─" * 72 + "[/]",
        "",
    ]

    for line in warning_lines:
        console.print(line)


# =============================================================================
# Terminal Resize (SIGWINCH) Handling
# =============================================================================


def get_terminal_size() -> Tuple[int, int]:
    """Get current terminal size as (columns, rows).

    Returns:
        Tuple of (columns, rows). Falls back to (80, 24) if detection fails.
    """
    try:
        size = shutil.get_terminal_size()
        return (size.columns, size.lines)
    except Exception:
        return (80, 24)


def _handle_sigwinch(signum: int, frame) -> None:
    """Internal SIGWINCH handler.

    Called when the terminal is resized. Triggers screen refresh to
    prevent display corruption from stale cursor positions.
    """
    global _last_terminal_size, _resize_callback

    new_size = get_terminal_size()
    old_size = _last_terminal_size
    _last_terminal_size = new_size

    # Only refresh if size actually changed significantly
    if old_size is not None:
        old_cols, old_rows = old_size
        new_cols, new_rows = new_size

        # Check for significant change (more than just 1-2 character jitter)
        if abs(new_cols - old_cols) <= 2 and abs(new_rows - old_rows) <= 2:
            return

    # Perform terminal refresh to fix cursor position and screen state
    refresh_terminal_on_resize()

    # Call registered callback if any
    if _resize_callback is not None:
        try:
            _resize_callback()
        except Exception:
            pass  # Don't let callback errors crash the signal handler


def refresh_terminal_on_resize() -> None:
    """Refresh terminal display after a resize event.

    This clears the screen state and repositions the cursor to prevent
    the display corruption that occurs when terminal geometry changes
    but the application's internal state is stale.
    """
    if platform.system() == "Windows":
        # Windows doesn't have SIGWINCH, handled differently
        return

    try:
        # ANSI escape sequences for terminal refresh:
        # \x1b[2J - Clear entire screen
        # \x1b[H  - Move cursor to home position (top-left)
        # \x1b[0m - Reset all attributes
        #
        # Note: We use a softer approach that doesn't lose context:
        # Just reset cursor position tracking and clear below cursor
        #
        # \x1b[0m  - Reset attributes
        # \x1b[J   - Clear from cursor to end of screen
        sys.stdout.write("\x1b[0m\x1b[J")
        sys.stdout.flush()
    except Exception:
        pass  # Best effort - don't crash on I/O errors


def install_sigwinch_handler(callback: Optional[Callable[[], None]] = None) -> bool:
    """Install a SIGWINCH handler for terminal resize events.

    This handler automatically refreshes the terminal display when
    the terminal is resized, preventing display corruption.

    Args:
        callback: Optional callback function to invoke on resize.
                  The callback receives no arguments.

    Returns:
        True if handler was installed successfully, False otherwise.
        Always returns False on Windows (no SIGWINCH support).
    """
    global _resize_handler_installed, _last_terminal_size, _resize_callback
    global _original_sigwinch_handler

    # SIGWINCH doesn't exist on Windows
    if platform.system() == "Windows":
        return False

    # Don't reinstall if already installed
    if _resize_handler_installed:
        # But do update the callback if provided
        if callback is not None:
            _resize_callback = callback
        return True

    try:
        # Check if SIGWINCH exists (it's Unix-only)
        if not hasattr(signal, "SIGWINCH"):
            return False

        # Store initial terminal size
        _last_terminal_size = get_terminal_size()
        _resize_callback = callback

        # Save original handler for cleanup
        _original_sigwinch_handler = signal.getsignal(signal.SIGWINCH)

        # Install our handler
        signal.signal(signal.SIGWINCH, _handle_sigwinch)
        _resize_handler_installed = True

        return True

    except Exception:
        return False


def uninstall_sigwinch_handler() -> bool:
    """Uninstall the SIGWINCH handler and restore original.

    Returns:
        True if handler was uninstalled, False otherwise.
    """
    global _resize_handler_installed, _resize_callback, _original_sigwinch_handler

    if platform.system() == "Windows":
        return False

    if not _resize_handler_installed:
        return True  # Already not installed

    try:
        if hasattr(signal, "SIGWINCH") and _original_sigwinch_handler is not None:
            signal.signal(signal.SIGWINCH, _original_sigwinch_handler)

        _resize_handler_installed = False
        _resize_callback = None
        _original_sigwinch_handler = None

        return True

    except Exception:
        return False


def is_sigwinch_handler_installed() -> bool:
    """Check if the SIGWINCH handler is currently installed.

    Returns:
        True if handler is installed, False otherwise.
    """
    return _resize_handler_installed
