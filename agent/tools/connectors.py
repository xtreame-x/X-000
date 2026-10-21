"""Connector tools (3): tool_search, search_mcp_registry, suggest_connectors.

``tool_search`` is the gate for deferred tools: the 238 Vercel tools and the
Drive/SketchUp/Three.js tools stay hidden from the model until it searches for
them, which keeps the advertised tool list small without losing capability.
"""
from __future__ import annotations

from agent.tools.base import REGISTRY, ToolContext, ToolResult, tool
from agent.state import now_iso

#: Curated MCP server catalog. Names and packages are the stable identifiers; the
#: descriptions state what each server actually exposes.
MCP_CATALOG: list[dict] = [
    {"name": "filesystem", "package": "@modelcontextprotocol/server-filesystem",
     "capabilities": ["read files", "write files", "list directories", "search"],
     "description": "Sandboxed local filesystem access with configurable roots."},
    {"name": "git", "package": "@modelcontextprotocol/server-git",
     "capabilities": ["status", "diff", "log", "commit"],
     "description": "Inspect and manipulate a local Git repository."},
    {"name": "github", "package": "@modelcontextprotocol/server-github",
     "capabilities": ["issues", "pull requests", "repositories", "code search"],
     "description": "GitHub API access for issues, PRs and repository metadata."},
    {"name": "postgres", "package": "@modelcontextprotocol/server-postgres",
     "capabilities": ["sql query", "schema inspection"],
     "description": "Read-only SQL access to a PostgreSQL database."},
    {"name": "sqlite", "package": "@modelcontextprotocol/server-sqlite",
     "capabilities": ["sql query", "schema inspection", "local database"],
     "description": "Query and inspect a local SQLite database file."},
    {"name": "slack", "package": "@modelcontextprotocol/server-slack",
     "capabilities": ["channels", "messages", "post message"],
     "description": "Read and post Slack messages."},
    {"name": "google-drive", "package": "mcp__Google_Drive__*",
     "capabilities": ["search files", "read content", "share", "upload"],
     "description": "Google Drive file operations (bundled in this agent)."},
    {"name": "google-maps", "package": "@modelcontextprotocol/server-google-maps",
     "capabilities": ["geocode", "places", "directions"],
     "description": "Google Maps geocoding, places and routing."},
    {"name": "puppeteer", "package": "@modelcontextprotocol/server-puppeteer",
     "capabilities": ["browser automation", "screenshot", "scrape"],
     "description": "Headless browser control for scraping and screenshots."},
    {"name": "fetch", "package": "mcp-server-fetch",
     "capabilities": ["http fetch", "html to markdown"],
     "description": "Fetch URLs and convert pages to LLM-friendly markdown."},
    {"name": "memory", "package": "@modelcontextprotocol/server-memory",
     "capabilities": ["knowledge graph", "entities", "relations"],
     "description": "Persistent knowledge-graph memory across sessions."},
    {"name": "sequential-thinking", "package": "@modelcontextprotocol/server-sequential-thinking",
     "capabilities": ["planning", "reasoning steps"],
     "description": "Structured step-by-step reasoning scratchpad."},
    {"name": "sentry", "package": "@sentry/mcp-server",
     "capabilities": ["error monitoring", "issues", "traces"],
     "description": "Read Sentry issues, events and releases."},
    {"name": "vercel", "package": "mcp__Vercel__*",
     "capabilities": ["deployments", "projects", "domains", "edge config", "flags"],
     "description": "Vercel platform management (238 tools, bundled in this agent)."},
    {"name": "notion", "package": "@notionhq/notion-mcp-server",
     "capabilities": ["pages", "databases", "search"],
     "description": "Read and write Notion pages and databases."},
    {"name": "stripe", "package": "@stripe/mcp",
     "capabilities": ["payments", "customers", "invoices"],
     "description": "Stripe API access for payments and billing objects."},
]

GOAL_KEYWORDS = {
    "database": ["postgres", "sqlite"],
    "sql": ["postgres", "sqlite"],
    "repo": ["git", "github"],
    "code": ["github", "git"],
    "deploy": ["vercel"],
    "hosting": ["vercel"],
    "files": ["filesystem", "google-drive"],
    "documents": ["google-drive", "notion"],
    "browse": ["puppeteer", "fetch"],
    "scrape": ["puppeteer", "fetch"],
    "chat": ["slack"],
    "notify": ["slack"],
    "memory": ["memory"],
    "plan": ["sequential-thinking"],
    "errors": ["sentry"],
    "monitoring": ["sentry"],
    "payments": ["stripe"],
    "maps": ["google-maps"],
    "location": ["google-maps"],
}


