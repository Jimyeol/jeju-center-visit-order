# -*- coding: utf-8 -*-
"""공개 데이터 적재·월별 집계·계절 지수·비중."""
import os
import shutil
import tempfile
import unittest

from tests._base import ROOT  # noqa: F401

from app import config, planner
from app import public_data as P


class SeasonalIndex(unittest.TestCase):
    def series(self, years, shape):
        out = []
        for y in years:
            for m in range(1, 13):
                out.append({"ym": "%04d-%02d" % (y, m), "value": shape[m - 1] * (1 + 0.1 * (y - years[0]))})
        return out

    def test_index_averages_to_100_and_ignores_growth(self):
        shape = [80, 70, 90, 100, 110, 100, 105, 130, 100, 115, 100, 100]
        si = P.seasonal_index(self.series([2019, 2020, 2021], shape))
        idx = dict((r["month"], r["index"]) for r in si["index"])
        self.assertAlmostEqual(sum(idx.values()) / 12.0, 100.0, places=0)
        self.assertEqual(max(idx, key=idx.get), 8)
        self.assertEqual(min(idx, key=idx.get), 2)
        self.assertEqual(si["years"], [2019, 2020, 2021])
        self.assertAlmostEqual(idx[8], 130 * 12 * 100.0 / sum(shape), places=1)

    def test_incomplete_years_are_not_used(self):
        s = self.series([2020, 2021], [100] * 7 + [200] + [100] * 4) + [{"ym": "2022-01", "value": 9999}]
        si = P.seasonal_index(s)
        self.assertEqual(si["years"], [2020, 2021])

    def test_split_range(self):
        s = self.series([2020, 2021, 2023, 2024], [100] * 12)
        self.assertEqual(P.seasonal_index(s, start="2022-07")["years"], [2023, 2024])
        self.assertEqual(P.seasonal_index(s, end="2022-06")["years"], [2020, 2021])

    def test_pick_three_months(self):
        idx = [{"month": m, "index": v} for m, v in zip(range(1, 13), [90, 80, 95, 101, 104, 99.5, 103, 120, 100.4, 110, 97, 92])]
        got = P.pick_season_months(idx)
        self.assertEqual([(g["month"], g["role"]) for g in got],
                         [(8, "많은 달"), (2, "적은 달"), (9, "평균에 가까운 달")])

    def test_empty(self):
        self.assertEqual(P.seasonal_index([])["index"], [])
        self.assertEqual(P.pick_season_months([]), [])


