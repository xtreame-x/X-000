import os
import subprocess
import threading
import uuid
from queue import Queue
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
from orc.logger import get_logger

logger = get_logger('tasks')

MAX_OUTPUT_CHARS = 10000
MAX_TASKS = 100
TASK_CLEANUP_AGE_HOURS = 1


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    id: str
    command: str
    status: TaskStatus = TaskStatus.PENDING
    output: str = ""
    error: str = ""
    returncode: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    process: Optional[subprocess.Popen] = field(default=None, repr=False)


class AsyncTaskManager:
    def __init__(self, max_workers: int = 3):
        self.tasks: Dict[str, Task] = {}
        self.queue = Queue()
        self.max_workers = max_workers
        self._workers = []
        self._lock = threading.Lock()
        self._start_workers()

    def _start_workers(self):
        for _ in range(self.max_workers):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            self._workers.append(t)

    def _worker(self):
        while True:
            task_id = self.queue.get()
            try:
                with self._lock:
                    task = self.tasks.get(task_id)
                if task:
                    self._run_task(task)
            except Exception as e:
                logger.error(f"Worker error: {e}")
            finally:
                self.queue.task_done()

    def _run_task(self, task: Task):
        task.status = TaskStatus.RUNNING
        logger.info(f"Running task {task.id}: {task.command[:50]}")

        try:
            proc = subprocess.Popen(
                task.command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=os.path.expanduser('~'),
            )
            task.process = proc

            stdout, stderr = proc.communicate(timeout=300)
            out = stdout.strip() if stdout else ""
            err = stderr.strip() if stderr else ""
            task.output = out[:MAX_OUTPUT_CHARS]
            task.error = err[:MAX_OUTPUT_CHARS]
            task.returncode = proc.returncode

            if proc.returncode == 0:
                task.status = TaskStatus.COMPLETED
            else:
                task.status = TaskStatus.FAILED

            if len(out) > MAX_OUTPUT_CHARS:
                logger.warning(f"Task {task.id} output truncated ({len(out)} -> {MAX_OUTPUT_CHARS})")

        except subprocess.TimeoutExpired:
            task.status = TaskStatus.FAILED
            task.error = "task timed out after 300s"
            if task.process:
                task.process.kill()
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)[:MAX_OUTPUT_CHARS]
        finally:
            task.completed_at = datetime.now()
            task.process = None
            logger.info(f"Task {task.id} finished: {task.status.value}")

    def submit(self, command: str, timeout: int = 300) -> str:
        self._auto_prune()
        self._enforce_max_tasks()

        task_id = uuid.uuid4().hex[:8]
        task = Task(id=task_id, command=command)

        with self._lock:
            self.tasks[task_id] = task

        self.queue.put(task_id)
        logger.info(f"Task submitted: {task_id}")
        return task_id

    def status(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return False

            if task.status == TaskStatus.RUNNING:
                if task.process:
                    task.process.kill()
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now()
                return True

            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now()
                return True

        return False

    def list_active(self) -> List[Task]:
        return [
            t for t in self.tasks.values()
            if t.status in (TaskStatus.RUNNING, TaskStatus.PENDING)
        ]

    def list_all(self, limit: int = 20) -> List[Task]:
        tasks = sorted(
            self.tasks.values(),
            key=lambda t: t.created_at,
            reverse=True
        )
        return tasks[:limit]

    def cleanup(self, max_age_hours: int = TASK_CLEANUP_AGE_HOURS):
        cutoff = datetime.now().timestamp() - (max_age_hours * 3600)
        to_remove = []

        with self._lock:
            for task_id, task in list(self.tasks.items()):
                if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                    if task.completed_at and task.completed_at.timestamp() < cutoff:
                        to_remove.append(task_id)

            for task_id in to_remove:
                del self.tasks[task_id]

        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} old tasks")

    def _auto_prune(self):
        self.cleanup(max_age_hours=TASK_CLEANUP_AGE_HOURS)

    def _enforce_max_tasks(self):
        with self._lock:
            if len(self.tasks) >= MAX_TASKS:
                sorted_tasks = sorted(
                    self.tasks.items(),
                    key=lambda t: t[1].created_at
                )
                to_remove = len(self.tasks) - MAX_TASKS + 1
                for task_id, _ in sorted_tasks[:to_remove]:
                    if self.tasks[task_id].status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                        del self.tasks[task_id]


_manager = None


def get_manager() -> AsyncTaskManager:
    global _manager
    if _manager is None:
        _manager = AsyncTaskManager()
    return _manager
