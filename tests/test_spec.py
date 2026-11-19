# -*- coding: utf-8 -*-
"""명세서 변환(tools/build_spec.py)과 spec.json."""
import json
import os
import subprocess
import sys
import unittest

from tests._base import ROOT

from app import catalog, config

XLSX = os.path.join(config.SPEC_DIR, "데이터명세서_kr.xlsx")


def load_spec():
    with open(config.SPEC_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


class SpecCounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = load_spec()
        cls.by = dict((d["no"], d) for d in cls.spec["datasets"])

    def test_version_and_counts_are_counted_by_program(self):
        s = self.spec
        self.assertEqual(s["version"], "v13")
        self.assertEqual(s["date"], "2026-11-04")
        self.assertEqual(s["counts"]["datasets"], len(s["datasets"]))
        self.assertEqual(s["counts"]["datasets"], 71)
        self.assertEqual(s["counts"]["ref_tables"], 2)
        self.assertEqual(s["counts"]["columns"], sum(len(d["columns"]) for d in s["datasets"]))
        self.assertEqual(s["counts"]["columns"], 906)
        self.assertEqual(s["counts"]["sheets"], 74)
        self.assertEqual(s["problems"], [])

    def test_label_never_says_65_or_73(self):
        label = catalog.get().label()
        self.assertEqual(label, "명세서 v13(2026-11-04) 기준 데이터 71종, 참고표 2종")

    def test_partition_columns_marked(self):
        d = self.by["1-14"]
        part = [c["en"] for c in d["columns"] if c["partition"]]
        self.assertEqual(part, ["pt_year", "pt_month"])
        self.assertEqual(len(d["columns"]), 15)
        self.assertEqual(len(catalog.get().columns("1-14")), 13)

    def test_column_names_have_no_surrounding_spaces(self):
        for d in self.spec["datasets"]:
            for c in d["columns"]:
                self.assertEqual(c["en"], c["en"].strip(), d["no"])
                self.assertTrue(c["en"], d["no"])

    def test_two_header_styles_and_shifted_sheet(self):
        self.assertEqual(self.by["1-14"]["layout"]["header_label"], "컬럼명(영문)")
        self.assertEqual(self.by["1-22"]["layout"]["header_label"], "Column 명")
        self.assertEqual(self.by["1-32"]["layout"]["header_row"], 10)
        self.assertTrue(self.by["1-32"]["layout"]["shifted"])
        self.assertEqual(self.by["1-32"]["name"], "탐나는전 이용 현황")
        self.assertEqual(self.by["1-32"]["columns"][0]["en"], "pt_year")
        shifted = [d["no"] for d in self.spec["datasets"] if d["layout"]["shifted"]]
        self.assertEqual(shifted, ["1-32"])

    def test_provider_comes_from_each_sheet(self):
        self.assertEqual(self.by["4-10"]["provider"], "제주특별자치도(SKT, KT)")
        self.assertEqual(self.by["4-10"]["provider_in_list"], "제주특별자치도(SKT)")
        self.assertEqual(self.by["4-11"]["provider"], "제주특별자치도(SKT)")
        fields = set((m["no"], m["field"]) for m in self.spec["list_mismatches"])
        self.assertIn(("4-10", "제공처"), fields)
        self.assertIn(("5-06", "데이터명"), fields)

    def test_periods_three_notations(self):
        p = self.by["1-14"]["period"]
        self.assertEqual((p["start"], p["end"], p["open_ended"], p["kind"]), ("2023-08", None, True, "open"))
        p = self.by["1-07"]["period"]
        self.assertEqual((p["start"], p["end"], p["kind"]), ("2017-01", "2025-06", "range"))
        p = self.by["1-01"]["period"]
        self.assertEqual((p["start"], p["end"]), ("2016-09", "2018-12"))
        p = self.by["4-15"]["period"]          # '2025 .04. 01 ~현재까지'
        self.assertEqual((p["start"], p["end"], p["open_ended"]), ("2025-04", None, True))
        p = self.by["4-10"]["period"]          # KT 구간과 SKT 구간 두 줄
        self.assertEqual((p["start"], p["end"]), ("2017-01", "2025-04"))
        self.assertEqual([s["label"] for s in p["segments"]], ["KT", "SKT"])
        p = self.by["1-30"]["period"]          # '2020년 1월부터 … 누적 업데이트'
        self.assertEqual((p["start"], p["kind"]), ("2020-01", "accumulating"))
        self.assertEqual(self.by["2-01"]["period"]["kind"], "snapshot")
        self.assertEqual(self.by["4-02"]["period"]["kind"], "before")

    def test_sheets_without_period_are_null(self):
        none = sorted(d["no"] for d in self.spec["datasets"] if d["period"] is None)
        self.assertEqual(none, ["3-01", "3-02", "3-03", "3-04", "3-05", "3-06", "3-07", "3-08", "3-09", "3-10", "5-01", "5-02"])
        self.assertEqual(self.spec["counts"]["period_range_checkable"], 48)
        self.assertEqual(catalog.get().period_text("3-01"), "수록 기간이 명세서에 없음")

    def test_age_format_note_is_not_taken_as_period(self):
        # 4-04 설명란에는 'age 컬럼은 2017.1.5. 기준으로…' 줄이 있다. 기간은 2016-11 ~ 2020-09 여야 한다.
        p = self.by["4-04"]["period"]
        self.assertEqual((p["start"], p["end"]), ("2016-11", "2020-09"))

    def test_reference_tables(self):
        d = self.by["1-14"]
        self.assertEqual(len(d["refs"]["업종코드 및 업종명"]), 42)
        self.assertIn(["I56111", "한식 음식점업"], d["refs"]["업종코드 및 업종명"])
        self.assertEqual(len(d["refs"]["법정동"]), 74)                      # 월평동이 두 줄
        self.assertEqual(len(set(r[0] for r in d["refs"]["법정동"])), 73)
        self.assertEqual(len(self.by["1-07"]["refs"]["법정동"]), 73)
        self.assertEqual(len(d["refs"]["지역구분"]), 7)
        self.assertEqual([r[0] for r in d["refs"]["시간대"]], ["오전", "점심", "오후", "저녁", "밤"])
        self.assertEqual(len(self.by["4-15"]["refs"]["행정동코드 및 행정동명"]), 43)
        self.assertEqual(len(self.by["4-15"]["refs"]["국적코드(단기체류외국인 국적)"]), 31)
        self.assertEqual(self.by["2-01"]["refs"]["구매처판매처구분"], ["구매", "판매"])   # 번호 열 없는 표
        cells = dict((m["key"], m["cell"]) for m in d["ref_tables"])
        self.assertEqual(cells["법정동"], "A36")

    def test_biz_table_same_on_nine_sheets(self):
        base = self.by["1-14"]["refs"]["업종코드 및 업종명"]
        for no in ("1-07", "1-08", "1-09", "1-10", "1-11", "1-12", "1-13", "1-14", "1-17"):
            self.assertEqual(self.by[no]["refs"]["업종코드 및 업종명"], base, no)

    def test_ref_sheets(self):
        rs = dict((r["name"], r) for r in self.spec["ref_sheets"])
        self.assertEqual(rs["(참고표1) 업종코드표"]["row_count"], 111)
        self.assertIn(["Q13", "커피/음료", "Q13007", "커피전문점"], rs["(참고표1) 업종코드표"]["rows"])
        self.assertEqual(rs["(참고표2) 메뉴코드표"]["row_count"], 4889)
        self.assertIsNone(rs["(참고표2) 메뉴코드표"]["rows"])

    def test_duplicate_headers(self):
        self.assertEqual(self.spec["duplicate_headers"], [["1-02", "1-05"], ["1-03", "1-06"], ["2-01", "2-02"], ["5-05", "5-06"]])

    def test_export_flags(self):
        self.assertEqual(self.spec["counts"]["export"], {"불가능": 49, "가능": 22})
        self.assertEqual(self.by["1-14"]["export"], "불가능")
        self.assertIn("가공 형태로만 반출", self.by["1-14"]["export_note"])
        self.assertEqual(self.by["3-01"]["export"], "가능")

    def test_footnote_outside_table(self):
        self.assertTrue(any("k값 3미만" in x for x in self.by["1-18"]["footnotes"]))


@unittest.skipUnless(os.path.exists(XLSX), "명세서 xlsx 가 data/spec/ 에 없다")
class BuildSpecScript(unittest.TestCase):
    def test_rebuild_matches_and_runs_isolated(self):
        # 외부에서 받은 파일을 읽으므로 -I(격리 모드)로 돌려도 동작해야 한다.
        # -I 는 PYTHONUTF8 환경변수를 무시한다. 한국어 Windows 에서는 파이프 출력이 cp949 가 되므로 -X utf8 로 고정한다.
        r = subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(ROOT, "tools", "build_spec.py"), "--check"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT)
        out = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("데이터 71종, 참고표 2종, 컬럼 906개", out)
        self.assertIn("같음", out)


if __name__ == "__main__":
    unittest.main()
