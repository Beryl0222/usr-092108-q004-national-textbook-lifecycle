"""校验领域事件信封的基础字段。"""

import json
from datetime import datetime
from pathlib import Path

_SCHEMA = json.loads(
    (Path(__file__).parents[1] / "contracts" / "domain.schema.json").read_text(encoding="utf-8")
)

REQUIRED = tuple(_SCHEMA["required"])
EVENT_TYPES = tuple(_SCHEMA["properties"]["event_type"]["enum"])
AGGREGATE_TYPES = tuple(_SCHEMA["properties"]["aggregate_type"]["enum"])

# 携带 report_key 的事件按去重键归并：重复上报不产生多次更正
DEDUPED_EVENTS = ("ISSUE_REPORTED",)


def _valid_refs(refs: object) -> bool:
    if not isinstance(refs, dict):
        return False
    return all(
        isinstance(role, str)
        and (
            isinstance(target, str)
            or (isinstance(target, list) and target and all(isinstance(item, str) for item in target))
        )
        for role, target in refs.items()
    )


def validate_event(record: dict) -> list[str]:
    errors = [f"缺少字段：{name}" for name in REQUIRED if name not in record]
    if "version" in record and (not isinstance(record["version"], int) or record["version"] < 1):
        errors.append("version 必须是正整数")
    event_type = record.get("event_type")
    if event_type is not None and event_type not in EVENT_TYPES:
        errors.append(f"未知事件类型：{event_type}")
    aggregate_type = record.get("aggregate_type")
    if aggregate_type is not None and aggregate_type not in AGGREGATE_TYPES:
        errors.append(f"未知聚合类型：{aggregate_type}")
    if "occurred_at" in record:
        occurred_at = record["occurred_at"]
        if not isinstance(occurred_at, str):
            errors.append("occurred_at 必须是 ISO 8601 日期时间字符串")
        else:
            try:
                datetime.fromisoformat(occurred_at)
            except ValueError:
                errors.append("occurred_at 必须是 ISO 8601 日期时间字符串")
    if event_type in DEDUPED_EVENTS and not record.get("report_key"):
        errors.append(f"{event_type} 必须携带 report_key，以便重复上报归并")
    if "refs" in record and not _valid_refs(record["refs"]):
        errors.append("refs 必须是关系角色到聚合标识（或其列表）的映射")
    return errors
