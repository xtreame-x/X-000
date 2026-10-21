import os
import re
from typing import Dict, Any
from .base import BaseAgent
from orc.logger import get_logger
from orc.files import (
    file_toc, read_file_context, estimate_tokens,
    parse_search_replace_blocks, apply_search_replace,
    create_file, read_file
)

logger = get_logger('agent.coder')

ORYN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

SMALL_FILE_TOKENS = 300
PROGRESSIVE_CHUNK_TOKENS = 1500
MAX_PROGRESSIVE_ROUNDS = 20

SEARCH_REPLACE_PROMPT = """\
All changes must use SEARCH/REPLACE blocks.

Format:
  path/to/file.py
  <<<<<<< SEARCH
  <exact code to find>
  =======
  <replacement code>
  >>>>>>> REPLACE

RULES:
- The SEARCH section must EXACTLY match existing file content, character for character
- If creating a NEW file, leave SEARCH empty:
    <<<<<<< SEARCH
    =======
    <file content>
    >>>>>>> REPLACE
- If appending to an EXISTING file, leave SEARCH empty:
    <<<<<<< SEARCH
    =======
    <content to append>
    >>>>>>> REPLACE
- Multiple SEARCH/REPLACE blocks in any order are allowed
- Each block must be separated by blank lines
- Keep each block small and focused on one change
- Include surrounding context in SEARCH to make the match unique"""


class CoderAgent(BaseAgent):
    """Generates, edits, and reviews code using SEARCH/REPLACE blocks."""

    name = "coder"

    def execute(self, task: str, context: Dict[str, Any]) -> str:
        codemap = context.get('codemap', '')
        deps = context.get('dependencies', '')
        error = context.get('previous_error', '')
        files_info = self._analyze_files(task, codemap)
        file_context = self._build_file_context(files_info)

        system = f"""You are a coder agent. You write and edit code files.

{codemap if codemap else ''}

{file_context}

{SEARCH_REPLACE_PROMPT}

{'PREVIOUS ERROR TO FIX: ' + error if error else ''}
"""

        user_msg = f"Task: {task}"
        if deps:
            user_msg += f"\n\nDependency results:\n{deps}"

        raw = self._chat(system, user_msg, coding=True)
        if not raw.strip():
            return "empty response from AI"

        results = self._apply_blocks(raw)

        # Progressive generation for large files
        created_files = [r for r in results if r.startswith('created:') or r.startswith('appended')]
        if created_files:
            results = self._progressive_loop(system, created_files, results)

        return "\n".join(results) if results else "no changes made"

    def _analyze_files(self, task, codemap):
        """Scan codemap and task to find files we need to work with."""
        files_info = {}
        for line in codemap.split('\n'):
            line = line.strip()
            if ':' in line and not line.startswith(('cm:', '[', 'no files')):
                parts = line.split(':', 1)
                path = parts[0].strip()
                path = os.path.join(ORYN_ROOT, path) if not os.path.isabs(path) else path
                if os.path.exists(path):
                    try:
                        with open(path, 'r') as f:
                            content = f.read()
                        token_est = estimate_tokens(content)
                        line_count = content.count('\n') + 1
                        files_info[path] = {
                            'exists': True,
                            'tokens': token_est,
                            'lines': line_count,
                            'small': token_est < SMALL_FILE_TOKENS,
                        }
                    except Exception:
                        files_info[path] = {'exists': False, 'tokens': 0, 'lines': 0}
                else:
                    files_info[path] = {'exists': False, 'tokens': 0, 'lines': 0}
        return files_info

    def _build_file_context(self, files_info):
        """Small files → full content. Large files → TOC."""
        parts = []
        for path, info in sorted(files_info.items()):
            if not info['exists']:
                parts.append(f"[NEW] {path}")
            elif info['small']:
                content = read_file(path, max_lines=200)
                parts.append(f"[EXISTS] {path} ({info['lines']} lines)\n{content}")
            else:
                toc = file_toc(path)
                parts.append(f"[EXISTS] {path} ({info['lines']} lines)\n{toc}")
        return "\n\n".join(parts)

    def _apply_blocks(self, raw):
        """Parse SEARCH/REPLACE blocks and apply them."""
        blocks = parse_search_replace_blocks(raw)
        if not blocks:
            return [self._fallback_creation(raw)]

        results = []
        for path, search_text, replace_text in blocks:
            msg, ok = apply_search_replace(path, search_text, replace_text)
            results.append(msg)
            logger.info(f"Coder: {msg}")
        return results

    def _progressive_loop(self, system, created_msgs, results):
        """Multi-round progressive generation for newly created files."""
        current_files = self._parse_created_paths(created_msgs)
        if not current_files:
            return results

        for _round in range(1, MAX_PROGRESSIVE_ROUNDS + 1):
            needs_more = {}
            for path in list(current_files.keys()):
                try:
                    with open(path, 'r') as f:
                        content = f.read()
                except Exception:
                    continue
                t = estimate_tokens(content)
                if t < PROGRESSIVE_CHUNK_TOKENS:
                    needs_more[path] = (t, content)

            if not needs_more:
                break

            for path, (current_tokens, content) in needs_more.items():
                lines = content.splitlines()
                tail = "\n".join(lines[-5:]) if lines else ""

                cont_msg = f"""Continue writing file: {path}
Lines written: {len(lines)}  Estimated tokens: {current_tokens}
Target: ~{PROGRESSIVE_CHUNK_TOKENS} tokens total

Last 5 lines:
{tail}

Append the next section using an empty SEARCH block:
<<<<<<< SEARCH
=======
<new content>
>>>>>>> REPLACE"""

                raw = self._chat(system, cont_msg, coding=True)
                if raw.strip():
                    new_results = self._apply_blocks(raw)
                    results.extend(new_results)
                    for nr in new_results:
                        if 'appended' in nr or 'updated' in nr:
                            for p in list(needs_more.keys()):
                                if p in nr and os.path.exists(p):
                                    try:
                                        with open(p, 'r') as f:
                                            c = f.read()
                                        if estimate_tokens(c) >= PROGRESSIVE_CHUNK_TOKENS:
                                            current_files.pop(p, None)
                                    except Exception:
                                        pass
        return results

    def _parse_created_paths(self, msgs):
        """Extract file paths from 'created: path' result messages."""
        paths = {}
        for msg in msgs:
            for prefix in ('created:', 'appended to:'):
                if prefix in msg:
                    parts = msg.split(':', 1)
                    if len(parts) > 1:
                        path = parts[1].split('(')[0].strip()
                        if os.path.exists(path):
                            paths[path] = True
        return paths

    def _fallback_creation(self, raw):
        """If no SEARCH/REPLACE blocks found, try treating raw as file content."""
        sanitized = re.sub(r'[^\w\- ]', '_', raw.strip()[:30])
        path = os.path.join(ORYN_ROOT, 'data', f'output_{sanitized}.txt')
        return create_file(path, raw.strip())


