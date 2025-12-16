from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Panel:
    x: int
    y: int
    width: int
    height: int
    color: tuple[int, int, int] = (0, 200, 255)
    outline_only: bool = True
    thickness: int = 2

    def rect(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.width, self.y + self.height