class LoadedData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pub = P.get()

    def test_status_shape(self):
        st = self.pub.status()
        for kind in ("d1048", "d520"):
            for key in ("rows", "first", "last", "placeholder"):
                self.assertIn(key, st[kind])
            self.assertGreater(st[kind]["rows"], 0)
            self.assertIsNone(st[kind]["error"])

    def test_1048_monthly_and_shares(self):
        s = self.pub.monthly_1048("한식 음식점업")
        self.assertGreaterEqual(len(s), 12)
        self.assertEqual(s[0]["ym"], self.pub.d1048.first)
        self.assertEqual(self.pub.monthly_1048("한식음식점업"), s)       # 공백을 지우고 맞춘다
        sh = self.pub.shares_1048("user_type", "한식 음식점업")["rows"]
        self.assertAlmostEqual(sum(r["pct"] for r in sh), 100.0, delta=0.5)
        self.assertEqual(self.pub.value_1048(s[-1]["ym"], "한식 음식점업"), s[-1]["value"])
        self.assertIsNone(self.pub.value_1048("2099-01", "한식 음식점업"))

    def test_user_count_is_not_summed_over_months(self):
        ans = planner.public_monthly("1048", "I56111", metric="user_cnt")
        self.assertTrue(any("더하지 않았다" in n for n in ans["notes"]))

    def test_520_split_and_foreign(self):
        ans = planner.public_monthly("520")
        self.assertTrue(ans["available"])
        self.assertEqual(len(ans["seasonal"]), 12)
        self.assertIn("seasonal_before_split", ans)
        self.assertTrue(any("체류량" in n for n in ans["notes"]))
        self.assertFalse(planner.public_monthly("520", who="foreign")["available"])
        self.assertIn("내국인", planner.public_monthly("520", who="foreign")["reason"])

    def test_all_42_center_biz_names_are_in_hub_1048(self):
        from app import catalog
        missing = [n for _, n in catalog.get().biz42() if not self.pub.has_biz(n)]
        self.assertEqual(missing, [])
        self.assertEqual(len(planner.public_scope()["d1048"]["values"]["biz"]), 42)

    def test_biz_with_missing_months_gets_no_seasonal_index(self):
        from app import catalog
        span = catalog.ym_to_int(self.pub.d1048.last) - catalog.ym_to_int(self.pub.d1048.first) + 1
        sparse = [n for _, n in catalog.get().biz42() if len(self.pub.monthly_1048(n)) < span]
        full = [n for _, n in catalog.get().biz42() if len(self.pub.monthly_1048(n)) == span]
        self.assertIn("한식 음식점업", full)
        self.assertIn("기타음식료품위주종합소매업", full)        # 깨진 표기 한 달을 합쳐 읽어야 빠지는 달이 없다
        for name in sparse:
            ans = planner.public_monthly("1048", name)
            self.assertTrue(ans["available"], name)
            self.assertEqual(ans["seasonal"], [], name)
            self.assertEqual(ans["season_months"], [], name)
            self.assertTrue(any("자료가 모자람" in n for n in ans["notes"]), name)
        if not self.pub.d1048.placeholder:
            self.assertEqual(sorted(sparse), ["기타 갬블링 및 베팅업", "버스 운송업", "택시 운송업"])

    def test_unknown_biz_is_reported_not_guessed(self):
        ans = planner.public_monthly("1048", "없는업종")
        self.assertFalse(ans["available"])
        self.assertEqual(ans["series"], [])


