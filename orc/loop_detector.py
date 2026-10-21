import hashlib
from collections import deque


class LoopDetector:
    """Hashes (action, args) pairs and flags repeats.

    Rolling window of last N entries.
    Same hash appearing >= repeat_threshold times triggers detection.
    """

    def __init__(self, window=8, repeat_threshold=3):
        self.window = window
        self.repeat_threshold = repeat_threshold
        self._history = deque(maxlen=window)

    def record(self, action, args=""):
        raw = f"{action}|{args}"
        h = hashlib.md5(raw.encode()).hexdigest()
        self._history.append(h)

    def is_looping(self):
        if len(self._history) < self.repeat_threshold:
            return False
        counts = {}
        for h in self._history:
            counts[h] = counts.get(h, 0) + 1
        return max(counts.values()) >= self.repeat_threshold

    def clear(self):
        self._history.clear()
