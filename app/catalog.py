# -*- coding: utf-8 -*-
"""명세 카탈로그: spec.json + dim_tags.json 을 읽어 '어느 데이터가 어떤 단위를 가졌나'를 답한다.

여기 함수들은 수치를 만들지 않는다. 명세서에 적힌 사실(컬럼, 값 목록, 수록 기간, 반출)을 찾아 줄 뿐이다.
"""
import json
import re
import threading

from . import config
from .korean import josa as kj


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def nospace(s):
    return re.sub(r"\s+", "", s or "")


def biz_key(name):
    """업종명을 맞출 때 쓰는 열쇠: 공백과 쉼표·물음표 같은 문장부호를 지운다.

    허브 1048 실물은 '피자 햄버거 샌드위치 및 유사 음식점업'(쉼표 없음)으로 적고, 한 달은 '기타음?식료품…'처럼
    글자가 깨져 있다. 센터 참고표는 '피자, 햄버거, 샌드위치 및 유사 음식점업'이다.
    """
    return re.sub(r"[\s,?·./]+", "", name or "")


def ym_to_int(ym):
    """'2023-08' 또는 '202308' → 연*12 + (월-1)."""
    s = str(ym).replace("-", "").strip()
    if not re.match(r"^\d{6}$", s):
        raise ValueError("연월 형식이 아님: %r" % (ym,))
    y, m = int(s[:4]), int(s[4:6])
    if not 1 <= m <= 12:
        raise ValueError("월이 1~12 가 아님: %r" % (ym,))
    return y * 12 + (m - 1)


