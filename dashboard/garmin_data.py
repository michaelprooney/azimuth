"""Read and summarize Garmin export cache data."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from dashboard.config import CACHE_DIR, EXPORT_DIR


def _deep_get(obj: Any, *keys: str, default: Any = None) -> Any:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def export_status() -> dict[str, Any]:
    cache = CACHE_DIR
    daily_dir = cache / "daily"
    activity_dir = cache / "activities"
    section_dir = cache / "sections"

    daily_files = list(daily_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json")) if daily_dir.exists() else []
    activity_files = list(activity_dir.glob("*.json")) if activity_dir.exists() else []
    section_files = list(section_dir.glob("*.json")) if section_dir.exists() else []

    export_files = sorted(EXPORT_DIR.glob("garmin_export_*.txt"), reverse=True) if EXPORT_DIR.exists() else []
    latest_export = export_files[0].name if export_files else None
    latest_mtime = export_files[0].stat().st_mtime if export_files else None

    dates = sorted(p.stem for p in daily_files)
    return {
        "export_dir": str(EXPORT_DIR),
        "cache_exists": cache.exists(),
        "daily_days": len(daily_files),
        "activities": len(activity_files),
        "sections": len(section_files),
        "date_range": {"start": dates[0], "end": dates[-1]} if dates else None,
        "latest_export_file": latest_export,
        "latest_export_time": datetime.fromtimestamp(latest_mtime).isoformat() if latest_mtime else None,
    }


def list_daily_dates(limit: int = 90) -> list[str]:
    daily_dir = CACHE_DIR / "daily"
    if not daily_dir.exists():
        return []
    dates = []
    for path in daily_dir.glob("*.json"):
        name = path.stem
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            dates.append(name)
    return sorted(dates, reverse=True)[:limit]


def load_day(date_str: str) -> dict[str, Any] | None:
    path = CACHE_DIR / "daily" / f"{date_str}.json"
    if not path.exists():
        return None
    data = _load_json(path)
    return data if isinstance(data, dict) else None


def _extract_day_metrics(day: dict[str, Any]) -> dict[str, Any]:
    summary = day.get("summary") or {}
    sleep = day.get("sleep") or {}
    stress = day.get("stress") or {}
    rhr = day.get("rhr") or {}
    intensity = day.get("intensity_min") or {}

    sleep_dto = sleep.get("dailySleepDTO") or sleep.get("sleepData") or sleep
    stress_vals = stress.get("stressValuesArray") or stress.get("values") or []

    steps = (
        summary.get("totalSteps")
        or _deep_get(summary, "stats", "totalSteps")
        or summary.get("steps")
    )
    distance_m = summary.get("totalDistanceMeters") or summary.get("distance")
    calories = summary.get("activeKilocalories") or summary.get("totalKilocalories")
    resting_hr = (
        rhr.get("restingHeartRate")
        or rhr.get("value")
        or summary.get("restingHeartRate")
    )
    sleep_seconds = (
        sleep_dto.get("sleepTimeSeconds")
        or sleep_dto.get("totalSleepSeconds")
        or sleep.get("sleepTimeSeconds")
    )
    avg_stress = stress.get("avgStressLevel") or stress.get("averageStressLevel")
    if avg_stress is None and isinstance(stress_vals, list) and stress_vals:
        nums = [v[1] for v in stress_vals if isinstance(v, (list, tuple)) and len(v) > 1]
        if nums:
            avg_stress = round(sum(nums) / len(nums), 1)

    moderate = intensity.get("moderateMinutes") or intensity.get("moderateIntensityMinutes")
    vigorous = intensity.get("vigorousMinutes") or intensity.get("vigorousIntensityMinutes")

    body_battery = _extract_body_battery(day.get("body_battery"))

    return {
        "steps": steps,
        "distance_km": round(distance_m / 1000, 2) if isinstance(distance_m, (int, float)) else None,
        "active_calories": calories,
        "resting_hr": resting_hr,
        "sleep_hours": round(sleep_seconds / 3600, 2) if isinstance(sleep_seconds, (int, float)) else None,
        "avg_stress": avg_stress,
        "moderate_minutes": moderate,
        "vigorous_minutes": vigorous,
        "body_battery_high": body_battery.get("high"),
        "body_battery_low": body_battery.get("low"),
        "body_battery_charged": body_battery.get("charged"),
        "body_battery_drained": body_battery.get("drained"),
    }


def _extract_body_battery(raw: Any) -> dict[str, int | None]:
    if not raw:
        return {"high": None, "low": None, "charged": None, "drained": None}
    entry = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(entry, dict):
        return {"high": None, "low": None, "charged": None, "drained": None}

    values = entry.get("bodyBatteryValuesArray") or []
    nums = [v[1] for v in values if isinstance(v, (list, tuple)) and len(v) > 1 and isinstance(v[1], (int, float))]
    return {
        "high": max(nums) if nums else None,
        "low": min(nums) if nums else None,
        "charged": entry.get("charged"),
        "drained": entry.get("drained"),
    }


def daily_metrics_series(days: int = 30, *, end_offset: int = 0) -> list[dict[str, Any]]:
    end = date.today() - timedelta(days=end_offset)
    start = end - timedelta(days=days - 1)
    series = []
    d = start
    while d <= end:
        ds = d.isoformat()
        raw = load_day(ds)
        metrics = _extract_day_metrics(raw) if raw else {}
        metrics["date"] = ds
        series.append(metrics)
        d += timedelta(days=1)
    return series


def load_section(name: str) -> Any | None:
    path = CACHE_DIR / "sections" / f"{name}.json"
    if not path.exists():
        return None
    return _load_json(path)


def list_activities(limit: int = 20) -> list[dict[str, Any]]:
    activity_dir = CACHE_DIR / "activities"
    if not activity_dir.exists():
        return []

    activities = []
    for path in activity_dir.glob("*.json"):
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        summary = data.get("summary") or {}
        detail = data.get("detail") or {}
        merged = {**detail, **summary} if isinstance(detail, dict) else summary
        activities.append(
            {
                "activity_id": summary.get("activityId") or path.stem,
                "garmin_label": merged.get("activityName") or "Unnamed",
                "name": merged.get("activityName") or "Unnamed",
                "type": _deep_get(merged, "activityType", "typeKey") or merged.get("activityType"),
                "start_time": merged.get("startTimeLocal") or merged.get("startTimeGMT"),
                "distance_km": _activity_distance_km(merged),
                "duration_min": _activity_duration_min(merged),
                "avg_hr": merged.get("averageHR") or merged.get("avgHeartRate"),
                "calories": merged.get("calories") or merged.get("activeKilocalories"),
                "structure": _quick_structure(data),
            }
        )

    activities.sort(key=lambda a: a.get("start_time") or "", reverse=True)
    return activities[:limit]


def _activity_distance_km(act: dict[str, Any]) -> float | None:
    dist = act.get("distance") or act.get("distanceMeters")
    if isinstance(dist, (int, float)):
        return round(dist / 1000, 2) if dist > 500 else round(dist, 2)
    return None


def _activity_duration_min(act: dict[str, Any]) -> float | None:
    dur = act.get("duration") or act.get("movingDuration") or act.get("elapsedDuration")
    if isinstance(dur, (int, float)):
        return round(dur / 60, 1) if dur > 500 else round(dur, 1)
    return None


def load_activity(activity_id: str) -> dict[str, Any] | None:
    path = CACHE_DIR / "activities" / f"{activity_id}.json"
    if not path.exists():
        return None
    data = _load_json(path)
    return data if isinstance(data, dict) else None


def _activity_record(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary") or {}
    detail = data.get("detail") or {}
    return {**detail, **summary} if isinstance(detail, dict) else summary


def _speed_to_pace_min_per_mile(speed_mps: float | None) -> float | None:
    if not isinstance(speed_mps, (int, float)) or speed_mps <= 0.5:
        return None
    return round((1609.34 / speed_mps) / 60, 2)


def _format_duration(seconds: float | None) -> str | None:
    if not isinstance(seconds, (int, float)):
        return None
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _classify_lap(lap: dict[str, Any], *, index: int, total: int) -> str:
    step = lap.get("wktStepIndex")
    distance = lap.get("distance") or 0
    duration = lap.get("movingDuration") or lap.get("duration") or 0

    if step == 0:
        return "warmup"
    if step == 1:
        return "work"
    if step == 2:
        return "recovery"

    if isinstance(distance, (int, float)) and distance >= 1400:
        return "work"
    if isinstance(distance, (int, float)) and distance < 250 and duration >= 60:
        return "recovery"
    if index == 0 and isinstance(distance, (int, float)) and distance < 1400:
        return "warmup"
    if index == total - 1 and isinstance(distance, (int, float)) and distance < 500:
        return "cooldown"
    return "other"


def _compact_lap(lap: dict[str, Any], *, index: int, total: int) -> dict[str, Any]:
    moving = lap.get("movingDuration") or lap.get("duration")
    speed = lap.get("averageSpeed") or lap.get("averageMovingSpeed")
    distance = lap.get("distance")
    return {
        "lap": index + 1,
        "segment": _classify_lap(lap, index=index, total=total),
        "distance_m": round(distance, 1) if isinstance(distance, (int, float)) else None,
        "distance_km": round(distance / 1000, 2) if isinstance(distance, (int, float)) else None,
        "duration_sec": round(moving, 1) if isinstance(moving, (int, float)) else None,
        "duration": _format_duration(moving),
        "pace_min_per_mile": _speed_to_pace_min_per_mile(speed),
        "avg_hr": lap.get("averageHR"),
        "max_hr": lap.get("maxHR"),
        "workout_step_index": lap.get("wktStepIndex"),
    }


def _analyze_laps(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    laps_raw = _deep_get(data, "splits", "lapDTOs") or []
    if not isinstance(laps_raw, list):
        laps_raw = []
    all_laps = [_compact_lap(lap, index=i, total=len(laps_raw)) for i, lap in enumerate(laps_raw)]
    work = [lap for lap in all_laps if lap["segment"] == "work"]
    recovery = [lap for lap in all_laps if lap["segment"] == "recovery"]
    return all_laps, work, recovery


def _infer_workout_structure(
    work: list[dict[str, Any]],
    recovery: list[dict[str, Any]],
    all_laps: list[dict[str, Any]],
) -> dict[str, Any]:
    if not all_laps:
        return {
            "pattern": "no_lap_data",
            "description": "No lap data recorded for this activity.",
            "work_segment_count": 0,
            "recovery_segment_count": 0,
            "total_laps": 0,
        }

    work_distances = [lap["distance_m"] for lap in work if isinstance(lap.get("distance_m"), (int, float))]
    avg_work_dist = round(sum(work_distances) / len(work_distances)) if work_distances else None
    recovery_durations = [
        lap["duration_sec"] for lap in recovery if isinstance(lap.get("duration_sec"), (int, float))
    ]
    avg_recovery_sec = round(sum(recovery_durations) / len(recovery_durations)) if recovery_durations else None

    pattern = "continuous"
    description = "Single sustained effort or unstructured session."

    if len(work) >= 2:
        if avg_work_dist and 1350 <= avg_work_dist <= 1750:
            pattern = "mile_repeats"
            description = (
                f"{len(work)} work segments averaging {avg_work_dist} m "
                f"with {len(recovery)} recovery segments."
            )
        elif avg_work_dist and 350 <= avg_work_dist <= 450:
            pattern = "400m_intervals"
            description = f"{len(work)} work segments averaging {avg_work_dist} m."
        elif avg_work_dist and 750 <= avg_work_dist <= 850:
            pattern = "800m_intervals"
            description = f"{len(work)} work segments averaging {avg_work_dist} m."
        elif avg_work_dist and 950 <= avg_work_dist <= 1050:
            pattern = "kilometer_repeats"
            description = f"{len(work)} work segments averaging {avg_work_dist} m."
        else:
            pattern = "structured_intervals"
            description = (
                f"{len(work)} work segments"
                + (f" averaging {avg_work_dist} m." if avg_work_dist else ".")
            )
    elif len(work) == 1:
        pattern = "single_hard_effort"
        if avg_work_dist:
            description = f"One hard segment of {avg_work_dist} m."
        else:
            description = "One hard segment identified from lap data."
    elif len(all_laps) >= 2:
        pattern = "multi_lap_unstructured"
        description = f"{len(all_laps)} laps without clear work/recovery alternation."

    return {
        "pattern": pattern,
        "description": description,
        "work_segment_count": len(work),
        "recovery_segment_count": len(recovery),
        "avg_work_distance_m": avg_work_dist,
        "avg_recovery_duration_sec": avg_recovery_sec,
        "total_laps": len(all_laps),
    }


def _quick_structure(data: dict[str, Any]) -> dict[str, Any]:
    all_laps, work, recovery = _analyze_laps(data)
    return _infer_workout_structure(work, recovery, all_laps)


def _compact_hr_zones(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    zones = data.get("hr_zones")
    if not isinstance(zones, list):
        return None
    compact = []
    for zone in zones[:10]:
        if not isinstance(zone, dict):
            continue
        compact.append(
            {
                "zone": zone.get("zoneNumber") or zone.get("zone"),
                "seconds": zone.get("secsInZone") or zone.get("secondsInZone"),
                "low_bpm": zone.get("zoneLowBoundary") or zone.get("low"),
                "high_bpm": zone.get("zoneHighBoundary") or zone.get("high"),
            }
        )
    return compact or None


def _compact_split_summaries(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    summaries = _deep_get(data, "split_summaries", "splitSummaries")
    if not isinstance(summaries, list):
        return None
    compact = []
    for item in summaries[:12]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "distance_km": round(item["distance"] / 1000, 2)
                if isinstance(item.get("distance"), (int, float))
                else None,
                "duration_min": round(item["duration"] / 60, 1)
                if isinstance(item.get("duration"), (int, float))
                else None,
                "avg_pace_min_per_mile": _speed_to_pace_min_per_mile(
                    item.get("averageSpeed") or item.get("averageMovingSpeed")
                ),
                "avg_hr": item.get("averageHR"),
                "max_hr": item.get("maxHR"),
            }
        )
    return compact or None


def _training_load_fields(merged: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "activityTrainingLoad",
        "aerobicTrainingEffect",
        "anaerobicTrainingEffect",
        "aerobicTrainingEffectMessage",
        "anaerobicTrainingEffectMessage",
        "trainingEffectLabel",
        "trainingLoadBalance",
    )
    out = {key: merged.get(key) for key in fields if merged.get(key) is not None}
    for idx in range(1, 6):
        key = f"hrTimeInZone_{idx}"
        if merged.get(key) is not None:
            out[key] = merged[key]
    return out


def activity_details(activity_id: str) -> dict[str, Any]:
    """Structured activity data for coach analysis — inferred from laps/metrics, not titles."""
    data = load_activity(activity_id)
    if data is None:
        return {"error": f"No activity found for id {activity_id}"}

    merged = _activity_record(data)
    all_laps, work, recovery = _analyze_laps(data)
    inferred = _infer_workout_structure(work, recovery, all_laps)
    work_paces = [lap["pace_min_per_mile"] for lap in work if lap.get("pace_min_per_mile") is not None]
    avg_speed = merged.get("averageSpeed") or merged.get("avgSpeed")

    return {
        "activity_id": str(merged.get("activityId") or activity_id),
        "garmin_label": merged.get("activityName") or "Unnamed",
        "note": "garmin_label is user-entered and may be inaccurate; infer workout type from structure and laps.",
        "activity_type": _deep_get(merged, "activityType", "typeKey") or merged.get("activityType"),
        "start_time": merged.get("startTimeLocal") or merged.get("startTimeGMT"),
        "device": merged.get("deviceName") or _deep_get(merged, "metadataDTO", "deviceName"),
        "summary": {
            "distance_km": _activity_distance_km(merged),
            "duration_min": _activity_duration_min(merged),
            "moving_duration_min": _activity_duration_min(
                {"duration": merged.get("movingDuration"), "movingDuration": merged.get("movingDuration")}
            ),
            "avg_hr": merged.get("averageHR") or merged.get("avgHeartRate"),
            "max_hr": merged.get("maxHR") or merged.get("maxHeartRate"),
            "avg_pace_min_per_mile": _speed_to_pace_min_per_mile(avg_speed),
            "max_pace_min_per_mile": _speed_to_pace_min_per_mile(
                merged.get("maxSpeed") or merged.get("maxMovingSpeed")
            ),
            "calories": merged.get("calories") or merged.get("activeKilocalories"),
            "elevation_gain_m": merged.get("elevationGain") or merged.get("totalElevationGain"),
        },
        "training_load": _training_load_fields(merged),
        "hr_zones": _compact_hr_zones(data),
        "split_summaries": _compact_split_summaries(data),
        "inferred_structure": inferred,
        "work_intervals": work,
        "recovery_periods": recovery,
        "all_laps": all_laps,
        "pace_summary": {
            "work_interval_count": len(work),
            "avg_work_pace_min_per_mile": round(sum(work_paces) / len(work_paces), 2) if work_paces else None,
            "best_work_pace_min_per_mile": min(work_paces) if work_paces else None,
            "slowest_work_pace_min_per_mile": max(work_paces) if work_paces else None,
        },
    }


def search_activities(
    *,
    date_str: str | None = None,
    activity_type: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Find cached activities by local date and/or activity type."""
    activity_dir = CACHE_DIR / "activities"
    if not activity_dir.exists():
        return []

    matches: list[dict[str, Any]] = []
    type_lower = activity_type.lower() if activity_type else None

    for path in activity_dir.glob("*.json"):
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        merged = _activity_record(data)
        start_time = merged.get("startTimeLocal") or merged.get("startTimeGMT") or ""
        act_type = _deep_get(merged, "activityType", "typeKey") or merged.get("activityType") or ""

        if date_str and not str(start_time).startswith(date_str):
            continue
        if type_lower and type_lower not in str(act_type).lower():
            continue

        matches.append(
            {
                "activity_id": str(merged.get("activityId") or path.stem),
                "garmin_label": merged.get("activityName") or "Unnamed",
                "name": merged.get("activityName") or "Unnamed",
                "activity_type": act_type,
                "start_time": start_time,
                "distance_km": _activity_distance_km(merged),
                "duration_min": _activity_duration_min(merged),
                "avg_hr": merged.get("averageHR") or merged.get("avgHeartRate"),
                "structure": _quick_structure(data),
            }
        )

    matches.sort(key=lambda a: a.get("start_time") or "", reverse=True)
    return matches[:limit]


