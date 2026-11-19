# -*- coding: utf-8 -*-
"""허브 공개 데이터 실물이 아직 없을 때 쓰는 대체 파일을 만든다(컬럼 구성만 같고 값은 지어낸 것).

만드는 파일(data/public/):
    _대체_업종별성별카드이용정보.csv        1048 꼴: base_year_month, biz_nm, user_type, sex, user_count, use_count, use_amount
    _대체_제주도방문내국인유동인구추이.csv  520 꼴: base_year_month, sex, age_range, time_range, visit_population_count

- 값은 고정 시드로 만든 가짜다. 계절 흐름과 2020~2021년의 꺾임, 2022-07 의 통신사 전환 단절을 흉내 낸다.
- 업종 42개 이름은 명세서(spec.json)의 참고표에서 읽는다.
- 실물 파일(앞머리가 '_대체_' 가 아닌 CSV)이 data/public/ 에 있으면 프로그램은 실물을 쓴다. 이 스크립트는 실물을 건드리지 않는다.
- 2026-11-17 에 실물 두 파일이 들어와 대체 파일은 폴더에서 뺐다. 실물이 없는 곳에서 돌려야 할 때만 이 스크립트를 쓴다.

사용: python3 tools/make_placeholder_public.py
"""
import csv
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import catalog, config  # noqa: E402

SEED_1048 = 1048
SEED_520 = 520
FIRST_1048, LAST_1048 = (2018, 1), (2025, 6)
FIRST_520, LAST_520 = (2018, 1), (2025, 4)

USER_TYPES = ["제주도민", "내국인", "법인", "중국", "일본", "동남아", "기타외국"]

# 1~12월 계절 꼴(평균이 1 근처)
PROFILES = {
    "tourist": [0.90, 0.86, 0.95, 1.03, 1.09, 1.00, 1.05, 1.16, 0.98, 1.10, 0.97, 0.91],
    "local":   [1.00, 0.94, 1.00, 1.00, 1.03, 0.99, 1.00, 1.01, 0.99, 1.02, 0.99, 1.03],
    "mixed":   [0.95, 0.89, 0.97, 1.02, 1.07, 0.99, 1.03, 1.10, 0.98, 1.06, 0.97, 0.97],
    "summer":  [0.55, 0.55, 0.75, 0.95, 1.10, 1.20, 1.55, 1.75, 1.10, 0.95, 0.80, 0.75],
    "golf":    [0.70, 0.80, 1.00, 1.15, 1.20, 1.05, 0.90, 0.85, 1.05, 1.25, 1.15, 0.90],
}

