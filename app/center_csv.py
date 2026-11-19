# -*- coding: utf-8 -*-
"""센터에서 내려받은 CSV 를 읽어 빈칸을 채운다(재분석). 이 단계에는 LLM 호출이 없다.

- 파일 이름(Results_날짜_시간.csv)으로는 어떤 데이터인지 알 수 없으므로 헤더를 명세서 71종과 대조해 인식한다.
- 파티션 컬럼(pt_year, pt_month)은 있어도 없어도 받는다. BOM, cp949, 컬럼명 앞뒤 공백, 전부 따옴표, 결측 NA 를 흡수한다.
- 한 줄씩 읽어 집계한다(파일을 통째로 메모리에 올리지 않는다).
- 비중·순위(응용집계)를 먼저 보이고, 건수·금액(단순집계)은 10 미만을 가리며 합계로 거꾸로 셈하지 못하게 한다.
- 지원: 카드 v02 계열 3종(1-08 읍면동, 1-12 요일별, 1-14 시간대). 그 밖의 데이터는 인식만 하고 표는 내지 않는다.

analyze() 는 예외를 던지지 않는다. 문제가 있으면 결과의 "error" 에 {"message", "action"} 을 담는다.
"""
import csv
import hashlib
import os
import re

from . import catalog as catalog_mod
from . import config
from .korean import josa
from . import masking
from . import public_data as public_mod
from .catalog import biz_key

PARTITIONS = ("pt_year", "pt_month")
NA_VALUES = ("", "NA", "N/A", "NULL", "NAN", "NONE")
NA_LABEL = "NA(알 수 없음)"
DIM_BY_DATASET = {"1-14": "time", "1-12": "week", "1-08": None}
MIN_SIMILARITY = 0.6


def _err(message, action):
    return {"message": message, "action": action}


def _empty_result():
    return {"recognized": {"dataset_no": None, "dataset_name": None, "ask": []}, "diagnostics": [], "rows": 0,
            "month": None, "tables": [], "reconcile": {"available": False, "public_value": None, "center_sum": None,
                                                        "diff_pct": None, "note": ""},
            "masked_total": 0, "recoverable": 0, "small_visible": 0, "sent_to_llm": 0, "virtual": False,
            "error": None, "scope": None}


def norm_col(name):
    """컬럼명 정리: BOM·따옴표·앞뒤 공백을 지우고 소문자로."""
    return (name or "").replace("﻿", "").strip().strip('"').strip().lower()


def clean_value(v):
    v = (v or "").strip()
    if v.upper() in NA_VALUES:
        return None
    return v


def to_int(v):
    """'4', '4.0', '1E+06', '1,000' → 정수. 결측이면 None, 숫자가 아니면 ValueError."""
    v = clean_value(v)
    if v is None:
        return None
    v = v.replace(",", "")
    try:
        return int(v)
    except ValueError:
        return int(round(float(v)))


def sniff_bytes(path):
    """CSV 가 아닌 파일을 가려낸다. 문제가 없으면 None."""
    size = os.path.getsize(path)
    if size == 0:
        return _err("빈 파일입니다(0바이트).", "센터 화면에서 다시 내려받은 뒤 그 파일을 넣어 주세요.")
    with open(path, "rb") as f:
        head = f.read(4096)
    if head.startswith(b"PK\x03\x04"):
        return _err("CSV 가 아니라 엑셀(xlsx)이나 압축 파일로 보입니다.", "센터에서 내려받은 CSV 파일(Results_날짜_시간.csv)을 그대로 넣어 주세요. 엑셀로 열었다면 'CSV UTF-8'로 저장해 주세요.")
    if head.startswith(b"%PDF"):
        return _err("CSV 가 아니라 PDF 파일입니다.", "센터에서 내려받은 CSV 파일을 넣어 주세요.")
    if b"\x00" in head:
        return _err("CSV 가 아닌 파일입니다(글자가 아닌 내용이 들어 있습니다).", "센터에서 내려받은 CSV 파일(Results_날짜_시간.csv)을 넣어 주세요.")
    return None


def detect_encoding(path):
    """utf-8-sig 로 끝까지 읽히면 그것, 아니면 cp949. 둘 다 안 되면 None."""
    for enc in config.READ_ENCODINGS:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                while f.read(1 << 16):
                    pass
            return enc
        except UnicodeDecodeError:
            continue
    return None


