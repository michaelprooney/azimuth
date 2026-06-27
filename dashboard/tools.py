"""Tool implementations shared by Gemini function calling and the MCP server."""

from __future__ import annotations

import json
from typing import Any, Callable

from dashboard import garmin_data, storage


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def tool_get_export_status() -> str:
    """Return status of the Garmin export cache and latest export file."""
    return _json(garmin_data.export_status())


def tool_get_fitness_summary(days: int = 30) -> str:
    """Summarize fitness metrics (steps, sleep, stress, activities) for the last N days."""
    days = max(1, min(int(days), 365))
    return _json(garmin_data.fitness_summary(days))


def tool_get_daily_health(start_date: str, end_date: str) -> str:
    """Get daily health metrics between two ISO dates (YYYY-MM-DD)."""
    from datetime import date, timedelta

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        start, end = end, start
    rows = []
    d = start
    while d <= end:
        ds = d.isoformat()
        raw = garmin_data.load_day(ds)
        rows.append({"date": ds, "metrics": garmin_data._extract_day_metrics(raw) if raw else {}})
        d += timedelta(days=1)
    return _json(rows)


def tool_get_recent_activities(limit: int = 10) -> str:
    """List recent Garmin activities with summary stats."""
    limit = max(1, min(int(limit), 50))
    return _json(garmin_data.list_activities(limit))


def tool_get_activity_detail(activity_id: str) -> str:
    """Get structured activity details: laps, splits, HR zones, inferred workout structure."""
    return _json(garmin_data.activity_details(str(activity_id)))


def tool_get_workout_breakdown(
    activity_id: str | None = None,
    date: str | None = None,
    activity_type: str | None = None,
) -> str:
    """Get lap/interval paces, durations, and HR for a structured workout."""
    return _json(
        garmin_data.resolve_workout_breakdown(
            activity_id=activity_id,
            date_str=date,
            activity_type=activity_type,
        )
    )


def tool_get_training_metrics() -> str:
    """Get training metrics (VO2 max, readiness, etc.) from export cache."""
    training = garmin_data.load_section("training")
    return _json(training or {"message": "No training section in cache. Run garmin_export.py first."})


def tool_get_user_goals() -> str:
    """Get the user's personal fitness goals stored in the dashboard."""
    return _json(storage.list_goals())


def tool_add_user_goal(text: str, target_date: str | None = None, category: str = "general") -> str:
    """Add a personal fitness goal."""
    goal = storage.add_goal(text, target_date, category)
    return _json(goal)


def tool_update_user_goal(goal_id: str, status: str | None = None, text: str | None = None) -> str:
    """Update a personal fitness goal's status or text."""
    updated = storage.update_goal(goal_id, status=status, text=text)
    if updated is None:
        return json.dumps({"error": f"Goal not found: {goal_id}"})
    return _json(updated)


def tool_delete_user_goal(goal_id: str) -> str:
    """Delete a personal fitness goal."""
    ok = storage.delete_goal(goal_id)
    return json.dumps({"deleted": ok, "goal_id": goal_id})


TOOL_REGISTRY: dict[str, Callable[..., str]] = {
    "get_export_status": tool_get_export_status,
    "get_fitness_summary": tool_get_fitness_summary,
    "get_daily_health": tool_get_daily_health,
    "get_recent_activities": tool_get_recent_activities,
    "get_activity_detail": tool_get_activity_detail,
    "get_workout_breakdown": tool_get_workout_breakdown,
    "get_training_metrics": tool_get_training_metrics,
    "get_user_goals": tool_get_user_goals,
    "add_user_goal": tool_add_user_goal,
    "update_user_goal": tool_update_user_goal,
    "delete_user_goal": tool_delete_user_goal,
}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_export_status",
        "description": "Check whether Garmin export cache exists and when it was last updated.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_fitness_summary",
        "description": "Get aggregated fitness summary for the last N days from Garmin export cache.",
        "parameters": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days (default 30, max 365)"},
            },
            "required": [],
        },
    },
    {
        "name": "get_daily_health",
        "description": "Get per-day health metrics between two dates.",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_recent_activities",
        "description": (
            "List recent activities with distance, duration, HR, and inferred workout structure "
            "from lap data. Do not infer workout type from garmin_label."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max activities to return (default 10)"},
            },
            "required": [],
        },
    },
    {
        "name": "get_activity_detail",
        "description": (
            "Get full structured activity details for one workout: summary stats, HR zones, "
            "split summaries, all laps, work/recovery segments, and inferred_structure derived "
            "from lap distances and paces. Use this instead of activity names to understand a workout."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "activity_id": {"type": "string", "description": "Garmin activity ID"},
            },
            "required": ["activity_id"],
        },
    },
    {
        "name": "get_workout_breakdown",
        "description": (
            "Get lap and interval breakdown for a workout: per-repeat pace, duration, heart rate, "
            "and inferred_structure from lap data. Use when the user asks about intervals, repeats, "
            "splits, or pacing. Provide activity_id or date (YYYY-MM-DD). Do not rely on garmin_label."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "activity_id": {"type": "string", "description": "Garmin activity ID if known"},
                "date": {"type": "string", "description": "Workout date YYYY-MM-DD"},
                "activity_type": {
                    "type": "string",
                    "description": "Optional filter e.g. running, cycling",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_training_metrics",
        "description": "Get VO2 max, training readiness, and related training metrics.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_user_goals",
        "description": "Get the user's personal fitness goals from dashboard storage.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "add_user_goal",
        "description": "Add a new personal fitness goal for the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Goal description"},
                "target_date": {"type": "string", "description": "Optional target date YYYY-MM-DD"},
                "category": {"type": "string", "description": "Category e.g. running, strength, weight"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "update_user_goal",
        "description": "Update an existing goal's text or status (active/completed/archived).",
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "status": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "delete_user_goal",
        "description": "Delete a personal fitness goal.",
        "parameters": {
            "type": "object",
            "properties": {"goal_id": {"type": "string"}},
            "required": ["goal_id"],
        },
    },
]


def execute_tool(name: str, args: dict[str, Any]) -> str:
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        return fn(**args)
    except TypeError as exc:
        return json.dumps({"error": f"Bad arguments for {name}: {exc}"})
    except Exception as exc:
        return json.dumps({"error": f"Tool {name} failed: {exc}"})
