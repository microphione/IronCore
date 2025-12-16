"""
Bootstrap helpers:
- ensure the virtual environment exists and dependencies are installed
- re-executes the current command inside the venv interpreter once
"""
from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

from setup_env import main as setup_env_main, venv_python

BOOTSTRAP_ENV_VAR = "IRONCORE_BOOTSTRAPPED"


def _current_invocation() -> list[str]:
    """Return the current invocation so we can replay it after switching interpreters."""
    script = Path(sys.argv[0]).resolve()
    if script.is_file():
        return [str(script), *sys.argv[1:]]
    return ["-m", "ironcore_bot", *sys.argv[1:]]


def ensure_env_ready() -> None:
    """
    Ensure venv + deps exist, then re-run the command in the venv interpreter (only once).
    """
    target_python = venv_python().resolve()
    try:
        current_python = Path(sys.executable).resolve()
    except FileNotFoundError:
        current_python = Path(sys.executable)

    if os.environ.get(BOOTSTRAP_ENV_VAR) == "1" or current_python == target_python:
        return

    print("Przygotowanie srodowiska (venv + dependencies)...")
    setup_env_main()
    print("Ponowne uruchomienie bota w venv...")

    env = os.environ.copy()
    env[BOOTSTRAP_ENV_VAR] = "1"
    cmd = [str(target_python), *_current_invocation()]
    result = subprocess.call(cmd, env=env)
    sys.exit(result)
