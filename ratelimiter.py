"""
Token-bucket rate limiter matching Anthropic's algorithm.

Anthropic enforces rate limits via a token bucket: capacity refills continuously
at limit/60 per second up to the maximum. Bursts up to the bucket size are
allowed; sustained rates above limit/60-per-second are throttled.

A sliding-window limiter is too conservative here — it counts burst tokens
against the limit for a full 60s, throttling well below what the API actually
allows. This token-bucket implementation matches the API's behavior, so we
get full throughput without 429s.

Thread-safe for concurrent chunk processing.
"""

from __future__ import annotations
import time
import threading


class RollingRateLimiter:
    """
    Token-bucket rate limiter with separate RPM and TPM buckets.

    Both buckets start full and refill at (limit / 60) per second. A request
    is admitted when BOTH buckets have headroom for it. If a bucket is empty,
    the caller blocks until enough refill has occurred.

    The class name is kept as RollingRateLimiter for backward compatibility
    with existing imports; the algorithm is now token bucket, not sliding
    window.

    Args:
        rpm_limit: Requests-per-minute ceiling per Anthropic's docs.
        tpm_limit: Tokens-per-minute ceiling (input tokens for most models).
        safety_margin: Fraction of the published limit to use as the bucket
                       size (default 0.95). Provides headroom for clock skew
                       between our timing and Anthropic's, and for tokens
                       still in flight that haven't been billed yet.
    """

    def __init__(self, rpm_limit: int = 50, tpm_limit: int = 50_000,
                 safety_margin: float = 0.95):
        self.rpm_limit = rpm_limit * safety_margin
        self.tpm_limit = tpm_limit * safety_margin
        # Refill rate per second matches the ceiling/60 (Anthropic's algorithm).
        self.rpm_refill_per_sec = self.rpm_limit / 60.0
        self.tpm_refill_per_sec = self.tpm_limit / 60.0

        self._lock = threading.Lock()
        self._rpm_bucket = float(self.rpm_limit)   # start full
        self._tpm_bucket = float(self.tpm_limit)   # start full
        self._last_refill = time.monotonic()

    def _refill(self, now: float) -> None:
        """Add tokens to both buckets based on time since last refill."""
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._rpm_bucket = min(
            self.rpm_limit,
            self._rpm_bucket + elapsed * self.rpm_refill_per_sec)
        self._tpm_bucket = min(
            self.tpm_limit,
            self._tpm_bucket + elapsed * self.tpm_refill_per_sec)
        self._last_refill = now

    def wait_if_needed(self, estimated_tokens: int) -> None:
        """
        Block until both buckets have headroom for the request, then deduct.

        Args:
            estimated_tokens: Caller's best estimate of input tokens this
                              request will use. Slight over-estimation is
                              safer than under-estimation (under-estimating
                              risks a 429; over-estimating just adds a tiny
                              delay).
        """
        while True:
            with self._lock:
                now = time.monotonic()
                self._refill(now)

                if self._rpm_bucket >= 1.0 and self._tpm_bucket >= estimated_tokens:
                    self._rpm_bucket -= 1.0
                    self._tpm_bucket -= estimated_tokens
                    return

                # Compute the minimum wait until BOTH buckets have headroom.
                rpm_wait = 0.0
                if self._rpm_bucket < 1.0:
                    rpm_wait = (1.0 - self._rpm_bucket) / self.rpm_refill_per_sec
                tpm_wait = 0.0
                if self._tpm_bucket < estimated_tokens:
                    tpm_wait = (estimated_tokens - self._tpm_bucket) / self.tpm_refill_per_sec
                # Floor at 100ms so we don't busy-spin on tiny waits.
                sleep_s = max(rpm_wait, tpm_wait, 0.1)

            # Sleep outside the lock so other threads can refill check.
            # Cap individual sleeps at 5s so we re-check the bucket regularly.
            time.sleep(min(sleep_s, 5.0))
