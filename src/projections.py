"""从只追加事件日志派生的读模型。

读模型只做折叠（fold），不产生新事实：所有业务事实仍以事件为准。
- scan_view：学生扫码，按手中版次返回一致的补充说明（勘误/替代/更新）。
- license_usages：许可到期时定位全部数字课件与公开页面。
- correction_trace：出版社从一条勘误追到问题、复核、发布、库存处置、通知与回执。
- affected_adoptions：紧急纠错时需要通知的采用（印次命中且尚未通知）。
"""

from datetime import datetime
from typing import Any


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def fold(events: list[dict]) -> dict[str, Any]:
    """把事件流折叠为当前状态快照。"""
    editions: dict[str, dict] = {}
    printings: dict[str, dict] = {}
    units: dict[str, dict] = {}
    issues: dict[str, dict] = {}
    corrections: dict[str, dict] = {}
    adoptions: dict[str, dict] = {}
    disposals: list[dict] = []
    notifications: dict[tuple[str, str], dict] = {}
    acks: dict[tuple[str, str], dict] = {}

    for event in events:
        event_type = event["event_type"]
        payload = event.get("payload", {})
        ts = event["occurred_at"]

        if event_type == "EDITION_RELEASED":
            editions[payload["edition_id"]] = {
                "edition_id": payload["edition_id"],
                "title_id": payload["title_id"],
                "edition_label": payload["edition_label"],
            }

        elif event_type == "PRINTING_RUN_RELEASED":
            printings[payload["printing_id"]] = {
                "printing_id": payload["printing_id"],
                "edition_id": payload["edition_id"],
                "impression": payload["impression"],
            }

        elif event_type in {
            "CHAPTER_PUBLISHED",
            "CONCEPT_PUBLISHED",
            "CASE_PUBLISHED",
            "REGULATION_CITED",
            "IMAGE_LICENSED",
            "SUPPLEMENT_PUBLISHED",
        }:
            units[payload["unit_id"]] = {
                "unit_id": payload["unit_id"],
                "kind": payload.get("unit_kind", "supplement"),
                "edition_id": payload.get("edition_id"),
                "title": payload.get("title"),
                "platform": payload.get("platform"),
                "citation": payload.get("citation"),
                "licensor": payload.get("licensor"),
                "license_valid_until": payload.get("license_valid_until"),
                "applies_edition_ids": payload.get("applies_edition_ids", []),
                "supplement_kind": payload.get("kind"),
                "usages": units.get(payload["unit_id"], {}).get("usages", []),
                "superseded": False,
            }

        elif event_type == "IMAGE_USAGE_REGISTERED":
            unit = units.get(payload["image_unit_id"])
            if unit is not None:
                unit["usages"].append(
                    {
                        "usage_kind": payload["usage_kind"],
                        "location": payload["location"],
                    }
                )

        elif event_type == "CONTENT_SUPERSEDED":
            unit = units.get(payload["unit_id"])
            if unit is not None:
                unit["superseded"] = True
                unit["supersede_reason"] = payload["reason"]
                unit["replacement_unit_id"] = payload["replacement_unit_id"]

        elif event_type == "ISSUE_REPORTED":
            issues[payload["issue_id"]] = {
                "issue_id": payload["issue_id"],
                "dedupe_key": payload["dedupe_key"],
                "edition_id": payload["edition_id"],
                "printing_id": payload.get("printing_id"),
                "unit_id": payload["unit_id"],
                "severity": payload["severity"],
                "reported_at": ts,
                "reviews": [],
            }

        elif event_type == "ISSUE_DEDUPED":
            issues[payload["issue_id"]] = {
                "issue_id": payload["issue_id"],
                "deduped_into": payload["duplicate_of"],
            }

        elif event_type in {"REVIEW_ASSIGNED", "REVIEW_APPROVED", "REVIEW_REJECTED", "REVIEW_HELD"}:
            issue = issues.get(payload["issue_id"])
            if issue is not None and "reviews" in issue:
                issue["reviews"].append(
                    {
                        "event_type": event_type,
                        "reviewer_id": payload["reviewer_id"],
                        "review_role": payload["review_role"],
                        "at": ts,
                        "note": payload.get("note"),
                        "reason": payload.get("reason"),
                        "dissent": payload.get("dissent"),
                    }
                )

        elif event_type == "CORRECTION_PUBLISHED":
            issue = issues.get(payload["issue_id"])
            corrections[payload["correction_id"]] = {
                "correction_id": payload["correction_id"],
                "issue_id": payload["issue_id"],
                "affects_printing_ids": list(payload["affects_printing_ids"]),
                "affects_unit_ids": list(payload["affects_unit_ids"]),
                "scope": payload["scope"],
                "supplement_unit_id": payload.get("supplement_unit_id"),
                "published_at": ts,
                "disposals": [],
                "notifications": [],
            }
            if issue is not None and "reviews" in issue:
                issue["correction_id"] = payload["correction_id"]

        elif event_type == "INVENTORY_DISPOSED":
            disposals.append(
                {
                    "printing_id": payload["printing_id"],
                    "action": payload["action"],
                    "quantity": payload["quantity"],
                    "correction_id": payload.get("correction_id"),
                    "reason": payload.get("reason"),
                    "at": ts,
                }
            )

        elif event_type == "NOTIFICATION_SENT":
            notifications[(payload["correction_id"], payload["adoption_id"])] = {
                "adoption_id": payload["adoption_id"],
                "school_id": payload["school_id"],
                "channel": payload["channel"],
                "sent_at": ts,
                "acknowledged_at": None,
            }

        elif event_type == "NOTICE_ACKNOWLEDGED":
            sent = notifications.get((payload["correction_id"], payload["adoption_id"]))
            if sent is not None:
                sent["acknowledged_at"] = ts
            acks[(payload["correction_id"], payload["adoption_id"])] = {"at": ts}

        elif event_type == "ADOPTION_REGISTERED":
            adoptions[payload["adoption_id"]] = {
                "adoption_id": payload["adoption_id"],
                "school_id": payload["school_id"],
                "school_name": payload.get("school_name"),
                "course": payload.get("course"),
                "edition_id": payload["edition_id"],
                "printing_id": payload.get("printing_id"),
                "term": payload.get("term"),
                "frozen_until": None,
            }

        elif event_type == "ADOPTION_FROZEN":
            adoption = adoptions.get(payload["adoption_id"])
            if adoption is not None:
                adoption["frozen_until"] = payload["frozen_until"]

        elif event_type == "UPDATE_APPLIED":
            adoption = adoptions.get(payload["adoption_id"])
            if adoption is not None:
                adoption["applied_correction_ids"] = payload.get("correction_ids", [])
                adoption["applied_supplement_unit_ids"] = payload.get("supplement_unit_ids", [])

    # 反向挂接：处置记录与通知归入各自更正。
    for disposal in disposals:
        correction = corrections.get(disposal["correction_id"] or "")
        if correction is not None:
            correction["disposals"].append(disposal)
    for (correction_id, _adoption_id), notice in notifications.items():
        correction = corrections.get(correction_id)
        if correction is not None:
            correction["notifications"].append(notice)

    return {
        "editions": editions,
        "printings": printings,
        "units": units,
        "issues": issues,
        "corrections": corrections,
        "adoptions": adoptions,
    }