# 업종코드 앞머리별 기본 꼴: (계절 꼴, 월 이용금액(억원) 범위, 건당 금액 범위, 이용자 섞임[도민, 내국인, 법인, 외국])
DEFAULTS = [
    ("G47130", ("tourist", (150, 220), (90000, 140000), (0.10, 0.50, 0.02, 0.38))),
    ("G47842", ("tourist", (30, 60), (25000, 40000), (0.15, 0.70, 0.03, 0.12))),
    ("G477", ("local", (120, 260), (45000, 60000), (0.62, 0.27, 0.10, 0.01))),
    ("G47", ("local", (40, 280), (9000, 45000), (0.74, 0.18, 0.06, 0.02))),
    ("H492", ("local", (15, 60), (4000, 12000), (0.60, 0.33, 0.04, 0.03))),
    ("H5", ("tourist", (60, 180), (50000, 110000), (0.30, 0.60, 0.06, 0.04))),
    ("I55", ("tourist", (90, 260), (110000, 190000), (0.08, 0.74, 0.06, 0.12))),
    ("I561", ("mixed", (60, 200), (18000, 38000), (0.52, 0.40, 0.05, 0.03))),
    ("I562", ("mixed", (50, 170), (8000, 45000), (0.55, 0.38, 0.04, 0.03))),
    ("L69110", ("tourist", (120, 200), (95000, 150000), (0.05, 0.86, 0.06, 0.03))),
    ("L69", ("tourist", (8, 20), (20000, 40000), (0.20, 0.72, 0.03, 0.05))),
    ("N75", ("tourist", (20, 70), (120000, 260000), (0.25, 0.60, 0.10, 0.05))),
    ("R91121", ("golf", (150, 240), (140000, 190000), (0.30, 0.58, 0.10, 0.02))),
    ("R91239", ("summer", (8, 25), (30000, 55000), (0.12, 0.80, 0.03, 0.05))),
    ("R91249", ("tourist", (20, 60), (150000, 400000), (0.02, 0.08, 0.00, 0.90))),
    ("R91", ("tourist", (15, 60), (15000, 45000), (0.30, 0.60, 0.04, 0.06))),
    ("S96", ("local", (8, 30), (9000, 60000), (0.65, 0.28, 0.02, 0.05))),
]
# 시나리오에 쓰는 두 업종은 크기를 정해 둔다.
FIXED = {
    "I56111": ("mixed", 450.0, 32000.0, (0.53, 0.40, 0.045, 0.025)),   # 한식 음식점업
    "I56220": ("mixed", 160.0, 9500.0, (0.50, 0.44, 0.03, 0.03)),      # 비알콜 음료점업
}
FOREIGN_SPLIT = {"중국": 0.55, "일본": 0.12, "동남아": 0.15, "기타외국": 0.18}


def months(first, last):
    y, m = first
    while (y, m) <= last:
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def tourist_shock(y, m):
    """내국인 관광객 쪽 꺾임."""
    t = y * 12 + m
    if 2020 * 12 + 2 <= t <= 2020 * 12 + 5:
        return 0.55
    if 2020 * 12 + 6 <= t <= 2020 * 12 + 12:
        return 0.80
    if y == 2021:
        return 0.95
    if y == 2022:
        return 1.08
    return 1.0


def foreign_shock(y, m):
    t = y * 12 + m
    if t < 2020 * 12 + 2:
        return 0.75
    if t <= 2022 * 12 + 5:
        return 0.03
    if y == 2022:
        return 0.15
    if y == 2023:
        return 0.45 + 0.04 * m
    return 1.0


def biz_params(code, rng):
    if code in FIXED:
        return FIXED[code]
    for prefix, (profile, amt_rng, ticket_rng, mix) in DEFAULTS:
        if code.startswith(prefix):
            return (profile, rng.uniform(*amt_rng), rng.uniform(*ticket_rng), mix)
    return ("mixed", rng.uniform(20, 80), rng.uniform(10000, 40000), (0.5, 0.4, 0.05, 0.05))


def make_1048(path, cat):
    rng = random.Random(SEED_1048)
    rows = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        # 허브 실물(2026-11-17 받은 파일)의 첫 줄과 같은 영문 컬럼명을 쓴다.
        w.writerow(["base_year_month", "biz_nm", "user_type", "sex", "user_count", "use_count", "use_amount"])
        for code, name in cat.biz42():
            profile, base_eok, ticket, mix = biz_params(code, rng)
            male = rng.uniform(0.42, 0.58)
            unknown = rng.uniform(0.01, 0.04)
            visits_per_user = rng.uniform(1.3, 2.6)
            for (y, m) in months(FIRST_1048, LAST_1048):
                growth = 1.04 ** (y - 2018 + (m - 1) / 12.0)
                for ut in USER_TYPES:
                    if ut == "제주도민":
                        share, season, shock = mix[0], PROFILES["local"][m - 1], 1.0 - 0.05 * (1 - tourist_shock(y, m))
                    elif ut == "내국인":
                        share, season, shock = mix[1], PROFILES[profile][m - 1], tourist_shock(y, m)
                    elif ut == "법인":
                        share, season, shock = mix[2], PROFILES["local"][m - 1], 1.0
                    else:
                        share = mix[3] * FOREIGN_SPLIT[ut]
                        season, shock = PROFILES["tourist"][m - 1], foreign_shock(y, m)
                    if share <= 0:
                        continue
                    amt_total = base_eok * 1e8 * share * season * shock * growth * (1 + rng.gauss(0, 0.025))
                    if ut in ("제주도민", "내국인"):
                        sexes = [("남성", male * (1 - unknown)), ("여성", (1 - male) * (1 - unknown)), ("알수없음", unknown)]
                    else:
                        sexes = [("알수없음", 1.0)]
                    for sx, sw in sexes:
                        amt = max(0, int(round(amt_total * sw * (1 + rng.gauss(0, 0.01)), -1)))
                        cnt = int(round(amt / (ticket * (1 + rng.gauss(0, 0.02)))))
                        if cnt <= 0 or amt <= 0:
                            continue
                        users = max(1, int(round(cnt / visits_per_user)))
                        w.writerow(["%04d%02d" % (y, m), name, ut, sx, users, cnt, amt])
                        rows += 1
    return rows


