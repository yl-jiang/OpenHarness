#!/usr/bin/env python3
"""
Extract health-related data from existing solo records and memory files,
and populate the structured health_records table.

This script:
1. Scans all records for health-related tags/content
2. Reads memory files for health facts
3. Categorizes each item (fitness/symptom/medication/medical/sleep/mental/nutrition)
4. Inserts structured entries into health_records table

Usage:
    uv run python scripts/extract_health_data.py [--dry-run] [--workspace ~/.solo]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from solo.core.store import SoloStore
from solo.core.models import SoloHealthRecord


# Health-related tag keywords (case-insensitive matching)
HEALTH_TAG_PATTERNS = {
    "fitness": ["运动", "跑步", "健身", "游泳", "瑜伽", "骑行", "爬山", "锻炼", "有氧"],
    "symptom": ["鼻炎", "过敏", "头疼", "头痛", "感冒", "发烧", "咳嗽", "疲劳", "疲惫",
                "失眠", "疼痛", "不适", "眨眼"],
    "medication": ["用药", "吃药", "药物"],
    "medical": ["医院", "体检", "复查", "门诊", "就诊", "医生", "诊断", "评估"],
    "sleep": ["睡眠", "熬夜", "作息", "午休"],
    "mental": ["情绪", "焦虑", "压力", "烦躁", "烦恼"],
    "nutrition": ["水果", "饮食", "营养"],
}


def detect_subject(record, content: str) -> str:
    """Detect who the health record is about based on tags and content."""
    # Check tags first
    if "图图" in record.tags:
        return "图图"
    if "明月" in record.tags:
        return "明月"

    # Check content for explicit mentions
    if "图图" in content:
        return "图图"
    if "明月" in content or "老婆" in content or "丈母娘" in content:
        # Be more careful: only if the health event is ABOUT them
        # e.g., "明月给点了外卖" doesn't mean the health event is about 明月
        if any(kw in content for kw in ["明月去医院", "明月复查", "明月身体", "老婆身体", "丈母娘做"]):
            return "明月" if "明月" in content else "丈母娘"

    return "self"


# Content-based patterns for extraction
CONTENT_PATTERNS = {
    "running_pace": re.compile(r"配速[^\d]*(\d+['\'′](\d+))?"),
    "running_distance": re.compile(r"(\d+(?:\.\d+)?)\s*(?:公里|km)", re.I),
    "running_duration": re.compile(r"(?:用时|时间)[^\d]*(\d+)[分:：](\d+)?"),
    "heart_rate": re.compile(r"心率[^\d]*(\d+)"),
    "sleep_hours": re.compile(r"睡了[^\d]*(\d+(?:\.\d+)?)\s*(?:小时|h)", re.I),
    "sleep_time": re.compile(r"(\d{1,2})[:：](\d{2})\s*(?:睡|上床|入睡)"),
    "wake_time": re.compile(r"(\d{1,2})[:：](\d{2})\s*(?:醒|起床)"),
}


def extract_fitness_from_record(record, content: str) -> SoloHealthRecord | None:
    """Extract fitness/exercise data from a record."""
    if not any(tag in record.tags for tag in HEALTH_TAG_PATTERNS["fitness"]):
        return None

    # Extract metrics
    metrics = {}
    exercise_type = "运动"
    duration_min = 0
    intensity = ""

    # Detect exercise type
    if "跑步" in record.tags or "跑步" in content:
        exercise_type = "跑步"
    elif "游泳" in record.tags:
        exercise_type = "游泳"
    elif "骑行" in record.tags:
        exercise_type = "骑行"

    # Extract distance
    m = CONTENT_PATTERNS["running_distance"].search(content)
    if m:
        metrics["distance_km"] = float(m.group(1))

    # Extract pace
    m = CONTENT_PATTERNS["running_pace"].search(content)
    if m:
        pace_str = m.group(0).replace("配速", "").strip()
        metrics["pace"] = pace_str

    # Extract duration
    m = CONTENT_PATTERNS["running_duration"].search(content)
    if m:
        mins = int(m.group(1))
        secs = int(m.group(2) or 0)
        duration_min = mins + (secs // 60)
        metrics["duration_sec"] = mins * 60 + secs

    # Extract heart rate
    m = CONTENT_PATTERNS["heart_rate"].search(content)
    if m:
        hr = int(m.group(1))
        metrics["heart_rate_avg"] = hr
        # Estimate intensity from heart rate
        if hr < 120:
            intensity = "low"
        elif hr < 150:
            intensity = "moderate"
        else:
            intensity = "high"

    # Estimate duration from content if not found
    if duration_min == 0 and "跑步" in content:
        # Rough estimate: 5km at ~7min/km pace = ~35min
        if metrics.get("distance_km"):
            duration_min = int(metrics["distance_km"] * 7)

    now = datetime.now().isoformat()
    subject = detect_subject(record, content)
    return SoloHealthRecord(
        id=uuid4().hex[:12],
        record_id=record.id,
        date=record.date,
        subject=subject,
        category="fitness",
        item=exercise_type,
        description=record.summary,
        exercise_type=exercise_type,
        exercise_duration_min=duration_min,
        exercise_intensity=intensity,
        metrics_json=json.dumps(metrics, ensure_ascii=False) if metrics else "{}",
        tags=record.tags,
        source="extraction",
        created_at=now,
        updated_at=now,
    )


def extract_symptom_from_record(record, content: str) -> SoloHealthRecord | None:
    """Extract symptom data from a record."""
    matched_symptoms = [s for s in HEALTH_TAG_PATTERNS["symptom"] if s in record.tags]
    if not matched_symptoms:
        return None

    # Determine body part and severity
    body_part = ""
    severity = ""
    item = matched_symptoms[0]

    if "鼻炎" in matched_symptoms or "过敏" in matched_symptoms:
        body_part = "鼻"
        item = "过敏性鼻炎"
        if "加重" in content or "厉害" in content or "严重" in content:
            severity = "moderate"
        elif "复发" in content or "犯了" in content:
            severity = "mild"

    elif "头疼" in matched_symptoms or "头痛" in matched_symptoms:
        body_part = "头"
        item = "头痛"

    elif "疲劳" in matched_symptoms or "疲惫" in matched_symptoms:
        body_part = "全身"
        item = "疲劳"
        severity = "mild"

    elif "眨眼" in matched_symptoms:
        body_part = "眼睛"
        item = "频繁眨眼"

    # Determine status
    status = "active"
    if "好转" in content or "好了" in content:
        status = "resolved"

    now = datetime.now().isoformat()
    subject = detect_subject(record, content)
    return SoloHealthRecord(
        id=uuid4().hex[:12],
        record_id=record.id,
        date=record.date,
        subject=subject,
        category="symptom",
        item=item,
        description=record.summary,
        body_part=body_part,
        severity=severity,
        status=status,
        tags=record.tags,
        source="extraction",
        created_at=now,
        updated_at=now,
    )


def extract_medication_from_record(record, content: str) -> SoloHealthRecord | None:
    """Extract medication usage from a record."""
    if "用药" not in record.tags and "药物" not in record.tags:
        # Check content for medication keywords
        if "喷雾剂" not in content and "药" not in content:
            return None

    medication_name = ""
    dosage = ""

    # Extract specific medication names
    if "色甘奈甲那敏" in content:
        medication_name = "色甘奈甲那敏鼻喷雾剂"
        dosage = "按需使用"
    elif "氨卓斯汀" in content:
        medication_name = "氨卓斯汀滴眼液"
        dosage = "早晚各一次"
    elif "环吡酮胺" in content:
        medication_name = "环吡酮胺眼液(II)"
        dosage = "早晚各一次"

    if not medication_name:
        return None

    now = datetime.now().isoformat()
    subject = detect_subject(record, content)
    return SoloHealthRecord(
        id=uuid4().hex[:12],
        record_id=record.id,
        date=record.date,
        subject=subject,
        category="medication",
        item=medication_name,
        description=record.summary,
        medication_name=medication_name,
        dosage=dosage,
        frequency="按需" if "缓解" in content else "每日两次",
        tags=record.tags,
        source="extraction",
        created_at=now,
        updated_at=now,
    )


def extract_medical_visit_from_record(record, content: str) -> SoloHealthRecord | None:
    """Extract medical visit/hospital data from a record."""
    if not any(tag in record.tags for tag in HEALTH_TAG_PATTERNS["medical"]):
        return None

    # Determine visit type
    item = "就诊"
    description = record.summary

    if "复查" in record.tags or "复查" in content:
        item = "复查"
    elif "体检" in record.tags:
        item = "体检"
    elif "评估" in record.tags or "评估" in content:
        if "Gesell" in content:
            item = "Gesell发育评估"
        elif "ASD" in content or "自闭症" in content:
            item = "ASD评估"
        else:
            item = "发育评估"

    # Extract hospital name if present
    hospital = ""
    if "新华医院" in content:
        hospital = "上海交通大学医学院附属新华医院"
    elif "耳鼻喉科医院" in content:
        hospital = "复旦大学附属眼耳鼻喉科医院"

    now = datetime.now().isoformat()
    subject = detect_subject(record, content)
    return SoloHealthRecord(
        id=uuid4().hex[:12],
        record_id=record.id,
        date=record.date,
        subject=subject,
        category="medical",
        item=item,
        description=f"{hospital + ' - ' if hospital else ''}{description}",
        status="resolved",
        tags=record.tags,
        source="extraction",
        created_at=now,
        updated_at=now,
    )


def extract_sleep_from_record(record, content: str) -> SoloHealthRecord | None:
    """Extract sleep data from a record."""
    if not any(tag in record.tags for tag in HEALTH_TAG_PATTERNS["sleep"]):
        return None

    # Skip if it's just a plan or mention without actual sleep data
    if "计划" in record.summary or "考虑" in record.summary:
        return None

    sleep_hours = 0.0
    sleep_quality = ""

    # Try to extract sleep hours
    m = CONTENT_PATTERNS["sleep_hours"].search(content)
    if m:
        sleep_hours = float(m.group(1))

    # Try to infer from sleep/wake times
    sleep_match = CONTENT_PATTERNS["sleep_time"].search(content)
    wake_match = CONTENT_PATTERNS["wake_time"].search(content)
    if sleep_match and wake_match:
        sleep_h = int(sleep_match.group(1))
        sleep_m = int(sleep_match.group(2))
        wake_h = int(wake_match.group(1))
        wake_m = int(wake_match.group(2))

        # Convert 12-hour to 24-hour based on context
        before_sleep = content[:sleep_match.start()]
        if any(kw in before_sleep[-10:] for kw in ("晚", "夜", "昨")):
            if sleep_h < 12:
                sleep_h += 12
        after_sleep = content[sleep_match.end():wake_match.start()]
        if any(kw in after_sleep for kw in ("今早", "早上", "早", "醒")):
            pass  # wake_h is already in morning range

        sleep_minutes = sleep_h * 60 + sleep_m
        wake_minutes = wake_h * 60 + wake_m
        if wake_minutes < sleep_minutes:
            wake_minutes += 24 * 60
        sleep_hours = round((wake_minutes - sleep_minutes) / 60, 1)

    # Infer quality from content
    if "好" in content or "不错" in content or "一觉到天亮" in content:
        sleep_quality = "good"
    elif "疲惫" in content or "没睡好" in content or "烦躁" in content:
        sleep_quality = "poor"
    elif sleep_hours > 0:
        sleep_quality = "fair"

    # Skip if no meaningful sleep data
    if sleep_hours == 0 and not sleep_quality:
        return None

    now = datetime.now().isoformat()
    subject = detect_subject(record, content)
    return SoloHealthRecord(
        id=uuid4().hex[:12],
        record_id=record.id,
        date=record.date,
        subject=subject,
        category="sleep",
        item="睡眠",
        description=record.summary,
        sleep_hours=sleep_hours,
        sleep_quality=sleep_quality,
        tags=record.tags,
        source="extraction",
        created_at=now,
        updated_at=now,
    )


def extract_mental_from_record(record, content: str) -> SoloHealthRecord | None:
    """Extract mental health data from a record."""
    if not any(tag in record.tags for tag in HEALTH_TAG_PATTERNS["mental"]):
        return None

    mood = ""
    stress_level = ""

    # Determine mood
    if "焦虑" in record.tags or "焦虑" in content:
        mood = "焦虑"
    elif "烦躁" in record.tags or "烦躁" in content:
        mood = "烦躁"
    elif "烦恼" in record.tags or "烦恼" in content:
        mood = "烦恼"
    elif "疲惫" in record.tags and "情绪" in record.tags:
        mood = "疲惫"

    # Determine stress level
    if "压力" in record.tags or "压力" in content:
        stress_level = "high" if "很大" in content else "moderate"
    elif "消耗" in content or "负能量" in content:
        stress_level = "high"

    if not mood and not stress_level:
        return None

    now = datetime.now().isoformat()
    subject = detect_subject(record, content)
    return SoloHealthRecord(
        id=uuid4().hex[:12],
        record_id=record.id,
        date=record.date,
        subject=subject,
        category="mental",
        item=mood or "情绪波动",
        description=record.summary,
        mood=mood,
        stress_level=stress_level,
        tags=record.tags,
        source="extraction",
        created_at=now,
        updated_at=now,
    )


def extract_nutrition_from_record(record, content: str) -> SoloHealthRecord | None:
    """Extract nutrition/diet data from a record."""
    if not any(tag in record.tags for tag in HEALTH_TAG_PATTERNS["nutrition"]):
        return None

    # Only extract if it's about habits/decisions, not just eating something
    if "决定" not in content and "习惯" not in content and "坚持" not in content:
        return None

    now = datetime.now().isoformat()
    subject = detect_subject(record, content)
    return SoloHealthRecord(
        id=uuid4().hex[:12],
        record_id=record.id,
        date=record.date,
        subject=subject,
        category="nutrition",
        item="饮食习惯",
        description=record.summary,
        status="active",
        tags=record.tags,
        source="extraction",
        created_at=now,
        updated_at=now,
    )


def extract_from_memory_file(memory_path: Path) -> list[SoloHealthRecord]:
    """Extract health facts from memory files."""
    records = []
    now = datetime.now().isoformat()

    content = memory_path.read_text(encoding="utf-8")

    # Parse metadata
    if not content.startswith("---"):
        return records

    parts = content.split("---", 2)
    if len(parts) < 3:
        return records

    try:
        import yaml
        metadata = yaml.safe_load(parts[1])
    except:
        # Fallback: simple key-value parsing
        metadata = {}
        for line in parts[1].strip().split("\n"):
            if ":" in line:
                key, _, value = line.partition(":")
                metadata[key.strip()] = value.strip().strip('"')

    body = parts[2].strip()
    memory_id = metadata.get("id", "")
    name = metadata.get("name", memory_path.stem)

    if name == "medical_history":
        # Extract allergic rhinitis
        records.append(SoloHealthRecord(
            id=uuid4().hex[:12],
            date="2026-06-13",  # First mentioned recurrence
            subject="self",
            category="symptom",
            item="过敏性鼻炎",
            description="慢性过敏性鼻炎，主要诱因：气温变化、灰尘等过敏原",
            body_part="鼻",
            severity="mild",
            status="chronic",
            linked_memory_id=memory_id,
            tags="鼻炎,过敏,慢性病",
            source="memory",
            created_at=now,
            updated_at=now,
        ))

        # Extract medication if mentioned
        if "色甘奈甲那敏" in body:
            records.append(SoloHealthRecord(
                id=uuid4().hex[:12],
                date="2026-06-13",
                subject="self",
                category="medication",
                item="色甘奈甲那敏鼻喷雾剂",
                description="用于缓解过敏性鼻炎症状",
                medication_name="色甘奈甲那敏鼻喷雾剂",
                frequency="按需使用",
                status="active",
                linked_memory_id=memory_id,
                tags="鼻炎,用药",
                source="memory",
                created_at=now,
                updated_at=now,
            ))

    elif name == "tutu_medical_assessment":
        # Extract Gesell assessment
        if "Gesell" in body:
            records.append(SoloHealthRecord(
                id=uuid4().hex[:12],
                date="2026-06-17",
                subject="图图",
                category="medical",
                item="Gesell发育评估",
                description="评估结果：正常。动作能DQ88，应物能DQ98，语言能DQ100，应人能DQ95",
                status="resolved",
                linked_memory_id=memory_id,
                tags="图图,医院,发育评估",
                source="memory",
                created_at=now,
                updated_at=now,
            ))

        # Extract ASD assessment plan
        if "ASD" in body or "自闭症" in body:
            records.append(SoloHealthRecord(
                id=uuid4().hex[:12],
                date="2026-06-17",
                subject="图图",
                category="medical",
                item="ASD评估",
                description="自闭症与社交问题专科门诊，已预约CARS和ADOS测试（6月22日）",
                status="active",
                linked_memory_id=memory_id,
                tags="图图,医院,ASD",
                source="memory",
                created_at=now,
                updated_at=now,
            ))

    elif name == "residence_and_habits":
        if "复查" in body:
            records.append(SoloHealthRecord(
                id=uuid4().hex[:12],
                date=datetime.now().strftime("%Y-%m-%d"),
                subject="self",
                category="medical",
                item="定期复查",
                description="有定期复查的健康管理习惯",
                status="active",
                linked_memory_id=memory_id,
                tags="复查,健康习惯",
                source="memory",
                created_at=now,
                updated_at=now,
            ))

    return records


def main():
    parser = argparse.ArgumentParser(description="Extract health data from solo records")
    parser.add_argument("--dry-run", action="store_true", help="Print records without inserting")
    parser.add_argument("--workspace", default="~/.solo", help="Solo workspace path")
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser()
    if not workspace.exists():
        print(f"Workspace not found: {workspace}")
        sys.exit(1)

    store = SoloStore(workspace)
    records = store.list_records()
    print(f"Found {len(records)} records to scan")

    health_records: list[SoloHealthRecord] = []
    seen_record_ids = set()  # Avoid duplicates from same record

    # Extract from records
    for record in records:
        content = f"{record.corrected_content} {record.summary}"

        extractors = [
            extract_fitness_from_record,
            extract_symptom_from_record,
            extract_medication_from_record,
            extract_medical_visit_from_record,
            extract_sleep_from_record,
            extract_mental_from_record,
            extract_nutrition_from_record,
        ]

        for extractor in extractors:
            try:
                hr = extractor(record, content)
                if hr:
                    # Avoid duplicate entries for same record+category
                    key = f"{record.id}:{hr.category}"
                    if key not in seen_record_ids:
                        health_records.append(hr)
                        seen_record_ids.add(key)
            except Exception as e:
                print(f"Error extracting from record {record.id}: {e}")

    # Extract from memory files
    memory_dir = workspace / "memory"
    if memory_dir.exists():
        for memory_file in memory_dir.glob("*.md"):
            if memory_file.name in ["medical_history.md", "tutu_medical_assessment.md",
                                    "residence_and_habits.md"]:
                try:
                    mem_records = extract_from_memory_file(memory_file)
                    health_records.extend(mem_records)
                    print(f"  Extracted {len(mem_records)} records from {memory_file.name}")
                except Exception as e:
                    print(f"Error extracting from {memory_file}: {e}")

    print(f"\nExtracted {len(health_records)} health records total")

    # Print summary
    from collections import Counter
    by_category = Counter(hr.category for hr in health_records)
    print("\nBy category:")
    for cat, count in sorted(by_category.items()):
        print(f"  {cat}: {count}")

    if args.dry_run:
        print("\n[DRY RUN] Would insert:")
        for hr in health_records[:10]:
            print(f"  {hr.date} | {hr.category:10s} | {hr.item}")
        if len(health_records) > 10:
            print(f"  ... and {len(health_records) - 10} more")
    else:
        print("\nInserting into health_records table...")
        for hr in health_records:
            try:
                store.add_health_record(hr)
            except Exception as e:
                print(f"Error inserting {hr.id}: {e}")

        # Verify
        categories = store.health_record_categories()
        print(f"\n✓ Inserted {sum(categories.values())} health records")
        print("By category in database:")
        for cat, count in sorted(categories.items()):
            print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