def int_to_ym(n):
    return "%04d-%02d" % (n // 12, n % 12 + 1)


def norm_ym(ym):
    return int_to_ym(ym_to_int(ym))


class Catalog(object):
    def __init__(self, spec, tags):
        self.spec = spec
        self.tags = tags
        self.datasets = {}
        self.order = []
        for d in spec["datasets"]:
            self.datasets[d["no"]] = d
            self.order.append(d["no"])
        self._rules = {}
        for r in tags["columns"]:
            self._rules.setdefault(r["col"], []).append(r)
        self._family_of = {}
        for key, fam in tags["families"].items():
            for no in fam["datasets"]:
                self._family_of[no] = key
        self._units_cache = {}

    # ------------------------------------------------------------ 기본 정보
    def counts(self):
        c = self.spec["counts"]
        return {"version": self.spec["version"], "date": self.spec["date"],
                "datasets": c["datasets"], "ref_tables": c["ref_tables"], "columns": c["columns"]}

    def label(self):
        return config.SPEC_LABEL.format(**self.counts())

    def has(self, no):
        return no in self.datasets

    def dataset(self, no):
        return self.datasets.get(no)

    def name(self, no):
        d = self.datasets.get(no)
        return d["name"] if d else ""

    def columns(self, no, include_partition=False):
        d = self.datasets.get(no)
        if not d:
            return []
        return [c for c in d["columns"] if include_partition or not c["partition"]]

    def column(self, no, en):
        for c in self.columns(no, include_partition=True):
            if c["en"] == en:
                return c
        return None

    def family_key(self, no):
        return self._family_of.get(no)

    def family(self, no):
        return self.tags["families"].get(self._family_of.get(no), None)

    def subject(self, no):
        fam = self.family(no)
        return fam["subject"] if fam else None

    # ------------------------------------------------------------ 단위 표시
    def tag(self, no, col):
        """(데이터, 컬럼)에 붙은 의미 표시 한 줄. 없으면 None. 데이터 번호를 적은 줄이 '*' 줄보다 먼저다."""
        rules = self._rules.get(col, [])
        star = None
        for r in rules:
            if r["ds"] == "*":
                star = r
            elif no in r["ds"]:
                return r
        return star

    def units(self, no):
        """{단위: {"columns": [컬럼명], "derived": bool, "scheme": 값 체계}}."""
        if no in self._units_cache:
            return self._units_cache[no]
        out = {}
        for c in self.columns(no):
            r = self.tag(no, c["en"])
            if not r or not r.get("unit"):
                continue
            u = out.setdefault(r["unit"], {"columns": [], "derived": False, "scheme": r.get("scheme")})
            u["columns"].append(c["en"])
            if r.get("scheme") and not u.get("scheme"):
                u["scheme"] = r["scheme"]
        for dv in self.tags.get("derived", []):
            if dv["from"] in out and dv["to"] not in out:
                out[dv["to"]] = {"columns": list(out[dv["from"]]["columns"]), "derived": True,
                                 "scheme": None, "note": dv["note"]}
        self._units_cache[no] = out
        return out

    def unit_label(self, key):
        u = self.tags["units"].get(key) or self.tags["need_units"].get(key)
        return u["label"] if u else key

    def unit_axis(self, key):
        u = self.tags["units"].get(key) or self.tags["need_units"].get(key)
        return u["axis"] if u else ""

    def scheme(self, no, unit):
        info = self.units(no).get(unit)
        if not info or not info.get("scheme"):
            return None
        return self.tags["schemes"].get(info["scheme"])

    def focus(self, no, unit, key):
        """목적 문장의 값(예: 20대)을 이 데이터에서 따로 볼 수 있는지.

        돌려주는 값: {"known": bool, "separable": bool, "values": [...], "note": str, "column": 컬럼명}
        """
        info = self.units(no).get(unit)
        if not info:
            return {"known": False, "separable": False, "values": [], "note": "%s 컬럼이 없다" % self.unit_label(unit), "column": None}
        col = info["columns"][0]
        if info.get("derived"):
            return {"known": True, "separable": True, "values": [], "note": info.get("note", ""), "column": col}
        sch = self.scheme(no, unit)
        if not sch or key not in sch.get("focus", {}):
            return {"known": False, "separable": True, "values": [], "note": "값 표기가 명세서에 없어 받은 뒤 확인해야 한다", "column": col}
        f = sch["focus"][key]
        return {"known": True, "separable": bool(f.get("separable")), "values": list(f.get("values", [])),
                "note": f.get("note", ""), "column": col}

    # ------------------------------------------------------------ 값 목록
    def _ref_rows(self, no, name):
        d = self.datasets.get(no)
        if not d:
            return None, None
        for meta in d.get("ref_tables", []):
            if meta["name"] == name or meta["key"] == name:
                return d["refs"].get(meta["key"]), meta
        return None, None

    def column_values(self, no, col):
        """조건 값으로 쓸 수 있는 값 목록과 그 출처. 명세서에 값 목록이 없으면 None."""
        c = self.column(no, col)
        if not c:
            return None
        r = self.tag(no, col)
        spec_v = (r or {}).get("values")
        if spec_v:
            src = spec_v["from"]
            if src in ("ref", "borrow"):
                ds_no = spec_v.get("dataset", no) if src == "borrow" else no
                rows, meta = self._ref_rows(ds_no, spec_v["name"])
                if rows is not None:
                    idx = spec_v.get("column", 0)
                    vals = [(x[idx] if isinstance(x, list) else x) for x in rows]
                    uniq = len(set(vals))
                    rows_text = "%d행" % meta["row_count"]
                    if uniq != meta["row_count"]:
                        # 예: 1-14 법정동 표는 74행이지만 월평동이 두 권역에 한 줄씩이라 이름은 73개다.
                        rows_text += ", 서로 다른 이름 %d개" % uniq
                    where = "%s %s(%s칸, %s)" % (ds_no, meta["title"], meta["cell"], rows_text)
                    return {"values": vals, "source": src, "evidence": where,
                            "borrowed_from": ds_no if (src == "borrow" and ds_no != no) else None}
            elif src == "ref_sheet":
                for rs in self.spec["ref_sheets"]:
                    if rs["name"] == spec_v["sheet"] and rs.get("rows"):
                        i = rs["header"].index(spec_v["column"])
                        vals = []
                        for row in rs["rows"]:
                            if row[i] not in vals:
                                vals.append(row[i])
                        return {"values": vals, "source": "ref_sheet",
                                "evidence": "%s 시트 %s 열(%d행)" % (rs["name"], spec_v["column"], rs["row_count"])}
            elif src == "list":
                return {"values": list(spec_v["items"]), "source": "note", "evidence": "비고 '%s'" % c["note"]}
            elif src == "note_paren":
                m = re.search(r"\((.*)\)\s*$", c["note"].replace("\n", " "))
                if m:
                    vals = self._split_paren_list(m.group(1))
                    return {"values": vals, "source": "note", "evidence": "비고 '%s'" % c["note"]}
        # 값 체계에 손으로 푼 값 목록이 있으면 그것(근거는 값 체계의 evidence).
        if r and r.get("scheme"):
            sch = self.tags["schemes"].get(r["scheme"])
            if sch and sch.get("values"):
                return {"values": list(sch["values"]), "source": "scheme", "evidence": sch["evidence"]}
        # 비고나 데이터형식이 쉼표 나열이면 그것.
        for field, label in (("note", "비고"), ("example", "데이터형식")):
            text = c[field].replace("\n", " ")
            if text.count(",") >= 1 and "…" not in text and "..." not in text and "(" not in text:
                vals = [v.strip() for v in text.split(",") if v.strip()]
                if vals and all(len(v) <= 20 for v in vals):
                    return {"values": vals, "source": field, "evidence": "%s '%s'" % (label, c[field])}
        return None

    @staticmethod
    def _split_paren_list(text):
        """'제주시 동부, 제주시 중부(동), …' 처럼 괄호가 든 쉼표 나열을 나눈다."""
        out, buf, depth = [], "", 0
        for ch in text:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                out.append(buf.strip())
                buf = ""
            else:
                buf += ch
        if buf.strip():
            out.append(buf.strip())
        return out

    def value_evidence(self, no, col, value):
        """조건 한 줄의 근거. {"ok", "sheet", "col_no", "note", "known", "borrowed"}.

        ok 가 False 면 컬럼이 없거나 값이 명세서의 값 목록에 없다. known 이 False 면 값 목록 자체가 명세서에 없다.
        borrowed 가 True 면 이 시트에는 값 목록이 없어 다른 시트의 목록을 빌려 대조했다(같은 표기인지는 명세서로 확인되지 않는다).
        """
        c = self.column(no, col)
        if not c:
            return {"ok": False, "known": False, "borrowed": False, "sheet": no, "col_no": None,
                    "note": "%s 시트에 %s 컬럼이 없다" % (no, col)}
        if c["partition"]:
            return {"ok": False, "known": False, "borrowed": False, "sheet": no, "col_no": c["n"],
                    "note": "%s 는 파티션 컬럼이라 내려받은 CSV 에 들어 있지 않다" % col}
        vals = self.column_values(no, col)
        base = "%s번 컬럼 %s(%s)" % (c["n"], c["en"], c["ko"])
        if c["note"]:
            base += ", 비고 '%s'" % c["note"].replace("\n", " ")
        if vals is None:
            return {"ok": True, "known": False, "borrowed": False, "sheet": no, "col_no": c["n"],
                    "note": base + ". 값 목록은 명세서에 없어 내려받기 화면에서 확인해야 한다"}
        want = nospace(str(value))
        hit = [v for v in vals["values"] if nospace(v) == want]
        src_no = vals.get("borrowed_from")
        if src_no:
            # 이 시트에는 값 목록이 없다. 다른 시트(다른 제공처일 수 있다)의 목록을 빌려 대조한 것이라 '확인 못 함'으로 센다.
            if c["example"]:
                base += ", 데이터형식 '%s'" % c["example"].replace("\n", " ")
            lead = "%s. %s 시트에는 이 컬럼의 값 목록이 없어 %s 를 빌려 대조했다" % (base, no, vals["evidence"])
            if hit:
                return {"ok": True, "known": True, "borrowed": True, "sheet": no, "col_no": c["n"],
                        "note": "%s. 값 '%s'%s 그 목록에 있다. %s 에 같은 표기로 들어 있는지는 명세서로 확인되지 않으니 내려받기 화면에서 확인한다"
                                % (lead, hit[0], kj(hit[0], "은"), no)}
            return {"ok": False, "known": True, "borrowed": True, "sheet": no, "col_no": c["n"],
                    "note": "%s. 값 '%s'%s 그 목록에 없다" % (lead, value, kj(value, "은"))}
        if hit:
            return {"ok": True, "known": True, "borrowed": False, "sheet": no, "col_no": c["n"],
                    "note": "%s. 값 '%s'%s %s 에 있다" % (base, hit[0], kj(hit[0], "은"), vals["evidence"])}
        return {"ok": False, "known": True, "borrowed": False, "sheet": no, "col_no": c["n"],
                "note": "%s. 값 '%s'%s %s 에 없다" % (base, value, kj(value, "은"), vals["evidence"])}

    # ------------------------------------------------------------ 수록 기간·반출
    def period(self, no):
        d = self.datasets.get(no)
        return d["period"] if d else None

    def period_text(self, no):
        p = self.period(no)
        if not p:
            return "수록 기간이 명세서에 없음"
        if p["kind"] == "range":
            return "%s ~ %s" % (p["start"], p["end"])
        if p["kind"] == "open":
            return "%s ~ 현재까지(실제 최신 달은 명세서에 없음)" % p["start"]
        if p["kind"] == "accumulating":
            return "%s 부터 누적(끝 달은 명세서에 없음)" % p["start"]
        if p["kind"] == "snapshot":
            return "%s 기준 한 시점" % p["start"]
        if p["kind"] == "before":
            return "%s 이전 등록분" % p["end"]
        return p.get("raw", "")

    def month_checkable(self, no):
        p = self.period(no)
        return bool(p) and p["kind"] in ("range", "open", "accumulating")

    def month_in_range(self, no, ym):
        """달이 수록 범위 안인가. 판정할 수 없으면 None."""
        if not self.month_checkable(no):
            return None
        p = self.period(no)
        n = ym_to_int(ym)
        if n < ym_to_int(p["start"]):
            return False
        if p["end"] and n > ym_to_int(p["end"]):
            return False
        return True

    def export_info(self, no):
        d = self.datasets.get(no)
        if not d:
            return {"allowed": False, "notes": []}
        allowed = d["export"] == "가능"
        notes = []
        head = "명세서 표기: %s" % d["export_raw"]
        if d.get("export_note"):
            head += " (%s)" % d["export_note"]
        notes.append(head)
        restricted = any(p in d["provider"] for p in config.EXPORT_RESTRICTED_PROVIDERS)
        if allowed and not restricted:
            notes.extend(config.EXPORT_NOTES_OPEN)
        else:
            notes.extend(config.EXPORT_NOTES_RESTRICTED)
        notes.append("근거: %s" % config.EXPORT_POLICY_SOURCE)
        return {"allowed": allowed, "notes": notes}

    # ------------------------------------------------------------ 지역·업종 이름
    def bjd_list(self):
        rows, _ = self._ref_rows("1-07", "법정동")
        return list(rows or [])

    def bjd_ambiguous(self):
        """이름이 같은 법정동과 그 권역: {'월평동': ['제주시 중부(동)', '서귀포시 중부']} (v02 참고표의 비고)."""
        rows, _ = self._ref_rows("1-14", "법정동")
        out = {}
        for r in rows or []:
            if isinstance(r, list) and len(r) > 1 and r[1]:
                out.setdefault(r[0], []).append(r[1])
        return dict((k, v) for k, v in out.items() if len(v) > 1)

    def emd_list(self):
        rows, _ = self._ref_rows("4-10", "행정동코드 및 행정동명")
        return [r[1] for r in rows or []]

    def emd_code(self, name):
        rows, _ = self._ref_rows("4-10", "행정동코드 및 행정동명")
        for r in rows or []:
            if r[1] == name:
                return r[0]
        return None

    def district7_list(self):
        rows, _ = self._ref_rows("1-14", "지역구분")
        return list(rows or [])

    def biz42(self):
        rows, _ = self._ref_rows("1-14", "업종코드 및 업종명")
        return [(r[0], r[1]) for r in rows or []]

    def biz_name(self, code):
        for c, n in self.biz42():
            if c == code:
                return n
        return None

    def biz_code(self, name):
        want = biz_key(name)
        for c, n in self.biz42():
            if biz_key(n) == want:
                return c
        return None

    def pos_biz(self, column, code):
        """(참고표1) 업종코드표에서 코드의 이름을 찾는다."""
        for rs in self.spec["ref_sheets"]:
            if rs.get("rows") and column in rs["header"]:
                i = rs["header"].index(column)
                j = rs["header"].index(column.replace("_cd", "_nm"))
                for row in rs["rows"]:
                    if row[i] == code:
                        return row[j]
        return None

    def resolve_region(self, text):
        """문장이나 이름에서 지역을 찾는다.

        돌려주는 값: {"text", "level": "bjd|emd|district7|city|all", "bjd", "emd", "district7", "city",
                    "ambiguous": [권역…] (이름이 같은 법정동이 두 곳일 때)}
        """
        t = text or ""
        out = {"text": "", "level": "all", "bjd": None, "emd": None, "district7": None, "city": None, "ambiguous": []}
        for d in sorted(self.district7_list(), key=len, reverse=True):
            if d in t:
                out["district7"] = d
                break
        for c in ("서귀포시", "제주시"):
            if c in t:
                out["city"] = c
                break
        if out["district7"] and not out["city"]:
            out["city"] = out["district7"].split(" ")[0]
        bjd = None
        for name in sorted(self.bjd_list(), key=len, reverse=True):
            if name in t:
                bjd = name
                break
        emd = None
        emds = self.emd_list()
        for full in sorted(emds, key=len, reverse=True):
            short = full.split(" ", 1)[1]
            if full in t or short in t:
                if out["city"] and not full.startswith(out["city"]) and full not in t:
                    continue
                emd = full
                break
        out["bjd"] = bjd
        out["emd"] = emd
        if bjd or emd:
            out["level"] = "bjd" if bjd else "emd"
            out["text"] = bjd or emd.split(" ", 1)[1]
            if bjd and bjd in self.bjd_ambiguous():
                options = self.bjd_ambiguous()[bjd]
                if out["district7"] in options:
                    options = [out["district7"]]
                elif out["city"]:
                    options = [o for o in options if o.startswith(out["city"])] or options
                out["ambiguous"] = options if len(options) > 1 else []
                if len(options) == 1:
                    out["district7"] = options[0]
                    out["city"] = options[0].split(" ")[0]
            if emd and not out["city"]:
                out["city"] = emd.split(" ")[0]
        elif out["district7"]:
            out["level"] = "district7"
            out["text"] = out["district7"]
        elif out["city"]:
            out["level"] = "city"
            out["text"] = out["city"]
        return out

    # ------------------------------------------------------------ CSV 헤더 인식
    def header_index(self):
        """{파티션을 뺀 컬럼명 집합: [데이터 번호…]} (헤더가 같은 쌍은 번호가 여럿)."""
        idx = {}
        for no in self.order:
            key = frozenset(c["en"] for c in self.columns(no))
            idx.setdefault(key, []).append(no)
        return idx


_lock = threading.Lock()
_cached = None


def get():
    """카탈로그를 한 번만 읽어 돌려준다."""
    global _cached
    with _lock:
        if _cached is None:
            _cached = Catalog(read_json(config.SPEC_JSON), read_json(config.DIM_TAGS_JSON))
        return _cached


def reset():
    global _cached
    with _lock:
        _cached = None
