# -*- coding: utf-8 -*-
"""검증기: 주문서의 데이터 번호·컬럼명·조건 값·달이 명세서(spec.json)에 실재하는지 대조한다.

validate_order(order, need) 는 어긋난 항목 목록을 돌려준다(없으면 빈 목록).
validate(order, need) 는 통과 수와 전체 수를 함께 센다.
strip_failed(order, issues) 는 어긋난 줄을 뺀 주문서를 돌려준다(통과한 항목만 화면에 낸다).
"""
import copy

from . import catalog as catalog_mod
from . import config
from .korean import josa as kj


def _issue(item, field, ref, message, alt=None):
    return {"item": item, "alt": alt, "field": field, "ref": ref, "message": message}


KINDS = ("dataset", "month", "condition", "use_column", "downloads", "unit")
KIND_LABELS = {"dataset": "데이터 번호·이름", "month": "받을 달", "condition": "조건 값", "use_column": "쓸 컬럼",
               "downloads": "내려받기 횟수", "unit": "필요한 단위"}


def _check_item(cat, it, idx, alt=None, stats=None):
    """한 주문 항목을 대조한다. (확인한 줄 수, 어긋난 줄 목록). stats 를 주면 종류별 줄 수와 '명세서로 확인 못 함' 줄 수를 센다."""
    issues = []
    total = 0
    stats = stats if stats is not None else {}

    def count(kind, n=1):
        stats[kind] = stats.get(kind, 0) + n

    no = it.get("dataset_no")
    total += 1
    count("dataset")
    if not no or not cat.has(no):
        issues.append(_issue(idx, "dataset_no", no, "데이터 번호 '%s'%s 명세서에 없다" % (no, kj(no, "은")), alt))
        return total, issues
    total += 1
    count("dataset")
    name = it.get("dataset_name")
    if name is not None and catalog_mod.nospace(name) != catalog_mod.nospace(cat.name(no)):
        issues.append(_issue(idx, "dataset_name", name,
                             "%s 의 이름은 '%s'이다('%s'%s 아니다)" % (no, cat.name(no), name, kj(name, "이")), alt))
    for m in it.get("months", []) or []:
        total += 1
        count("month")
        ym = m.get("ym") if isinstance(m, dict) else m
        try:
            ym_n = catalog_mod.norm_ym(ym)
        except (ValueError, TypeError):
            issues.append(_issue(idx, "month", ym, "받을 달 '%s'%s 연-월 형식이 아니다" % (ym, kj(ym, "은")), alt))
            continue
        ok = cat.month_in_range(no, ym_n)
        if ok is None:
            count("unverified")        # 수록 기간이 명세서에 없어 범위 안인지 확인하지 못했다
            stats.setdefault("unverified_notes", []).append("%s 받을 달 %s: 수록 기간이 명세서에 없어 범위를 확인하지 못함" % (no, ym_n))
        if ok is False:
            issues.append(_issue(idx, "month", ym_n, "받을 달 %s 은 %s 의 수록 범위(%s) 밖이다"
                                 % (ym_n, no, cat.period_text(no)), alt))
    for c in it.get("conditions", []) or []:
        total += 1
        count("condition")
        col, val = c.get("column"), c.get("value")
        ev = cat.value_evidence(no, col, val)
        if not ev["ok"]:
            issues.append(_issue(idx, "condition", "%s=%s" % (col, val), ev["note"], alt))
        elif ev.get("borrowed") or not ev.get("known"):
            # 컬럼은 명세서에 있으나, 값이 그 시트의 값 목록으로 확인된 것은 아니다.
            count("unverified")
            why = "다른 시트의 목록을 빌려 대조" if ev.get("borrowed") else "값 목록이 명세서에 없음"
            stats.setdefault("unverified_notes", []).append("%s 조건 %s = %s: %s" % (no, col, val, why))
    for c in it.get("use_columns", []) or []:
        total += 1
        count("use_column")
        en = c.get("en") if isinstance(c, dict) else c
        col = cat.column(no, en)
        if not col:
            issues.append(_issue(idx, "use_column", en, "%s 시트에 %s 컬럼이 없다" % (no, en), alt))
        elif col["partition"]:
            issues.append(_issue(idx, "use_column", en, "%s 는 파티션 컬럼이라 내려받은 CSV 에 들어 있지 않다" % en, alt))
    return total, issues


