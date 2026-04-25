from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class RateLimitDecision:
    limited: bool
    count: int
    limit: int
    retry_after_seconds: int


class SlidingWindowRateLimiter:
    def __init__(self):
        self._events = defaultdict(deque)
        self._lock = Lock()

    def _prune(self, queue, *, now, window_seconds):
        cutoff = now - window_seconds
        while queue and queue[0] <= cutoff:
            queue.popleft()

    def peek(self, bucket, key, *, limit, window_seconds, now=None):
        timestamp = now if now is not None else time.time()
        composite_key = f"{bucket}:{key}"
        with self._lock:
            queue = self._events.get(composite_key)
            if queue is None:
                return RateLimitDecision(
                    limited=False,
                    count=0,
                    limit=limit,
                    retry_after_seconds=0,
                )

            self._prune(queue, now=timestamp, window_seconds=window_seconds)
            if not queue:
                self._events.pop(composite_key, None)
                return RateLimitDecision(
                    limited=False,
                    count=0,
                    limit=limit,
                    retry_after_seconds=0,
                )

            retry_after = max(1, int(window_seconds - (timestamp - queue[0])))
            return RateLimitDecision(
                limited=len(queue) >= limit,
                count=len(queue),
                limit=limit,
                retry_after_seconds=retry_after if len(queue) >= limit else 0,
            )

    def record(self, bucket, key, *, limit, window_seconds, now=None):
        timestamp = now if now is not None else time.time()
        composite_key = f"{bucket}:{key}"
        with self._lock:
            queue = self._events[composite_key]
            self._prune(queue, now=timestamp, window_seconds=window_seconds)
            queue.append(timestamp)
            retry_after = max(1, int(window_seconds - (timestamp - queue[0])))
            return RateLimitDecision(
                limited=len(queue) >= limit,
                count=len(queue),
                limit=limit,
                retry_after_seconds=retry_after if len(queue) >= limit else 0,
            )

    def clear(self, bucket, key):
        composite_key = f"{bucket}:{key}"
        with self._lock:
            self._events.pop(composite_key, None)

    def reset_all(self):
        with self._lock:
            self._events.clear()


rate_limiter = SlidingWindowRateLimiter()
