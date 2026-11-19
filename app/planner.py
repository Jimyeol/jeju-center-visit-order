# -*- coding: utf-8 -*-
"""계획기: LLM 없이 도는 규칙 계획기이자, LLM 도구 5개가 부르는 실제 계산 함수의 집합.

도구 함수
    public_scope()                      공개 데이터가 가진 단위와 기간
    public_monthly(...)                 월별 집계·계절 지수·비중
    catalog_search(units, ...)          필요한 단위 → 후보 데이터, 채우는 단위와 못 채우는 단위
    pick_months(dataset_no, ...)        계절 지수와 대상 데이터 → 받을 달 3개
    build_order(need, draft=None)       주문서 조립 + 검증(submit_order 가 부른다)
규칙 모드
    plan_rule(slots=None, text=None)    /api/plan 응답을 그대로 만든다
LLM 모드에서 쓰는 조립 함수
    make_need(...), compose_response(need, built, trace, ...)

수치는 전부 이 파일과 public_data 의 코드가 계산한다. 입력·출력 모양은 app/INTERFACES.md 에 적었다.
"""
import datetime
import uuid

from . import catalog as catalog_mod
from . import config
from .korean import josa as kj
from . import public_data as public_mod
from . import validator
from .catalog import int_to_ym, nospace, read_json, ym_to_int

WHO_LABELS = {"all": "전체 이용자", "resident": "제주도민", "domestic": "내국인 관광객", "foreign": "외국인 관광객"}
SUBJECT_LABELS = {"spend": "카드 소비(업종별 이용)", "visit": "방문·유동인구"}
WHEN_OPTIONS = [("month", "월별 흐름만"), ("dow", "요일별"), ("slot", "시간대별"), ("dow_slot", "요일과 시간대를 함께")]
EXTRA_UNITS = ["age", "sex", "user_type", "nationality"]
REGION_UNITS = ("dong", "district7", "city")
# 지역 위계 컬럼은 행 수를 늘리지 않으므로 '남는 단위'로 세지 않는다.
NOT_EXTRA = ("month", "day", "district6", "district7", "city", "urban", "bjd", "emd")
EXTRA_WEIGHT = {"grid": 3, "point": 3, "menu": 3}

EXAMPLES = [
    {"title": "노형동 한식당, 시간대와 손님",
     "text": "노형동에 저녁 장사 위주로 한식당을 열려고 합니다. 어느 시간대에 어떤 손님이 많은지 알고 싶어요.",
     "slots": {"who": "all", "what": "biz:I56111", "when": "slot", "region": "노형동", "units": ["user_type", "age", "sex"]}},
    {"title": "애월읍 카페, 요일과 시간대를 함께",
     "text": "토요일 점심에 애월읍 카페에 20대가 얼마나 오는지, 요일과 시간대를 함께 보고 싶습니다.",
     "slots": {"who": "all", "what": "biz:I56220", "when": "dow_slot", "region": "애월읍", "units": ["age"],
               "focus": {"age": ["20대"], "dow": ["토요일"], "slot": ["점심"]}}},
    {"title": "성산읍 외국인 관광객, 연령대와 시간대",
     "text": "성산읍에 오는 외국인 관광객을 연령대별, 시간대별로 알고 싶습니다.",
     "slots": {"who": "foreign", "what": "pop", "when": "slot", "region": "성산읍", "units": ["age"]}},
]

_aliases_cache = None


def josa(word, pair):
    """받침에 맞는 조사를 붙인다. pair: "을를", "은는", "이가", "과와", "으로로"."""
    w = str(word)
    forms = {"을를": ("을", "를"), "은는": ("은", "는"), "이가": ("이", "가"), "과와": ("과", "와"), "으로로": ("으로", "로")}[pair]
    last = ""
    for ch in reversed(w):
        if ch.isalnum():
            last = ch
            break
    if "가" <= last <= "힣":
        jong = (ord(last) - 0xAC00) % 28
        if pair == "으로로" and jong == 8:
            return w + forms[1]
        return w + (forms[0] if jong else forms[1])
    if last.isdigit():
        has = last in "013678"
        if pair == "으로로" and last in "18":
            return w + forms[1]
        return w + (forms[0] if has else forms[1])
    return w + forms[1]


def aliases():
    global _aliases_cache
    if _aliases_cache is None:
        _aliases_cache = read_json(config.ALIASES_JSON)
    return _aliases_cache


def _today():
    return datetime.date.today()


# ====================================================================== 목적(need)

def make_need(subject=None, region=None, biz=None, who="all", units=None, focus=None, cross=False,
              text="", source="slots", notes=None, cat=None, pub=None):
    """목적을 한 모양으로 정리한다. LLM 이 푼 결과도, 세 칸 선택도, 낱말 사전으로 푼 문장도 이 함수로 모은다.

    subject: "spend"(업종별 카드 소비) 또는 "visit"(방문·유동인구). 비우면 biz 가 있으면 spend, 없으면 visit.
    region:  지역 이름(법정동·행정동·7개 권역·시). 비우면 도 전체.
    biz:     업종코드(I56111), 업종명(한식 음식점업), 또는 사전에 있는 낱말(카페).
    who:     all | resident | domestic | foreign
    units:   더 보고 싶은 단위 열쇠 목록. dow, slot, age, sex, user_type, nationality
    focus:   눈여겨볼 값. {"age": ["20대"], "dow": ["토요일"], "slot": ["점심"]}
    cross:   요일과 시간대를 한 표에서 함께 봐야 하면 True
    """
    cat = cat or catalog_mod.get()
    pub = pub or public_mod.get()
    notes = list(notes or [])
    units = [u for u in (units or []) if u]
    focus = dict((k, list(v)) for k, v in (focus or {}).items() if v)
    who = who if who in WHO_LABELS else "all"

    biz_detail = None
    if biz:
        biz_detail = resolve_biz(biz, cat)
        if biz_detail is None:
            notes.append("업종 '%s'%s 42개 업종에서 찾지 못했다" % (biz, kj(biz, "을")))
    if subject not in SUBJECT_LABELS:
        subject = "spend" if biz_detail else "visit"
    if subject == "visit":
        biz_detail = None

    reg = cat.resolve_region(region or "")
    if reg["level"] in ("all", "city", "district7"):
        # 명세서의 지역 이름(법정동·행정동)이 아닌 리·해변·명소 이름은 그것이 속한 읍면동으로 읽고, 그렇게 읽었다고 알린다.
        place = _place_hit(region or "")
        if place:
            reg2 = cat.resolve_region(place["region"])
            if reg2["level"] in ("bjd", "emd"):
                reg = reg2
                msg = "'%s'%s 명세서의 지역 이름이 아니라서 그것이 속한 '%s'%s 읽었다(%s). 리·명소 단위 데이터는 명세서에 없다" % (
                    place["word"], kj(place["word"], "은"), place["region"], kj(place["region"], "로"), place["basis"])
                if msg not in notes:
                    notes.append(msg)
    keys = []
    if reg["level"] in ("bjd", "emd"):
        keys.append("dong")
    elif reg["level"] == "district7":
        keys.append("district7")
    elif reg["level"] == "city":
        keys.append("city")
    if biz_detail:
        keys.append("biz")
    if who != "all":
        keys.append("who")
    for k in focus:
        if k in ("dow", "slot", "age") and k not in units:
            units.append(k)
    order = ["dow", "slot", "age", "sex", "user_type", "nationality"]
    for k in order:
        if k in units and k not in keys:
            keys.append(k)
    if cross and "dow" in keys and "slot" in keys:
        keys.append("dow_x_slot")
    else:
        cross = False

    pub_units = public_units(subject, who, pub)
    unit_rows = []
    for k in keys:
        if k == "who":
            label = WHO_LABELS[who] + " 구분"
        else:
            label = cat.unit_label(k)
        unit_rows.append({"key": k, "label": label, "in_public": k in pub_units})
    return {
        "region": reg["text"] or "도 전체",
        "biz": biz_detail["name"] if biz_detail else "",
        "who": WHO_LABELS[who],
        "units": unit_rows,
        "subject": subject,
        "subject_label": SUBJECT_LABELS[subject],
        "who_key": who,
        "region_detail": reg,
        "biz_detail": biz_detail,
        "focus": focus,
        "cross": bool(cross),
        "center_needed": any(not u["in_public"] for u in unit_rows),
        "text": text or "",
        "source": source,
        "notes": notes,
    }


def public_units(subject, who, pub=None):
    """공개 데이터가 이 목적에서 채워 주는 단위 열쇠."""
    pub = pub or public_mod.get()
    if subject == "spend":
        if not pub.d1048.ok:
            return set()
        return set(["month", "biz", "user_type", "sex", "who"])
    if who == "foreign" or who == "resident" or not pub.d520.ok:
        return set()
    return set(["month", "sex", "age", "slot", "who"])


def resolve_biz(word, cat=None):
    """업종코드·업종명·사전 낱말 → {"code", "name", "alias", "basis", "pos"}. 못 찾으면 None."""
    cat = cat or catalog_mod.get()
    w = (word or "").strip()
    if w.startswith("biz:"):
        w = w[4:]
    code, alias, basis = None, None, ""
    if cat.biz_name(w):
        code = w
    elif cat.biz_code(w):
        code = cat.biz_code(w)
    else:
        hit = _alias_hit(w)
        if hit:
            code, alias, basis = hit["biz_cd"], hit["word"], hit["basis"]
    if not code or not cat.biz_name(code):
        return None
    return {"code": code, "name": cat.biz_name(code), "alias": alias, "basis": basis,
            "pos": aliases()["card_to_pos"].get(code)}


def _place_hit(text):
    """문장에서 리·해변·명소 이름을 찾는다(가장 긴 낱말). {"word", "region", "basis"} 또는 None."""
    best = None
    for row in aliases().get("places", []):
        for w in row["words"]:
            if w in text and (best is None or len(w) > len(best["word"])):
                best = {"word": w, "region": row["region"], "basis": row["basis"]}
    return best


def out_of_scope(text):
    """이 프로그램이 주문서를 짝짓지 못하는 주제(버스·교통량·기업·와이파이·날씨 등)의 문장인가.

    주문서를 내는 범위는 카드·포스 소비 계열과 방문·유동인구 계열뿐이다. 그 밖의 주제를 억지로 카드 데이터에
    짝짓지 않으려고, 낱말로 주제를 가려 명세서의 해당 계열 데이터 이름만 안내한다.
    돌려주는 값: {"subject", "label", "word", "datasets": [{"dataset_no", "dataset_name"}]} 또는 None.
    """
    t = text or ""
    if not t.strip():
        return None
    cat = catalog_mod.get()
    for row in aliases().get("out_of_scope", []):
        hit = [w for w in row["words"] if w.lower() in t.lower()]
        if not hit:
            continue
        nos = [no for no in cat.order if cat.subject(no) in row["subjects"]]
        if not nos:
            continue
        return {"subject": row["subjects"][0], "label": row["label"], "word": hit[0],
                "datasets": [{"dataset_no": no, "dataset_name": cat.name(no), "period_text": cat.period_text(no),
                              "export": cat.dataset(no)["export"]} for no in nos]}
    return None


