import time
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Trips when failure rate exceeds threshold within a window.

    After cooldown, transitions to half-open: one probe request.
    If probe succeeds → CLOSED. If probe fails → OPEN again.
    """

    def __init__(self, name, failure_threshold=0.5, window=10, cooldown=30):
        self.name = name
        self.failure_threshold = failure_threshold
        self.window = window
        self.cooldown = cooldown
        self.state = CircuitState.CLOSED
        self._results = []
        self._tripped_at = 0.0

    def record(self, ok):
        now = time.time()
        self._results.append((now, ok))
        cutoff = now - self.window
        self._results = [(t, r) for t, r in self._results if t > cutoff]

        if self.state == CircuitState.HALF_OPEN:
            if ok:
                self.state = CircuitState.CLOSED
                self._results = []
            else:
                self.state = CircuitState.OPEN
                self._tripped_at = now
            return

        if len(self._results) < 5:
            return

        failures = sum(1 for _, r in self._results if not r)
        rate = failures / len(self._results)
        if rate >= self.failure_threshold and self.state == CircuitState.CLOSED:
            self.state = CircuitState.OPEN
            self._tripped_at = now

    def allow_request(self):
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self._tripped_at >= self.cooldown:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True  # half-open: allow probe
