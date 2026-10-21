import json
import os
from datetime import datetime
from enum import Enum, auto
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field, asdict
from orc.logger import get_logger
from orc.budgets import BudgetGroup
from orc.loop_detector import LoopDetector

logger = get_logger('planner')

ORYN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class StepStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class FailureClass(Enum):
    TRANSIENT = "transient"
    SYSTEMIC = "systemic"
    NON_RETRYABLE = "non_retryable"


class PlanState(Enum):
    IDLE = auto()
    PLANNING = auto()
    EXECUTING = auto()
    EVALUATING = auto()
    REPLANNING = auto()
    DONE = auto()
    FAILED = auto()


REPLAN_GATE_EVERY_K_STEPS = 3
DEFAULT_STEP_BUDGET = 15
DEFAULT_TIME_BUDGET_SEC = 300
DEFAULT_RETRY_BUDGET = 5


@dataclass
class Step:
    id: str
    description: str
    status: StepStatus = StepStatus.PENDING
    depends_on: List[str] = field(default_factory=list)
    agent: str = ""
    result: str = ""
    error: str = ""
    failure_class: Optional[FailureClass] = None
    retry_count: int = 0
    progress_signal: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str = ""

    def to_dict(self):
        d = asdict(self)
        d['status'] = self.status.value
        if self.failure_class:
            d['failure_class'] = self.failure_class.value
        return d

    @classmethod
    def from_dict(cls, data):
        if 'status' in data:
            data['status'] = StepStatus(data['status'])
        if data.get('failure_class'):
            data['failure_class'] = FailureClass(data['failure_class'])
        return cls(**data)


@dataclass
class Plan:
    task: str
    steps: List[Step] = field(default_factory=list)
    status: str = "active"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    log: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            'task': self.task,
            'steps': [s.to_dict() for s in self.steps],
            'status': self.status,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'log': self.log[-50:],
        }

    @classmethod
    def from_dict(cls, data):
        data['steps'] = [Step.from_dict(s) for s in data.get('steps', [])]
        return cls(**data)

    def next_step(self) -> Optional[Step]:
        done_ids = {s.id for s in self.steps if s.status == StepStatus.DONE}
        for step in self.steps:
            if step.status != StepStatus.PENDING:
                continue
            if all(dep in done_ids for dep in step.depends_on):
                return step
        return None

    def all_done(self):
        return all(s.status in (StepStatus.DONE, StepStatus.SKIPPED) for s in self.steps)

    def has_failed(self):
        return any(s.status == StepStatus.FAILED for s in self.steps)

    def update_step(self, step_id, status=StepStatus.DONE, result="", error="", failure_class=None):
        for step in self.steps:
            if step.id == step_id:
                step.status = status
                step.result = result
                step.error = error
                if failure_class:
                    step.failure_class = failure_class
                if status in (StepStatus.DONE, StepStatus.FAILED):
                    step.completed_at = datetime.now().isoformat()
                self.updated_at = datetime.now().isoformat()
                break

    def add_step(self, step):
        self.steps.append(step)
        self.updated_at = datetime.now().isoformat()

    def add_steps_after(self, after_id, new_steps):
        idx = next((i for i, s in enumerate(self.steps) if s.id == after_id), -1)
        if idx >= 0:
            for i, step in enumerate(new_steps):
                self.steps.insert(idx + 1 + i, step)
        else:
            self.steps.extend(new_steps)
        self.updated_at = datetime.now().isoformat()

    def log_event(self, msg):
        ts = datetime.now().strftime('%H:%M:%S')
        self.log.append(f"[{ts}] {msg}")

    def summary(self):
        lines = [f"\033[90mplan: {self.task}\033[0m ({self.status})"]
        for s in self.steps:
            icon = {
                StepStatus.PENDING: "\033[90m[ ]\033[0m",
                StepStatus.IN_PROGRESS: "\033[33m[>]\033[0m",
                StepStatus.DONE: "\033[32m[v]\033[0m",
                StepStatus.FAILED: "\033[31m[x]\033[0m",
                StepStatus.BLOCKED: "\033[31m[!]\033[0m",
                StepStatus.SKIPPED: "\033[90m[-]\033[0m",
            }.get(s.status, "[?]")
            agent_tag = f" \033[90m({s.agent})\033[0m" if s.agent else ""
            retry_tag = f" \033[33m(retry {s.retry_count})\033[0m" if s.retry_count else ""
            lines.append(f"  {icon} {s.id}: {s.description}{agent_tag}{retry_tag}")
        return "\n".join(lines)


def classify_failure(error: str) -> FailureClass:
    err_lower = error.lower()
    transient_keywords = ["timeout", "timed out", "429", "rate limit", "5xx", "500", "502", "503",
                          "connection", "temporary", "try again", "too many requests"]
    non_retryable_keywords = ["unauthorized", "401", "403", "forbidden", "invalid key",
                              "not found", "404", "bad request", "400", "permission",
                              "syntax error", "invalid input", "unknown tool"]

    for kw in non_retryable_keywords:
        if kw in err_lower:
            return FailureClass.NON_RETRYABLE
    for kw in transient_keywords:
        if kw in err_lower:
            return FailureClass.TRANSIENT
    return FailureClass.SYSTEMIC


