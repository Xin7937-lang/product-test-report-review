---
name: product-dv-report-review
description: 审核产品验证（DV，设计验证）测试报告与试验汇报（Word .docx / PPT .pptx），采用分阶段证据提取、确定性检查、上下文驱动语义复核与 HTML 主交付流程；覆盖文档合规性、数据逻辑、图文一致性与中英技术语言问题，支持单份审核与批量汇总/续跑。当用户提供 DV 测试报告、试验报告、型式试验报告、测试总结或汇报 PPT，并要求审核、检查、评审、review、把关时使用。
---

# 产品验证（DV）测试报告审核

## 适用范围与限制

- 支持 `.docx` 与 `.pptx`；旧版 `.doc`/`.ppt` 请先另存为新格式。
- 审核深度：**文档合规性 + 数据逻辑一致性 + 图/表/正文/中英技术语言一致性**。不做试验方法的技术判定（那是工程师的职责）。
- 脚本对图片、SmartArt、嵌入图表对象中的文字仍不可机读。**若运行环境支持模型视觉，图像相关问题优先做视觉复核；若不支持，必须显式转为人工核对项，不得静默跳过或假设其正确。**

## 产物说明

单份审核保留一个主交付物和一个溯源工件目录：

- 主交付物：`<报告所在目录>\<报告文件名>.review.html`
- 溯源目录：`<报告所在目录>\<报告文件名>.review-artifacts\`

| 产物 | 文件 | 性质 |
|---|---|---|
| HTML 审核报告 | `<报告文件名>.review.html`（报告目录） | **主交付物**：最终交付、表格排版 + 严重度颜色标记 |
| 审核工作稿 | `review-artifacts\<报告文件名>.workpaper.md` | 必留工作底稿：自动检查线索 + 提取全文（含定位索引） |
| 归一化问题清单 | `review-artifacts\<报告文件名>.issues.json` | 必留机器可聚合工件：JSON-first 字段，用于批量汇总与续跑 |
| 证据与媒体 | `review-artifacts\evidence.json`、`media\*` | 文档单元、上下文、预期/实际图项、图片媒体和哈希 |

`.review.md` 仅作为生成 HTML 的中间稿，转换成功后默认删除；`.extracted.md` 在并入 `.workpaper.md` 后默认删除；`pair-checks.md` 仅在 Word/PPT 成对对照时生成。

## 本地部署与更新

技能包更新后，在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy.ps1
```

