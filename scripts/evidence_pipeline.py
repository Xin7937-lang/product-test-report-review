#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build structured review evidence for .docx/.pptx with stdlib-first parsing."""

import argparse
import hashlib
import io
import json
import os
import posixpath
import re
import struct
import sys
import zipfile
import xml.etree.ElementTree as ET

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from location_resolver import resolve_docx_pages

try:  # Optional only; core logic stays stdlib-first.
    from PIL import Image
except Exception:  # pragma: no cover - fallback path is the default guarantee.
    Image = None


W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
V = '{urn:schemas-microsoft-com:vml}'

SCHEMA_VERSION = 'evidence.v1'
UNIT_TOKEN = r'(?:°C|℃|%RH|%|mΩ|Ω/V|Ω|kWh|Wh|kW|W|mV|V|mA|A|mAh|Ah|Hz|rpm|min|h|kg|g|mm|cm|MPa|Pa|N|m|s)'
UNIT_VALUE_RE = re.compile(
    r'(?:(?<=\d)\s*(?P<value_unit>' + UNIT_TOKEN + r')(?=$|[^A-Za-z]))|'
    r'(?:(?<=/)\s*(?P<axis_unit>' + UNIT_TOKEN + r')(?=$|[^A-Za-z]))',
    re.I,
)
RANGE_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*(?:~|～|-|至|to)\s*(\d+(?:\.\d+)?)(?:\s*(?P<unit>' + UNIT_TOKEN + r')(?=$|[^A-Za-z]))?',
    re.I,
)
FIGURE_RE = re.compile(
    r'(?P<raw>(?P<prefix>图|Figure|Fig(?:ure)?\.?)\s*(?P<ident>[A-Za-z]?\d+(?:[-.]\d+)?[A-Za-z]?))',
    re.I,
)
PHRASE_RE = re.compile(
    r'(如图所示|如下图|见下图|见上图|as shown(?: below| above| in(?: the)? figure)?|shown below|shown above|see figure below|see figure above)',
    re.I,
)
EXPLICIT_COUNT_RE = re.compile(
    r'(?:共|共有|含|包含|如下|以下|总计)?\s*(\d+)\s*(?:张|幅|个|页)?\s*(?:图|Figures?|images?|photos?)',
    re.I,
)
AXIS_HINT_RE = re.compile(
    r'(?:横轴|纵轴|X轴|Y轴|x-axis|y-axis|axis|time|temperature|voltage|current|capacity|energy|power|frequency|温度|电压|电流|容量|能量|功率|频率|时间|内阻|阻抗|SOC)',
    re.I,
)
STOPWORDS = {
    'THE', 'AND', 'FOR', 'WITH', 'THIS', 'THAT', 'FROM', 'INTO', 'OF', 'IN',
    'ON', 'AT', 'TO', 'BY', 'IS', 'ARE', '图', 'FIG', 'FIGURE', 'AS', 'SHOWN',
    'SEE', 'BELOW', 'ABOVE', '如下图', '如图', '见下图', '见上图',
}


def _clean(text):
    return re.sub(r'\s+', ' ', text or '').strip()


def _local(tag):
    if '}' in tag:
        return tag.rsplit('}', 1)[1]
    return tag


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _dedupe_keep_order(items):
    out, seen = [], set()
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else item
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _safe_name(name):
    return re.sub(r'[^0-9A-Za-z._-]+', '_', name).strip('._') or 'artifact'


def _warning(warnings, code, message, anchor=None, source_path=None):
    record = {'code': code, 'message': message}
    if anchor:
        record['anchor'] = anchor
    if source_path:
        record['source_path'] = source_path
    warnings.append(record)


def _text_from_itertext(el):
    return _clean(' '.join(part for part in el.itertext() if part and part.strip()))


def _posix_target(part_name, target):
    base = posixpath.dirname(part_name)
    return posixpath.normpath(posixpath.join(base, target))


def _rels_name(part_name):
    parent = posixpath.dirname(part_name)
    leaf = posixpath.basename(part_name)
    return posixpath.join(parent, '_rels', leaf + '.rels') if parent else posixpath.join('_rels', leaf + '.rels')


def _load_relationships(zf, part_name, cache):
    if part_name in cache:
        return cache[part_name]
    rels = {}
    try:
        root = ET.fromstring(zf.read(_rels_name(part_name)))
    except KeyError:
        cache[part_name] = rels
        return rels
    for rel in root.iter(PKG_REL + 'Relationship'):
        rid = rel.get('Id')
        if not rid:
            continue
        rels[rid] = {
            'target': _posix_target(part_name, rel.get('Target', '')),
            'type': rel.get('Type', ''),
            'mode': rel.get('TargetMode', 'Internal'),
        }
    cache[part_name] = rels
    return rels


def _read_part(zf, source_path, warnings, anchor=None):
    try:
        return zf.read(source_path)
    except KeyError:
        _warning(warnings, 'missing_part', f'包内缺少部件: {source_path}', anchor=anchor, source_path=source_path)
        return None