class Planner:
    def __init__(self, providers):
        self.providers = providers
        self.state = PlanState.IDLE
        self.budgets = BudgetGroup()
        self.budgets.add("steps", DEFAULT_STEP_BUDGET)
        self.budgets.add("retries", DEFAULT_RETRY_BUDGET)
        self.loop_detector = LoopDetector()
        self._steps_since_replan = 0

    def create_plan(self, task):
        self.state = PlanState.PLANNING
        self._steps_since_replan = 0

        from orc.providers import ChatMessage
        system = """You are a task planner. Break the user's task into concrete, executable steps.

Rules:
- Each step must be doable by ONE agent type: coder, searcher, runner, or system
- Steps should have clear dependencies
- Keep steps small and specific (each step < 5 actions)
- First step is usually research/search
- Last step is usually run/verify
- Output ONLY a JSON array of steps, no explanation

Format:
[
  {"id": "1", "description": "what to do", "agent": "coder|searcher|runner|system", "depends_on": []},
  {"id": "2", "description": "next thing", "agent": "coder", "depends_on": ["1"]}
]"""

        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=f"Break this into steps: {task}")
        ]

        try:
            response = self.providers.chat(messages, coding=True)
            raw = response.content.strip()
            start = raw.find('[')
            end = raw.rfind(']') + 1
            if start >= 0 and end > start:
                steps_data = json.loads(raw[start:end])
            else:
                steps_data = json.loads(raw)

            plan = Plan(task=task)
            for s in steps_data:
                step = Step(
                    id=s['id'],
                    description=s['description'],
                    agent=s.get('agent', ''),
                    depends_on=s.get('depends_on', []),
                )
                plan.add_step(step)

            plan.log_event(f"plan created with {len(plan.steps)} steps")
            logger.info(f"Created plan: {task} ({len(plan.steps)} steps)")
            self.state = PlanState.EXECUTING
            return plan

        except Exception as e:
            logger.error(f"Plan creation failed: {e}")
            plan = Plan(task=task)
            plan.add_step(Step(id="1", description=task, agent="coder"))
            plan.log_event(f"fallback plan (creation error: {e})")
            self.state = PlanState.EXECUTING
            return plan

    def should_replan(self, plan, step=None):
        if self.state != PlanState.EXECUTING:
            return False
        if self.budgets.exhausted:
            return False
        self._steps_since_replan += 1
        if self._steps_since_replan >= REPLAN_GATE_EVERY_K_STEPS:
            self._steps_since_replan = 0
            return True
        if step and step.failure_class == FailureClass.TRANSIENT and step.retry_count >= 2:
            return True
        if self.loop_detector.is_looping():
            self.loop_detector.clear()
            return True
        return False

    def replan(self, plan, failed_step, error):
        self.state = PlanState.REPLANNING
        failure_class = classify_failure(error)

        if failure_class == FailureClass.NON_RETRYABLE:
            plan.update_step(failed_step.id, StepStatus.FAILED, error=error, failure_class=failure_class)
            plan.log_event(f"non-retryable failure: {error[:100]}")
            self.state = PlanState.FAILED
            return plan

        if failure_class == FailureClass.TRANSIENT:
            if failed_step.retry_count < 3:
                failed_step.retry_count += 1
                plan.update_step(failed_step.id, StepStatus.PENDING, failure_class=failure_class)
                plan.log_event(f"retry {failed_step.retry_count}/3 (transient): {error[:80]}")
                self.state = PlanState.EXECUTING
                return plan

        # Exhausted retries or systemic → ask AI what to do
        from orc.providers import ChatMessage
        context = f"""Original task: {plan.task}
Failed step: {failed_step.id}: {failed_step.description}
Error: {error}
Failure class: {failure_class.value}
Retries attempted: {failed_step.retry_count}
Plan so far:
{plan.summary()}

Respond with a JSON object:
{{"action": "retry|split|skip|abort", "new_steps": [...], "reason": "why"}}"""

        system = "You are a replanner. A step failed. Decide: retry, split into smaller steps, skip, or abort."
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=context)
        ]

        try:
            response = self.providers.chat(messages, coding=True)
            raw = response.content.strip()
            start = raw.find('{')
            end = raw.rfind('}') + 1
            data = json.loads(raw[start:end]) if start >= 0 else {}
            action = data.get('action', 'retry')
            reason = data.get('reason', '')

            if action == 'split':
                new_steps = []
                for s in data.get('new_steps', []):
                    step = Step(id=s['id'], description=s['description'],
                                agent=s.get('agent', failed_step.agent),
                                depends_on=s.get('depends_on', []))
                    new_steps.append(step)
                plan.add_steps_after(failed_step.id, new_steps)
                plan.update_step(failed_step.id, StepStatus.SKIPPED)
                plan.log_event(f"split step {failed_step.id}: {reason}")
            elif action == 'skip':
                plan.update_step(failed_step.id, StepStatus.SKIPPED)
                plan.log_event(f"skipped step {failed_step.id}: {reason}")
            elif action == 'abort':
                plan.status = "failed"
                plan.log_event(f"aborted: {reason}")
                self.state = PlanState.FAILED
                return plan
            else:
                failed_step.retry_count += 1
                plan.update_step(failed_step.id, StepStatus.PENDING, failure_class=failure_class)
                plan.log_event(f"retrying step {failed_step.id}: {reason}")

            self.state = PlanState.EXECUTING
            self.loop_detector.clear()
            return plan

        except Exception as e:
            logger.error(f"Replan failed: {e}")
            plan.update_step(failed_step.id, StepStatus.FAILED, error=error)
            plan.log_event(f"replan error, step {failed_step.id} marked failed")
            self.state = PlanState.EXECUTING
            return plan