def scope_counts(cat=None):
    """주문서를 내는 범위의 종수: {"spend", "visit", "in_scope", "total", "reanalysis", "text"}"""
    cat = cat or catalog_mod.get()
    n_spend = sum(1 for no in cat.order if cat.subject(no) == "spend")
    n_visit = sum(1 for no in cat.order if cat.subject(no) == "visit")
    total = len(cat.order)
    text = ("주문서를 내는 범위: 카드·포스 소비 계열 %d종과 방문·유동인구 계열 %d종(데이터 %d종 가운데 %d종). "
            "버스·교통량·기업·와이파이·날씨·폐기물·메뉴 같은 나머지 %d종은 주문서를 내지 않고 명세서의 데이터 이름만 안내합니다."
            % (n_spend, n_visit, total, n_spend + n_visit, total - n_spend - n_visit))
    return {"spend": n_spend, "visit": n_visit, "in_scope": n_spend + n_visit, "total": total,
            "reanalysis": len(config.REANALYSIS_DATASETS), "text": text}


def _alias_hit(text):
    """문장에서 가장 긴 업종 낱말을 찾는다."""
    best = None
    for row in aliases()["biz"]:
        for w in row["words"]:
            if w in text and (best is None or len(w) > len(best["word"])):
                best = {"word": w, "biz_cd": row["biz_cd"], "basis": row["basis"]}
    return best


def parse_text(text, cat=None):
    """규칙 모드의 문장 해석: 지역명·업종명·단위 낱말 사전으로 푼다. make_need 에 넘길 인자를 돌려준다."""
    cat = cat or catalog_mod.get()
    al = aliases()
    t = text or ""
    notes = []
    biz = None
    for code, name in cat.biz42():
        if name in t or nospace(name) in nospace(t):
            biz = code
            notes.append("업종명 '%s'%s 문장에 있다" % (name, kj(name, "이")))
            break
    if biz is None:
        hit = _alias_hit(t)
        if hit:
            biz = hit["word"]
            notes.append("낱말 '%s' → %s(%s). 근거: %s" % (hit["word"], cat.biz_name(hit["biz_cd"]), hit["biz_cd"], hit["basis"]))
    who = "all"
    for row in al["who"]:
        if any(w in t for w in row["words"]):
            who = row["key"]
            break
    units = []
    for row in al["units"]:
        if any(w in t for w in row["words"]):
            for u in row["unit"].split("+"):
                if u not in units:
                    units.append(u)
    focus = {}
    for unit, table in al["focus"].items():
        for key, words in table.items():
            if any(w in t for w in words):
                focus.setdefault(unit, []).append(key)
    # '저녁 장사'처럼 시간대 낱말만 있고 '요일'이 없으면 요일 단위는 넣지 않는다(위 units 규칙이 이미 그렇게 한다).
    cross = ("dow" in units or "dow" in focus) and ("slot" in units or "slot" in focus) and any(w in t for w in al["cross"])
    subject = "spend" if biz else "visit"
    if subject == "visit" and who == "all" and not any(w in t for w in al["subject_visit"]) and not spend_words(t):
        notes.append("업종 낱말을 찾지 못해 방문·유동인구로 해석했다")
    # 방문 목적에서 '관광객'은 사람을 가리키는 말이라 이용자 범위로 쓴다. 소비 목적에서는 구분이 필요할 때만 쓴다.
    return {"subject": subject, "region": t, "biz": biz, "who": who, "units": units, "focus": focus,
            "cross": cross, "text": t, "source": "text", "notes": notes}


def text_supports_unit(text, unit):
    """문장에 그 단위(요일·시간대)를 가리키는 말이 있는가. 낱말 사전의 단위 낱말과 눈여겨볼 값 낱말로 본다.

    LLM 이 문장에 없는 요일·시간대 단위를 넣어 받을 데이터를 늘리지 못하게 하는 데 쓴다. 다른 단위는 늘 True.
    """
    if unit not in ("dow", "slot"):
        return True
    al = aliases()
    words = []
    for row in al["units"]:
        if unit in row["unit"].split("+"):
            words.extend(row["words"])
    for vals in al["focus"].get(unit, {}).values():
        words.extend(vals)
    if unit == "slot":
        words.extend(["시간", "새벽", "낮", "야간"])
    t = text or ""
    return any(w in t for w in words)


def spend_words(text):
    """문장에서 소비·매출을 가리키는 낱말(매출, 장사, 차리다, ○○집 등)을 찾는다."""
    t = text or ""
    return [w for w in aliases().get("subject_spend", []) if w in t]


def warnings_for(need, text="", raw_region=""):
    """주문서 위에 띄울 경고: 지역이나 업종을 뜻대로 읽지 못해 조용히 넓게 풀린 경우.

    문장으로 넣었을 때(낱말 사전이나 LLM 이 푼 경우)만 낸다. 칸으로 고른 값은 고른 그대로다.
    돌려주는 값: [{"kind": "region|biz|place", "text": 문장, "action": "slots"}]
    """
    out = []
    if need.get("source") not in ("text", "llm"):
        return out
    t = text or need.get("text") or ""
    for n in need.get("notes", []):
        if "명세서의 지역 이름이 아니라서" in n:
            out.append({"kind": "place", "text": n + ". 다른 읍면동을 보려면 '칸으로 고르기'에서 지역을 고르십시오.", "action": "slots"})
    if need["region_detail"]["level"] == "all":
        if raw_region:
            out.append({"kind": "region", "action": "slots",
                        "text": "'%s'%s 명세서의 지역 이름(법정동·행정동·권역·시)에서 찾지 못해 도 전체로 풀었습니다. "
                                "'직접 고르기'에서 읍면동을 골라 다시 만들어 주세요." % (raw_region, kj(raw_region, "을"))})
        elif t.strip():
            out.append({"kind": "region", "action": "slots",
                        "text": "문장에서 명세서의 지역 이름(법정동·행정동·권역·시)을 찾지 못해 도 전체로 풀었습니다. "
                                "마을(리)·해변·관광지 이름은 명세서에 없습니다. 지역을 정하려면 '칸으로 고르기'에서 읍면동을 고르십시오."})
    sw = spend_words(t)
    if need["subject"] == "visit" and sw:
        msg = ("소비·매출을 묻는 말('%s')이 있는데 업종을 42개 업종에서 찾지 못해, 카드 소비가 아니라 방문·유동인구 데이터로 풀었습니다. "
               "매출·소비를 보려면 '칸으로 고르기'의 '무엇을'에서 업종을 골라 다시 만드십시오." % sw[0])
        if "숙박" in t or "펜션" in t or "민박" in t:
            msg += " 숙박은 42개 업종에서 호텔업·여관업·휴양콘도 운영업으로 나뉘어 있어 그 가운데 하나를 고릅니다."
        out.append({"kind": "biz", "text": msg, "action": "slots"})
    return out


def parse_slots(slots):
    """세 칸 선택(누구를·무엇을·어느 시기) + 지역 + 더 볼 단위 → make_need 에 넘길 인자."""
    s = slots or {}
    what = (s.get("what") or "").strip()
    when = (s.get("when") or "month").strip()
    units = [u for u in (s.get("units") or []) if u in ("dow", "slot", "age", "sex", "user_type", "nationality")]
    cross = False
    if when == "dow":
        units.append("dow")
    elif when == "slot":
        units.append("slot")
    elif when == "dow_slot":
        units.extend(["dow", "slot"])
        cross = True
    biz = None
    subject = "visit"
    if what and what != "pop":
        biz = what
        subject = "spend"
    return {"subject": subject, "region": s.get("region") or "", "biz": biz, "who": s.get("who") or "all",
            "units": units, "focus": s.get("focus") or {}, "cross": cross, "text": "", "source": "slots", "notes": []}


# ====================================================================== 후보 평가

def _name_col(cols):
    """코드 컬럼과 이름 컬럼이 함께 있으면 이름 컬럼을 고른다."""
    for c in cols:
        if not c.endswith("_cd"):
            return c
    return cols[0]


def evaluate(need, no, cat=None):
    """데이터 한 종이 목적의 단위를 얼마나 채우는지. 단위 집합의 비교로만 판정한다.

    돌려주는 값: {"dataset_no", "dataset_name", "family", "covers": [열쇠], "missing": [열쇠],
                "missing_notes": {열쇠: 이유}, "value_gaps": [{"unit", "value", "note"}],
                "unit_columns": {열쇠: [컬럼]}, "hard_ok": bool, "extras": n}
    """
    cat = cat or catalog_mod.get()
    units = cat.units(no)
    fam = cat.family(no) or {"who": {}, "measures": [], "caveat": ""}
    reg = need["region_detail"]
    covers, missing, miss_notes, gaps, ucols = [], [], {}, [], {}
    hard_ok = True
    for u in need["units"]:
        k = u["key"]
        if k == "dong":
            if "bjd" in units and reg["bjd"]:
                covers.append(k)
                ucols[k] = [_name_col(units["bjd"]["columns"])]
            elif "emd" in units and reg["emd"]:
                covers.append(k)
                ucols[k] = [_name_col(units["emd"]["columns"])]
            else:
                missing.append(k)
                if "bjd" in units or "emd" in units:
                    have = "법정동" if "bjd" in units else "행정동"
                    miss_notes[k] = "%s 컬럼은 있으나 %s 그 이름 목록에 없다(법정동↔행정동 대응표는 명세서에 없음)" % (
                        have, josa("'%s'" % reg["text"], "이가"))
                elif "district6" in units:
                    miss_notes[k] = "법정동 컬럼이 없다(지역은 6개 권역까지)"
                elif "district7" in units:
                    miss_notes[k] = "읍면동 컬럼이 없다(지역은 7개 권역까지)"
                elif "grid" in units:
                    miss_notes[k] = "읍면동 컬럼이 없다(격자코드뿐이고 좌표표는 명세서에 없음)"
                elif "city" in units:
                    miss_notes[k] = "읍면동 컬럼이 없다(지역은 시 단위까지)"
                else:
                    miss_notes[k] = "지역 컬럼이 없다(도 전체 값)"
        elif k == "district7":
            if "district7" in units:
                covers.append(k)
                ucols[k] = list(units["district7"]["columns"])
            else:
                missing.append(k)
                miss_notes[k] = "7개 권역 컬럼이 없다"
        elif k == "city":
            src = [x for x in ("city", "district7", "emd") if x in units]
            if src:
                covers.append(k)
                ucols[k] = [_name_col(units[src[0]]["columns"])]
            else:
                missing.append(k)
                miss_notes[k] = "시 구분 컬럼이 없다"
        elif k == "biz":
            cond = biz_condition(need, no, cat)
            if cond:
                covers.append(k)
                ucols[k] = [cond["column"]]
            else:
                missing.append(k)
                hard_ok = False
                if "biz" in units:
                    sch = units["biz"].get("scheme")
                    if sch == "golf1":
                        miss_notes[k] = "골프장 운영업 한 업종만 들어 있다"
                    elif sch == "pos":
                        miss_notes[k] = "포스 업종코드표에 '%s' 와 이을 업종을 정해 두지 않았다" % need["biz"]
                    else:
                        miss_notes[k] = "업종 체계가 42개 업종과 다르다"
                else:
                    miss_notes[k] = "업종 컬럼이 없다"
        elif k == "who":
            w = fam["who"].get(need["who_key"])
            if w is not None and (w.get("scope") or not w.get("column") or cat.column(no, w["column"])):
                covers.append(k)
                ucols[k] = [w["column"]] if w.get("column") else []
            else:
                missing.append(k)
                hard_ok = False
                miss_notes[k] = "%s 가를 수 없다" % josa(WHO_LABELS[need["who_key"]], "을를")
        elif k == "dow_x_slot":
            if "dow" in units and "slot" in units:
                covers.append(k)
                ucols[k] = None
            else:
                missing.append(k)
                lack = [cat.unit_label(x) for x in ("dow", "slot") if x not in units]
                miss_notes[k] = "%s 컬럼이 없어 요일과 시간대를 한 표에서 볼 수 없다" % "·".join(lack)
        else:
            if k in units:
                covers.append(k)
                ucols[k] = [_name_col(units[k]["columns"])]
            else:
                missing.append(k)
                miss_notes[k] = "%s 컬럼이 없다" % cat.unit_label(k)
    if need["who_key"] == "all" and "all" not in fam["who"]:
        # 이용자 범위를 정하지 않았는데 이 데이터는 특정 집단만 담고 있다(예: 외국인 유동인구).
        hard_ok = False
        miss_notes["scope"] = "특정 이용자 집단만 담은 데이터다"
    for unit, keys in need["focus"].items():
        if unit not in units:
            continue
        for key in keys:
            f = cat.focus(no, unit, key)
            if f["known"] and not f["separable"]:
                gaps.append({"unit": unit, "value": key, "note": f["note"]})
    # 눈여겨볼 값을 말하지 않은 단위는 dim_tags 의 core_values 를 각각 따로 볼 수 있어야 한다(값 범위의 비교).
    for u in need["units"]:
        k = u["key"]
        core = (cat.tags["need_units"].get(k) or {}).get("core_values")
        if not core or k in need["focus"] or k not in units:
            continue
        for key in core:
            f = cat.focus(no, k, key)
            if f["known"] and not f["separable"]:
                gaps.append({"unit": k, "value": key, "note": f["note"]})
    need_keys = set(u["key"] for u in need["units"])
    raw_needed = set()
    for k in need_keys:
        if k == "dong":
            raw_needed |= set(["bjd", "emd"])
        elif k == "dow_x_slot":
            raw_needed |= set(["dow", "slot"])
        else:
            raw_needed.add(k)
    extras = 0
    for k, info in units.items():
        if k in NOT_EXTRA or k in raw_needed or info.get("derived"):
            continue
        extras += EXTRA_WEIGHT.get(k, 1)
    return {"dataset_no": no, "dataset_name": cat.name(no), "family": cat.family_key(no),
            "covers": covers, "missing": missing, "missing_notes": miss_notes, "value_gaps": gaps,
            "unit_columns": ucols, "hard_ok": hard_ok, "extras": extras}


