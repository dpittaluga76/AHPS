"""Throttled terminal progress, separate from AHPS codec work.

Percentages represent completed workflow stages. Opaque codec calls keep their
current percentage while the spinner remains active. No time-based percentage
is invented, and only a successful context exit displays 100 percent.
"""

import math
import sys
import threading
import time
from typing import Optional, TextIO


class TerminalProgress:
    """Render at most four periodic updates per second on stderr by default.

    Disabled instances start no thread and perform no writes. ``update`` only
    records a small state snapshot; terminal writes happen on the renderer
    thread, apart from the initial and final frames outside codec execution.
    """

    def __init__(self, label: str, enabled: Optional[bool] = None,
                 stream: Optional[TextIO] = None, interval: float = 0.25):
        self.stream = sys.stderr if stream is None else stream
        if enabled is None:
            try:
                enabled = self.stream.isatty()
            except (AttributeError, OSError, ValueError):
                enabled = False
        self.enabled = bool(enabled)
        interval = float(interval)
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("Progress interval must be positive and finite.")
        self.interval = max(0.25, interval)
        self.label = self._clean(label)[:12]
        self._fraction = 0.0
        self._stage = "Starting"
        self._failure_stage = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._started = None
        self._entered = False
        self._closed = False
        self._io_failed = False
        self._frame = 0
        self._width = 0

    @staticmethod
    def _clean(value: str) -> str:
        return "".join(char if char.isprintable() else " " for char in str(value))

    def __enter__(self):
        if self._entered:
            raise RuntimeError("A progress context cannot be reused.")
        self._entered = True
        if self.enabled:
            self._started = time.monotonic()
            self._render()
            if not self._io_failed:
                self._thread = threading.Thread(target=self._run, name="ahps-progress", daemon=True)
                self._thread.start()
        return self

    def update(self, fraction: float, stage: str) -> None:
        """Record monotonic completion, reserving 100 percent for success."""
        if not self.enabled or self._closed or self._io_failed:
            return
        fraction = float(fraction)
        if not math.isfinite(fraction):
            return
        stage = self._clean(stage)[:26]
        with self._lock:
            self._fraction = max(self._fraction, min(0.999, max(0.0, fraction)))
            self._stage = stage

    def mark_failed(self, stage: str = "Completed with errors") -> None:
        """Keep a failed outcome visible when callers handle errors themselves."""
        if not self.enabled or self._closed:
            return
        with self._lock:
            self._failure_stage = self._clean(stage)[:26]

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self._render()

    def write_line(self, text: str, *, file: Optional[TextIO] = None) -> None:
        """Print a status line without interleaving it with the active bar.

        The next scheduled tick redraws the bar. Status messages therefore do
        not add extra progress frames or reset the renderer's throttle.
        """
        destination = sys.stdout if file is None else file
        if not self.enabled:
            print(text, file=destination, flush=True)
            return
        with self._write_lock:
            if self._entered and not self._closed and not self._io_failed:
                self._write("\r" + " " * self._width + "\r")
            print(text, file=destination, flush=True)

    def _render(self, final: Optional[bool] = None) -> None:
        if self._io_failed:
            return
        with self._lock:
            fraction, stage = self._fraction, self._stage
            failure_stage = self._failure_stage
        if final is True:
            fraction, stage, marker = 1.0, "Done", "+"
        elif final is False:
            stage = failure_stage if failure_stage is not None else ("Failed: " + stage)[:26]
            marker = "!"
        else:
            marker = "|/-\\"[self._frame % 4]
            self._frame += 1
        percent = int(fraction * 100)
        filled = int(fraction * 18)
        bar = "#" * filled + "-" * (18 - filled)
        elapsed = min(999999.9, max(0.0, time.monotonic() - self._started))
        line = f"{self.label:<12} [{bar}] {percent:3d}% {marker} {stage:<26} {elapsed:6.1f}s"
        self._width = max(self._width, len(line))
        with self._write_lock:
            self._write("\r" + line.ljust(self._width) + ("\n" if final is not None else ""))

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except (OSError, ValueError, UnicodeError):
            # A detached/closed terminal must not fail a successful codec call.
            self._io_failed = True
            self._stop.set()

    def __exit__(self, exc_type, exc_value, traceback):
        self._closed = True
        if self.enabled:
            self._stop.set()
            if self._thread is not None:
                self._thread.join()
            self._render(final=exc_type is None and self._failure_stage is None)
        return False
