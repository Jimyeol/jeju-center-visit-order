# -*- coding: utf-8 -*-
"""가상 센터 CSV 3종을 만든다(카드 이용 현황 v02 계열: 1-14 시간대, 1-12 요일별, 1-08 읍면동).

실제 센터 데이터가 아니다. 명세서(spec.json)의 컬럼과 참고표대로, 고정 시드로 만든 가짜다.
- 값의 뼈대: 허브 1048(지금 data/public/ 에 있는 파일. 실물이 없으면 대체 파일)의 해당 달·업종 도 전체 값을
  이용자구분×성별 칸마다 법정동·관광구분·연령대·시간대(또는 요일)로 쪼갰다. 그래서 파일의 이용건수·이용금액
  합계가 공개 값과 정확히 같다(대조 줄이 0.0% 로 나온다). 이용자수는 더해지는 값이 아니라 맞추지 않았다.
- 형식: 운영진 안내대로 UTF-8(BOM 없음), 모든 값 큰따옴표, 결측 NA, 파티션 컬럼 없음, 파일명 Results_날짜_시간.csv.
- 법정동이 어느 권역(dstrct_se)·시(city)에 드는지는 명세서에 대응표가 없어(월평동 두 줄만 있음) 아래 표로 가정했다.
- 공개 데이터 파일이 바뀌면(실물을 넣으면) 이 스크립트를 다시 돌려야 합계가 다시 맞는다.

사용: python3 tools/make_sample_center.py
"""
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import catalog, config, planner  # noqa: E402
from app import public_data  # noqa: E402

SEED = 20261117
TODAY_FOR_MONTHS = (2026, 11, 17)

FILES = [
    {"file": "Results_20261117_101502.csv", "dataset_no": "1-14", "biz_cd": "I56111", "use": "시나리오 1(노형동 한식당)의 주문서대로 받은 꼴"},
    {"file": "Results_20261117_101845.csv", "dataset_no": "1-12", "biz_cd": "I56220", "use": "시나리오 2(애월읍 카페) 가안의 요일별 파일"},
    {"file": "Results_20261117_102211.csv", "dataset_no": "1-08", "biz_cd": "I56111", "use": "읍면동 단위(요일·시간대 없음) 파일"},
]

# 가정: 법정동 → 7개 권역. 명세서에는 이 대응표가 없다(월평동 두 줄의 비고만 있음). 가상 파일을 만들 때만 쓴다.
DISTRICT_OF = {
    "제주시 동부": ["구좌읍", "조천읍", "우도면"],
    "제주시 서부": ["한림읍", "애월읍", "한경면", "추자면"],
    "제주시 중부(동)": ["일도일동", "일도이동", "이도일동", "이도이동", "건입동", "화북일동", "화북이동", "삼양일동", "삼양이동",
                  "삼양삼동", "봉개동", "아라일동", "아라이동", "영평동", "용강동", "회천동", "도련일동", "도련이동", "오등동", "월평동"],
    "제주시 중부(서)": ["삼도일동", "삼도이동", "용담일동", "용담이동", "용담삼동", "오라일동", "오라이동", "오라삼동", "연동",
                  "노형동", "외도일동", "외도이동", "이호일동", "이호이동", "도두일동", "도두이동", "내도동", "도평동", "해안동", "도남동"],
    "서귀포시 동부": ["성산읍", "표선면", "남원읍"],
    "서귀포시 서부": ["대정읍", "안덕면"],
    "서귀포시 중부": ["서귀동", "동홍동", "서홍동", "토평동", "보목동", "상효동", "신효동", "하효동", "법환동", "서호동", "호근동",
                 "강정동", "도순동", "영남동", "월평동", "하원동", "대포동", "회수동", "중문동", "색달동", "상예동", "하예동"],
}
# 크기 가중치(가정). 적지 않은 곳은 1.0. 아주 작은 곳이 있어야 10 미만 칸이 섞여 나온다.
WEIGHT = {"노형동": 9.0, "연동": 9.5, "이도이동": 8.0, "일도이동": 5.5, "아라일동": 4.0, "삼도일동": 3.0, "삼도이동": 2.6,
          "애월읍": 6.0, "한림읍": 3.8, "조천읍": 3.6, "구좌읍": 3.0, "성산읍": 4.2, "대정읍": 3.0, "남원읍": 2.2,
          "표선면": 2.4, "안덕면": 2.8, "서귀동": 5.0, "동홍동": 3.2, "서홍동": 1.8, "중문동": 3.0, "색달동": 2.4,
          "용담이동": 2.4, "용담일동": 1.4, "건입동": 2.0, "이도일동": 2.2, "일도일동": 1.8, "화북일동": 2.0,
          "삼양이동": 1.6, "외도일동": 1.8, "오라일동": 1.6, "한경면": 1.2, "강정동": 1.0, "법환동": 1.0,
          "우도면": 0.5, "추자면": 0.06, "영남동": 0.004, "회수동": 0.03, "도순동": 0.05, "하원동": 0.08, "용강동": 0.03,
          "오등동": 0.12, "회천동": 0.08, "도련이동": 0.06, "오라삼동": 0.2, "내도동": 0.12, "도평동": 0.15,
          "해안동": 0.2, "영평동": 0.2, "봉개동": 0.4, "상효동": 0.15, "하예동": 0.2, "상예동": 0.3, "호근동": 0.2,
          "서호동": 0.3, "보목동": 0.3, "신효동": 0.3, "하효동": 0.4, "토평동": 0.6, "대포동": 0.5, "이호이동": 0.2,
          "도두이동": 0.2, "외도이동": 0.3, "삼양삼동": 0.3, "삼양일동": 0.5, "화북이동": 0.4, "도련일동": 0.4,
          "아라이동": 0.9, "오라이동": 0.7, "용담삼동": 0.5, "이호일동": 0.7, "도두일동": 0.7, "도남동": 0.9}