def _extract_dimensions_fallback(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24:
        width, height = struct.unpack('>II', data[16:24])
        return {'width': width, 'height': height, 'source': 'png_header'}
    if data[:6] in (b'GIF87a', b'GIF89a') and len(data) >= 10:
        width, height = struct.unpack('<HH', data[6:10])
        return {'width': width, 'height': height, 'source': 'gif_header'}
    if data.startswith(b'BM') and len(data) >= 26:
        width, height = struct.unpack('<ii', data[18:26])
        return {'width': abs(width), 'height': abs(height), 'source': 'bmp_header'}
    if data.startswith(b'\xff\xd8'):
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            i += 2
            if marker in (0xD8, 0xD9):
                continue
            if i + 2 > len(data):
                break
            seg_len = struct.unpack('>H', data[i:i + 2])[0]
            if seg_len < 2 or i + seg_len > len(data):
                break
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                if i + 7 <= len(data):
                    height, width = struct.unpack('>HH', data[i + 3:i + 7])
                    return {'width': width, 'height': height, 'source': 'jpeg_header'}
                break
            i += seg_len
    if len(data) >= 8 and data[:4] in (b'II*\x00', b'MM\x00*'):
        endian = '<' if data[:2] == b'II' else '>'
        ifd_offset = struct.unpack(endian + 'I', data[4:8])[0]
        if ifd_offset + 2 <= len(data):
            count = struct.unpack(endian + 'H', data[ifd_offset:ifd_offset + 2])[0]
            cursor = ifd_offset + 2
            width = height = None
            for _ in range(count):
                if cursor + 12 > len(data):
                    break
                tag, value_type, value_count, value_or_offset = struct.unpack(endian + 'HHII', data[cursor:cursor + 12])
                if tag in (256, 257):
                    value = value_or_offset
                    if value_type == 3:
                        value = value_or_offset & 0xFFFF
                    if tag == 256:
                        width = value
                    else:
                        height = value
                cursor += 12
            if width and height:
                return {'width': width, 'height': height, 'source': 'tiff_header'}
    return None


def _detect_dimensions(data):
    if Image is not None:
        try:
            with Image.open(io.BytesIO(data)) as img:
                return {'width': int(img.width), 'height': int(img.height), 'source': 'pillow'}
        except Exception:
            pass
    return _extract_dimensions_fallback(data)


def _tokens(text):
    tokens = []
    for token in re.findall(r'[A-Za-z]+(?:-[A-Za-z]+)?|\d+(?:\.\d+)?|[\u4e00-\u9fff]{1,4}', text or ''):
        upper = token.upper()
        if upper in STOPWORDS:
            continue
        if len(token) == 1 and not token.isdigit() and not re.match(r'[\u4e00-\u9fff]', token):
            continue
        tokens.append(token)
    return tokens


def _keywords_from_texts(texts, extra=None, limit=10):
    out, seen = [], set()
    for text in texts:
        for token in _tokens(text):
            key = token.upper()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    for token in extra or []:
        key = token.upper()
        if key in seen:
            continue
        out.append(token)
        seen.add(key)
        if len(out) >= limit:
            break
    return out


def _extract_hints(text):
    clean = _clean(text)
    axis = _dedupe_keep_order(m.group(0) for m in AXIS_HINT_RE.finditer(clean))
    units = _dedupe_keep_order(
        (m.group('value_unit') or m.group('axis_unit'))
        for m in UNIT_VALUE_RE.finditer(clean)
        if (m.group('value_unit') or m.group('axis_unit'))
    )
    ranges = _dedupe_keep_order(
        _clean(f'{m.group(1)}-{m.group(2)}{m.group("unit") or ""}')
        for m in RANGE_RE.finditer(clean)
    )
    return {'axis': axis, 'units': units, 'ranges': ranges}


def _normalize_figure_id(raw):
    match = FIGURE_RE.search(raw or '')
    if not match:
        return None
    ident = match.group('ident').upper().replace('.', '-')
    ident = re.sub(r'\s+', '', ident)
    ident = re.sub(r'-+', '-', ident)
    return ident


def _figure_refs_from_text(text):
    refs = []
    for match in FIGURE_RE.finditer(text or ''):
        start = match.start()
        context = (text or '')[max(0, start - 16):match.end() + 16]
        role = 'caption' if start <= 3 and re.match(r'^\s*$', (text or '')[:start]) else 'reference'
        if re.search(r'(见|参见|详见|如|shown|see|refer)', context, re.I):
            role = 'direct_reference'
        refs.append({
            'raw': match.group('raw'),
            'normalized_id': _normalize_figure_id(match.group('raw')),
            'role': role,
            'context': _clean(context),
        })
    return refs


def _is_caption_like(text):
    return bool(re.match(r'^\s*(图|Figure|Fig(?:ure)?\.?)\s*[A-Za-z]?\d+(?:[-.]\d+)?[A-Za-z]?(?:[\s:：.\-、]|$)', text or '', re.I))


def _is_list_of_figures_like(text, section_context):
    scoped = ' '.join(section_context or [])
    return (
        bool(re.search(r'(插图目录|图目录|List of Figures)', scoped + ' ' + (text or ''), re.I)) or
        bool(re.search(r'(图|Figure|Fig\.?).+\.{2,}\s*\d+\s*$', text or '', re.I))
    )


def _excerpt(text, limit=160):
    text = _clean(text)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _anchor_family(anchor):
    if not anchor:
        return ('', 0, 0)
    if anchor.startswith('[P'):
        return ('docx', int(anchor[2:6]), 0)
    if anchor.startswith('[T'):
        return ('docx-table', int(anchor[2:4]), 0)
    match = re.match(r'\[S(\d+)(?:-([A-Za-z]?)(\d+))?\]', anchor)
    if match:
        slide = int(match.group(1))
        tail = int(match.group(3) or 0)
        return ('pptx', slide, tail)
    return ('other', 0, 0)


def _docx_style_levels(zf):
    levels = {}
    try:
        root = ET.fromstring(zf.read('word/styles.xml'))
    except KeyError:
        return levels
    for st in root.iter(W + 'style'):
        sid = st.get(W + 'styleId')
        name_el = st.find(W + 'name')
        name = (name_el.get(W + 'val') if name_el is not None else '') or ''
        title_match = re.match(r'^(?:title|标题)$', name.strip(), re.I)
        if title_match:
            levels[sid] = 1
            continue
        heading_match = re.match(r'^(?:heading|标题)\s*(\d+)$', name.strip(), re.I)
        if heading_match:
            levels[sid] = min(int(heading_match.group(1)), 6)
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


def _walk_docx_blocks(container):
    for child in list(container):
        if child.tag == W + 'p':
            yield 'p', child
        elif child.tag == W + 'tbl':
            yield 'tbl', child
        elif child.tag in (W + 'sdt', W + 'sdtContent', W + 'body'):
            for inner in _walk_docx_blocks(child):
                yield inner
        else:
            sdt_content = child.find(f'{W}sdtContent')
            if sdt_content is not None:
                for inner in _walk_docx_blocks(sdt_content):
                    yield inner


def _docx_table_rows(tbl):
    rows = []
    for tr in tbl.iter(W + 'tr'):
        row = []
        for tc in tr.findall(W + 'tc'):
            row.append(_text_from_itertext(tc))
        if any(cell for cell in row):
            rows.append(row)
    return rows


def _docx_vml_objects(zf, rels, pict, warnings):
    objects = []
    for img in pict.iter(V + 'imagedata'):
        rid = None
        for key, value in img.attrib.items():
            if key.endswith('}id'):
                rid = value
                break
        if not rid:
            objects.append({
                'kind': 'image_placeholder',
                'source_path': None,
                'source_bytes': None,
                'alt_text': '',
                'object_text': '',
                'excluded': True,
                'excluded_reason': 'VML image without relationship id',
            })
            continue
        rel = rels.get(rid)
        if not rel or rel.get('mode') == 'External':
            objects.append({
                'kind': 'image_placeholder',
                'source_path': None,
                'source_bytes': None,
                'alt_text': '',
                'object_text': '',
                'excluded': True,
                'excluded_reason': f'Unresolved or external relationship: {rid}',
            })
            continue
        source_path = rel['target']
        source_bytes = _read_part(zf, source_path, warnings)
        objects.append({
            'kind': 'image',
            'source_path': source_path,
            'source_bytes': source_bytes,
            'alt_text': '',
            'object_text': '',
            'excluded': source_bytes is None,
            'excluded_reason': None if source_bytes is not None else f'Missing image part: {source_path}',
        })
    return objects


def _xml_text_from_bytes(data):
    if not data:
        return ''
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return ''
    return _text_from_itertext(root)


def _docx_drawing_objects(zf, rels, drawing, warnings):
    objects = []
    docpr = drawing.find('.//' + WP + 'docPr')
    alt_text = _clean(' '.join(
        value for value in (
            docpr.get('name', '') if docpr is not None else '',
            docpr.get('descr', '') if docpr is not None else '',
            docpr.get('title', '') if docpr is not None else '',
        ) if value
    ))
    handled = False
    for blip in drawing.iter(A + 'blip'):
        rid = blip.get(R + 'embed')
        if not rid:
            continue
        handled = True
        rel = rels.get(rid)
        if not rel or rel.get('mode') == 'External':
            objects.append({
                'kind': 'image_placeholder',
                'source_path': None,
                'source_bytes': None,
                'alt_text': alt_text,
                'object_text': '',
                'excluded': True,
                'excluded_reason': f'Unresolved or external relationship: {rid}',
            })
            continue
        source_path = rel['target']
        source_bytes = _read_part(zf, source_path, warnings)
        objects.append({
            'kind': 'image',
            'source_path': source_path,
            'source_bytes': source_bytes,
            'alt_text': alt_text,
            'object_text': '',
            'excluded': source_bytes is None,
            'excluded_reason': None if source_bytes is not None else f'Missing image part: {source_path}',
        })
    for chart in drawing.iter():
        if _local(chart.tag) != 'chart':
            continue
        rid = chart.get(R + 'id')
        if not rid:
            continue
        handled = True
        rel = rels.get(rid)
        source_path = rel['target'] if rel else None
        source_bytes = _read_part(zf, source_path, warnings) if source_path else None
        objects.append({
            'kind': 'chart',
            'source_path': source_path,
            'source_bytes': source_bytes,
            'alt_text': alt_text,
            'object_text': _xml_text_from_bytes(source_bytes),
            'excluded': source_bytes is None,
            'excluded_reason': None if source_bytes is not None else 'Chart relationship missing or unreadable',
        })
    for rel_ids in drawing.iter():
        if _local(rel_ids.tag) != 'relIds':
            continue
        dm_rid = rel_ids.get(R + 'dm') or rel_ids.get(R + 'lo') or rel_ids.get(R + 'qs') or rel_ids.get(R + 'cs')
        if not dm_rid:
            continue
        handled = True
        rel = rels.get(dm_rid)
        source_path = rel['target'] if rel else None
        source_bytes = _read_part(zf, source_path, warnings) if source_path else None
        objects.append({
            'kind': 'smartart',
            'source_path': source_path,
            'source_bytes': source_bytes,
            'alt_text': alt_text,
            'object_text': _xml_text_from_bytes(source_bytes),
            'excluded': source_bytes is None,
            'excluded_reason': None if source_bytes is not None else 'SmartArt relationship missing or unreadable',
        })
    if not handled:
        graphic_data = drawing.find('.//' + A + 'graphicData')
        uri = graphic_data.get('uri', '') if graphic_data is not None else ''
        if 'chart' in uri:
            objects.append({
                'kind': 'chart_placeholder',
                'source_path': None,
                'source_bytes': None,
                'alt_text': alt_text,
                'object_text': '',
                'excluded': True,
                'excluded_reason': 'Chart object without resolvable chart part',
            })
        elif 'diagram' in uri:
            objects.append({
                'kind': 'smartart_placeholder',
                'source_path': None,
                'source_bytes': None,
                'alt_text': alt_text,
                'object_text': '',
                'excluded': True,
                'excluded_reason': 'SmartArt object without resolvable data part',
            })
    return objects


def _docx_objects_in_container(zf, part_name, container, rel_cache, warnings):
    rels = _load_relationships(zf, part_name, rel_cache)
    raw_objects = []
    for drawing in container.iter(W + 'drawing'):
        raw_objects.extend(_docx_drawing_objects(zf, rels, drawing, warnings))
    for pict in container.iter(W + 'pict'):
        raw_objects.extend(_docx_vml_objects(zf, rels, pict, warnings))
    return raw_objects


def _pptx_notes(zf, slide_name, rel_cache):
    rels = _load_relationships(zf, slide_name, rel_cache)
    for rel in rels.values():
        if rel['type'].endswith('/notesSlide'):
            data = _read_part(zf, rel['target'], [])
            if not data:
                return ''
            try:
                root = ET.fromstring(data)
            except ET.ParseError:
                return ''
            return _text_from_itertext(root)
    return ''


def _pptx_shape_text(el):
    paras = []
    for para in el.iter(A + 'p'):
        text = _clean(' '.join(t.text or '' for t in para.iter(A + 't')))
        if text:
            paras.append(text)
    return '；'.join(paras)


def _pptx_shape_placeholder_type(el):
    for path in (
        f'./{P}nvSpPr/{P}nvPr/{P}ph',
        f'./{P}nvPicPr/{P}nvPr/{P}ph',
        f'./{P}nvGraphicFramePr/{P}nvPr/{P}ph',
        f'./{P}nvCxnSpPr/{P}nvPr/{P}ph',
    ):
        ph = el.find(path)
        if ph is not None:
            return ph.get('type', '')
    return ''


def _pptx_shape_cnvpr(el):
    for path in (
        f'./{P}nvSpPr/{P}cNvPr',
        f'./{P}nvPicPr/{P}cNvPr',
        f'./{P}nvGraphicFramePr/{P}cNvPr',
        f'./{P}nvCxnSpPr/{P}cNvPr',
    ):
        node = el.find(path)
        if node is not None:
            return node
    return None


def _int_attr(node, name):
    if node is None:
        return None
    try:
        return int(node.get(name))
    except (TypeError, ValueError):
        return None


def _pptx_shape_geometry(el, slide_size=None):
    """Return EMU geometry and relative percentages when the shape exposes xfrm."""
    xfrm = el.find('.//' + A + 'xfrm')
    if xfrm is None:
        xfrm = el.find('.//' + P + 'xfrm')
    if xfrm is None:
        return None
    off = xfrm.find(A + 'off')
    ext = xfrm.find(A + 'ext')
    x = _int_attr(off, 'x')
    y = _int_attr(off, 'y')
    width = _int_attr(ext, 'cx')
    height = _int_attr(ext, 'cy')
    if None in (x, y, width, height):
        return None
    result = {
        'x': x,
        'y': y,
        'width': width,
        'height': height,
        'unit': 'EMU',
    }
    if slide_size and slide_size.get('width') and slide_size.get('height'):
        result['relative'] = {
            'left_percent': round(x / slide_size['width'] * 100, 1),
            'top_percent': round(y / slide_size['height'] * 100, 1),
            'width_percent': round(width / slide_size['width'] * 100, 1),
            'height_percent': round(height / slide_size['height'] * 100, 1),
        }
    return result


def _pptx_slide_size(zf):
    try:
        root = ET.fromstring(zf.read('ppt/presentation.xml'))
    except KeyError:
        return None
    node = root.find('.//' + P + 'sldSz')
    if node is None:
        return None
    width = _int_attr(node, 'cx')
    height = _int_attr(node, 'cy')
    if not width or not height:
        return None
    return {'width': width, 'height': height, 'unit': 'EMU'}


def _object_name(cnvpr):
    if cnvpr is None:
        return ''
    return _clean(' '.join(
        value for value in (
            cnvpr.get('name', ''),
            cnvpr.get('descr', ''),
            cnvpr.get('title', ''),
        ) if value
    ))


def _pptx_slide_title(sp_tree):
    first_text = ''

    def walk(nodes):
        nonlocal first_text
        for node in nodes:
            if node.tag == P + 'grpSp':
                value = walk(list(node))
                if value:
                    return value
                continue
            text = _clean(_pptx_shape_text(node))
            if text and not first_text:
                first_text = text
            ph_type = _pptx_shape_placeholder_type(node)
            if text and ph_type in ('title', 'ctrTitle', 'subTitle'):
                return text
        return ''

    title = walk(list(sp_tree))
    return title or first_text


def _unit_record(order, anchor, kind, text, section_context, part, slide=None, shape=None,
                 table=None, rows=None, position=None, object_name=None, object_type=None):
    refs = _figure_refs_from_text(text)
    return {
        'order': order,
        'anchor': anchor,
        'kind': kind,
        'text': text,
        'section_context': list(section_context or []),
        'part': part,
        'slide': slide,
        'shape': shape,
        'table': table,
        'position': position,
        'object_name': object_name,
        'object_type': object_type or kind,
        'rows': rows or [],
        'direct_figure_references': refs,
        'caption_candidates': [text] if _is_caption_like(text) else [],
        'neighbor_previous': None,
        'neighbor_next': None,
    }


def _actual_record(kind, source_anchor, unit_order, section_context, placement, source_path=None, source_bytes=None,
                   caption_candidates=None, object_text='', excluded=False, excluded_reason=None,
                   position=None, object_name=None):
    return {
        'id': None,
        'kind': kind,
        'figure_id': None,
        'normalized_figure_id': None,
        'source_anchor': source_anchor,
        'source_path': source_path,
        'extracted_path': None,
        'sha256': None,
        'dimensions': None,
        'placement': placement,
        'position': position,
        'object_name': object_name,
        'object_type': kind,
        'nearby_text': {},
        'caption_candidates': _dedupe_keep_order([c for c in (caption_candidates or []) if c]),
        'image_type_candidate': None,
        'excluded': bool(excluded),
        'excluded_reason': excluded_reason,
        'section_context': list(section_context or []),
        'matched_expected_ids': [],
        '_source_order': unit_order,
        '_blob': source_bytes,
        '_object_text': _clean(object_text),
    }


def _update_heading_stack(stack, level, text):
    while len(stack) >= level:
        stack.pop()
    stack.append(text)


def _build_docx_evidence(src, output_dir):
    units, actuals, warnings = [], [], []
    rel_cache = {}
    with zipfile.ZipFile(src) as zf:
        style_levels = _docx_style_levels(zf)
        try:
            root = ET.fromstring(zf.read('word/document.xml'))
        except KeyError:
            raise SystemExit('错误：文件不含 word/document.xml，不是有效的 .docx')
        body = root.find(W + 'body')
        if body is None:
            raise SystemExit('错误：word/document.xml 不含 body')
        section_stack = []
        p_count = t_count = 0
        for tag, el in _walk_docx_blocks(body):
            if tag == 'p':
                text = _text_from_itertext(el)
                raw_objects = _docx_objects_in_container(zf, 'word/document.xml', el, rel_cache, warnings)
                if not text and not raw_objects:
                    continue
                p_count += 1
                anchor = f'[P{p_count:04d}]'
                level = _docx_para_level(el, style_levels)
                if level and text:
                    _update_heading_stack(section_stack, level, text)
                order = len(units) + 1
                units.append(_unit_record(
                    order, anchor, 'paragraph', text, section_stack,
                    'word/document.xml',
                ))
                for raw in raw_objects:
                    actuals.append(_actual_record(
                        raw['kind'],
                        anchor,
                        order,
                        section_stack,
                        {
                            'document_part': 'body',
                            'page': None,
                            'slide': None,
                            'shape': None,
                        },
                        source_path=raw.get('source_path'),
                        source_bytes=raw.get('source_bytes'),
                        caption_candidates=[raw.get('alt_text', ''), raw.get('object_text', '')],
                        object_text=raw.get('object_text', ''),
                        object_name=raw.get('alt_text', ''),
                        excluded=raw.get('excluded', False),
                        excluded_reason=raw.get('excluded_reason'),
                    ))
            else:
                rows = _docx_table_rows(el)
                raw_objects = _docx_objects_in_container(zf, 'word/document.xml', el, rel_cache, warnings)
                if not rows and not raw_objects:
                    continue
                t_count += 1
                anchor = f'[T{t_count:02d}]'
                text = ' / '.join(' | '.join(cell for cell in row if cell) for row in rows)
                order = len(units) + 1
                units.append(_unit_record(
                    order, anchor, 'table', text, section_stack,
                    'word/document.xml', rows=rows,
                ))
                for raw in raw_objects:
                    actuals.append(_actual_record(
                        raw['kind'],
                        anchor,
                        order,
                        section_stack,
                        {
                            'document_part': 'table',
                            'page': None,
                            'slide': None,
                            'shape': None,
                        },
                        source_path=raw.get('source_path'),
                        source_bytes=raw.get('source_bytes'),
                        caption_candidates=[raw.get('alt_text', ''), raw.get('object_text', '')],
                        object_text=raw.get('object_text', ''),
                        object_name=raw.get('alt_text', ''),
                        excluded=raw.get('excluded', False),
                        excluded_reason=raw.get('excluded_reason'),
                    ))
    return {'units': units, 'actuals': actuals, 'warnings': warnings}


def _pptx_object_from_graphic_frame(zf, rels, el, warnings, slide_size=None):
    cnvpr = _pptx_shape_cnvpr(el)
    alt_text = _object_name(cnvpr)
    geometry = _pptx_shape_geometry(el, slide_size)
    gd = el.find('.//' + A + 'graphicData')
    uri = gd.get('uri', '') if gd is not None else ''
    if 'table' in uri:
        rows = []
        for tr in el.iter(A + 'tr'):
            row = []
            for tc in tr.findall(A + 'tc'):
                row.append(_text_from_itertext(tc))
            if any(cell for cell in row):
                rows.append(row)
        return {'table_rows': rows}
    if 'chart' in uri:
        chart_node = None
        for node in el.iter():
            if _local(node.tag) == 'chart':
                chart_node = node
                break
        rid = chart_node.get(R + 'id') if chart_node is not None else None
        rel = rels.get(rid) if rid else None
        source_path = rel['target'] if rel else None
        source_bytes = _read_part(zf, source_path, warnings) if source_path else None
        return {
            'actual': {
                'kind': 'chart',
                'source_path': source_path,
                'source_bytes': source_bytes,
                'caption_candidates': [alt_text],
                'object_text': _xml_text_from_bytes(source_bytes),
                'position': geometry,
                'object_name': alt_text,
                'excluded': source_bytes is None,
                'excluded_reason': None if source_bytes is not None else 'Chart relationship missing or unreadable',
            }
        }
    if 'diagram' in uri:
        rel_ids = None
        for node in el.iter():
            if _local(node.tag) == 'relIds':
                rel_ids = node
                break
        dm_rid = None
        if rel_ids is not None:
            dm_rid = rel_ids.get(R + 'dm') or rel_ids.get(R + 'lo') or rel_ids.get(R + 'qs') or rel_ids.get(R + 'cs')
        rel = rels.get(dm_rid) if dm_rid else None
        source_path = rel['target'] if rel else None
        source_bytes = _read_part(zf, source_path, warnings) if source_path else None
        return {
            'actual': {
                'kind': 'smartart',
                'source_path': source_path,
                'source_bytes': source_bytes,
                'caption_candidates': [alt_text],
                'object_text': _xml_text_from_bytes(source_bytes),
                'position': geometry,
                'object_name': alt_text,
                'excluded': source_bytes is None,
                'excluded_reason': None if source_bytes is not None else 'SmartArt relationship missing or unreadable',
            }
        }
    return {
        'actual': {
            'kind': 'object_placeholder',
            'source_path': None,
            'source_bytes': None,
            'caption_candidates': [alt_text],
            'object_text': '',
            'position': geometry,
            'object_name': alt_text,
            'excluded': True,
            'excluded_reason': f'Unsupported graphic object: {uri or "unknown"}',
        }
    }


def _build_pptx_evidence(src, output_dir):
    units, actuals, warnings = [], [], []
    rel_cache = {}

    def walk_slide_nodes(zf, slide_name, rels, nodes, sid, slide_title, counters, slide_size):
        for node in nodes:
            if node.tag == P + 'grpSp':
                walk_slide_nodes(
                    zf, slide_name, rels, list(node), sid, slide_title, counters, slide_size
                )
                continue
            if node.tag in (P + 'sp', P + 'cxnSp'):
                text = _clean(_pptx_shape_text(node))
                ph_type = _pptx_shape_placeholder_type(node)
                if not text and not ph_type:
                    continue
                counters['shape'] += 1
                anchor = f'[S{sid:02d}-{counters["shape"]}]'
                cnvpr = _pptx_shape_cnvpr(node)
                object_name = _object_name(cnvpr)
                geometry = _pptx_shape_geometry(node, slide_size)
                order = len(units) + 1
                units.append(_unit_record(
                    order, anchor, 'shape', text, [slide_title] if slide_title else [],
                    slide_name, slide=sid, shape=counters['shape'],
                    position=geometry, object_name=object_name, object_type=ph_type or 'shape',
                ))
                if not text and ph_type in ('pic', 'chart', 'media', 'obj'):
                    kind = {'pic': 'image_placeholder', 'chart': 'chart_placeholder'}.get(ph_type, 'placeholder')
                    actuals.append(_actual_record(
                        kind,
                        anchor,
                        order,
                        [slide_title] if slide_title else [],
                        {'page': sid, 'slide': sid, 'shape': counters['shape'], 'placeholder_type': ph_type},
                        caption_candidates=[],
                        position=geometry,
                        object_name=object_name,
                        excluded=True,
                        excluded_reason=f'Placeholder type "{ph_type}" has no embedded object',
                    ))
            elif node.tag == P + 'pic':
                counters['shape'] += 1
                anchor = f'[S{sid:02d}-{counters["shape"]}]'
                cnvpr = _pptx_shape_cnvpr(node)
                alt_text = _object_name(cnvpr)
                geometry = _pptx_shape_geometry(node, slide_size)
                order = len(units) + 1
                units.append(_unit_record(
                    order, anchor, 'image', alt_text, [slide_title] if slide_title else [],
                    slide_name, slide=sid, shape=counters['shape'],
                    position=geometry, object_name=alt_text, object_type='image',
                ))
                blip = node.find('.//' + A + 'blip')
                rid = blip.get(R + 'embed') if blip is not None else None
                rel = rels.get(rid) if rid else None
                source_path = rel['target'] if rel else None
                source_bytes = _read_part(zf, source_path, warnings) if source_path else None
                actuals.append(_actual_record(
                    'image',
                    anchor,
                    order,
                    [slide_title] if slide_title else [],
                    {'page': sid, 'slide': sid, 'shape': counters['shape'], 'placeholder_type': None},
                    source_path=source_path,
                    source_bytes=source_bytes,
                    caption_candidates=[alt_text],
                    position=geometry,
                    object_name=alt_text,
                    excluded=source_bytes is None,
                    excluded_reason=None if source_bytes is not None else 'Picture relationship missing or unreadable',
                ))
            elif node.tag == P + 'graphicFrame':
                info = _pptx_object_from_graphic_frame(zf, rels, node, warnings, slide_size)
                if 'table_rows' in info:
                    rows = info['table_rows']
                    if not rows:
                        continue
                    counters['table'] += 1
                    anchor = f'[S{sid:02d}-T{counters["table"]}]'
                    order = len(units) + 1
                    units.append(_unit_record(
                        order, anchor, 'table',
                        ' / '.join(' | '.join(cell for cell in row if cell) for row in rows),
                        [slide_title] if slide_title else [], slide_name,
                        slide=sid, table=counters['table'], rows=rows,
                        position=_pptx_shape_geometry(node, slide_size),
                        object_name=_object_name(_pptx_shape_cnvpr(node)),
                        object_type='table',
                    ))
                else:
                    counters['shape'] += 1
                    anchor = f'[S{sid:02d}-{counters["shape"]}]'
                    actual = info['actual']
                    text = actual.get('object_text', '') or _clean(' '.join(actual.get('caption_candidates', [])))
                    order = len(units) + 1
                    units.append(_unit_record(
                        order, anchor, 'object', text, [slide_title] if slide_title else [],
                        slide_name, slide=sid, shape=counters['shape'],
                        position=actual.get('position'),
                        object_name=actual.get('object_name'),
                        object_type=actual.get('kind'),
                    ))
                    actuals.append(_actual_record(
                        actual['kind'],
                        anchor,
                        order,
                        [slide_title] if slide_title else [],
                        {'page': sid, 'slide': sid, 'shape': counters['shape'], 'placeholder_type': None},
                        source_path=actual.get('source_path'),
                        source_bytes=actual.get('source_bytes'),
                        caption_candidates=actual.get('caption_candidates'),
                        object_text=actual.get('object_text', ''),
                        position=actual.get('position'),
                        object_name=actual.get('object_name'),
                        excluded=actual.get('excluded', False),
                        excluded_reason=actual.get('excluded_reason'),
                    ))

    with zipfile.ZipFile(src) as zf:
        slide_size = _pptx_slide_size(zf)
        slide_names = sorted(
            [name for name in zf.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', name)],
            key=lambda x: int(re.search(r'slide(\d+)\.xml$', x).group(1)),
        )
        if not slide_names:
            raise SystemExit('错误：文件不含 ppt/slides/slide*.xml，不是有效的 .pptx')
        for slide_name in slide_names:
            sid = int(re.search(r'slide(\d+)\.xml$', slide_name).group(1))
            root = ET.fromstring(zf.read(slide_name))
            sp_tree = root.find(f'.//{P}spTree')
            if sp_tree is None:
                continue
            slide_title = _pptx_slide_title(sp_tree)
            slide_anchor = f'[S{sid:02d}]'
            slide_order = len(units) + 1
            units.append(_unit_record(
                slide_order,
                slide_anchor,
                'slide',
                slide_title,
                [slide_title] if slide_title else [],
                slide_name,
                slide=sid,
            ))
            rels = _load_relationships(zf, slide_name, rel_cache)
            counters = {'shape': 0, 'table': 0}
            walk_slide_nodes(
                zf, slide_name, rels, list(sp_tree), sid, slide_title, counters, slide_size
            )
            notes = _pptx_notes(zf, slide_name, rel_cache)
            if notes:
                note_anchor = f'[S{sid:02d}-N]'
                units.append(_unit_record(
                    len(units) + 1, note_anchor, 'notes', notes, [slide_title] if slide_title else [],
                    slide_name, slide=sid,
                ))
    return {'units': units, 'actuals': actuals, 'warnings': warnings, 'slide_size': slide_size}


def _apply_neighbor_context(units):
    text_indexes = [i for i, unit in enumerate(units) if unit.get('text') or unit.get('rows')]
    for i, unit in enumerate(units):
        prev_idx = next_idx = None
        for candidate in reversed(text_indexes):
            if candidate < i:
                prev_idx = candidate
                break
        for candidate in text_indexes:
            if candidate > i:
                next_idx = candidate
                break
        if prev_idx is not None:
            unit['neighbor_previous'] = {
                'anchor': units[prev_idx]['anchor'],
                'text': _excerpt(units[prev_idx].get('text') or ' / '.join(' | '.join(r) for r in units[prev_idx].get('rows', []))),
            }
        if next_idx is not None:
            unit['neighbor_next'] = {
                'anchor': units[next_idx]['anchor'],
                'text': _excerpt(units[next_idx].get('text') or ' / '.join(' | '.join(r) for r in units[next_idx].get('rows', []))),
            }


def _actual_type_candidate(actual, context_text):
    text = ' '.join([
        ' '.join(actual.get('caption_candidates', [])),
        actual.get('_object_text', ''),
        context_text,
        actual.get('source_path') or '',
        actual.get('kind') or '',
    ]).lower()
    kind = actual.get('kind')
    if 'chart' in (kind or '') or 'axis' in text or '曲线' in text or '柱状' in text:
        return 'chart'
    if 'smartart' in (kind or '') or '流程' in text or '示意' in text or '架构' in text or 'diagram' in text:
        return 'diagram'
    if 'screenshot' in text or '截图' in text or '界面' in text or 'screen' in text:
        return 'screenshot'
    if 'photo' in text or '照片' in text or '现场' in text or '外观' in text:
        return 'photo'
    if 'logo' in text:
        return 'logo'
    if 'placeholder' in (kind or ''):
        return 'placeholder'
    return 'image'


def _attach_actual_context(units, actuals):
    anchor_to_index = {unit['anchor']: idx for idx, unit in enumerate(units)}
    for actual in actuals:
        idx = anchor_to_index.get(actual['source_anchor'])
        if idx is None:
            continue
        unit = units[idx]
        nearby = {
            'same_unit': _excerpt(unit.get('text') or ' / '.join(' | '.join(r) for r in unit.get('rows', []))),
            'previous': unit.get('neighbor_previous'),
            'next': unit.get('neighbor_next'),
        }
        actual['nearby_text'] = nearby
        candidates = list(actual.get('caption_candidates', []))
        for text in (
            unit.get('text', ''),
            (unit.get('neighbor_previous') or {}).get('text', ''),
            (unit.get('neighbor_next') or {}).get('text', ''),
        ):
            clean = _clean(text)
            if clean and (_is_caption_like(clean) or len(clean) <= 120):
                candidates.append(clean)
        actual['caption_candidates'] = _dedupe_keep_order([c for c in candidates if c])
        for candidate in actual['caption_candidates']:
            refs = _figure_refs_from_text(candidate)
            if refs:
                actual['figure_id'] = refs[0]['raw']
                actual['normalized_figure_id'] = refs[0]['normalized_id']
                break
        context_text = ' '.join(
            part for part in (
                unit.get('text', ''),
                (unit.get('neighbor_previous') or {}).get('text', ''),
                (unit.get('neighbor_next') or {}).get('text', ''),
                actual.get('_object_text', ''),
            ) if part
        )
        actual['image_type_candidate'] = _actual_type_candidate(actual, context_text)


def _mark_non_figure_assets(actuals):
    for actual in actuals:
        searchable = ' '.join([
            actual.get('source_path') or '',
            actual.get('figure_id') or '',
            ' '.join(actual.get('caption_candidates') or []),
            actual.get('image_type_candidate') or '',
        ]).lower()
        if actual.get('image_type_candidate') == 'logo' or re.search(
                r'(^|[\s_\-/])logo($|[\s_\-.\/])|watermark|company mark|企业标识|公司标识',
                searchable):
            actual['excluded'] = True
            actual['excluded_reason'] = 'Likely logo/watermark/decorative brand asset; excluded from figure counts'


def _write_artifacts(output_dir, actuals):
    media_dir = os.path.join(output_dir, 'media')
    os.makedirs(media_dir, exist_ok=True)
    extracted_cache = {}
    for idx, actual in enumerate(actuals, 1):
        actual['id'] = f'AF{idx:04d}'
        blob = actual.pop('_blob', None)
        if blob is None:
            actual.pop('_object_text', None)
            continue
        sha = _sha256_bytes(blob)
        actual['sha256'] = sha
        actual['dimensions'] = _detect_dimensions(blob)
        source_name = _safe_name(os.path.basename(actual.get('source_path') or f'{actual["id"]}.bin'))
        rel_path = extracted_cache.get((sha, source_name))
        if rel_path is None:
            rel_path = os.path.join('media', f'{actual["id"]}_{source_name}')
            abs_path = os.path.join(output_dir, rel_path)
            with open(abs_path, 'wb') as f:
                f.write(blob)
            extracted_cache[(sha, source_name)] = rel_path
        actual['extracted_path'] = rel_path
        actual.pop('_object_text', None)


def _expected_scope(unit):
    if unit.get('slide'):
        return ('slide', unit['slide'])
    if unit.get('section_context'):
        return ('section', tuple(unit['section_context']))
    return ('document', 1)


def _actual_scope(actual):
    slide = actual.get('placement', {}).get('slide')
    if slide:
        return ('slide', slide)
    if actual.get('section_context'):
        return ('section', tuple(actual['section_context']))
    return ('document', 1)


_LOCATION_OBJECT_LABELS = {
    'chart': '图表',
    'chart_placeholder': '图表占位符',
    'image': '图片',
    'image_placeholder': '图片占位符',
    'smartart': 'SmartArt',
    'object': '图形对象',
    'object_placeholder': '图形对象占位符',
    'table': '表格',
    'shape': '形状',
    'slide': '页面',
    'notes': '备注',
}


def _location_anchor_id(anchor):
    token = re.sub(r'[^0-9A-Za-z_-]+', '-', str(anchor or '').strip('[]'))
    return f'location-{token or "unknown"}'


def _position_summary(position):
    relative = (position or {}).get('relative') if isinstance(position, dict) else None
    if not relative:
        return ''
    left = relative.get('left_percent')
    top = relative.get('top_percent')
    width = relative.get('width_percent')
    height = relative.get('height_percent')
    parts = []
    if left is not None:
        parts.append(f'左侧 {left:g}%')
    if top is not None:
        parts.append(f'上方 {top:g}%')
    if width is not None and height is not None:
        parts.append(f'尺寸 {width:g}%×{height:g}%')
    return ' · '.join(parts)


def _location_display(source_type, anchor, page=None, page_status='unavailable',
                      paragraph=None, table=None, slide=None, shape=None,
                      object_type=None, section_context=None, position_summary=''):
    label = _LOCATION_OBJECT_LABELS.get(object_type or '', object_type or '')
    parts = []
    if source_type == 'pptx':
        if slide is not None:
            parts.append(f'第 {slide} 页')
        if table is not None:
            parts.append(f'第 {table} 个表格')
        elif shape is not None:
            parts.append(f'第 {shape} 个形状')
        if label and label not in {'页面', '形状'}:
            parts.append(f'（{label}）')
        if position_summary:
            parts.append(position_summary)
    else:
        if page is not None:
            parts.append(f'第 {page} 页')
        elif page_status != 'resolved':
            parts.append('页码未解析')
        if paragraph is not None:
            parts.append(f'第 {paragraph} 个段落')
        if table is not None:
            parts.append(f'第 {table} 个表格')
        if section_context:
            parts.append(f'章节：{" / ".join(section_context[-2:])}')
    parts.append(anchor or '[?]')
    return ' · '.join(parts)


def _location_detail(src, output_dir, anchor, source_type, part, section_context,
                     page_info=None, slide=None, shape=None, table=None,
                     position=None, object_name=None, object_type=None,
                     paragraph=None, page_label=None):
    page_info = page_info or {}
    page = page_info.get('pages', {}).get(anchor)
    page_status = 'resolved' if page is not None else 'unavailable'
    if source_type == 'pptx':
        page = slide
        page_status = 'known'
        page_label = 'slide'
    position_summary = _position_summary(position)
    detail = {
        'schema_version': 'location.v1',
        'source_type': source_type,
        'source_file': os.path.abspath(src),
        'source_anchor': anchor,
        'document_part': part,
        'section_context': list(section_context or []),
        'page': page,
        'page_status': page_status,
        'page_method': page_info.get('method') if source_type == 'docx' else 'slide-number',
        'page_label': page_label or 'page',
        'paragraph': paragraph,
        'table': table,
        'slide': slide,
        'shape': shape,
        'object_type': object_type,
        'object_name': object_name,
        'position': position,
        'position_summary': position_summary,
        'evidence_path': os.path.abspath(os.path.join(output_dir, 'evidence.html')),
        'evidence_anchor': _location_anchor_id(anchor),
    }
    detail['display'] = _location_display(
        source_type,
        anchor,
        page=page,
        page_status=page_status,
        paragraph=paragraph,
        table=table,
        slide=slide,
        shape=shape,
        object_type=object_type,
        section_context=section_context,
        position_summary=position_summary,
    )
    if page_status == 'unavailable':
        detail['page_note'] = 'DOCX 页码需要 Word/LibreOffice 解析；当前未获得真实页码，未猜测。'
    return detail


def _anchor_parts(anchor):
    if not anchor:
        return {}
    text = str(anchor)
    paragraph = re.match(r'^\[P(\d+)\]$', text)
    if paragraph:
        return {'paragraph': int(paragraph.group(1))}
    table = re.match(r'^\[T(\d+)\]$', text)
    if table:
        return {'table': int(table.group(1))}
    ppt = re.match(r'^\[S(\d+)(?:-([A-Za-z]+)(\d+))?\]$', text)
    if ppt:
        result = {'slide': int(ppt.group(1))}
        if ppt.group(2) == 'T':
            result['table'] = int(ppt.group(3))
        elif ppt.group(2):
            result['shape'] = int(ppt.group(3))
        return result
    return {}


def _attach_location_details(src, output_dir, source_type, units, actuals, page_info=None):
    by_anchor = {}
    for unit in units:
        parts = _anchor_parts(unit.get('anchor'))
        detail = _location_detail(
            src,
            output_dir,
            unit.get('anchor'),
            source_type,
            unit.get('part'),
            unit.get('section_context'),
            page_info=page_info,
            slide=unit.get('slide') or parts.get('slide'),
            shape=unit.get('shape') or parts.get('shape'),
            table=unit.get('table') or parts.get('table'),
            position=unit.get('position'),
            object_name=unit.get('object_name'),
            object_type=unit.get('object_type') or unit.get('kind'),
            paragraph=parts.get('paragraph'),
        )
        unit['location_detail'] = detail
        by_anchor[unit.get('anchor')] = detail

    for actual in actuals:
        anchor = actual.get('source_anchor')
        base = dict(by_anchor.get(anchor) or {})
        placement = actual.get('placement') or {}
        parts = _anchor_parts(anchor)
        if not base:
            base = _location_detail(
                src,
                output_dir,
                anchor,
                source_type,
                None,
                actual.get('section_context'),
                page_info=page_info,
                slide=placement.get('slide') or parts.get('slide'),
                shape=placement.get('shape') or parts.get('shape'),
                table=placement.get('table') or parts.get('table'),
            )
        base.update({
            'object_type': actual.get('object_type') or actual.get('kind') or base.get('object_type'),
            'object_name': actual.get('object_name') or base.get('object_name'),
            'position': actual.get('position') or base.get('position'),
        })
        base['position_summary'] = _position_summary(base.get('position'))
        base['display'] = _location_display(
            source_type,
            base.get('source_anchor'),
            page=base.get('page'),
            page_status=base.get('page_status'),
            paragraph=base.get('paragraph'),
            table=base.get('table'),
            slide=base.get('slide'),
            shape=base.get('shape'),
            object_type=base.get('object_type'),
            section_context=base.get('section_context'),
            position_summary=base.get('position_summary'),
        )
        base['actual_id'] = actual.get('id')
        actual['location_detail'] = base
    return by_anchor


def _add_expected_group(groups, key, source_type, unit, figure_id=None, normalized_id=None, required=True,
                        count_hint=None, note=None):
    if key not in groups:
        groups[key] = {
            'expected_id': None,
            'figure_id': figure_id,
            'normalized_figure_id': normalized_id,
            'source_types': [],
            'source_anchors': [],
            'excerpts': [],
            'evidence': [],
            'keywords': [],
            'required': required,
            'axis_unit_range_hints': {'axis': [], 'units': [], 'ranges': []},
            'section_context': list(unit.get('section_context') or []),
            'scope': _expected_scope(unit),
            '_order': unit['order'],
            'count_hint': count_hint,
            'note': note,
        }
    group = groups[key]
    group['required'] = group['required'] or required
    if source_type not in group['source_types']:
        group['source_types'].append(source_type)
    if unit['anchor'] not in group['source_anchors']:
        group['source_anchors'].append(unit['anchor'])
    excerpt = _excerpt(unit.get('text') or ' / '.join(' | '.join(r) for r in unit.get('rows', [])))
    if excerpt and excerpt not in group['excerpts']:
        group['excerpts'].append(excerpt)
    evidence = {'type': source_type, 'anchor': unit['anchor'], 'excerpt': excerpt}
    if note:
        evidence['note'] = note
    group['evidence'].append(evidence)
    hints = _extract_hints(' '.join(filter(None, [
        unit.get('text', ''),
        (unit.get('neighbor_previous') or {}).get('text', ''),
        (unit.get('neighbor_next') or {}).get('text', ''),
    ])))
    for name in ('axis', 'units', 'ranges'):
        group['axis_unit_range_hints'][name] = _dedupe_keep_order(group['axis_unit_range_hints'][name] + hints[name])
    group['keywords'] = _keywords_from_texts(group['excerpts'], extra=[figure_id] if figure_id else [])


def _derive_expected_figures(units):
    groups = {}
    numbered = []
    for unit in units:
        text = unit.get('text', '')
        if not text:
            continue
        refs = _figure_refs_from_text(text)
        for ref in refs:
            source_type = 'caption' if _is_caption_like(text) else ref['role']
            if _is_list_of_figures_like(text, unit.get('section_context')):
                source_type = 'list_of_figures'
            key = ('fig', ref['normalized_id'])
            _add_expected_group(
                groups,
                key,
                source_type,
                unit,
                figure_id=ref['raw'],
                normalized_id=ref['normalized_id'],
                required=True,
            )
            if ref['normalized_id'] and re.match(r'^\d+$', ref['normalized_id']):
                numbered.append((int(ref['normalized_id']), unit))
        for phrase in PHRASE_RE.finditer(text):
            phrase_key = ('phrase', unit['anchor'], phrase.group(0))
            _add_expected_group(
                groups, phrase_key, 'implicit_phrase', unit, required=True, note=phrase.group(0)
            )
        for count_match in EXPLICIT_COUNT_RE.finditer(text):
            count = int(count_match.group(1))
            count_key = ('count', unit['anchor'], count)
            _add_expected_group(
                groups, count_key, 'explicit_count', unit, required=True, count_hint=count,
                note=f'count={count}',
            )
    numbered = sorted({(num, unit['anchor'], unit['order']) for num, unit in numbered}, key=lambda item: item[0])
    for i in range(len(numbered) - 1):
        current_num, current_anchor, current_order = numbered[i]
        next_num, _, _ = numbered[i + 1]
        if next_num - current_num <= 1:
            continue
        for missing in range(current_num + 1, next_num):
            anchor_unit = next(unit for unit in units if unit['anchor'] == current_anchor)
            _add_expected_group(
                groups,
                ('gap', str(missing)),
                'numbering_gap',
                anchor_unit,
                figure_id=f'图{missing}',
                normalized_id=str(missing),
                required=True,
                note=f'missing between {current_num} and {next_num}',
            )
    expected = sorted(groups.values(), key=lambda item: (item['_order'], item['figure_id'] or '', item['count_hint'] or 0))
    for idx, item in enumerate(expected, 1):
        item['expected_id'] = f'EF{idx:04d}'
        item['source_types'] = _dedupe_keep_order(item['source_types'])
        item['source_anchors'] = _dedupe_keep_order(item['source_anchors'])
        item['excerpts'] = _dedupe_keep_order(item['excerpts'])
        item['evidence'] = _dedupe_keep_order(item['evidence'])
        item['keywords'] = _dedupe_keep_order(item['keywords'])
        item.pop('_order', None)
        item.pop('scope', None)
    return expected


def _overlap_score(left, right):
    left_set = {token.upper() for token in _tokens(left)}
    right_set = {token.upper() for token in _tokens(right)}
    if not left_set or not right_set:
        return 0
    return len(left_set & right_set)


def _match_score(expected, actual):
    score = 0
    reasons = []
    if expected.get('normalized_figure_id') and expected['normalized_figure_id'] == actual.get('normalized_figure_id'):
        score += 100
        reasons.append('figure_id_exact')
    elif expected.get('figure_id') and actual.get('figure_id') and _overlap_score(expected['figure_id'], actual['figure_id']):
        score += 40
        reasons.append('figure_id_partial')
    exp_text = ' '.join(expected.get('excerpts', []))
    act_text = ' '.join(actual.get('caption_candidates', []))
    overlap = _overlap_score(exp_text, act_text)
    if overlap:
        score += min(35, overlap * 7)
        reasons.append('caption_overlap')
    if expected.get('section_context') and actual.get('section_context') and expected['section_context'] == actual['section_context']:
        score += 15
        reasons.append('section_match')
    exp_family = _anchor_family(expected['source_anchors'][0] if expected.get('source_anchors') else '')
    act_family = _anchor_family(actual.get('source_anchor'))
    if exp_family[:2] == act_family[:2]:
        score += 12
        reasons.append('location_family')
    if actual.get('image_type_candidate') == 'chart' and any(unit for unit in expected.get('axis_unit_range_hints', {}).get('axis', [])):
        score += 8
        reasons.append('axis_hint_support')
    return score, reasons


def _build_matches(expected_figures, actual_figures):
    matches = []
    used_actual_ids = set()
    used_hashes = set()

    def expected_priority(item):
        type_rank = 0
        if 'caption' in item.get('source_types', []):
            type_rank = 0
        elif 'direct_reference' in item.get('source_types', []):
            type_rank = 1
        elif 'list_of_figures' in item.get('source_types', []):
            type_rank = 2
        elif 'numbering_gap' in item.get('source_types', []):
            type_rank = 3
        elif 'explicit_count' in item.get('source_types', []):
            type_rank = 4
        else:
            type_rank = 5
        return (type_rank, item['source_anchors'][0] if item.get('source_anchors') else '', item.get('figure_id') or '')

    ordered_expected = sorted(expected_figures, key=expected_priority)
    for idx, expected in enumerate(ordered_expected, 1):
        match = {
            'match_id': f'M{idx:04d}',
            'expected_id': expected['expected_id'],
            'actual_ids': [],
            'status': 'unmatched',
            'score': 0,
            'match_basis': [],
        }
        if 'explicit_count' in expected.get('source_types', []):
            source_anchor = expected['source_anchors'][0] if expected.get('source_anchors') else ''
            scope = None
            for unit in actual_figures:
                if unit.get('source_anchor') == source_anchor:
                    scope = _actual_scope(unit)
                    break
            if scope is None:
                scope = ('slide', _anchor_family(source_anchor)[1]) if source_anchor.startswith('[S') else ('document', 1)
            scoped_actuals = [a for a in actual_figures if not a.get('excluded') and _actual_scope(a) == scope]
            match['actual_ids'] = [a['id'] for a in scoped_actuals]
            match['score'] = len(scoped_actuals)
            expected_count = expected.get('count_hint') or 0
            match['status'] = 'count_satisfied' if len(scoped_actuals) >= expected_count else 'count_shortfall'
            match['match_basis'] = ['explicit_count', f'expected={expected_count}', f'actual={len(scoped_actuals)}']
            matches.append(match)
            continue
        candidates = []
        for actual in actual_figures:
            score, reasons = _match_score(expected, actual)
            if actual['id'] in used_actual_ids:
                score -= 10
            if actual.get('sha256') and actual['sha256'] in used_hashes:
                score -= 5
                reasons = reasons + ['media_hash_duplicate_penalty']
            candidates.append((score, reasons, actual))
        candidates.sort(key=lambda item: (-item[0], item[2]['source_anchor'], item[2]['id']))
        best_score, reasons, best_actual = candidates[0] if candidates else (0, [], None)
        if best_actual is not None and best_score >= 35:
            match['actual_ids'] = [best_actual['id']]
            match['status'] = 'matched'
            match['score'] = best_score
            match['match_basis'] = reasons
            used_actual_ids.add(best_actual['id'])
            if best_actual.get('sha256'):
                used_hashes.add(best_actual['sha256'])
            best_actual['matched_expected_ids'].append(expected['expected_id'])
        else:
            match['match_basis'] = reasons if best_actual is not None else []
        matches.append(match)
    return matches


def _finalize_expected(expected_figures, matches):
    matched = {match['expected_id']: match for match in matches}
    for item in expected_figures:
        item['match_status'] = matched[item['expected_id']]['status']
        item['matched_actual_ids'] = matched[item['expected_id']]['actual_ids']


def _finalize_actual(actual_figures):
    for actual in actual_figures:
        actual['matched_expected_ids'] = _dedupe_keep_order(actual['matched_expected_ids'])
        actual.pop('_source_order', None)


def _attach_expected_location_details(expected_figures, by_anchor):
    for item in expected_figures:
        details = [
            by_anchor[anchor]
            for anchor in item.get('source_anchors') or []
            if anchor in by_anchor
        ]
        item['location_details'] = _dedupe_keep_order(details)
        if len(details) == 1:
            item['location_detail'] = details[0]
        elif details:
            item['location_detail'] = details


def build_evidence(src, output_dir, page_map_path=None, resolve_pages=False):
    ext = os.path.splitext(src)[1].lower()
    page_info = {
        'pages': {},
        'method': 'unavailable',
        'status': 'unavailable',
        'warnings': [],
    }
    if ext == '.docx':
        parsed = _build_docx_evidence(src, output_dir)
        source_type = 'docx'
        page_info = resolve_docx_pages(
            src,
            parsed['units'],
            page_map_path=page_map_path,
            use_word=resolve_pages,
        )
    elif ext == '.pptx':
        parsed = _build_pptx_evidence(src, output_dir)
        source_type = 'pptx'
    else:
        raise SystemExit(f'错误：不支持的格式 {ext}（仅支持 .docx / .pptx）')
    units = parsed['units']
    actuals = parsed['actuals']
    warnings = parsed['warnings']
    for message in page_info.get('warnings') or []:
        _warning(warnings, 'page_resolution', message, source_path=src)
    _apply_neighbor_context(units)
    _attach_actual_context(units, actuals)
    _mark_non_figure_assets(actuals)
    _write_artifacts(output_dir, actuals)
    location_by_anchor = _attach_location_details(
        src,
        output_dir,
        source_type,
        units,
        actuals,
        page_info=page_info,
    )
    expected = _derive_expected_figures(units)
    _attach_expected_location_details(expected, location_by_anchor)
    matches = _build_matches(expected, actuals)
    _finalize_expected(expected, matches)
    _finalize_actual(actuals)
    return {
        'schema_version': SCHEMA_VERSION,
        'document': {
            'source_path': os.path.abspath(src),
            'source_name': os.path.basename(src),
            'source_type': source_type,
            'source_sha256': _sha256_file(src),
            'artifact_dir': os.path.abspath(output_dir),
            'pillow_available': Image is not None,
            'unit_count': len(units),
            'expected_figure_count': len(expected),
            'actual_figure_count': len(actuals),
            'match_count': len(matches),
            'location_schema_version': 'location.v1',
            'page_resolution': page_info,
            'evidence_viewer_path': os.path.abspath(os.path.join(output_dir, 'evidence.html')),
        },
        'units': units,
        'expected_figures': expected,
        'actual_figures': actuals,
        'matches': matches,
        'extraction_warnings': warnings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='提取 DOCX/PPTX 的结构化图证据（文档单元、媒体、预期图、匹配结果）。'
    )
    parser.add_argument('source', help='输入的 .docx 或 .pptx 文件')
    parser.add_argument(
        '--output-dir',
        required=True,
        help='输出工件目录（将创建 media 子目录）',
    )
    parser.add_argument(
        '--output-json',
        help='输出 JSON 路径；默认写入 <output-dir>\\evidence.json',
    )
    parser.add_argument(
        '--page-map',
        help='可选 DOCX 页码映射 JSON（格式：{"[P0001]": 2, "[T01]": 3}）',
    )
    parser.add_argument(
        '--resolve-pages',
        action='store_true',
        help='尝试通过可用的 Microsoft Word COM 解析 DOCX 真实页码',
    )
    args = parser.parse_args(argv)

    src = os.path.abspath(args.source)
    output_dir = os.path.abspath(args.output_dir)
    output_json = os.path.abspath(args.output_json or os.path.join(output_dir, 'evidence.json'))
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.dirname(output_json), exist_ok=True)

    evidence = build_evidence(
        src,
        output_dir,
        page_map_path=args.page_map,
        resolve_pages=args.resolve_pages,
    )
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(evidence, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write('\n')
    from render_evidence import write_evidence_html
    evidence_viewer = write_evidence_html(
        output_json,
        os.path.join(output_dir, 'evidence.html'),
    )
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    print(
        f"OK: {output_json} "
        f"(units={evidence['document']['unit_count']}, "
        f"expected={evidence['document']['expected_figure_count']}, "
        f"actual={evidence['document']['actual_figure_count']}, "
        f"warnings={len(evidence['extraction_warnings'])}, "
        f"viewer={evidence_viewer})"
    )


if __name__ == '__main__':
    main()