def recognize_header(header, cat=None):
    """헤더 한 줄을 명세서와 대조한다.

    돌려주는 값: {"matches": [번호…], "exact": bool, "columns": [정리한 컬럼명], "has_partition": bool,
                "extra": [명세서에 없는 컬럼], "absent": [파일에 없는 컬럼], "similarity": 0~1}
    """
    cat = cat or catalog_mod.get()
    cols = [norm_col(h) for h in header]
    has_part = any(c in PARTITIONS for c in cols)
    key = frozenset(c for c in cols if c and c not in PARTITIONS)
    idx = cat.header_index()
    if key in idx:
        return {"matches": list(idx[key]), "exact": True, "columns": cols, "has_partition": has_part,
                "extra": [], "absent": [], "similarity": 1.0}
    best, best_sim = [], 0.0
    for k, nos in idx.items():
        union = len(k | key)
        sim = (len(k & key) / float(union)) if union else 0.0
        if sim > best_sim + 1e-9:
            best, best_sim = list(nos), sim
        elif abs(sim - best_sim) <= 1e-9 and sim > 0:
            best.extend(nos)
    extra, absent = [], []
    if best:
        spec_cols = set(c["en"] for c in cat.columns(best[0]))
        extra = sorted(key - spec_cols)
        absent = sorted(spec_cols - key)
    return {"matches": best, "exact": False, "columns": cols, "has_partition": has_part,
            "extra": extra, "absent": absent, "similarity": round(best_sim, 3)}


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 16)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def is_virtual(path):
    """동봉한 가상 CSV 인지(생성 스크립트가 적어 둔 해시와 같은지)."""
    try:
        man = catalog_mod.read_json(config.SAMPLE_MANIFEST)
    except (OSError, ValueError):
        return False
    digest = file_sha256(path)
    return any(f.get("sha256") == digest for f in man.get("files", []))


class _Acc(object):
    """한 범위(scope)의 집계."""

    def __init__(self, dim):
        self.dim = dim
        self.rows = 0
        self.total = [0, 0, 0]            # 이용자수, 이용건수, 이용금액
        self.by = {"dim": {}, "user_type": {}, "age": {}, "sex": {}}
        self.cross = {"age": {}, "user_type": {}}   # (dim 값, 연령대 또는 이용자구분) → [users, cnt, amt]

    def add(self, rec, nums):
        self.rows += 1
        for i in range(3):
            self.total[i] += nums[i]
        keys = {"user_type": rec.get("user_type"), "age": rec.get("age"), "sex": rec.get("sex")}
        if self.dim:
            keys["dim"] = rec.get(self.dim)
        for name, k in keys.items():
            a = self.by[name].setdefault(k, [0, 0, 0])
            for i in range(3):
                a[i] += nums[i]
        if self.dim:
            for other in ("age", "user_type"):
                a = self.cross[other].setdefault((rec.get(self.dim), rec.get(other)), [0, 0, 0])
                for i in range(3):
                    a[i] += nums[i]


def _scan(path, enc, dim, want_biz, want_bjd, want_district):
    """한 줄씩 읽어 집계한다. want_* 가 None 이면 그 조건은 걸지 않는다."""
    out = {"rows": 0, "bad_rows": 0, "bad_numbers": 0, "na_numbers": 0, "months": {}, "biz": {}, "bjd_all": set(),
           "focus": _Acc(dim), "biz_scope": {"total": [0, 0, 0], "by_bjd": {}}, "header": []}
    with open(path, "r", encoding=enc, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None) or []
        cols = [norm_col(h) for h in header]
        out["header"] = cols
        pos = dict((c, i) for i, c in enumerate(cols) if c)
        n = len(cols)
        want = ("base_year_month", "dstrct_se", "bjd", "biz_cd", "biz_nm", "user_type", "age", "sex", "time", "week")
        num_cols = ("user_cnt", "use_cnt", "use_amt")
        for row in reader:
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            if len(row) != n:
                out["bad_rows"] += 1
                continue
            out["rows"] += 1
            rec = {}
            for c in want:
                if c in pos:
                    v = clean_value(row[pos[c]])
                    rec[c] = v if v is not None else NA_LABEL
            nums = [0, 0, 0]
            for i, c in enumerate(num_cols):
                if c in pos:
                    try:
                        v = to_int(row[pos[c]])
                    except ValueError:
                        out["bad_numbers"] += 1
                        v = None
                    else:
                        if v is None:
                            out["na_numbers"] += 1
                    nums[i] = v or 0
            ym = re.sub(r"\D", "", rec.get("base_year_month", "").split(".")[0])[:6]
            if ym:
                out["months"][ym] = out["months"].get(ym, 0) + 1
            b = out["biz"].setdefault((rec.get("biz_cd"), rec.get("biz_nm")), [0, 0, 0])
            for i in range(3):
                b[i] += nums[i]
            if "bjd" in rec:
                out["bjd_all"].add(rec["bjd"])
            if want_biz and not (rec.get("biz_cd") == want_biz[0] or biz_key(rec.get("biz_nm") or "") == biz_key(want_biz[1])):
                continue
            bs = out["biz_scope"]
            for i in range(3):
                bs["total"][i] += nums[i]
            if "bjd" in rec:
                a = bs["by_bjd"].setdefault(rec["bjd"], [0, 0, 0])
                for i in range(3):
                    a[i] += nums[i]
            if want_bjd and rec.get("bjd") != want_bjd:
                continue
            if want_district and rec.get("dstrct_se") not in want_district:
                continue
            out["focus"].add(rec, nums)
    return out


