---
name: product-dv-report-review
description: 审核产品验证（DV，设计验证）测试报告与试验汇报（Word .docx / PPT .pptx），适用于电池包等各类产品。检查文档合规性、数据逻辑一致性、试验项目覆盖度，输出带严重度颜色标记的 HTML 审核报告；支持单份审核与文件夹批量审核。当用户提供DV测试报告、试验报告、型式试验报告、测试总结或汇报PPT，并要求审核、检查、评审、review、把关时使用。
---

# 产品验证（DV）测试报告审核

## 适用范围与限制

- 支持 `.docx` 与 `.pptx`；旧版 `.doc`/`.ppt` 请先让用户另存为新格式。
- 审核深度：**文档合规性 + 数据逻辑一致性**。不做试验方法的技术判定（那是工程师的职责）。
- 图片、SmartArt、嵌入图表对象中的文字无法机读 → 由脚本标记后列入"人工核对项"，不得静默跳过。

## 产物说明

所有产物保存在**被审报告同目录**，默认只留两个文件：

| 产物 | 文件 | 性质 |
|---|---|---|
| HTML 审核报告 | `<报告文件名>.review.html` | **主交付物**：表格排版 + 严重度颜色标记（严重红/一般橙/建议蓝/人工核对紫） |
| 审核工作稿 | `<报告文件名>.workpaper.md` | 中间产物**合并单文件**：自动检查线索 + 提取全文（含定位索引），保留供溯源 |

Markdown 版审核报告（`.review.md`）只是生成 HTML 的中间步骤，**转换成功后默认删除**；仅当用户明确要求保留 Markdown 版时才保留提供（批量汇总的 `batch-review-summary.md` 同理）。

## 审核工作流（单份报告）

严格按以下步骤执行，不要跳步：

1. **提取文本**：运行 `python scripts/extract_report.py <报告文件>`（脚本路径相对于本 SKILL.md 所在目录），生成临时的 `<报告文件名>.extracted.md`。PPT 页数很多（>30 页）时用 `--slides 1-30` 等参数分段提取、逐段审核。
2. **确定性检查**：运行 `python scripts/report_checks.py <上一步的 .extracted.md>`，生成 `<报告文件名>.workpaper.md`（检查线索与提取全文合并为单一工作稿，`.extracted.md` 并入后自动删除）。若 `references/report-template-profile.md` 已填写必备章节（存在非"（示例）"条目），追加 `--template references/report-template-profile.md` 参数。
3. **语义审核**：先读 `references/checklist-auto-hints.md` 明确分工——脚本已覆盖的条目直接甄别线索即可，把精力放在纯语义条目（判定方向、临界值、结论逻辑等）。然后阅读 `.workpaper.md`，对照 `references/checklist-doc-compliance.md` 与 `references/checklist-data-logic.md` 逐条核对。报告较长时可分章节读，但封面、样品信息、汇总表、结论四处必须完整阅读。
4. **覆盖度核查**：若 `references/standards/` 下存在适用于该报告客户/项目的标准矩阵（非 `_` 开头的文件），逐项核对试验项目覆盖情况，标记"大纲有、报告无"的漏项。没有适用矩阵时跳过本步并在输出中说明。
5. **输出审核报告**：
   a. 按 `references/review-output-template.md` 的格式写 `<报告文件名>.review.md`（保存到报告同目录），并将工作稿中的脚本线索甄别后纳入（线索需经你核实，不是直接照抄）；
   b. 运行 `python scripts/make_html_report.py <上一步的 .review.md> --rm` 生成 `<报告文件名>.review.html` 并删除 md 源文件（**用户明确要求保留 Markdown 版时去掉 `--rm`**）；
   c. 对话内给出总体评价与严重发现摘要，并告知 HTML 报告路径。

## 可选输入（用户提供或配置后启用）

