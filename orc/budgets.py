import time


class Budget:
    """Tracks a consumable resource with a hard limit."""

    def __init__(self, name, limit):
        self.name = name
        self.limit = limit
        self.used = 0

    @property
    def remaining(self):
        return self.limit - self.used

    @property
    def exhausted(self):
        return self.used >= self.limit

    def consume(self, amount=1):
        self.used += amount
        return not self.exhausted


class BudgetGroup:
    """Group of budgets that all must have remaining capacity."""

    def __init__(self):
        self._budgets = {}

    def add(self, name, limit):
        self._budgets[name] = Budget(name, limit)
        return self._budgets[name]

    def get(self, name):
        return self._budgets.get(name)

    @property
    def exhausted(self):
        return any(b.exhausted for b in self._budgets.values())

    def __getitem__(self, name):
        return self._budgets[name]

    def __contains__(self, name):
        return name in self._budgets

    def summary(self):
        parts = []
        for b in self._budgets.values():
            parts.append(f"{b.name}: {b.used}/{b.limit}")
        return ", ".join(parts)