部署脚本会把当前版本同步到用户级
`.agents\skills\product-dv-report-review\`，并排除 `.git`、缓存和报告输入文件。
部署后先运行 `python scripts\render_review.py --smoke-test` 验证渲染器，再处理实际报告。

## 审核工作流（单份报告）

严格按以下 5 步执行；**保留现有 CHECK-1~CHECK-8 / DC-* / DL-* 命名，不改判定边界**。

1. **提取文本（阶段 1：证据骨架）**
   - 先创建工件目录：`<报告目录>\<报告文件名>.review-artifacts\`。
   - 运行 `python scripts\extract_report.py <报告文件> -o <工件目录>\<报告文件名>.extracted.md`。
   - 运行 `python scripts\evidence_pipeline.py <报告文件> --output-dir <工件目录> --output-json <工件目录>\evidence.json`，提取文档结构、上下文、图像/图表对象、媒体文件、预期图项和确定性匹配。
   - PPT 页数很多（>30 页）时用 `--slides 1-30` 等参数分段提取、逐段审核。
2. **确定性检查（阶段 1：规则线索）**
   - 运行 `python scripts\report_checks.py <上一步的 .extracted.md>`，生成 `<工件目录>\<报告文件名>.workpaper.md`。
   - 若 `references\report-template-profile.md` 已填写必备章节（存在非“（示例）”条目），追加 `--template references\report-template-profile.md` 参数。
3. **语义审核（阶段 2：上下文驱动复核）**
   - 运行 `python scripts\language_review.py <工件目录>\evidence.json -o <工件目录>\language-review-input.json`，把章节、前后段落、关联图表和术语上下文整理为独立语言审核输入。
   - 先读 `references\checklist-auto-hints.md`，并从 `.workpaper.md` 与 `evidence.json` 提取**必备证据包**：封面/样品信息/引用标准/汇总表/结论/全部 ⚠️ 图像标记/正文中声明的关键图表与照片。
   - 对照 `references\checklist-doc-compliance.md` 与 `references\checklist-data-logic.md` 逐条核对；报告较长时可分章节读，但封面、样品信息、汇总表、结论、关键图表声明处必须完整阅读。
   - **独立执行中英技术语言复核**：中文术语、英文缩写、双语标题/图注/结论页分别检查，不把它们混同为纯格式问题。
   - **独立执行图/表清单复核**：先做“应有 vs 实有”图表/照片清单，再核对图文一致性、正文引用关系与完整性。
   - **视觉优先**：若模型可查看渲染页、截图或导出的图像，先做视觉复核再下图像相关结论；若无视觉能力，则把相关问题写入“人工核对项”，并明确注明 `no-vision/manual fallback` 原因。
   - 若没有视觉观察 JSON，运行 `python scripts\figure_checks.py <工件目录>\evidence.json -o <工件目录>\figure-review.json --no-vision`；有视觉能力时先将模型观察写入 JSON，再运行 `--vision-observations <观察 JSON>`。优先使用当前工具链明确支持图像输入的视觉模型；不支持时必须切换模型或输出人工核对项，不得猜测图像内容。
4. **覆盖度核查（阶段 2：标准对照）**
   - 若 `references\standards\` 下存在适用于该报告客户/项目的标准矩阵（非 `_` 开头文件），逐项核对试验项目覆盖情况，标记“大纲有、报告无”的漏项。
   - 若矩阵含“试验条件要点”，按 DL-A06 做条件对照；只呈现差异，不判定方法对错。
   - 没有适用矩阵时跳过本步，并在输出中说明原因。
5. **输出审核报告（阶段 3：JSON-first → HTML）**
   - 先把每条发现规范为临时 `<工件目录>\<报告文件名>.raw-review.json`。语言发现至少包含原文、建议、原因、上下文依据、位置、置信度和技术含义是否变化；图表发现必须包含预期、实际、证据和图项位置。
   - 原始审核 JSON 由模型/人工语义复核产生，至少保留每条发现的类别、严重度、标题、预期、实际、位置、证据摘录、建议和置信度；字段契约以 `references\review-output-template.md` 为准。
   - 运行 `python scripts\normalize_review.py <工件目录>\<报告文件名>.raw-review.json -o <工件目录>\<报告文件名>.issues.json --evidence <工件目录>\evidence.json --figure-review <工件目录>\figure-review.json --language-input <工件目录>\language-review-input.json --workpaper <工件目录>\<报告文件名>.workpaper.md`。
   - 运行 `python scripts\render_review.py <工件目录>\<报告文件名>.issues.json -o <报告目录>\<报告文件名>.review.html` 生成 HTML 主交付物。`make_html_report.py` 保留用于旧版 Markdown 审核稿和兼容场景。
   - 对话内给出总体评价、严重发现摘要、图像审阅模式（vision / no-vision / hybrid）与 HTML 路径。

## 可选输入（用户提供或配置后启用）

- **公司报告模板**：`references\report-template-profile.md` 填写后生效。步骤 2 的脚本用 `--template` 检查必备章节缺失；步骤 3 再对照档案中的“必备要素”与“章节顺序要求”做语义核对，发现归入“一般”级。
- **同项目 Word + PPT 并存**：两份都完成步骤 1-2 后，运行 `python scripts\report_checks.py --pair <A.workpaper.md> <B.workpaper.md>` 生成 `pair-checks.md`，结果用于 DC-P05 核对；剩余的图表来源、截图页、失效描述仍由你语义/视觉比对。
- **同类项目参考报告**：用户另给 1~2 份其它项目的同类报告作参考时，先对其执行步骤 1 提取，抽取其判定准则、试验条件、关键图表组织方式作为对照基线。受审报告与基线的差异列 ⚠️（“一般”级），只呈现差异，不判定对错。
- **测试方法/标准条款**：纳入 `references\standards\` 矩阵的“试验条件要点”列（见 `standards\README.md`），审核时按 DL-A06 对照，不做方法对错判定。

## 批量审核模式

当用户给的是一个文件夹或多份报告（触发词如“批量审核”“审核这个文件夹”“这批报告”）时：

1. 列出范围内全部 `.docx`/`.pptx`，向用户确认清单后再开始。
2. **逐份处理**：一份完整执行单份五步流程并输出后，再处理下一份；每份报告各自使用独立的 `.review-artifacts` 子目录。
3. 每份默认产出报告目录中的 `.review.html`，以及独立工件目录中的 `.workpaper.md` + `.issues.json` + `evidence.json`；对话内只报一行进度（n/N + 总体评价）。
4. **失败隔离**：某份报告提取失败（损坏/加密/旧格式）时，记录文件名与原因，跳过继续下一份，并在批量汇总中列出失败/待人工处理清单；不得因单份失败中断整批。
5. **中断恢复**：用户说“继续”时，先检查报告目录中是否已有 `.review.html`，并检查工件目录中是否同时已有 `.issues.json` 与 `evidence.json`；三者都在才视为完成并跳过，只存在其一时视为未完成，重新补齐。
6. 全部完成后，按 `references\batch-summary-template.md` 写 `batch-review-summary.md`，优先聚合各报告的 `.issues.json`，再运行 `python scripts\make_html_report.py batch-review-summary.md --rm` 生成 `batch-review-summary.html`；对话内展示“各报告结论一览”“失败/跳过清单”“严重问题清单”。

## 审核原则（铁律）

- **每条发现必须附完整证据**：至少包含 `预期`、`实际`、位置索引（如 `[P0012]`、`[S03]`）与原文摘录。没有证据的发现不得写入报告。
- **每条发现必须给置信度**：`高`=文字/表格直接证据；`中`=跨段归纳或部分视觉证据；`人工`=AI 无法确认、需人工核查。不要输出“低置信度定论”。
- **区分事实与推测**：脚本线索和语义存疑处标注“线索/待人工确认”，不写成定论。
- **图像相关结论遵循 vision-first**：无法视觉核对时，写成人工核对项，不得伪装成自动识别结果。
- **不误判优先**：拿不准的项目标 ⚠️ 并说明需要人工核什么，而不是猜一个结论。
- **AI 辅助、人工终判**：输出末尾必须保留模板中的免责声明。

## 严重度分级

- **严重**（HTML 红色标记）：影响报告有效性或结论可信度（结论与数据矛盾、漏项、校准失效、无法追溯、关键图文相互否定等）
- **一般**（橙色）：影响规范性与可信度但不颠覆结论（标准年号旧、签署不全、临界值未说明、中英术语不一致、图表引用断裂等）
- **建议**（蓝色）：格式与可读性问题（单位写法不统一、图题/来源格式不一致等）
- **人工核对项**（紫色）：AI 无法确认、需工程师核查的事项（截图页、图片内文字、证书真伪、无视觉能力下的图像页等）

## references/ 索引

| 文件 | 何时读 |
|---|---|
| `references\checklist-auto-hints.md` | 步骤 3 开头必读（自动化分工 + 证据抓取提醒） |
| `references\checklist-doc-compliance.md` | 步骤 3 必读（含图/表/双语语言检查项） |
| `references\checklist-data-logic.md` | 步骤 3 必读（含图文逻辑一致性） |
| `references\common-defects.md` | 步骤 3 参考，用于比对高频缺陷 |
| `references\review-output-template.md` | 步骤 5 必读（单份 JSON-first / HTML 输出模板） |
| `references\vision-observation-template.json` | 需要视觉模型观察图表时作为字段模板 |
| `references\batch-summary-template.md` | 批量模式收尾时必读 |
| `references\report-template-profile.md` | 模板检查启用时读（步骤 2/3） |
| `references\standards-active.md` | 维护现行标准年号表时编辑（脚本自动读取） |
| `references\standards\README.md` | 需要登记新客户标准时读 |

## 环境说明

- 脚本仅依赖 Python 3 标准库，无需安装任何包。
- 若 `python` 命令不存在，依次尝试 `py`、`python3`；都不可用时报错并请用户安装 Python 3 或手动另存报告为文本。
