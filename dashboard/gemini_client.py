"""Gemini API client with function calling over Garmin fitness tools."""

from __future__ import annotations

import json
import re
from typing import Any

from dashboard import config
from dashboard import garmin_data
from dashboard.tools import TOOL_SCHEMAS, execute_tool

try:
    from google import genai
    from google.genai import types
    from google.genai import errors as genai_errors

    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False
    genai_errors = None  # type: ignore[assignment,misc]


SYSTEM_INSTRUCTION = """You are a knowledgeable running and fitness coach with access to the user's
Garmin Connect data exported locally. Use the provided tools to read their metrics, activities,
training data, and personal goals before answering. Be specific with numbers and dates when available.
Do not infer workout type from activity names or garmin_label fields — those are user-entered titles.
Instead call get_recent_activities, get_activity_detail, or get_workout_breakdown and analyze
inferred_structure, lap distances, paces, durations, and heart rate.
When the user asks about a workout, intervals, repeats, splits, or pacing, fetch activity details
yourself (by date or activity_id) instead of asking them to describe the session.
If export data is missing, say so and suggest running garmin_export.py --update.
Do not invent metrics you cannot verify from tool results."""


def _function_declarations() -> list[Any]:
    """Convert TOOL_SCHEMAS to Gemini FunctionDeclaration objects."""
    return [
        types.FunctionDeclaration(
            name=schema["name"],
            description=schema["description"],
            parameters=schema["parameters"],
        )
        for schema in TOOL_SCHEMAS
    ]


def _require_client():
    if not HAS_GENAI:
        raise RuntimeError("Install google-genai: pip install google-genai")
    config.load_env()
    if not config.GEMINI_API_KEY:
        raise RuntimeError("Set GEMINI_API_KEY in azimuth/.env")
    return genai.Client(api_key=config.GEMINI_API_KEY)


def _friendly_api_error(exc: Exception) -> RuntimeError:
    status = getattr(exc, "status_code", None)
    msg = str(exc)

    if status == 429 or "RESOURCE_EXHAUSTED" in msg or "429" in msg:
        retry = ""
        match = re.search(r"retry in ([0-9.]+)s", msg, re.I)
        if match:
            retry = f" Retry in about {int(float(match.group(1)))} seconds."

        if re.search(r"limit:\s*0\b", msg):
            return RuntimeError(
                f"Model '{config.GEMINI_MODEL}' has no free-tier quota on your Google "
                "project (limit: 0) — this is not caused by a single API call. "
                "Set GEMINI_MODEL=gemini-2.5-flash-lite in azimuth/.env and restart "
                "the dashboard, or check live limits at https://ai.dev/rate-limit"
            )

        return RuntimeError(
            f"Gemini API quota exceeded for model '{config.GEMINI_MODEL}'.{retry} "
            "Options: wait and try again; try GEMINI_MODEL=gemini-2.5-flash-lite; "
            "or enable billing at https://ai.google.dev"
        )

    if status == 404 or "NOT_FOUND" in msg:
        return RuntimeError(
            f"Gemini model '{config.GEMINI_MODEL}' not found or unavailable. "
            "Try GEMINI_MODEL=gemini-2.5-flash-lite in .env"
        )

    if status == 400 or "API_KEY_INVALID" in msg or "API key not valid" in msg:
        return RuntimeError("Invalid GEMINI_API_KEY — check your key at https://aistudio.google.com/apikey")

    return RuntimeError(f"Gemini API error: {msg[:300]}")


def _generate(client: Any, contents: list[Any], *, use_tools: bool) -> str:
    if use_tools:
        tools = [types.Tool(function_declarations=_function_declarations())]
        gen_config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=tools,
            temperature=0.4,
        )
    else:
        gen_config = types.GenerateContentConfig(temperature=0.4)

    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=contents,
            config=gen_config,
        )
    except Exception as exc:
        if genai_errors and isinstance(exc, genai_errors.APIError):
            raise _friendly_api_error(exc) from exc
        raise _friendly_api_error(exc) from exc

    return (response.text or "").strip() or "I couldn't generate a response."


def _run_tool_loop(client: Any, contents: list[Any], max_rounds: int = 5) -> str:
    tools = [types.Tool(function_declarations=_function_declarations())]
    gen_config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=tools,
        temperature=0.4,
    )

    for _ in range(max_rounds):
        try:
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=contents,
                config=gen_config,
            )
        except Exception as exc:
            if genai_errors and isinstance(exc, genai_errors.APIError):
                raise _friendly_api_error(exc) from exc
            raise _friendly_api_error(exc) from exc

        candidate = response.candidates[0] if response.candidates else None
        if not candidate or not candidate.content or not candidate.content.parts:
            return response.text or "I couldn't generate a response."

        function_calls = [p.function_call for p in candidate.content.parts if p.function_call]
        if not function_calls:
            text_parts = [p.text for p in candidate.content.parts if p.text]
            return "\n".join(text_parts).strip() or "Done."

        contents.append(candidate.content)
        tool_responses = []
        for fc in function_calls:
            args = dict(fc.args) if fc.args else {}
            result = execute_tool(fc.name, args)
            tool_responses.append(
                types.Part.from_function_response(name=fc.name, response={"result": result})
            )
        contents.append(types.Content(role="user", parts=tool_responses))

    return "I hit the tool call limit. Please try a simpler question."


def generate_insights(goals: list[dict[str, Any]]) -> str:
    """Single API call — injects fitness summary directly to save quota."""
    client = _require_client()
    summary = garmin_data.fitness_summary(30)
    prompt = (
        "Analyze my recent training progress using the Garmin data below and my personal goals. "
        "Cover: consistency, volume trends, recovery signals (sleep/stress if available), "
        "and progress toward goals. Give 3-5 actionable bullet points.\n\n"
        f"Garmin data (last 30 days):\n{json.dumps(summary, indent=2, default=str)}\n\n"
        f"My personal goals:\n{json.dumps(goals, indent=2)}"
    )
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
    return _generate(client, contents, use_tools=False)


def chat(user_message: str, history: list[dict[str, Any]]) -> str:
    client = _require_client()
    contents: list[Any] = []
    for msg in history[-20:]:
        role = "user" if msg.get("role") == "user" else "model"
        contents.append(
            types.Content(role=role, parts=[types.Part.from_text(text=msg.get("content", ""))])
        )
    contents.append(
        types.Content(role="user", parts=[types.Part.from_text(text=user_message)])
    )
    return _run_tool_loop(client, contents)