def _covered_units(cat, items, need):
    """주문 항목들의 '쓸 컬럼'과 조건이 채우는 단위 열쇠."""
    from . import planner  # 순환 import 를 피하려고 여기서 부른다
    covered = set()
    per_item = []
    for it in items:
        no = it.get("dataset_no")
        if not cat.has(no):
            continue
        ev = planner.evaluate(need, no, cat)
        cols = set((c.get("en") if isinstance(c, dict) else c) for c in it.get("use_columns", []) or [])
        cols |= set(c.get("column") for c in it.get("conditions", []) or [])
        got = set()
        for key in ev["covers"]:
            need_cols = ev["unit_columns"].get(key)
            if need_cols is None or not need_cols or any(c in cols for c in need_cols):
                got.add(key)
        per_item.append(got)
        covered |= got
    # 요일×시간대 교차는 한 항목이 둘 다 채울 때만 인정한다.
    if "dow_x_slot" in covered and not any("dow_x_slot" in g for g in per_item):
        covered.discard("dow_x_slot")
    return covered


def validate(order, need=None, cat=None, max_downloads=None):
    """{"passed": n, "total": n, "issues": [...], "breakdown": {종류: 줄 수}, "unverified": n, "unverified_notes": [...]}

    passed = total - 어긋난 줄. unverified 는 통과한 줄 가운데 명세서의 그 시트로는 확인하지 못한 줄
    (값 목록이 없거나 다른 시트의 목록을 빌려 대조한 조건 값, 수록 기간이 없는 데이터의 받을 달)이다.
    breakdown 에는 견준 안(alternatives)의 항목도 들어 있다.
    """
    cat = cat or catalog_mod.get()
    cap = config.MAX_DOWNLOADS if max_downloads is None else max_downloads
    issues = []
    total = 0
    stats = {}
    items = order.get("items", []) or []
    for i, it in enumerate(items):
        t, iss = _check_item(cat, it, i, stats=stats)
        total += t
        issues.extend(iss)
    for a, alt in enumerate(order.get("alternatives", []) or []):
        for i, it in enumerate(alt.get("items", []) or []):
            t, iss = _check_item(cat, it, i, alt=a, stats=stats)
            total += t
            issues.extend(iss)
    # 내려받기 횟수
    total += 1
    stats["downloads"] = 1
    downloads = sum(len(it.get("months", []) or []) for it in items)
    if downloads > cap:
        issues.append(_issue(None, "downloads", downloads, "내려받기 %d회는 상한 %d회를 넘는다" % (downloads, cap)))
    # 필요한 단위가 채워졌는지
    if need is not None and need.get("center_needed", True):
        valid_items = [it for i, it in enumerate(items)
                       if not any(x["item"] == i and x["alt"] is None and x["field"] == "dataset_no" for x in issues)]
        covered = _covered_units(cat, valid_items, need)
        declared = set()
        for nd in order.get("no_data", []) or []:
            declared.add(nd.get("key") or nd.get("unit"))
        for alt in order.get("alternatives", []) or []:
            if alt.get("recommended"):
                for k in alt.get("give_up_keys", []) or []:
                    declared.add(k)
        labels = dict((u["key"], u["label"]) for u in need.get("units", []))
        for u in need.get("units", []):
            total += 1
            stats["unit"] = stats.get("unit", 0) + 1
            if u["key"] in covered:
                continue
            if u["key"] in declared or u["label"] in declared:
                continue
            if not items and not (order.get("no_data") or []):
                issues.append(_issue(None, "unit", u["key"], "주문서에 항목이 없다"))
                continue
            issues.append(_issue(None, "unit", u["key"],
                                 "필요한 단위 '%s'%s 채우는 항목도, 메울 데이터가 없다는 표시도 없다" % (labels.get(u["key"], u["key"]), kj(labels.get(u["key"], u["key"]), "을"))))
    # 같은 줄이 주문 항목과 견준 안에 함께 있으면 '확인 못 함' 설명은 한 번만 적는다(줄 수는 그대로 센다).
    notes = []
    for x in stats.get("unverified_notes", []):
        if x not in notes:
            notes.append(x)
    return {"passed": total - len(issues), "total": total, "issues": issues,
            "breakdown": dict((k, stats.get(k, 0)) for k in KINDS),
            "unverified": stats.get("unverified", 0), "unverified_notes": notes,
            "has_alternatives": bool(order.get("alternatives"))}


