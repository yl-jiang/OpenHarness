"""Apple Health export data parser and importer.

Parses Apple Health export.xml (or 导出.xml for Chinese locale) from
either a .zip or raw .xml file. Aggregates biometric data by day and
builds SoloHealthRecord objects for import into the health_records table.

Usage:
    from solo.core.apple_health import AppleHealthImporter

    importer = AppleHealthImporter()
    result = importer.parse(file_bytes, filename="export.zip")
    records = importer.build_records(result)
"""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4
from xml.etree.ElementTree import iterparse


# ── HK type mapping ──

_HK_TYPES: dict[str, str] = {
    # Fitness
    "HKQuantityTypeIdentifierStepCount": "steps",
    "HKQuantityTypeIdentifierDistanceWalkingRunning": "distance_km",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "active_kcal",
    "HKQuantityTypeIdentifierAppleExerciseTime": "exercise_min",
    "HKQuantityTypeIdentifierAppleStandTime": "stand_min",
    # Vital signs
    "HKQuantityTypeIdentifierHeartRate": "heart_rate",
    "HKQuantityTypeIdentifierRestingHeartRate": "resting_hr",
    "HKQuantityTypeIdentifierWalkingHeartRateAverage": "walking_hr",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "hrv",
    "HKQuantityTypeIdentifierOxygenSaturation": "spo2",
    "HKQuantityTypeIdentifierRespiratoryRate": "respiratory_rate",
    "HKQuantityTypeIdentifierVO2Max": "vo2_max",
    "HKQuantityTypeIdentifierBodyMass": "weight_kg",
    "HKQuantityTypeIdentifierAppleSleepingWristTemperature": "wrist_temp",
    "HKQuantityTypeIdentifierEnvironmentalAudioExposure": "noise_db",
    # Sleep
    "HKCategoryTypeIdentifierSleepAnalysis": "sleep",
    # Mental
    "HKCategoryTypeIdentifierMindfulSession": "mindful_min",
}

_SLEEP_ASLEEP_VALUES = frozenset({
    "HKCategoryValueSleepAnalysisAsleep",
    "HKCategoryValueSleepAnalysisAsleepCore",
    "HKCategoryValueSleepAnalysisAsleepDeep",
    "HKCategoryValueSleepAnalysisAsleepREM",
})

_WORKOUT_TYPES: dict[str, str] = {
    "HKWorkoutActivityTypeRunning": "跑步",
    "HKWorkoutActivityTypeWalking": "走路",
    "HKWorkoutActivityTypeCycling": "骑行",
    "HKWorkoutActivityTypeSwimming": "游泳",
    "HKWorkoutActivityTypeFunctionalStrengthTraining": "力量训练",
    "HKWorkoutActivityTypeYoga": "瑜伽",
    "HKWorkoutActivityTypeRowing": "划船",
    "HKWorkoutActivityTypeJumpRope": "跳绳",
    "HKWorkoutActivityTypeElliptical": "椭圆机",
    "HKWorkoutActivityTypeHiking": "徒步",
}


# ── Data structures ──

@dataclass
class WorkoutData:
    activity: str
    duration: float
    distance: float
    energy: float


@dataclass
class ParseResult:
    """Result of parsing an Apple Health export file."""
    record_count: int = 0
    daily: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    workouts: dict[str, list[WorkoutData]] = field(default_factory=dict)


@dataclass
class ImportResult:
    """Result of importing parsed data into health records."""
    ok: bool = True
    error: str = ""
    parsed_records: int = 0
    dates_total: int = 0
    dates_new: int = 0
    inserted: int = 0
    date_range: str = ""
    message: str = ""
    by_type: dict[str, int] = field(default_factory=dict)


# ── Parser ──