def biz_condition(need, no, cat=None):
    """이 데이터에서 목적의 업종을 고르는 조건. 고를 수 없으면 None."""
    cat = cat or catalog_mod.get()
    bd = need.get("biz_detail")
    if not bd:
        return None
    info = cat.units(no).get("biz")
    if not info:
        return None
    sch = info.get("scheme")
    if sch in ("card42", "golf1"):
        vals = cat.column_values(no, "biz_cd")
        if vals and bd["code"] in vals["values"]:
            return {"column": "biz_cd", "value": bd["code"], "value_label": bd["name"]}
        return None
    if sch == "pos":
        pos = bd.get("pos")
        if pos and cat.column(no, pos["column"]) and cat.pos_biz(pos["column"], pos["code"]):
            return {"column": pos["column"], "value": pos["code"], "value_label": pos["name"],
                    "note": "카드 업종 '%s' 와 뜻이 가까운 포스 업종으로 이었다(%s)" % (bd["name"], pos["basis"])}
    return None


def _recency(cat, no):
    """최근 달을 받을 수 있는 정도. 끝이 열려 있으면 가장 높다."""
    p = cat.period(no)
    if not p or not cat.month_checkable(no):
        return (0, 0)
    if p["end"] is None:
        return (2, ym_to_int(p["start"]))
    return (1, ym_to_int(p["end"]))


def _linked(no):
    try:
        m = read_json(config.HUB_MAP_JSON)
        return no in m["d1048"]["center_datasets"] or no in m["d520"]["center_datasets"]
    except Exception:
        return False


def _rank_key(cat, ev):
    r = _recency(cat, ev["dataset_no"])
    # 순서: 최근 달을 받을 수 있는가 → 공개 데이터와 값 체계가 같은 계열인가(대조 가능) → 남는 단위가 적은가
    return (-r[0], 0 if _linked(ev["dataset_no"]) else 1, ev["extras"], -r[1], cat.order.index(ev["dataset_no"]))


def decide(need, cat=None, max_downloads=None):
    """목적 → 받을 데이터의 짜임. 단위 집합의 비교로 한 종·타협안·메울 데이터 없음을 가른다.

    돌려주는 값: {"evaluations": {번호: evaluate 결과}, "candidates": [번호], "no_data": [단위 열쇠],
                "mode": "single|compromise|none|not_needed", "choice": [번호…],
                "alternatives": [{"key", "datasets": [번호], "give_up_keys": [...], "give_up": [...], "gain": [...]}],
                "rejected": [{"dataset_no", "reason"}]}
    """
    cat = cat or catalog_mod.get()
    out = {"evaluations": {}, "candidates": [], "no_data": [], "mode": "none", "choice": [],
           "alternatives": [], "rejected": []}
    cands = [no for no in cat.order if cat.subject(no) == need["subject"]]
    evs = dict((no, evaluate(need, no, cat)) for no in cands)
    out["evaluations"] = evs
    out["candidates"] = cands
    if not need["center_needed"]:
        out["mode"] = "not_needed"
        return out
    hard = [no for no in cands if evs[no]["hard_ok"]]
    keys = [u["key"] for u in need["units"]]
    soft = [k for k in keys if k not in ("biz", "who")]
    if not hard:
        out["no_data"] = list(soft)
        for no in cands:
            out["rejected"].append({"dataset_no": no, "reason": _why_not(evs[no], cat)})
        return out
    # 어떤 후보도 갖지 않은 단위 = 메울 데이터 없음
    for k in soft:
        if not any(k in evs[no]["covers"] for no in hard):
            out["no_data"].append(k)
    live = [k for k in soft if k not in out["no_data"]]

    def lacks(no):
        return [k for k in live if k not in evs[no]["covers"]]

    full = [no for no in hard if not lacks(no) and not evs[no]["value_gaps"]]
    if full:
        full.sort(key=lambda no: _rank_key(cat, evs[no]))
        out["mode"] = "single"
        out["choice"] = [full[0]]
        for no in hard:
            if no != full[0]:
                out["rejected"].append({"dataset_no": no, "reason": _why_not(evs[no], cat, lacks(no), full[0])})
        return out

    # 한 종으로 다 채울 수 없다 → 타협안
    out["mode"] = "compromise"
    anchors = [k for k in live if k in REGION_UNITS]
    alts = []
    # (가) 여러 종을 따로 받기: 지역·업종을 가진 데이터들로 나머지 단위를 나눠 채운다.
    # 측정 계열이 같은 데이터끼리만 나눠 받는다(계열이 다르면 표본과 업종·연령 체계가 달라 나란히 놓을 수 없다).
    pool = [no for no in hard if all(a in evs[no]["covers"] for a in anchors)]
    targets = [k for k in live if k not in anchors and k != "dow_x_slot"]

    def gives(no):
        gapped = set(g["unit"] for g in evs[no]["value_gaps"])
        return set(k for k in targets if k in evs[no]["covers"] and k not in gapped)

    def cover(members):
        chosen, left = [], set(targets)
        while left:
            best = None
            for no in members:
                if no in chosen:
                    continue
                gain = len(gives(no) & left)
                if gain == 0:
                    continue
                key = (-gain,) + _rank_key(cat, evs[no])
                if best is None or key < best[0]:
                    best = (key, no)
            if best is None:
                break
            chosen.append(best[1])
            left -= gives(best[1])
        return chosen, left

    best_split = None
    fams = []
    for no in pool:
        if cat.family_key(no) not in fams:
            fams.append(cat.family_key(no))
    for fk in fams:
        chosen, left = cover([no for no in pool if cat.family_key(no) == fk])
        if len(chosen) < 2:
            continue
        key = (len(left), len(chosen), min(_rank_key(cat, evs[no]) for no in chosen))
        if best_split is None or key < best_split[0]:
            best_split = (key, chosen, left)
    if best_split:
        chosen, left = best_split[1], best_split[2]
        chosen.sort(key=lambda no: cat.order.index(no))
        gu_keys, gu = [], []
        if "dow_x_slot" in keys:
            # 교차를 채울 데이터가 아예 없어 '메울 데이터 없음'으로 빠진 경우에도, 이 안이 포기하는 것으로 적는다.
            gu_keys.append("dow_x_slot")
            gu.append("요일과 시간대를 한 표에서 함께 볼 수 없다(따로 받은 두 파일은 서로 곱해 볼 수 없음)")
        for k in sorted(left):
            gu_keys.append(k)
            gu.append("%s: 눈여겨볼 값을 따로 떼어 볼 수 없다" % cat.unit_label(k))
        gain = []
        for no in chosen:
            gain.append("%s: %s" % (no, "·".join(cat.unit_label(k) for k in sorted(gives(no), key=targets.index)) or "-"))
        sep = []
        for unit, vals in need["focus"].items():
            if unit in left:
                continue
            if any(unit in gives(no) for no in chosen):
                sep.extend(vals)
        if sep:
            gain.append("%s 따로 떼어 볼 수 있다" % josa("·".join(sep), "을를"))
        alts.append({"key": "split", "datasets": chosen, "give_up_keys": gu_keys, "give_up": gu, "gain": gain})
    # (나) 한 종으로: 못 채우는 것이 가장 적은 한 종
    def single_cost(no):
        lk = lacks(no)
        cross_miss = 1 if "dow_x_slot" in lk else 0
        return (cross_miss, len(lk) + len(evs[no]["value_gaps"])) + _rank_key(cat, evs[no])

    singles = sorted(hard, key=single_cost)
    if singles:
        s = singles[0]
        gu_keys = list(lacks(s))
        gu = ["%s: %s" % (cat.unit_label(k), evs[s]["missing_notes"].get(k, "컬럼이 없다")) for k in gu_keys]
        if "dow_x_slot" in keys and "dow_x_slot" not in live and "dow_x_slot" not in evs[s]["covers"]:
            gu_keys.append("dow_x_slot")
            gu.append("요일과 시간대를 한 표에서 함께 볼 수 없다")
        for g in evs[s]["value_gaps"]:
            gu_keys.append("%s=%s" % (g["unit"], g["value"]))
            gu.append("%s %s: %s" % (cat.unit_label(g["unit"]), g["value"], g["note"]))
        fam = cat.family(s)
        if fam and fam.get("caveat") and cat.family_key(s) not in [cat.family_key(x) for a in alts for x in a["datasets"]]:
            gu.append(fam["caveat"])
        got = [k for k in live if k in evs[s]["covers"]]
        gain = ["한 파일에서 %s 함께 본다" % josa("·".join(cat.unit_label(k) for k in got if k != "dow_x_slot"), "을를")]
        if "dow_x_slot" in got:
            gain.append("요일과 시간대를 한 표에서 교차해 볼 수 있다")
        if not (alts and alts[0]["datasets"] == [s]):
            alts.append({"key": "single", "datasets": [s], "give_up_keys": gu_keys, "give_up": gu, "gain": gain})
    alts = alts[:config.MAX_ALTERNATIVES]
    # 먼저 권할 안: 교차를 바랐으면 교차를 지키는 안, 아니면 포기가 적은 안(같으면 내려받기가 적은 안).
    def prefer(a):
        keeps_cross = 0 if (need["cross"] and "dow_x_slot" not in a["give_up_keys"]) else 1
        return (keeps_cross if need["cross"] else 0, len(a["give_up_keys"]), len(a["datasets"]))

    if alts:
        best = min(alts, key=prefer)
        for a in alts:
            a["recommended"] = a is best
        out["choice"] = list(best["datasets"])
    out["alternatives"] = alts
    used = set(no for a in alts for no in a["datasets"])
    for no in hard:
        if no not in used:
            out["rejected"].append({"dataset_no": no, "reason": _why_not(evs[no], cat, lacks(no))})
    return out


