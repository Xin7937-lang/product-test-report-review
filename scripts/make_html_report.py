#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_html_report.py — 将审核报告 Markdown 转成带排版的单文件 HTML（仅标准库）。

针对 review-output-template / batch-summary-template 的结构优化：
- 表格排版（边框、斑马纹、表头底色）
- 严重度颜色标记：严重=红、一般=橙、建议=蓝、人工核对=紫
  （按 ### 章节名、表头"严重度"列或单元格文本中的严重度词自动着色）

用法：
  python make_html_report.py <审核报告.md> [-o 输出.html] [--rm]
  默认输出到 md 同目录、同名 .html；--rm 转换成功后删除源 md（默认交付只留 HTML 时使用）
"""
import html
import os
import re
import sys
import datetime

SEV_MAP = [
    ('严重', 'sev-critical'), ('一般', 'sev-major'), ('建议', 'sev-minor'),
    ('人工核对', 'sev-manual'), ('高', 'sev-critical'), ('中', 'sev-major'),
    ('低', 'sev-minor'),
]

CSS = """
:root { --crit:#c62828; --crit-bg:#fdecea; --maj:#e65100; --maj-bg:#fff3e0;
        --min:#1565c0; --min-bg:#e3f2fd; --man:#6a1b9a; --man-bg:#f3e5f5;
        --ink:#1a1a1a; --line:#d0d0d0; --zebra:#f7f7f9; }
* { box-sizing: border-box; }
body { font-family: "Microsoft YaHei","PingFang SC","Segoe UI",sans-serif;
       color: var(--ink); max-width: 1080px; margin: 0 auto; padding: 32px 40px;
       line-height: 1.65; font-size: 14px; }
h1 { font-size: 22px; border-bottom: 3px solid var(--ink); padding-bottom: 8px; }
h2 { font-size: 17px; margin-top: 28px; border-left: 5px solid #888;
     padding-left: 10px; }
h3 { font-size: 15px; margin-top: 20px; padding: 4px 10px; border-radius: 4px;
     display: inline-block; }
h3.sev-critical { background: var(--crit-bg); color: var(--crit); }
h3.sev-major    { background: var(--maj-bg);  color: var(--maj); }
h3.sev-minor    { background: var(--min-bg);  color: var(--min); }
h3.sev-manual   { background: var(--man-bg);  color: var(--man); }
table { border-collapse: collapse; width: 100%; margin: 12px 0 20px;
        font-size: 13px; }
th, td { border: 1px solid var(--line); padding: 6px 10px; text-align: left;
         vertical-align: top; }
th { background: #efefef; font-weight: 600; }
tr:nth-child(even) td { background: var(--zebra); }
tr.sev-critical td { background: var(--crit-bg); }
tr.sev-major td    { background: var(--maj-bg); }
tr.sev-minor td    { background: var(--min-bg); }
tr.sev-manual td   { background: var(--man-bg); }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px;
         font-size: 12px; font-weight: 600; white-space: nowrap; }
.badge.sev-critical { background: var(--crit); color: #fff; }
.badge.sev-major    { background: var(--maj);  color: #fff; }
.badge.sev-minor    { background: var(--min);  color: #fff; }
.badge.sev-manual   { background: var(--man);  color: #fff; }
code { background: #f0f0f0; padding: 1px 5px; border-radius: 3px;
       font-family: Consolas, monospace; font-size: 12.5px; }
blockquote { border-left: 4px solid #bbb; margin: 12px 0; padding: 4px 14px;
             color: #555; background: #fafafa; }
hr { border: none; border-top: 1px solid var(--line); margin: 24px 0; }
.footer { margin-top: 36px; padding-top: 10px; border-top: 1px solid var(--line);
          color: #888; font-size: 12px; }
@media print { body { max-width: none; padding: 10mm; }
               h2 { page-break-after: avoid; } table { page-break-inside: auto; } }
"""


def _sev_class(text):
    for kw, cls in SEV_MAP:
        if kw in text:
            return cls
    return ''


def _inline(text, badges=True):
    out = html.escape(text)
    out = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', out)
    out = re.sub(r'`([^`]+)`', r'<code>\1</code>', out)
    if badges:  # 严重度关键词加徽章（表头不加，避免"建议"列名误标记）
        for kw, cls in SEV_MAP:
            out = re.sub(rf'(?<![\w>]){re.escape(kw)}(?![\w<])',
                         f'<span class="badge {cls}">{kw}</span>', out, count=1)
    return out


def _cells(row_line):
    return [c.strip() for c in row_line.strip().strip('|').split('|')]


def render_markdown(md):
    lines = md.splitlines()
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s == '---':
            out.append('<hr>')
            i += 1
            continue
        m = re.match(r'(#{1,4})\s+(.*)', s)
        if m:
            level, text = len(m.group(1)), m.group(2)
            cls = _sev_class(text) if level == 3 else _sev_class(text) if level <= 3 else ''
            cls_attr = f' class="{cls}"' if cls else ''
            out.append(f'<h{level}{cls_attr}>{_inline(text)}</h{level}>')
            i += 1
            continue
        if s.startswith('|'):
            block = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                block.append(lines[i])
                i += 1
            rows = [_cells(r) for r in block]
            header = rows[0] if rows else []
            data = [r for r in rows[1:]
                    if not all(set(c) <= set('-: ') for c in r)]
            sev_col = next((k for k, c in enumerate(header) if '严重度' in c), None)
            out.append('<table>')
            out.append('<tr>' + ''.join(f'<th>{_inline(c, badges=False)}</th>' for c in header) + '</tr>')
            for r in data:
                cls = ''
                if sev_col is not None and sev_col < len(r):
                    cls = _sev_class(r[sev_col])
                if not cls:  # 汇总表里"严重/一般"作为行标题时
                    cls = _sev_class(r[0]) if r else ''
                tr_cls = f' class="{cls}"' if cls else ''
                out.append(f'<tr{tr_cls}>' + ''.join(f'<td>{_inline(c)}</td>' for c in r) + '</tr>')
            out.append('</table>')
            continue
        if s.startswith('>'):
            out.append(f'<blockquote>{_inline(s.lstrip("> "))}</blockquote>')
            i += 1
            continue
        if re.match(r'[-*]\s+', s):
            items = []
            while i < len(lines) and re.match(r'\s*[-*]\s+', lines[i]):
                items.append(re.sub(r'^\s*[-*]\s+', '', lines[i]))
                i += 1
            out.append('<ul>' + ''.join(f'<li>{_inline(x)}</li>' for x in items) + '</ul>')
            continue
        if re.match(r'\d+[.、]\s*', s):
            items = []
            while i < len(lines) and re.match(r'\s*\d+[.、]\s+', lines[i]):
                items.append(re.sub(r'^\s*\d+[.、]\s+', '', lines[i]))
                i += 1
            out.append('<ol>' + ''.join(f'<li>{_inline(x)}</li>' for x in items) + '</ol>')
            continue
        out.append(f'<p>{_inline(s)}</p>')
        i += 1
    return '\n'.join(out)


def main(argv):
    if len(argv) >= 2 and argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)
    if len(argv) < 2:
        raise SystemExit(__doc__)
    src = argv[1]
    out_path = argv[argv.index('-o') + 1] if '-o' in argv else \
        os.path.splitext(src)[0] + '.html'
    with open(src, encoding='utf-8') as f:
        md = f.read()
    title = os.path.splitext(os.path.basename(src))[0]
    m = re.search(r'^#\s+(.+)$', md, re.M)
    if m:
        title = m.group(1).strip()
    body = render_markdown(md)
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    page = (f'<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{html.escape(title)}</title>\n<style>{CSS}</style>\n</head>\n'
            f'<body>\n{body}\n'
            f'<div class="footer">由 product-dv-report-review 生成 · {stamp} · '
            f'AI 辅助审核，结果需工程师复核确认</div>\n</body>\n</html>\n')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(page)
    if '--rm' in argv:
        os.remove(src)
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    print(f'OK: {out_path}')


if __name__ == '__main__':
    main(sys.argv)
