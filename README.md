# 国家教材版本接续

本仓库保存教材版本接续后端的领域资料、事件契约与不变量校验，供业务团队在统一语义上继续建设。
覆盖两部国家级新闻学教材的书目、版次、印次、章节、概念、案例、法规引用、图像授权、
勘误、补充材料、课程采用与库存处置的可追踪关系。

## 已有内容

- `contracts/domain.schema.json`：领域事件契约（聚合、26 类事件、payload 条件约束）。
- `data/sample.json`：单事件信封联调样例。
- `data/sample_stream.json`：完整中文业务故事线（42 条事件），可直接作为联调夹具。
- `src/domain.py`：事件类型 → 聚合/payload 规格表与复核角色常量。
- `src/validator.py`：
  - `validate_event` 单事件信封与 payload 校验；
  - `validate_stream` 事件流跨事件不变量校验（见下）。
- `src/projections.py`：从事件流折叠的读模型（扫码视图、授权定位、更正追溯、通知名单）。
- `tests/`：28 项测试，覆盖题目各项要求。

## 核心原则

1. **事件只追加，已印刷内容不可被电子修订覆盖。**
   `event_id` 唯一，聚合内 `version` 连续递增；章节/案例/引注等内容单元只创建一次，
   后续变化只能产生勘误、`CONTENT_SUPERSEDED` 失效标注或后继补充材料。
2. **更正只能追加，不能回写。** 案例失效、平台下线、法规更新走 `CONTENT_SUPERSEDED`
   + `SUPPLEMENT_PUBLISHED`；原课堂依据保留可查，替代材料不反向改写它。
   电子勘误（`scope=electronic_errata`）必须挂载补充材料，禁止覆盖印次内容。
3. **重复上报不会造成多次更正。** 问题以 `dedupe_key` 收敛：重复件只能用
   `ISSUE_DEDUPED` 挂接到 canonical 问题；重复件不得进入复核或单独发布更正，
   一个问题至多产生一条更正。
4. **分工明确的复核门控。** 复核人必须先 `REVIEW_ASSIGNED` 才能出具意见；
   发布更正需作者 + 政策专家批准，紧急（`urgent`）问题追加主编终审；
   未撤回的 `REVIEW_HELD`/`REVIEW_REJECTED` 阻止发布，异议（`dissent`）随事件永久保留。
5. **紧急纠错定向通知。** `NOTIFICATION_SENT` 只发给采用了受影响印次的学校；
   同一（更正，采用）只通知一次；`NOTICE_ACKNOWLEDGED` 必须对应已发通知且唯一。
6. **教学基线冻结。** 教师 `ADOPTION_FROZEN` 一学期基线，`UPDATE_SCHEDULED`
   只能安排在冻结截止之后，冻结期内不得 `UPDATE_APPLIED`。
7. **授权可定位。** 图像通过 `IMAGE_USAGE_REGISTERED` 登记每个数字课件/公开页面使用点，
   许可到期可用读模型一次定位全部位置。

## 读模型

```python
import json
from src.projections import fold, scan_view, license_usages, correction_trace, affected_adoptions

events = json.load(open("data/sample_stream.json", encoding="utf-8"))
state = fold(events)

scan_view(state, "ed-j101-v1")                 # 学生扫码：与手中版次一致的勘误与替代
license_usages(state, "img-ch08-01")           # 授权到期：定位全部课件与公开页面
correction_trace(state, "cor-2026-001")        # 勘误 → 复核 → 处置 → 通知 → 回执
affected_adoptions(state, "cor-2026-001")      # 待通知/已通知学校名单
```

读模型只做折叠，不产生新事实；事实以事件日志为准。

## 本地检查

```bash
python3 -m unittest discover -s tests
```
