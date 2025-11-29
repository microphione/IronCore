"""
Entry point for the Ironcore overlay bot.
 - Locates the Ironcore client window
 - Creates a transparent, click-through overlay matched to the window size
 - Highlights the first capture zone and grabs a sample screenshot
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from setup_env import VENV_DIR, main as setup_env_main, venv_python

PROCESS_NAME = "ironcore.exe"
BOOTSTRAP_ENV_VAR = "IRONCORE_BOOTSTRAPPED"


def ensure_env_and_reexec() -> None:
    """
    Uruchom setup_env, a następnie ponownie startuj main.py z interpretera venv.
    """
    target_python = venv_python().resolve()
    current_python = Path(sys.executable).resolve()
    if current_python == target_python:
        return

    print("Przygotowanie środowiska (venv + dependencies)...")
    setup_env_main()
    print("Ponowne uruchomienie bota w venv...")

    env = os.environ.copy()
    env[BOOTSTRAP_ENV_VAR] = "1"
    cmd = [str(target_python), str(Path(__file__).resolve()), *sys.argv[1:]]
    result = subprocess.call(cmd, env=env)
    sys.exit(result)


def run_bot() -> None:
    """
    Importujemy cięższe zależności dopiero po upewnieniu się, że środowisko jest gotowe.
    """
    import win32gui
    from ironcore_bot.client_window import find_window_for_process
    from ironcore_bot.overlay import TransparentOverlay
    from ironcore_bot.capture import capture_full_window
    from ironcore_bot.skills import SkillsWatcher
    from ironcore_bot.exp_tracker import ExpTracker
    from ironcore_bot.custom_actions_runner import CustomActionsRunner

    window = find_window_for_process(PROCESS_NAME)
    if not window:
        print(f"Nie znaleziono procesu {PROCESS_NAME}. Uruchom grę i spróbuj ponownie.")
        sys.exit(1)

    print(
        f"Znaleziono okno: hwnd={window.hwnd}, pid={window.process_id}, "
        f"rozmiar={window.width}x{window.height}"
    )

    # Nakładka jest przezroczysta na całej powierzchni okna gry.
    overlay = TransparentOverlay(window=window, panels=[])
    tracker = ExpTracker()
    actions_runner = CustomActionsRunner(on_update=overlay.set_actions_status)
    actions_runner.start()
    watcher = SkillsWatcher(window=window, overlay=overlay, interval=1.0, tracker=tracker, actions_runner=actions_runner)
    watcher.start()

    # Wstępny zrzut całego okna gry.
    try:
        image = capture_full_window(window.hwnd)
        image.save("full_client_capture.png")
        print(
            f"Zapisano pełny zrzut okna klienta do full_client_capture.png "
            f"(rozmiar {image.size[0]}x{image.size[1]})."
        )
    except Exception as exc:
        print(f"Nie udało się wykonać zrzutu próbnego: {exc}")

    # Przycisk reset i custom w panelu controls (względem jego lewego-górnego rogu)
    btn_w, btn_h = 52, 20
    btn_x = 8
    btn_y = 30

    def reset_stats() -> None:
        exp_text = watcher.last_experience
        try:
            exp_val = int(str(exp_text).replace(",", "").replace(".", "")) if exp_text else None
        except ValueError:
            exp_val = None
        tracker.reset(exp_val)
        watcher._update_status()
        print("Zresetowano statystyki exp.")

    overlay.set_button((btn_x, btn_y, btn_w, btn_h), label="Reset", on_click=reset_stats)
    # Przycisk Custom actions obok reset
    ca_w, ca_h = 96, 20
    ca_x = btn_x + btn_w + 8
    ca_y = btn_y

    def open_custom_actions() -> None:
        overlay.custom_modal_visible = True
        win32gui.InvalidateRect(overlay._hwnd, None, True)

    overlay.set_custom_button((ca_x, ca_y, ca_w, ca_h), on_click=open_custom_actions)
    overlay.on_save_custom = actions_runner.reload
    overlay.on_close_custom = actions_runner.reload
    overlay.on_close = lambda: (
        actions_runner.stop(),
        watcher.stop(),
        print("Overlay zamknięty."),
    )

    overlay.show()


def main() -> None:
    if os.environ.get(BOOTSTRAP_ENV_VAR) != "1":
        ensure_env_and_reexec()
    run_bot()


if __name__ == "__main__":
    main()
