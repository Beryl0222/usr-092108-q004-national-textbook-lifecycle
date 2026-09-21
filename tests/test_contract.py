import json
import unittest
from pathlib import Path

from src.validator import EVENT_TYPES, validate_event

DATA_DIR = Path(__file__).parents[1] / "data"


def load_samples() -> list[Path]:
    return sorted(DATA_DIR.glob("*.json"))


def valid_record() -> dict:
    return {
        "event_id": "test-001",
        "event_type": "EDITION_RELEASED",
        "aggregate_type": "textbook_edition",
        "aggregate_id": "textbook_edition-test",
        "occurred_at": "2026-09-21T12:00:00+08:00",
        "version": 1,
        "summary": "测试用基础记录",
    }


class ContractTest(unittest.TestCase):
    def test_samples_match_envelope(self) -> None:
        for path in load_samples():
            with self.subTest(sample=path.name):
                record = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(validate_event(record), [])

    def test_every_event_type_has_sample(self) -> None:
        seen = {json.loads(path.read_text(encoding="utf-8"))["event_type"] for path in load_samples()}
        self.assertEqual(set(EVENT_TYPES), seen)

    def test_missing_required_field(self) -> None:
        record = valid_record()
        del record["summary"]
        self.assertIn("缺少字段：summary", validate_event(record))

    def test_version_must_be_positive_int(self) -> None:
        record = valid_record()
        record["version"] = 0
        self.assertIn("version 必须是正整数", validate_event(record))

    def test_unknown_event_type_rejected(self) -> None:
        record = valid_record()
        record["event_type"] = "EDITION_ERASED"
        self.assertIn("未知事件类型：EDITION_ERASED", validate_event(record))

    def test_unknown_aggregate_type_rejected(self) -> None:
        record = valid_record()
        record["aggregate_type"] = "school"
        self.assertIn("未知聚合类型：school", validate_event(record))

    def test_occurred_at_must_be_iso_datetime(self) -> None:
        record = valid_record()
        record["occurred_at"] = "2026年9月21日"
        self.assertIn("occurred_at 必须是 ISO 8601 日期时间字符串", validate_event(record))

    def test_issue_report_requires_report_key(self) -> None:
        record = valid_record()
        record["event_type"] = "ISSUE_REPORTED"
        self.assertIn("ISSUE_REPORTED 必须携带 report_key，以便重复上报归并", validate_event(record))
        record["report_key"] = "dup-key-1"
        self.assertEqual(validate_event(record), [])

    def test_refs_must_map_roles_to_identifiers(self) -> None:
        record = valid_record()
        record["refs"] = {"correction": 42}
        self.assertIn("refs 必须是关系角色到聚合标识（或其列表）的映射", validate_event(record))
        record["refs"] = {"correction": "correction_notice-1", "adoption": ["course_adoption-1"]}
        self.assertEqual(validate_event(record), [])


if __name__ == "__main__":
    unittest.main()
