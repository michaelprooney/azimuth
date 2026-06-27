# Azimuth Fitness Dashboard

Localhost dashboard that reads your `garmin_export.py` cache, tracks personal
fitness goals, chats with a Gemini-powered coach, and exposes MCP tools for
agents.

## Architecture

```
garmin_export.py  →  export/.cache/*.json
                           ↓
              dashboard (FastAPI @ localhost:8765)
                     ├── Metrics & activities UI
                     ├── Goals (persisted in dashboard/data/)
                     ├── Chat history (persisted)
                     ├── Gemini insights + chat (function calling)
                     └── MCP server (dashboard/mcp_server.py)
```

## Setup

From the `azimuth` directory (same venv as garmin_export):

```bash
pip install -r dashboard/requirements.txt
```

Add to `.env`:

```
GEMINI_API_KEY=your-key-here
# optional:
GEMINI_MODEL=gemini-2.0-flash
DASHBOARD_PORT=8765
```

You need Garmin export data first:

```bash
python garmin_export.py --all --compact
# or incremental:
python garmin_export.py --update
```

## Run the dashboard

```bash
python -m dashboard
```

Open **http://127.0.0.1:8765**

## MCP server (for Cursor / Gemini agents)

```bash
python -m dashboard.mcp_server
```

Cursor MCP config example:

```json
{
  "mcpServers": {
    "garmin-fitness": {
      "command": "python",
      "args": ["-m", "dashboard.mcp_server"],
      "cwd": "C:/JQR/azimuth"
    }
  }
}
```

### MCP tools

| Tool | Description |
|------|-------------|
| `get_export_status` | Cache file counts, date range |
| `get_fitness_summary` | Aggregated metrics for N days |
| `get_daily_health` | Per-day metrics between dates |
| `get_recent_activities` | Activity list |
| `get_activity_detail` | Full activity JSON |
| `get_training_metrics` | VO2 max, readiness, etc. |
| `get_user_goals` | Personal goals from dashboard |
| `add_user_goal` | Add a goal |
| `update_user_goal` | Update goal status/text |
| `delete_user_goal` | Remove a goal |

## Persistence

Stored in `dashboard/data/user_state.json` (gitignored):

- Personal fitness goals
- Chat history (last 500 messages)
- Cached AI insights text

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/status` | Export cache status |
| GET | `/api/summary?days=30` | Dashboard metrics |
| GET/POST/PATCH/DELETE | `/api/goals` | Goal CRUD |
| GET/POST/DELETE | `/api/chat` | Chat history & send |
| GET/POST | `/api/insights` | AI training analysis |
| POST | `/api/export/update` | Run `garmin_export.py --update` |
