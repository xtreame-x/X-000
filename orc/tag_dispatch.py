import re
from typing import List, Tuple, Optional, Dict, Callable

TAG_PATTERN = re.compile(r'\[ACT\]\[(\w+)\](.*?)\[/ACT\]', re.DOTALL)
MSG_PATTERN = re.compile(r'\[MSG\](.*?)(?=\[ACT|\Z)', re.DOTALL)


class TagDispatch:
    """Parses model response into [MSG] and [ACT][tool_name] blocks.

    [MSG] stays free text — no parsing overhead.
    [ACT][tool_name] args [/ACT] is dispatched to the registered handler
    for `tool_name`. Unknown tool names fail fast with a clear error.
    """

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._schemas: Dict[str, str] = {}

    def register(self, name: str, handler: Callable, schema: str = ""):
        self._handlers[name] = handler
        self._schemas[name] = schema

    def parse(self, raw: str) -> Tuple[str, List[Tuple[str, str, Optional[str]]]]:
        """Returns (msg_text, [(tool_name, args, result_or_error)])."""
        msg = self._extract_msg(raw)
        actions = self._extract_actions(raw)
        return msg, actions

    def dispatch(self, raw: str) -> Tuple[str, List[Tuple[str, str, Optional[str]]]]:
        """Parse and execute all actions. Returns (msg, [(tool, args, result)])."""
        msg, actions = self.parse(raw)
        results = []
        for tool_name, args in actions:
            if tool_name not in self._handlers:
                results.append((tool_name, args, f"[ERR] unknown tool: {tool_name}"))
                continue
            try:
                result = self._handlers[tool_name](args)
                results.append((tool_name, args, result))
            except Exception as e:
                results.append((tool_name, args, f"[ERR] {e}"))
        return msg, results

    def _extract_msg(self, raw: str) -> str:
        match = MSG_PATTERN.search(raw)
        if match:
            return match.group(1).strip()
        # No [MSG] tag — return everything before first [ACT]
        idx = raw.find("[ACT]")
        if idx >= 0:
            return raw[:idx].strip()
        return raw.strip()

    def _extract_actions(self, raw: str) -> List[Tuple[str, str]]:
        return [(m.group(1), m.group(2).strip()) for m in TAG_PATTERN.finditer(raw)]