AGES = ["10", "20", "30", "40", "50", "60", "70"]
AGE_W = [0.03, 0.16, 0.24, 0.25, 0.19, 0.09, 0.04]
TIMES = ["오전", "점심", "오후", "저녁", "밤"]
TIME_W = {"I56111": [0.08, 0.34, 0.14, 0.38, 0.06], "I56220": [0.20, 0.27, 0.33, 0.16, 0.04]}
WEEKS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일", "공휴일"]
WEEK_W = {"I56111": [0.12, 0.12, 0.125, 0.13, 0.15, 0.16, 0.14, 0.055], "I56220": [0.115, 0.115, 0.12, 0.125, 0.14, 0.18, 0.16, 0.045]}
TOUR = {"제주도민": [("도민", 1.0)], "내국인": [("개별", 0.86), ("단체", 0.14)], "법인": [("NA", 1.0)],
        "중국": [("개별", 0.55), ("단체", 0.45)], "일본": [("개별", 0.7), ("단체", 0.3)],
        "동남아": [("개별", 0.6), ("단체", 0.4)], "기타외국": [("개별", 0.8), ("단체", 0.2)]}
FOREIGN = ("중국", "일본", "동남아", "기타외국")


def split_exact(total, weights):
    """정수 total 을 가중치대로 나눈다(합이 정확히 total. 큰 나머지부터 1씩 더한다)."""
    s = float(sum(weights))
    if s <= 0 or total <= 0:
        return [0] * len(weights)
    raw = [total * w / s for w in weights]
    base = [int(x) for x in raw]
    rest = total - sum(base)
    order = sorted(range(len(weights)), key=lambda i: (-(raw[i] - base[i]), i))
    for i in order[:rest]:
        base[i] += 1
    return base


def bjd_rows(cat):
    """[(법정동, 권역, 시, 도심구분)] 명세서의 법정동 73개를 모두 쓰고, 월평동은 두 줄."""
    names = cat.bjd_list()
    rows = []
    seen = set()
    for district, members in DISTRICT_OF.items():
        for name in members:
            if name not in names:
                raise SystemExit("권역 가정 표의 '%s' 가 명세서 법정동 목록에 없습니다" % name)
            city = district.split(" ")[0]
            urban = "읍면" if (name.endswith("읍") or name.endswith("면")) else "도심"
            rows.append((name, district, city, urban))
            seen.add(name)
    missing = [n for n in names if n not in seen]
    if missing:
        raise SystemExit("권역 가정 표에 없는 법정동: %s" % ", ".join(missing))
    return rows


