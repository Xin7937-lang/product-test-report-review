---
name: product-dv-report-review
description: 审核产品验证（DV，设计验证）测试报告与试验汇报（Word .docx / PPT .pptx），适用于电池包等各类产品。检查文档合规性、数据逻辑一致性、试验项目覆盖度，输出带证据引用的分级审核发现；支持单份审核与文件夹批量审核。当用户提供DV测试报告、试验报告、型式试验报告、测试总结或汇报PPT，并要求审核、检查、评审、review、把关时使用。
---

# 产品验证（DV）测试报告审核

## 适用范围与限制

- 支持 `.docx` 与 `.pptx`；旧版 `.doc`/`.ppt` 请先让用户另存为新格式。
- 审核深度：**文档合规性 + 数据逻辑一致性**。不做试验方法的技术判定（那是工程师的职责）。
- 图片、SmartArt、嵌入图表对象中的文字无法机读 → 由脚本标记后列入"人工核对项"，不得静默跳过。

## 审核工作流（单份报告）

严格按以下步骤执行，不要跳步：

1. **提取文本**：运行 `python scripts/extract_report.py <报告文件>`（脚本路径相对于本 SKILL.md 所在目录）。生成 `<报告文件名>.extracted.md`（保留原扩展名，如 `xx.docx.extracted.md`），内含带定位索引的纯文本与表格（`[P0001]` 段落、`[T01]` 表格、`[S03]` 幻灯片页）。
2. **确定性检查**：运行 `python scripts/report_checks.py <上一步生成的 .extracted.md>`，生成 `.checks.md` 线索清单。若 `references/report-template-profile.md` 已填写必备章节（存在非"（示例）"条目），追加 `--template references/report-template-profile.md` 参数。
3. **语义审核**：阅读 `.extracted.md`，对照 `references/checklist-doc-compliance.md` 与 `references/checklist-data-logic.md` 逐条核对。报告较长时可分章节读，但封面、样品信息、汇总表、结论四处必须完整阅读。
4. **覆盖度核查**：若 `references/standards/` 下存在适用于该报告客户/项目的标准矩阵（非 `_` 开头的文件），逐项核对试验项目覆盖情况，标记"大纲有、报告无"的漏项。没有适用矩阵时跳过本步并在输出中说明。
5. **输出审核报告**：按 `references/review-output-template.md` 的格式输出，并将 `.checks.md` 的线索甄别后纳入（脚本线索需经你核实，不是直接照抄）。

## 可选输入（用户提供或配置后启用）

- **公司报告模板**：`references/report-template-profile.md` 填写后生效。步骤 2 的脚本用 `--template` 检查必备章节缺失；步骤 3 再对照档案中的"必备要素"与"章节顺序要求"做语义核对，发现归入"一般"级。
- **同类项目参考报告**：用户另给 1~2 份其它项目的同类报告作参考时，先对其执行步骤 1 提取，抽取其判定准则与试验条件作为对照基线。受审报告与基线的差异列 ⚠️（"一般"级），只呈现差异，不判定对错。
- **测试方法/标准条款**：纳入 `references/standards/` 矩阵的"试验条件要点"列（见 `standards/README.md`），审核时按 DL-A06 对照，不做方法对错判定。

## 批量审核模式

当用户给的是一个文件夹或多份报告（触发词如"批量审核""审核这个文件夹""这批报告"）时：

1. 列出范围内全部 `.docx`/`.pptx`，向用户确认清单后再开始。
2. **逐份处理**：一份完整执行单份五步流程并输出后，再处理下一份。不要把多份报告的提取文本同时读入上下文。
3. 每份审核报告**写入文件**：`<报告文件名>.review.md`，存到被审核文件夹内（用户另有指定除外）；对话内只报该份的总体评价与严重发现摘要。
4. 全部完成后，按 `references/batch-summary-template.md` 生成 `batch-review-summary.md` 到同一文件夹，并在对话内展示其"各报告结论一览"与"严重问题清单"两节。
5. 中间产物（`.extracted.md` / `.checks.md`）保留在原处，供人工复核溯源。

## 审核原则（铁律）

- **每条发现必须附证据**：位置索引（如 `[P0012]`、`[S03]`）+ 原文摘录。没有证据的发现不得写入报告。
- **区分事实与推测**：脚本线索和语义存疑处标注"线索/待人工确认"，不写成定论。
- **不误判优先**：拿不准的项目标 ⚠️ 并说明需要人工核什么，而不是猜一个结论。
- **AI 辅助、人工终判**：输出末尾必须保留模板中的免责声明。

## 严重度分级

- **严重**：影响报告有效性或结论可信度（结论与数据矛盾、漏项、校准失效、无法追溯等）
- **一般**：影响规范性与可信度但不颠覆结论（标准年号旧、签署不全、临界值未说明等）
- **建议**：格式与可读性问题（单位写法不统一、图表缺来源等）

## references/ 索引

| 文件 | 何时读 |
|---|---|
| `references/checklist-doc-compliance.md` | 步骤 3 必读 |
| `references/checklist-data-logic.md` | 步骤 3 必读 |
| `references/common-defects.md` | 步骤 3 参考，用于比对高频缺陷 |
| `references/review-output-template.md` | 步骤 5 必读（单份） |
| `references/batch-summary-template.md` | 批量模式收尾时必读 |
| `references/report-template-profile.md` | 模板检查启用时读（步骤 2/3） |
| `references/standards/README.md` | 需要登记新客户标准时读 |

## 环境说明

- 脚本仅依赖 Python 3 标准库，无需安装任何包。
- 若 `python` 命令不存在，依次尝试 `py`、`python3`；都不可用时报错并请用户安装 Python 3 或手动另存报告为文本。