def workout_breakdown(activity_id: str) -> dict[str, Any]:
    """Summarize laps/intervals, paces, and HR for a structured workout."""
    details = activity_details(activity_id)
    if "error" in details:
        return details

    return {
        "activity_id": details["activity_id"],
        "garmin_label": details["garmin_label"],
        "activity_type": details["activity_type"],
        "start_time": details["start_time"],
        "inferred_structure": details["inferred_structure"],
        "summary": details["summary"],
        "work_intervals": details["work_intervals"],
        "recovery_periods": details["recovery_periods"],
        "all_laps": details["all_laps"],
        "pace_summary": details["pace_summary"],
    }


def resolve_workout_breakdown(
    *,
    activity_id: str | None = None,
    date_str: str | None = None,
    activity_type: str | None = None,
) -> dict[str, Any]:
    """Find an activity and return its workout breakdown."""
    if activity_id:
        return workout_breakdown(activity_id)

    matches = search_activities(date_str=date_str, activity_type=activity_type, limit=10)
    if not matches:
        hint = []
        if date_str:
            hint.append(f"date={date_str}")
        if activity_type:
            hint.append(f"type={activity_type}")
        return {"error": "No matching activities found", "search": ", ".join(hint) or "no filters"}

    if len(matches) == 1:
        return workout_breakdown(str(matches[0]["activity_id"]))

    return {
        "message": (
            "Multiple activities matched. Choose by activity_id using inferred_structure "
            "and lap details below — do not rely on garmin_label."
        ),
        "activities": [activity_details(str(match["activity_id"])) for match in matches],
    }