def _why_not(ev, cat, lacking=None, chosen=None):
    parts = []
    for k in (lacking if lacking is not None else ev["missing"]):
        parts.append(ev["missing_notes"].get(k, "%s 컬럼이 없다" % cat.unit_label(k)))
    if "scope" in ev["missing_notes"]:
        parts.append(ev["missing_notes"]["scope"])
    if lacking is not None:
        for k in ev["missing"]:
            if k in ("biz", "who") and ev["missing_notes"].get(k) not in parts:
                parts.append(ev["missing_notes"][k])
    for g in ev["value_gaps"]:
        parts.append("%s %s: %s" % (cat.unit_label(g["unit"]), g["value"], g["note"]))
    if not parts and chosen:
        r_me, r_ch = _recency(cat, ev["dataset_no"]), _recency(cat, chosen)
        if r_me[0] < r_ch[0]:
            parts.append("수록이 %s 에서 끝난다(%s 는 현재까지)" % (cat.period(ev["dataset_no"])["end"], chosen))
        else:
            parts.append("필요 없는 단위가 더 많아 파일이 커진다(%s 를 골랐다)" % chosen)
    return "; ".join(parts) or "조건에 맞지 않는다"


# ====================================================================== 도구 1·2: 공개 데이터

def public_scope():
    """도구 public_scope: 공개 데이터가 가진 단위와 기간."""
    pub = public_mod.get()
    sc = pub.scope()
    cat = catalog_mod.get()
    # 업종명은 센터 참고표의 표기로 맞춰 보여 준다(허브 실물은 쉼표가 빠진 이름이 있다).
    canon = dict((catalog_mod.biz_key(n), n) for _, n in cat.biz42())
    sc["d1048"]["values"]["biz"] = [canon.get(catalog_mod.biz_key(b), b) for b in sc["d1048"]["values"]["biz"]]
    sc["placeholder"] = pub.placeholder
    if pub.placeholder:
        sc["notice"] = "대체 데이터 사용 중(허브 실물 파일이 data/public/ 에 없어 컬럼만 같은 가짜 값을 쓴다)"
    return sc


def _who_user_types(who):
    fam = catalog_mod.get().tags["families"]["card42"]["who"].get(who) or {}
    return fam.get("values") or None


def public_monthly(source="1048", biz=None, who="all", sex=None, age=None, metric=None):
    """도구 public_monthly: 거른 월별 집계·계절 지수·비중. 수치는 코드가 계산한다.

    source: "1048"(카드, 업종·이용자구분·성별) 또는 "520"(방문 내국인, 성별·연령대·시간)
    돌려주는 값: {"available", "reason", "source", "metric", "series": [{"ym","value"}],
                "seasonal": [{"month","index"}], "season_months": [{"month","role","index"}],
                "shares": [{"title", "rows": [{"label","pct"}]}], "notes": [...]}
    """
    cat = catalog_mod.get()
    pub = public_mod.get()
    src = str(source).replace("d", "")
    notes = []
    if pub.placeholder:
        notes.append("대체 데이터 사용 중: 아래 수치는 실물이 아니라 컬럼만 같은 가짜 값이다.")
    if src == "1048":
        s = pub.d1048
        if not s.ok:
            return _no_public("허브 1048 파일을 읽지 못했다(%s)" % (s.error or "파일 없음"), notes)
        bd = resolve_biz(biz, cat) if biz else None
        if biz and not bd:
            return _no_public("업종 '%s'%s 42개 업종에서 찾지 못했다" % (biz, kj(biz, "을")), notes)
        name = bd["name"] if bd else None
        if name and not pub.has_biz(name):
            return _no_public("허브 1048 에 업종 '%s'%s 없다" % (name, kj(name, "이")), notes)
        m = metric if metric in ("use_amt", "use_cnt", "user_cnt") else "use_amt"
        uts = _who_user_types(who) if who != "all" else None
        sexes = [sex] if sex else None
        series = pub.monthly_1048(name, uts, sexes, m)
        if not series:
            return _no_public("조건에 맞는 줄이 허브 1048 에 없다", notes)
        # 달이 비는 업종은 계절 지수를 내지 않는다(자료가 모자람).
        span = ym_to_int(s.last) - ym_to_int(s.first) + 1
        lacking = len(series) < span
        if lacking:
            si = {"index": [], "years": [], "method": "자료가 모자람"}
            notes.append("자료가 모자람: %s~%s %d달 가운데 %d달만 값이 있어 계절 지수를 내지 않았다." % (s.first, s.last, span, len(series)))
        else:
            si = public_mod.seasonal_index(series)
        shares = []
        sh = pub.shares_1048("user_type", name, m)
        shares.append({"title": "이용자구분별 %s 비중(%s~%s)" % (public_mod.METRIC_LABELS[m], sh["from"], sh["to"]), "rows": sh["rows"]})
        sh = pub.shares_1048("sex", name, m, user_types=uts)
        shares.append({"title": "성별 %s 비중(%s~%s)" % (public_mod.METRIC_LABELS[m], sh["from"], sh["to"]), "rows": sh["rows"]})
        notes.append("도 전체 값이다. 지역·요일·시간대·연령대 구분은 이 공개 데이터에 없다.")
        if any(r["label"] == "알수없음" for r in sh["rows"]):
            notes.append("성별 '알수없음'은 법인과 외국인 이용분이다(허브 1048 에서 법인·외국인은 성별이 모두 '알수없음'으로 들어 있다).")
        if m == "user_cnt":
            notes.append("이용자수는 월별 값이라 여러 달을 더하지 않았다.")
        if not lacking:
            notes.append("계절 지수: %s." % si["method"])
        label = "%s%s" % (name or "전체 업종", (" · " + WHO_LABELS[who]) if who != "all" else "")
        return {"available": True, "reason": "", "source": "허브 1048 업종별 성별 카드 이용 정보(%s~%s) · %s" % (s.first, s.last, label),
                "metric": public_mod.METRIC_LABELS[m], "series": series, "seasonal": si["index"],
                "season_months": public_mod.pick_season_months(si["index"]), "shares": shares, "notes": notes}
    if src == "520":
        s = pub.d520
        if not s.ok:
            return _no_public("허브 520 파일을 읽지 못했다(%s)" % (s.error or "파일 없음"), notes)
        if who == "foreign":
            return _no_public("허브 520 은 내국인 방문객만 센 값이라 외국인에 대해서는 도 전체 값도 낼 수 없다", notes)
        if who == "resident":
            return _no_public("허브 520 은 제주를 방문한 내국인만 센 값이라 도민은 들어 있지 않다", notes)
        ages = [public_mod.age_key(age)] if age else None
        sexes = [sex] if sex else None
        series = pub.monthly_520(sexes, ages)
        if not series:
            return _no_public("조건에 맞는 줄이 허브 520 에 없다", notes)
        split = read_json(config.HUB_MAP_JSON)["d520"]["split"]["ym"]
        after = public_mod.seasonal_index(series, start=split)
        before = public_mod.seasonal_index(series, end=int_to_ym(ym_to_int(split) - 1))
        shares = []
        sh = pub.shares_520("hour", ages, sexes, bins=public_mod.CARD_SLOT_BINS)
        shares.append({"title": "시간대별 방문인구 비중%s(%s~%s, 24시간을 5구간으로 묶음)" % (
            " · " + ages[0] if ages else "", sh["from"], sh["to"]), "rows": sh["rows"]})
        notes.append(_slot_bins_note())
        if not ages:
            sh = pub.shares_520("age", None, sexes)
            shares.append({"title": "연령대별 방문인구 비중(%s~%s)" % (sh["from"], sh["to"]), "rows": sh["rows"]})
        if not sexes:
            sh = pub.shares_520("sex", ages, None)
            shares.append({"title": "성별 방문인구 비중(%s~%s)" % (sh["from"], sh["to"]), "rows": sh["rows"]})
        notes.append("도 전체, 제주를 방문한 내국인만 센 값이다. 사람 수가 아니라 체류량에 가깝다.")
        notes.append("%s 에 통신사가 바뀌어 계절 지수를 앞뒤로 나눠 계산했다. 아래 지수는 %s 이후 구간이다(%s)."
                     % (split, split, after["method"]))
        return {"available": True, "reason": "", "source": "허브 520 제주도 방문 내국인 유동인구 추이(%s~%s)%s" % (
                    s.first, s.last, " · " + ages[0] if ages else ""),
                "metric": "방문인구", "series": series, "seasonal": after["index"],
                "seasonal_before_split": before["index"],
                "season_months": public_mod.pick_season_months(after["index"]), "shares": shares, "notes": notes}
    return _no_public("공개 데이터 '%s'%s 없다(1048, 520 중에서 고른다)" % (source, kj(source, "은")), notes)


def _slot_bins_note():
    return ("시간대 5구간은 길이가 다르다(%s). 구간이 길수록 비중이 크게 나오므로 '밤'의 비중이 크다고 가장 붐빈다는 뜻은 아니다."
            % ", ".join("%s %d시간" % (name.split("(")[0], len(hours)) for name, hours in public_mod.CARD_SLOT_BINS))


def _no_public(reason, notes):
    return {"available": False, "reason": reason, "source": "", "metric": "", "series": [], "seasonal": [],
            "season_months": [], "shares": [], "notes": list(notes)}


def public_answer(need):
    """목적에 맞춰 공개 데이터로 채운 칸(API 의 public_answer)."""
    age = (need["focus"].get("age") or [None])[0]
    if need["subject"] == "spend":
        ans = public_monthly("1048", need["biz_detail"]["code"] if need["biz_detail"] else None, need["who_key"])
        want_slot_age = any(u["key"] in ("slot", "age") for u in need["units"])
        if ans["available"] and want_slot_age and need["who_key"] in ("all", "domestic"):
            ref = public_monthly("520", age=age)
            if ref["available"] and ref["shares"]:
                t = dict(ref["shares"][0])
                t["title"] = "참고(카드 소비가 아니라 방문 내국인 체류량, 허브 520): " + t["title"]
                ans["shares"].append(t)
                ans["notes"].append("참고 표는 방문 내국인의 체류량이라 카드 소비의 시간대와 같지 않다. 업종·지역 구분도 없다. " + _slot_bins_note())
        return ans
    if need["who_key"] == "foreign":
        ans = public_monthly("520", who="foreign")
        ref = public_monthly("520")
        if ref["available"]:
            ans["reference"] = {"title": "참고: 내국인 방문객의 계절 지수(허브 520). 외국인의 흐름과 같다는 근거는 없다.",
                                "seasonal": ref["seasonal"], "season_months": ref["season_months"]}
            ans["notes"] = ans["notes"] + ["내국인 방문객의 계절 지수를 참고값으로만 붙였다."]
        return ans
    return public_monthly("520", who=need["who_key"], age=age)


# ====================================================================== 도구 3: 명세서 검색