class AppleHealthImporter:
    """Parse Apple Health export data and build health records."""

    def parse(self, file_bytes: bytes, filename: str, *, date_from: str | None = None) -> ParseResult:
        """Parse an Apple Health export file (.zip or .xml).

        For zip files, extracts the XML to a temp file first to avoid
        memory pressure from on-the-fly decompression of large files.
        Streams through the XML using iterparse for memory efficiency.
        """
        tmp_path = None
        try:
            if filename.endswith(".zip"):
                fp, tmp_path = self._extract_xml_from_zip(file_bytes)
            else:
                fp = io.BytesIO(file_bytes)

            result = ParseResult()
            daily_agg: dict = defaultdict(lambda: defaultdict(lambda: {"values": [], "count": 0, "total": 0.0}))
            workout_agg: dict = defaultdict(list)

            for _, elem in iterparse(fp, events=["end"]):
                if elem.tag == "Workout":
                    self._parse_workout(elem, workout_agg, date_from)
                elif elem.tag == "Record":
                    self._parse_record(elem, daily_agg, date_from)
                else:
                    continue
                result.record_count += 1
                elem.clear()

            fp.close()
            result.daily = dict(daily_agg)
            result.workouts = dict(workout_agg)
            return result
        finally:
            if tmp_path:
                import os
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def build_records(
        self,
        parsed: ParseResult,
    ) -> tuple[list[dict[str, Any]], ImportResult]:
        """Build health record dicts from parsed data for all dates.

        Returns (records_to_insert, import_result_summary).
        """
        daily = parsed.daily

        if not daily and not parsed.workouts:
            return [], ImportResult(
                parsed_records=parsed.record_count,
                message="No health data found in parsed export",
            )

        now = datetime.now().isoformat()
        records: list[dict[str, Any]] = []
        categories: list[str] = []

        for date_str in sorted(daily.keys()):
            metrics = daily[date_str]
            # Vital
            vital = self._build_vital(metrics)
            if vital:
                desc_parts = []
                hr = self._agg(metrics, "heart_rate")
                if hr:
                    desc_parts.append(f"心率 {hr['avg']}bpm")
                spo2 = self._agg(metrics, "spo2")
                if spo2:
                    desc_parts.append(f"血氧 {vital.get('spo2_avg', '?')}%")
                hrv = self._agg(metrics, "hrv")
                if hrv:
                    desc_parts.append(f"HRV {hrv['avg']}ms")
                records.append(self._make_record(
                    date_str, "vital", "体征数据",
                    " / ".join(desc_parts) if desc_parts else "体征数据",
                    metrics_json=vital, tags="apple_health,体征", now=now,
                ))
                categories.append("vital")

            # Fitness (daily activity)
            steps = metrics.get("steps")
            exercise = metrics.get("exercise_min")
            stand = metrics.get("stand_min")
            if steps or exercise:
                fm: dict[str, Any] = {}
                if steps:
                    fm["steps"] = int(steps["total"])
                dist = metrics.get("distance_km")
                if dist:
                    fm["distance_km"] = round(dist["total"], 2)
                energy = metrics.get("active_kcal")
                if energy:
                    fm["active_kcal"] = round(energy["total"], 1)
                if stand:
                    fm["stand_min"] = round(stand["total"], 1)
                ex_min = int(exercise["total"]) if exercise else 0
                st = fm.get("steps", 0)
                records.append(self._make_record(
                    date_str, "fitness", f"日常活动 ({st}步)",
                    f"{st}步" + (f" / {ex_min}分钟运动" if ex_min else ""),
                    exercise_type="日常活动", exercise_duration_min=ex_min,
                    exercise_intensity="moderate" if ex_min >= 30 else "low",
                    metrics_json=fm, tags="apple_health,运动,步数", now=now,
                ))
                categories.append("fitness")

            # Sleep
            sleep = metrics.get("sleep")
            if sleep and sleep["total"] > 0:
                hours = round(sleep["total"] / 60, 1)
                quality = "good" if hours >= 7 else ("fair" if hours >= 6 else "poor")
                records.append(self._make_record(
                    date_str, "sleep", "睡眠",
                    f"睡眠 {hours} 小时",
                    sleep_hours=hours, sleep_quality=quality,
                    tags="apple_health,睡眠", now=now,
                ))
                categories.append("sleep")

            # Mental
            mindful = metrics.get("mindful_min")
            if mindful and mindful["total"] > 0:
                mins = round(mindful["total"], 1)
                records.append(self._make_record(
                    date_str, "mental", "正念冥想",
                    f"正念 {mins} 分钟",
                    mood="平静", tags="apple_health,正念", now=now,
                ))
                categories.append("mental")

        # Workout records
        for date_str in sorted(parsed.workouts.keys()):
            for w in parsed.workouts[date_str]:
                w_metrics: dict[str, Any] = {}
                if w.distance:
                    w_metrics["distance_km"] = round(w.distance, 2)
                if w.energy:
                    w_metrics["active_kcal"] = round(w.energy, 1)
                dur = int(w.duration)
                desc = f"{w.activity} {dur}分钟"
                if w.distance:
                    desc += f" / {round(w.distance, 1)}km"
                records.append(self._make_record(
                    date_str, "fitness", w.activity, desc,
                    exercise_type=w.activity, exercise_duration_min=dur,
                    exercise_intensity="high" if dur >= 45 else ("moderate" if dur >= 20 else "low"),
                    metrics_json=w_metrics, tags="apple_health,运动,workout", now=now,
                ))
                categories.append("fitness")

        all_dates = set(daily.keys()) | set(parsed.workouts.keys())
        import_result = ImportResult(
            ok=True,
            parsed_records=parsed.record_count,
            dates_total=len(all_dates),
            dates_new=len(all_dates),
            inserted=len(records),
            date_range=f"{min(all_dates)} ~ {max(all_dates)}" if all_dates else "",
            message=f"导入 {len(records)} 条记录（{len(all_dates)} 天数据）",
            by_type=dict(Counter(categories)),
        )
        return records, import_result

    # ── Private helpers ──

    @staticmethod
    def _extract_xml_from_zip(file_bytes: bytes) -> tuple[io.IOBase, str]:
        """Extract the data XML from a zip to a temp file for streaming parse.

        Returns (file_handle, temp_file_path). Caller must unlink the temp file.
        Handles localized filenames (e.g. '导出.xml') and nested paths.
        """
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))

        # Find the data XML file
        xml_name = next((n for n in zf.namelist() if n.endswith("export.xml")), None)
        if not xml_name:
            candidates = [
                (n, zf.getinfo(n).file_size)
                for n in zf.namelist()
                if n.endswith(".xml") and "cda" not in n.lower()
            ]
            if candidates:
                candidates.sort(key=lambda x: x[1], reverse=True)
                xml_name = candidates[0][0]
        if not xml_name:
            raise FileNotFoundError(
                f"No XML data file found in zip. Files: {zf.namelist()[:10]}"
            )

        # Extract to temp file so iterparse can stream without holding
        # the entire decompressed content in memory
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".xml", prefix="apple_health_")
        import os
        try:
            with os.fdopen(tmp_fd, "wb") as tmp_f:
                with zf.open(xml_name) as src:
                    while True:
                        chunk = src.read(1024 * 1024)  # 1MB chunks
                        if not chunk:
                            break
                        tmp_f.write(chunk)
            return open(tmp_path, "rb"), tmp_path
        except Exception:
            os.unlink(tmp_path)
            raise

    @staticmethod
    def _parse_workout(elem: Any, workout_agg: dict, date_from: str | None) -> None:
        date_str = elem.get("startDate", "")[:10]
        if date_from and date_str < date_from:
            return
        activity_type = elem.get("workoutActivityType", "")
        activity_name = _WORKOUT_TYPES.get(activity_type)
        if not activity_name:
            suffix = activity_type.split("HKWorkoutActivityType")[-1] if "HKWorkoutActivityType" in activity_type else activity_type
            activity_name = suffix or "运动"
        try:
            duration = float(elem.get("duration", "0"))
        except (ValueError, TypeError):
            duration = 0
        try:
            distance = float(elem.get("totalDistance", "0"))
        except (ValueError, TypeError):
            distance = 0
        try:
            energy = float(elem.get("totalEnergyBurned", "0"))
        except (ValueError, TypeError):
            energy = 0
        workout_agg[date_str].append(
            WorkoutData(activity=activity_name, duration=duration, distance=distance, energy=energy)
        )

    @staticmethod
    def _parse_record(elem: Any, daily_agg: dict, date_from: str | None) -> None:
        hk_type = elem.get("type", "")
        metric = _HK_TYPES.get(hk_type)
        if metric is None:
            return
        date_str = elem.get("startDate", "")[:10]
        if date_from and date_str < date_from:
            return
        value = elem.get("value", "")

        if hk_type == "HKCategoryTypeIdentifierSleepAnalysis":
            if value in _SLEEP_ASLEEP_VALUES:
                try:
                    dt_s = AppleHealthImporter._parse_dt(elem.get("startDate", ""))
                    dt_e = AppleHealthImporter._parse_dt(elem.get("endDate", ""))
                    mins = (dt_e - dt_s).total_seconds() / 60
                    if mins > 0:
                        daily_agg[date_str]["sleep"]["total"] += mins
                        daily_agg[date_str]["sleep"]["count"] += 1
                except Exception:
                    pass
        elif hk_type == "HKCategoryTypeIdentifierMindfulSession":
            try:
                dt_s = AppleHealthImporter._parse_dt(elem.get("startDate", ""))
                dt_e = AppleHealthImporter._parse_dt(elem.get("endDate", ""))
                mins = (dt_e - dt_s).total_seconds() / 60
                if mins > 0:
                    daily_agg[date_str]["mindful_min"]["total"] += mins
                    daily_agg[date_str]["mindful_min"]["count"] += 1
            except Exception:
                pass
        else:
            try:
                fval = float(value)
            except (ValueError, TypeError):
                fval = 0.0
            bucket = daily_agg[date_str][metric]
            bucket["values"].append(fval)
            bucket["count"] += 1
            bucket["total"] += fval

    @staticmethod
    def _parse_dt(s: str) -> datetime:
        for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return datetime.now()

    @staticmethod
    def _agg(metrics: dict, key: str) -> dict[str, float] | None:
        m = metrics.get(key)
        if not m or not m["values"]:
            return None
        v = m["values"]
        return {"avg": round(sum(v) / len(v), 1), "min": round(min(v), 1), "max": round(max(v), 1)}

    @staticmethod
    def _build_vital(metrics: dict) -> dict[str, Any]:
        vital: dict[str, Any] = {}
        agg = AppleHealthImporter._agg
        hr = agg(metrics, "heart_rate")
        if hr:
            vital["heart_rate_avg"] = hr["avg"]
            vital["heart_rate_min"] = hr["min"]
            vital["heart_rate_max"] = hr["max"]
        resting = agg(metrics, "resting_hr")
        if resting:
            vital["resting_heart_rate"] = resting["avg"]
        walking = agg(metrics, "walking_hr")
        if walking:
            vital["walking_heart_rate"] = walking["avg"]
        hrv = agg(metrics, "hrv")
        if hrv:
            vital["hrv_avg"] = hrv["avg"]
            vital["hrv_min"] = hrv["min"]
            vital["hrv_max"] = hrv["max"]
        spo2 = agg(metrics, "spo2")
        if spo2:
            vital["spo2_avg"] = round(spo2["avg"] * 100, 1) if spo2["avg"] <= 1 else spo2["avg"]
            vital["spo2_min"] = round(spo2["min"] * 100, 1) if spo2["min"] <= 1 else spo2["min"]
            vital["spo2_max"] = round(spo2["max"] * 100, 1) if spo2["max"] <= 1 else spo2["max"]
        resp = agg(metrics, "respiratory_rate")
        if resp:
            vital["respiratory_rate"] = resp["avg"]
        vo2 = agg(metrics, "vo2_max")
        if vo2:
            vital["vo2_max"] = vo2["max"]
        weight = agg(metrics, "weight_kg")
        if weight:
            vital["weight_kg"] = weight["max"]
        temp = agg(metrics, "wrist_temp")
        if temp:
            vital["wrist_temperature"] = temp["avg"]
        noise = agg(metrics, "noise_db")
        if noise:
            vital["noise_db_avg"] = noise["avg"]
            vital["noise_db_max"] = noise["max"]
        return vital

    @staticmethod
    def _make_record(
        date: str, category: str, item: str, description: str,
        *, tags: str, now: str, metrics_json: dict | None = None,
        exercise_type: str = "", exercise_duration_min: int = 0,
        exercise_intensity: str = "", sleep_hours: float = 0,
        sleep_quality: str = "", mood: str = "",
    ) -> dict[str, Any]:
        """Build a flat dict suitable for SoloHealthRecord(**dict)."""
        return {
            "id": uuid4().hex[:12],
            "date": date,
            "subject": "self",
            "category": category,
            "item": item,
            "description": description,
            "exercise_type": exercise_type,
            "exercise_duration_min": exercise_duration_min,
            "exercise_intensity": exercise_intensity,
            "sleep_hours": sleep_hours,
            "sleep_quality": sleep_quality,
            "mood": mood,
            "metrics_json": json.dumps(metrics_json or {}, ensure_ascii=False),
            "tags": tags,
            "source": "import",
            "created_at": now,
            "updated_at": now,
        }
