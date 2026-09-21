# 国家教材版本接续

本仓库保存该服务的领域资料与最小事件约定，供业务团队在统一语义上继续建设。

## 已有内容

- `contracts/domain.schema.json`：领域事件的基础字段、聚合类型和事件名称。
- `data/`：覆盖全部事件类型的中文样例记录，可用于联调。
- `src/`：只负责校验基础事件信封，不包含业务流程。
- `tests/`：验证样例与基础约定保持一致。

## 领域边界

当前资料围绕教材版次、勘误传播和法规引用整理。事件一旦被接收，其标识、发生时间和版本不应被原地改写；业务更正应产生后继记录。涉及个人、机构或商业敏感信息时，调用方只读取完成职责所必需的字段。

### 聚合类型

书目 `textbook_title`、版次 `textbook_edition`、章节 `content_unit`、概念条目 `concept_entry`、案例 `case_material`、法规引用 `regulation_citation`、图像授权 `image_license`、勘误 `correction_notice`、补充材料 `supplement`、课程采用 `course_adoption`、印次库存 `print_run`。

### 事件约定

- 印次登记与处置（`PRINT_RUN_RECORDED` / `INVENTORY_DISPOSED`）只追加记录；已印刷内容不得被电子修订覆盖，更正以勘误等后继记录呈现。
- 案例失效、平台下线或法规更新以 `CONTENT_SUPERSEDED` 建立替代链（`refs.replacement` 指向替代材料），曾经使用的课堂依据保留，不得反向改写。
- 紧急纠错以 `URGENT_NOTICE_DISPATCHED` 发布，`refs` 指向受影响的印次、章节与采用学校；学校回执以 `NOTICE_ACKNOWLEDGED` 记录。
- `ISSUE_REPORTED` 必须携带 `report_key`；同一 `report_key` 的重复上报归并为一条，不触发多次更正。
- 复核意见分歧时以 `PUBLICATION_HELD` 暂缓发布，异议随事件保留，待复核通过后以后继事件解除。
- 许可到期以 `LICENSE_EXPIRY_FLAGGED` 标记，`exposed_in` 列出受影响的数字课件与公开页面，供统一下线或替换。
- 教师以 `ADOPTION_FROZEN` 冻结学期教学基线，以 `UPDATE_SCHEDULED` 安排后续更新；学生扫码所见补充说明与手中版次保持一致。
- `refs` 串联勘误、审定、发行、库存处置与通知回执，支持从一条勘误全程追踪。

## 本地检查

```bash
python3 -m unittest discover -s tests
```