MASK_NOTE = ("'10 미만'은 그 칸의 값이 10 미만이라는 뜻이다. '가림'은 값은 10 이상이지만 이용자수가 10 미만이거나, "
             "합계에서 빼면 가린 칸을 알 수 있어 함께 가린 칸이다. 합계도 10 미만이면 숨긴다.")


def _basis(v):
    """가리기 기준 값: 화면에 나가는 결과값(이용건수·이용금액)과 이용자수 가운데 가장 작은 값.

    이용자수 합이 0 이면(컬럼이 없거나 전부 결측) 이용자수는 빼고 본다.
    """
    vals = [v[1], v[2]]
    if v[0]:
        vals.append(v[0])
    return min(vals)


def _basis_cnt(v):
    """건수만 보이는 표(행렬)의 기준 값: 이용자수 합과 이용건수 합 가운데 작은 쪽."""
    return min(v[0], v[1]) if v[0] else v[1]


def _mt(value, flag):
    """가린 칸의 글자. 보일 값 자체가 10 미만이면 '10 미만', 아니면 '가림'."""
    if not flag:
        return "-" if value is None else value
    if value is not None and value < config.MASK_THRESHOLD:
        return config.MASK_TEXT
    return config.MASK_TEXT_SECONDARY


def _pct(part, total):
    return round(part * 100.0 / total, 1) if total else 0.0


def _order(keys, canon):
    known = [k for k in canon if k in keys]
    rest = sorted(k for k in keys if k not in canon and k != NA_LABEL)
    tail = [NA_LABEL] if NA_LABEL in keys else []
    return known + rest + tail


class _Vec(object):
    """한 기준(시간대·요일·이용자구분·연령대)으로 나눈 값과 그 가리기 결정.

    같은 결정을 비중 표, 건수·금액 표, 행렬 표의 줄·열 합계가 함께 쓴다(한 표에서 가린 값이 다른 표에서 드러나지 않게).
    """

    def __init__(self, name, groups, canon, total):
        self.name = name
        self.groups = groups
        self.total = total
        self.keys = _order(list(groups.keys()), canon)
        m = masking.mask_vector([_basis(groups[k]) for k in self.keys], total_basis=_basis(total))
        self.flag = dict(zip(self.keys, m["mask"]))
        self.hide_total = m["hide_total"]

    def register(self, led):
        ids = [(self.name, k) for k in self.keys]
        for k in self.keys:
            if self.flag[k]:
                led["hidden"].add((self.name, k))
            elif _basis(self.groups[k]) < config.MASK_THRESHOLD:
                led["small"] += 1
        if self.hide_total:
            led["hidden"].add(("G",))
        elif _basis(self.total) < config.MASK_THRESHOLD:
            led["small"] += 1
        led["rel"].append((("G",), ids))


def _share_table(title, label, vec, note=None):
    """응용집계 표: 비중과 순위. 기준 값이 10 미만인 칸은 합계로 거꾸로 셈할 수 있어 가린다.

    결측(NA)은 순위에서 뺀다. 표 위에 보일 한 줄(headline)은 가리지 않은 칸만으로 코드가 만든다.
    """
    keys, groups = vec.keys, vec.groups
    tot_cnt = sum(groups[k][1] for k in keys)
    tot_amt = sum(groups[k][2] for k in keys)
    ranked = sorted((k for k in keys if k != NA_LABEL), key=lambda k: -groups[k][1])
    rows = []
    for k in keys:
        flag = vec.flag[k]
        cnt, amt = groups[k][1], groups[k][2]
        if k == NA_LABEL:
            rank = "-" if not flag else _mt(cnt, flag)
        else:
            rank = ranked.index(k) + 1 if not flag else _mt(cnt, flag)
        rows.append([k,
                     _pct(cnt, tot_cnt) if not flag else _mt(cnt, flag),
                     _pct(amt, tot_amt) if not flag else _mt(amt, flag),
                     rank])
    masked = sum(1 for k in keys if vec.flag[k]) * 3
    t = {"title": title, "kind": "응용집계", "columns": [label, "이용건수 비중(%)", "이용금액 비중(%)", "순위(건수)"],
         "rows": rows, "masked_cells": masked}
    top = [k for k in ranked[:2] if not vec.flag[k]]
    if top and top[0] == ranked[0] and len(ranked) > 1:
        def shown(k):
            return (k + "대") if (label == "연령대" and k.isdigit()) else k
        t["headline"] = "이용건수 기준 " + ", ".join(
            "%d위 %s %s%%" % (i + 1, shown(k), _pct(groups[k][1], tot_cnt)) for i, k in enumerate(top))
    notes = [note] if note else []
    if NA_LABEL in keys:
        notes.append("%s%s %s 값이 기록되지 않은 줄이다. 순위에서 뺐다(명세서의 값 목록에 NA 가 있는 컬럼이다)." % (NA_LABEL, josa(NA_LABEL, "은"), label))
    if notes:
        t["note"] = " ".join(notes)
    return t


