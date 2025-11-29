"""
Entry point for the Ironcore overlay bot.
Bootstraps the environment and delegates to the runtime app.
"""
from __future__ import annotations

from ironcore_bot.app import run_bot
from ironcore_bot.bootstrap import ensure_env_ready


def main() -> None:
    ensure_env_ready()
    run_bot()


if __name__ == "__main__":
    main()