def catalog_search(units, subject=None, region=None, biz=None, who="all", focus=None, cross=False, limit=8):
    """도구 catalog_search: 필요한 단위 목록 → 후보 데이터, 각 후보가 채우는 단위와 못 채우는 단위, 수록 기간, 반출.

    units: ["dow", "slot", "age", "sex", "user_type", "nationality"] 가운데 필요한 것
    돌려주는 값: {"need_units": [...], "candidates": [{"dataset_no", "dataset_name", "covers", "missing",
                 "missing_notes", "value_gaps", "period_text", "export", "provider", "full": bool}],
                 "no_data": [단위 이름], "mode", "suggestion": {...}, "rejected": [{"dataset_no","reason"}]}
    """
    cat = catalog_mod.get()
    need = make_need(subject=subject, region=region, biz=biz, who=who, units=units, focus=focus, cross=cross, cat=cat)
    return search_for(need, limit=limit, cat=cat)


def search_for(need, limit=8, cat=None):
    cat = cat or catalog_mod.get()
    d = decide(need, cat)
    evs = d["evaluations"]
    labels = dict((u["key"], u["label"]) for u in need["units"])

    def lab(k):
        return labels.get(k, cat.unit_label(k))

    order = sorted(evs, key=lambda no: (0 if no in d["choice"] else 1, 0 if evs[no]["hard_ok"] else 1,
                                        len(evs[no]["missing"]) + len(evs[no]["value_gaps"])) + _rank_key(cat, evs[no]))
    cands = []
    for no in order[:limit]:
        ev = evs[no]
        ds = cat.dataset(no)
        cands.append({
            "dataset_no": no, "dataset_name": ev["dataset_name"],
            "covers": [lab(k) for k in ev["covers"]],
            "missing": [lab(k) for k in ev["missing"]],
            "missing_notes": [ev["missing_notes"][k] for k in ev["missing"] if k in ev["missing_notes"]],
            "value_gaps": ["%s %s: %s" % (cat.unit_label(g["unit"]), g["value"], g["note"]) for g in ev["value_gaps"]],
            "period_text": cat.period_text(no), "export": ds["export"], "provider": ds["provider"],
            "full": ev["hard_ok"] and not ev["missing"] and not ev["value_gaps"],
            "chosen": no in d["choice"],
        })
    sug = {"mode": d["mode"], "datasets": d["choice"]}
    if d["mode"] == "compromise":
        sug["alternatives"] = [{"key": a["key"], "datasets": a["datasets"], "gain": a["gain"], "give_up": a["give_up"],
                                "recommended": a.get("recommended", False)} for a in d["alternatives"]]
    return {"need_units": [u["label"] for u in need["units"]], "subject": need["subject_label"],
            "candidates": cands, "no_data": [lab(k) for k in d["no_data"]],
            "mode": d["mode"], "suggestion": sug,
            "rejected": [{"dataset_no": r["dataset_no"], "dataset_name": cat.name(r["dataset_no"]), "reason": r["reason"]}
                         for r in d["rejected"]],
            "counted": "%s 가운데 '%s' 계열 %d종을 대조했다" % (cat.label(), need["subject_label"], len(evs))}


# ====================================================================== 도구 4: 달 고르기

def pick_months(dataset_no, biz=None, who="all", subject=None, today=None, count=None, need=None):
    """도구 pick_months: 계절 지수에서 많은 달·적은 달·평균에 가까운 달을 고르고, 연도를 대상 데이터의 수록 범위에 맞춘다.

    돌려주는 값: {"dataset_no", "available", "source", "reference_only", "months": [{"ym", "role", "basis",
                 "index", "reconcilable", "assumed"}], "seasonal": [...], "range": {...}, "notes": [...]}
    """
    cat = catalog_mod.get()
    pub = public_mod.get()
    today = today or _today()
    count = config.MONTHS_PER_ITEM if count is None else count
    out = {"dataset_no": dataset_no, "available": False, "source": "", "reference_only": False, "months": [],
           "seasonal": [], "range": None, "notes": []}
    if not cat.has(dataset_no):
        out["notes"].append("데이터 번호 '%s'%s 명세서에 없다" % (dataset_no, kj(dataset_no, "은")))
        return out
    if need is not None:
        subject = need["subject"]
        who = need["who_key"]
        biz = need["biz_detail"]["code"] if need["biz_detail"] else None
    subject = subject or cat.subject(dataset_no)
    # 계절 지수의 출처
    if subject == "spend":
        ans = public_monthly("1048", biz, who)
        bd = resolve_biz(biz, cat) if biz else None
        what = ("도 전체 %s 이용금액" % bd["name"]) if bd else "도 전체 업종 합계 이용금액"
        if biz and (not ans["available"] or not ans["seasonal"]):
            why = ans["reason"] if not ans["available"] else "이 업종은 달이 비어 계절 지수를 낼 수 없다(자료가 모자람)"
            alt = public_monthly("1048", None, who if ans["available"] else "all")
            if alt["available"] and alt["seasonal"]:
                out["notes"].append("%s. 그래서 전체 업종의 계절 지수로 골랐다." % why.rstrip("."))
                ans = alt
                what = "도 전체 업종 합계 이용금액"
        pub_first, pub_last = pub.d1048.first, pub.d1048.last
        src_name = "허브 1048"
    else:
        ans = public_monthly("520", who="all" if who in ("foreign", "resident") else who)
        pub_first, pub_last = pub.d520.first, pub.d520.last
        what = "도 전체 방문 내국인 체류량"
        src_name = "허브 520"
        if who in ("foreign", "resident"):
            out["reference_only"] = True
            out["notes"].append("공개 데이터에 %s 값이 없어 받을 달을 공개 데이터로 고를 수 없다. 내국인 방문객의 계절 지수를 참고값으로만 썼다."
                                % WHO_LABELS[who])
    if not ans["available"] or not ans["seasonal"]:
        out["notes"].append("공개 데이터로 계절 지수를 계산하지 못했다(%s). 받을 달은 직접 정해야 한다." % (ans["reason"] or "자료가 모자람"))
        return out
    out["source"] = src_name
    out["seasonal"] = ans["seasonal"]
    picks = public_mod.pick_season_months(ans["seasonal"])[:count]
    p = cat.period(dataset_no)
    checkable = cat.month_checkable(dataset_no)
    rng = {"start": p["start"] if p else None, "end": p["end"] if p else None,
           "open_ended": bool(p and p["open_ended"]), "text": cat.period_text(dataset_no)}
    out["range"] = rng
    if not checkable:
        out["notes"].append("%s 의 수록 기간이 명세서에 없어(또는 한 시점 자료라) 달이 범위 안인지 확인하지 못한다." % dataset_no)
    start = ym_to_int(p["start"]) if (checkable and p["start"]) else ym_to_int(pub_first)
    last_done = today.year * 12 + (today.month - 1) - 1          # 지난달
    if checkable and p["end"]:
        end = ym_to_int(p["end"])
        assumed_end = False
    elif checkable:
        end = last_done - (config.OPEN_END_LAG_MONTHS - 1)
        assumed_end = True
    else:
        end = ym_to_int(pub_last)
        assumed_end = False
    ov_lo, ov_hi = max(start, ym_to_int(pub_first)), min(end, ym_to_int(pub_last))

    def latest(month, lo, hi):
        n = hi
        while n >= lo:
            if n % 12 + 1 == month:
                return n
            n -= 1
        return None

    for pk in picks:
        role = pk["role"]
        if out["reference_only"]:
            role = "참고: 내국인 기준 " + role
        n = latest(pk["month"], ov_lo, ov_hi) if ov_lo <= ov_hi else None
        head = "%s의 계절 지수 %s(%d월, %s)." % (what, pk["index"], pk["month"], pk["role"])
        if n is not None:
            # 허브 값이 있는 달이라, 내려받은 뒤 도 전체 값과 견줄 수 있다. 그래서 겹치는 범위 안에서 가장 최근 해로 맞춘다.
            tail = ("%s 수록 범위(%s)와 %s 기간(%s~%s)이 겹치는 범위에서 가장 최근 해로 골랐다(받은 뒤 허브 값과 견줄 수 있는 달). "
                    "더 최근 해가 필요하면 같은 달의 최근 연도를 받는다." % (dataset_no, rng["text"], src_name, pub_first, pub_last))
            out["months"].append({"ym": int_to_ym(n), "role": role, "basis": head + " " + tail, "index": pk["index"],
                                  "season_month": pk["month"], "tail": tail,
                                  "reconcilable": not out["reference_only"], "assumed": False})
            continue
        n = latest(pk["month"], start, end)
        if n is None:
            out["notes"].append("%d월(%s)은 %s 의 수록 범위(%s)에 없어 뺐다." % (pk["month"], pk["role"], dataset_no, rng["text"]))
            continue
        tail = "%s 기간(~%s)과 겹치는 달이 없어 %s 수록 범위(%s) 안에서 가장 최근 해로 골랐다." % (
            src_name, pub_last, dataset_no, rng["text"])
        if assumed_end:
            tail += " '현재까지'의 실제 최신 달은 명세서에 없어 오늘(%s) 기준 %d개월 앞선 달까지 있다고 가정했다. 센터 화면에 그 달이 없으면 수록 범위 안에서 가장 가까운 앞 달을 받는다." % (
                today.strftime("%Y-%m"), config.OPEN_END_LAG_MONTHS)
        out["months"].append({"ym": int_to_ym(n), "role": role, "basis": head + " " + tail, "index": pk["index"],
                              "season_month": pk["month"], "tail": tail,
                              "reconcilable": False, "assumed": assumed_end})
    out["months"].sort(key=lambda m: m["ym"])
    out["available"] = bool(out["months"])
    out["notes"].append("도 전체 기준으로 고른 달이다. 읍면동에서는 많은 달과 적은 달이 다를 수 있다.")
    if rng["open_ended"]:
        out["notes"].append("%s 는 '현재까지' 누적되는 데이터다. 실제 최신 달은 명세서에 없다. 더 최근 해의 같은 달이 센터 화면에 있으면 그 달로 바꿔 받아도 된다." % dataset_no)
    return out


# ====================================================================== 도구 5: 주문서 조립

def value_label(cat, no, col, value):
    """조건 값이 코드일 때 그 뜻(업종 이름). 코드가 아니면 None."""
    if not col or value is None:
        return None
    if col == "biz_cd":
        return cat.biz_name(str(value))
    if col.endswith("_cd"):
        try:
            return cat.pos_biz(col, str(value))
        except (ValueError, KeyError):
            return None
    return None


