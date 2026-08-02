"""Progress reporting and cancellation, shared by the CLI and the GUI.

The pipeline talks to a Reporter instead of calling print() directly, so the
desktop app can drive a progress bar and a live log from the same code path the
CLI uses. ConsoleReporter reproduces the original CLI output exactly.
"""

from __future__ import annotations


class Cancelled(Exception):
    """Raised inside the pipeline when the caller asked it to stop."""


# key, human label, share of the overall progress bar
STAGES: tuple[tuple[str, str, float], ...] = (
    ("script", "Writing script", 0.05),
    ("voice", "Recording narration", 0.20),
    ("captions", "Aligning captions", 0.10),
    ("visuals", "Generating visuals", 0.30),
    ("motion", "Rendering clips", 0.20),
    ("audio", "Mixing audio", 0.03),
    ("assemble", "Assembling video", 0.09),
    ("thumbnail", "Drawing thumbnail", 0.03),
)

_ORDER = {key: i for i, (key, _, _) in enumerate(STAGES)}
_LABELS = {key: label for key, label, _ in STAGES}


def stage_label(key: str) -> str:
    return _LABELS.get(key, key)


def overall_fraction(stage_key: str, within: float = 0.0) -> float:
    """Overall 0..1 completion, given the active stage and its own progress."""
    done = 0.0
    for key, _, weight in STAGES:
        if key == stage_key:
            return min(1.0, done + weight * max(0.0, min(1.0, within)))
        done += weight
    return min(1.0, done)


class Reporter:
    """Base reporter. Silent by default; subclasses render the events."""

    def __init__(self) -> None:
        self._cancelled = False
        self.stage_key: str | None = None

    # -- cancellation ------------------------------------------------------
    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise Cancelled()

    # -- events ------------------------------------------------------------
    def begin(self, stage_key: str, detail: str = "") -> None:
        """A pipeline stage started."""
        self.raise_if_cancelled()
        self.stage_key = stage_key
        self.on_stage(stage_key, detail)

    def substep(self, done: int, total: int, detail: str = "") -> None:
        """Progress within the current stage (e.g. scene 3 of 27)."""
        self.raise_if_cancelled()
        fraction = (done / total) if total else 0.0
        self.on_progress(self.stage_key or "", fraction, detail)

    def log(self, message: str) -> None:
        self.on_log(message)

    # -- hooks -------------------------------------------------------------
    def on_stage(self, stage_key: str, detail: str) -> None:  # pragma: no cover
        pass

    def on_progress(self, stage_key: str, fraction: float, detail: str) -> None:
        pass

    def on_log(self, message: str) -> None:
        pass


class ConsoleReporter(Reporter):
    """Prints the same output the CLI has always produced."""

    def on_stage(self, stage_key: str, detail: str) -> None:
        suffix = f" {detail}" if detail else ""
        print(f"→ {stage_label(stage_key).lower()}{suffix}…")

    def on_log(self, message: str) -> None:
        print(f"  {message}")
