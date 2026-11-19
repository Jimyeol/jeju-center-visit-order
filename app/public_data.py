# -*- coding: utf-8 -*-
"""제주데이터허브 공개 데이터 2종(1048 카드, 520 유동인구)을 읽어 월별 집계·계절 지수·비중을 계산한다.

- data/public/ 에 실물 CSV 가 있으면 실물을 쓰고(placeholder=False), 없으면 '_대체_' 로 시작하는 대체 파일을 쓴다.
- 실물인지는 파일 이름이 아니라 헤더로 가린다. 컬럼은 이름 후보 목록(hub_center_map.json)으로 찾는다.
- 읽기는 utf-8-sig 를 먼저, 실패하면 cp949.
- 1048 의 이용자수는 월별 값이라 여러 달을 더하지 않는다(건수·금액만 더한다).
"""
import csv
import os
import re
import threading

from . import config
from .catalog import biz_key, int_to_ym, nospace, read_json, ym_to_int

METRIC_LABELS = {"use_amt": "이용금액", "use_cnt": "이용건수", "user_cnt": "이용자수", "visit": "방문인구"}

_FALLBACK_CANDIDATES = {
    "d1048": {"ym": ["base_year_month", "연월", "년월"], "biz": ["biz_nm", "업종명", "업종"],
              "user_type": ["user_type", "이용자구분"], "sex": ["sex", "성별"],
              "user_cnt": ["user_count", "user_cnt", "이용자수"], "use_cnt": ["use_count", "use_cnt", "이용건수"],
              "use_amt": ["use_amount", "use_amt", "이용금액"]},
    "d520": {"ym": ["base_year_month", "연월", "년월"], "sex": ["sex", "성별"], "age": ["age_range", "연령대"],
             "time": ["time_range", "시간대", "시간"], "visit": ["visit_population_count", "방문인구"]},
}


def _candidates():
    try:
        m = read_json(config.HUB_MAP_JSON)
        out = {}
        for kind in ("d1048", "d520"):
            out[kind] = dict((c["key"], c["hub_candidates"]) for c in m[kind]["columns"])
        return out
    except Exception:
        return _FALLBACK_CANDIDATES


def _norm_header(h):
    return nospace(h.replace("﻿", "")).strip('"').lower()


def open_text(path):
    """utf-8-sig 를 먼저, 실패하면 cp949 로 읽을 수 있는 인코딩을 골라 돌려준다."""
    for enc in config.READ_ENCODINGS:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                while f.read(1 << 16):
                    pass
            return enc
        except UnicodeDecodeError:
            continue
    return config.READ_ENCODINGS[-1]


def read_header(path):
    enc = open_text(path)
    with open(path, "r", encoding=enc, newline="") as f:
        try:
            row = next(csv.reader(f))
        except StopIteration:
            return enc, []
    return enc, row


def map_columns(header, cands):
    """{키: 열 번호}. 못 찾은 키가 있으면 None."""
    norm = [_norm_header(h) for h in header]
    out = {}
    for key, names in cands.items():
        idx = None
        for n in names:
            n2 = _norm_header(n)
            if n2 in norm:
                idx = norm.index(n2)
                break
        if idx is None:
            return None
        out[key] = idx
    return out


