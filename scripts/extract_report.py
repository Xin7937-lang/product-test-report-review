#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""extract_report.py — 从 .docx / .pptx 提取纯文本与表格，生成带定位索引的 Markdown。

仅使用 Python 标准库（zipfile + ElementTree），无需安装第三方包。

定位索引约定：
  [P0001]  Word 段落（含标题）
  [T01]    Word 表格
  [H1]/[F1] Word 页眉/页脚
  [S03]    PPT 第 3 页；[S03-2] 该页第 2 个形状；[S03-T1] 该页第 1 个表格

用法：
  python extract_report.py <报告.docx|报告.pptx> [-o 输出文件] [--stdout]
"""
import os
import posixpath
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
PKG_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'


def _text_of(el, ns_t):
    """拼接元素下所有文本节点。"""
    return ''.join(t.text or '' for t in el.iter(ns_t))


def _clean(text):
    return re.sub(r'\s+', ' ', text).strip()


def _cell(text):
    """单元格文本转义，防止破坏 Markdown 管道表。"""
    return _clean(text).replace('|', '\\|') or ' '


# ---------------------------------------------------------------- docx ----
def _docx_style_levels(zf):
    """从 styles.xml 建立 styleId -> 标题级别 的映射（标题/Heading N 或 outlineLvl）。"""
    levels = {}
    try:
        root = ET.fromstring(zf.read('word/styles.xml'))
    except KeyError:
        return levels
    for st in root.iter(W + 'style'):
        sid = st.get(W + 'styleId')
        name_el = st.find(W + 'name')
        name = (name_el.get(W + 'val') if name_el is not None else '') or ''
        m = re.match(r'^(?:heading|标题)\s*(\d+)$', name.strip(), re.I)
        if m:
            levels[sid] = min(int(m.group(1)), 6)
            continue
        ol = st.find(f'{W}pPr/{W}outlineLvl')
        if ol is not None and ol.get(W + 'val') is not None:
            levels[sid] = min(int(ol.get(W + 'val')) + 1, 6)
    return levels


def _docx_para_level(p, style_levels):
    ppr = p.find(W + 'pPr')
    if ppr is None:
        return 0
    ps = ppr.find(W + 'pStyle')
    if ps is not None and ps.get(W + 'val') in style_levels:
        return style_levels[ps.get(W + 'val')]
    ol = ppr.find(W + 'outlineLvl')
    if ol is not None and ol.get(W + 'val') is not None:
        return min(int(ol.get(W + 'val')) + 1, 6)
    return 0


def _walk_blocks(container):
    """按文档顺序产出 (tag, element)，tag ∈ {'p','tbl'}；未知容器递归进入。"""
    for child in list(container):
        if child.tag == W + 'p':
            yield 'p', child
        elif child.tag == W + 'tbl':
            yield 'tbl', child
        elif child.tag in (W + 'sdt', W + 'sdtContent', W + 'body'):
            yield from _walk_blocks(child)
        else:
            sdt_content = child.find(f'{W}sdtContent')
            if sdt_content is not None:
                yield from _walk_blocks(sdt_content)


def extract_docx(path):
    out, n_p, n_t = [], 0, 0
    with zipfile.ZipFile(path) as zf:
        style_levels = _docx_style_levels(zf)
        try:
            root = ET.fromstring(zf.read('word/document.xml'))
        except KeyError:
            raise SystemExit('错误：文件不含 word/document.xml，不是有效的 .docx'
                             '（若为旧版 .doc 请先用 Word 另存为 .docx）')
        body = root.find(W + 'body')
        for tag, el in _walk_blocks(body):
            if tag == 'p':
                text = _clean(_text_of(el, W + 't'))
                if not text:
                    continue
                n_p += 1
                level = _docx_para_level(el, style_levels)
                if level:
                    out.append(f"\n{'#' * min(level + 1, 6)} {text}  [P{n_p:04d}]\n")
                else:
                    out.append(f"[P{n_p:04d}] {text}")
            else:
                n_t += 1
                rows = []
                for tr in el.iter(W + 'tr'):
                    cells = [_cell(_text_of(tc, W + 't')) for tc in tr.findall(W + 'tc')]
                    if cells:
                        rows.append('| ' + ' | '.join(cells) + ' |')
                if rows:
                    ncol = rows[0].count('|') - 1
                    out.append(f"\n[T{n_t:02d}] 表格：")
                    out.append(rows[0])
                    out.append('|' + ' --- |' * ncol)
                    out.extend(rows[1:])
                    out.append('')
        # 页眉/页脚（报告编号、页码常在此处）
        for kind, pat, tag in (('页眉', r'word/header\d*\.xml$', 'H'),
                               ('页脚', r'word/footer\d*\.xml$', 'F')):
            n_hf = 0
            for name in sorted(zf.namelist()):
                if re.match(pat, name):
                    text = _clean(_text_of(ET.fromstring(zf.read(name)), W + 't'))
                    if text:
                        n_hf += 1
                        out.append(f"[{tag}{n_hf}] （{kind}）{text}")
    return out, {'paragraphs': n_p, 'tables': n_t, 'slides': 0, 'flags': 0}


# ---------------------------------------------------------------- pptx ----
def _slide_sort_key(name):
    m = re.search(r'slide(\d+)\.xml$', name)
    return int(m.group(1)) if m else 0


def _pptx_notes(zf, slide_name):
    """经 slide 的 rels 找到备注页并提取文本。"""
    rels_name = f"ppt/slides/_rels/{posixpath.basename(slide_name)}.rels"
    try:
        rels = ET.fromstring(zf.read(rels_name))
    except KeyError:
        return ''
    for rel in rels.iter(PKG_REL + 'Relationship'):
        if rel.get('Type', '').endswith('/notesSlide'):
            target = posixpath.normpath(posixpath.join('ppt/slides', rel.get('Target', '')))
            try:
                return _clean(_text_of(ET.fromstring(zf.read(target)), A + 't'))
            except KeyError:
                return ''
    return ''


def _pptx_shape_lines(el, sid, counters, flags):
    """递归处理单个形状/图片/图形框/组合，输出带索引的行。"""
    lines = []
    tag = el.tag
    if tag == P + 'sp' or tag == P + 'cxnSp':
        paras = [''.join(t.text or '' for t in p.iter(A + 't'))
                 for p in el.iter(A + 'p')]
        text = _clean('；'.join(x for x in paras if x.strip()))
        if text:
            counters['shape'] += 1
            lines.append(f"[S{sid:02d}-{counters['shape']}] {text}")
    elif tag == P + 'pic':
        counters['shape'] += 1
        flags.append(sid)
        lines.append(f"[S{sid:02d}-{counters['shape']}] ⚠️ [图片] 内容不可机读，需人工核对")
    elif tag == P + 'graphicFrame':
        gd = el.find(f'.//{A}graphicData')
        uri = gd.get('uri', '') if gd is not None else ''
        if 'table' in uri:
            counters['table'] += 1
            rows = []
            for tr in el.iter(A + 'tr'):
                cells = [_cell(_text_of(tc, A + 't')) for tc in tr.findall(A + 'tc')]
                if cells:
                    rows.append('| ' + ' | '.join(cells) + ' |')
            if rows:
                ncol = rows[0].count('|') - 1
                lines.append(f"[S{sid:02d}-T{counters['table']}] 表格：")
                lines.append(rows[0])
                lines.append('|' + ' --- |' * ncol)
                lines.extend(rows[1:])
        else:
            counters['shape'] += 1
            flags.append(sid)
            kind = ('图表对象' if 'chart' in uri else
                    'SmartArt/示意图' if 'diagram' in uri else f'未识别图形对象({uri})')
            lines.append(f"[S{sid:02d}-{counters['shape']}] ⚠️ [{kind}] 内容不可机读，需人工核对")
    elif tag == P + 'grpSp':
        for sub in list(el):
            lines.extend(_pptx_shape_lines(sub, sid, counters, flags))
    return lines


def extract_pptx(path):
    out, flags, n_tables = [], [], 0
    with zipfile.ZipFile(path) as zf:
        slides = sorted((n for n in zf.namelist()
                         if re.match(r'ppt/slides/slide\d+\.xml$', n)),
                        key=_slide_sort_key)
        if not slides:
            raise SystemExit('错误：文件不含 ppt/slides/，不是有效的 .pptx'
                             '（若为旧版 .ppt 请先用 PowerPoint 另存为 .pptx）')
        for slide_name in slides:
            sid = _slide_sort_key(slide_name)
            root = ET.fromstring(zf.read(slide_name))
            out.append(f"\n## 第{sid}页 [S{sid:02d}]")
            counters = {'shape': 0, 'table': 0}
            sp_tree = root.find(f'.//{P}spTree')
            if sp_tree is not None:
                for el in list(sp_tree):
                    out.extend(_pptx_shape_lines(el, sid, counters, flags))
            n_tables += counters['table']
            notes = _pptx_notes(zf, slide_name)
            if notes:
                out.append(f"[S{sid:02d}-N] （备注）{notes}")
    return out, {'paragraphs': 0, 'tables': n_tables, 'slides': len(slides),
                 'flags': len(set(flags))}


# ---------------------------------------------------------------- main ----
def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    src = argv[1]
    out_path, to_stdout = None, False
    args = argv[2:]
    if '-o' in args:
        out_path = args[args.index('-o') + 1]
    if '--stdout' in args:
        to_stdout = True

    ext = os.path.splitext(src)[1].lower()
    if ext == '.docx':
        lines, stats = extract_docx(src)
    elif ext == '.pptx':
        lines, stats = extract_pptx(src)
    else:
        raise SystemExit(f'错误：不支持的格式 {ext}（仅支持 .docx / .pptx；'
                         '旧版 .doc/.ppt 请先另存为新格式）')

    header = [
        f"# {os.path.basename(src)} — 提取文本",
        '',
        '> 由 extract_report.py 生成。索引：[Pxxxx]=段落 [Txx]=表格 [H/F]=页眉页脚 '
        '[Sxx]=PPT页。⚠️ 标记处（图片/SmartArt/图表对象）文字不可机读，须列入人工核对。',
        '',
    ]
    content = '\n'.join(header + lines) + '\n'

    if out_path is None:
        out_path = src + '.extracted.md'
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(content)
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    print(f"OK: {out_path}  ({stats})")
    if to_stdout:
        print(content)


if __name__ == '__main__':
    main(sys.argv)
