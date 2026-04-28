from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProgressReporter:
    enabled: bool = True

    def step(self, current: int, total: int, message: str) -> None:
        if not self.enabled:
            return
        print(f"[{current}/{total}] {message}", flush=True)

    def item(self, category: str, current: int, total: int, detail: str = "") -> None:
        if not self.enabled:
            return
        suffix = f": {detail}" if detail else ""
        print(f"    {category} {current}/{total}{suffix}", flush=True)
