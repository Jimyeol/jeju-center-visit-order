# -*- coding: utf-8 -*-
"""데이터명세서_kr.xlsx 를 읽어 data/spec/spec.json 을 만든다.

표준 라이브러리(zipfile, xml.etree)만 쓴다. 외부에서 받은 파일을 읽으므로
`python3 -I tools/build_spec.py` 로 돌려도 동작한다(자기 폴더의 다른 모듈을 import 하지 않는다).

종수와 컬럼 수는 이 스크립트가 세어 spec.json 의 counts 에 적는다.
같은 입력이면 같은 출력이 나온다(만든 시각을 적지 않는다).

사용:
    python3 tools/build_spec.py            # 변환하고 요약을 출력
    python3 tools/build_spec.py --check    # 변환 결과가 기존 spec.json 과 같은지만 확인
"""
import datetime
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPEC_DIR = os.path.join(ROOT, "data", "spec")
XLSX_NAME = "데이터명세서_kr.xlsx"
XLSX_PATH = os.path.join(SPEC_DIR, XLSX_NAME)
OUT_PATH = os.path.join(SPEC_DIR, "spec.json")

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

PARTITION_COLUMNS = ("pt_year", "pt_month")
# 메뉴코드표는 4,889행이라 값은 싣지 않고 행 수와 단계별 개수만 적는다(메뉴 단위 분석은 범위 밖).
REF_SHEET_MAX_ROWS = 500


# ---------------------------------------------------------------- xlsx 읽기