class RealFileTakesOver(unittest.TestCase):
    """실물 파일이 data/public/ 에 생기면 실물을 쓰고 placeholder 가 False 가 된다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pub_")
        self.old = config.PUBLIC_DIR
        # 작은 대체 파일을 직접 만든다(프로젝트의 data/public/ 내용에 기대지 않는다).
        with open(os.path.join(self.tmp, config.PLACEHOLDER_1048), "w", encoding="utf-8-sig", newline="") as f:
            f.write("연월,업종명,이용자구분,성별,이용자수,이용건수,이용금액\r\n")
            f.write("202301,한식 음식점업,제주도민,남성,1,2,300\r\n")
        with open(os.path.join(self.tmp, config.PLACEHOLDER_520), "w", encoding="utf-8", newline="") as f:
            f.write("base_year_month,sex,age_range,time_range,visit_population_count\r\n")
            f.write("202301,남성,20대,01시,10\r\n")
        config.PUBLIC_DIR = self.tmp
        P.reset()

    def tearDown(self):
        config.PUBLIC_DIR = self.old
        P.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_placeholder_only(self):
        pub = P.get(force=True)
        self.assertTrue(pub.d1048.placeholder)
        self.assertTrue(pub.d520.placeholder)
        self.assertTrue(pub.placeholder)
        self.assertEqual(pub.d1048.rows, 1)
        self.assertIn("대체 데이터 사용 중", planner.public_scope()["notice"])

    def test_hub_file_english_headers_and_name_quirks(self):
        # 허브에서 받은 실물의 꼴: 영문 컬럼명, 쉼표 없는 업종명, 한 달의 깨진 글자, 같은 연령대 이름이 두 줄.
        with open(os.path.join(self.tmp, "업종별성별카드이용정보.csv"), "w", encoding="utf-8-sig", newline="") as f:
            f.write("base_year_month,biz_nm,user_type,sex,user_count,use_count,use_amount\r\n")
            f.write("202111,기타음식료품위주종합소매업,내국인,남성,1,2,100\r\n")
            f.write("202112,기타음?식료품위주종합소매업,내국인,남성,1,2,200\r\n")
            f.write("202112,피자 햄버거 샌드위치 및 유사 음식점업,내국인,남성,1,2,50\r\n")
        with open(os.path.join(self.tmp, "제주도방문내국인유동인구추이.csv"), "w", encoding="utf-8-sig", newline="") as f:
            f.write("base_year_month,sex,age_range,time_range,visit_population_count\r\n")
            f.write("202207,남성,20대,00시,100.5\r\n202207,남성,20대,00시,50.5\r\n202206,남성,20대,24시,7\r\n")
        pub = P.get(force=True)
        self.assertFalse(pub.placeholder)
        self.assertEqual([p["value"] for p in pub.monthly_1048("기타음식료품위주종합소매업")], [100, 200])
        self.assertTrue(pub.has_biz("피자, 햄버거, 샌드위치 및 유사 음식점업"))
        self.assertEqual(pub.value_1048("2021-12", "피자, 햄버거, 샌드위치 및 유사 음식점업"), 50)
        self.assertEqual(pub.monthly_520()[-1]["value"], 151)
        rows = dict((r["label"], r["pct"]) for r in pub.shares_520("hour", bins=P.CARD_SLOT_BINS)["rows"])
        self.assertEqual(rows["밤(22시~07시)"], 100.0)          # 00시와 24시는 둘 다 자정

    def test_real_1048_cp949_other_column_names_and_file_name(self):
        path = os.path.join(self.tmp, "허브에서_받은_파일.csv")
        with open(path, "w", encoding="cp949", newline="") as f:
            f.write("기준연월,업종,이용자 구분,성별,이용자 수,이용 건수,이용 금액\r\n")
            f.write("202401,한식 음식점업,제주도민,남성,10,20,3000\r\n")
            f.write("202401,한식 음식점업,내국인,여성,5,7,1000\r\n")
            f.write("202402,한식 음식점업,내국인,여성,1,2,500\r\n")
        pub = P.get(force=True)
        self.assertFalse(pub.d1048.placeholder)
        self.assertEqual(pub.d1048.file, "허브에서_받은_파일.csv")
        self.assertEqual(pub.d1048.rows, 3)
        self.assertEqual((pub.d1048.first, pub.d1048.last), ("2024-01", "2024-02"))
        self.assertEqual(pub.value_1048("2024-01", "한식 음식점업"), 4000)
        self.assertTrue(pub.d520.placeholder)          # 520 은 아직 대체 파일
        self.assertTrue(pub.placeholder)

    def test_real_520_with_bom(self):
        path = os.path.join(self.tmp, "제주도방문내국인유동인구추이.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("base_year_month,sex,age_range,time_range,visit_population_count\n")
            f.write("202401,남성,20대,01시,100\n202401,여성,70대 이상,13시,300\n")
        pub = P.get(force=True)
        self.assertFalse(pub.d520.placeholder)
        self.assertEqual(pub.d520.rows, 2)
        self.assertEqual(pub.monthly_520()[0]["value"], 400)

    def test_unreadable_and_hidden_csv_do_not_break_loading(self):
        # 맥에서 옮길 때 생기는 ._이름.csv(글자가 아닌 바이트)나 깨진 CSV 가 섞여도 실물 두 파일은 읽힌다.
        for name in ("업종별성별카드이용정보.csv", "제주도방문내국인유동인구추이.csv"):
            src = os.path.join(self.old, name)
            if not os.path.exists(src):
                self.skipTest("허브 실물 파일이 없다")
            shutil.copy(src, os.path.join(self.tmp, name))
        junk = b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        " + bytes(range(128, 256)) * 4
        for name in ("._업종별성별카드이용정보.csv", "~$제주도방문내국인유동인구추이.csv", "0깨진파일.csv"):
            with open(os.path.join(self.tmp, name), "wb") as f:
                f.write(junk)
        P.reset()
        pub = P.get()
        self.assertTrue(pub.d1048.ok, pub.d1048.error)
        self.assertTrue(pub.d520.ok, pub.d520.error)
        self.assertEqual(pub.d1048.file, "업종별성별카드이용정보.csv")
        self.assertFalse(pub.placeholder)

    def test_unrelated_csv_is_ignored(self):
        with open(os.path.join(self.tmp, "메모.csv"), "w", encoding="utf-8") as f:
            f.write("a,b\n1,2\n")
        pub = P.get(force=True)
        self.assertTrue(pub.d1048.placeholder)


if __name__ == "__main__":
    unittest.main()
