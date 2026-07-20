#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""report_checks.py — 对 extract_report.py 的提取产物做确定性规则检查（仅标准库）。

输入 .extracted.md（或直接给 .docx/.pptx，会先自动调用 extract_report.py）。
输出 <报告名>.workpaper.md：合并单文件中间产物 = 自动检查线索 + 提取全文。
合并后原 .extracted.md 自动删除（--keep-extracted 可保留）。

用法：
  python report_checks.py <报告.extracted.md | 报告.docx | 报告.pptx> [--template 模板档案路径] [--keep-extracted]
  python report_checks.py --pair <A.workpaper.md> <B.workpaper.md>   # 同项目两份报告关键数据对照（DC-P05）
"""
import datetime
import os
import re
import subprocess
import sys

ANCHOR_RE = re.compile(r'\[(P\d{4}|T\d{2}|S\d{2}(?:-[A-Za-z0-9]+)?|[HF]\d+)\]')

DATE_RE = re.compile(r'(20\d{2})\s*[年\-/\.]\s*(\d{1,2})\s*[月\-/\.]\s*(\d{1,2})\s*日?')

# 标准年号核查：现行有效表与提示表外置在 references/standards-active.md
# （团队按公司标准受控清单维护，改完重新部署即生效，无需改源码）。
# 下列为文件缺失时的内置兜底值。
_DEFAULT_KNOWN_CURRENT = {'GB 38031': '2020'}
_DEFAULT_SOFT_WARN = {
    'GB/T 31485': '其安全要求内容已并入 GB 38031-2020，引用前请确认客户/DV大纲要求',
    'GB/T 31486': '其电性能要求内容已并入 GB 38031-2020，引用前请确认客户/DV大纲要求',
}


def _load_standards_ref():
    """读取 references/standards-active.md；文件缺失或无可解析条目时用内置兜底。"""
    known, soft = dict(_DEFAULT_KNOWN_CURRENT), dict(_DEFAULT_SOFT_WARN)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'references', 'standards-active.md')
    try:
        with open(path, encoding='utf-8') as f:
            tlines = f.read().splitlines()
    except OSError:
        return known, soft
    section = ''
    for l in tlines:
        s = l.strip()
        if s.startswith('## '):
            section = s[3:].strip()
            continue
        m = re.match(r'-\s+(.+?)\s*[:：]\s*(.+?)\s*$', s)
        if m and '（示例）' not in s:
            if section == 'KNOWN_CURRENT':
                known[m.group(1)] = m.group(2)
            elif section == 'SOFT_WARN':
                soft[m.group(1)] = m.group(2)
    return known, soft


# 运行时在 main() 中由 _load_standards_ref() 填充
STD_REFS = {'known': dict(_DEFAULT_KNOWN_CURRENT), 'soft': dict(_DEFAULT_SOFT_WARN)}

STD_RE = re.compile(r'\b(GB/T|GB|QC/T|ISO|IEC|UL|EN|SAE)\s*(\d+(?:\.\d+)*)\s*[-—–]\s*(\d{4})\b')

# 编号类停止词（标准号、体系代号不算样品/报告编号）
ID_STOPLIST = {'GB', 'GBT', 'QC', 'QCT', 'ISO', 'IEC', 'UN', 'UL', 'EN',
               'DIN', 'SAE', 'VW', 'LV', 'TL', 'ECE', 'IEEE', 'IEC'}


def find_anchor(line, lines, idx):
    """取本行索引；没有则向上找最近的索引。"""
    m = ANCHOR_RE.search(line)
    if m:
        return f'[{m.group(1)}]'
    for j in range(idx, -1, -1):
        m = ANCHOR_RE.search(lines[j])
        if m:
            return f'[{m.group(1)}]'
    return '[?]'


def excerpt(line, n=80):
    s = line.strip()
    return (s[:n] + '…') if len(s) > n else s


# ------------------------------------------------------------- CHECK-1 ----
def check_placeholders(lines):
    pats = [(r'×{2,}', '占位符"××"'), (r'[XxＸｘ]{3,}', '占位符"XXX"'),
            (r'待补充|待完善|待填写', '待办占位文字'), (r'\bTBD\b|\bTODO\b', 'TBD/TODO'),
            (r'_{4,}', '下划线留白'), (r'【\s*】', '空方括号【】'), (r'（\s*）', '空括号（）')]
    out = []
    for i, line in enumerate(lines):
        if line.startswith('>') or line.startswith('# '):
            continue
        for pat, name in pats:
            if re.search(pat, line):
                out.append(('中', find_anchor(line, lines, i), f'{name}：{excerpt(line)}'))
                break
    return out


# ------------------------------------------------------------- CHECK-2 ----
def _table_header_map(lines):
    """行号 -> 所在管道表块的表头行文本（用于表格内日期的语义标注）。"""
    m, i = {}, 0
    while i < len(lines):
        if lines[i].lstrip().startswith('|'):
            header = lines[i]
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                m[i] = header
                i += 1
        else:
            i += 1
    return m


def check_dates(lines, today):
    LABELS = [('委托', '委托'), ('送样', '委托'), ('到样', '委托'), ('收样', '委托'),
              ('试验开始', '试验开始'), ('开始日期', '试验开始'),
              ('试验结束', '试验结束'), ('完成日期', '试验结束'), ('结束日期', '试验结束'),
              ('报告日期', '报告日期'), ('签发', '签发'), ('批准', '批准'), ('编制', '编制'),
              ('有效期', '校准有效期'), ('检定', '校准'), ('校准', '校准')]
    found = {}  # label -> [(date, loc)]
    all_dates = []
    hdr_map = _table_header_map(lines)
    for i, line in enumerate(lines):
        for m in DATE_RE.finditer(line):
            d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            loc = find_anchor(line, lines, i)
            all_dates.append((d, loc, line))
            ctx = line[max(0, m.start() - 12):m.start()]
            lab = None
            for kw, l in LABELS:
                if kw in ctx:
                    lab = l
                    break
            if lab is None and line.lstrip().startswith('|'):
                if re.search(r'有效期|校准|检定', hdr_map.get(i, '')):
                    lab = '校准有效期'
            if lab:
                found.setdefault(lab, []).append((d, loc))
        # “试验时间：D1 至 D2”区间的处理
        rm = re.search(r'试验(?:时间|日期|周期)[^0-9]{0,6}(' + DATE_RE.pattern + r')\s*[~～至\-—]{1,2}\s*('
                       + DATE_RE.pattern + r')', line)
        if rm:
            d1 = datetime.date(int(rm.group(2)), int(rm.group(3)), int(rm.group(4)))
            d2 = datetime.date(int(rm.group(6)), int(rm.group(7)), int(rm.group(8)))
            loc = find_anchor(line, lines, i)
            found.setdefault('试验开始', []).append((d1, loc))
            found.setdefault('试验结束', []).append((d2, loc))
    out = []

    def first(lab):
        return min(found[lab]) if lab in found else None

    pairs = [('委托', '试验开始', '试验开始早于委托/送样日期，日期倒挂'),
             ('试验开始', '试验结束', '试验结束早于试验开始，日期倒挂'),
             ('试验结束', '签发', '签发日期早于试验结束日期'),
             ('试验结束', '批准', '批准日期早于试验结束日期'),
             ('试验结束', '报告日期', '报告日期早于试验结束日期')]
    for a, b, msg in pairs:
        va, vb = first(a), first(b)
        if va and vb and va[0] > vb[0]:
            out.append(('高', f'{va[1]}/{vb[1]}',
                        f'{msg}：{a}={va[0]}，{b}={vb[0]}'))
    if '校准有效期' in found and '试验结束' in found:
        exp = min(found['校准有效期'])
        end = max(found['试验结束'])
        if exp[0] < end[0]:
            out.append(('高', f'{exp[1]}',
                        f'校准有效期({exp[0]})早于试验结束日期({end[0]})，设备校准可能不覆盖试验期（CD-S03），请核对证书'))
    for d, loc, line in all_dates:
        if d > today + datetime.timedelta(days=1):
            out.append(('中', loc, f'出现未来日期 {d}：{excerpt(line)}'))
            break
    return out


# ------------------------------------------------------------- CHECK-3 ----
def check_ids(lines):
    text = '\n'.join(lines)
    ids = {}  # raw -> [loc]

    def add(raw, loc):
        ids.setdefault(raw, [])
        if loc not in ids[raw]:
            ids[raw].append(loc)

    for i, line in enumerate(lines):
        loc = find_anchor(line, lines, i)
        for m in re.finditer(r'(?:样品编号|报告编号|样品号)[:：\s]*([A-Za-z0-9\-_/]{3,})', line):
            add(m.group(1), loc)
        for m in re.finditer(r'\b([A-Za-z]{2,6}[-_]\d[A-Za-z0-9\-_]{1,})\b', line):
            prefix = re.match(r'[A-Za-z]+', m.group(1)).group(0).upper()
            if prefix not in ID_STOPLIST:
                add(m.group(1), loc)

    out = []
    rep_ids = re.findall(r'报告编号[:：\s]*([A-Za-z0-9\-_/]{4,})', text)
    if len(set(rep_ids)) > 1:
        out.append(('高', '[?]', f'报告编号不一致，出现 {len(set(rep_ids))} 种写法：'
                                 f'{"、".join(sorted(set(rep_ids))[:5])}（CD-G10）'))
    # 写法不一致（分隔符/大小写不同）
    groups = {}
    for raw in ids:
        norm = raw.upper().replace('-', '').replace('_', '')
        groups.setdefault(norm, set()).add(raw)
    for norm, raws in groups.items():
        if len(raws) > 1:
            locs = sorted({l for r in raws for l in ids[r]})
            out.append(('中', '/'.join(locs[:3]),
                        f'编号写法不一致：{"、".join(sorted(raws))}（疑为同一编号，请统一）'))
    # 疑似笔误：不同归一化分组之间，长度相同且仅差1字符
    canon = {norm: sorted(raws, key=lambda r: -len(ids[r]))[0]
             for norm, raws in groups.items()}
    norms = sorted(canon)
    for x in range(len(norms)):
        for y in range(x + 1, len(norms)):
            n1, n2 = norms[x], norms[y]
            if len(n1) == len(n2) and sum(a != b for a, b in zip(n1, n2)) == 1:
                r1, r2 = canon[n1], canon[n2]
                out.append(('高', f'{ids[r1][0]}/{ids[r2][0]}',
                            f'疑似笔误："{r1}" 与 "{r2}" 仅差1字符，疑为同一编号（CD-S04）'))
    return out


# ------------------------------------------------------------- CHECK-4 ----
def check_units(lines):
    text = '\n'.join(lines)
    out = []
    kwh = {}
    for i, line in enumerate(lines):
        for m in re.finditer(r'(?<![A-Za-z])[kK][wW][hH](?![A-Za-z])', line):
            kwh.setdefault(m.group(0), find_anchor(line, lines, i))
    bad = {k: v for k, v in kwh.items() if k != 'kWh'}
    if bad:
        out.append(('低', '/'.join(sorted(set(bad.values()))),
                    f'单位写法不规范：{"、".join(sorted(bad))}（应为 kWh）（CD-J01）'))
    for a, b, name in (('℃', '°C', '温度单位'), ('％', '%', '百分号'),
                       ('毫欧', 'mΩ', '毫欧单位')):
        if a in text and b in text:
            out.append(('低', '[?]', f'{name}写法混用："{a}" 与 "{b}" 同时出现（CD-J01）'))
    return out


# ------------------------------------------------------------- CHECK-5 ----
def _parse_tables(lines):
    """产出 [(loc, header_cells, data_rows)]，data_rows 为 cell 列表。"""
    tables, i = [], 0
    while i < len(lines):
        if lines[i].lstrip().startswith('|'):
            loc = '[?]'
            for j in range(i - 1, max(-1, i - 4), -1):
                if j >= 0 and ANCHOR_RE.search(lines[j]):
                    loc = f'[{ANCHOR_RE.search(lines[j]).group(1)}]'
                    break
            block = []
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                block.append(lines[i])
                i += 1
            rows = [[c.strip() for c in r.strip().strip('|').split('|')] for r in block]
            if rows:
                tables.append((loc, rows[0], [r for r in rows[1:]
                                              if not all(set(c) <= set('-: ') for c in r)]))
        else:
            i += 1
    return tables


def check_judgement(lines):
    text = '\n'.join(lines)
    out = []
    total_pass = total_fail = 0
    judged_tables = 0
    for loc, header, rows in _parse_tables(lines):
        col = next((k for k, c in enumerate(header)
                    if re.search(r'判定|结论', c)), None)
        if col is None or not re.search(r'项目|试验|序号|名称', ''.join(header)):
            continue
        judged_tables += 1
        for r in rows:
            if col >= len(r):
                continue
            cell = r[col]
            if re.search(r'不合格|不通过|不符合|Fail|FAIL|NG', cell):
                total_fail += 1
                out.append(('中', loc, f'汇总表存在不合格项：{excerpt(" | ".join(r))}'))
            elif re.search(r'合格|通过|符合|PASS|Pass|OK', cell):
                total_pass += 1
    claim_all = re.search(r'(\d+)\s*项[^。\n]{0,12}(?:全部|均)(?:合格|通过)', text) or \
        re.search(r'(?:全部|均)(?:合格|通过)[^。\n]{0,12}(\d+)\s*项', text)
    if judged_tables and total_fail > 0 and claim_all:
        out.append(('高', '[?]',
                    f'文字称"{claim_all.group(0)}"，但汇总表中有 {total_fail} 项不合格（CD-S01）'))
    m = re.search(r'共\s*(\d+)\s*项[^。\n]{0,15}?(\d+)\s*项\s*合格', text)
    if judged_tables and m and total_pass != int(m.group(2)):
        out.append(('中', '[?]',
                    f'文字称{m.group(1)}项中{m.group(2)}项合格，汇总表统计合格 {total_pass} 项，计数不一致'))
    if judged_tables and total_fail > 0 and \
            re.search(r'结论[:：]?\s*(合格|通过|满足要求)', text):
        out.append(('高', '[?]',
                    f'存在 {total_fail} 个不合格项，但报告结论为合格/通过（CD-S01）'))
    if judged_tables:
        out.append(('统计', '[?]',
                    f'判定汇总表 {judged_tables} 个，合格 {total_pass} 项、不合格 {total_fail} 项（统计事实，非严重度线索，仅供核对）'))
    return out


# ------------------------------------------------------------- CHECK-6 ----
def check_standards(lines):
    refs = {}
    for i, line in enumerate(lines):
        for m in STD_RE.finditer(line):
            key = f'{m.group(1)} {m.group(2)}'
            refs.setdefault(key, {})
            refs[key].setdefault(m.group(3), find_anchor(line, lines, i))
    out = []
    for key, years in refs.items():
        if len(years) > 1:
            detail = '、'.join(f'{y}({loc})' for y, loc in sorted(years.items()))
            out.append(('中', '/'.join(years.values()),
                        f'同一标准 {key} 年号不一致：{detail}（CD-G02）'))
        cur = STD_REFS['known'].get(key)
        for y, loc in years.items():
            if cur and y != cur:
                out.append(('中', loc,
                            f'{key}-{y} 可能为作废版本（现行 {cur}，请按公司标准清单核实）（CD-G01）'))
        if key in STD_REFS['soft']:
            out.append(('低', next(iter(years.values())), f'{key}：{STD_REFS["soft"][key]}'))
    return out


# ------------------------------------------------------------- CHECK-7 ----
def check_empty_cells(lines):
    out = []
    for loc, header, rows in _parse_tables(lines):
        for r in rows:
            if any(c == '' for c in r):
                out.append(('低', loc, f'表格存在空白单元格：{excerpt(" | ".join(r))}（CD-G07）'))
    return out


# ------------------------------------------------------------- CHECK-8 ----
def check_template(lines, template_path):
    """模板档案"必备章节"关键词是否出现在报告中（结构完整性线索）。"""
    try:
        with open(template_path, encoding='utf-8') as f:
            tlines = f.read().splitlines()
    except OSError:
        return [('低', '[?]', f'模板档案不可读：{template_path}')]
    required, in_sec = [], False
    for l in tlines:
        if l.strip().startswith('## '):
            in_sec = l.strip().startswith('## 必备章节')
            continue
        m = re.match(r'\s*-\s+(.+?)\s*$', l)
        if in_sec and m and '（示例）' not in m.group(1):
            required.append(m.group(1))
    if not required:
        return []
    text = '\n'.join(lines).lower()
    return [('中', '[?]', f'模板必备章节缺失："{kw}"（依据 {os.path.basename(template_path)}）')
            for kw in required if kw.lower() not in text]


# ------------------------------------------------------------- --pair ----
def _judgement_map(lines):
    """从判定汇总表提取 {试验项目: (判定, 位置)}。"""
    result = {}
    for loc, header, rows in _parse_tables(lines):
        col_j = next((k for k, c in enumerate(header) if re.search(r'判定|结论', c)), None)
        col_p = next((k for k, c in enumerate(header) if re.search(r'项目|名称', c)), None)
        if col_j is None or col_p is None:
            continue
        for r in rows:
            if col_p >= len(r) or col_j >= len(r):
                continue
            name, cell = r[col_p].strip(), r[col_j].strip()
            if not name:
                continue
            if re.search(r'不合格|不通过|不符合|Fail|FAIL|NG', cell):
                v = '不合格'
            elif re.search(r'合格|通过|符合|PASS|Pass|OK', cell):
                v = '合格'
            else:
                v = cell or '（空）'
            result[name] = (v, loc)
    return result


def check_pair(path_a, path_b):
    """同项目两份报告（如 Word 报告 + PPT 汇报）关键数据对照（DC-P05）。"""
    def rd(p):
        with open(p, encoding='utf-8') as f:
            return f.read().splitlines()
    la, lb = rd(path_a), rd(path_b)
    na, nb = os.path.basename(path_a), os.path.basename(path_b)
    out = []
    ma, mb = _judgement_map(la), _judgement_map(lb)
    for name in sorted(set(ma) & set(mb)):
        va, vb = ma[name][0], mb[name][0]
        if va != vb:
            out.append(('高', f'{ma[name][1]}/{mb[name][1]}',
                        f'判定不一致："{name}" 在 {na} 为"{va}"、{nb} 为"{vb}"（DC-P05/CD-S01）'))

    def sample_ids(lines):
        ids = set()
        for line in lines:
            for m in re.finditer(r'样品编号[:：\s]*([A-Za-z0-9\-_/]{3,})', line):
                ids.add(m.group(1).upper().replace('-', '').replace('_', ''))
        return ids
    sa, sb = sample_ids(la), sample_ids(lb)
    if sa and sb and not (sa & sb):
        out.append(('中', '[?]',
                    f'样品编号无交集：{na} 与 {nb} 疑非同一批样品，请人工确认（DC-P05）'))
    return out


# ---------------------------------------------------------------- main ----
CHECKS = [('CHECK-1 占位符/待办残留', check_placeholders),
          ('CHECK-2 日期逻辑', None),  # 特殊处理 today
          ('CHECK-3 编号一致性', check_ids),
          ('CHECK-4 单位规范', check_units),
          ('CHECK-5 判定一致性', check_judgement),
          ('CHECK-6 标准引用年号', check_standards),
          ('CHECK-7 表格空白单元格', check_empty_cells)]


def main(argv):
    if len(argv) >= 2 and argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)
    if len(argv) < 2:
        raise SystemExit(__doc__)
    if '--pair' in argv:
        k = argv.index('--pair')
        if len(argv) < k + 3:
            raise SystemExit('错误：--pair 需要两个文件参数（.workpaper.md 或 .extracted.md）')
        pa, pb = argv[k + 1], argv[k + 2]
        findings = check_pair(pa, pb)
        out_path = os.path.join(os.path.dirname(os.path.abspath(pa)), 'pair-checks.md')
        body = '\n'.join(f'- 【{sev}】{loc} {msg}' for sev, loc, msg in findings) or '- 无'
        content = (f'# 跨报告对照线索（DC-P05）\n\n'
                   f'> {os.path.basename(pa)}  vs  {os.path.basename(pb)}\n'
                   f'> 由 report_checks.py --pair 生成，须人工/AI 甄别后纳入审核报告。\n\n{body}\n')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'OK: {out_path}  (findings={len(findings)})')
        return
    src = argv[1]
    ext = os.path.splitext(src)[1].lower()
    if ext in ('.docx', '.pptx'):
        helper = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'extract_report.py')
        subprocess.run([sys.executable, helper, src], check=True)
        src = src + '.extracted.md'
    with open(src, encoding='utf-8') as f:
        lines = f.read().splitlines()

    today = datetime.date.today()
    STD_REFS['known'], STD_REFS['soft'] = _load_standards_ref()
    sections = []
    total = 0
    for name, fn in CHECKS:
        findings = check_dates(lines, today) if fn is None else fn(lines)
        total += len(findings)
        body = '\n'.join(f'- 【{sev}】{loc} {msg}' for sev, loc, msg in findings) or '- 无'
        sections.append(f'## {name}（{len(findings)} 条）\n\n{body}')

    if '--template' in argv:
        tpl = argv[argv.index('--template') + 1]
        findings = check_template(lines, tpl)
        total += len(findings)
        body = '\n'.join(f'- 【{sev}】{loc} {msg}' for sev, loc, msg in findings) or '- 无'
        sections.append(f'## CHECK-8 模板必备章节（{len(findings)} 条）\n\n{body}')

    base = src.replace('.extracted.md', '')
    out_path = base + '.workpaper.md'
    content = (f'# 审核工作稿 — {os.path.basename(base)}\n\n'
               f'> 中间产物（合并单文件）。第一部分为自动检查线索（report_checks.py 生成，\n'
               f'> 确定性规则含启发式判断，可能有误报；【高/中/低】为规则建议严重度，【统计】为统计事实不作分级，均须甄别）；\n'
               f'> 第二部分为报告提取全文（extract_report.py 生成，[Pxxxx]/[Txx]/[Sxx] 为证据定位索引）。\n\n'
               f'# 第一部分：自动检查线索\n\n'
               + '\n\n'.join(sections) + f'\n\n---\n共 {total} 条线索。\n\n'
               f'# 第二部分：提取全文\n\n' + '\n'.join(lines) + '\n')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(content)
    if '--keep-extracted' not in argv and src.endswith('.extracted.md'):
        os.remove(src)
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    print(f'OK: {out_path}  (findings={total})')


if __name__ == '__main__':
    main(sys.argv)