def _conditions(need, no, ev, cat):
    conds = []
    reg = need["region_detail"]
    units = cat.units(no)

    def add(col, value, label=None, extra=None):
        c = cat.column(no, col)
        e = cat.value_evidence(no, col, value)
        note = e["note"]
        if extra:
            note = note + ". " + extra
        row = {"column": col, "column_ko": c["ko"] if c else "", "value": value,
               "evidence": {"sheet": e["sheet"], "col_no": e["col_no"], "note": note,
                            "verified": bool(e.get("known")) and not e.get("borrowed")}}
        label = label or value_label(cat, no, col, value)
        if label:
            row["value_label"] = label
        conds.append(row)

    if "dong" in ev["covers"]:
        col = ev["unit_columns"]["dong"][0]
        if cat.tag(no, col)["unit"] == "bjd":
            add(col, reg["bjd"])
            if reg["ambiguous"]:
                tip = "'%s'%s %s에 하나씩 있다. 둘 가운데 원하는 쪽 하나만 고른다" % (reg["bjd"], kj(reg["bjd"], "은"), ", ".join(reg["ambiguous"]))
                if "district7" in units:
                    for opt in reg["ambiguous"]:
                        add(units["district7"]["columns"][0], opt, extra=tip)
                elif "city" in units:
                    for opt in reg["ambiguous"]:
                        add(_name_col(units["city"]["columns"]), opt.split(" ")[0], extra=tip)
            elif reg["bjd"] in cat.bjd_ambiguous() and reg["district7"]:
                tip = "'%s'%s 두 곳에 있어 권역을 함께 적는다" % (reg["bjd"], kj(reg["bjd"], "은"))
                if "district7" in units:
                    add(units["district7"]["columns"][0], reg["district7"], extra=tip)
                elif "city" in units:
                    add(_name_col(units["city"]["columns"]), reg["city"], extra=tip)
        else:
            if col == "emd_nm":
                add(col, reg["emd"])
            else:
                add(col, reg["emd"].split(" ", 1)[1])
    if "district7" in ev["covers"] and "dong" not in ev["covers"]:
        add(ev["unit_columns"]["district7"][0], reg["district7"])
    if "city" in ev["covers"] and "dong" not in ev["covers"]:
        col = ev["unit_columns"]["city"][0]
        if cat.tag(no, col)["unit"] == "city":
            add(col, reg["city"])
    if "biz" in ev["covers"]:
        bc = biz_condition(need, no, cat)
        add(bc["column"], bc["value"], bc.get("value_label"), bc.get("note"))
    if "who" in ev["covers"]:
        w = (cat.family(no) or {}).get("who", {}).get(need["who_key"]) or {}
        if w.get("column") and w.get("values"):
            many = len(w["values"]) > 1
            for v in w["values"]:
                add(w["column"], v, extra=("%s%s 이 값들을 함께 고른다: %s" % (WHO_LABELS[need["who_key"]], kj(WHO_LABELS[need["who_key"]], "은"), ", ".join(w["values"]))) if many else None)
    return conds


def _use_columns(need, no, ev, cat, conds):
    fixed = {}
    for c in conds:
        fixed[c["column"]] = fixed.get(c["column"], 0) + 1
    single_fixed = set(k for k, n in fixed.items() if n == 1)
    cols = []
    units = cat.units(no)

    def push(en):
        c = cat.column(no, en)
        if c and not c["partition"] and en not in [x["en"] for x in cols]:
            cols.append({"en": en, "ko": c["ko"]})

    for u in need["units"]:
        k = u["key"]
        if k in ("dong", "district7", "city", "biz"):
            continue
        if k == "dow_x_slot":
            continue
        if k == "who":
            for en in ev["unit_columns"].get("who") or []:
                if en not in single_fixed:
                    push(en)
            continue
        if k in ev["covers"]:
            for en in ev["unit_columns"][k]:
                push(en)
    fam = cat.family(no) or {}
    for en in fam.get("measures", []):
        push(en)
    return cols


def _evidence_lines(need, no, ev, cat):
    lines = []
    p = cat.period(no)
    if p:
        lines.append("수록 기간: %s 설명란 '%s'" % (no, p["raw"]))
    else:
        lines.append("수록 기간: %s 설명란에 기간 표기가 없다" % no)
    seen = set()
    for k in ev["covers"]:
        for en in (ev["unit_columns"].get(k) or []):
            if en in seen:
                continue
            seen.add(en)
            r = cat.tag(no, en)
            c = cat.column(no, en)
            if r and c:
                lines.append("%s: %s %d번 컬럼 %s(%s)%s" % (
                    cat.unit_label(r["unit"]), no, c["n"], en, c["ko"],
                    (", 비고 '%s'" % c["note"].replace("\n", " ")) if c["note"] else
                    ((", 데이터형식 '%s'" % c["example"]) if c["example"] else "")))
    if "dow_x_slot" in ev["covers"]:
        u = cat.units(no)
        lines.append("요일과 시간대: %s 에 %s 와 %s 컬럼이 함께 있다" % (no, u["dow"]["columns"][0], u["slot"]["columns"][0]))
    ds = cat.dataset(no)
    lines.append("제공처: %s 시트 '%s'" % (no, ds["provider"]))
    for fn in ds.get("footnotes", []):
        lines.append("시트 주석: %s" % fn)
    return lines


def _focus_rows(need, no, ev, cat):
    rows = []
    for unit, keys in need["focus"].items():
        if unit not in cat.units(no):
            continue
        for key in keys:
            f = cat.focus(no, unit, key)
            rows.append({"unit": cat.unit_label(unit), "want": key, "column": f["column"], "values": f["values"],
                         "separable": f["separable"], "note": f["note"]})
    return rows


def make_item(need, no, cat=None, today=None, months=None, why=None, month_count=None):
    """주문서 한 항목을 명세서 사실로 채운다."""
    cat = cat or catalog_mod.get()
    ev = evaluate(need, no, cat)
    ds = cat.dataset(no)
    conds = _conditions(need, no, ev, cat)
    pm = pick_months(no, need=need, today=today, count=month_count)
    def mrow(m):
        return {"ym": m["ym"], "role": m["role"], "basis": m["basis"], "index": m["index"],
                "season_month": m["season_month"]}

    if months is None:
        mrows = [mrow(m) for m in pm["months"]]
        used = list(pm["months"])
    else:
        known = dict((m["ym"], m) for m in pm["months"])
        mrows, used = [], []
        for ym in months:
            ym_s = ym.get("ym") if isinstance(ym, dict) else ym
            try:
                ym_n = catalog_mod.norm_ym(ym_s)
            except (ValueError, TypeError):
                ym_n = str(ym_s)
            if ym_n in known:
                mrows.append(mrow(known[ym_n]))
                used.append(known[ym_n])
            else:
                mrows.append({"ym": ym_n, "role": "직접 고른 달", "basis": "계절 지수로 고른 달이 아니다"})
    # 달마다 되풀이되는 근거 문장(수록 범위, 겹치는 범위)은 칸 아래에 한 번만 쓴다.
    months_note = []
    for m in used:
        if m.get("tail") and m["tail"] not in months_note:
            months_note.append(m["tail"])
    if pm["source"] and used:
        months_note.insert(0, "받을 달은 %s 의 1~12월 계절 지수에서 많은 달·적은 달·평균에 가까운 달로 골랐다(도 전체 기준)." % pm["source"])
    fam = cat.family(no) or {}
    item = {
        "dataset_no": no,
        "dataset_name": ds["name"],
        "provider": ds["provider"],
        "period_text": cat.period_text(no),
        "months": mrows,
        "months_note": months_note,
        "conditions": conds,
        "use_columns": _use_columns(need, no, ev, cat, conds),
        "export": cat.export_info(no),
        "downloads": len(mrows),
        "why": why or "",
        "evidence": _evidence_lines(need, no, ev, cat),
        "focus": _focus_rows(need, no, ev, cat),
        "caveat": fam.get("caveat", ""),
        "month_notes": pm["notes"],
        "reanalysis": no in config.REANALYSIS_DATASETS,
    }
    return item


def _base_name(name):
    """'카드 이용 현황(시간대)_v02' → '카드 이용 현황(시간대)'"""
    return name.rsplit("_v", 1)[0] if "_v" in name else name


def sorted_rejected(d, cat):
    """버린 후보를 고른 데이터와 가까운 순서로: 판(v01·v02)만 다른 것, 같은 계열, 빠진 단위가 적은 것."""
    chosen = d["choice"]
    bases = set(_base_name(cat.name(no)) for no in chosen)
    fams = set(cat.family_key(no) for no in chosen)
    evs = d["evaluations"]

    def key(r):
        no = r["dataset_no"]
        ev = evs[no]
        return (0 if _base_name(cat.name(no)) in bases else 1,
                0 if cat.family_key(no) in fams else 1,
                len(ev["missing"]) + len(ev["value_gaps"]),
                cat.order.index(no))

    return sorted(d["rejected"], key=key)


def _why_single(need, no, d, cat):
    ev = d["evaluations"][no]
    nd = set(d["no_data"])
    labels = [cat.unit_label(k) for k in ev["covers"] if k not in ("who", "dow_x_slot")]
    if nd:
        s = "메울 수 있는 단위(%s)를 한 파일에 모두 가진 데이터다. %s 메울 데이터가 없다." % (
            "·".join(labels), josa("·".join(cat.unit_label(k) for k in d["no_data"]), "은는"))
    else:
        s = "필요한 단위(%s)를 한 파일에 모두 가진 데이터다." % "·".join(labels)
    for r in sorted_rejected(d, cat)[:2]:
        s += " %s(%s): %s." % (cat.name(r["dataset_no"]), r["dataset_no"], r["reason"])
    return s


