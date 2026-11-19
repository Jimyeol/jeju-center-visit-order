"""동네 미리 보기: 공개 Open API 세 가지를 불러 받을 목록 옆에 보여 준다.

- 제주데이터허브 583 「읍면동 단위 내국인 유동인구」(JEJU_HUB_KEY)
- 소상공인시장진흥공단 상가(상권)정보 (공공데이터포털, SBIZ_STORE_KEY)
- 소상공인365 간단분석 화면 주소 (SBIZ365_CERT_KEY)

보내는 값은 읍면동 이름과 업종 분류 코드뿐이다. 센터 CSV 값은 이 모듈을 거치지 않는다.
키가 없거나 호출이 실패하면 그 항목만 None 으로 두고, 주문서 만들기에는 영향을 주지 않는다.
"""
import calendar
import datetime
import json
import os
import threading
import urllib.parse
import urllib.request

from . import agent

HUB_URL = "https://open.jejudatahub.net/api/proxy/Daaa1t3at3tt8a8DD3t55538t35Dab1t/"
STORE_BASE = "https://apis.data.go.kr/B553077/api/open/sdsc2/"
SBIZ365_URL = "https://bigdata.sbiz.or.kr/#/openApi/simple?certKey="
TIMEOUT = 8
AGE_ORDER = ["10세미만", "10대", "20대", "30대", "40대", "50대", "60대", "70대이상"]
# 허브 1048·센터 업종코드(KSIC 앞자리) → 상가정보 업종 중분류
KSIC_TO_MCLS = [("I5611", "I201", "한식"), ("I56121", "I202", "중식"), ("I56122", "I203", "일식"),
                ("I56123", "I204", "서양식"), ("I5621", "I211", "주점"), ("I5622", "I212", "비알코올 음료")]

_cache = {}
_lock = threading.Lock()


def _key(name):
    v = os.environ.get(name)
    if not v or not v.strip():
        v = agent.read_env_file().get(name, "")
    return (v or "").strip()


def _get_json(url):
    last = None
    for mac, relaxed in agent._ssl_stages():
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT, context=agent._ssl_context(mac, relaxed)) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # 인증서 오류만 다음 단계로
            last = e
            if not agent._is_cert_error(e):
                raise
    raise last


def _cached(k, fn):
    with _lock:
        if k in _cache:
            return _cache[k]
    v = fn()
    with _lock:
        _cache[k] = v
    return v


def _month_ends(today, n=24):
    y, m = today.year, today.month
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        yield "%04d%02d%02d" % (y, m, calendar.monthrange(y, m)[1])


def hub_floating(emd, today=None):
    """읍면동의 가장 최근 달 말일 하루 값: 거주·근무·방문 비중과 방문이 가장 많은 연령대."""
    key = _key("JEJU_HUB_KEY")
    if not key or not emd:
        return None

    def fetch(day):
        q = urllib.parse.urlencode({"startDate": day, "endDate": day, "emd": emd, "limit": 100})
        return _get_json(HUB_URL + urllib.parse.quote(key) + "?" + q).get("data") or []

    def find():
        for day in _month_ends(today or datetime.date.today()):
            rows = fetch(day)
            if rows:
                return day, rows
        return None, []

    day, rows = _cached(("hub", emd), find)
    if not rows:
        return None
    tot = {"resd": 0, "work": 0, "visit": 0}
    by_age = {}
    for r in rows:
        tot["resd"] += int(r.get("resdPop") or 0)
        tot["work"] += int(r.get("workPop") or 0)
        v = int(r.get("visitPop") or 0)
        tot["visit"] += v
        by_age[r.get("ageGroup")] = by_age.get(r.get("ageGroup"), 0) + v
    s = sum(tot.values()) or 1
    top = max(by_age, key=by_age.get) if by_age else ""
    return {
        "source": "제주데이터허브 583 읍면동 단위 내국인 유동인구(Open API)",
        "emd": emd, "date": "%s-%s-%s" % (day[:4], day[4:6], day[6:]),
        "share": {k: round(v * 100.0 / s, 1) for k, v in tot.items()},
        "top_visit_age": top,
        "top_visit_age_share": round(by_age.get(top, 0) * 100.0 / (tot["visit"] or 1), 1),
        "visit_by_age": [[a, round(by_age.get(a, 0) * 100.0 / (tot["visit"] or 1), 1)] for a in AGE_ORDER if a in by_age],
    }


def _store_url(op, params):
    key = _key("SBIZ_STORE_KEY")
    # 공공데이터포털 '인코딩' 키는 이미 % 로 바뀌어 있으므로 그대로 붙인다
    k = key if "%" in key else urllib.parse.quote(key, safe="")
    return STORE_BASE + op + "?serviceKey=" + k + "&type=json&" + urllib.parse.urlencode(params)


def store_count(emd, biz_cd=""):
    """행정동 이름이 같은 곳의 상가 수(업종 중분류가 정해지면 그 업종만)."""
    if not _key("SBIZ_STORE_KEY") or not emd:
        return None

    def codes():
        out = {}
        for sg in ("50110", "50130"):
            for it in (_get_json(_store_url("baroApi", {"resId": "dong", "catId": "admi", "signguCd": sg}))
                       .get("body", {}).get("items") or []):
                out[it.get("adongNm")] = it.get("adongCd")
        return out

    code = _cached("adong", codes).get(emd)
    if not code:
        return None
    mcls = next(((c, n) for p, c, n in sorted(KSIC_TO_MCLS, key=lambda x: -len(x[0])) if (biz_cd or "").startswith(p)), None)
    params = {"divId": "adongCd", "key": code, "numOfRows": 1, "pageNo": 1}
    if mcls:
        params["indsMclsCd"] = mcls[0]
    body = _cached(("store", code, params.get("indsMclsCd")), lambda: _get_json(_store_url("storeListInDong", params)).get("body", {}))
    return {"source": "소상공인시장진흥공단 상가(상권)정보(공공데이터포털 Open API)",
            "emd": emd, "upjong": mcls[1] if mcls else "전체 업종", "count": int(body.get("totalCount") or 0)}


def sbiz365_url():
    k = _key("SBIZ365_CERT_KEY")
    return SBIZ365_URL + urllib.parse.quote(k, safe="") if k else None


def preview(emd, biz_cd=""):
    out = {"emd": emd, "hub": None, "store": None, "sbiz365_url": sbiz365_url(), "errors": []}
    for name, fn in (("hub", lambda: hub_floating(emd)), ("store", lambda: store_count(emd, biz_cd))):
        try:
            out[name] = fn()
        except Exception as e:
            out["errors"].append("%s: %s" % (name, type(e).__name__))
    return out
