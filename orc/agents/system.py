import os
import subprocess
from typing import Dict, Any
from .base import BaseAgent
from orc.files import handle_file_op
from orc.logger import get_logger

logger = get_logger('agent.system')


class SystemAgent(BaseAgent):
    """Handles file operations, process management, and system tasks."""

    name = "system"

    def execute(self, task: str, context: Dict[str, Any]) -> str:
        task_lower = task.lower()

        # File operations
        if any(w in task_lower for w in ['create', 'write', 'file', 'folder', 'directory']):
            return self._handle_file_task(task, context)

        # Process operations
        if any(w in task_lower for w in ['process', 'kill', 'task list', 'running']):
            return self._handle_process_task(task, context)

        # UI operations
        if any(w in task_lower for w in ['open', 'launch', 'browser', 'app']):
            return self._handle_ui_task(task, context)

        # Default: use AI to determine what to do
        return self._ai_decide(task, context)

    def _handle_file_task(self, task: str, context: Dict[str, Any]) -> str:
        """Handle file creation/editing tasks."""
        # Use AI to generate the file operation
        codemap = context.get('codemap', '')

        system = f"""You are a system agent handling file operations.

CODEMAP:
{codemap}

Output EXACTLY one FILE_OP command per line:
FILE_OP:create|<path>|<content>
FILE_OP:move|<src>|<dst>
FILE_OP:copy|<src>|<dst>
FILE_OP:delete|<path>
FILE_OP:list|<path>
FILE_OP:read|<path>

RULES:
- Use paths relative to {os.path.dirname(os.path.dirname(__file__))}
- For create, include FULL content
- One operation per line"""

        result = self._chat(system, task)

        # Execute each FILE_OP
        outputs = []
        for line in result.split('\n'):
            line = line.strip()
            if line.startswith('FILE_OP:'):
                op = line[8:]
                try:
                    output = handle_file_op(op)
                    outputs.append(output)
                except Exception as e:
                    outputs.append(f"error: {e}")

        return "\n".join(outputs) if outputs else "no file operations executed"

    def _handle_process_task(self, task: str, context: Dict[str, Any]) -> str:
        """Handle process management tasks."""
        task_lower = task.lower()

        if 'kill' in task_lower or 'stop' in task_lower:
            # Extract process name
            import re
            match = re.search(r'(?:kill|stop|end)\s+(\S+)', task_lower)
            if match:
                proc = match.group(1)
                try:
                    subprocess.run(f'taskkill /F /IM {proc}', shell=True, capture_output=True)
                    return f"killed {proc}"
                except Exception as e:
                    return f"failed to kill {proc}: {e}"

        if 'list' in task_lower or 'running' in task_lower:
            try:
                result = subprocess.run('tasklist', shell=True, capture_output=True, text=True)
                lines = result.stdout.strip().split('\n')[:20]
                return "\n".join(lines)
            except Exception as e:
                return f"failed to list processes: {e}"

        return self._ai_decide(task, context)

    def _handle_ui_task(self, task: str, context: Dict[str, Any]) -> str:
        """Handle UI operations like opening apps."""
        import re

        # Open URL
        url_match = re.search(r'(https?://\S+)', task)
        if url_match:
            url = url_match.group(1)
            try:
                import webbrowser
                webbrowser.open(url)
                return f"opened {url}"
            except Exception as e:
                return f"failed to open url: {e}"

        # Open file
        file_match = re.search(r'open\s+(\S+\.\w+)', task.lower())
        if file_match:
            path = file_match.group(1)
            return handle_file_op(f"open|{path}")

        return self._ai_decide(task, context)

    def _ai_decide(self, task: str, context: Dict[str, Any]) -> str:
        """Let the AI decide what system command to run."""
        system = """You are a system agent. Determine the best way to accomplish the task.
Output ONLY the shell command to run, nothing else.
Use PowerShell syntax."""

        cmd = self._chat(system, task)

        # Clean up the command
        cmd = cmd.strip().strip('`').strip()
        if cmd.startswith('```'):
            cmd = cmd.split('\n')[1] if '\n' in cmd else cmd[3:]
            cmd = cmd.rstrip('`')

        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            output = result.stdout
            if result.stderr:
                output += f"\n{result.stderr}"
            return output[:2000] if output else f"command executed (exit {result.returncode})"
        except Exception as e:
            return f"command failed: {e}"