def make_file(spec_item, cat, pub, month, rng):
    no, biz_cd = spec_item["dataset_no"], spec_item["biz_cd"]
    biz_nm = cat.biz_name(biz_cd)
    columns = [c["en"] for c in cat.columns(no)]
    dim = "time" if "time" in columns else ("week" if "week" in columns else None)
    dim_vals = TIMES if dim == "time" else (WEEKS if dim == "week" else [None])
    dim_w = (TIME_W if dim == "time" else WEEK_W)[biz_cd] if dim else [1.0]
    places = bjd_rows(cat)
    # 곳마다 시간대·요일 꼴과 이용자 섞임을 조금씩 달리한다.
    tilt = {}
    for (name, district, city, urban) in places:
        base_w = WEIGHT.get(name, 1.0) * (0.5 if name == "월평동" else 1.0)
        tourist = 1.6 if urban == "읍면" else (1.3 if district.startswith("서귀포시") else 0.8)
        tilt[(name, district)] = {
            "w": base_w, "tourist": tourist * rng.uniform(0.8, 1.25),
            "dim": [w * rng.uniform(0.75, 1.3) for w in dim_w],
        }
    ym_int = catalog.ym_to_int(month)
    out_rows = []
    totals = {"use_cnt": 0, "use_amt": 0}
    for (ym, bk, ut, sx), v in sorted(pub.d1048.agg.items()):
        if ym != ym_int or bk != catalog.biz_key(biz_nm):
            continue
        cnt_total, amt_total = int(round(v[1])), int(round(v[2]))
        combos, weights = [], []
        for (name, district, city, urban) in places:
            t = tilt[(name, district)]
            place_w = t["w"] * (t["tourist"] if ut != "제주도민" else 1.0 / t["tourist"] ** 0.5)
            if ut in FOREIGN:
                place_w *= (2.5 if name in ("연동", "노형동", "성산읍", "서귀동", "중문동", "색달동") else 0.35)
            for tour, tw in TOUR[ut]:
                if ut in ("제주도민", "내국인") and sx in ("남성", "여성"):
                    ages = list(zip(AGES, AGE_W))
                else:
                    ages = [("NA", 1.0)]
                for ai, (age, aw) in enumerate(ages):
                    for di, dv in enumerate(dim_vals):
                        w = place_w * tw * aw * t["dim"][di]
                        if dim == "time" and age != "NA":
                            # 젊을수록 저녁·밤, 나이 들수록 점심 쪽
                            w *= (1.0 + (0.12 * (2 - ai) if dv in ("저녁", "밤") else (0.06 * (ai - 2) if dv == "점심" else 0.0)))
                        if dim == "week" and age != "NA":
                            w *= (1.0 + (0.10 * (2 - ai) if dv in ("토요일", "일요일") else 0.0))
                        if ut in FOREIGN:
                            # 외국인은 밤과 공휴일 이용이 드물다고 두었다(10 미만 칸이 섞여 나오게 하려는 가정).
                            if dv == "밤":
                                w *= 0.08
                            elif dv == "오전":
                                w *= 0.4
                            elif dv == "공휴일":
                                w *= 0.25
                        combos.append((name, district, city, urban, tour, age, dv))
                        weights.append(max(w, 0.0) * rng.uniform(0.85, 1.15))
        cnts = split_exact(cnt_total, weights)
        amt_w = [c * rng.uniform(0.8, 1.25) for c in cnts]
        amts = split_exact(amt_total, amt_w)
        for (name, district, city, urban, tour, age, dv), c, a in zip(combos, cnts, amts):
            if c <= 0:
                continue
            users = max(1, int(round(c / rng.uniform(1.0, 1.9))))
            rec = {"base_year_month": month.replace("-", ""), "dstrct_se": district, "city": city, "area_type": urban,
                   "bjd": name, "biz_cd": biz_cd, "biz_nm": biz_nm, "user_type": ut, "tour_type": tour,
                   "age": age, "sex": sx, "user_cnt": users, "use_cnt": c, "use_amt": a}
            if dim:
                rec[dim] = dv
            out_rows.append([rec[col] for col in columns])
            totals["use_cnt"] += c
            totals["use_amt"] += a
    path = os.path.join(config.SAMPLE_DIR, spec_item["file"])
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(columns) + "\n")
        for row in out_rows:
            f.write(",".join('"%s"' % str(x).replace('"', '""') for x in row) + "\n")
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return {"file": spec_item["file"], "dataset_no": no, "dataset_name": cat.name(no), "month": month,
            "biz_cd": biz_cd, "biz_nm": biz_nm, "rows": len(out_rows), "bytes": os.path.getsize(path),
            "sum_use_cnt": totals["use_cnt"], "sum_use_amt": totals["use_amt"],
            "public_use_amt": pub.value_1048(month, biz_nm, "use_amt"),
            "public_use_cnt": pub.value_1048(month, biz_nm, "use_cnt"),
            "columns": columns, "use": spec_item["use"], "sha256": digest}