@tool(group="Connectors", tags=("tool", "search", "discover", "load", "deferred"))
def tool_search(ctx: ToolContext, query: str, limit: int = 10) -> ToolResult:
    """Search all tools, including deferred ones, and unlock the matches.

    Deferred tools cannot be called until they are unlocked here. Searching
    returns each tool's JSON Schema so you can call it immediately afterwards.

    Args:
        query: What you want to do, e.g. 'create deployment' or 'list drive files'.
        limit: Maximum tools to return (1-25).
    """
    REGISTRY.load_all()
    limit = max(1, min(int(limit), 25))
    matches = REGISTRY.search(query, limit=limit)
    if not matches:
        return ToolResult.text(
            f"No tools matched {query!r}. Try a broader query such as 'drive', 'vercel' or 'files'.",
            matches=[],
        )
    for tool_obj in matches:
        ctx.loaded_tools.add(tool_obj.name)
    payload = [tool_obj.schema() for tool_obj in matches]
    ctx.state.append_jsonl(
        "tool_search.jsonl",
        {"timestamp": now_iso(), "query": query, "unlocked": [t.name for t in matches]},
    )
    listing = "\n".join(f"- {t.name} ({t.group})" for t in matches)
    return ToolResult.json(
        payload,
        text=f"unlocked {len(matches)} tool(s):\n{listing}",
        query=query,
        unlocked=[t.name for t in matches],
    )


@tool(group="Connectors", tags=("mcp", "registry", "connector", "integration", "install"))
def search_mcp_registry(
    ctx: ToolContext,
    query: str = "",
    capability: str | None = None,
    limit: int = 8,
) -> ToolResult:
    """Search a curated catalog of MCP servers by name or capability.

    The catalog is bundled with the agent (it does not call a remote registry),
    so results are deterministic and available offline.

    Args:
        query: Free-text match against name, description and capabilities.
        capability: Restrict to servers advertising this capability.
        limit: Maximum results (1-20).
    """
    limit = max(1, min(int(limit), 20))
    needle = query.strip().lower()
    results = []
    for entry in MCP_CATALOG:
        haystack = " ".join([entry["name"], entry["description"], *entry["capabilities"]]).lower()
        if needle and needle not in haystack:
            continue
        if capability and not any(capability.lower() in cap.lower() for cap in entry["capabilities"]):
            continue
        results.append(entry)
    results = results[:limit]
    if not results:
        return ToolResult.text(
            f"No MCP servers matched {query!r}.", servers=[], catalogSize=len(MCP_CATALOG)
        )
    body = "\n".join(
        f"- {entry['name']} ({entry['package']}): {entry['description']}" for entry in results
    )
    return ToolResult.text(body, servers=results, catalogSize=len(MCP_CATALOG))


@tool(group="Connectors", tags=("connector", "suggest", "integrate", "recommend"))
def suggest_connectors(ctx: ToolContext, goal: str, limit: int = 3) -> ToolResult:
    """Suggest MCP connectors that would help accomplish a goal.

    Args:
        goal: What the user is trying to do, in their own words.
        limit: Maximum suggestions (1-5).
    """
    text = goal.lower()
    scores: dict[str, int] = {}
    for keyword, names in GOAL_KEYWORDS.items():
        if keyword in text:
            for name in names:
                scores[name] = scores.get(name, 0) + 1
    for entry in MCP_CATALOG:
        if entry["name"].replace("-", " ") in text:
            scores[entry["name"]] = scores.get(entry["name"], 0) + 3

    ranked = sorted(scores.items(), key=lambda pair: -pair[1])[: max(1, min(int(limit), 5))]
    by_name = {entry["name"]: entry for entry in MCP_CATALOG}
    suggestions = [
        {**by_name[name], "why": f"matches keywords in: {goal[:80]}"}
        for name, _ in ranked
        if name in by_name
    ]
    if not suggestions:
        return ToolResult.text(
            "No connector suggestion matched. Search the registry instead.",
            suggestions=[],
            goal=goal,
        )
    body = "\n".join(
        f"- {item['name']} ({item['package']}): {item['description']}" for item in suggestions
    )
    return ToolResult.text(body, suggestions=suggestions, goal=goal)


__all__ = ["tool_search", "search_mcp_registry", "suggest_connectors", "MCP_CATALOG"]
