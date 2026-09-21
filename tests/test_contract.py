import copy
import json
import unittest
from pathlib import Path

from src.projections import affected_adoptions, correction_trace, fold, license_usages, scan_view
from src.validator import validate_event, validate_stream

DATA_DIR = Path(__file__).parents[1] / "data"


def load_stream() -> list[dict]:
    return json.loads((DATA_DIR / "sample_stream.json").read_text(encoding="utf-8"))


def find(events: list[dict], event_id: str) -> dict:
    return next(event for event in events if event["event_id"] == event_id)


class ContractTest(unittest.TestCase):
    def test_sample_matches_envelope(self) -> None:
        sample = json.loads((DATA_DIR / "sample.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_event(sample), [])

    def test_event_type_must_match_aggregate(self) -> None:
        sample = json.loads((DATA_DIR / "sample.json").read_text(encoding="utf-8"))
        sample["aggregate_type"] = "printing_run"
        errors = validate_event(sample)
        self.assertTrue(any("必须归属聚合" in e for e in errors))

    def test_payload_required_fields(self) -> None:
        sample = json.loads((DATA_DIR / "sample.json").read_text(encoding="utf-8"))
        del sample["payload"]["edition_id"]
        errors = validate_event(sample)
        self.assertTrue(any("edition_id" in e for e in errors))


class SampleStreamTest(unittest.TestCase):
    def test_sample_stream_is_valid(self) -> None:
        self.assertEqual(validate_stream(load_stream()), [])

    def test_disputed_issue_has_no_correction(self) -> None:
        # issue-2026-003 存在未撤回的暂缓，故事线中不应出现其更正。
        state = fold(load_stream())
        self.assertNotIn("issue-2026-003", [c["issue_id"] for c in state["corrections"].values()])
        held = [
            review
            for issue in state["issues"].values()
            if issue.get("issue_id") == "issue-2026-003"
            for review in issue.get("reviews", [])
            if review["event_type"] == "REVIEW_HELD"
        ]
        self.assertEqual(len(held), 1)
        self.assertIn("责任豁免", held[0]["dissent"]["position"])


class AppendOnlyTest(unittest.TestCase):
    def test_duplicate_event_id_rejected(self) -> None:
        events = load_stream()
        events.append(copy.deepcopy(find(events, "evt-005")))
        errors = validate_stream(events)
        self.assertTrue(any("event_id 重复" in e for e in errors))

    def test_version_gap_rejected(self) -> None:
        events = load_stream()
        find(events, "evt-002")["version"] = 9
        errors = validate_stream(events)
        self.assertTrue(any("version 应为" in e for e in errors))

    def test_printed_content_cannot_be_overwritten(self) -> None:
        # 已发布章节不得用同 unit_id 再次“发布”覆盖。
        events = load_stream()
        duplicate = copy.deepcopy(find(events, "evt-007"))
        duplicate["event_id"] = "evt-overwrite"
        duplicate["occurred_at"] = "2026-10-01T10:00:00+08:00"
        duplicate["version"] = 99
        duplicate["summary"] = "试图用电子修订覆盖已印刷章节"
        events.append(duplicate)
        errors = validate_stream(events)
        self.assertTrue(any("禁止覆盖" in e for e in errors))

    def test_duplicate_entity_creation_rejected(self) -> None:
        events = load_stream()
        duplicate = copy.deepcopy(find(events, "evt-029"))  # 第2次印次
        duplicate["event_id"] = "evt-printing-dup"
        duplicate["occurred_at"] = "2026-10-02T08:00:00+08:00"
        duplicate["version"] = 2
        events.append(duplicate)
        errors = validate_stream(events)
        self.assertTrue(any("印次 pr-j101-2 已创建" in e for e in errors))


class DedupeTest(unittest.TestCase):
    def test_same_dedupe_key_must_use_dedup_event(self) -> None:
        events = load_stream()
        second_report = {
            "event_id": "evt-dup-report",
            "event_type": "ISSUE_REPORTED",
            "aggregate_type": "correction_notice",
            "aggregate_id": "issue-2026-009",
            "occurred_at": "2026-09-10T08:00:00+08:00",
            "version": 1,
            "summary": "试图用相同 dedupe_key 再报一次",
            "payload": {
                "issue_id": "issue-2026-009",
                "dedupe_key": "j101|pr-j101-1|con-deepfake-norm|labeling-duty",
                "edition_id": "ed-j101-v1",
                "printing_id": "pr-j101-1",
                "unit_id": "con-deepfake-norm",
                "severity": "urgent",
            },
        }
        events.append(second_report)
        errors = validate_stream(events)
        self.assertTrue(any("ISSUE_DEDUPED" in e for e in errors))

    def test_duplicate_report_cannot_trigger_second_correction(self) -> None:
        events = load_stream()
        # 重复件直接走复核 → 更正，应被拒绝。
        events.append(
            {
                "event_id": "evt-bad-corr",
                "event_type": "CORRECTION_PUBLISHED",
                "aggregate_type": "correction_notice",
                "aggregate_id": "cor-bad",
                "occurred_at": "2026-09-12T08:00:00+08:00",
                "version": 1,
                "summary": "重复件试图发布第二条更正",
                "payload": {
                    "issue_id": "issue-2026-002",
                    "correction_id": "cor-bad",
                    "affects_printing_ids": ["pr-j101-1"],
                    "affects_unit_ids": ["con-deepfake-norm"],
                    "scope": "electronic_errata",
                    "supplement_unit_id": "sup-errata-001",
                },
            }
        )
        errors = validate_stream(events)
        self.assertTrue(any("重复件不得单独发布更正" in e for e in errors))

    def test_trace_lists_duplicate_reports(self) -> None:
        state = fold(load_stream())
        trace = correction_trace(state, "cor-2026-001")
        self.assertEqual(trace["issue"]["duplicate_reports"], ["issue-2026-002"])


class ReviewGateTest(unittest.TestCase):
    def test_publish_without_approvals_rejected(self) -> None:
        events = load_stream()
        # 对尚未完成复核的问题尝试发布更正。
        events.append(
            {
                "event_id": "evt-corr-003",
                "event_type": "CORRECTION_PUBLISHED",
                "aggregate_type": "correction_notice",
                "aggregate_id": "cor-003",
                "occurred_at": "2026-09-21T10:00:00+08:00",
                "version": 1,
                "summary": "争议问题在暂缓期间试图发布",
                "payload": {
                    "issue_id": "issue-2026-003",
                    "correction_id": "cor-003",
                    "affects_printing_ids": [],
                    "affects_unit_ids": ["ch-08"],
                    "scope": "electronic_errata",
                    "supplement_unit_id": "sup-reg-003",
                },
            }
        )
        errors = validate_stream(events)
        self.assertTrue(any("policy_expert" in e for e in errors))
        self.assertTrue(any("暂缓/驳回尚未撤回" in e for e in errors))

    def test_urgent_requires_chief_editor(self) -> None:
        events = load_stream()
        # 把主编终审从紧急问题上拿掉，发布应失败。
        chief = find(events, "evt-023")
        chief["event_type"] = "REVIEW_HELD"
        chief["summary"] = "主编改为暂缓"
        chief["payload"]["reason"] = "测试"
        errors = validate_stream(events)
        self.assertTrue(
            any("chief_editor" in e and "批准" in e for e in errors),
            errors,
        )

    def test_reviewer_must_be_assigned_first(self) -> None:
        events = load_stream()
        events.append(
            {
                "event_id": "evt-unassigned-approval",
                "event_type": "REVIEW_APPROVED",
                "aggregate_type": "correction_notice",
                "aggregate_id": "issue-2026-003",
                "occurred_at": "2026-09-21T11:00:00+08:00",
                "version": 6,
                "summary": "未分工的复核人直接出具意见",
                "payload": {
                    "issue_id": "issue-2026-003",
                    "reviewer_id": "rev-stranger",
                    "review_role": "chief_editor",
                },
            }
        )
        errors = validate_stream(events)
        self.assertTrue(any("未经分工" in e for e in errors))

    def test_dissent_is_retained_after_hold_withdrawn(self) -> None:
        state = fold(load_stream())
        trace = correction_trace(state, "cor-2026-001")
        dissent_events = [r for r in trace["reviews"] if r.get("dissent")]
        self.assertEqual(len(dissent_events), 1)
        self.assertIn("事前标识义务", dissent_events[0]["dissent"]["position"])


class ElectronicErrataTest(unittest.TestCase):
    def test_electronic_errata_requires_supplement(self) -> None:
        events = load_stream()
        bad = copy.deepcopy(find(events, "evt-025"))
        bad["event_id"] = "evt-corr-no-sup"
        bad["aggregate_id"] = "cor-no-sup"
        bad["occurred_at"] = "2026-12-01T08:00:00+08:00"
        bad["version"] = 1
        bad["payload"]["correction_id"] = "cor-no-sup"
        bad["payload"]["scope"] = "electronic_errata"
        del bad["payload"]["supplement_unit_id"]
        events.append(bad)
        errors = validate_stream(events)
        self.assertTrue(any("不得覆盖已印刷内容" in e for e in errors))


class NotificationTargetingTest(unittest.TestCase):
    def test_school_with_unaffected_printing_cannot_be_notified(self) -> None:
        events = load_stream()
        # 北方学院持第2次印次（已修正），向其发送紧急通知应被拒绝。
        events.append(
            {
                "event_id": "evt-wrong-notice",
                "event_type": "NOTIFICATION_SENT",
                "aggregate_type": "correction_notice",
                "aggregate_id": "cor-2026-001",
                "occurred_at": "2026-09-12T12:00:00+08:00",
                "version": 4,
                "summary": "误向不受影响印次的学校发通知",
                "payload": {
                    "correction_id": "cor-2026-001",
                    "adoption_id": "adopt-bfxy-2026autumn",
                    "school_id": "school-bfxy",
                    "channel": "email",
                },
            }
        )
        errors = validate_stream(events)
        self.assertTrue(any("不在更正影响范围内" in e for e in errors))

    def test_duplicate_notification_rejected(self) -> None:
        events = load_stream()
        dup = copy.deepcopy(find(events, "evt-027"))
        dup["event_id"] = "evt-notice-dup"
        dup["occurred_at"] = "2026-09-08T10:00:00+08:00"
        dup["version"] = 4
        events.append(dup)
        errors = validate_stream(events)
        self.assertTrue(any("已通知过" in e for e in errors))

    def test_ack_without_notification_rejected(self) -> None:
        events = load_stream()
        events.append(
            {
                "event_id": "evt-phantom-ack",
                "event_type": "NOTICE_ACKNOWLEDGED",
                "aggregate_type": "correction_notice",
                "aggregate_id": "cor-2026-001",
                "occurred_at": "2026-09-12T13:00:00+08:00",
                "version": 4,
                "summary": "没有通知记录却收到回执",
                "payload": {
                    "correction_id": "cor-2026-001",
                    "adoption_id": "adopt-bfxy-2026autumn",
                    "school_id": "school-bfxy",
                },
            }
        )
        errors = validate_stream(events)
        self.assertTrue(any("没有对应的已发送通知" in e for e in errors))


class BaselineFreezeTest(unittest.TestCase):
    def test_update_during_freeze_rejected(self) -> None:
        events = load_stream()
        events.append(
            {
                "event_id": "evt-bad-update",
                "event_type": "UPDATE_SCHEDULED",
                "aggregate_type": "course_adoption",
                "aggregate_id": "adopt-jhdx-2026autumn",
                "occurred_at": "2026-09-21T09:00:00+08:00",
                "version": 4,
                "summary": "试图把更新安排在冻结期内",
                "payload": {
                    "adoption_id": "adopt-jhdx-2026autumn",
                    "scheduled_at": "2026-10-01T09:00:00+08:00",
                },
            }
        )
        errors = validate_stream(events)
        self.assertTrue(any("冻结期内" in e for e in errors))

    def test_schedule_without_freeze_rejected(self) -> None:
        events = load_stream()
        events.append(
            {
                "event_id": "evt-schedule-nofreeze",
                "event_type": "UPDATE_SCHEDULED",
                "aggregate_type": "course_adoption",
                "aggregate_id": "adopt-bfxy-2026autumn",
                "occurred_at": "2026-09-21T09:00:00+08:00",
                "version": 2,
                "summary": "未冻结基线却安排更新",
                "payload": {
                    "adoption_id": "adopt-bfxy-2026autumn",
                    "scheduled_at": "2027-02-22T09:00:00+08:00",
                },
            }
        )
        errors = validate_stream(events)
        self.assertTrue(any("尚未冻结" in e for e in errors))


class ProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = fold(load_stream())

    def test_scan_view_matches_edition_in_hand(self) -> None:
        view = scan_view(self.state, "ed-j101-v1")
        supplement_titles = {item["title"] for item in view["supplements"]}
        self.assertIn("电子勘误：8.2.3 深度伪造内容的标识义务（2026-09）", supplement_titles)
        self.assertIn("替代案例：跨平台合成内容标识义务对照（2026-09）", supplement_titles)

        superseded = {item["unit_id"]: item for item in view["superseded"]}
        self.assertEqual(superseded["case-df-platform-01"]["reason"], "platform_offline")
        self.assertEqual(
            superseded["case-df-platform-01"]["replacement_unit_id"], "sup-case-002"
        )
        self.assertEqual(superseded["reg-algo-01"]["reason"], "regulation_updated")

    def test_scan_view_for_other_textbook_only_gets_shared_regulation_update(self) -> None:
        view = scan_view(self.state, "ed-j202-v1")
        kinds = {item["kind"] for item in view["supplements"]}
        self.assertEqual(kinds, {"regulation_update"})
        self.assertEqual(view["superseded"], [])

    def test_scan_view_unknown_edition(self) -> None:
        self.assertFalse(scan_view(self.state, "ed-nope")["found"])

    def test_license_expiry_locates_all_usages(self) -> None:
        usage = license_usages(self.state, "img-ch08-01")
        self.assertTrue(usage["found"])
        self.assertEqual(usage["licensor"], "某视觉素材社")
        kinds = {item["usage_kind"] for item in usage["usages"]}
        self.assertEqual(kinds, {"digital_courseware", "public_page"})
        self.assertEqual(len(usage["usages"]), 2)

    def test_correction_trace_is_complete(self) -> None:
        trace = correction_trace(self.state, "cor-2026-001")
        self.assertTrue(trace["found"])
        # 上报 → 复核 → 发布 → 库存处置 → 通知 → 回执 全链条。
        self.assertEqual(trace["issue"]["issue_id"], "issue-2026-001")
        roles = {r["review_role"] for r in trace["reviews"] if r["event_type"] == "REVIEW_APPROVED"}
        self.assertEqual(roles, {"author", "policy_expert", "chief_editor"})
        actions = [d["action"] for d in trace["inventory_disposals"]]
        self.assertEqual(actions, ["hold", "reissue_label"])
        self.assertEqual(len(trace["notifications"]), 1)
        notice = trace["notifications"][0]
        self.assertEqual(notice["school_name"], "京华大学")
        self.assertIsNotNone(notice["acknowledged_at"])

    def test_affected_adoptions_targets_affected_printing_only(self) -> None:
        affected = affected_adoptions(self.state, "cor-2026-001")
        schools = {row["school_id"]: row for row in affected}
        self.assertIn("school-jhdx", schools)
        self.assertNotIn("school-bfxy", schools)
        self.assertTrue(schools["school-jhdx"]["already_notified"])


if __name__ == "__main__":
    unittest.main()