def _count_table(title, label, vec):
    """단순집계 표: 건수와 금액. 10 미만은 가리고, 한 칸만 가려지면 다음으로 작은 칸도 가린다. 합계도 10 미만이면 숨긴다."""
    keys, groups = vec.keys, vec.groups
    rows = []
    for k in keys:
        flag = vec.flag[k]
        rows.append([k, _mt(groups[k][1], flag), _mt(groups[k][2], flag)])
    if vec.hide_total:
        rows.append(["합계", "숨김", "숨김"])
    else:
        rows.append(["합계", vec.total[1], vec.total[2]])
    masked = sum(1 for k in keys if vec.flag[k]) * 2 + (2 if vec.hide_total else 0)
    return {"title": title, "kind": "단순집계", "columns": [label, "이용건수", "이용금액(원)"], "rows": rows,
            "masked_cells": masked}


def _matrix_table(title, row_label, cross, row_vec, col_vec, led, tag):
    """단순집계 행렬(행 × 열 이용건수). 줄·열 합계는 한 줄짜리 표의 가리기 결정을 그대로 따른다.

    줄이나 열이 하나뿐이면 한 줄짜리 표와 같은 값이라 내지 않는다(None).
    """
    rks, cks = row_vec.keys, col_vec.keys
    if len(rks) < 2 or len(cks) < 2:
        return None
    basis = [[_basis_cnt(cross.get((r, c), [0, 0, 0])) for c in cks] for r in rks]
    vals = [[cross.get((r, c), [0, 0, 0])[1] for c in cks] for r in rks]
    row_hidden = [bool(row_vec.flag[r]) for r in rks]
    col_hidden = [bool(col_vec.flag[c]) for c in cks]
    m = masking.mask_matrix(basis, row_hidden=row_hidden, col_hidden=col_hidden)
    if m["hide_row_total"] != row_hidden or m["hide_col_total"] != col_hidden:
        return None     # 한 줄짜리 표와 어긋나는 가리기가 필요한 표는 내지 않는다
    hide_grand = row_vec.hide_total or col_vec.hide_total
    rows = []
    for i, r in enumerate(rks):
        line = [r] + [_mt(vals[i][j], m["mask"][i][j]) for j in range(len(cks))]
        line.append("숨김" if row_hidden[i] else sum(vals[i]))
        rows.append(line)
    foot = ["합계"]
    for j in range(len(cks)):
        foot.append("숨김" if col_hidden[j] else sum(vals[i][j] for i in range(len(rks))))
    foot.append("숨김" if hide_grand else sum(sum(v) for v in vals))
    rows.append(foot)
    for i, r in enumerate(rks):
        ids = []
        for j, c in enumerate(cks):
            ids.append((tag, r, c))
            if m["mask"][i][j]:
                led["hidden"].add((tag, r, c))
        led["rel"].append(((row_vec.name, r), ids))
    for j, c in enumerate(cks):
        led["rel"].append(((col_vec.name, c), [(tag, r, c) for r in rks]))
    masked = (m["primary"] + m["secondary"] + sum(1 for x in row_hidden if x) + sum(1 for x in col_hidden if x)
              + (1 if hide_grand else 0))
    return {"title": title, "kind": "단순집계", "columns": [row_label] + cks + ["합계"], "rows": rows,
            "masked_cells": masked, "masked_primary": m["primary"], "masked_secondary": m["secondary"],
            "recoverable": masking.recoverable_cells(m["mask"], row_hidden, col_hidden, hide_grand),
            "note": "줄 합계와 열 합계는 위의 한 줄짜리 표에서 가린 값이면 여기서도 숨기고, 그 줄(열)의 칸을 더해 알 수 없게 가장 작은 칸을 함께 가렸다. 값이 0 인 칸도 '10 미만'으로 적는다."}


def _canon(cat, no, col):
    v = cat.column_values(no, col)
    return v["values"] if v else []


