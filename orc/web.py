from ddgs import DDGS
from orc.logger import get_logger

logger = get_logger('web')


def search(query, max_results=5):
    """Search the web using DuckDuckGo."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return f"no results found for: {query}"

        return format_results(query, results)

    except Exception as e:
        logger.error(f"Search failed: {e}")
        return f"search error: {e}"


def format_results(query, results):
    """Format search results for display."""
    lines = [f"\033[90msearch: {query} ({len(results)} results)\033[0m"]

    for i, r in enumerate(results, 1):
        title = r.get('title', 'untitled')
        url = r.get('href', '')
        body = r.get('body', '')[:150]

        lines.append(f"\n  \033[34m{i}. {title}\033[0m")
        lines.append(f"     \033[90m{url}\033[0m")
        if body:
            lines.append(f"     {body}...")

    return '\n'.join(lines)


def search_for_context(query, max_results=3):
    """Search and return raw results for AI context injection."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        context_parts = []
        for r in results:
            title = r.get('title', '')
            body = r.get('body', '')
            context_parts.append(f"{title}: {body}")

        return '\n'.join(context_parts)

    except Exception as e:
        return f"search unavailable: {e}"