def _period_averages(series: list[dict[str, Any]]) -> dict[str, float | None]:
    populated = [d for d in series if any(v is not None for k, v in d.items() if k != "date")]

    def avg(field: str) -> float | None:
        vals = [d[field] for d in populated if d.get(field) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    def total(field: str) -> float | None:
        vals = [d[field] for d in populated if d.get(field) is not None]
        return round(sum(vals), 2) if vals else None

    return {
        "days_with_data": len(populated),
        "steps": avg("steps"),
        "sleep_hours": avg("sleep_hours"),
        "resting_hr": avg("resting_hr"),
        "avg_stress": avg("avg_stress"),
        "active_calories": avg("active_calories"),
        "total_steps": total("steps"),
        "moderate_minutes": total("moderate_minutes"),
        "vigorous_minutes": total("vigorous_minutes"),
    }


def _trend_delta(current: float | None, previous: float | None, *, lower_is_better: bool = False) -> dict[str, Any] | None:
    if current is None or previous is None or previous == 0:
        return None
    pct = round(((current - previous) / abs(previous)) * 100, 1)
    improved = pct < 0 if lower_is_better else pct > 0
    return {"pct": pct, "direction": "up" if pct > 0 else "down" if pct < 0 else "flat", "improved": improved}


def metrics_trends(days: int = 30) -> dict[str, Any]:
    current = daily_metrics_series(days)
    previous = daily_metrics_series(days, end_offset=days)
    cur = _period_averages(current)
    prev = _period_averages(previous)
    return {
        "period_days": days,
        "current": cur,
        "previous": prev,
        "trends": {
            "steps": _trend_delta(cur.get("steps"), prev.get("steps")),
            "sleep_hours": _trend_delta(cur.get("sleep_hours"), prev.get("sleep_hours")),
            "resting_hr": _trend_delta(cur.get("resting_hr"), prev.get("resting_hr"), lower_is_better=True),
            "avg_stress": _trend_delta(cur.get("avg_stress"), prev.get("avg_stress"), lower_is_better=True),
            "active_calories": _trend_delta(cur.get("active_calories"), prev.get("active_calories")),
        },
    }


def today_snapshot() -> dict[str, Any]:
    today = date.today().isoformat()
    raw = load_day(today)
    metrics = _extract_day_metrics(raw) if raw else {}
    metrics["date"] = today
    return {
        "date": today,
        "metrics": metrics,
        "has_data": bool(raw),
    }


def weekly_activity_volume(weeks: int = 8) -> list[dict[str, Any]]:
    activity_dir = CACHE_DIR / "activities"
    if not activity_dir.exists():
        return []

    buckets: dict[str, dict[str, Any]] = {}
    for path in activity_dir.glob("*.json"):
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        merged = _activity_record(data)
        start_time = merged.get("startTimeLocal") or merged.get("startTimeGMT") or ""
        if not start_time:
            continue
        try:
            act_date = datetime.fromisoformat(str(start_time).replace(" ", "T")[:19]).date()
        except ValueError:
            continue
        iso = act_date.isocalendar()
        week_key = f"{iso.year}-W{iso.week:02d}"
        bucket = buckets.setdefault(
            week_key,
            {"week": week_key, "start_date": act_date.isoformat(), "count": 0, "distance_km": 0.0, "duration_min": 0.0},
        )
        bucket["count"] += 1
        dist = _activity_distance_km(merged)
        dur = _activity_duration_min(merged)
        if dist:
            bucket["distance_km"] = round(bucket["distance_km"] + dist, 2)
        if dur:
            bucket["duration_min"] = round(bucket["duration_min"] + dur, 1)

    ordered = sorted(buckets.values(), key=lambda b: b["week"])
    return ordered[-weeks:]


def fitness_summary(days: int = 30) -> dict[str, Any]:
    series = daily_metrics_series(days)
    stats = _period_averages(series)
    trends = metrics_trends(days)
    recent_activities = list_activities(limit=15)
    training = load_section("training") or {}
    goals_section = load_section("goals") or {}

    return {
        "period_days": days,
        "days_with_data": stats["days_with_data"],
        "averages": {
            "steps": stats["steps"],
            "sleep_hours": stats["sleep_hours"],
            "resting_hr": stats["resting_hr"],
            "avg_stress": stats["avg_stress"],
            "active_calories": stats["active_calories"],
        },
        "totals": {
            "steps": stats["total_steps"],
            "moderate_minutes": stats["moderate_minutes"],
            "vigorous_minutes": stats["vigorous_minutes"],
        },
        "trends": trends["trends"],
        "recent_activities": recent_activities,
        "training_highlights": _training_highlights(training),
        "garmin_goals": _compact_garmin_goals(goals_section),
        "export_status": export_status(),
        "daily_series": series,
        "today": today_snapshot(),
        "weekly_volume": weekly_activity_volume(8),
    }


def _training_highlights(training: Any) -> dict[str, Any]:
    if not isinstance(training, dict):
        return {}

    out: dict[str, Any] = {}

    fitness_age = training.get("fitness_age")
    if isinstance(fitness_age, dict):
        out["fitness_age"] = fitness_age.get("fitnessAge")
        out["chronological_age"] = fitness_age.get("chronologicalAge")

    vo2 = None
    for source in (training.get("max_metrics"), training.get("training_status")):
        if isinstance(source, list) and source:
            generic = source[0].get("generic") if isinstance(source[0], dict) else None
            if isinstance(generic, dict):
                vo2 = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
                break
        if isinstance(source, dict):
            vo2_block = source.get("mostRecentVO2Max") or source
            if isinstance(vo2_block, dict):
                generic = vo2_block.get("generic")
                if isinstance(generic, dict):
                    vo2 = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
                    break
    if vo2 is not None:
        out["vo2_max"] = vo2

    status = training.get("training_status")
    if isinstance(status, dict):
        recent = status.get("mostRecentTrainingStatus")
        if isinstance(recent, dict):
            latest = recent.get("latestTrainingStatusData")
            if isinstance(latest, dict):
                for device_data in latest.values():
                    if isinstance(device_data, dict):
                        out["training_status"] = device_data.get("trainingStatusFeedbackPhrase")
                        out["weekly_training_load"] = device_data.get("weeklyTrainingLoad")
                        out["fitness_trend"] = device_data.get("fitnessTrend")
                        break
        balance = status.get("mostRecentTrainingLoadBalance")
        if isinstance(balance, dict):
            dto_map = balance.get("metricsTrainingLoadBalanceDTOMap")
            if isinstance(dto_map, dict):
                for device_data in dto_map.values():
                    if isinstance(device_data, dict):
                        out["load_balance"] = device_data.get("trainingBalanceFeedbackPhrase")
                        break

    return out


def _compact_training(training: Any) -> dict[str, Any]:
    return _training_highlights(training)


def _compact_garmin_goals(goals: Any) -> list[dict[str, Any]]:
    if not isinstance(goals, dict):
        return []
    items = goals.get("activeGoals") or goals.get("goals") or goals.get("pastGoals") or []
    if not isinstance(items, list):
        return []
    compact = []
    for g in items[:10]:
        if isinstance(g, dict):
            compact.append({
                "name": g.get("goalName") or g.get("name"),
                "type": g.get("goalType") or g.get("type"),
                "target": g.get("targetValue") or g.get("target"),
                "current": g.get("currentValue") or g.get("current"),
            })
    return compact
