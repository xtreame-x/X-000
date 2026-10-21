import os
from typing import Dict, Any
from .base import BaseAgent
from orc.logger import get_logger

logger = get_logger('agent.searcher')


class SearcherAgent(BaseAgent):
    """Searches the web and gathers information."""

    name = "searcher"

    def execute(self, task: str, context: Dict[str, Any]) -> str:
        system = """You are a searcher agent. You find information on the web.

RULES:
- Use SEARCH when you need real-time information
- Summarize findings concisely
- Focus on actionable facts, not fluff
- If searching for code patterns, include key snippets
- Always cite what you found"""

        user_msg = f"Search for: {task}"

        try:
            # Use the shell's web_search if available
            from orc.shell import web_search
            results = web_search(task)

            # Let AI synthesize the results
            synth_system = "Synthesize these search results into a concise, actionable summary."
            summary = self._chat(synth_system, f"Query: {task}\n\nResults:\n{results}")
            return summary

        except ImportError:
            # Fallback: just ask the AI
            return self._chat(system, user_msg)
        except Exception as e:
            logger.error(f"Searcher agent error: {e}")
            # Fallback to AI knowledge
            return self._chat(system, user_msg)