AGES = [("10대", 0.05), ("20대", 0.17), ("30대", 0.22), ("40대", 0.22), ("50대", 0.18), ("60대", 0.11), ("70대 이상", 0.05)]


def hour_weight(h, age_idx):
    """h: 1~24. 낮에 높고 새벽에 낮다. 젊을수록 저녁 쪽으로 조금 민다."""
    center = 14.5 + (2 - age_idx) * 0.45
    day = math.exp(-((h - center) ** 2) / (2 * 4.6 ** 2))
    night = 0.33
    return night + day


def make_520(path):
    rng = random.Random(SEED_520)
    rows = 0
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["base_year_month", "sex", "age_range", "time_range", "visit_population_count"])
        for (y, m) in months(FIRST_520, LAST_520):
            growth = 1.02 ** (y - 2018)
            carrier = 1.22 if (y, m) >= (2022, 7) else 1.0   # 통신사 전환에 따른 단절을 흉내 낸다
            month_total = 4.2e7 * PROFILES["tourist"][m - 1] * tourist_shock(y, m) * growth * carrier
            month_total *= (1 + rng.gauss(0, 0.02))
            for sx, sw in (("남성", 0.49), ("여성", 0.51)):
                for ai, (age, aw) in enumerate(AGES):
                    ws = [hour_weight(h, ai) for h in range(1, 25)]
                    tot = sum(ws)
                    # 여름에는 젊은 층이 조금 더 많다
                    tilt = 1.0 + (0.10 if (m in (7, 8) and ai <= 2) else 0.0) - (0.05 if (m in (7, 8) and ai >= 4) else 0.0)
                    for h in range(1, 25):
                        v = month_total * sw * aw * tilt * ws[h - 1] / tot * (1 + rng.gauss(0, 0.01))
                        w.writerow(["%04d%02d" % (y, m), sx, age, "%02d시" % h, int(round(v))])
                        rows += 1
    return rows


def main():
    cat = catalog.get()
    if not os.path.isdir(config.PUBLIC_DIR):
        os.makedirs(config.PUBLIC_DIR)
    p1 = os.path.join(config.PUBLIC_DIR, config.PLACEHOLDER_1048)
    p2 = os.path.join(config.PUBLIC_DIR, config.PLACEHOLDER_520)
    n1 = make_1048(p1, cat)
    n2 = make_520(p2)
    print("대체 파일을 만들었습니다(값은 지어낸 것, 고정 시드 %d·%d)." % (SEED_1048, SEED_520))
    print("  %s  %d줄  %d바이트" % (os.path.relpath(p1, ROOT), n1, os.path.getsize(p1)))
    print("  %s  %d줄  %d바이트" % (os.path.relpath(p2, ROOT), n2, os.path.getsize(p2)))
    print("실물 CSV 를 data/public/ 에 넣으면 프로그램이 실물을 씁니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
