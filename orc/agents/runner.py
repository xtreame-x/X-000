import os
import subprocess
from typing import Dict, Any
from .base import BaseAgent
from orc.logger import get_logger

logger = get_logger('agent.runner')

ORYN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))


class RunnerAgent(BaseAgent):
    """Runs code and captures output/errors."""

    name = "runner"

    def execute(self, task: str, context: Dict[str, Any]) -> str:
        codemap = context.get('codemap', '')
        deps = context.get('dependencies', '')

        # Determine what to run from the task
        system = f"""You are a runner agent. You execute code and report results.

CODEMAP:
{codemap}

RULES:
- Determine the correct command to run
- For Python: python <file>
- For scripts: run them directly
- Report stdout, stderr, and exit code
- If there's an error, include the full traceback"""

        # Parse the command from the task
        cmd = self._extract_command(task, context)

        try:
            logger.info(f"running: {cmd}")
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=ORYN_ROOT,
                timeout=60,
            )

            output = result.stdout
            error = result.stderr
            code = result.returncode

            report = f"command: {cmd}\nexit code: {code}"
            if output:
                report += f"\nstdout:\n{output[:2000]}"
            if error:
                report += f"\nstderr:\n{error[:2000]}"

            return report

        except subprocess.TimeoutExpired:
            return f"command timed out after 60s: {cmd}"
        except Exception as e:
            logger.error(f"Runner agent error: {e}")
            return f"execution error: {e}"

    def _extract_command(self, task: str, context: Dict[str, Any]) -> str:
        """Figure out what command to run."""
        task_lower = task.lower()

        # If task mentions a specific file, run it
        codemap = context.get('codemap', '')
        if codemap:
            for line in codemap.split('\n'):
                if '.py' in line:
                    path = line.split(':')[0].strip()
                    if os.path.exists(os.path.join(ORYN_ROOT, path)):
                        return f"python {os.path.join(ORYN_ROOT, path)}"

        # Default: try to run python on the task
        if 'python' in task_lower:
            # Extract python command from task
            import re
            match = re.search(r'python\s+(\S+)', task)
            if match:
                return f"python {os.path.join(ORYN_ROOT, match.group(1))}"

        # Try running the task as a command
        return task