def scan_view(state: dict, edition_id: str) -> dict:
    """学生扫码视图：返回与手中版次一致的补充说明。

    已印刷原文不出现修改稿，只有：勘误补充材料、失效案例/法规的替代指引。
    """
    if edition_id not in state["editions"]:
        return {"edition_id": edition_id, "found": False, "supplements": [], "superseded": []}

    supplements = []
    for unit in state["units"].values():
        if edition_id in unit.get("applies_edition_ids", []):
            supplements.append(
                {
                    "unit_id": unit["unit_id"],
                    "kind": unit.get("supplement_kind"),
                    "title": unit.get("title"),
                }
            )

    superseded = []
    for unit in state["units"].values():
        if unit.get("edition_id") == edition_id and unit.get("superseded"):
            replacement = state["units"].get(unit["replacement_unit_id"])
            superseded.append(
                {
                    "unit_id": unit["unit_id"],
                    "kind": unit["kind"],
                    "title": unit.get("title"),
                    "reason": unit.get("supersede_reason"),
                    "replacement_unit_id": unit["replacement_unit_id"],
                    "replacement_title": replacement.get("title") if replacement else None,
                }
            )

    return {
        "found": True,
        "edition_id": edition_id,
        "edition_label": state["editions"][edition_id]["edition_label"],
        "printed_content_note": "纸书内容以该印次原文为准；以下仅为追加的勘误与替代材料，不回写原文。",
        "supplements": sorted(supplements, key=lambda item: item["unit_id"]),
        "superseded": sorted(superseded, key=lambda item: item["unit_id"]),
    }


