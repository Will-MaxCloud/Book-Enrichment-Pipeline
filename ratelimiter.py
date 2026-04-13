"""
Rolling-window rate limiter for Anthropic API calls.
Tracks RPM and TPM in a 60-second window and pre-emptively throttles
before hitting limits — preventing 429 errors rather than recovering from them.
Thread-safe for concurrent chunk processing.
"""

from __future__ import annotations
import time
import threading
from collections import deque


class RollingRateLimiter:
    """
    Sliding 60-second window rate limiter.

    Call wait_if_needed(estimated_tokens) before every API request.
    Blocks until both RPM and TPM have headroom (90% threshold).
    Thread-safe: a lock ensures concurrent callers queue correctly.
    """

    def __init__(self, rpm_limit: int = 50, tpm_limit: int = 50_000):
        self.rpm_limit = rpm_limit
        self.tpm_limit = tpm_limit
        self._window = 60.0
        self._threshold = 0.90          # slow down at 90% of limit
        self._lock = threading.Lock()
        self._requests: deque[float] = deque()           # timestamps
        self._tokens: deque[tuple[float, int]] = deque() # (timestamp, count)

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._requests and self._requests[0] <= cutoff:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] <= cutoff:
            self._tokens.popleft()

    def wait_if_needed(self, estimated_tokens: int) -> None:
        """Block until both RPM and TPM windows have headroom, then reserve a slot."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._prune(now)

                current_rpm = len(self._requests)
                current_tpm = sum(n for _, n in self._tokens)

                rpm_ok = current_rpm < self.rpm_limit * self._threshold
                tpm_ok = (current_tpm + estimated_tokens) < self.tpm_limit * self._threshold

                if rpm_ok and tpm_ok:
                    self._requests.append(now)
                    self._tokens.append((now, estimated_tokens))
                    return

                # Calculate minimum sleep until the oldest entry ages out
                sleep_s = 1.0
                if not rpm_ok and self._requests:
                    sleep_s = max(sleep_s, self._window - (now - self._requests[0]))
                if not tpm_ok and self._tokens:
                    sleep_s = max(sleep_s, self._window - (now - self._tokens[0][0]))

            time.sleep(min(sleep_s, 5.0))
