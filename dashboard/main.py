"""Fitness dashboard API — localhost server for Garmin data + Gemini coach."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dashboard import gemini_client, garmin_data, storage
from dashboard.config import (
    GARMIN_EXPORT_SCRIPT,
    HOST,
    PORT,
    load_env,
)

load_env()

app = FastAPI(title="Azimuth Fitness Dashboard", version="1.0.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"

_export_lock = threading.Lock()
_export_running = False


class GoalCreate(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    target_date: str | None = None
    category: str = "general"


class GoalUpdate(BaseModel):
    text: str | None = None
    target_date: str | None = None
    category: str | None = None
    status: str | None = None


class ChatMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@app.get("/api/status")
def api_status():
    return garmin_data.export_status()


@app.get("/api/summary")
def api_summary(days: int = 30):
    days = max(1, min(days, 365))
    return garmin_data.fitness_summary(days)


@app.get("/api/metrics/timeseries")
def api_timeseries(days: int = 30):
    days = max(1, min(days, 365))
    return {
        "period_days": days,
        "series": garmin_data.daily_metrics_series(days),
        "trends": garmin_data.metrics_trends(days),
        "weekly_volume": garmin_data.weekly_activity_volume(8),
    }


@app.get("/api/activities/{activity_id}")
def api_activity(activity_id: str):
    details = garmin_data.activity_details(activity_id)
    if "error" in details:
        raise HTTPException(404, details["error"])
    return details


@app.get("/api/activities")
def api_activities(limit: int = 20):
    limit = max(1, min(limit, 100))
    return {"activities": garmin_data.list_activities(limit)}


@app.get("/api/goals")
def api_list_goals():
    return {"goals": storage.list_goals()}


@app.post("/api/goals")
def api_add_goal(body: GoalCreate):
    goal = storage.add_goal(body.text, body.target_date, body.category)
    return goal


@app.patch("/api/goals/{goal_id}")
def api_update_goal(goal_id: str, body: GoalUpdate):
    updated = storage.update_goal(
        goal_id,
        text=body.text,
        target_date=body.target_date,
        category=body.category,
        status=body.status,
    )
    if updated is None:
        raise HTTPException(404, "Goal not found")
    return updated


@app.delete("/api/goals/{goal_id}")
def api_delete_goal(goal_id: str):
    if not storage.delete_goal(goal_id):
        raise HTTPException(404, "Goal not found")
    return {"deleted": True}


@app.get("/api/chat")
def api_get_chat():
    return {"history": storage.get_chat_history()}


@app.delete("/api/chat")
def api_clear_chat():
    storage.clear_chat()
    return {"cleared": True}


@app.post("/api/chat")
def api_chat(body: ChatMessage):
    try:
        history = storage.get_chat_history()
        storage.append_chat("user", body.message)
        reply = gemini_client.chat(body.message, history)
        storage.append_chat("assistant", reply)
        return {"reply": reply}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"AI request failed: {exc}") from exc


@app.get("/api/insights")
def api_get_insights():
    cached = storage.get_insights_cache()
    return {"insights": cached}


@app.post("/api/insights")
def api_generate_insights():
    try:
        goals = storage.list_goals()
        text = gemini_client.generate_insights(goals)
        entry = storage.set_insights_cache(text)
        return {"insights": entry}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"AI request failed: {exc}") from exc


@app.post("/api/export/update")
def api_run_export():
    global _export_running
    if not GARMIN_EXPORT_SCRIPT.exists():
        raise HTTPException(500, f"Export script not found: {GARMIN_EXPORT_SCRIPT}")

    with _export_lock:
        if _export_running:
            return {"status": "already_running"}
        _export_running = True

    def _run():
        global _export_running
        try:
            subprocess.run(
                [sys.executable, str(GARMIN_EXPORT_SCRIPT), "--update"],
                cwd=str(GARMIN_EXPORT_SCRIPT.parent),
                check=False,
            )
        finally:
            with _export_lock:
                _export_running = False

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main():
    import uvicorn

    print(f"Azimuth Fitness Dashboard: http://{HOST}:{PORT}")
    uvicorn.run("dashboard.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