def to_number(s):
    s = (s or "").strip().replace(",", "")
    if s == "" or s.upper() in ("NA", "NULL", "NAN"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def to_ym_int(s):
    digits = re.sub(r"\D", "", (s or "").split(".")[0])
    if len(digits) < 6:
        return None
    try:
        return ym_to_int(digits[:6])
    except ValueError:
        return None


def hour_of(label):
    """'01시', '1', '01' → 1 … 24. 모르면 None."""
    m = re.search(r"\d+", label or "")
    if not m:
        return None
    h = int(m.group(0))
    return h if 0 <= h <= 24 else None


def age_key(label):
    """520 의 연령대 표기를 '20대' 같은 열쇠로 바꾼다(표기가 달라도 맞추기 위해)."""
    s = nospace(label)
    m = re.search(r"\d+", s)
    if not m:
        return s
    n = int(m.group(0))
    if "미만" in s and n <= 10:
        return "10세 미만"
    if n >= 70:
        return "70대 이상"
    if n < 10:
        return "10세 미만"
    return "%d대" % (n // 10 * 10)


class Source(object):
    """공개 데이터 한 종을 읽어 놓은 것."""

    def __init__(self, kind):
        self.kind = kind
        self.file = None
        self.placeholder = False
        self.rows = 0
        self.first = None
        self.last = None
        self.error = None
        self.encoding = None
        self.agg = {}
        self.values = {}

    def status(self):
        return {"rows": self.rows, "first": self.first, "last": self.last,
                "placeholder": self.placeholder, "file": self.file, "error": self.error}

    @property
    def ok(self):
        return self.rows > 0 and not self.error


def _find_file(kind, cands):
    """실물(앞머리가 '_대체_' 가 아닌 CSV)을 헤더로 찾고, 없으면 대체 파일."""
    if not os.path.isdir(config.PUBLIC_DIR):
        return None, False
    # 점(.)이나 ~$ 로 시작하는 이름은 운영체제나 엑셀이 만든 딸린 파일이다(맥의 ._이름.csv 등). 읽지 않는다.
    names = sorted(n for n in os.listdir(config.PUBLIC_DIR)
                   if n.lower().endswith(".csv") and not n.startswith((".", "~$")))
    real = [n for n in names if not n.startswith(config.PLACEHOLDER_PREFIX)]
    for n in real:
        path = os.path.join(config.PUBLIC_DIR, n)
        try:
            _, header = read_header(path)
        except (OSError, UnicodeError, csv.Error):
            continue        # 글자로 읽히지 않는 파일 하나 때문에 다른 파일까지 못 읽지 않게 그 파일만 넘어간다
        if header and map_columns(header, cands) is not None:
            return path, False
    ph = os.path.join(config.PUBLIC_DIR, config.PLACEHOLDER_1048 if kind == "d1048" else config.PLACEHOLDER_520)
    if os.path.exists(ph):
        return ph, True
    return None, False


def _load_1048(cands):
    src = Source("d1048")
    path, placeholder = _find_file("d1048", cands)
    if not path:
        src.error = "1048 파일이 data/public/ 에 없다"
        return src
    src.file = os.path.basename(path)
    src.placeholder = placeholder
    enc = open_text(path)
    src.encoding = enc
    biz_names, users, sexes = {}, [], []
    lo, hi = None, None
    with open(path, "r", encoding=enc, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        cm = map_columns(header or [], cands)
        if cm is None:
            src.error = "1048 파일의 컬럼을 알아보지 못했다: %s" % ", ".join(header or [])
            return src
        need = max(cm.values())
        for row in reader:
            if len(row) <= need:
                continue
            ym = to_ym_int(row[cm["ym"]])
            if ym is None:
                continue
            biz = row[cm["biz"]].strip()
            bk = biz_key(biz)
            ut = row[cm["user_type"]].strip()
            sx = row[cm["sex"]].strip()
            if bk not in biz_names or "?" in biz_names[bk]:
                biz_names[bk] = biz
            if ut not in users:
                users.append(ut)
            if sx not in sexes:
                sexes.append(sx)
            a = src.agg.get((ym, bk, ut, sx))
            if a is None:
                a = [0.0, 0.0, 0.0]
                src.agg[(ym, bk, ut, sx)] = a
            a[0] += to_number(row[cm["user_cnt"]])
            a[1] += to_number(row[cm["use_cnt"]])
            a[2] += to_number(row[cm["use_amt"]])
            src.rows += 1
            lo = ym if lo is None or ym < lo else lo
            hi = ym if hi is None or ym > hi else hi
    if src.rows == 0:
        src.error = "1048 파일에 읽을 수 있는 줄이 없다"
        return src
    src.first, src.last = int_to_ym(lo), int_to_ym(hi)
    src.values = {"biz": [biz_names[k] for k in sorted(biz_names, key=lambda k: biz_names[k])],
                  "biz_keys": biz_names, "user_type": sorted(users), "sex": sorted(sexes)}
    return src


def _load_520(cands):
    src = Source("d520")
    path, placeholder = _find_file("d520", cands)
    if not path:
        src.error = "520 파일이 data/public/ 에 없다"
        return src
    src.file = os.path.basename(path)
    src.placeholder = placeholder
    enc = open_text(path)
    src.encoding = enc
    sexes, ages, hours = [], [], []
    lo, hi = None, None
    with open(path, "r", encoding=enc, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        cm = map_columns(header or [], cands)
        if cm is None:
            src.error = "520 파일의 컬럼을 알아보지 못했다: %s" % ", ".join(header or [])
            return src
        need = max(cm.values())
        for row in reader:
            if len(row) <= need:
                continue
            ym = to_ym_int(row[cm["ym"]])
            if ym is None:
                continue
            sx = row[cm["sex"]].strip()
            ag = age_key(row[cm["age"]])
            hr = hour_of(row[cm["time"]])
            if hr is None:
                continue
            if sx not in sexes:
                sexes.append(sx)
            if ag not in ages:
                ages.append(ag)
            if hr not in hours:
                hours.append(hr)
            key = (ym, sx, ag, hr)
            src.agg[key] = src.agg.get(key, 0.0) + to_number(row[cm["visit"]])
            src.rows += 1
            lo = ym if lo is None or ym < lo else lo
            hi = ym if hi is None or ym > hi else hi
    if src.rows == 0:
        src.error = "520 파일에 읽을 수 있는 줄이 없다"
        return src
    src.first, src.last = int_to_ym(lo), int_to_ym(hi)
    src.values = {"sex": sexes, "age": sorted(ages, key=lambda a: (int(re.search(r"\d+", a).group(0)) if re.search(r"\d+", a) else 0, a)),
                  "hour": sorted(hours)}
    return src


class PublicData(object):
    def __init__(self):
        cands = _candidates()
        self.d1048 = _load_1048(cands["d1048"])
        self.d520 = _load_520(cands["d520"])

    # ------------------------------------------------------------ 상태·범위
    def status(self):
        return {"d1048": self.d1048.status(), "d520": self.d520.status()}

    @property
    def placeholder(self):
        return bool(self.d1048.placeholder or self.d520.placeholder)

    def scope(self):
        a, b = self.d1048, self.d520
        return {
            "d1048": {
                "name": "업종별 성별 카드 이용 정보(허브 1048)", "available": a.ok, "placeholder": a.placeholder,
                "grain": "도 전체 × 월 × 업종 × 이용자구분 × 성별",
                "units": ["month", "biz", "user_type", "sex"],
                "missing_units": ["읍면동 등 지역", "요일", "시간대", "연령대"],
                "period": {"first": a.first, "last": a.last},
                "values": {"biz": a.values.get("biz", []), "user_type": a.values.get("user_type", []),
                           "sex": a.values.get("sex", [])},
                "metrics": ["use_amt", "use_cnt", "user_cnt"],
                "notes": ["이용자수는 월별 값이라 여러 달을 더하지 않는다.", "지역 구분이 없는 도 전체 값이다."],
            },
            "d520": {
                "name": "제주도 방문 내국인 유동인구 추이(허브 520)", "available": b.ok, "placeholder": b.placeholder,
                "grain": "도 전체 × 월 × 성별 × 연령대 × 시간(01~24시)",
                "units": ["month", "sex", "age", "slot"],
                "missing_units": ["읍면동 등 지역", "요일", "업종", "외국인"],
                "period": {"first": b.first, "last": b.last},
                "values": {"sex": b.values.get("sex", []), "age": b.values.get("age", []),
                           "hour": b.values.get("hour", [])},
                "metrics": ["visit"],
                "notes": ["내국인 방문객만 센 값이다. 외국인은 들어 있지 않다.",
                          "사람 수가 아니라 체류량에 가깝다(시각마다 잰 값을 더한 것).",
                          "2022-07 에 통신사가 바뀌어 계절 지수는 앞뒤를 나눠 계산한다."],
            },
        }

    # ------------------------------------------------------------ 1048
    def has_biz(self, biz_name):
        return biz_key(biz_name) in self.d1048.values.get("biz_keys", {})

    def _iter_1048(self, biz=None, user_types=None, sexes=None):
        bk = biz_key(biz) if biz else None
        for (ym, b, ut, sx), v in self.d1048.agg.items():
            if bk and b != bk:
                continue
            if user_types and ut not in user_types:
                continue
            if sexes and sx not in sexes:
                continue
            yield ym, b, ut, sx, v

    def monthly_1048(self, biz=None, user_types=None, sexes=None, metric="use_amt"):
        i = {"user_cnt": 0, "use_cnt": 1, "use_amt": 2}[metric]
        by = {}
        for ym, b, ut, sx, v in self._iter_1048(biz, user_types, sexes):
            by[ym] = by.get(ym, 0.0) + v[i]
        return [{"ym": int_to_ym(k), "value": int(round(by[k]))} for k in sorted(by)]

    def value_1048(self, ym, biz=None, metric="use_amt", user_types=None, sexes=None):
        """한 달의 도 전체 값. 그 달이 없으면 None."""
        i = {"user_cnt": 0, "use_cnt": 1, "use_amt": 2}[metric]
        n = ym_to_int(ym)
        total, seen = 0.0, False
        for ym2, b, ut, sx, v in self._iter_1048(biz, user_types, sexes):
            if ym2 == n:
                total += v[i]
                seen = True
        return int(round(total)) if seen else None

    def shares_1048(self, by, biz=None, metric="use_amt", last_months=12, user_types=None):
        """최근 last_months 달의 비중(%). by: 'user_type' 또는 'sex'."""
        i = {"user_cnt": 0, "use_cnt": 1, "use_amt": 2}[metric]
        if not self.d1048.ok:
            return {"rows": [], "from": None, "to": None}
        hi = ym_to_int(self.d1048.last)
        lo = hi - (last_months - 1)
        acc = {}
        for ym, b, ut, sx, v in self._iter_1048(biz, user_types):
            if ym < lo:
                continue
            k = ut if by == "user_type" else sx
            acc[k] = acc.get(k, 0.0) + v[i]
        return {"rows": pct_rows(acc), "from": int_to_ym(lo), "to": int_to_ym(hi)}

    # ------------------------------------------------------------ 520
    def monthly_520(self, sexes=None, ages=None):
        by = {}
        for (ym, sx, ag, hr), v in self.d520.agg.items():
            if sexes and sx not in sexes:
                continue
            if ages and ag not in ages:
                continue
            by[ym] = by.get(ym, 0.0) + v
        return [{"ym": int_to_ym(k), "value": int(round(by[k]))} for k in sorted(by)]

    def shares_520(self, by, ages=None, sexes=None, last_months=12, bins=None):
        """최근 last_months 달의 비중(%). by: 'hour', 'age', 'sex'. bins 를 주면 시각을 구간으로 묶는다."""
        if not self.d520.ok:
            return {"rows": [], "from": None, "to": None}
        hi = ym_to_int(self.d520.last)
        lo = hi - (last_months - 1)
        acc = {}
        order = []
        for (ym, sx, ag, hr), v in sorted(self.d520.agg.items()):
            if ym < lo:
                continue
            if sexes and sx not in sexes:
                continue
            if ages and ag not in ages:
                continue
            if by == "hour":
                k = hour_bin(hr, bins) if bins else "%02d시" % hr
            elif by == "age":
                k = ag
            else:
                k = sx
            if k not in acc:
                order.append(k)
            acc[k] = acc.get(k, 0.0) + v
        if by == "hour" and bins:
            keep = [label for label, _ in bins]
        elif by == "hour":
            keep = sorted(acc)
        else:
            keep = order
        rows = pct_rows(acc, keep_order=keep)
        return {"rows": rows, "from": int_to_ym(lo), "to": int_to_ym(hi)}


# 520 의 시각을 카드의 5구간(1-14 [참고5] 시간대)과 나란히 놓기 위한 묶음.
# 센터 4-10 명세서 비고가 '해당 시각 정각 측정 방문인구'라서 'h시' 값을 h시 정각에 잰 값으로 보고 묶었다.
# 520 실물은 2022-06 까지 자정을 24시로(01~24시), 2022-07 부터 00시로(00~23시) 적는다.
CARD_SLOT_BINS = [("오전(07~11시)", [7, 8, 9, 10]), ("점심(11~14시)", [11, 12, 13]),
                  ("오후(14~18시)", [14, 15, 16, 17]), ("저녁(18~22시)", [18, 19, 20, 21]),
                  ("밤(22시~07시)", [22, 23, 24, 0, 1, 2, 3, 4, 5, 6])]


def hour_bin(hr, bins):
    for label, hours in bins:
        if hr in hours:
            return label
    return "기타"


def pct_rows(acc, keep_order=None):
    total = sum(acc.values())
    keys = keep_order if keep_order is not None else sorted(acc, key=lambda k: -acc[k])
    rows = []
    for k in keys:
        if k not in acc:
            continue
        rows.append({"label": k, "pct": round(acc[k] * 100.0 / total, 1) if total else 0.0})
    return rows


def seasonal_index(series, start=None, end=None):
    """월별 값 목록에서 1~12월 계절 지수(평균 = 100)를 계산한다.

    방법: 12달이 다 있는 해마다 '그 달 값 ÷ 그 해 월평균'을 구하고, 달마다 여러 해의 평균을 낸다.
    12달이 다 있는 해가 없으면 달별 평균 ÷ 전체 평균으로 계산한다.
    돌려주는 값: {"index": [{"month", "index"}], "years": [쓴 해…], "method": "..."} (값이 없으면 index 가 빈 목록)
    """
    pts = {}
    for p in series:
        n = ym_to_int(p["ym"])
        if start and n < ym_to_int(start):
            continue
        if end and n > ym_to_int(end):
            continue
        pts[n] = float(p["value"])
    if not pts:
        return {"index": [], "years": [], "method": "값 없음"}
    years = {}
    for n, v in pts.items():
        years.setdefault(n // 12, {})[n % 12 + 1] = v
    full = sorted(y for y, mv in years.items() if len(mv) == 12 and sum(mv.values()) > 0)
    idx = {}
    if full:
        for m in range(1, 13):
            ratios = []
            for y in full:
                mean = sum(years[y].values()) / 12.0
                ratios.append(years[y][m] / mean)
            idx[m] = sum(ratios) / len(ratios) * 100.0
        method = "12달이 다 있는 해(%s)마다 그 해 월평균 대비 비율을 구해 평균함" % ", ".join(str(y) for y in full)
    else:
        by_m = {}
        for n, v in pts.items():
            by_m.setdefault(n % 12 + 1, []).append(v)
        means = dict((m, sum(v) / len(v)) for m, v in by_m.items())
        overall = sum(means.values()) / len(means) if means else 0.0
        for m, v in means.items():
            idx[m] = v / overall * 100.0 if overall else 0.0
        method = "12달이 다 있는 해가 없어 달별 평균 ÷ 전체 평균으로 계산함"
    return {"index": [{"month": m, "index": round(idx[m], 1)} for m in sorted(idx)],
            "years": full, "method": method}


def pick_season_months(index_rows):
    """계절 지수에서 가장 많은 달, 가장 적은 달, 평균(100)에 가장 가까운 달.

    돌려주는 값: [{"month", "role", "index"}] (지수가 3달 미만이면 있는 만큼)
    """
    rows = [r for r in index_rows if r.get("index") is not None]
    if not rows:
        return []
    hi = max(rows, key=lambda r: (r["index"], -r["month"]))
    lo = min(rows, key=lambda r: (r["index"], r["month"]))
    out = [{"month": hi["month"], "role": "많은 달", "index": hi["index"]}]
    if lo["month"] != hi["month"]:
        out.append({"month": lo["month"], "role": "적은 달", "index": lo["index"]})
    rest = [r for r in rows if r["month"] not in (hi["month"], lo["month"])]
    if rest:
        mid = min(rest, key=lambda r: (abs(r["index"] - 100.0), r["month"]))
        out.append({"month": mid["month"], "role": "평균에 가까운 달", "index": mid["index"]})
    return out


_lock = threading.Lock()
_cached = None


def get(force=False):
    global _cached
    with _lock:
        if _cached is None or force:
            _cached = PublicData()
        return _cached


def reset():
    global _cached
    with _lock:
        _cached = None