def write_readme(metas, pub):
    lines = [
        "# 가상 센터 CSV 설명",
        "",
        "이 폴더의 CSV 는 **실제 제주빅데이터센터 데이터가 아니다.** 센터 데이터는 방문해야만 받을 수 있어,",
        "명세서(v13)의 컬럼과 참고표대로 만든 가짜 파일로 '내려받은 CSV 로 다시 돌리기'를 보여 준다.",
        "",
        "| 파일 | 어떤 데이터 | 달 | 업종(조건) | 줄 수 | 쓰임 |",
        "|---|---|---|---|---|---|",
    ]
    for m in metas:
        lines.append("| %s | %s %s | %s | %s %s | %s | %s |" % (
            m["file"], m["dataset_no"], m["dataset_name"], m["month"], m["biz_cd"], m["biz_nm"], "{:,}".format(m["rows"]), m["use"]))
    lines += [
        "",
        "## 어떻게 만들었나",
        "",
        "- 생성 스크립트: `tools/make_sample_center.py` (고정 시드 %d). 다시 돌리면 같은 파일이 나온다." % SEED,
        "- 값의 뼈대는 허브 1048(업종별 성별 카드 이용 정보)의 그 달·그 업종 도 전체 값이다. 이용자구분×성별 칸마다",
        "  법정동·관광구분·연령대·시간대(또는 요일)로 쪼갰으므로 파일의 이용건수·이용금액 합계는 공개 값과 같다.",
        "  그래서 화면의 대조 줄이 0.0% 로 나온다. 이것은 그렇게 만들었기 때문이지 실제 센터 데이터가 그렇다는 증거가 아니다.",
        "- 지금 쓴 공개 값: `data/public/%s`%s." % (pub.d1048.file, " (대체 데이터)" if pub.d1048.placeholder else " (허브 실물)"),
        "  공개 데이터 파일을 바꾸면 이 스크립트를 다시 돌려야 합계가 다시 맞는다.",
        "- 업종은 한 가지만 들어 있다(센터 화면에서 업종 조건을 걸어 받은 꼴). 법정동은 73곳이 모두 들어 있다.",
        "- 작은 법정동과 외국인 이용자 칸에는 10 미만 값이 섞여 있다(가리기를 보여 주려고 일부러 둔 것).",
        "",
        "## 형식",
        "",
        "- 운영진 안내(게시판 40번·47번)대로: UTF-8(BOM 없음), 구분자 쉼표, 모든 값 큰따옴표, 결측 `NA`,",
        "  파일명 `Results_날짜_시간.csv`, 헤더는 명세서의 영문 컬럼명, 파티션 컬럼(pt_year, pt_month) 없음.",
        "- 컬럼 순서는 명세서 시트의 순서에서 파티션 컬럼만 뺀 것이다. 실제 파일의 컬럼 순서는 확인하지 못했다",
        "  (프로그램은 순서가 달라도 컬럼명으로 읽는다).",
        "",
        "## 가정한 것(명세서에 없는 것)",
        "",
        "- 법정동이 어느 권역(dstrct_se)·시(city)에 드는지: 명세서에는 월평동 두 줄의 비고만 있다. 나머지는 스크립트의",
        "  `DISTRICT_OF` 표로 가정했다. 실제 센터 데이터의 대응과 다를 수 있다.",
        "- 법정동별 크기, 시간대·요일·연령대 분포: 전부 지어낸 가중치다.",
        "- 이용자수(user_cnt): 이용건수를 1.0~1.9 로 나눈 값이다. 공개 값과 맞추지 않았다.",
        "",
    ]
    with open(os.path.join(config.SAMPLE_DIR, "가상데이터_설명.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


def main():
    import datetime
    cat = catalog.get()
    pub = public_data.get(force=True)
    if not pub.d1048.ok:
        print("허브 1048 파일(또는 대체 파일)이 없습니다. 먼저 python3 tools/make_placeholder_public.py 를 실행하세요.")
        return 2
    if not os.path.isdir(config.SAMPLE_DIR):
        os.makedirs(config.SAMPLE_DIR)
    today = datetime.date(*TODAY_FOR_MONTHS)
    metas = []
    for i, item in enumerate(FILES):
        pm = planner.pick_months(item["dataset_no"], biz=item["biz_cd"], today=today)
        peak = [m for m in pm["months"] if m["role"] == "많은 달" and m["reconcilable"]]
        if not peak:
            print("%s: 공개 데이터와 겹치는 달을 고르지 못했습니다." % item["dataset_no"])
            return 2
        rng = random.Random(SEED + i)
        metas.append(make_file(item, cat, pub, peak[0]["ym"], rng))
    manifest = {"note": "가상 데이터. tools/make_sample_center.py 가 만들었다.", "seed": SEED,
                "public_file": pub.d1048.file, "public_placeholder": pub.d1048.placeholder, "files": metas}
    with open(config.SAMPLE_MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
        f.write("\n")
    write_readme(metas, pub)
    print("가상 센터 CSV 를 만들었습니다(실제 센터 데이터 아님, 고정 시드 %d)." % SEED)
    for m in metas:
        ok = m["sum_use_amt"] == m["public_use_amt"] and m["sum_use_cnt"] == m["public_use_cnt"]
        print("  %s  %s %s  %s  %s  %d줄  %d바이트  합계=공개 값: %s" % (
            m["file"], m["dataset_no"], m["dataset_name"], m["month"], m["biz_nm"], m["rows"], m["bytes"], "예" if ok else "아니오"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
