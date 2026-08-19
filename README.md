# product-dv-report-review

产品验证（DV，设计验证）测试报告通用审核 Skill，适用于电池包等各类产品的 DV/型式试验报告与汇报 PPT。适用于 **GitHub Copilot CLI**、**OpenCode** 及其它支持 Agent Skills 的工具。

核心升级：**分阶段证据提取**、**上下文驱动的中英技术语言复核**、**图/表“应有 vs 实有”清单**、**vision-first / no-vision fallback**、**JSON-first 问题归一化**、**HTML 主交付 + 每份报告独立工件目录**。

| 能做什么 | 不做什么 |
|---|---|
| 文档合规性、数据逻辑、图文一致性、中英技术语言检查 | 试验方法技术对错判定 |
| 先提证据、再判问题；每条发现带预期/实际/摘录/置信度 | 凭猜测给图像页下定论 |
| 视觉能力可用时优先复核图像；不可用时显式转人工核对 | 静默跳过图片/截图/SmartArt |
| 单份审核、批量汇总、续跑恢复 | 旧版 `.doc` / `.ppt` |

---

## 目录结构

```
├── SKILL.md
├── references/
│   ├── checklist-doc-compliance.md
│   ├── checklist-data-logic.md
│   ├── checklist-auto-hints.md
│   ├── common-defects.md
│   ├── review-output-template.md
│   ├── batch-summary-template.md
│   ├── vision-observation-template.json
│   ├── report-template-profile.md
│   ├── standards-active.md
│   └── standards/
├── scripts/
│   ├── extract_report.py
│   ├── report_checks.py
│   ├── evidence_pipeline.py
│   ├── language_review.py
│   ├── figure_checks.py
│   ├── normalize_review.py
│   ├── render_review.py
│   └── make_html_report.py       # 兼容旧版 Markdown 审核稿
├── tests/
└── deploy.ps1
```

## 默认产物（单份报告）

每份报告在原文件同目录下生成一个独立工件目录：

```
D:\reports\
├── BTR-DV-2025-0042.docx
├── BTR-DV-2025-0042.docx.review.html
└── BTR-DV-2025-0042.docx.review-artifacts\
   ├── BTR-DV-2025-0042.docx.workpaper.md
   ├── BTR-DV-2025-0042.docx.issues.json
   ├── evidence.json
   ├── language-review-input.json
   ├── figure-review.json
   └── media\
```

说明：
- `.review.html` 是**主交付物**，保存在报告同目录。
- `.workpaper.md` 是保留溯源底稿（自动检查线索 + 提取全文）。
- `.issues.json` 是归一化问题台账，供批量聚合、续跑恢复和后续统计使用。
- `evidence.json`、`language-review-input.json`、`figure-review.json` 和 `media\` 用于还原审核证据。
- `.review.md` / `.extracted.md` 为中间文件，默认在成功后删除。

## 审核流程（单份）

1. **提取文本与证据**：`extract_report.py` 保留定位索引；`evidence_pipeline.py` 生成结构化文档单元、上下文、媒体清单和预期/实际图项。
2. **确定性检查**：`report_checks.py` 保留现有 CHECK-1~CHECK-8，生成 `.workpaper.md`。
3. **上下文驱动复核**：
   - 先抽取证据包（封面、样品信息、汇总表、结论、关键图表声明、全部 ⚠️ 图像页）；
   - 再按 `checklist-doc-compliance.md`、`checklist-data-logic.md`、`common-defects.md` 核对；
   - 独立做**中英技术语言复核**和**图/表清单 + 图文一致性复核**。
4. **JSON-first 归一化**：`language_review.py` 准备语言上下文，`figure_checks.py` 检查图项；模型/人工发现经 `normalize_review.py` 归一化。
5. **HTML 交付**：`render_review.py` 从归一化 JSON 生成 `.review.html`；`make_html_report.py` 保留旧版 Markdown 兼容路径。

## JSON-first 命令链

以下命令构成一份报告的最小新流程；`<artifact-dir>` 应位于报告同目录的
`<报告文件名>.review-artifacts\`：

```powershell
python scripts\extract_report.py <报告文件> -o <artifact-dir>\<报告文件名>.extracted.md
python scripts\evidence_pipeline.py <报告文件> --output-dir <artifact-dir>
python scripts\report_checks.py <artifact-dir>\<报告文件名>.extracted.md
python scripts\language_review.py <artifact-dir>\evidence.json
python scripts\figure_checks.py <artifact-dir>\evidence.json --no-vision
python scripts\normalize_review.py <artifact-dir>\<报告文件名>.raw-review.json `
  -o <artifact-dir>\<报告文件名>.issues.json `
  --evidence <artifact-dir>\evidence.json `
  --figure-review <artifact-dir>\figure-review.json `
  --language-input <artifact-dir>\language-review-input.json `
  --workpaper <artifact-dir>\<报告文件名>.workpaper.md
python scripts\render_review.py <artifact-dir>\<报告文件名>.issues.json `
  -o <报告目录>\<报告文件名>.review.html
