"""
Runtime orchestration for the Ironcore overlay bot.
"""
from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path
from typing import Optional, Callable

import psutil

PROCESS_NAME = "ironcore.exe"


LOCK_FILE = Path(__file__).resolve().parent.parent / ".ironcore.lock"
_MUTEX_HANDLE: Optional[int] = None


def _ensure_single_instance() -> None:
    """
    If previous bot is recorded and still running, terminate it, then write our pid.
    Lock file is removed on clean exit.
    """
    current_pid = os.getpid()
    try:
        if LOCK_FILE.exists():
            pid_text = LOCK_FILE.read_text(encoding="utf-8").strip()
            if pid_text.isdigit():
                old_pid = int(pid_text)
                if old_pid != current_pid:
                    try:
                        proc = psutil.Process(old_pid)
                        cmd = " ".join(proc.cmdline()).lower()
                        if "ironcore_bot" in cmd or "main.py" in cmd:
                            proc.terminate()
                            proc.wait(timeout=2)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                        pass
    except Exception:
        pass
    try:
        LOCK_FILE.write_text(str(current_pid), encoding="utf-8")
    except Exception:
        pass

    def _cleanup_lock() -> None:
        try:
            if LOCK_FILE.exists():
                LOCK_FILE.unlink()
        except Exception:
            pass

    atexit.register(_cleanup_lock)


def _acquire_process_mutex() -> bool:
    """
    Use a named OS mutex to block concurrent instances. Returns True if acquired.
    """
    global _MUTEX_HANDLE
    try:
        import win32api
        import win32con
        import win32event

        handle = win32event.CreateMutex(None, False, "Global\\IroncoreBotMutex")
        # If already exists, GetLastError will be ERROR_ALREADY_EXISTS
        if win32api.GetLastError() == win32con.ERROR_ALREADY_EXISTS:
            win32api.CloseHandle(handle)
            return False
        _MUTEX_HANDLE = handle
        # Cleanup on exit
        def _release() -> None:
            try:
                if _MUTEX_HANDLE:
                    win32api.CloseHandle(_MUTEX_HANDLE)
            except Exception:
                pass
        atexit.register(_release)
        return True
    except Exception:
        # If mutex fails, do not block startup; fallback to lock-file logic only.
        return True


