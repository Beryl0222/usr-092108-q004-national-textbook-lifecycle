"""领域事件规格：事件类型、所属聚合与 payload 必填字段。

事件只追加（append-only）。任何对已发布内容的更正都必须产生后继事件，
不得原地改写既有事件，规格表与校验器共同强制这一点。
"""

from typing import NamedTuple


class EventSpec(NamedTuple):
    aggregate_type: str
    required_payload: tuple[str, ...]


# 事件名 -> （聚合类型, payload 必填字段）
EVENT_SPECS: dict[str, EventSpec] = {
    "TITLE_REGISTERED": EventSpec("textbook_catalog", ("title_id", "title", "discipline")),
    "EDITION_RELEASED": EventSpec("textbook_edition", ("edition_id", "title_id", "edition_label")),
    "PRINTING_RUN_RELEASED": EventSpec(
        "printing_run", ("printing_id", "edition_id", "impression", "printed_at", "quantity")
    ),
    "INVENTORY_DISPOSED": EventSpec("printing_run", ("printing_id", "action", "quantity")),
    "CHAPTER_PUBLISHED": EventSpec(
        "content_unit", ("unit_id", "edition_id", "unit_kind")
    ),
    "CONCEPT_PUBLISHED": EventSpec("content_unit", ("unit_id", "edition_id", "unit_kind")),
    "CASE_PUBLISHED": EventSpec("content_unit", ("unit_id", "edition_id", "unit_kind")),
    "REGULATION_CITED": EventSpec("content_unit", ("unit_id", "edition_id", "unit_kind")),
    "IMAGE_LICENSED": EventSpec("content_unit", ("unit_id", "edition_id", "unit_kind")),
    "IMAGE_USAGE_REGISTERED": EventSpec(
        "content_unit", ("image_unit_id", "usage_kind", "location")
    ),
    "CONTENT_SUPERSEDED": EventSpec(
        "content_unit", ("unit_id", "reason", "replacement_unit_id")
    ),
    "SUPPLEMENT_PUBLISHED": EventSpec(
        "content_unit", ("unit_id", "applies_edition_ids", "kind", "title")
    ),
    "ISSUE_REPORTED": EventSpec(
        "correction_notice",
        ("issue_id", "dedupe_key", "edition_id", "unit_id", "severity"),
    ),
    "ISSUE_DEDUPED": EventSpec("correction_notice", ("issue_id", "duplicate_of")),
    "REVIEW_ASSIGNED": EventSpec(
        "correction_notice", ("issue_id", "reviewer_id", "review_role")
    ),
    "REVIEW_APPROVED": EventSpec(
        "correction_notice", ("issue_id", "reviewer_id", "review_role")
    ),
    "REVIEW_REJECTED": EventSpec(
        "correction_notice", ("issue_id", "reviewer_id", "review_role")
    ),
    "REVIEW_HELD": EventSpec(
        "correction_notice", ("issue_id", "reviewer_id", "review_role")
    ),
    "CORRECTION_PUBLISHED": EventSpec(
        "correction_notice",
        ("issue_id", "correction_id", "affects_printing_ids", "affects_unit_ids", "scope"),
    ),
    "NOTIFICATION_SENT": EventSpec(
        "correction_notice", ("correction_id", "adoption_id", "school_id", "channel")
    ),
    "NOTICE_ACKNOWLEDGED": EventSpec(
        "correction_notice", ("correction_id", "adoption_id", "school_id")
    ),
    "ADOPTION_REGISTERED": EventSpec("course_adoption", ("adoption_id", "school_id", "edition_id")),
    "ADOPTION_FROZEN": EventSpec("course_adoption", ("adoption_id", "frozen_until")),
    "UPDATE_SCHEDULED": EventSpec("course_adoption", ("adoption_id", "scheduled_at")),
    "UPDATE_APPLIED": EventSpec("course_adoption", ("adoption_id", "applied_at")),
}

AGGREGATE_TYPES = {spec.aggregate_type for spec in EVENT_SPECS.values()}

# 创建内容单元的事件：同一 unit_id 只能被创建一次，
# 此后任何变化只能通过 SUPERSEDED / 勘误 / 补充材料等后继事件表达。
CONTENT_CREATORS = {
    "CHAPTER_PUBLISHED",
    "CONCEPT_PUBLISHED",
    "CASE_PUBLISHED",
    "REGULATION_CITED",
    "IMAGE_LICENSED",
    "SUPPLEMENT_PUBLISHED",
}

# 各实体的“创建事件”，实体 id 取自 payload，禁止重复创建。
ENTITY_CREATORS: dict[str, tuple[str, str]] = {
    # event_type: (id 字段, 实体类别名)
    "TITLE_REGISTERED": ("title_id", "书目"),
    "EDITION_RELEASED": ("edition_id", "版次"),
    "PRINTING_RUN_RELEASED": ("printing_id", "印次"),
    "ADOPTION_REGISTERED": ("adoption_id", "课程采用"),
    "ISSUE_REPORTED": ("issue_id", "问题报告"),
    "CORRECTION_PUBLISHED": ("correction_id", "更正"),
}

# 发布更正前必须取得批准的复核角色；urgent 问题还需主编终审。
BASE_REVIEW_ROLES = {"author", "policy_expert"}
URGENT_EXTRA_ROLE = "chief_editor"

REVIEW_DISPUTE_EVENTS = {"REVIEW_HELD", "REVIEW_REJECTED"}

ENVELOPE_FIELDS = (
    "event_id",
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "occurred_at",
    "version",
    "summary",
)