def _col_num(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


def col_letter(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def read_workbook(path):
    """[(시트 이름, {(행, 열): 값 문자열})] 을 시트 순서대로 돌려준다."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(NS + "si"):
                parts = []
                for node in si.iter():
                    if node.tag == NS + "rPh":
                        continue
                    if node.tag == NS + "t":
                        parts.append(node.text or "")
                shared.append("".join(parts))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rel_map = {}
        for r in rels:
            rel_map[r.get("Id")] = r.get("Target")
        sheets = []
        for sh in wb.find(NS + "sheets"):
            name = sh.get("name")
            target = rel_map[sh.get(RNS + "id")]
            target = target.lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            root = ET.fromstring(z.read(target))
            cells = {}
            for c in root.iter(NS + "c"):
                ref = c.get("r") or ""
                m = re.match(r"([A-Z]+)(\d+)$", ref)
                if not m:
                    continue
                t = c.get("t")
                v = c.find(NS + "v")
                if t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(NS + "t"))
                elif v is None or v.text is None:
                    continue
                elif t == "s":
                    val = shared[int(v.text)]
                else:
                    val = v.text
                if val.strip() == "":
                    continue
                cells[(int(m.group(2)), _col_num(m.group(1)))] = val
            sheets.append((name, cells))
    return sheets


# ---------------------------------------------------------------- 작은 도구

def clean(s):
    """앞뒤 공백과 줄 끝 공백을 지운다. 값 칸 앞 공백 2칸, 컬럼명 뒤 공백을 흡수한다."""
    if s is None:
        return ""
    return str(s).replace(" ", " ").strip()


def excel_serial_to_date(text):
    try:
        n = int(float(text))
    except (TypeError, ValueError):
        return None
    if n < 20000 or n > 80000:
        return None
    d = datetime.date(1899, 12, 30) + datetime.timedelta(days=n)
    return d.isoformat()


def _find_label_row(cells, label, max_row=20):
    for (r, c), v in sorted(cells.items()):
        if c == 1 and r <= max_row and clean(v) == label:
            return r
    return None


def _right_of(cells, row, label):
    """row 행에서 값이 label 인 칸의 오른쪽 첫 값."""
    cols = sorted(c for (r, c) in cells if r == row)
    for c in cols:
        if clean(cells[(row, c)]) == label:
            for c2 in cols:
                if c2 > c:
                    return clean(cells[(row, c2)])
    return ""


# ---------------------------------------------------------------- 기간 뽑기

_DATE_KR = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?")
_DATE_DOT = re.compile(r"(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})")


def _dates_in(line):
    """줄에서 날짜 표기를 나온 순서대로 뽑는다: (위치, 연, 월, 일 또는 None)."""
    found = []
    for m in _DATE_KR.finditer(line):
        found.append((m.start(), int(m.group(1)), int(m.group(2)),
                      int(m.group(3)) if m.group(3) else None))
    for m in _DATE_DOT.finditer(line):
        found.append((m.start(), int(m.group(1)), int(m.group(2)), int(m.group(3))))
    found.sort()
    return [f for f in found if 1 <= f[2] <= 12]


def _ym(y, m):
    return "%04d-%02d" % (y, m)


def parse_period(description):
    """설명란에서 수록 기간을 뽑는다. 날짜가 없으면 None.

    kind: range(시작과 끝), open(시작 + 현재까지), accumulating(시작 + 누적 업데이트, 끝 표기 없음),
          snapshot(단일 기준일), before(~ 이전 등록).
    """
    lines = [clean(x).lstrip("-").strip() for x in (description or "").split("\n")]
    segments = []
    raws = []
    for line in lines:
        if not line:
            continue
        # 기간이 아니라 컬럼 형식이 바뀐 날짜를 적은 줄은 건너뛴다.
        if "컬럼" in line:
            continue
        dates = _dates_in(line)
        if not dates:
            continue
        label = ""
        m = re.search(r"\(([A-Za-z, ]+)\)\s*$", line)
        if m:
            label = m.group(1).strip()
        has_now = "현재" in line
        seg = None
        if len(dates) >= 2 and ("~" in line or ("부터" in line and "까지" in line)):
            a, b = dates[0], dates[1]
            seg = {"kind": "range", "start": _ym(a[1], a[2]), "end": _ym(b[1], b[2])}
        elif has_now:
            a = dates[0]
            seg = {"kind": "open", "start": _ym(a[1], a[2]), "end": None}
        elif "이전" in line:
            a = dates[0]
            seg = {"kind": "before", "start": None, "end": _ym(a[1], a[2])}
        elif "부터" in line and "업데이트" in line:
            a = dates[0]
            seg = {"kind": "accumulating", "start": _ym(a[1], a[2]), "end": None}
        elif "기준" in line:
            a = dates[0]
            seg = {"kind": "snapshot", "start": _ym(a[1], a[2]), "end": _ym(a[1], a[2])}
        if seg is None:
            continue
        a = dates[0]
        seg["day_precision"] = a[3] is not None
        seg["label"] = label
        seg["raw"] = line
        segments.append(seg)
        raws.append(line)
    if not segments:
        return None
    kinds = [s["kind"] for s in segments]
    starts = [s["start"] for s in segments if s["start"]]
    last = segments[-1]
    if "open" in kinds:
        kind = "open"
    elif "accumulating" in kinds:
        kind = "accumulating"
    elif "range" in kinds:
        kind = "range"
    else:
        kind = last["kind"]
    if kind in ("open", "accumulating"):
        end = None
    else:
        ends = [s["end"] for s in segments if s["end"]]
        end = max(ends) if ends else None
    return {
        "start": min(starts) if starts else None,
        "end": end,
        "open_ended": kind in ("open", "accumulating"),
        "kind": kind,
        "raw": " / ".join(raws),
        "segments": [{"start": s["start"], "end": s["end"], "kind": s["kind"],
                      "label": s["label"], "raw": s["raw"]} for s in segments],
    }


# ---------------------------------------------------------------- 참고표

def _is_ref_title(v):
    return clean(v).startswith("[참고")


def parse_ref_tables(cells, first_row):
    """컬럼 목록 아래의 '[참고…' 표들을 읽는다. 옆으로 나란한 표와 아래 줄의 표를 모두 받는다."""
    titles = sorted((r, c) for (r, c), v in cells.items() if r >= first_row and _is_ref_title(v))
    if not titles:
        return []
    max_row = max(r for (r, c) in cells)
    max_col = max(c for (r, c) in cells)
    tables = []
    for (tr, tc) in titles:
        same_row_next = [c for (r, c) in titles if r == tr and c > tc]
        cap = (min(same_row_next) - 1) if same_row_next else max_col
        header_row = tr + 1

        def contiguous(row, start, cap_col):
            end = start - 1
            c = start
            while c <= cap_col and (row, c) in cells and not _is_ref_title(cells[(row, c)]):
                end = c
                c += 1
            return end

        end_h = contiguous(header_row, tc, cap)
        end_v = contiguous(header_row + 1, tc, cap)
        end_col = max(end_h, end_v, tc)
        header = [clean(cells.get((header_row, c), "")) for c in range(tc, end_col + 1)]
        rows = []
        r = header_row + 1
        while r <= max_row:
            vals = [cells.get((r, c)) for c in range(tc, end_col + 1)]
            if all(v is None for v in vals):
                break
            if any(v is not None and _is_ref_title(v) for v in vals):
                break
            rows.append([clean(v) for v in vals])
            r += 1
        title = clean(cells[(tr, tc)])
        m = re.match(r"(\[참고\d*\])\s*(.*)$", title)
        tag = m.group(1) if m else title
        name = m.group(2).strip() if m else ""
        # '번호' 열 찾기: 머리글이 No 이거나, 머리글이 한 칸뿐인데 값이 두 칸이고 첫 칸이 1,2,3… 인 경우.
        no_col = None
        if header and header[0] == "No":
            no_col = 0
        elif rows and len(rows[0]) >= 2 and all(re.match(r"^\d+$", x[0] or "") for x in rows) \
                and [int(x[0]) for x in rows] == list(range(1, len(rows) + 1)) \
                and len([h for h in header if h]) < len(rows[0]):
            no_col = 0
        value_header = [h for i, h in enumerate(header) if i != no_col]
        value_rows = [[v for i, v in enumerate(row) if i != no_col] for row in rows]
        if no_col is not None and header and header[0] != "No":
            # 2-01 형: 머리글 칸에 표 이름만 있다.
            if not name:
                name = header[0]
            value_header = [header[0]] + [h for h in header[1:] if h][1:]
        if not name:
            name = " · ".join(h for h in value_header if h) or tag
        tables.append({
            "tag": tag,
            "name": name,
            "title": title,
            "cell": "%s%d" % (col_letter(tc), tr),
            "header": value_header,
            "rows": value_rows,
            "row_count": len(value_rows),
        })
    return tables


def refs_from_tables(tables):
    """HANDOFF 의 refs 모양: {표 이름: 값 목록}. 값 열이 하나면 문자열 목록, 여럿이면 목록의 목록.

    값은 refs 에만 싣고, ref_tables 에는 표의 자리(칸 주소)·머리글·행 수와 refs 의 키만 남긴다.
    """
    refs = {}
    metas = []
    for t in tables:
        key = t["name"]
        if key in refs:
            key = "%s %s" % (t["name"], t["tag"])
        width = max([len(r) for r in t["rows"]] + [0])
        used = [i for i in range(width) if any((r[i] if i < len(r) else "") for r in t["rows"])]
        flat = len(used) <= 1
        if flat:
            idx = used[0] if used else 0
            refs[key] = [(r[idx] if idx < len(r) else "") for r in t["rows"]]
        else:
            refs[key] = [list(r) for r in t["rows"]]
        metas.append({"key": key, "tag": t["tag"], "name": t["name"], "title": t["title"],
                      "cell": t["cell"], "header": t["header"], "row_count": t["row_count"],
                      "flat": flat})
    return refs, metas


# ---------------------------------------------------------------- 시트 해석

def parse_list_sheet(cells):
    version, date = "", ""
    for (r, c), v in sorted(cells.items()):
        m = re.search(r"\((v\d+)\s*,\s*(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})\)", v)
        if m:
            version = m.group(1)
            date = "%04d-%02d-%02d" % (int(m.group(2)), int(m.group(3)), int(m.group(4)))
            break
    head = None
    for (r, c), v in sorted(cells.items()):
        if c == 1 and clean(v) == "No":
            head = r
            break
    entries = []
    category = ""
    declared = {}
    if head is not None:
        r = head + 1
        max_row = max(rr for (rr, cc) in cells)
        while r <= max_row:
            no = clean(cells.get((r, 1), ""))
            if not no:
                r += 1
                continue
            cat = cells.get((r, 2))
            if cat is not None:
                m = re.match(r"\s*(\S+)\s*\(?(\d+)?\)?", cat.replace("\n", " "))
                category = m.group(1) if m else clean(cat)
                if m and m.group(2):
                    declared[category] = int(m.group(2))
            entries.append({
                "no": no,
                "category": category,
                "name": clean(cells.get((r, 3), "")),
                "export": clean(cells.get((r, 4), "")),
                "provider": clean(cells.get((r, 5), "")),
                "row": r,
            })
            r += 1
    return version, date, entries, declared


def parse_data_sheet(sheet_name, cells):
    m = re.match(r"\s*(\d+-\d+)\s+(.*)$", sheet_name)
    no = m.group(1)
    r_cat = _find_label_row(cells, "카테고리")
    r_name = _find_label_row(cells, "데이터명(한글)")
    r_desc = _find_label_row(cells, "설명")
    r_reg = _find_label_row(cells, "등록일자")
    r_en = _find_label_row(cells, "데이터명(영문)")
    r_head = None
    for (r, c), v in sorted(cells.items()):
        if c == 1 and clean(v) == "No" and (r_reg is None or r > r_reg):
            r_head = r
            break
    if r_head is None:
        raise ValueError("컬럼 표 머리글(No)을 찾지 못함: %s" % sheet_name)
    description = cells.get((r_desc, 3), "") if r_desc else ""
    desc_lines = [clean(x).lstrip("-").strip() for x in description.split("\n")]
    desc_lines = [x for x in desc_lines if x]
    export_raw = _right_of(cells, r_reg, "반출") if r_reg else ""
    provider = _right_of(cells, r_reg, "제공처") if r_reg else ""
    if "불가능" in export_raw:
        export = "불가능"
    elif "가능" in export_raw:
        export = "가능"
    else:
        export = export_raw
    export_note = ""
    for line in desc_lines:
        if "반출" in line:
            export_note = line
            break
    # 컬럼 목록: 머리글 아래로 A열 번호가 이어지는 동안.
    columns = []
    r = r_head + 1
    while True:
        a = clean(cells.get((r, 1), ""))
        if not re.match(r"^\d+$", a):
            break
        en_raw = cells.get((r, 2), "")
        en = clean(en_raw)
        note = clean(cells.get((r, 7), "")) or clean(cells.get((r, 6), ""))
        columns.append({
            "n": int(a),
            "en": en,
            "ko": clean(cells.get((r, 3), "")),
            "type": clean(cells.get((r, 4), "")),
            "example": clean(cells.get((r, 5), "")),
            "note": note,
            "partition": en in PARTITION_COLUMNS,
        })
        r += 1
    last_col_row = r - 1
    # 표 밖 주석(예: 1-18 의 k값 주석)
    footnotes = []
    max_row = max(rr for (rr, cc) in cells)
    rr = last_col_row + 1
    while rr <= max_row:
        a = cells.get((rr, 1))
        if a is not None and _is_ref_title(a):
            break
        if any(_is_ref_title(v) for (r2, c2), v in cells.items() if r2 == rr):
            break
        if a is not None and clean(a):
            footnotes.append(clean(a))
        rr += 1
    tables = parse_ref_tables(cells, last_col_row + 1)
    refs, ref_metas = refs_from_tables(tables)
    head_b = clean(cells.get((r_head, 2), ""))
    registered_raw = clean(cells.get((r_reg, 3), "")) if r_reg else ""
    return {
        "no": no,
        "name": clean(cells.get((r_name, 3), "")) if r_name else clean(m.group(2)),
        "name_en": clean(cells.get((r_en, 3), "")) if r_en else "",
        "sheet": clean(sheet_name),
        "category": clean(cells.get((r_cat, 3), "")) if r_cat else "",
        "export": export,
        "export_raw": export_raw,
        "export_note": export_note,
        "provider": provider,
        "registered": excel_serial_to_date(registered_raw),
        "period": parse_period(description),
        "description": "\n".join(desc_lines),
        "columns": columns,
        "refs": refs,
        "ref_tables": ref_metas,
        "footnotes": footnotes,
        "layout": {"header_row": r_head, "header_label": head_b, "shifted": r_head != 9},
    }


def parse_ref_sheet(sheet_name, cells):
    """(참고표1) 업종코드표, (참고표2) 메뉴코드표: A1 제목, 3행 머리글, 4행부터 값."""
    head_row = None
    for r in range(2, 8):
        if (r, 1) in cells and re.match(r"^[a-z_]+$", clean(cells[(r, 1)])):
            head_row = r
            break
    if head_row is None:
        raise ValueError("참고표 머리글을 찾지 못함: %s" % sheet_name)
    width = 0
    while (head_row, width + 1) in cells:
        width += 1
    header = [clean(cells[(head_row, c)]) for c in range(1, width + 1)]
    rows = []
    r = head_row + 1
    while (r, 1) in cells:
        rows.append([clean(cells.get((r, c), "")) for c in range(1, width + 1)])
        r += 1
    distinct = {}
    for i, h in enumerate(header):
        if h.endswith("_cd"):
            distinct[h] = len(set(row[i] for row in rows))
    out = {
        "name": clean(sheet_name),
        "header": header,
        "row_count": len(rows),
        "distinct": distinct,
        "rows": rows if len(rows) <= REF_SHEET_MAX_ROWS else None,
    }
    if out["rows"] is None:
        out["rows_note"] = "행이 많아 값은 싣지 않았다(행 수와 단계별 개수만)."
    return out


def build(xlsx_path=XLSX_PATH):
    sheets = read_workbook(xlsx_path)
    version, date, list_entries, declared = "", "", [], {}
    datasets = []
    ref_sheets = []
    problems = []
    for name, cells in sheets:
        nm = clean(name)
        if nm == "목록":
            version, date, list_entries, declared = parse_list_sheet(cells)
        elif re.match(r"^\d+-\d+\s", nm):
            try:
                datasets.append(parse_data_sheet(name, cells))
            except Exception as e:  # 한 시트가 어긋나도 나머지는 변환한다.
                problems.append("%s: %s" % (nm, e))
        elif nm.startswith("(참고표"):
            ref_sheets.append(parse_ref_sheet(name, cells))
        else:
            problems.append("분류하지 못한 시트: %s" % nm)

    by_no = dict((e["no"], e) for e in list_entries if e["no"] != "-")
    list_ref = [e for e in list_entries if e["no"] == "-"]
    mismatches = []
    for d in datasets:
        e = by_no.get(d["no"])
        if not e:
            mismatches.append({"no": d["no"], "field": "목록", "list": None, "sheet": d["name"]})
            continue
        d["category"] = d["category"] or e["category"]
        d["provider_in_list"] = e["provider"]
        d["export_in_list"] = e["export"]
        if e["provider"] != d["provider"]:
            mismatches.append({"no": d["no"], "field": "제공처", "list": e["provider"], "sheet": d["provider"]})
        if re.sub(r"\s+", "", e["name"]) != re.sub(r"\s+", "", d["name"]):
            mismatches.append({"no": d["no"], "field": "데이터명", "list": e["name"], "sheet": d["name"]})
        if e["export"] != d["export"]:
            mismatches.append({"no": d["no"], "field": "반출", "list": e["export"], "sheet": d["export"]})
    for rs in ref_sheets:
        for e in list_ref:
            if re.sub(r"\s+", "", e["name"]) == re.sub(r"\s+", "", rs["name"]):
                rs["export"] = e["export"]
                rs["provider"] = e["provider"]

    # 헤더(파티션 뺀 영문 컬럼명 순서)가 완전히 같은 데이터 묶음.
    groups = {}
    for d in datasets:
        key = tuple(c["en"] for c in d["columns"] if not c["partition"])
        groups.setdefault(key, []).append(d["no"])
    dup_headers = sorted(v for v in groups.values() if len(v) > 1)

    n_cols = sum(len(d["columns"]) for d in datasets)
    n_part = sum(1 for d in datasets for c in d["columns"] if c["partition"])
    period_kinds = {}
    for d in datasets:
        k = d["period"]["kind"] if d["period"] else "none"
        period_kinds[k] = period_kinds.get(k, 0) + 1
    per_category = {}
    for d in datasets:
        per_category[d["category"]] = per_category.get(d["category"], 0) + 1
    export_counts = {}
    for d in datasets:
        export_counts[d["export"]] = export_counts.get(d["export"], 0) + 1

    spec = {
        "version": version,
        "date": date,
        "source_file": XLSX_NAME,
        "counts": {
            "sheets": len(sheets),
            "datasets": len(datasets),
            "ref_tables": len(ref_sheets),
            "columns": n_cols,
            "partition_columns": n_part,
            "columns_without_partition": n_cols - n_part,
            "per_category": per_category,
            "declared_in_list": declared,
            "export": export_counts,
            "period_kinds": period_kinds,
            "period_range_checkable": sum(v for k, v in period_kinds.items()
                                          if k in ("range", "open", "accumulating")),
        },
        "datasets": datasets,
        "ref_sheets": ref_sheets,
        "list_mismatches": mismatches,
        "duplicate_headers": dup_headers,
        "problems": problems,
    }
    return spec


def dumps(spec):
    return json.dumps(spec, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def summary(spec):
    c = spec["counts"]
    lines = [
        "명세서 %s(%s) 기준 데이터 %d종, 참고표 %d종, 컬럼 %d개(파티션 컬럼 %d개 포함)" % (
            spec["version"], spec["date"], c["datasets"], c["ref_tables"], c["columns"], c["partition_columns"]),
        "시트 %d개, 카테고리별 %s" % (c["sheets"], ", ".join("%s %d" % kv for kv in c["per_category"].items())),
        "반출 %s" % ", ".join("%s %d" % kv for kv in sorted(c["export"].items())),
        "기간 표기 %s (달 범위를 판정할 수 있는 데이터 %d종)" % (
            ", ".join("%s %d" % kv for kv in sorted(c["period_kinds"].items())), c["period_range_checkable"]),
        "헤더가 같은 묶음 %d개: %s" % (len(spec["duplicate_headers"]),
                                 " / ".join("·".join(g) for g in spec["duplicate_headers"])),
        "목록 시트와 다른 곳 %d건" % len(spec["list_mismatches"]),
    ]
    if spec["problems"]:
        lines.append("문제 %d건: %s" % (len(spec["problems"]), "; ".join(spec["problems"])))
    return "\n".join(lines)


def main(argv):
    if not os.path.exists(XLSX_PATH):
        print("명세서 파일이 없습니다: %s" % XLSX_PATH)
        print("조치: 데이터명세서_kr.xlsx 를 data/spec/ 폴더에 넣고 다시 실행하세요.")
        return 2
    spec = build(XLSX_PATH)
    text = dumps(spec)
    if "--check" in argv:
        old = ""
        if os.path.exists(OUT_PATH):
            with open(OUT_PATH, "r", encoding="utf-8") as f:
                old = f.read()
        same = old == text
        print(summary(spec))
        print("기존 spec.json 과 %s" % ("같음" if same else "다름"))
        return 0 if same else 1
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(summary(spec))
    print("저장: %s (%d바이트)" % (os.path.relpath(OUT_PATH, ROOT), len(text.encode("utf-8"))))
    return 1 if spec["problems"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
