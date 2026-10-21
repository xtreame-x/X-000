import json
import os
import time
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from orc.planner import Plan, Step, StepStatus, Planner, PlanState, classify_failure, FailureClass
from orc.loop_detector import LoopDetector
from orc.budgets import BudgetGroup
from orc.logger import get_logger

logger = get_logger('orchestrator')

ORYN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
STATE_FILE = os.path.join(ORYN_ROOT, 'data', 'orchestrator_state.json')

DEFAULT_BUDGET = {
    'steps': 15,
    'retries': 5,
    'tokens': 100000,
    'wall_time_sec': 300,
}


@dataclass
class CodeMap:
    files: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def add(self, path, desc, lines=0, deps=None):
        self.files[path] = {'desc': desc, 'lines': lines, 'deps': deps or []}

    def remove(self, path):
        self.files.pop(path, None)

    def update(self, path, desc=None, lines=None):
        if path in self.files:
            if desc:
                self.files[path]['desc'] = desc
            if lines:
                self.files[path]['lines'] = lines

    def get(self, path):
        return self.files.get(path)

    def summary(self):
        if not self.files:
            return "codemap: empty"
        lines = ["\033[90mcodemap:\033[0m"]
        for path, info in sorted(self.files.items()):
            lines.append(f"  {path} — {info['desc']} ({info['lines']} lines)")
        return "\n".join(lines)

    def to_context(self):
        if not self.files:
            return "No files created yet."
        lines = []
        for path, info in sorted(self.files.items()):
            size_hint = f" ({info['lines']} lines)" if info.get('lines') else ""
            lines.append(f"{path}: {info['desc']}{size_hint}")
        return "\n".join(lines)

    def to_dict(self):
        return self.files

    @classmethod
    def from_dict(cls, data):
        return cls(files=data or {})


@dataclass
class OrchestratorState:
    plan: Optional[Plan] = None
    codemap: CodeMap = field(default_factory=CodeMap)
    history: list = field(default_factory=list)
    state: str = "idle"

    def save(self, path=STATE_FILE):
        data = {
            'plan': self.plan.to_dict() if self.plan else None,
            'codemap': self.codemap.to_dict(),
            'history': self.history[-50:],
            'state': self.state,
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path=STATE_FILE):
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            state = cls()
            if data.get('plan'):
                state.plan = Plan.from_dict(data['plan'])
            state.codemap = CodeMap.from_dict(data.get('codemap', {}))
            state.history = data.get('history', [])
            state.state = data.get('state', 'idle')
            return state
        except Exception:
            return cls()


