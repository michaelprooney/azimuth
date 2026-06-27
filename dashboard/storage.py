"""Persistent user state: goals and chat history."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from dashboard.config import STATE_FILE

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state() -> dict[str, Any]:
    return {
        "goals": [],
        "chat_history": [],
        "insights_cache": None,
    }


def load_state() -> dict[str, Any]:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        return _default_state()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _default_state()
        data.setdefault("goals", [])
        data.setdefault("chat_history", [])
        data.setdefault("insights_cache", None)
        return data
    except (json.JSONDecodeError, OSError):
        return _default_state()


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def list_goals() -> list[dict[str, Any]]:
    with _lock:
        return list(load_state().get("goals", []))


def add_goal(text: str, target_date: str | None = None, category: str = "general") -> dict[str, Any]:
    goal = {
        "id": str(uuid.uuid4()),
        "text": text.strip(),
        "target_date": target_date,
        "category": category,
        "status": "active",
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _lock:
        state = load_state()
        state["goals"].append(goal)
        save_state(state)
    return goal


def update_goal(goal_id: str, **fields: Any) -> dict[str, Any] | None:
    with _lock:
        state = load_state()
        for goal in state["goals"]:
            if goal.get("id") == goal_id:
                for key, val in fields.items():
                    if key in ("text", "target_date", "category", "status") and val is not None:
                        goal[key] = val
                goal["updated_at"] = _now()
                save_state(state)
                return goal
    return None


def delete_goal(goal_id: str) -> bool:
    with _lock:
        state = load_state()
        before = len(state["goals"])
        state["goals"] = [g for g in state["goals"] if g.get("id") != goal_id]
        if len(state["goals"]) == before:
            return False
        save_state(state)
        return True


def get_chat_history(limit: int = 100) -> list[dict[str, Any]]:
    with _lock:
        history = load_state().get("chat_history", [])
        return history[-limit:]


def append_chat(role: str, content: str) -> dict[str, Any]:
    msg = {"role": role, "content": content, "timestamp": _now()}
    with _lock:
        state = load_state()
        state.setdefault("chat_history", []).append(msg)
        if len(state["chat_history"]) > 500:
            state["chat_history"] = state["chat_history"][-500:]
        save_state(state)
    return msg


def clear_chat() -> None:
    with _lock:
        state = load_state()
        state["chat_history"] = []
        save_state(state)


def get_insights_cache() -> dict[str, Any] | None:
    with _lock:
        return load_state().get("insights_cache")


def set_insights_cache(text: str) -> dict[str, Any]:
    entry = {"text": text, "generated_at": _now()}
    with _lock:
        state = load_state()
        state["insights_cache"] = entry
        save_state(state)
    return entry