def validate_order(order, need=None, cat=None, max_downloads=None):
    """어긋난 항목 목록을 돌려준다. 한 줄: {"item", "alt", "field", "ref", "message"}"""
    return validate(order, need, cat, max_downloads)["issues"]


def strip_failed(order, issues):
    """어긋난 줄(데이터, 달, 조건, 컬럼)을 뺀 주문서 사본을 돌려준다."""
    out = copy.deepcopy(order)

    def clean(items, alt):
        keep = []
        for i, it in enumerate(items):
            mine = [x for x in issues if x["item"] == i and x["alt"] == alt]
            if any(x["field"] in ("dataset_no", "dataset_name") for x in mine):
                continue
            bad_m = set(str(x["ref"]) for x in mine if x["field"] == "month")
            bad_c = set(x["ref"] for x in mine if x["field"] == "condition")
            bad_u = set(x["ref"] for x in mine if x["field"] == "use_column")
            it["months"] = [m for m in it.get("months", [])
                            if str(m.get("ym") if isinstance(m, dict) else m) not in bad_m
                            and _safe_norm(m.get("ym") if isinstance(m, dict) else m) not in bad_m]
            it["conditions"] = [c for c in it.get("conditions", [])
                                if "%s=%s" % (c.get("column"), c.get("value")) not in bad_c]
            it["use_columns"] = [c for c in it.get("use_columns", [])
                                 if (c.get("en") if isinstance(c, dict) else c) not in bad_u]
            it["downloads"] = len(it["months"])
            keep.append(it)
        return keep

    out["items"] = clean(out.get("items", []) or [], None)
    for a, alt in enumerate(out.get("alternatives", []) or []):
        alt["items"] = clean(alt.get("items", []) or [], a)
        alt["downloads"] = sum(it.get("downloads", 0) for it in alt["items"])
    out["total_downloads"] = sum(it.get("downloads", 0) for it in out["items"])
    return out


def _safe_norm(ym):
    try:
        return catalog_mod.norm_ym(ym)
    except (ValueError, TypeError):
        return None


def summary(result):
    """API 의 validation 모양: {"passed", "total", "issues": [문장…]}"""
    msgs = []
    for x in result["issues"]:
        where = ""
        if x["alt"] is not None:
            where = "대안 %d의 " % (x["alt"] + 1)
        if x["item"] is not None:
            where += "%d번 항목: " % (x["item"] + 1)
        msgs.append(where + x["message"])
    bd = result.get("breakdown") or {}
    parts = ["%s %d" % (KIND_LABELS[k], bd[k]) for k in KINDS if bd.get(k)]
    text = ", ".join(parts)
    if text and result.get("has_alternatives"):
        text += " (견준 안의 항목 포함)"
    return {"passed": result["passed"], "total": result["total"], "issues": msgs,
            "breakdown": bd, "breakdown_text": text,
            "unverified": result.get("unverified", 0), "unverified_notes": list(result.get("unverified_notes") or [])}