class Orchestrator:
    """Hierarchical state machine: IDLE → PLANNING → EXECUTING → EVALUATING → (DONE|FAILED).

    Checkpoints after every step. Loop/stall detection on actions.
    Re-plan gate fires every K steps or when confidence drops.
    """

    def __init__(self, providers, config=None):
        self.providers = providers
        self.config = config or {}
        self.planner = Planner(providers)
        self.state = OrchestratorState.load()
        self.budgets = BudgetGroup()
        self.budgets.add("steps", DEFAULT_BUDGET['steps'])
        self.budgets.add("retries", DEFAULT_BUDGET['retries'])
        self._loop_detector = LoopDetector()
        self._start_time = time.time()
        self._agents = {}

    def _get_agent(self, name):
        if name not in self._agents:
            if name == 'coder':
                from orc.agents.coder import CoderAgent
                self._agents[name] = CoderAgent(self.providers, self)
            elif name == 'searcher':
                from orc.agents.searcher import SearcherAgent
                self._agents[name] = SearcherAgent(self.providers, self)
            elif name == 'runner':
                from orc.agents.runner import RunnerAgent
                self._agents[name] = RunnerAgent(self.providers, self)
            elif name == 'system':
                from orc.agents.system import SystemAgent
                self._agents[name] = SystemAgent(self.providers, self)
            else:
                raise ValueError(f"unknown agent: {name}")
        return self._agents[name]

    def _wall_time_exceeded(self):
        return time.time() - self._start_time > DEFAULT_BUDGET['wall_time_sec']

    def start(self, task):
        self.state = OrchestratorState()
        self.state.state = "planning"
        self._start_time = time.time()
        self._loop_detector.clear()
        self.budgets = BudgetGroup()
        self.budgets.add("steps", DEFAULT_BUDGET['steps'])
        self.budgets.add("retries", DEFAULT_BUDGET['retries'])

        self.state.plan = self.planner.create_plan(task)
        self.state.history = []

        self.state.state = "executing"
        self.state.save()
        return self.state.plan.summary()

    def step(self):
        if self.state.state == "failed":
            return "plan already failed."
        if self.state.state == "done":
            return "plan already completed."
        if self._wall_time_exceeded():
            self.state.state = "failed"
            self.state.save()
            return "wall time budget exceeded."

        plan = self.state.plan
        if not plan:
            return "no active plan. use 'orch <task>' to start."

        if plan.all_done():
            plan.status = "completed"
            self.state.state = "done"
            self.state.save()
            return "plan completed."

        if not self.budgets['steps'].consume():
            self.state.state = "failed"
            self.state.save()
            return "step budget exhausted."

        if plan.has_failed():
            failed = next((s for s in plan.steps if s.status == StepStatus.FAILED), None)
            if failed:
                plan = self.planner.replan(plan, failed, failed.error)
                self.state.plan = plan
                if self.state.state == "failed":
                    self.state.save()
                    return "plan failed, cannot recover."
                self.state.save()
                return f"replanned after {failed.id}: {failed.error[:100]}"

        step = plan.next_step()
        if not step:
            pending = [s for s in plan.steps if s.status == StepStatus.PENDING]
            if not pending:
                plan.status = "completed"
                self.state.state = "done"
                self.state.save()
                return "all steps completed."
            return f"waiting on {len(pending)} pending step(s)."

        plan.update_step(step.id, StepStatus.IN_PROGRESS)
        plan.log_event(f"starting step {step.id}: {step.description}")
        self.state.save()

        try:
            agent = self._get_agent(step.agent)
            context = self._build_context(step)
            result = agent.execute(step.description, context)

            self._loop_detector.record(step.agent, step.description[:80])
            if self._loop_detector.is_looping():
                self._loop_detector.clear()
                plan.log_event(f"loop detected on step {step.id}, forcing strategy switch")
                result += "\n[LOOP_DETECTED] strategy switched"

            plan.update_step(step.id, StepStatus.DONE, result=result)
            plan.log_event(f"step {step.id} done")
            self._update_codemap(step, result)

            self.state.history.append({
                'step': step.id,
                'agent': step.agent,
                'description': step.description,
                'result': result[:500],
            })

            # Re-plan gate
            if self.planner.should_replan(plan, step):
                plan.log_event("re-plan gate triggered")
                self.state.state = "evaluating"
                self.state.save()

            self.state.save()
            return f"step {step.id} done: {result[:200]}"

        except Exception as e:
            failure_class = classify_failure(str(e))
            plan.update_step(step.id, StepStatus.FAILED, error=str(e), failure_class=failure_class)
            plan.log_event(f"step {step.id} failed ({failure_class.value}): {e}")
            self.state.save()
            return f"step {step.id} failed: {e}"

    def run_all(self, callback=None):
        results = []
        for _ in range(DEFAULT_BUDGET['steps']):
            if self.state.state in ("done", "failed"):
                break
            result = self.step()
            results.append(result)
            if callback:
                callback(result)
            if "completed" in result or "cannot recover" in result:
                break
        return "\n".join(results)

    def _build_context(self, step):
        context = {
            'task': self.state.plan.task if self.state.plan else "",
            'codemap': self.state.codemap.to_context(),
            'step': step.description,
            'step_id': step.id,
        }
        if step.depends_on:
            dep_results = []
            for dep_id in step.depends_on:
                dep_step = next((s for s in self.state.plan.steps if s.id == dep_id), None)
                if dep_step and dep_step.result:
                    dep_results.append(f"[{dep_id}] {dep_step.result[:300]}")
            context['dependencies'] = "\n".join(dep_results)
        if step.error:
            context['previous_error'] = step.error
        context['budgets'] = self.budgets.summary()
        return context

    def _update_codemap(self, step, result):
        import re
        created = re.findall(r'created?:?\s+(\S+\.\w+)', result, re.IGNORECASE)
        for path in created:
            self.state.codemap.add(path, f"created in step {step.id}")
        edited = re.findall(r'updated?:?\s+(\S+\.\w+)', result, re.IGNORECASE)
        for path in edited:
            self.state.codemap.update(path, desc=f"edited in step {step.id}")

    def status(self):
        lines = []
        if self.state.plan:
            lines.append(self.state.plan.summary())
        else:
            lines.append("no active plan")
        lines.append(f"\033[90mstate: {self.state.state}\033[0m")
        lines.append(f"\033[90m{self.budgets.summary()}\033[0m")
        lines.append("")
        lines.append(self.state.codemap.summary())
        if self.state.history:
            lines.append(f"\n\033[90m{len(self.state.history)} steps completed\033[0m")
        return "\n".join(lines)

    def abort(self):
        if self.state.plan:
            self.state.plan.status = "aborted"
            self.state.plan.log_event("aborted by user")
            self.state.state = "failed"
            self.state.save()
            return "plan aborted"
        return "no active plan"

    def add_file(self, path, desc, lines=0):
        self.state.codemap.add(path, desc, lines)
        self.state.save()

    def get_plan(self):
        return self.state.plan

    def get_codemap(self):
        return self.state.codemap
