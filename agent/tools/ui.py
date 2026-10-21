"""UI and display tools (6).

Charts and widgets are rendered to self-contained HTML/SVG artifacts instead of
being drawn by the model: deterministic output, no external libraries, and
nothing that breaks in a terminal client.
"""
from __future__ import annotations

import html
import math
from typing import Literal

from agent.state import now_iso
from agent.tools.base import ToolContext, ToolResult, tool

MAX_WIDGET_CHARS = 200_000
CHART_WIDTH, CHART_HEIGHT = 720, 420
PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 64, 24, 48, 72

PALETTE = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#06b6d4", "#ec4899", "#84cc16"]


def _fmt(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}"


def _scale(values: list[float]) -> tuple[float, float, float]:
    """Return (low, high, ticks) for a value range that includes zero."""
    low = min(0.0, min(values))
    high = max(0.0, max(values))
    if high == low:
        high = low + 1.0
    return low, high, (high - low)


def _bar_chart(labels: list[str], values: list[float], title: str) -> str:
    low, high, span = _scale(values)
    plot_w = CHART_WIDTH - PAD_LEFT - PAD_RIGHT
    plot_h = CHART_HEIGHT - PAD_TOP - PAD_BOTTOM
    slot = plot_w / max(len(values), 1)
    bar_w = slot * 0.62
    zero_y = PAD_TOP + plot_h * (high / span)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" '
        f'width="100%" role="img" aria-label="{html.escape(title)}">',
        f'<rect width="{CHART_WIDTH}" height="{CHART_HEIGHT}" fill="#ffffff"/>',
        f'<text x="{PAD_LEFT}" y="28" font-family="system-ui,sans-serif" font-size="18" '
        f'font-weight="600" fill="#111827">{html.escape(title)}</text>',
        f'<line x1="{PAD_LEFT}" y1="{zero_y:.2f}" x2="{CHART_WIDTH - PAD_RIGHT}" y2="{zero_y:.2f}" '
        f'stroke="#9ca3af" stroke-width="1"/>',
    ]
    for index, (label, value) in enumerate(zip(labels, values)):
        height = abs(value) / span * plot_h
        x = PAD_LEFT + index * slot + (slot - bar_w) / 2
        y = zero_y - height if value >= 0 else zero_y
        colour = PALETTE[index % len(PALETTE)]
        parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_w:.2f}" height="{height:.2f}" '
            f'fill="{colour}" rx="3"/>'
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.2f}" y="{(y - 6) if value >= 0 else (y + height + 16):.2f}" '
            f'text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#374151">'
            f"{_fmt(value)}</text>"
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.2f}" y="{CHART_HEIGHT - PAD_BOTTOM + 20}" text-anchor="middle" '
            f'font-family="system-ui,sans-serif" font-size="11" fill="#4b5563">'
            f"{html.escape(label[:14])}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def _line_chart(labels: list[str], values: list[float], title: str) -> str:
    low, high, span = _scale(values)
    plot_w = CHART_WIDTH - PAD_LEFT - PAD_RIGHT
    plot_h = CHART_HEIGHT - PAD_TOP - PAD_BOTTOM
    step = plot_w / max(len(values) - 1, 1)
    points = []
    for index, value in enumerate(values):
        x = PAD_LEFT + index * step
        y = PAD_TOP + plot_h * (1 - (value - low) / span)
        points.append((x, y))
    polyline = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" '
        f'width="100%" role="img" aria-label="{html.escape(title)}">',
        f'<rect width="{CHART_WIDTH}" height="{CHART_HEIGHT}" fill="#ffffff"/>',
        f'<text x="{PAD_LEFT}" y="28" font-family="system-ui,sans-serif" font-size="18" '
        f'font-weight="600" fill="#111827">{html.escape(title)}</text>',
        f'<polyline points="{polyline}" fill="none" stroke="#2563eb" stroke-width="2.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>',
    ]
    for (x, y), value, label in zip(points, values, labels):
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.5" fill="#2563eb"/>')
        parts.append(
            f'<text x="{x:.2f}" y="{y - 10:.2f}" text-anchor="middle" font-size="11" '
            f'font-family="system-ui,sans-serif" fill="#374151">{_fmt(value)}</text>'
        )
        parts.append(
            f'<text x="{x:.2f}" y="{CHART_HEIGHT - PAD_BOTTOM + 20}" text-anchor="middle" '
            f'font-size="11" font-family="system-ui,sans-serif" fill="#4b5563">'
            f"{html.escape(label[:14])}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def _pie_chart(labels: list[str], values: list[float], title: str) -> str:
    positive = [max(value, 0.0) for value in values]
    total = sum(positive) or 1.0
    cx, cy, radius = CHART_WIDTH * 0.36, CHART_HEIGHT * 0.55, min(CHART_HEIGHT, CHART_WIDTH) * 0.32
    angle = -math.pi / 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" '
        f'width="100%" role="img" aria-label="{html.escape(title)}">',
        f'<rect width="{CHART_WIDTH}" height="{CHART_HEIGHT}" fill="#ffffff"/>',
        f'<text x="{PAD_LEFT}" y="28" font-family="system-ui,sans-serif" font-size="18" '
        f'font-weight="600" fill="#111827">{html.escape(title)}</text>',
    ]
    for index, (label, value) in enumerate(zip(labels, positive)):
        sweep = value / total * 2 * math.pi
        if sweep <= 0:
            continue
        end = angle + sweep
        x1, y1 = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
        x2, y2 = cx + radius * math.cos(end), cy + radius * math.sin(end)
        large = 1 if sweep > math.pi else 0
        colour = PALETTE[index % len(PALETTE)]
        if sweep >= 2 * math.pi - 1e-9:
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{colour}"/>')
        else:
            parts.append(
                f'<path d="M {cx} {cy} L {x1:.2f} {y1:.2f} A {radius} {radius} 0 {large} 1 '
                f'{x2:.2f} {y2:.2f} Z" fill="{colour}" stroke="#ffffff" stroke-width="1.5"/>'
            )
        angle = end
    legend_x = CHART_WIDTH * 0.68
    for index, (label, value) in enumerate(zip(labels, positive)):
        y = PAD_TOP + 24 + index * 26
        share = value / total * 100
        parts.append(
            f'<rect x="{legend_x}" y="{y - 10}" width="12" height="12" rx="2" '
            f'fill="{PALETTE[index % len(PALETTE)]}"/>'
        )
        parts.append(
            f'<text x="{legend_x + 20}" y="{y}" font-family="system-ui,sans-serif" font-size="12" '
            f'fill="#374151">{html.escape(label[:18])} — {share:.1f}%</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _page(title: str, svg: str, note: str = "") -> str:
    return (
        "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:24px;background:#f9fafb}"
        ".card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:16px;"
        "max-width:900px;margin:0 auto}</style></head><body><div class=\"card\">"
        f"{svg}{f'<p style=color:#6b7280;font-size:13px>{html.escape(note)}</p>' if note else ''}"
        "</div></body></html>"
    )


@tool(group="UI and display", tags=("ask", "question", "clarify", "input", "user"))
def ask_user_input_v0(
    ctx: ToolContext,
    question: str,
    options: list[str] | None = None,
    allow_free_text: bool = True,
) -> ToolResult:
    """Ask the user a question and wait for the answer.

    Requires an interactive host (a UI or terminal that supplies an input
    channel). When none exists the question is recorded and returned as
    ``no_interactive_channel`` so the caller can fall back to a default instead
    of blocking forever.

    Args:
        question: The question to display.
        options: Suggested answers. Free-text is still allowed when allow_free_text is set.
        allow_free_text: Permit an answer outside the suggested options.
    """
    if not question.strip():
        return ToolResult.error("question must not be empty", error="invalid_arguments")
    choices = [str(option) for option in (options or [])]
    ctx.emit_event({"type": "question", "question": question, "options": choices})

    if ctx.ask is None:
        ctx.state.append_jsonl(
            "pending_questions.jsonl",
            {"timestamp": now_iso(), "question": question, "options": choices},
        )
        return ToolResult.error(
            "no interactive channel is connected; the question was recorded but cannot be answered",
            error="no_interactive_channel",
            question=question,
            options=choices,
        )

    answer = ctx.ask(question, choices)
    if choices and not allow_free_text and answer not in choices:
        return ToolResult.error(
            f"answer must be one of {choices}", error="invalid_answer", answer=answer
        )
    return ToolResult.text(answer, question=question, answer=answer, options=choices)


@tool(group="UI and display", tags=("chart", "graph", "plot", "visualise", "data"))
def chart_display_v0(
    ctx: ToolContext,
    title: str,
    data: list[dict],
    x: str,
    y: str,
    chart_type: Literal["bar", "line", "pie"] = "bar",
) -> ToolResult:
    """Render a chart from tabular data and save it as an HTML artifact.

    Values must be numeric; non-numeric values produce a precise error naming
    the offending row instead of a silently empty chart.

    Args:
        title: Chart title.
        data: Rows of data, each a mapping of column name to value.
        x: Column used for the category axis.
        y: Column used for the numeric axis.
        chart_type: One of bar, line or pie.
    """
    if not data:
        return ToolResult.error("data must contain at least one row", error="invalid_arguments")
    labels: list[str] = []
    values: list[float] = []
    for index, row in enumerate(data, 1):
        if x not in row or y not in row:
            return ToolResult.error(
                f"row {index} is missing {x!r} or {y!r}", error="invalid_arguments", row=row
            )
        try:
            values.append(float(row[y]))
        except (TypeError, ValueError):
            return ToolResult.error(
                f"row {index}: {y}={row[y]!r} is not numeric", error="invalid_arguments", row=row
            )
        labels.append(str(row[x]))

    renderer = {"bar": _bar_chart, "line": _line_chart, "pie": _pie_chart}[chart_type]
    svg = renderer(labels, values, title)
    page = _page(title, svg, f"{chart_type} chart · {len(values)} series point(s)")
    info = ctx.save_artifact(page, name=title, ext="html", mime="text/html")
    ctx.emit_event(
        {"type": "chart", "title": title, "chartType": chart_type, "path": info["path"]}
    )
    return ToolResult.text(
        f"saved {chart_type} chart to {info['path']}",
        **info,
        chartType=chart_type,
        points=len(values),
    )


@tool(group="UI and display", tags=("recipe", "cooking", "ingredients", "steps"))
def recipe_display_v0(
    ctx: ToolContext,
    title: str,
    ingredients: list[str],
    steps: list[str],
    servings: int | None = None,
    notes: str = "",
) -> ToolResult:
    """Render a recipe card as a Markdown artifact.

    Args:
        title: Recipe name.
        ingredients: Ingredient lines.
        steps: Ordered preparation steps.
        servings: Number of servings, if known.
        notes: Optional tips or substitutions.
    """
    if not ingredients or not steps:
        return ToolResult.error(
            "ingredients and steps must both be non-empty", error="invalid_arguments"
        )
    servings_line = f"\n**Servings:** {servings}\n" if servings else ""
    body = (
        f"# {title}\n{servings_line}\n## Ingredients\n"
        + "\n".join(f"- {item}" for item in ingredients)
        + "\n\n## Method\n"
        + "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))
        + (f"\n\n## Notes\n{notes}" if notes.strip() else "")
        + "\n"
    )
    info = ctx.save_artifact(body, name=title, ext="md", mime="text/markdown")
    return ToolResult.text(f"saved recipe to {info['path']}", **info, servings=servings)


@tool(group="UI and display", tags=("cards", "recommendations", "options", "compare"))
def show_recommendation_cards(
    ctx: ToolContext,
    cards: list[dict],
    title: str = "Recommendations",
) -> ToolResult:
    """Render recommendation cards as an HTML artifact.

    Args:
        cards: Objects with title and optional description, url, imageUrl and badge.
        title: Heading above the cards.
    """
    if not cards:
        return ToolResult.error("at least one card is required", error="invalid_arguments")
    rendered = []
    for index, card in enumerate(cards, 1):
        if not card.get("title"):
            return ToolResult.error(
                f"card #{index} needs a title", error="invalid_arguments", card=card
            )
        link = str(card.get("url") or "")
        badge = str(card.get("badge") or "")
        image = str(card.get("imageUrl") or "")
        rendered.append(
            "<article style=\"border:1px solid #e5e7eb;border-radius:12px;padding:14px;"
            "display:flex;flex-direction:column;gap:8px\">"
            + (f'<img src="{html.escape(image)}" alt="" style="width:100%;border-radius:8px">' if image else "")
            + (f'<span style="font-size:11px;color:#6b7280;text-transform:uppercase">{html.escape(badge)}</span>' if badge else "")
            + f'<h3 style="margin:0;font-size:16px">{html.escape(str(card["title"]))}</h3>'
            + (f'<p style="margin:0;color:#4b5563;font-size:13px">{html.escape(str(card.get("description") or ""))}</p>' if card.get("description") else "")
            + (f'<a href="{html.escape(link)}" style="font-size:13px;color:#2563eb">Open</a>' if link else "")
            + "</article>"
        )
    page = (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title></head>"
        "<body style=\"font-family:system-ui,sans-serif;background:#f9fafb;margin:24px\">"
        f"<h1 style=\"font-size:20px\">{html.escape(title)}</h1>"
        "<div style=\"display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px\">"
        + "".join(rendered)
        + "</div></body></html>"
    )
    info = ctx.save_artifact(page, name=title, ext="html", mime="text/html")
    ctx.emit_event({"type": "cards", "title": title, "path": info["path"], "count": len(cards)})
    return ToolResult.text(f"rendered {len(cards)} card(s) to {info['path']}", **info, count=len(cards))


@tool(group="UI and display", name="mcp__visualize__read_me", tags=("visualize", "widget", "guide"))
def mcp__visualize__read_me() -> ToolResult:
    """Explain how to use the widget renderer (visualize show_widget).

    Read this before rendering a complex widget: it documents the accepted
    widget types, size limits and the rules that keep widgets portable.
    """
    guide = (
        "# Visualize widgets\n\n"
        "show_widget(widget_type, content, title)\n\n"
        "- `html`: a complete or fragment HTML document; inline styles only, no external JS.\n"
        "- `svg`: a single root <svg> element with an explicit viewBox and width/height.\n"
        "- `markdown`: CommonMark text; tables and code fences are supported.\n\n"
        f"Limits: {MAX_WIDGET_CHARS} characters.\n\n"
        "Rules:\n"
        "1. No external scripts, iframes or fonts; they will not load in sandboxed clients.\n"
        "2. Prefer system font stacks so the widget renders identically everywhere.\n"
        "3. Include `role=\"img\"` and `aria-label` on SVG for accessibility.\n"
        "4. Charts are better produced with chart_display_v0, which computes scales for you.\n"
    )
    return ToolResult.text(guide, maxChars=MAX_WIDGET_CHARS, widgetTypes=["html", "svg", "markdown"])


@tool(group="UI and display", name="mcp__visualize__show_widget", tags=("widget", "render", "html", "svg"))
def mcp__visualize__show_widget(
    ctx: ToolContext,
    widget_type: Literal["html", "svg", "markdown"],
    content: str,
    title: str = "Widget",
) -> ToolResult:
    """Render a widget and save it as an artifact.

    Args:
        widget_type: One of html, svg or markdown.
        content: Widget body. External scripts and iframes are rejected.
        title: Widget title, also used for the file name.
    """
    if not content.strip():
        return ToolResult.error("content must not be empty", error="invalid_arguments")
    if len(content) > MAX_WIDGET_CHARS:
        return ToolResult.error(
            f"content is {len(content)} characters, over the {MAX_WIDGET_CHARS} limit",
            error="too_large",
            characters=len(content),
        )
    lowered = content.lower()
    if widget_type == "html" and ("<script" in lowered or "<iframe" in lowered):
        return ToolResult.error(
            "external scripts and iframes are not allowed in widgets",
            error="unsafe_content",
        )
    if widget_type == "svg" and "<svg" not in lowered:
        return ToolResult.error("svg widgets need a root <svg> element", error="invalid_arguments")

    extension = {"html": "html", "svg": "svg", "markdown": "md"}[widget_type]
    mime = {"html": "text/html", "svg": "image/svg+xml", "markdown": "text/markdown"}[widget_type]
    payload = content
    if widget_type == "svg":
        payload = _page(title, content)
        extension, mime = "html", "text/html"
    info = ctx.save_artifact(payload, name=title, ext=extension, mime=mime)
    ctx.emit_event({"type": "widget", "widgetType": widget_type, **info, "title": title})
    return ToolResult.text(f"rendered {widget_type} widget to {info['path']}", **info, widgetType=widget_type)


__all__ = [
    "ask_user_input_v0",
    "chart_display_v0",
    "recipe_display_v0",
    "show_recommendation_cards",
    "mcp__visualize__read_me",
    "mcp__visualize__show_widget",
]
