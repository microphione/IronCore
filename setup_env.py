"""
Utworzenie wirtualnego środowiska i instalacja zależności z requirements.txt jednym poleceniem:
    python setup_env.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parent
VENV_DIR = ROOT / ".venv"
REQ_FILE = ROOT / "requirements.txt"


def venv_python() -> Path:
    """
    Ścieżka do interpretera Pythona wewnątrz venv (Windows/Unix).
    """
    if sys.platform.startswith("win"):
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def ensure_venv() -> None:
    if VENV_DIR.exists():
        print(f"Venv już istnieje: {VENV_DIR}")
        return
    print(f"Tworzenie venv w {VENV_DIR}...")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])


def install_requirements(python_bin: Path) -> None:
    if not REQ_FILE.exists():
        raise FileNotFoundError(f"Nie znaleziono {REQ_FILE}")
    print("Instalacja zależności z requirements.txt...")
    subprocess.check_call([str(python_bin), "-m", "pip", "install", "-r", str(REQ_FILE)])


def main() -> None:
    ensure_venv()
    python_bin = venv_python()
    if not python_bin.exists():
        raise RuntimeError(f"Nie odnaleziono interpretera venv: {python_bin}")
    install_requirements(python_bin)
    print(f"Gotowe. Aktywuj venv: \"{python_bin}\" lub standardowo przez Scripts/activate.")


if __name__ == "__main__":
    main()