def analyze(path, filename=None, request=None, dataset_no=None):
    """내려받은 CSV 한 개를 분석한다.

    path:        서버가 임시로 저장한 파일 경로
    filename:    올린 파일 이름(진단 문구용)
    request:     /api/plan 응답의 need(없으면 파일 전체로 집계). region_detail.bjd, biz_detail 을 조건으로 쓴다.
    dataset_no:  헤더가 같은 데이터가 여럿일 때 사용자가 고른 번호
    돌려주는 값: /api/center-csv 응답 모양 + "error"({"message","action"} 또는 None) + "scope"
    """
    cat = catalog_mod.get()
    res = _empty_result()
    diag = res["diagnostics"]

    def fail(message, action):
        res["error"] = _err(message, action)
        diag.append({"level": "error", "detail": "%s 조치: %s" % (message, action)})
        return res

    try:
        if not os.path.isfile(path):
            return fail("파일을 찾지 못했습니다.", "파일을 다시 넣어 주세요.")
        bad = sniff_bytes(path)
        if bad:
            return fail(bad["message"], bad["action"])
        enc = detect_encoding(path)
        if enc is None:
            return fail("파일의 글자 인코딩을 알아보지 못했습니다(UTF-8 도 CP949 도 아닙니다).",
                        "센터에서 내려받은 CSV 를 다른 프로그램으로 다시 저장하지 말고 그대로 넣어 주세요.")
        with open(path, "r", encoding=enc, newline="") as f:
            try:
                header = next(csv.reader(f))
            except StopIteration:
                header = []
            except csv.Error:
                return fail("CSV 로 읽을 수 없는 파일입니다.", "센터에서 내려받은 CSV 파일(Results_날짜_시간.csv)을 넣어 주세요.")
        if not header or not any(h.strip() for h in header):
            return fail("첫 줄(컬럼명 줄)이 비어 있습니다.", "센터에서 다시 내려받은 CSV 를 넣어 주세요.")
        if enc != "utf-8-sig":
            diag.append({"level": "info", "detail": "UTF-8 이 아니라 CP949 로 읽었습니다(엑셀에서 다시 저장한 파일로 보입니다)."})
        if header[0].startswith("﻿"):
            diag.append({"level": "info", "detail": "파일 앞에 BOM 이 있어 지우고 읽었습니다."})
        if len(header) == 1 and (";" in header[0] or "\t" in header[0]):
            return fail("쉼표로 나뉜 CSV 가 아닙니다(탭이나 세미콜론으로 나뉘어 있습니다).", "센터에서 내려받은 CSV 를 그대로 넣어 주세요.")
        if re.match(r"^\d{6,8}$", norm_col(header[0])):
            return fail("첫 줄이 컬럼명이 아니라 값입니다(헤더 줄이 없습니다).",
                        "첫 줄에 명세서의 영문 컬럼명이 있는 CSV 를 넣어 주세요. 센터에서 내려받은 파일은 첫 줄이 컬럼명입니다.")
        rec = recognize_header(header, cat)
        if any(h != h.strip() for h in header):
            diag.append({"level": "info", "detail": "컬럼명 앞뒤의 공백을 지우고 읽었습니다."})
        diag.append({"level": "info", "detail": "파티션 컬럼(pt_year, pt_month)이 %s." % (
            "들어 있어 빼고 대조했습니다" if rec["has_partition"] else "없는 파일입니다(게시판 40번 정정 안내와 같음)")})
        matches = rec["matches"]
        if not matches or rec["similarity"] < MIN_SIMILARITY:
            return fail("헤더가 명세서(%s)의 어느 데이터와도 맞지 않습니다. 읽은 컬럼: %s" % (cat.label(), ", ".join(c for c in rec["columns"] if c)[:300]),
                        "센터에서 내려받은 CSV 인지 확인해 주세요. 컬럼을 지우거나 이름을 바꾼 파일은 인식하지 못합니다.")
        if len(matches) > 1:
            if dataset_no in matches:
                matches = [dataset_no]
            else:
                res["recognized"]["ask"] = [{"dataset_no": no, "dataset_name": cat.name(no)} for no in matches]
                diag.append({"level": "ask", "detail": "헤더가 같은 데이터가 %d종입니다(%s). 어느 데이터인지 골라 주세요." % (
                    len(matches), ", ".join("%s %s" % (no, cat.name(no)) for no in matches))})
                return res
        no = matches[0]
        res["recognized"]["dataset_no"] = no
        res["recognized"]["dataset_name"] = cat.name(no)
        if rec["exact"]:
            diag.append({"level": "ok", "detail": "헤더로 '%s %s'%s 인식했습니다(파티션을 뺀 컬럼 %d개가 명세서와 같음)." % (
                no, cat.name(no), josa(cat.name(no), "을"), len(cat.columns(no)))})
        else:
            diag.append({"level": "warn", "detail": "명세서와 다른 컬럼이 있습니다. 가장 가까운 데이터는 '%s %s' 입니다(같은 정도 %.0f%%). 파일에만 있는 컬럼: %s / 파일에 없는 컬럼: %s" % (
                no, cat.name(no), rec["similarity"] * 100, ", ".join(rec["extra"]) or "없음", ", ".join(rec["absent"]) or "없음")})
        res["virtual"] = is_virtual(path)
        if res["virtual"]:
            diag.append({"level": "info", "detail": "동봉한 가상 데이터입니다(명세서대로 만든 파일이며 실제 센터 데이터가 아님)."})
        if no not in config.REANALYSIS_DATASETS:
            diag.append({"level": "warn", "detail": "이 데이터는 주문서까지만 지원합니다. 다시 돌리기는 카드 v02 계열 3종(%s)만 됩니다." % ", ".join(config.REANALYSIS_DATASETS)})
            res["rows"] = _count_rows(path, enc)
            return res
        cols = set(rec["columns"])
        must = [c for c in ("base_year_month", "use_cnt", "use_amt") if c not in cols]
        if must:
            return fail("집계에 꼭 필요한 컬럼이 파일에 없습니다: %s" % ", ".join(must),
                        "센터 화면에서 컬럼을 빼지 말고 다시 내려받아 주세요.")
        dim = DIM_BY_DATASET[no]
        if dim and dim not in cols:
            diag.append({"level": "warn", "detail": "%s 컬럼이 없어 그 표는 만들지 못했습니다." % dim})
            dim = None

        # 요청 조건
        want_biz = want_bjd = want_district = None
        if request:
            bd = request.get("biz_detail")
            if bd:
                want_biz = (bd["code"], bd["name"])
            rd = request.get("region_detail") or {}
            if rd.get("bjd") and "bjd" in cols:
                want_bjd = rd["bjd"]
                if rd["bjd"] in cat.bjd_ambiguous() and rd.get("district7") and "dstrct_se" in cols:
                    want_district = [rd["district7"]]
                elif rd["bjd"] in cat.bjd_ambiguous():
                    diag.append({"level": "warn", "detail": "'%s'%s 이름이 같은 법정동이 두 곳(%s)입니다. 권역을 정하지 않아 두 곳을 합쳐 집계했습니다. 한 곳만 보려면 주문서를 '제주시 %s' 또는 '서귀포시 %s'로 다시 만든 뒤 넣어 주세요." % (
                        rd["bjd"], josa(rd["bjd"], "은"), ", ".join(cat.bjd_ambiguous()[rd["bjd"]]), rd["bjd"], rd["bjd"])})
        sc = _scan(path, enc, dim, want_biz, want_bjd, want_district)
        if sc["rows"] == 0:
            return fail("컬럼명 줄만 있고 데이터 줄이 없습니다.", "조건이 너무 좁지 않은지 확인하고 센터에서 다시 내려받아 주세요.")
        relaxed = []
        if want_biz and sc["biz_scope"]["total"][1] == 0 and not sc["biz_scope"]["by_bjd"]:
            relaxed.append("요청한 업종 '%s'%s 파일에 없어 파일에 든 업종 전체로 집계했습니다." % (want_biz[1], josa(want_biz[1], "이")))
            want_biz = None
        if want_bjd and want_bjd not in sc["bjd_all"]:
            relaxed.append("요청한 법정동 '%s'%s 파일에 없어 파일에 든 지역 전체로 집계했습니다." % (want_bjd, josa(want_bjd, "이")))
            want_bjd = want_district = None
        if relaxed:
            for r in relaxed:
                diag.append({"level": "warn", "detail": r})
            sc = _scan(path, enc, dim, want_biz, want_bjd, want_district)
        res["rows"] = sc["rows"]
        if sc["bad_rows"]:
            diag.append({"level": "warn", "detail": "칸 수가 컬럼 수와 다른 줄 %d개를 건너뛰었습니다." % sc["bad_rows"]})
        if sc["bad_numbers"]:
            diag.append({"level": "warn", "detail": "숫자가 아닌 값 %d개를 0 으로 보고 집계했습니다." % sc["bad_numbers"]})
        if sc["na_numbers"]:
            diag.append({"level": "info", "detail": "수치 칸의 결측(NA) %d개를 0 으로 보고 집계했습니다." % sc["na_numbers"]})
        months = sorted(sc["months"])
        month = months[0] if months else None
        res["month"] = ("%s-%s" % (month[:4], month[4:6])) if month else None
        if len(months) > 1:
            diag.append({"level": "warn", "detail": "한 파일에 여러 달(%s)이 들어 있습니다. 표는 달을 합쳐 집계했고 대조 줄은 내지 않았습니다." % ", ".join(months)})

        # 범위 설명
        biz_in_file = sorted(sc["biz"], key=lambda k: -sc["biz"][k][2])
        if want_biz:
            biz_label = want_biz[1]
        elif len(biz_in_file) == 1:
            biz_label = biz_in_file[0][1] or biz_in_file[0][0] or "업종 미상"
        else:
            biz_label = "업종 %d개 전체" % len(biz_in_file)
        region_label = want_bjd or ("법정동 %d곳 전체" % len(sc["bjd_all"]) if sc["bjd_all"] else "지역 전체")
        if want_district:
            region_label += "(%s)" % want_district[0]
        scope = "%s · %s · %s" % (region_label, biz_label, res["month"] or "달 미상")
        res["scope"] = {"region": region_label, "biz": biz_label, "month": res["month"], "text": scope,
                        "rows_in_scope": sc["focus"].rows, "bjd_in_file": len(sc["bjd_all"])}
        acc = sc["focus"]
        if acc.rows == 0:
            diag.append({"level": "warn", "detail": "조건(%s)에 맞는 줄이 없습니다." % scope})
            return res

        tables = []
        led = {"hidden": set(), "small": 0, "rel": []}
        dim_label = {"time": "시간대", "week": "요일"}.get(dim)
        v_dim = _Vec("dim", acc.by["dim"], _canon(cat, no, dim), acc.total) if dim else None
        v_ut = _Vec("ut", acc.by["user_type"], _canon(cat, no, "user_type"), acc.total) if "user_type" in cols else None
        v_age = _Vec("age", acc.by["age"], _canon(cat, no, "age"), acc.total) if "age" in cols else None
        for v in (v_dim, v_ut, v_age):
            if v:
                v.register(led)
        if v_dim:
            note = None
            if dim == "time":
                tv = cat.dataset(no)["refs"].get("시간대") or []
                note = "시간대 기준(명세서 %s 참고표): %s." % (no, ", ".join("%s %s" % (r[0], r[1]) for r in tv if isinstance(r, list)))
            tables.append(_share_table("%s별 비중 · %s" % (dim_label, scope), dim_label, v_dim, note))
        if v_ut:
            tables.append(_share_table("이용자구분별 비중 · %s" % scope, "이용자구분", v_ut))
        if v_age:
            tables.append(_share_table("연령대별 비중 · %s" % scope, "연령대", v_age,
                                       "연령대 기준: 10=19세 이하, 20=29세 이하 … 70=70세 이상(명세서 %s 참고표)." % no))
        by_bjd = sc["biz_scope"]["by_bjd"]
        hide_center_sum = False
        if len(by_bjd) > 1:
            t, hide_center_sum = _bjd_rank_table(by_bjd, want_bjd, biz_label, res["month"], led,
                                                 same_as_total=bool(want_bjd) and not want_district)
            tables.append(t)
        skipped = 0
        if v_dim:
            tables.append(_count_table("%s별 이용건수·이용금액 · %s" % (dim_label, scope), dim_label, v_dim))
            for v_other, other_label, tag in ((v_ut, "이용자구분", "x_ut"), (v_age, "연령대", "x_age")):
                if not v_other:
                    continue
                t = _matrix_table("%s × %s 이용건수 · %s" % (dim_label, other_label, scope), dim_label,
                                  acc.cross["user_type" if tag == "x_ut" else "age"], v_dim, v_other, led, tag)
                if t:
                    tables.append(t)
                else:
                    skipped += 1
        else:
            if v_ut:
                tables.append(_count_table("이용자구분별 이용건수·이용금액 · %s" % scope, "이용자구분", v_ut))
            if v_age:
                tables.append(_count_table("연령대별 이용건수·이용금액 · %s" % scope, "연령대", v_age))
        if skipped:
            diag.append({"level": "info", "detail": "줄이나 열이 하나뿐인 교차표 %d개는 내지 않았습니다(위의 한 줄짜리 표와 같은 값이라 가린 칸을 거꾸로 셈할 수 있습니다)." % skipped})
        res["tables"] = tables
        res["masked_total"] = sum(t["masked_cells"] for t in tables)
        if res["masked_total"]:
            res["mask_note"] = MASK_NOTE
        # 화면 전체의 역산 점검: 가린 값 가운데 다른 표의 합계로 알아낼 수 있는 것, 가리지 않은 10 미만 값(둘 다 0 이어야 한다).
        res["recoverable"] = masking.audit(led["rel"], led["hidden"])
        res["small_visible"] = led["small"]
        res["reconcile"] = _reconcile(sc, months, want_biz, biz_in_file, cat, res["virtual"], hide_center_sum)
        return res
    except Exception as e:  # 어떤 파일을 넣어도 죽지 않는다.
        return fail("파일을 읽는 중에 예상하지 못한 문제가 생겼습니다(%s)." % type(e).__name__,
                    "센터에서 내려받은 CSV 를 그대로 넣었는지 확인해 주세요. 같은 문제가 이어지면 파일의 첫 두 줄을 확인해 주세요.")


