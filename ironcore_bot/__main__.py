"""
Module entry point: allows `python -m ironcore_bot`.
"""
from __future__ import annotations

from .app import run_bot
from .bootstrap import ensure_env_ready


def main() -> None:
    ensure_env_ready()
    run_bot()


if __name__ == "__main__":
    main()