def license_usages(state: dict, image_unit_id: str) -> dict:
    """许可到期/处理时，定位一张图被哪些数字课件与公开页面使用。"""
    image = state["units"].get(image_unit_id)
    if image is None:
        return {"image_unit_id": image_unit_id, "found": False, "usages": []}
    return {
        "found": True,
        "image_unit_id": image_unit_id,
        "licensor": image.get("licensor"),
        "license_valid_until": image.get("license_valid_until"),
        "usages": list(image.get("usages", [])),
    }


def correction_trace(state: dict, correction_id: str) -> dict:
    """一条勘误的完整追溯：上报 → 复核 → 发布 → 库存处置 → 通知 → 回执。"""
    correction = state["corrections"].get(correction_id)
    if correction is None:
        return {"correction_id": correction_id, "found": False}

    issue = state["issues"].get(correction["issue_id"], {})
    duplicates = [
        dup["issue_id"]
        for dup in state["issues"].values()
        if dup.get("deduped_into") == issue.get("issue_id")
    ]

    notices = []
    for notice in correction["notifications"]:
        adoption = state["adoptions"].get(notice["adoption_id"], {})
        notices.append(
            {
                "school_id": notice["school_id"],
                "school_name": adoption.get("school_name"),
                "channel": notice["channel"],
                "sent_at": notice["sent_at"],
                "acknowledged_at": notice["acknowledged_at"],
            }
        )

    return {
        "found": True,
        "correction_id": correction_id,
        "published_at": correction["published_at"],
        "scope": correction["scope"],
        "issue": {
            "issue_id": issue.get("issue_id"),
            "severity": issue.get("severity"),
            "reported_at": issue.get("reported_at"),
            "unit_id": issue.get("unit_id"),
            "printing_id": issue.get("printing_id"),
            "duplicate_reports": sorted(duplicates),
        },
        "reviews": issue.get("reviews", []),
        "affects_printing_ids": correction["affects_printing_ids"],
        "affects_unit_ids": correction["affects_unit_ids"],
        "supplement_unit_id": correction.get("supplement_unit_id"),
        "inventory_disposals": correction["disposals"],
        "notifications": sorted(notices, key=lambda item: item["sent_at"]),
    }


def affected_adoptions(state: dict, correction_id: str) -> list[dict]:
    """紧急纠错通知名单：采用了任一受影响印次、且尚未收到该更正通知的学校。"""
    correction = state["corrections"].get(correction_id)
    if correction is None:
        return []

    notified_schools = {notice["school_id"] for notice in correction["notifications"]}
    result = []
    for adoption in state["adoptions"].values():
        if adoption.get("printing_id") not in correction["affects_printing_ids"]:
            continue
        result.append(
            {
                "adoption_id": adoption["adoption_id"],
                "school_id": adoption["school_id"],
                "school_name": adoption.get("school_name"),
                "printing_id": adoption["printing_id"],
                "frozen_until": adoption.get("frozen_until"),
                "already_notified": adoption["school_id"] in notified_schools,
            }
        )
    return sorted(result, key=lambda item: item["adoption_id"])