- **公司报告模板**：`references/report-template-profile.md` 填写后生效。步骤 2 的脚本用 `--template` 检查必备章节缺失；步骤 3 再对照档案中的"必备要素"与"章节顺序要求"做语义核对，发现归入"一般"级。
- **同项目 Word + PPT 并存**：两份都完成步骤 1-2 后，运行 `python scripts/report_checks.py --pair <A.workpaper.md> <B.workpaper.md>` 生成 `pair-checks.md`（判定不一致、样品编号交集），结果用于 DC-P05 核对；剩余的呈现层一致性（图表来源、失效描述）仍由你语义比对。
- **同类项目参考报告**：用户另给 1~2 份其它项目的同类报告作参考时，先对其执行步骤 1 提取，抽取其判定准则与试验条件作为对照基线。受审报告与基线的差异列 ⚠️（"一般"级），只呈现差异，不判定对错。
- **测试方法/标准条款**：纳入 `references/standards/` 矩阵的"试验条件要点"列（见 `standards/README.md`），审核时按 DL-A06 对照，不做方法对错判定。

## 批量审核模式

当用户给的是一个文件夹或多份报告（触发词如"批量审核""审核这个文件夹""这批报告"）时：

1. 列出范围内全部 `.docx`/`.pptx`，向用户确认清单后再开始。
2. **逐份处理**：一份完整执行单份五步流程并输出后，再处理下一份。不要把多份报告的工作稿同时读入上下文。
3. 每份产出 `.workpaper.md` + `.review.html`（review.md 默认转换后删除），全部存到被审核文件夹内（用户另有指定除外）；对话内只报一行进度（n/N + 总体评价）。
4. **失败隔离**：某份报告提取失败（损坏/加密/旧格式）时，记录文件名与原因，跳过继续下一份，并在批量汇总中列出失败清单；不得因单份失败中断整批。
5. **中断恢复**：批量被中断后，用户说"继续"时先检查各报告是否已有 `.review.html`，已有的视为完成直接跳过，只处理剩余报告。
6. 全部完成后，按 `references/batch-summary-template.md` 写 `batch-review-summary.md`，并运行 `make_html_report.py`（默认加 `--rm`）生成 `batch-review-summary.html`，在对话内展示"各报告结论一览"与"严重问题清单"两节。

## 审核原则（铁律）

- **每条发现必须附证据**：位置索引（如 `[P0012]`、`[S03]`）+ 原文摘录。没有证据的发现不得写入报告。
- **区分事实与推测**：脚本线索和语义存疑处标注"线索/待人工确认"，不写成定论。
- **不误判优先**：拿不准的项目标 ⚠️ 并说明需要人工核什么，而不是猜一个结论。
- **AI 辅助、人工终判**：输出末尾必须保留模板中的免责声明。

## 严重度分级

- **严重**（HTML 红色标记）：影响报告有效性或结论可信度（结论与数据矛盾、漏项、校准失效、无法追溯等）
- **一般**（橙色）：影响规范性与可信度但不颠覆结论（标准年号旧、签署不全、临界值未说明等）
- **建议**（蓝色）：格式与可读性问题（单位写法不统一、图表缺来源等）
- **人工核对项**（紫色）：AI 无法确认、需工程师核查的事项

## references/ 索引

| 文件 | 何时读 |
|---|---|
| `references/checklist-auto-hints.md` | 步骤 3 开头必读（自动化分工） |
| `references/checklist-doc-compliance.md` | 步骤 3 必读 |
| `references/checklist-data-logic.md` | 步骤 3 必读 |
| `references/common-defects.md` | 步骤 3 参考，用于比对高频缺陷 |
| `references/review-output-template.md` | 步骤 5a 必读（单份） |
| `references/batch-summary-template.md` | 批量模式收尾时必读 |
| `references/report-template-profile.md` | 模板检查启用时读（步骤 2/3） |
| `references/standards-active.md` | 维护现行标准年号表时编辑（脚本自动读取） |
| `references/standards/README.md` | 需要登记新客户标准时读 |

## 环境说明

- 脚本仅依赖 Python 3 标准库，无需安装任何包。
- 若 `python` 命令不存在，依次尝试 `py`、`python3`；都不可用时报错并请用户安装 Python 3 或手动另存报告为文本。
