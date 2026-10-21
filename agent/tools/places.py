"""Places and sports tools (3).

Place search uses OpenStreetMap's Nominatim (free, no key, requires a
descriptive User-Agent and at most one request per second). Map display renders
a self-contained Leaflet page into the artifacts directory.
"""
from __future__ import annotations

import html
from datetime import date as date_cls, datetime, timezone
from typing import Any, Literal

import httpx

from agent.tools.base import ToolContext, ToolResult, tool

NOMINATIM = "https://nominatim.openstreetmap.org/search"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) OrynAgent/1.0 (contact: admin@oryn.local)"


@tool(group="Places and sports", tags=("places", "search", "map", "restaurant", "nearby"))
def places_search(
    ctx: ToolContext,
    query: str,
    near: str | None = None,
    limit: int = 5,
    country_code: str | None = None,
) -> ToolResult:
    """Search for places by name or category.

    Args:
        query: What to look for, e.g. 'coffee shop' or 'Eiffel Tower'.
        near: Optional locality to bias the search, e.g. 'Berlin'.
        limit: Maximum results (1-20).
        country_code: Optional ISO 3166-1 alpha-2 filter, e.g. 'de'.
    """
    if not query.strip():
        return ToolResult.error("query must not be empty", error="invalid_arguments")
    limit = max(1, min(int(limit), 20))
    params: dict[str, Any] = {
        "q": f"{query}, {near}" if near else query,
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": limit,
        "extratags": 1,
    }
    if country_code:
        params["countrycodes"] = country_code.lower()

    try:
        with httpx.Client(timeout=ctx.settings.request_timeout, headers={"User-Agent": USER_AGENT}) as client:
            response = client.get(NOMINATIM, params=params)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return ToolResult.error(
            f"places_search failed: {type(exc).__name__}: {exc}", error="network_error"
        )

    results = []
    for item in payload:
        results.append(
            {
                "name": (item.get("name") or item.get("display_name", "").split(",")[0]).strip(),
                "address": item.get("display_name", ""),
                "latitude": float(item["lat"]) if item.get("lat") else None,
                "longitude": float(item["lon"]) if item.get("lon") else None,
                "category": item.get("category") or item.get("type", ""),
                "osmType": item.get("osm_type", ""),
                "osmId": item.get("osm_id", ""),
            }
        )
    if not results:
        return ToolResult.text(f"No places found for {query!r}.", results=[], query=query)
    body = "\n".join(
        f"{i}. {r['name']} ({r['category']}) — {r['address']}"
        for i, r in enumerate(results, 1)
    )
    return ToolResult.text(body, results=results, query=query, near=near)