def _count_rows(path, enc):
    n = 0
    with open(path, "r", encoding=enc, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if row:
                n += 1
    return n


def _bjd_rank_table(by_bjd, want_bjd, biz_label, month, led, same_as_total=False):
    """응용집계: 법정동별 이용금액 비중과 순위(상위 10곳 + 요청한 법정동). (표, 대조 줄의 파일 합계를 숨겨야 하는가)

    파일에 든 법정동이 모두 표에 나오면 비중의 합이 100 이라, 가린 곳이 하나뿐일 때 나머지를 빼서 알 수 있다.
    그때는 다음으로 작은 곳(요청한 곳은 다른 표에 값이 나오므로 빼고)을 함께 가린다.
    """
    keys = sorted(by_bjd, key=lambda k: -by_bjd[k][2])
    tot_amt = sum(v[2] for v in by_bjd.values())
    tot_cnt = sum(v[1] for v in by_bjd.values())
    show = keys[:10]
    if want_bjd and want_bjd in by_bjd and want_bjd not in show:
        show.append(want_bjd)
    all_shown = len(show) == len(keys)
    flags = dict((k, 1 if _basis(by_bjd[k]) < config.MASK_THRESHOLD else 0) for k in show)
    hide_center_sum = False
    if all_shown and sum(1 for k in show if flags[k]) == 1:
        rest = [k for k in show if not flags[k] and k != want_bjd]
        if rest:
            flags[min(rest, key=lambda k: (_basis(by_bjd[k]), k))] = 2
        else:
            hide_center_sum = True      # 가릴 곳이 더 없다: 파일 합계를 숨겨 거꾸로 셈하지 못하게 한다
    rows = []
    for k in show:
        flag = flags[k]
        name = k + (" (요청한 곳)" if k == want_bjd else "")
        rows.append([keys.index(k) + 1, name,
                     _pct(by_bjd[k][2], tot_amt) if not flag else _mt(by_bjd[k][2], flag),
                     _pct(by_bjd[k][1], tot_cnt) if not flag else _mt(by_bjd[k][1], flag)])
    for k in show:
        if flags[k]:
            led["hidden"].add(("bjd", k))
    if all_shown:
        if hide_center_sum:
            led["hidden"].add(("biz",))
        led["rel"].append((("biz",), [("bjd", k) for k in show]))
    if same_as_total and want_bjd in flags:
        led["rel"].append((("G",), [("bjd", want_bjd)]))
    masked = sum(1 for k in show if flags[k]) * 2
    note = "이름이 같은 법정동(월평동)은 이 표에서 한 줄로 합쳐진다."
    return ({"title": "법정동별 비중과 순위 · %s · %s (파일에 든 법정동 %d곳 가운데)" % (biz_label, month or "달 미상", len(keys)),
             "kind": "응용집계", "columns": ["순위(금액)", "법정동", "이용금액 비중(%)", "이용건수 비중(%)"], "rows": rows,
             "masked_cells": masked, "note": note}, hide_center_sum)


def _reconcile(sc, months, want_biz, biz_in_file, cat, virtual=False, hide_center_sum=False):
    """허브 1048 도 전체 값과의 대조 줄. '검증'이 아니라 '차이'로 적는다.

    파일에 법정동이 모두 들어 있을 때만 차이(%)를 낸다. 조건을 걸어 받은 파일은 도 전체 값과 견주지 않는다.
    """
    out = {"available": False, "public_value": None, "center_sum": None, "diff_pct": None, "note": ""}
    pub = public_mod.get()
    if not pub.d1048.ok:
        out["note"] = "허브 1048 공개 데이터를 읽지 못해 대조하지 않았다."
        return out
    if len(months) != 1:
        out["note"] = "파일에 달이 하나가 아니라서 대조하지 않았다."
        return out
    ym = "%s-%s" % (months[0][:4], months[0][4:6])
    if want_biz:
        names = [want_biz[1]]
    else:
        names = [(k[1] or cat.biz_name(k[0]) or "") for k in biz_in_file]
    names = [n for n in names if n]
    if not names:
        out["note"] = "파일에서 업종명을 찾지 못해 대조하지 않았다."
        return out
    missing = [n for n in names if not pub.has_biz(n)]
    if missing:
        out["note"] = "허브 1048 에 없는 업종(%s)이 있어 대조하지 않았다." % ", ".join(missing[:3])
        return out
    total = 0
    for n in names:
        v = pub.value_1048(ym, n, "use_amt")
        if v is None:
            out["note"] = "허브 1048 은 %s~%s 만 있어 %s 값과 대조할 수 없다." % (pub.d1048.first, pub.d1048.last, ym)
            return out
        total += v
    center = sc["biz_scope"]["total"][2]
    label = names[0] if len(names) == 1 else "업종 %d개" % len(names)
    n_bjd = len(sc["biz_scope"]["by_bjd"])
    full = len(cat.bjd_list())
    out["available"] = True
    out["public_value"] = total
    out["center_sum"] = None if hide_center_sum else center
    head = "%s %s 이용금액. " % (ym, label)
    if n_bjd and n_bjd < full:
        # 지역 조건을 걸어 받았거나 거래가 없는 법정동이 빠진 파일이다. 도 전체 값과의 차이는 뜻이 없어 내지 않는다.
        out["note"] = head + ("파일에 법정동이 %d곳 가운데 %d곳만 들어 있어(지역 조건을 걸어 받았거나 거래가 없는 곳이 빠진 파일) "
                              "도 전체 값과 견주지 않았다. 차이(%%)는 법정동이 모두 든 파일에서만 낸다." % (full, n_bjd))
        if hide_center_sum:
            out["note"] += " 이 파일의 합계는 가린 칸을 거꾸로 셈할 수 있어 숨겼다."
        return out
    out["diff_pct"] = round((center - total) * 100.0 / total, 1) if total else None
    note = ""
    if virtual:
        note += ("이 파일은 허브 값을 쪼개 만든 가상 파일이라 차이 %s%% 는 만든 방식 때문이다. "
                 "실제 센터 데이터가 허브 값과 맞는다는 근거가 아니다. " % out["diff_pct"])
    note += head + "이 파일의 합계와 허브 1048 도 전체 값의 차이다. "
    if n_bjd:
        note += "파일에 법정동 %d곳이 모두 들어 있다. 차이가 크면 다른 조건을 걸어 받았거나 집계 정의가 다르다는 뜻이다. " % n_bjd
    note += "허브 1048 이 센터 카드 데이터의 도 전체 집계본이라는 것은 추정이라 '검증'이 아니라 '차이'로 적는다."
    if pub.d1048.placeholder:
        note += " 지금 공개 값은 대체 데이터다."
    out["note"] = note
    return out
