"""领域事件信封校验与事件流不变量校验。

设计原则：
- 事件只追加：event_id 唯一，聚合内 version 只增；任何内容单元只创建一次，
  已印刷内容不会被后继事件原地改写，更正只能产生勘误/补充材料/印次处置等新记录。
- 重复上报靠 dedupe_key 收敛：同一问题只允许一条 canonical ISSUE_REPORTED，
  其余以 ISSUE_DEDUPED 挂接，且最多产生一条更正。
- 更正发布前必须完成分工复核；紧急问题追加主编终审；未撤回的暂缓/驳回阻止发布，
  异议随 REVIEW_HELD 的 dissent 永久保留。
- 通知只发给“采用了受影响印次”的学校，同一（更正，采用）只通知一次，
  回执必须能对应到已发通知。
"""

from datetime import datetime
from typing import Any

from .domain import (
    AGGREGATE_TYPES,
    BASE_REVIEW_ROLES,
    CONTENT_CREATORS,
    ENTITY_CREATORS,
    ENVELOPE_FIELDS,
    EVENT_SPECS,
    URGENT_EXTRA_ROLE,
)


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def validate_event(record: dict) -> list[str]:
    """校验单条事件信封与 payload 必填字段。"""
    errors = [f"缺少字段：{name}" for name in ENVELOPE_FIELDS if name not in record]

    event_type = record.get("event_type")
    if event_type is not None and event_type not in EVENT_SPECS:
        errors.append(f"未知事件类型：{event_type}")

    aggregate_type = record.get("aggregate_type")
    if aggregate_type is not None and aggregate_type not in AGGREGATE_TYPES:
        errors.append(f"未知聚合类型：{aggregate_type}")

    if event_type in EVENT_SPECS:
        spec = EVENT_SPECS[event_type]
        if aggregate_type is not None and aggregate_type != spec.aggregate_type:
            errors.append(
                f"事件 {event_type} 必须归属聚合 {spec.aggregate_type}，实际为 {aggregate_type}"
            )
        payload = record.get("payload")
        if not isinstance(payload, dict):
            errors.append("payload 必须是对象")
        else:
            for field in spec.required_payload:
                if field not in payload:
                    errors.append(f"payload 缺少字段：{field}")

    if "version" in record and (not isinstance(record["version"], int) or record["version"] < 1):
        errors.append("version 必须是正整数")

    if "occurred_at" in record and _parse_dt(record["occurred_at"]) is None:
        errors.append("occurred_at 必须是合法的 date-time")

    return errors