@tool(group="Places and sports", tags=("map", "places", "display", "markers"))
def places_map_display_v0(
    ctx: ToolContext,
    places: list[dict],
    title: str = "Map",
    zoom: int | None = None,
) -> ToolResult:
    """Render places as an interactive map artifact.

    Args:
        places: Objects with name, latitude and longitude (optionally address).
        title: Map heading and file name.
        zoom: Initial zoom level 1-19; inferred from marker spread when omitted.
    """
    markers = []
    for index, item in enumerate(places, 1):
        try:
            latitude = float(item["latitude"])
            longitude = float(item["longitude"])
        except (KeyError, TypeError, ValueError):
            return ToolResult.error(
                f"place #{index} needs numeric latitude and longitude",
                error="invalid_arguments",
                place=item,
            )
        markers.append(
            {
                "name": str(item.get("name") or f"Place {index}"),
                "address": str(item.get("address") or ""),
                "latitude": latitude,
                "longitude": longitude,
            }
        )
    if not markers:
        return ToolResult.error("at least one place is required", error="invalid_arguments")

    center_lat = sum(m["latitude"] for m in markers) / len(markers)
    center_lon = sum(m["longitude"] for m in markers) / len(markers)
    if zoom is None:
        spread = max(
            max(m["latitude"] for m in markers) - min(m["latitude"] for m in markers),
            max(m["longitude"] for m in markers) - min(m["longitude"] for m in markers),
        )
        zoom = 13 if spread < 0.05 else 11 if spread < 0.5 else 8 if spread < 5 else 3
    zoom = max(1, min(int(zoom), 19))

    points = [[m["latitude"], m["longitude"], html.escape(m["name"]), html.escape(m["address"])] for m in markers]
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body{{margin:0;height:100%}}#map{{height:100%;min-height:420px}}</style>
</head><body><div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const places = {points!r};
const map = L.map('map').setView([{center_lat}, {center_lon}], {zoom});
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
}}).addTo(map);
places.forEach(([lat, lon, name, address]) => {{
  L.marker([lat, lon]).addTo(map).bindPopup('<strong>' + name + '</strong><br>' + address);
}});
</script></body></html>"""
    info = ctx.save_artifact(page, name=title, ext="html", mime="text/html")
    return ToolResult.text(
        f"rendered {len(markers)} place(s) to {info['path']}",
        **info,
        places=markers,
    )


@tool(group="Places and sports", tags=("sports", "scores", "games", "espn", "fixtures"))
def fetch_sports_data(
    ctx: ToolContext,
    sport: Literal["football", "basketball", "baseball", "hockey", "soccer"] = "basketball",
    league: str = "nba",
    date: str | None = None,
) -> ToolResult:
    """Fetch scores and fixtures from ESPN's public scoreboard API.

    Args:
        sport: Sport family, e.g. football, basketball, baseball, hockey, soccer.
        league: League slug within the sport, e.g. nba, nfl, mlb, nhl, eng.1.
        date: Day to query as YYYY-MM-DD; defaults to today (UTC).
    """
    day = date or datetime.now(timezone.utc).date().isoformat()
    try:
        parsed = date_cls.fromisoformat(day)
    except ValueError:
        return ToolResult.error(f"invalid date {day!r}; expected YYYY-MM-DD", error="invalid_arguments")

    url = ESPN.format(sport=sport, league=league)
    espn_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=ctx.settings.request_timeout, headers=espn_headers) as client:
            response = client.get(url, params={"dates": parsed.strftime("%Y%m%d")})
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return ToolResult.error(
            f"fetch_sports_data failed for {sport}/{league}: {type(exc).__name__}: {exc}",
            error="network_error",
            sport=sport,
            league=league,
        )

    games = []
    for event in payload.get("events", []):
        competition = (event.get("competitions") or [{}])[0]
        competitors = []
        for side in competition.get("competitors", []):
            team = side.get("team", {})
            competitors.append(
                {
                    "team": team.get("displayName") or team.get("name", ""),
                    "score": side.get("score"),
                    "homeAway": side.get("homeAway", ""),
                    "winner": bool(side.get("winner", False)),
                    "record": next(
                        (r.get("summary") for r in side.get("records", []) if r.get("summary")),
                        "",
                    ),
                }
            )
        games.append(
            {
                "id": event.get("id"),
                "name": event.get("name", ""),
                "date": event.get("date", ""),
                "status": (event.get("status") or {}).get("type", {}).get("description", ""),
                "detail": (event.get("status") or {}).get("type", {}).get("detail", ""),
                "venue": (competition.get("venue") or {}).get("fullName", ""),
                "competitors": competitors,
            }
        )

    if not games:
        return ToolResult.text(
            f"No {league} games on {day}.", games=[], sport=sport, league=league, date=day
        )
    lines = []
    for game in games:
        score = " vs ".join(
            f"{c['team']} {c['score']}" for c in game["competitors"]
        ) or game["name"]
        lines.append(f"- {score} — {game['status']} ({game['detail']})".rstrip())
    return ToolResult.text(
        "\n".join(lines), games=games, sport=sport, league=league, date=day
    )


__all__ = ["places_search", "places_map_display_v0", "fetch_sports_data"]
