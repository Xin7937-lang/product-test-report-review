# 审核报告输出模板

严格按此结构输出审核结果。语言：中文。**先把发现整理成 JSON，再写 Markdown；HTML 是主交付。** 所有发现必须带位置索引、原文摘录、`预期 vs 实际` 与 `置信度`。

## JSON-first 归一化字段（写 `.issues.json` 时至少包含）

- `issue_id`：单报告内唯一 ID（如 `ISS-001`）
- `report_file`
- `category`：清单或缺陷库编号（如 `DC-G02`、`DL-C06`、`CD-S09`）
- `severity`：`严重` / `一般` / `建议` / `人工核对项`
- `title`
- `expected`
- `actual`
- `location`：可为单个或多个旧版位置索引，也可直接传结构化位置对象
- `location_detail` / `location_details`：推荐；由 `evidence.json` 的 `location.v1` 生成或补全
- `location_display`：推荐的人类可读位置文本
- `evidence_quote`
- `evidence_type`：`text` / `table` / `pair-check` / `vision` / `manual`
- `source_mode`：`text` / `vision` / `hybrid` / `manual`
- `confidence`：`高` / `中` / `人工`
- `suggestion`
- `figure_refs`：（可选）涉及的图/表/页编号
- `location_schema_version`：归一化结果为 `location.v1`

---

```markdown
# DV测试报告审核报告

## 一、审核概要

| 项 | 内容 |
|---|---|
| 受审文件 | <文件名>（.docx/.pptx） |
| 工件目录 | <报告文件名>.review-artifacts |
| 图像审阅模式 | <vision / no-vision / hybrid> |
| 报告编号 | <提取到的编号，多处不一致时全部列出> |
| 样品/项目 | <样品名称型号、试验项目名称> |
| 引用标准 | <报告中引用的标准及年号> |
| 覆盖度核查 | <使用的 `references\standards\` 矩阵名称；未使用则说明原因> |
| 总体评价 | <通过 / 修改后通过 / 存在严重问题需整改> |

## 二、发现统计

| 严重度 | 数量 |
|---|---|
| 严重 | n |
| 一般 | n |
| 建议 | n |
| 人工核对项 | n |

## 三、发现明细

### 严重
| # | 类别 | 详细位置（可点击） | 问题描述 | 预期 | 实际 | 证据摘录 | 置信度 | 建议 |
|---|---|---|---|---|---|---|---|---|
| 1 | DC-G02 | 页码未解析 · 第 35 个段落 / 第 2 个表格 · [P0035]/[T02] | 汇总结论与单项数据矛盾 | 单项超限时不应判合格 | 表中实测超限但汇总写“通过” | “实测 38.2mΩ（限值≤35mΩ）…判定：合格” | 高 | 复核该项判定，必要时改判并触发整改流程 |

### 一般
（同上表结构）

### 建议
（同上表结构）

## 四、图/表清单与图文一致性

| # | 预期对象 | 实际对象/详细位置 | 审阅方式 | 一致性 | 备注 |
|---|---|---|---|---|---|
| 1 | 正文称“见图3振动后外观” | 第 8 页 · 第 1 个形状（图片） · [S08-1]；无可机读正文说明 | manual | ⚠️ 待人工核对 | 当前环境无视觉能力，需人工核对图片内容 |
| 2 | 结论页称“详见图5曲线” | 第 211 个段落 / 第 12 页 · [P0211]/[S12] | vision | ❌ 不一致 | 曲线显示峰值超限，正文仍写“无异常” |

## 五、试验项目覆盖度（有 `references\standards\` 矩阵时必填）

| 大纲/标准项目 | 条款 | 报告对应章节 | 覆盖状态 |
|---|---|---|---|
| 振动试验 | X.X | 4.3 节 [P0089] | ✅ 已覆盖 |
| 盐雾试验 | X.X | — | ❌ 漏项 |

## 六、人工核对项（AI 无法确认，需工程师核查）

| # | 事项 | 详细位置（可点击） | 原因 | 当前证据 |
|---|---|---|---|---|
| 1 | 第 5 页截图内容 | 第 5 页 · 第 2 个形状（图片） · [S05-2] | no-vision/manual fallback；图片内文字不可机读 | `extract_report.py` 标记为图片页 |
| 2 | 校准证书真实性 | 页码未解析 · 第 42 个段落 · [P0042] | 文本仅声明“有效期至 2026-05”，需核证书原件 | “校准有效期至2026年5月” |

## 七、已核对项（抽样说明）

<列出已核对且符合的清单条目范围，如：DC-A01~A06、DL-C01~C06 已核对，无异常>

---

**免责声明**：本审核由 AI 辅助完成，所有发现均需工程师复核确认。最终判定权归审核工程师。
**审核依据**：references\checklist-doc-compliance.md、references\checklist-data-logic.md、references\common-defects.md。
```

---

## 填写要求

- 先落 `.issues.json`，再把它投影成上面的 Markdown/HTML；批量汇总优先依赖 `.issues.json` 聚合。
- “类别”列填清单条目编号（DC-x / DL-x）或缺陷库编号（CD-x），便于追溯与统计。
- **位置优先写详细位置，不要只写 `[P0035]`**。保留旧锚点作为机器追溯键；HTML 会把 `source_file` 渲染为“打开原文件”，把 `evidence_path` + `evidence_anchor` 渲染为“打开证据定位”。
- PPTX 至少写明第几页和第几个形状/表格；若有 `position`，同时保留 EMU 几何和相对百分比。DOCX 至少写明段落/表格序号和章节路径；真实页码只能来自解析器或外部页码映射，`page_status=unavailable` 时不得猜测。
- 结构化位置示例：

  ```json
  {
    "schema_version": "location.v1",
    "display": "第 8 页 · 第 3 个形状（图片） · 左侧 10% · 上方 25% · [S08-3]",
    "source_file": "C:\\reports\\demo.pptx",
    "source_anchor": "[S08-3]",
    "slide": 8,
    "shape": 3,
    "object_type": "image",
    "position_summary": "左侧 10% · 上方 25% · 尺寸 40%×30%",
    "evidence_path": "C:\\reports\\demo.pptx.review-artifacts\\evidence.html",
    "evidence_anchor": "location-S08-3"
  }
  ```

- DOCX 页码不可用时，显示应类似 `页码未解析 · 第 35 个段落 · 章节：6 单项试验结果 / 6.1 振动试验 · [P0035]`，并保留“未猜测页码”的说明。
- **每条发现都要有 `预期` 与 `实际`**；即使是格式问题，也要写清“应统一成什么 / 当前实际怎样”。
- `置信度` 规则：`高`=文字/表格直接证据；`中`=跨段归纳、部分视觉证据或图文联判；`人工`=AI 不能确认，只能转人工核对项。不要输出“低置信度定论”。
- `evidence_type=vision` 或 `source_mode=vision/hybrid` 的问题，必须在“图像审阅模式”中说明视觉前提；无视觉能力时改写入第六节。
- 证据摘录控制在 50 字以内，够定位即可；原文过长的用“…”截断。
- 没有任何严重/一般/建议发现时，相应小节写“无”，不要省略章节。
- 图/表清单与图文一致性若不适用，写“无”；不要因为没看图就省略该节。
- `.review.md` 仅作为兼容或人工编辑中间稿，建议写入 `*.review-artifacts\` 目录；JSON-first 流程运行 `python scripts\render_review.py <issues.json> -o <报告目录>\<报告文件名>.review.html`。旧版 Markdown 流程仍可运行 `python scripts\make_html_report.py <本md> --rm`。