```

`raw-review.json` 由模型或人工语义复核产生，不是脚本自动生成；至少应包含
`issues`，也可包含 `language_findings`、`figure_inventory`、`metadata` 和
`checked_items`。图表规则结果由单独的 `figure-review.json` 通过命令行合并。
字段要求以
`references\review-output-template.md` 为准。具备可靠视觉能力时，将模型观察写入
`references\vision-observation-template.json` 兼容的 JSON，再用
`figure_checks.py --vision-observations <观察.json>` 替代 `--no-vision`。

## 视觉策略（重要）

- **vision-first**：如果运行环境能看渲染后的页面、截图或导出图像，图像相关问题优先用视觉复核。
- **no-vision/manual fallback**：若当前环境无法可靠看图，则图像页、截图页、嵌入图表对象只可进入“人工核对项”；要明确写明原因，不能假装已识别图片内容。
- 无视觉能力时可提醒用户切换到当前工具链明确支持图像输入的视觉模型，具体模型名称以运行环境能力说明为准。
- 文本脚本仍会把不可机读对象打上 ⚠️ 标记，帮助建立“应有 vs 实有”图表清单。

## 批量审核与续跑

- 每份报告独立生成 `*.review-artifacts\` 子目录，HTML 主交付保存在报告目录。
- 批量中断后再次运行时：**报告目录存在 `.review.html` 且工件目录同时存在 `.issues.json` 与 `evidence.json` 才算完成**；缺一则继续补齐。
- 全批完成后，在批次根目录输出 `batch-review-summary.html`（主交付）与相应的中间汇总 md（默认删除）。

## 本地部署与更新

在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy.ps1
```

该命令将当前技能包同步到用户级 `.agents\skills\product-dv-report-review\`。
更新后应至少运行 `python scripts\render_review.py --smoke-test`，再开始审核新报告。

## 使用示例

### 单份 Word

```
审核一下这份 DV 测试报告：D:\reports\BTR-DV-2025-0042.docx
```

### 单份 PPT（含图像页）

```
帮我检查 D:\reports\项目DV汇报.pptx，有没有图文不一致或截图页风险
```

### 批量

```
批量审核 D:\reports\batch01
```

### 同项目 Word + PPT 对照

```
审核 D:\reports\项目DV报告.docx，并对照同项目 PPT：D:\reports\项目DV汇报.pptx
```

## 验收场景

1. **单份 Word**：生成独立工件目录，至少包含 `.review.html`、`.workpaper.md`、`.issues.json`；每条发现带 `预期/实际/证据/置信度`。
2. **PPT + 可视觉复核**：关键截图/图表页先做视觉检查，再输出图/表清单与图文一致性结论。
3. **PPT + 无视觉能力**：报告中明确出现 `no-vision/manual fallback` 说明；相关页只进入人工核对项，不写成定论。
4. **中英混排报告**：能独立识别中文术语、英文缩写、双语标题/图注不一致问题，即使数值本身没错也可提出。
5. **批量续跑**：已有报告目录 `.review.html` + 工件目录 `.issues.json` + `evidence.json` 的报告被跳过，剩余报告继续处理，并最终生成批量汇总。
6. **Word/PPT 成对对照**：`--pair` 结果被纳入 DC-P05，能指出判定结果、样本量、关键数值或图表来源不一致。

## 常见问题

**Q1：最终交付是什么？**
A：单份以 `.review.html` 为主交付；`.workpaper.md` 和 `.issues.json` 保留在工件目录中供溯源与聚合。

**Q2：必须提供公司模板或标准矩阵吗？**
A：不是。没有也能审；提供后可增加结构完整性、覆盖度和条件对照检查。

**Q3：能自动读懂图片、截图、SmartArt 吗？**
A：脚本不能。只有在当前模型/工具链确实支持视觉复核时，才可对图像内容给出结论；否则必须转人工核对项，并在结果中保留 `no-vision/manual fallback` 说明。

**Q4：会不会替代工程师最终判定？**
A：不会。所有输出都保留“AI 辅助、人工终判”的免责声明。

## 限制

- 仅支持 `.docx` / `.pptx`；旧版 `.doc` / `.ppt` 请先另存为新格式。
- 图片、SmartArt、嵌入图表对象中的文字默认不可机读。
- 脚本线索为启发式规则，写入报告前必须回原文核对。

## 维护

- 标准年号表：`references\standards-active.md`
- 缺陷库：`references\common-defects.md`
- 模板档案：`references\report-template-profile.md`
- 输出与聚合规范：`references\review-output-template.md`、`references\batch-summary-template.md`