def build_order(need, draft=None, today=None, max_downloads=None, cat=None):
    """도구 submit_order 의 본체: 주문서를 조립하고 검증한다.

    draft 가 없으면 규칙으로 정한다. draft(LLM 초안)가 있으면 그 선택을 따르되, 이름·기간·근거·반출 줄은
    명세서에서 다시 채우고 검증기로 대조한다.
      draft = {"items": [{"dataset_no", "months": ["2024-08"…] (없으면 계절 지수로 고름), "why": "이유 문장",
                          "conditions": [{"column","value"}] (없으면 목적에서 만든다), "use_columns": ["time"…]}],
               "choose": "split" | "single" (타협안 가운데 무엇을 먼저 권할지, 없으면 규칙)}
    돌려주는 값: {"order": 통과한 줄만 남긴 주문서, "validation": {"passed","total","issues":[문장]},
                "issues": [어긋난 줄(자세히)], "ok": bool, "decision": decide 결과}
    """
    cat = cat or catalog_mod.get()
    cap = config.MAX_DOWNLOADS if max_downloads is None else max_downloads
    d = decide(need, cat, cap)
    labels = dict((u["key"], u["label"]) for u in need["units"])
    order = {"items": [], "alternatives": [], "no_data": [], "total_downloads": 0}

    # 메울 데이터 없음
    for k in d["no_data"]:
        order["no_data"].append(_no_data_row(need, k, d, cat, labels))

    def items_for(nos, whys=None):
        n = max(1, len(nos))
        per = config.MONTHS_PER_ITEM
        note = None
        if n * per > cap:
            per = max(1, cap // n)
            note = "내려받기 상한 %d회에 맞추려고 데이터마다 %d달만 받는다(%d종 × %d달)." % (cap, per, n, per)
        out = []
        for no in nos:
            it = make_item(need, no, cat, today, why=(whys or {}).get(no), month_count=per)
            if note:
                it["month_notes"] = [note] + it["month_notes"]
            out.append(it)
        return out

    if draft and draft.get("items"):
        for di in draft["items"]:
            no = di.get("dataset_no")
            if not cat.has(no):
                order["items"].append({"dataset_no": no, "dataset_name": di.get("dataset_name"), "months": [],
                                       "conditions": [], "use_columns": [], "downloads": 0, "why": di.get("why", "")})
                continue
            it = make_item(need, no, cat, today, months=di.get("months") or None, why=di.get("why"))
            if di.get("conditions"):
                rows = []
                for c in di["conditions"]:
                    e = cat.value_evidence(no, c.get("column"), c.get("value"))
                    col = cat.column(no, c.get("column"))
                    crow = {"column": c.get("column"), "column_ko": col["ko"] if col else "", "value": c.get("value"),
                            "evidence": {"sheet": e["sheet"], "col_no": e["col_no"], "note": e["note"],
                                         "verified": bool(e.get("known")) and not e.get("borrowed")}}
                    vl = value_label(cat, no, c.get("column"), c.get("value")) if col else None
                    if vl:
                        crow["value_label"] = vl
                    rows.append(crow)
                it["conditions"] = rows
            if di.get("use_columns"):
                # 받을 항목은 코드가 목적에서 정한 목록을 기준으로 한다. LLM 이 고른 항목은 그 목록에
                # 더하기만 하고 빼지는 못한다(실행마다 항목이 빠져 주문서가 달라지는 것을 막는다).
                rows = list(it.get("use_columns") or [])
                have = set(r.get("en") for r in rows)
                for en in di["use_columns"]:
                    en = en.get("en") if isinstance(en, dict) else en
                    if not en or en in have:
                        continue
                    col = cat.column(no, en)
                    rows.append({"en": en, "ko": col["ko"] if col else ""})
                    have.add(en)
                it["use_columns"] = rows
            order["items"].append(it)
    elif d["mode"] == "single":
        no = d["choice"][0]
        order["items"] = items_for([no], {no: _why_single(need, no, d, cat)})
    elif d["mode"] == "compromise":
        pass

    if d["mode"] == "compromise":
        names = {"split": "여러 종을 따로 받기", "single": "한 종으로 함께 보기"}
        want = (draft or {}).get("choose")
        alts = d["alternatives"]
        if want in ("split", "single") and any(a["key"] == want for a in alts):
            for a in alts:
                a["recommended"] = a["key"] == want
        for i, a in enumerate(alts):
            its = items_for(a["datasets"])
            for it in its:
                it["why"] = it["why"] or "타협안 '%s' 의 항목" % names[a["key"]]
            title = "%s안: %s (%s)" % ("가나"[i], names[a["key"]], " + ".join(a["datasets"]))
            calc = "%d종 × %s달 = %d회" % (len(its), "·".join(str(it["downloads"]) for it in its) if len(set(it["downloads"] for it in its)) > 1 else str(its[0]["downloads"] if its else 0),
                                        sum(it["downloads"] for it in its))
            order["alternatives"].append({
                "title": title, "key": a["key"], "items": its,
                "downloads": sum(it["downloads"] for it in its), "downloads_calc": calc,
                "gain": a["gain"], "give_up": a["give_up"], "give_up_keys": a["give_up_keys"],
                "recommended": bool(a.get("recommended")),
            })
        if not (draft and draft.get("items")):
            rec = [a for a in order["alternatives"] if a["recommended"]]
            if rec:
                r = rec[0]
                order["items"] = [dict(it) for it in r["items"]]
                for it in order["items"]:
                    it["why"] = "한 데이터로는 필요한 단위를 다 채울 수 없어 %d개 안을 비교했다. 먼저 권하는 안은 '%s'. 얻는 것: %s. 포기하는 것: %s." % (
                        len(order["alternatives"]), r["title"], " / ".join(x.rstrip(".") for x in r["gain"]) or "-",
                        " / ".join(x.rstrip(".") for x in r["give_up"]) or "없음")
    order["total_downloads"] = sum(it.get("downloads", 0) for it in order["items"])
    if d["mode"] == "not_needed":
        order["note"] = "필요한 단위가 모두 공개 데이터에 있어 센터에서 받을 것이 없다."

    result = validator.validate(order, need, cat, cap)
    cleaned = validator.strip_failed(order, result["issues"])
    return {"order": cleaned, "validation": validator.summary(result), "issues": result["issues"],
            "ok": not result["issues"], "decision": d}


def _no_data_row(need, k, d, cat, labels):
    """메울 데이터가 없는 단위 한 줄: 설명과 대신 가능한 것."""
    label = labels.get(k, cat.unit_label(k))
    evs = d["evaluations"]
    hard = [no for no in evs if evs[no]["hard_ok"]]
    if need["who_key"] != "all":
        scope = (need["biz"] + " · " if need["biz"] else "") + need["who"]
    else:
        scope = need["biz"] or need["subject_label"]
    if k == "dow_x_slot":
        explain = "%s 담은 데이터 %d종(%s) 가운데 요일과 시간대를 한 표에 함께 가진 것이 없다." % (
            josa(scope, "을를"), len(hard), ", ".join(hard) or "-")
    else:
        explain = "%s 담은 데이터 %d종(%s) 가운데 %s 컬럼을 가진 것이 없다." % (
            josa(scope, "을를"), len(hard), ", ".join(hard) or "-", label)
    if k == "dong":
        named = [evs[no]["missing_notes"].get(k, "") for no in hard if "이름 목록에 없다" in evs[no]["missing_notes"].get(k, "")]
        if named:
            explain = "%s 담은 데이터 %d종(%s) 가운데 %s 고를 수 있는 것이 없다. %s." % (
                josa(scope, "을를"), len(hard), ", ".join(hard), josa("'%s'" % need["region"], "을를"), named[0])
    instead = []
    # 같은 데이터로 가능한 것: 고를 데이터가 가진 같은 축의 다른 단위
    axis = cat.unit_axis(k)
    need_keys = set(u["key"] for u in need["units"])
    live = [u["key"] for u in need["units"] if u["key"] not in d["no_data"] and u["key"] not in ("biz", "who")]
    best = [no for no in hard if all(x in evs[no]["covers"] for x in live)]
    best.sort(key=lambda no: _rank_key(cat, evs[no]))
    for no in best[:1]:
        for uk, info in cat.units(no).items():
            if cat.unit_axis(uk) != axis or uk in need_keys or info.get("derived") or uk in ("month", "day"):
                continue
            col = _name_col(info["columns"])
            c = cat.column(no, col)
            instead.append("같은 데이터로 가능한 것: %s 대신 %s 나눠 본다(%s %d번 컬럼 %s%s)" % (
                label, josa(cat.unit_label(uk), "으로로"), no, c["n"], col, (", 비고 '%s'" % c["note"]) if c["note"] else ""))
    # 다른 측정값: 그 단위와 이용자 범위를 함께 가진 다른 계열(계열마다 한 종)
    if need["who_key"] != "all":
        seen_fam = []
        for no in sorted(cat.order, key=lambda n: (-_recency(cat, n)[0], cat.order.index(n))):
            fk = cat.family_key(no)
            if cat.subject(no) == need["subject"] or fk in seen_fam:
                continue
            fam = cat.family(no) or {}
            w = fam.get("who", {}).get(need["who_key"])
            if k not in cat.units(no) or w is None:
                continue
            if (cat.units(no).get("biz") or {}).get("scheme") == "golf1":
                continue
            if not (w.get("scope") or (w.get("column") and cat.column(no, w["column"]))):
                continue
            seen_fam.append(fk)
            instead.append("다른 측정값: %s(%s)에는 %s 컬럼이 있다. 다만 %s %s 줄에 %s 값이 채워져 있는지는 명세서만으로 알 수 없다." % (
                cat.name(no), no, label, fam.get("caveat") or "측정값이 다르다.", need["who"], label))
            if len(seen_fam) >= 2:
                break
        other = [no for no in evs if not evs[no]["hard_ok"] and k in cat.units(no)]
        other.sort(key=lambda no: _rank_key(cat, evs[no]))
        if other:
            no = other[0]
            instead.append("이용자 범위가 다른 데이터: %s(%s)에는 %s 컬럼이 있으나 %s" % (
                cat.name(no), no, label, evs[no]["missing_notes"].get("who") or evs[no]["missing_notes"].get("scope") or "이용자 범위가 다르다"))
    return {"unit": label, "key": k, "explain": explain, "instead": instead}


# ====================================================================== 응답 조립

def gaps_for(need, order, decision, cat=None):
    cat = cat or catalog_mod.get()
    rows = []
    nd = set(decision["no_data"])
    for u in need["units"]:
        if u["in_public"]:
            continue
        if u["key"] in nd:
            status = "메울 데이터 없음"
        elif not order["items"]:
            status = "메울 데이터 없음"
        else:
            status = "센터 데이터로 메움"
        row = {"key": u["key"], "label": u["label"], "status": status}
        if status == "센터 데이터로 메움":
            by = []
            for it in order["items"]:
                ev = decision["evaluations"].get(it["dataset_no"])
                if ev and u["key"] in ev["covers"]:
                    by.append(it["dataset_no"])
                    for g in ev["value_gaps"]:
                        if g["unit"] == u["key"]:
                            row["note"] = "%s: %s" % (g["value"], g["note"])
            row["by"] = by
            if not by:
                row["note"] = "먼저 권한 안에서는 포기한 단위다(다른 안에서 채워진다)"
        rows.append(row)
    return rows


def limits_for(need, order, pub=None, cat=None, mode_used="rule"):
    cat = cat or catalog_mod.get()
    pub = pub or public_mod.get()
    out = []
    if pub.placeholder:
        out.append("대체 데이터 사용 중: 허브 공개 데이터 실물이 없어 컬럼만 같은 가짜 값으로 계산했다. 실물을 data/public/ 에 넣으면 실물로 계산한다.")
    out.append("받을 달은 도 전체 공개 데이터의 계절 지수로 골랐다. 읍면동에서는 많은 달과 적은 달이 다를 수 있다.")
    if any("현재까지" in (it.get("period_text") or "") for it in order["items"]):
        out.append("'현재까지'인 데이터의 실제 최신 달은 명세서에 없다.")
    seen = set()
    for it in order["items"]:
        cv = it.get("caveat")
        if cv and cv not in seen:
            seen.add(cv)
            out.append("%s: %s" % (it["dataset_no"], cv))
    if need["subject"] == "spend":
        out.append("허브 1048 과 센터 카드 데이터가 같은 원천이라는 것은 추정이다(끝 달과 값 체계가 같다는 정황). 그래서 대조 줄은 '차이'로만 적는다.")
    if mode_used == "rule":
        out.append("규칙 모드(축소 모드): 문장은 낱말 사전으로 풀었고 타협안은 정해진 규칙으로 골랐다. 문장 해석·타협 선택·이유 설명은 LLM 모드에만 있다.")
    if not need["units"]:
        out.append("목적에서 지역·업종·더 볼 단위를 찾지 못해 도 전체 흐름만 냈다. 지역 이름이나 업종을 넣어 다시 해 본다.")
    sc = scope_counts(cat)
    out.append("주문서를 내는 범위는 카드·포스 소비 계열 %d종과 방문·유동인구 계열 %d종이다(데이터 %d종 가운데 %d종). 나머지 %d종은 데이터 이름만 안내한다."
               % (sc["spend"], sc["visit"], sc["total"], sc["in_scope"], sc["total"] - sc["in_scope"]))
    out.append("재분석(내려받은 CSV 로 다시 돌리기)은 카드 v02 계열 3종(1-08, 1-12, 1-14)만 지원한다.")
    return out


def compose_response(need, built, trace=None, mode_used="rule", fallback_reason="", sent_to_llm=None, run_id=None,
                     raw_region=""):
    """/api/plan 응답 모양으로 묶는다. built 는 build_order 의 결과.

    raw_region: LLM 이 적어 낸 지역 낱말(명세서에서 못 찾았을 때 경고에 쓴다).
    """
    cat = catalog_mod.get()
    pub = public_mod.get()
    order = built["order"]
    api_need = dict(need)
    warnings = warnings_for(need, need.get("text", ""), raw_region)
    v = built["validation"]
    if v.get("total") and v.get("passed") != v.get("total"):
        warnings.append({"kind": "validation", "action": "",
                         "text": "검증에서 어긋난 줄이 %d개 있습니다. 아래 주문서는 통과한 줄만 남긴 것이라 목적을 다 채우지 못할 수 있습니다. 어긋난 줄을 확인하십시오."
                                 % (v["total"] - v["passed"])})
    return {
        "warnings": warnings,
        "run_id": run_id or uuid.uuid4().hex[:12],
        "mode_used": mode_used,
        "fallback_reason": fallback_reason or "",
        "need": api_need,
        "public_answer": public_answer(need),
        "gaps": gaps_for(need, order, built["decision"], cat),
        "order": order,
        "validation": built["validation"],
        "trace": trace or [],
        "sent_to_llm": sent_to_llm or {"purpose_text": False, "public_aggregates": 0, "spec_items": 0, "center_values": 0},
        "limits": limits_for(need, order, pub, cat, mode_used),
        "spec_label": cat.label(),
        "placeholder": pub.placeholder,
    }


def out_of_scope_response(text, hit, run_id=None, fallback_reason="", sent_to_llm=None):
    """짝짓지 못하는 주제의 응답: 주문서를 내지 않고, 명세서의 해당 계열 데이터 이름만 안내한다."""
    cat = catalog_mod.get()
    pub = public_mod.get()
    sc = scope_counts(cat)
    need = make_need(subject="visit", text=text, source="text", cat=cat,
                     notes=["낱말 '%s' → '%s' 주제. 이 프로그램이 주문서를 내는 범위 밖이다" % (hit["word"], hit["label"])])
    need["subject"] = "out_of_scope"
    need["subject_label"] = hit["label"]
    need["units"] = []
    need["center_needed"] = False
    message = ("이 문장은 '%s' 주제로 읽혔습니다. 이 프로그램은 카드·포스 소비 계열 %d종과 방문·유동인구 계열 %d종만 주문서로 짝짓습니다. "
               "이 주제는 짝짓지 못하므로 주문서를 내지 않았습니다. 명세서에 있는 해당 계열 데이터의 이름만 아래에 적습니다."
               % (hit["label"], sc["spend"], sc["visit"]))
    order = {"items": [], "alternatives": [], "no_data": [], "total_downloads": 0,
             "out_of_scope": {"topic": hit["label"], "word": hit["word"], "message": message, "datasets": hit["datasets"]}}
    names = ", ".join("%s %s" % (d["dataset_no"], d["dataset_name"]) for d in hit["datasets"])
    trace = [
        {"step": 1, "tool": "해석", "args_summary": "문장을 낱말 사전으로 풂",
         "result_summary": "주제: %s (낱말 '%s')" % (hit["label"], hit["word"]), "note": ""},
        {"step": 2, "tool": "범위 판정", "args_summary": "주문서를 내는 범위: 카드·포스 소비 %d종, 방문·유동인구 %d종" % (sc["spend"], sc["visit"]),
         "result_summary": "범위 밖 → 주문서를 내지 않음", "note": "명세서의 해당 계열 데이터 %d종: %s" % (len(hit["datasets"]), names)},
    ]
    pa = _no_public("이 주제는 동봉한 허브 공개 데이터(1048 카드, 520 방문 내국인)에 없다", [])
    return {
        "warnings": [],
        "run_id": run_id or uuid.uuid4().hex[:12],
        "mode_used": "rule",
        "fallback_reason": fallback_reason or "",
        "need": need,
        "public_answer": pa,
        "gaps": [],
        "order": order,
        "validation": {"passed": 0, "total": 0, "issues": [], "breakdown": {}, "breakdown_text": "", "unverified": 0,
                       "unverified_notes": []},
        "trace": trace,
        "sent_to_llm": sent_to_llm or {"purpose_text": False, "public_aggregates": 0, "spec_items": 0, "center_values": 0},
        "limits": [sc["text"], "주제는 문장에 든 낱말로 가렸다(코드의 낱말 사전). 뜻과 다르게 읽혔으면 '칸으로 고르기'에서 골라 다시 만든다."],
        "spec_label": cat.label(),
        "placeholder": pub.placeholder,
    }


def plan_rule(slots=None, text=None, today=None, run_id=None, fallback_reason="", sent_to_llm=None):
    """규칙 모드: 세 칸 선택(slots) 또는 문장(text)으로 /api/plan 응답을 만든다. LLM 을 부르지 않는다.

    sent_to_llm: LLM 을 몇 번 부른 뒤 실패해 이리로 넘어온 경우, 그때까지 보낸 내용(없으면 보낸 것이 없다).
    """
    cat = catalog_mod.get()
    trace = []

    def step(tool, args, result, note="", sub=False):
        row = {"step": len(trace) + 1, "tool": tool, "args_summary": args, "result_summary": result, "note": note}
        if sub:
            row["sub"] = True
        trace.append(row)

    use_text = bool(text and text.strip()) and not _has_slots(slots)
    if use_text:
        hit = out_of_scope(text)
        if hit:
            return out_of_scope_response(text, hit, run_id, fallback_reason, sent_to_llm)
        args = parse_text(text, cat)
    else:
        args = parse_slots(slots)
        if text and text.strip():
            args["text"] = text
    need = make_need(cat=cat, **args)
    unit_names = "·".join(u["label"] for u in need["units"]) or "월별 흐름만"
    step("해석", "문장을 낱말 사전으로 풂" if use_text else "세 칸 선택",
         "무엇을: %s%s / 누구를: %s / 지역: %s / 필요한 단위: %s" % (
             need["subject_label"], (" · " + need["biz"]) if need["biz"] else "", need["who"], need["region"], unit_names),
         " / ".join(need["notes"]))
    if need["region_detail"]["ambiguous"]:
        step("해석", need["region_detail"]["bjd"], "같은 이름의 법정동이 두 곳: %s" % ", ".join(need["region_detail"]["ambiguous"]),
             "조건에 권역을 함께 적는다")

    sc = public_scope()
    in_pub = [u["label"] for u in need["units"] if u["in_public"]]
    not_pub = [u["label"] for u in need["units"] if not u["in_public"]]
    step("public_scope", "", "1048: %s~%s %d줄 / 520: %s~%s %d줄%s" % (
        sc["d1048"]["period"]["first"], sc["d1048"]["period"]["last"], public_mod.get().d1048.rows,
        sc["d520"]["period"]["first"], sc["d520"]["period"]["last"], public_mod.get().d520.rows,
        " (대체 데이터)" if sc["placeholder"] else ""),
         "공개 데이터에 있는 단위: %s / 없는 단위: %s" % ("·".join(in_pub) or "없음", "·".join(not_pub) or "없음"))

    pa = public_answer(need)
    if pa["available"]:
        sm = pa.get("season_months") or []
        step("public_monthly", pa["source"], "%s 월별 %d달, 계절 지수 %s" % (
            pa["metric"], len(pa["series"]), ", ".join("%s %d월(%s)" % (m["role"], m["month"], m["index"]) for m in sm) or "내지 않음(자료가 모자람)"), "")
    else:
        step("public_monthly", "", "공개 데이터로 답할 수 없음", pa["reason"])

    built = build_order(need, today=today, cat=cat)
    d = built["decision"]
    hard = [no for no in d["candidates"] if d["evaluations"][no]["hard_ok"]]
    if d["mode"] == "single":
        res = "후보 %d종 가운데 %s 단위를 모두 가진 데이터: %s %s" % (
            len(hard), "메울 수 있는" if built["order"]["no_data"] else "필요한", d["choice"][0], cat.name(d["choice"][0]))
    elif d["mode"] == "compromise":
        res = "후보 %d종 가운데 한 데이터로 필요한 단위를 다 채우는 것이 없다 → 타협안 %d개를 비교" % (len(hard), len(d["alternatives"]))
    elif d["mode"] == "not_needed":
        res = "필요한 단위가 모두 공개 데이터에 있어 센터 데이터를 고르지 않았다"
    else:
        res = "조건에 맞는 데이터가 없다"
    step("catalog_search", "필요한 단위: %s / 대조한 데이터 %d종(%s 계열)" % (unit_names, len(d["candidates"]), need["subject_label"]), res,
         ("메울 데이터 없음: %s" % "·".join(x["unit"] for x in built["order"]["no_data"])) if built["order"]["no_data"] else "")
    shown = 0
    rej = sorted_rejected(d, cat)
    for r in rej:
        if shown >= 6:
            break
        step("catalog_search", "%s %s" % (r["dataset_no"], cat.name(r["dataset_no"])), "버림", r["reason"], sub=True)
        shown += 1
    if len(rej) > shown:
        step("catalog_search", "그 밖의 후보 %d종" % (len(rej) - shown), "버림", "필요한 단위가 더 많이 빠진다", sub=True)
    for a in built["order"]["alternatives"]:
        step("타협안 비교", a["title"], "내려받기 %s%s" % (a["downloads_calc"], " · 먼저 권함" if a["recommended"] else ""),
             "얻는 것: %s / 포기하는 것: %s" % (" · ".join(a["gain"]) or "-", " · ".join(a["give_up"]) or "없음"), sub=True)
    for nd in built["order"]["no_data"]:
        step("없음 판정", nd["unit"], "메울 데이터 없음", nd["explain"])
    for it in built["order"]["items"]:
        step("pick_months", it["dataset_no"], ", ".join("%s(%s)" % (m["ym"], m["role"]) for m in it["months"]) or "고르지 못함",
             "수록 범위: %s" % it["period_text"])
    v = built["validation"]
    step("submit_order", "주문 항목 %d개, 내려받기 %d회" % (len(built["order"]["items"]), built["order"]["total_downloads"]),
         "검증 통과 %d/%d" % (v["passed"], v["total"]), " / ".join(v["issues"]))
    return compose_response(need, built, trace, "rule", fallback_reason, sent_to_llm, run_id)


def _has_slots(slots):
    s = slots or {}
    return bool(s.get("what") or s.get("region") or s.get("units") or (s.get("who") and s.get("who") != "all")
                or (s.get("when") and s.get("when") != "month"))


BIZ_GROUPS = (("I56", "음식점·주점·카페"), ("I55", "숙박"), ("G47", "소매(마트·편의점·주유소 등)"),
              ("H", "운송"), ("L", "임대(렌터카 등)"), ("N", "여행·행사"), ("R", "스포츠·오락"), ("S", "욕탕·마사지"))


def options():
    """/api/options 응답."""
    cat = catalog_mod.get()
    everyday = dict((row["biz_cd"], row["words"][0]) for row in aliases()["biz"])
    what = [{"key": "pop", "label": "방문·유동인구", "group": "방문"}]
    for code, name in cat.biz42():
        group = "기타"
        for prefix, label in BIZ_GROUPS:
            if code.startswith(prefix):
                group = label
                break
        word = everyday.get(code)
        # 일상어가 업종 이름에 이미 들어 있으면(면세점, 슈퍼마켓) 되풀이하지 않는다.
        shown = name if (not word or word in name.replace(" ", "")) else "%s (%s)" % (name, word)
        what.append({"key": "biz:" + code, "label": shown, "group": group})
    return {
        "who": [{"key": k, "label": WHO_LABELS[k]} for k in ("all", "resident", "domestic", "foreign")],
        "what": what,
        "when": [{"key": k, "label": v} for k, v in WHEN_OPTIONS],
        "regions": {"bjd": cat.bjd_list(), "emd": cat.emd_list(), "district7": cat.district7_list()},
        "units": [{"key": k, "label": cat.unit_label(k)} for k in EXTRA_UNITS],
        "focus": dict((unit, list(table.keys())) for unit, table in aliases()["focus"].items()),
        "examples": [{"title": e["title"], "text": e["text"], "slots": e["slots"]} for e in EXAMPLES],
        "scope": scope_counts(cat),
    }
