# -*- coding: utf-8 -*-
"""단위 표시(dim_tags.json), 낱말 사전, 허브·센터 대응표가 명세서와 어긋나지 않는지."""
import unittest

from tests._base import ROOT  # noqa: F401

from app import catalog, config, planner


class DimTags(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = catalog.get()

    def nos_with(self, unit, derived=False):
        return sorted(no for no in self.cat.order
                      if unit in self.cat.units(no) and (derived or not self.cat.units(no)[unit]["derived"]))

    def test_every_rule_points_to_real_sheet_and_column(self):
        for r in self.cat.tags["columns"]:
            targets = self.cat.order if r["ds"] == "*" else r["ds"]
            hit = [no for no in targets if self.cat.column(no, r["col"])]
            self.assertTrue(hit, "어느 시트에도 없는 컬럼: %s" % r["col"])
            if r["ds"] != "*":
                for no in r["ds"]:
                    self.assertTrue(self.cat.has(no), no)
                    self.assertIsNotNone(self.cat.column(no, r["col"]), "%s 에 %s 없음" % (no, r["col"]))
            self.assertTrue(r["evidence"])
            if r.get("unit"):
                self.assertIn(r["unit"], self.cat.tags["units"])
            if r.get("scheme") and r["scheme"] in self.cat.tags["schemes"]:
                self.assertTrue(self.cat.tags["schemes"][r["scheme"]]["evidence"])

    def test_every_dataset_belongs_to_one_family(self):
        seen = {}
        for key, fam in self.cat.tags["families"].items():
            for no in fam["datasets"]:
                self.assertTrue(self.cat.has(no), no)
                self.assertNotIn(no, seen, "%s 가 두 계열에 있음" % no)
                seen[no] = key
            for m in fam["measures"]:
                self.assertTrue(any(self.cat.column(no, m) for no in fam["datasets"]), "%s: %s" % (key, m))
        self.assertEqual(sorted(seen), sorted(self.cat.order))

    def test_unit_sets_match_what_the_sheets_hold(self):
        self.assertEqual(self.nos_with("bjd"), ["1-07", "1-08", "1-09", "1-10", "1-12", "1-14", "1-16", "1-19", "1-22", "1-24"])
        self.assertEqual(self.nos_with("district7"), ["1-08", "1-12", "1-14", "1-16", "4-11", "4-13", "4-15"])
        self.assertEqual(self.nos_with("district6"), ["1-11", "1-13", "1-15"])
        self.assertEqual(self.nos_with("tour_type"), ["1-07", "1-08", "1-11", "1-12", "1-13", "1-14", "1-15", "1-16"])
        self.assertEqual(self.nos_with("dow"), ["1-11", "1-12", "1-15", "1-16", "1-21", "1-22", "1-23", "1-30", "1-31",
                                                "3-02", "3-04", "3-09", "3-10", "4-06"])
        self.assertEqual(len(self.nos_with("slot")), 19)
        self.assertEqual(len(self.nos_with("age")), 22)
        self.assertEqual(self.nos_with("age"), self.nos_with("sex"))
        self.assertEqual(self.nos_with("nationality"), ["4-04", "4-05", "4-06", "4-08", "4-09", "4-12", "4-13", "4-14", "4-15"])

    def test_dow_and_slot_together_only_in_golf_and_pos_among_spend(self):
        both = [no for no in self.cat.order if self.cat.subject(no) == "spend"
                and "dow" in self.cat.units(no) and "slot" in self.cat.units(no)]
        self.assertEqual(both, ["1-15", "1-16", "1-21", "1-22", "1-23"])

    def test_same_column_name_different_meaning(self):
        self.assertEqual(self.cat.tag("1-08", "area_type")["unit"], "urban")
        self.assertEqual(self.cat.tag("1-13", "area_type")["unit"], "district6")
        self.assertIsNone(self.cat.tag("2-01", "nationality")["unit"])
        self.assertEqual(self.cat.units("1-14")["slot"]["scheme"], "card5")
        self.assertEqual(self.cat.units("1-22")["slot"]["scheme"], "pos7")
        self.assertEqual(self.cat.units("4-15")["slot"]["scheme"], "hour24")

    def test_scheme_values_agree_with_reference_tables(self):
        d = self.cat.dataset("1-14")
        self.assertEqual(self.cat.tags["schemes"]["card5"]["values"], [r[0] for r in d["refs"]["시간대"]])
        self.assertEqual(self.cat.tags["schemes"]["card10"]["values"][:7], [r[0] for r in d["refs"]["연령대"]])
        ex = self.cat.column("1-22", "age")["example"]
        self.assertEqual(self.cat.tags["schemes"]["pos6"]["values"], [x.strip() for x in ex.split(",")])
        ex = self.cat.column("1-22", "time")["example"]
        self.assertEqual(self.cat.tags["schemes"]["pos7"]["values"], [x.strip() for x in ex.split(",")])

    def test_core_values_are_known_focus_keys(self):
        core = self.cat.tags["need_units"]["age"]["core_values"]
        for key in core:
            self.assertIn(key, self.cat.tags["schemes"]["card10"]["focus"])
            self.assertTrue(self.cat.focus("1-14", "age", key)["separable"])

    def test_focus_separability_comes_from_value_ranges(self):
        self.assertTrue(self.cat.focus("1-14", "age", "20대")["separable"])
        f = self.cat.focus("1-22", "age", "20대")
        self.assertFalse(f["separable"])
        self.assertEqual(f["values"], ["20(이하)"])
        self.assertTrue(self.cat.focus("1-22", "age", "30대")["separable"])
        self.assertEqual(self.cat.focus("1-22", "dow", "토요일")["values"], ["토"])
        self.assertFalse(self.cat.focus("1-22", "dow", "공휴일")["separable"])


class Values(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = catalog.get()

    def test_condition_values_checked_against_spec(self):
        ok = self.cat.value_evidence("1-14", "bjd", "노형동")
        self.assertTrue(ok["ok"])
        self.assertEqual((ok["sheet"], ok["col_no"]), ("1-14", 5))
        self.assertIn("73개 법정동", ok["note"])
        self.assertTrue(self.cat.value_evidence("1-14", "biz_cd", "I56111")["ok"])
        self.assertFalse(self.cat.value_evidence("1-14", "biz_cd", "I99999")["ok"])
        self.assertFalse(self.cat.value_evidence("1-14", "bjd", "없는동")["ok"])
        self.assertFalse(self.cat.value_evidence("1-13", "bjd", "노형동")["ok"])      # v01 에는 bjd 가 없다
        self.assertFalse(self.cat.value_evidence("1-14", "pt_year", "2024")["ok"])   # 파티션 컬럼
        self.assertTrue(self.cat.value_evidence("1-14", "dstrct_se", "제주시 중부(동)")["ok"])
        self.assertTrue(self.cat.value_evidence("1-22", "biz_small_cd", "Q13007")["ok"])
        self.assertTrue(self.cat.value_evidence("1-22", "bjd_nm", "애월읍")["ok"])
        self.assertTrue(self.cat.value_evidence("4-15", "emd_nm", "서귀포시 성산읍")["ok"])
        self.assertFalse(self.cat.value_evidence("4-15", "emd_nm", "성산읍")["ok"])

    def test_example_value_error_is_not_used(self):
        # 1-08 의 예시는 biz_cd=G47119 에 biz_nm=건강보조식품 소매업으로 적혀 있지만 참고표로는 다른 업종이다.
        self.assertEqual(self.cat.biz_name("G47119"), "기타 대형 종합 소매업")
        self.assertEqual(self.cat.biz_code("건강보조식품 소매업"), "G47216")
        self.assertEqual(self.cat.biz_code("한식음식점업"), "I56111")              # 공백을 지우고 맞춘다

    def test_region_names(self):
        self.assertEqual(len(self.cat.bjd_list()), 73)
        self.assertEqual(len(self.cat.emd_list()), 43)
        self.assertEqual(len(self.cat.district7_list()), 7)
        self.assertEqual(len(self.cat.biz42()), 42)
        r = self.cat.resolve_region("성산읍에 오는")
        self.assertEqual((r["bjd"], r["emd"]), ("성산읍", "서귀포시 성산읍"))
        r = self.cat.resolve_region("삼도일동")
        self.assertEqual((r["bjd"], r["emd"]), ("삼도일동", None))

    def test_wolpyeong_is_two_places(self):
        self.assertEqual(self.cat.bjd_ambiguous(), {"월평동": ["제주시 중부(동)", "서귀포시 중부"]})
        r = self.cat.resolve_region("월평동")
        self.assertEqual(r["ambiguous"], ["제주시 중부(동)", "서귀포시 중부"])
        r = self.cat.resolve_region("서귀포시 월평동")
        self.assertEqual((r["ambiguous"], r["district7"]), ([], "서귀포시 중부"))

    def test_month_range(self):
        self.assertTrue(self.cat.month_in_range("1-14", "2023-08"))
        self.assertFalse(self.cat.month_in_range("1-14", "2023-07"))
        self.assertFalse(self.cat.month_in_range("1-13", "2025-07"))
        self.assertIsNone(self.cat.month_in_range("3-02", "2024-01"))
        self.assertIsNone(self.cat.month_in_range("2-01", "2019-12"))

    def test_export_notes(self):
        e = self.cat.export_info("1-14")
        self.assertFalse(e["allowed"])
        text = " ".join(e["notes"])
        for word in ("10 미만", "스프레드시트", "캡처", "출처"):
            self.assertIn(word, text)
        self.assertTrue(self.cat.export_info("3-02")["allowed"])


class Dictionaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = catalog.get()
        cls.al = planner.aliases()

    def test_alias_codes_exist_and_words_unique(self):
        seen = {}
        for row in self.al["biz"]:
            self.assertIsNotNone(self.cat.biz_name(row["biz_cd"]), row)
            self.assertTrue(row["basis"])
            for w in row["words"]:
                self.assertNotIn(w, seen, "낱말이 두 업종에 걸림: %s" % w)
                seen[w] = row["biz_cd"]
        self.assertEqual(seen["카페"], "I56220")
        self.assertEqual(self.cat.biz_name("I56220"), "비알콜 음료점업")

    def test_card_to_pos_codes_exist(self):
        for code, pos in self.al["card_to_pos"].items():
            self.assertIsNotNone(self.cat.biz_name(code), code)
            self.assertEqual(self.cat.pos_biz(pos["column"], pos["code"]), pos["name"], pos)
        self.assertEqual(self.al["card_to_pos"]["I56220"]["code"], "Q13007")

    def test_hub_center_map_columns_exist(self):
        m = catalog.read_json(config.HUB_MAP_JSON)
        self.assertIn("추정", m["relation"])
        for kind in ("d1048", "d520"):
            for no in m[kind]["center_datasets"]:
                for col in m[kind]["columns"]:
                    self.assertIsNotNone(self.cat.column(no, col["center"]), "%s 에 %s 없음" % (no, col["center"]))
        ut = [c for c in m["d1048"]["columns"] if c["key"] == "user_type"][0]["values"]
        self.assertEqual(sorted(ut), sorted(self.cat.column_values("1-14", "user_type")["values"]))


if __name__ == "__main__":
    unittest.main()