def run_bot(process_name: str = PROCESS_NAME) -> None:
    """
    Start the bot: find the game window, create overlay, trackers and watchers.
    Heavy imports live inside to avoid triggering them before the environment is ready.
    """
    import win32gui

    from ironcore_bot.capture import capture_full_window
    from ironcore_bot.client_window import find_window_for_process, list_windows_for_process
    from ironcore_bot.custom_actions_runner import CustomActionsRunner
    from ironcore_bot.exp_tracker import ExpTracker
    from ironcore_bot.overlay import TransparentOverlay
    from ironcore_bot.skills import SkillsWatcher

    print("[ironcore] start run_bot", flush=True)
    if not _acquire_process_mutex():
        print("[ironcore] Druga instancja wykryta (mutex) - wychodze.", flush=True)
        sys.exit(0)
    _ensure_single_instance()
    print("[ironcore] after single-instance check", flush=True)

    window = find_window_for_process(process_name)
    if not window:
        print(f"[ironcore] Nie znaleziono procesu {process_name}. Uruchom gre i sprobuj ponownie.")
        sys.exit(1)

    # window info intentionally muted

    overlay = TransparentOverlay(window=window, panels=[])
    tracker = ExpTracker()
    actions_runner = CustomActionsRunner(on_update=overlay.set_actions_status, active_window=lambda: overlay.window.hwnd)
    actions_runner.start()
    watcher = SkillsWatcher(window=window, overlay=overlay, interval=1.0, tracker=tracker, actions_runner=actions_runner)
    watcher.start()

    try:
        image = capture_full_window(window.hwnd)
        image.save("full_client_capture.png")
        print(
            f"Zapisano pelny zrzut okna klienta do full_client_capture.png "
            f"(rozmiar {image.size[0]}x{image.size[1]})."
        )
    except Exception as exc:
        print(f"Nie udalo sie wykonac zrzutu probnego: {exc}")

    def reset_stats() -> None:
        exp_text = watcher.last_experience
        try:
            exp_val = int(str(exp_text).replace(",", "").replace(".", "")) if exp_text else None
        except ValueError:
            exp_val = None
        tracker.reset(exp_val)
        watcher._update_status()
        print("Zresetowano statystyki exp.")

    def open_custom_actions() -> None:
        overlay.open_custom_modal()

    def open_options() -> None:
        windows = list_windows_for_process(process_name)
        overlay.start_options(windows, current_hwnd=window.hwnd)
        win32gui.InvalidateRect(overlay._hwnd, None, True)

    def apply_options(
        selected_hwnd: Optional[int],
        pane_sizes: dict,
        selected_melee: Optional[str] = None,
        selected_shield_mode: Optional[int] = None,
    ) -> None:
        nonlocal window
        if pane_sizes:
            overlay.apply_pane_sizes(pane_sizes)
        if selected_melee:
            overlay.selected_melee = selected_melee
            try:
                overlay._save_positions()
            except Exception:
                pass
        if selected_shield_mode in (1, 2):
            overlay.selected_shield_mode = selected_shield_mode
            try:
                overlay._save_positions()
            except Exception:
                pass
        if selected_hwnd and window and selected_hwnd != window.hwnd:
            target = next((w for w in overlay.available_windows if w.hwnd == selected_hwnd), None)
            if target is None:
                refreshed = list_windows_for_process(process_name)
                target = next((w for w in refreshed if w.hwnd == selected_hwnd), None)
            if target:
                window = target
                overlay.update_window(target)
                watcher.update_window(target)
                print(f"Przelaczono na okno hwnd={target.hwnd}, pid={target.process_id}")
            else:
                print("Wybrane okno nie jest juz dostepne.")

    def layout_controls_buttons() -> None:
        pane_w = overlay.controls_pane[2]
        margin = 8
        spacing = 8
        row_h = 24
        min_button_w = 60
        buttons = [
            ("options", 90, open_options),
            ("custom", 100, open_custom_actions),
        ]
        positions: list[tuple[str, tuple[int, int, int, int], Callable[[], None]]] = []
        x = margin
        # start lower to avoid titlebar drag area
        y = 32

        for key, width_hint, handler in buttons:
            width = max(width_hint, min_button_w)
            available = max(min_button_w, pane_w - 2 * margin)
            if available < width or pane_w < width + 2 * margin:
                rect = (margin, y, available, row_h)
                y += row_h + spacing
                x = margin
            else:
                if x + width + margin > pane_w:
                    y += row_h + spacing
                    x = margin
                rect = (x, y, width, row_h)
                x += width + spacing
            positions.append((key, rect, handler))

        # Ensure panel height fits buttons
        if positions:
            last_rect = positions[-1][1]
            needed_height = last_rect[1] + last_rect[3] + margin
            cx, cy, cw, ch = overlay.controls_pane
            if ch < needed_height:
                overlay.controls_pane = (cx, cy, cw, needed_height)

        for key, rect, handler in positions:
            if key == "custom":
                overlay.set_custom_button(rect, on_click=handler)
            elif key == "options":
                overlay.set_options_button(rect, on_click=handler)

    overlay.set_custom_button((0, 0, 0, 0), on_click=open_custom_actions)
    overlay.on_save_custom = actions_runner.reload
    overlay.on_close_custom = actions_runner.reload
    overlay.on_close = lambda: (
        actions_runner.stop(),
        watcher.stop(),
        print("Overlay zamkniety."),
    )

    def test_afk_sound() -> None:
        try:
            import winsound

            duration = 300
            freq = 750
            winsound.Beep(freq, duration)
        except Exception:
            pass

    overlay.set_options_button((0, 0, 0, 0), on_click=open_options)
    overlay.on_apply_options = apply_options
    overlay.on_panes_changed = lambda sizes: layout_controls_buttons()
    overlay.on_test_afk_sound = test_afk_sound

    overlay.set_status_reset_button(label="Reset", on_click=reset_stats)
    # initial layout of buttons
    layout_controls_buttons()

    overlay.show()
