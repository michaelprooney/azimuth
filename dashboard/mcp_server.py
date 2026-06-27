#!/usr/bin/env python3
"""MCP server exposing Garmin fitness data tools for Gemini / Cursor agents.

Run:
    python -m dashboard.mcp_server

Or configure in Cursor MCP settings:
    {"command": "python", "args": ["-m", "dashboard.mcp_server"], "cwd": "C:/JQR/azimuth"}
"""

from __future__ import annotations

from dashboard.config import load_env
from dashboard.tools import (
    tool_add_user_goal,
    tool_delete_user_goal,
    tool_get_activity_detail,
    tool_get_daily_health,
    tool_get_export_status,
    tool_get_fitness_summary,
    tool_get_recent_activities,
    tool_get_training_metrics,
    tool_get_user_goals,
    tool_get_workout_breakdown,
    tool_update_user_goal,
)

load_env()

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:
    raise SystemExit("Install mcp: pip install mcp") from exc

mcp = FastMCP(
    "Garmin Fitness",
    instructions=(
        "Tools for reading Garmin Connect export data and user fitness goals. "
        "Data comes from garmin_export.py cache under export/.cache/."
    ),
)


@mcp.tool()
def get_export_status() -> str:
    """Check Garmin export cache status and latest export file."""
    return tool_get_export_status()


@mcp.tool()
def get_fitness_summary(days: int = 30) -> str:
    """Summarize steps, sleep, stress, and recent activities for the last N days."""
    return tool_get_fitness_summary(days)


@mcp.tool()
def get_daily_health(start_date: str, end_date: str) -> str:
    """Get daily health metrics between two ISO dates (YYYY-MM-DD)."""
    return tool_get_daily_health(start_date, end_date)


@mcp.tool()
def get_recent_activities(limit: int = 10) -> str:
    """List recent Garmin activities."""
    return tool_get_recent_activities(limit)


@mcp.tool()
def get_activity_detail(activity_id: str) -> str:
    """Get structured activity details: laps, splits, HR zones, inferred workout structure."""
    return tool_get_activity_detail(activity_id)


@mcp.tool()
def get_workout_breakdown(
    activity_id: str | None = None,
    date: str | None = None,
    activity_type: str | None = None,
) -> str:
    """Get lap/interval paces, durations, and HR for a structured workout."""
    return tool_get_workout_breakdown(activity_id, date, activity_type)


@mcp.tool()
def get_training_metrics() -> str:
    """Get VO2 max, training readiness, and related metrics."""
    return tool_get_training_metrics()


@mcp.tool()
def get_user_goals() -> str:
    """Get personal fitness goals stored in the dashboard."""
    return tool_get_user_goals()


@mcp.tool()
def add_user_goal(text: str, target_date: str | None = None, category: str = "general") -> str:
    """Add a personal fitness goal."""
    return tool_add_user_goal(text, target_date, category)


@mcp.tool()
def update_user_goal(goal_id: str, status: str | None = None, text: str | None = None) -> str:
    """Update a goal's status or text."""
    return tool_update_user_goal(goal_id, status, text)


@mcp.tool()
def delete_user_goal(goal_id: str) -> str:
    """Delete a personal fitness goal."""
    return tool_delete_user_goal(goal_id)


if __name__ == "__main__":
    mcp.run()