def validate_stream(events: list[dict]) -> list[str]:
    """对整条事件日志做跨事件不变量校验，返回中文错误列表（空列表表示通过）。"""
    errors: list[str] = []

    seen_event_ids: set[str] = set()
    agg_versions: dict[str, int] = {}

    titles: dict[str, dict] = {}
    editions: dict[str, dict] = {}
    printings: dict[str, dict] = {}
    units: dict[str, dict] = {}
    adoptions: dict[str, dict] = {}
    issues: dict[str, dict] = {}
    corrections: dict[str, dict] = {}
    dedupe_keys: dict[str, str] = {}
    notifications: set[tuple[str, str]] = set()
    acks: set[tuple[str, str]] = set()
    schedules: dict[str, dict] = {}
    # 跨事件引用可能在同一批次相邻写入（更正 ↔ 电子勘误补充材料），
    # 这类存在性/适用性检查在流末统一完成。
    pending_supplement_checks: list[tuple[dict, str, str]] = []
    pending_correction_supplements: list[tuple[dict, str, str]] = []
    pending_supersedes: list[tuple[dict, str, str]] = []

    def err(event: dict | None, message: str) -> None:
        eid = event.get("event_id", "?") if isinstance(event, dict) else "?"
        errors.append(f"[事件 {eid}] {message}")

    for event in events:
        if not isinstance(event, dict):
            errors.append("事件必须是对象")
            continue

        single_errors = validate_event(event)
        for single_error in single_errors:
            err(event, single_error)
        # 信封/载荷不完整的事件无法参与后续跨事件校验。
        if single_errors:
            continue

        event_id = event["event_id"]
        event_type = event["event_type"]
        aggregate_id = event["aggregate_id"]
        ts = _parse_dt(event["occurred_at"])
        payload = event["payload"]

        if event_id in seen_event_ids:
            err(event, f"event_id 重复：{event_id}（事件不可重复写入）")
        seen_event_ids.add(event_id)

        expected_version = agg_versions.get(aggregate_id, 0) + 1
        if event["version"] != expected_version:
            err(
                event,
                f"聚合 {aggregate_id} 的 version 应为 {expected_version}，"
                f"实际为 {event['version']}（只允许追加，不允许跳号或改写）",
            )
        agg_versions[aggregate_id] = event["version"]

        # —— 实体创建：同一实体只能创建一次 ——
        if event_type in ENTITY_CREATORS:
            id_field, label = ENTITY_CREATORS[event_type]
            entity_id = payload[id_field]
            registry = {
                "title_id": titles,
                "edition_id": editions,
                "printing_id": printings,
                "adoption_id": adoptions,
                "issue_id": issues,
                "correction_id": corrections,
            }[id_field]
            if entity_id in registry:
                err(event, f"{label} {entity_id} 已创建，禁止重复创建（只追加，不改写）")

        if event_type == "TITLE_REGISTERED":
            titles[payload["title_id"]] = {"title": payload["title"]}

        elif event_type == "EDITION_RELEASED":
            if payload["title_id"] not in titles:
                err(event, f"引用的书目 {payload['title_id']} 不存在")
            editions[payload["edition_id"]] = {
                "title_id": payload["title_id"],
                "label": payload["edition_label"],
            }

        elif event_type == "PRINTING_RUN_RELEASED":
            edition_id = payload["edition_id"]
            if edition_id not in editions:
                err(event, f"引用的版次 {edition_id} 不存在")
            printings[payload["printing_id"]] = {
                "edition_id": edition_id,
                "impression": payload["impression"],
            }

        elif event_type in CONTENT_CREATORS:
            unit_id = payload["unit_id"]
            edition_id = payload.get("edition_id")
            if event_type != "SUPPLEMENT_PUBLISHED" and edition_id not in editions:
                err(event, f"内容单元引用的版次 {edition_id} 不存在")
            if unit_id in units:
                err(event, f"内容单元 {unit_id} 已发布，禁止覆盖（已发布内容不可原地改写）")
            else:
                units[unit_id] = {
                    "kind": payload.get("unit_kind", "supplement"),
                    "edition_id": edition_id,
                    "superseded": False,
                }
            if event_type == "SUPPLEMENT_PUBLISHED":
                for edition_ref in payload["applies_edition_ids"]:
                    if edition_ref not in editions:
                        err(event, f"补充材料适用的版次 {edition_ref} 不存在")
                if "anchor_unit_id" in payload and payload["anchor_unit_id"] not in units:
                    err(event, f"补充材料锚定的内容单元 {payload['anchor_unit_id']} 不存在")
                if "correction_id" in payload and payload["correction_id"] not in corrections:
                    # 更正可能与补充材料同批写入，流末再校验。
                    pending_supplement_checks.append(
                        (event, payload["unit_id"], payload["correction_id"])
                    )
                units[unit_id]["supplement"] = {
                    "applies": set(payload["applies_edition_ids"]),
                    "kind": payload["kind"],
                }

        elif event_type == "IMAGE_USAGE_REGISTERED":
            image_id = payload["image_unit_id"]
            image = units.get(image_id)
            if image is None or image.get("kind") != "image":
                err(event, f"图像授权单元 {image_id} 不存在或不是图像")
            else:
                image.setdefault("usages", []).append(
                    {"kind": payload["usage_kind"], "location": payload["location"]}
                )

        elif event_type == "CONTENT_SUPERSEDED":
            unit_id = payload["unit_id"]
            replacement_id = payload["replacement_unit_id"]
            if unit_id not in units:
                err(event, f"被替代的内容单元 {unit_id} 不存在")
            elif unit_id == replacement_id:
                err(event, "替代材料不能指向单元自身")
            if unit_id in units and units[unit_id].get("superseded"):
                err(event, f"单元 {unit_id} 已标注失效，失效标注只能追加一次")
            if unit_id in units:
                units[unit_id]["superseded"] = True
                units[unit_id]["replacement"] = replacement_id
            # 替代材料可与失效标注同批相邻写入，存在性在流末统一校验。
            pending_supersedes.append((event, unit_id, replacement_id))

        elif event_type == "ISSUE_REPORTED":
            issue_id = payload["issue_id"]
            dedupe_key = payload["dedupe_key"]
            if dedupe_key in dedupe_keys:
                err(
                    event,
                    f"问题 {issue_id} 的 dedupe_key“{dedupe_key}”已被 "
                    f"{dedupe_keys[dedupe_key]} 占用：重复上报必须以 ISSUE_DEDUPED 挂接",
                )
            if payload["edition_id"] not in editions:
                err(event, f"问题引用的版次 {payload['edition_id']} 不存在")
            if payload["unit_id"] not in units:
                err(event, f"问题引用的内容单元 {payload['unit_id']} 不存在")
            printing_id = payload.get("printing_id")
            if printing_id is not None:
                if printing_id not in printings:
                    err(event, f"问题引用的印次 {printing_id} 不存在")
                elif printings[printing_id]["edition_id"] != payload["edition_id"]:
                    err(event, f"印次 {printing_id} 不属于版次 {payload['edition_id']}")
            dedupe_keys[dedupe_key] = issue_id
            issues[issue_id] = {
                "dedupe_key": dedupe_key,
                "severity": payload["severity"],
                "edition_id": payload["edition_id"],
                "printing_id": printing_id,
                "unit_id": payload["unit_id"],
                "deduped": False,
                "reviewers": {},
                "correction_id": None,
            }

        elif event_type == "ISSUE_DEDUPED":
            dup_id = payload["issue_id"]
            canonical_id = payload["duplicate_of"]
            if dup_id in issues:
                err(event, f"问题 {dup_id} 已是 canonical 问题，不能再被挂接为重复件")
            canonical = issues.get(canonical_id)
            if canonical is None:
                err(event, f"挂接目标问题 {canonical_id} 不存在")
            elif canonical["deduped"]:
                err(event, f"挂接目标 {canonical_id} 本身是重复件，不能作为 canonical")
            issues[dup_id] = {"deduped": True, "canonical": canonical_id}

        elif event_type in {"REVIEW_ASSIGNED", "REVIEW_APPROVED", "REVIEW_REJECTED", "REVIEW_HELD"}:
            issue = issues.get(payload["issue_id"])
            if issue is None:
                err(event, f"复核引用的问题 {payload['issue_id']} 不存在")
            elif issue.get("deduped"):
                err(event, "重复件不得进入复核流程（其 canonical 问题统一处理）")
            elif event_type == "REVIEW_ASSIGNED":
                reviewer = payload["reviewer_id"]
                if reviewer in issue["reviewers"]:
                    err(event, f"复核人 {reviewer} 对该问题已分工，不得重复分工")
                issue["reviewers"][reviewer] = {"role": payload["review_role"], "state": "assigned"}
            else:
                reviewer = payload["reviewer_id"]
                slot = issue["reviewers"].get(reviewer)
                if slot is None:
                    err(event, f"复核人 {reviewer} 未经分工（REVIEW_ASSIGNED）不得出具意见")
                elif slot["role"] != payload["review_role"]:
                    err(event, f"复核人 {reviewer} 的角色与分工记录不一致")
                else:
                    state = {
                        "REVIEW_APPROVED": "approved",
                        "REVIEW_REJECTED": "rejected",
                        "REVIEW_HELD": "held",
                    }[event_type]
                    slot["state"] = state
                    if "dissent" in payload:
                        slot["dissent"] = payload["dissent"]

        elif event_type == "CORRECTION_PUBLISHED":
            issue = issues.get(payload["issue_id"])
            correction_id = payload["correction_id"]
            if issue is None:
                err(event, f"更正引用的问题 {payload['issue_id']} 不存在")
            elif issue.get("deduped"):
                err(event, "重复件不得单独发布更正")
            elif issue["correction_id"] is not None:
                err(
                    event,
                    f"问题 {payload['issue_id']} 已产生更正 {issue['correction_id']}，"
                    "重复上报不得造成多次更正",
                )
            else:
                required_roles = set(BASE_REVIEW_ROLES)
                if issue["severity"] == "urgent":
                    required_roles.add(URGENT_EXTRA_ROLE)
                role_states: dict[str, str] = {}
                for slot in issue["reviewers"].values():
                    # 同一角色多名复核人时，以该角色最后一名复核人的状态为准。
                    role_states[slot["role"]] = slot["state"]
                for role in required_roles:
                    if role_states.get(role) != "approved":
                        err(
                            event,
                            f"更正缺少必需的复核批准：{role}（紧急问题还需主编终审）",
                        )
                for slot in issue["reviewers"].values():
                    if slot["state"] in {"held", "rejected"}:
                        err(
                            event,
                            f"复核人 {slot['role']} 的暂缓/驳回尚未撤回，争议表述不得发布",
                        )

            for printing_id in payload["affects_printing_ids"]:
                if printing_id not in printings:
                    err(event, f"受影响印次 {printing_id} 不存在")
                elif issue is not None and not issue.get("deduped"):
                    if printings[printing_id]["edition_id"] != issue["edition_id"]:
                        err(event, f"受影响印次 {printing_id} 不属于问题所在版次")
            for unit_id in payload["affects_unit_ids"]:
                if unit_id not in units:
                    err(event, f"受影响内容单元 {unit_id} 不存在")

            supplement_id = payload.get("supplement_unit_id")
            if supplement_id is None and payload["scope"] == "electronic_errata":
                err(
                    event,
                    "scope=electronic_errata 必须提供 supplement_unit_id："
                    "电子勘误只能以补充材料呈现，不得覆盖已印刷内容",
                )
            elif supplement_id is not None and issue is not None and not issue.get("deduped"):
                # 补充材料可能与更正在同一批次相邻写入，其存在性/适用性在流末统一校验。
                pending_correction_supplements.append(
                    (event, issue["edition_id"], supplement_id)
                )

            corrections[correction_id] = {
                "issue_id": payload["issue_id"],
                "printing_ids": set(payload["affects_printing_ids"]),
                "unit_ids": set(payload["affects_unit_ids"]),
                "scope": payload["scope"],
            }
            if issue is not None and not issue.get("deduped"):
                issue["correction_id"] = correction_id

        elif event_type == "INVENTORY_DISPOSED":
            printing_id = payload["printing_id"]
            if printing_id not in printings:
                err(event, f"库存处置引用的印次 {printing_id} 不存在")
            linked = payload.get("correction_id")
            if linked is not None and linked not in corrections:
                err(event, f"库存处置引用的更正 {linked} 尚未发布")

        elif event_type == "NOTIFICATION_SENT":
            correction = corrections.get(payload["correction_id"])
            adoption = adoptions.get(payload["adoption_id"])
            if correction is None:
                err(event, "通知引用的更正尚未发布")
            if adoption is None:
                err(event, f"通知引用的课程采用 {payload['adoption_id']} 不存在")
            elif payload["school_id"] != adoption["school_id"]:
                err(event, "通知学校与采用记录不一致")
            if correction is not None and adoption is not None:
                if adoption["printing_id"] not in correction["printing_ids"]:
                    err(
                        event,
                        f"学校 {payload['school_id']} 采用的印次 {adoption['printing_id']} "
                        "不在更正影响范围内，不得向其发送紧急纠错通知",
                    )
                key = (payload["correction_id"], payload["adoption_id"])
                if key in notifications:
                    err(event, "同一（更正，采用）已通知过，禁止重复通知")
                notifications.add(key)

        elif event_type == "NOTICE_ACKNOWLEDGED":
            key = (payload["correction_id"], payload["adoption_id"])
            if key not in notifications:
                err(event, "回执没有对应的已发送通知")
            adoption = adoptions.get(payload["adoption_id"])
            if adoption is not None and payload["school_id"] != adoption["school_id"]:
                err(event, "回执学校与采用记录不一致")
            if key in acks:
                err(event, "同一通知已存在回执，禁止重复回执")
            acks.add(key)

        elif event_type == "ADOPTION_REGISTERED":
            edition_id = payload["edition_id"]
            printing_id = payload.get("printing_id")
            if edition_id not in editions:
                err(event, f"采用引用的版次 {edition_id} 不存在")
            if printing_id is not None:
                if printing_id not in printings:
                    err(event, f"采用引用的印次 {printing_id} 不存在")
                elif printings[printing_id]["edition_id"] != edition_id:
                    err(event, f"印次 {printing_id} 不属于版次 {edition_id}")
            adoptions[payload["adoption_id"]] = {
                "school_id": payload["school_id"],
                "edition_id": edition_id,
                "printing_id": printing_id,
                "frozen_until": None,
            }

        elif event_type == "ADOPTION_FROZEN":
            adoption = adoptions.get(payload["adoption_id"])
            if adoption is None:
                err(event, f"冻结引用的采用 {payload['adoption_id']} 不存在")
            elif ts is not None and _parse_dt(payload["frozen_until"]) <= ts:
                err(event, "冻结截止时间必须晚于冻结操作时间")
            else:
                adoption["frozen_until"] = _parse_dt(payload["frozen_until"])

        elif event_type == "UPDATE_SCHEDULED":
            adoption = adoptions.get(payload["adoption_id"])
            scheduled_at = _parse_dt(payload["scheduled_at"])
            if adoption is None:
                err(event, f"更新安排引用的采用 {payload['adoption_id']} 不存在")
            elif adoption["frozen_until"] is None:
                err(event, "教学基线尚未冻结，不能安排解冻后更新")
            elif scheduled_at < adoption["frozen_until"]:
                err(event, "安排的更新时间落在教学基线冻结期内，须在冻结截止之后")
            schedules[payload["adoption_id"]] = {"scheduled_at": scheduled_at, "applied": False}

        elif event_type == "UPDATE_APPLIED":
            adoption = adoptions.get(payload["adoption_id"])
            schedule = schedules.get(payload["adoption_id"])
            applied_at = _parse_dt(payload["applied_at"])
            if adoption is None:
                err(event, f"更新生效引用的采用 {payload['adoption_id']} 不存在")
            elif schedule is None:
                err(event, "更新生效前必须先有 UPDATE_SCHEDULED")
            elif schedule["applied"]:
                err(event, "安排的更新已生效，禁止重复应用")
            elif adoption["frozen_until"] is not None and applied_at < adoption["frozen_until"]:
                err(event, "教学基线冻结期内不得应用更新")
            else:
                schedule["applied"] = True
            for correction_id in payload.get("correction_ids", []):
                correction = corrections.get(correction_id)
                if correction is None:
                    err(event, f"更新引用的更正 {correction_id} 不存在")
                elif adoption is not None and adoption["printing_id"] not in correction["printing_ids"]:
                    err(event, f"更正 {correction_id} 不影响该采用所持印次，不应进入本次更新")

    # —— 流末：同批写入的相互引用统一解析 ——
    for event, unit_id, correction_id in pending_supplement_checks:
        if correction_id not in corrections:
            err(event, f"补充材料引用的更正 {correction_id} 不存在")

    for event, edition_id, supplement_id in pending_correction_supplements:
        supplement = units.get(supplement_id)
        if supplement is None:
            err(event, f"更正挂载的补充材料 {supplement_id} 不存在")
        elif edition_id not in supplement.get("supplement", {}).get("applies", set()):
            err(event, f"补充材料 {supplement_id} 不适用于问题所在版次 {edition_id}")

    for event, unit_id, replacement_id in pending_supersedes:
        if replacement_id not in units:
            err(event, f"单元 {unit_id} 的替代材料 {replacement_id} 不存在（须先发布后继材料）")

    return errors
