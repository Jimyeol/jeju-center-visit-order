# -*- coding: utf-8 -*-
"""규칙 계획기·검증기: 시나리오 3개가 기대한 주문서를 내는지, 그것이 단위 집합의 비교에서 나오는지."""
import json
import unittest

from tests._base import ROOT, S1, S2, S3, TODAY  # noqa: F401

from app import catalog, config, planner, validator
from app import public_data as P

API_KEYS = ["run_id", "mode_used", "fallback_reason", "need", "public_answer", "gaps", "order", "validation",
            "trace", "sent_to_llm", "limits"]
ITEM_KEYS = ["dataset_no", "dataset_name", "provider", "period_text", "months", "conditions", "use_columns",
             "export", "downloads", "why", "evidence"]


def plan(text=None, slots=None):
    return planner.plan_rule(text=text, slots=slots, today=TODAY)


def conds(item):
    return [(c["column"], c["value"]) for c in item["conditions"]]


class ApiShape(unittest.TestCase):
    def test_contract_keys(self):
        for text in (S1, S2, S3):
            r = plan(text)
            for k in API_KEYS:
                self.assertIn(k, r)
            self.assertEqual(r["mode_used"], "rule")
            self.assertEqual(sorted(k for k in ("region", "biz", "who", "units") if k in r["need"]), ["biz", "region", "units", "who"])
            for u in r["need"]["units"]:
                self.assertEqual(sorted(k for k in u if k in ("key", "label", "in_public")), ["in_public", "key", "label"])
            pa = r["public_answer"]
            for k in ("available", "reason", "source", "series", "metric", "seasonal", "shares", "notes"):
                self.assertIn(k, pa)
            for g in r["gaps"]:
                self.assertIn(g["status"], ("센터 데이터로 메움", "메울 데이터 없음"))
            o = r["order"]
            for k in ("items", "alternatives", "no_data", "total_downloads"):
                self.assertIn(k, o)
            for it in o["items"]:
                for k in ITEM_KEYS:
                    self.assertIn(k, it)
                for m in it["months"]:
                    self.assertTrue(set(["basis", "role", "ym"]) <= set(m), m)      # 계약의 세 칸은 그대로, 덧붙인 칸(index 등)은 있어도 된다
                for c in it["conditions"]:
                    self.assertTrue(set(["col_no", "note", "sheet"]) <= set(c["evidence"]), c)
                self.assertIn("allowed", it["export"])
                self.assertTrue(it["export"]["notes"])
            for a in o["alternatives"]:
                for k in ("title", "items", "downloads", "gain", "give_up"):
                    self.assertIn(k, a)
            for n in o["no_data"]:
                for k in ("unit", "explain", "instead"):
                    self.assertIn(k, n)
            self.assertTrue(set(["issues", "passed", "total"]) <= set(r["validation"]))     # 덧붙인 칸(내역, 확인 못 함)은 있어도 된다
            for t in r["trace"]:
                self.assertEqual(sorted(k for k in t if k != "sub"), ["args_summary", "note", "result_summary", "step", "tool"])
            self.assertEqual(r["sent_to_llm"]["center_values"], 0)
            self.assertFalse(r["sent_to_llm"]["purpose_text"])
            json.dumps(r, ensure_ascii=False)        # JSON 으로 바꿀 수 있어야 한다

    def test_options(self):
        o = planner.options()
        self.assertEqual(sorted(k for k in o if k in ("who", "what", "when", "regions", "units", "examples")),
                         ["examples", "regions", "units", "what", "when", "who"])
        self.assertEqual(len(o["regions"]["bjd"]), 73)
        self.assertEqual(len(o["regions"]["emd"]), 43)
        self.assertEqual(len(o["regions"]["district7"]), 7)
        self.assertEqual(len(o["what"]), 43)             # 42개 업종 + 방문·유동인구
        self.assertEqual(len(o["examples"]), 3)
        self.assertEqual([e["text"] for e in o["examples"]], [S1, S2, S3])


class Scenario1(unittest.TestCase):
    """노형동 한식당: 1-14, bjd=노형동, 한식 음식점업 코드, 달 3개가 2023-08 이후, v01 을 버린 이유."""

    @classmethod
    def setUpClass(cls):
        cls.r = plan(S1)
        cls.item = cls.r["order"]["items"][0]

    def test_need(self):
        n = self.r["need"]
        self.assertEqual((n["region"], n["biz"], n["subject"]), ("노형동", "한식 음식점업", "spend"))
        keys = [u["key"] for u in n["units"]]
        for k in ("dong", "biz", "slot", "user_type", "age", "sex"):
            self.assertIn(k, keys)
        self.assertNotIn("dow", keys)
        self.assertEqual(n["biz_detail"]["alias"], "한식당")

    def test_order_is_1_14(self):
        self.assertEqual(len(self.r["order"]["items"]), 1)
        self.assertEqual(self.item["dataset_no"], "1-14")
        self.assertEqual(self.item["dataset_name"], "카드 이용 현황(시간대)_v02")
        self.assertEqual(self.item["provider"], "제주특별자치도(비씨카드)")
        self.assertEqual(self.r["order"]["alternatives"], [])
        self.assertEqual(self.r["order"]["no_data"], [])

    def test_conditions_with_spec_evidence(self):
        self.assertEqual(conds(self.item), [("bjd", "노형동"), ("biz_cd", "I56111")])
        ev = self.item["conditions"][0]["evidence"]
        self.assertEqual((ev["sheet"], ev["col_no"]), ("1-14", 5))
        self.assertIn("73개 법정동", ev["note"])
        ev = self.item["conditions"][1]["evidence"]
        self.assertEqual((ev["sheet"], ev["col_no"]), ("1-14", 6))
        self.assertIn("[참고3]", ev["note"])
        self.assertEqual(self.item["conditions"][1]["value_label"], "한식 음식점업")

    def test_three_months_inside_v02_range(self):
        months = self.item["months"]
        self.assertEqual(len(months), 3)
        self.assertEqual(sorted(m["role"] for m in months), ["많은 달", "적은 달", "평균에 가까운 달"])
        last = P.get().d1048.last
        for m in months:
            self.assertGreaterEqual(m["ym"], "2023-08")
            self.assertLessEqual(m["ym"], last)                 # 공개 데이터와 겹치는 범위에서 골랐다
            self.assertIn("계절 지수", m["basis"])
        self.assertEqual(self.item["downloads"], 3)
        self.assertEqual(self.r["order"]["total_downloads"], 3)

    def test_use_columns_exclude_partition_and_fixed(self):
        cols = [c["en"] for c in self.item["use_columns"]]
        self.assertEqual(sorted(cols), sorted(["time", "user_type", "age", "sex", "user_cnt", "use_cnt", "use_amt"]))
        self.assertNotIn("pt_year", cols)

    def test_reason_v01_dropped_is_in_trace(self):
        lines = [t for t in self.r["trace"] if t["args_summary"].startswith("1-13")]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["result_summary"], "버림")
        self.assertIn("법정동 컬럼이 없다", lines[0]["note"])
        self.assertIn("6개 권역", lines[0]["note"])
        self.assertIn("1-13", self.item["why"])

    def test_public_answer_and_gaps(self):
        pa = self.r["public_answer"]
        self.assertTrue(pa["available"])
        self.assertIn("한식 음식점업", pa["source"])
        self.assertEqual(len(pa["seasonal"]), 12)
        self.assertGreaterEqual(len(pa["shares"]), 2)
        gaps = dict((g["key"], g["status"]) for g in self.r["gaps"])
        self.assertEqual(gaps["dong"], "센터 데이터로 메움")
        self.assertEqual(gaps["slot"], "센터 데이터로 메움")
        self.assertNotIn("biz", gaps)                             # 업종은 공개 데이터에 있다

    def test_validation_all_passed(self):
        v = self.r["validation"]
        self.assertEqual(v["issues"], [])
        self.assertEqual(v["passed"], v["total"])
        self.assertGreater(v["total"], 10)

    def test_export_line(self):
        self.assertFalse(self.item["export"]["allowed"])
        self.assertIn("데이터 반출 불가능", self.item["export"]["notes"][0])

    def test_slots_give_same_order(self):
        r = plan(slots={"who": "all", "what": "biz:I56111", "when": "slot", "region": "노형동", "units": ["user_type", "age", "sex"]})
        it = r["order"]["items"][0]
        self.assertEqual(it["dataset_no"], "1-14")
        self.assertEqual(conds(it), conds(self.item))
        self.assertEqual([m["ym"] for m in it["months"]], [m["ym"] for m in self.item["months"]])


class Scenario2(unittest.TestCase):
    """애월읍 카페: 한 데이터로 안 됨 → 카드 1-12 + 1-14 따로 / 포스 1-22 한 종."""

    @classmethod
    def setUpClass(cls):
        cls.r = plan(S2)
        cls.alts = dict((a["key"], a) for a in cls.r["order"]["alternatives"])

    def test_need(self):
        n = self.r["need"]
        self.assertEqual((n["region"], n["biz"]), ("애월읍", "비알콜 음료점업"))
        keys = [u["key"] for u in n["units"]]
        for k in ("dong", "biz", "dow", "slot", "age", "dow_x_slot"):
            self.assertIn(k, keys)
        self.assertEqual(n["focus"], {"dow": ["토요일"], "slot": ["점심"], "age": ["20대"]})
        self.assertTrue(n["cross"])

    def test_says_one_dataset_is_not_enough(self):
        res = [t["result_summary"] for t in self.r["trace"] if t["tool"] == "catalog_search"]
        self.assertTrue(any("한 데이터로 필요한 단위를 다 채우는 것이 없다" in x for x in res))
        self.assertEqual(len(self.r["order"]["alternatives"]), 2)

    def test_split_alternative_is_card_1_12_and_1_14(self):
        a = self.alts["split"]
        self.assertEqual([it["dataset_no"] for it in a["items"]], ["1-12", "1-14"])
        self.assertEqual(a["downloads"], 6)                      # 2종 × 3달
        self.assertEqual(a["downloads"], sum(len(it["months"]) for it in a["items"]))
        self.assertIn("dow_x_slot", a["give_up_keys"])
        self.assertTrue(any("한 표에서 함께 볼 수 없다" in g for g in a["give_up"]))
        self.assertTrue(any("20대" in g for g in a["gain"]))
        for it in a["items"]:
            self.assertEqual(conds(it), [("bjd", "애월읍"), ("biz_cd", "I56220")])

    def test_single_alternative_is_pos_1_22(self):
        a = self.alts["single"]
        self.assertEqual([it["dataset_no"] for it in a["items"]], ["1-22"])
        self.assertEqual(a["downloads"], 3)
        it = a["items"][0]
        self.assertEqual(conds(it), [("bjd_nm", "애월읍"), ("biz_small_cd", "Q13007")])
        self.assertEqual(it["conditions"][1]["value_label"], "커피전문점")
        self.assertTrue(any("20(이하)" in g for g in a["give_up"]))
        self.assertTrue(any("표본" in g for g in a["give_up"]))
        self.assertTrue(any("교차" in g for g in a["gain"]))
        cols = [c["en"] for c in it["use_columns"]]
        self.assertIn("week", cols)
        self.assertIn("time", cols)

    def test_cross_purpose_recommends_the_one_that_keeps_cross(self):
        self.assertTrue(self.alts["single"]["recommended"])
        self.assertFalse(self.alts["split"]["recommended"])
        self.assertEqual([it["dataset_no"] for it in self.r["order"]["items"]], ["1-22"])
        self.assertLessEqual(self.r["order"]["total_downloads"], config.MAX_DOWNLOADS)

    def test_comparison_is_in_trace(self):
        lines = [t for t in self.r["trace"] if t["tool"] == "타협안 비교"]
        self.assertEqual(len(lines), 2)
        self.assertIn("2종 × 3달 = 6회", lines[0]["result_summary"])
        self.assertIn("1종 × 3달 = 3회", lines[1]["result_summary"])

    def test_public_answer_has_520_reference_for_twenties(self):
        titles = [s["title"] for s in self.r["public_answer"]["shares"]]
        self.assertTrue(any("520" in t and "20대" in t for t in titles))

    def test_validation(self):
        self.assertEqual(self.r["validation"]["issues"], [])

    def test_slots_with_focus_give_same_alternatives(self):
        r = plan(slots=planner.EXAMPLES[1]["slots"])
        self.assertEqual([[it["dataset_no"] for it in a["items"]] for a in r["order"]["alternatives"]], [["1-12", "1-14"], ["1-22"]])

    def test_three_boxes_without_focus_still_compare_value_ranges(self):
        # 화면의 세 칸 선택에는 '20대' 같은 값이 없다. 연령대를 바라면 20~60대를 각각 볼 수 있어야 채워진 것으로 본다.
        r = plan(slots={"who": "all", "what": "biz:I56220", "when": "dow_slot", "region": "애월읍", "units": ["age"]})
        alts = r["order"]["alternatives"]
        self.assertEqual([[it["dataset_no"] for it in a["items"]] for a in alts], [["1-12", "1-14"], ["1-22"]])
        self.assertEqual([a["downloads"] for a in alts], [6, 3])
        self.assertTrue(any("20(이하)" in g for g in alts[1]["give_up"]))
        self.assertTrue(alts[1]["recommended"])
        self.assertEqual(r["validation"]["issues"], [])
        # 연령대를 바라지 않으면 1-22 한 종으로 다 채워진다
        r = plan(slots={"who": "all", "what": "biz:I56220", "when": "dow_slot", "region": "애월읍", "units": []})
        self.assertEqual([it["dataset_no"] for it in r["order"]["items"]], ["1-22"])
        self.assertEqual(r["order"]["alternatives"], [])


class Scenario3(unittest.TestCase):
    """성산읍 외국인 관광객: 공개 데이터로 답 못 함, 4-15, 연령대는 메울 데이터 없음."""

    @classmethod
    def setUpClass(cls):
        cls.r = plan(S3)

    def test_public_data_cannot_answer(self):
        pa = self.r["public_answer"]
        self.assertFalse(pa["available"])
        self.assertIn("내국인", pa["reason"])
        self.assertEqual(pa["series"], [])
        self.assertEqual(pa["shares"], [])
        self.assertTrue(all(not u["in_public"] for u in self.r["need"]["units"]))

    def test_order_is_4_15_with_emd_condition(self):
        items = self.r["order"]["items"]
        self.assertEqual([it["dataset_no"] for it in items], ["4-15"])
        self.assertEqual(conds(items[0]), [("emd_nm", "서귀포시 성산읍")])
        self.assertEqual([c["en"] for c in items[0]["use_columns"]], ["time", "visit_pop_cnt"])
        self.assertEqual(self.r["order"]["alternatives"], [])

    def test_age_has_no_data(self):
        nd = self.r["order"]["no_data"]
        self.assertEqual([n["unit"] for n in nd], ["연령대"])
        self.assertIn("4-15", nd[0]["explain"])
        self.assertTrue(any("국적" in x for x in nd[0]["instead"]))
        gaps = dict((g["key"], g["status"]) for g in self.r["gaps"])
        self.assertEqual(gaps["age"], "메울 데이터 없음")
        self.assertEqual(gaps["slot"], "센터 데이터로 메움")
        self.assertEqual(gaps["dong"], "센터 데이터로 메움")
        self.assertTrue(any(t["tool"] == "없음 판정" for t in self.r["trace"]))

    def test_months_are_reference_only_and_in_range(self):
        months = self.r["order"]["items"][0]["months"]
        self.assertEqual(len(months), 3)
        for m in months:
            self.assertTrue(m["role"].startswith("참고"))
            self.assertGreaterEqual(m["ym"], "2025-04")
            self.assertLess(m["ym"], TODAY.strftime("%Y-%m"))
            self.assertIn("명세서에 없어", m["basis"])
        notes = " ".join(self.r["order"]["items"][0]["month_notes"])
        self.assertIn("공개 데이터로 고를 수 없다", notes)

    def test_validation(self):
        self.assertEqual(self.r["validation"]["issues"], [])

    def test_slots(self):
        r = plan(slots={"who": "foreign", "what": "pop", "when": "slot", "region": "성산읍", "units": ["age"]})
        self.assertEqual([it["dataset_no"] for it in r["order"]["items"]], ["4-15"])
        self.assertEqual([n["unit"] for n in r["order"]["no_data"]], ["연령대"])


class NotHardcoded(unittest.TestCase):
    """문장에 맞춘 답이 아니라 단위 집합의 비교에서 나온다는 것을 다른 문장으로 확인한다."""

    def test_same_shape_other_place_and_biz(self):
        r = plan("한림읍에 중국집을 내려고 합니다. 시간대별로 어떤 손님이 오는지 궁금합니다.")
        it = r["order"]["items"][0]
        self.assertEqual(it["dataset_no"], "1-14")
        self.assertEqual(conds(it), [("bjd", "한림읍"), ("biz_cd", "I56112")])

    def test_thirties_can_be_separated_in_pos_so_no_compromise(self):
        # 20대와 달리 30대는 포스 연령 값 '30' 으로 따로 볼 수 있다 → 1-22 한 종으로 다 채워진다.
        r = plan("토요일 점심에 애월읍 카페에 30대가 얼마나 오는지, 요일과 시간대를 함께 보고 싶습니다.")
        self.assertEqual([it["dataset_no"] for it in r["order"]["items"]], ["1-22"])
        self.assertEqual(r["order"]["alternatives"], [])

    def test_without_cross_wish_split_or_single_card_is_enough(self):
        # '함께'가 없으면 요일×시간대 교차를 요구하지 않는다.
        r = plan("애월읍 카페에 20대가 어느 요일에 많이 오는지 알고 싶습니다.")
        self.assertEqual([it["dataset_no"] for it in r["order"]["items"]], ["1-12"])
        self.assertEqual(r["order"]["alternatives"], [])

    def test_biz_without_pos_mapping_has_no_cross_data(self):
        # 편의점은 포스(외식업) 업종코드표에 없다 → 요일×시간대 교차는 메울 데이터가 없고, 카드 두 종을 따로 받는다.
        r = plan("노형동 편의점을 요일과 시간대를 함께 보고 싶습니다.")
        self.assertIn("요일과 시간대를 한 표에서 함께", [n["unit"] for n in r["order"]["no_data"]])
        self.assertEqual([it["dataset_no"] for it in r["order"]["items"]], ["1-12", "1-14"])
        self.assertEqual(r["order"]["total_downloads"], 6)
        self.assertEqual(r["validation"]["issues"], [])

    def test_golf_has_dow_and_slot_in_one_file(self):
        r = plan("골프장을 요일과 시간대를 함께 보고 싶습니다. 애월읍입니다.")
        self.assertEqual([it["dataset_no"] for it in r["order"]["items"]], ["1-16"])
        self.assertEqual(r["order"]["alternatives"], [])

    def test_domestic_visitors_have_age(self):
        r = plan("성산읍에 오는 내국인 관광객을 연령대별, 시간대별로 알고 싶습니다.")
        self.assertEqual(r["order"]["no_data"], [])
        self.assertIn(r["order"]["items"][0]["dataset_no"], ("4-10", "4-11"))
        self.assertTrue(r["public_answer"]["available"])

    def test_legal_dong_not_in_admin_dong_list(self):
        r = plan("삼도일동 방문객을 시간대별로 보고 싶습니다.")
        self.assertEqual([n["key"] for n in r["order"]["no_data"]], ["dong"])
        self.assertIn("대응표", r["order"]["no_data"][0]["explain"])

    def test_wolpyeong_gets_district_condition(self):
        r = plan("월평동 편의점 요일별 이용")
        c = conds(r["order"]["items"][0])
        self.assertIn(("dstrct_se", "제주시 중부(동)"), c)
        self.assertIn(("dstrct_se", "서귀포시 중부"), c)
        r = plan("서귀포시 월평동 편의점 요일별 이용")
        c = conds(r["order"]["items"][0])
        self.assertEqual([x for x in c if x[0] == "dstrct_se"], [("dstrct_se", "서귀포시 중부")])

    def test_public_only_purpose_needs_no_center_data(self):
        r = plan("한식 음식점업 도 전체 흐름")
        self.assertEqual(r["order"]["items"], [])
        self.assertIn("note", r["order"])
        self.assertEqual(r["validation"]["issues"], [])

    def test_empty_input_does_not_crash(self):
        for r in (plan(""), plan(None, {}), plan("ㅁㄴㅇㄹ")):
            self.assertEqual(r["mode_used"], "rule")
            self.assertEqual(r["validation"]["issues"], [])

    def test_evaluate_is_set_comparison(self):
        need = planner.make_need(region="노형동", biz="I56111", units=["slot"])
        ev14 = planner.evaluate(need, "1-14")
        ev13 = planner.evaluate(need, "1-13")
        self.assertEqual(ev14["missing"], [])
        self.assertEqual(ev13["missing"], ["dong"])
        self.assertTrue(set(ev13["covers"]) < set(ev14["covers"]))


class MonthPicking(unittest.TestCase):
    def test_overlap_with_public_data_is_preferred(self):
        pm = planner.pick_months("1-13", biz="I56111", today=TODAY)
        self.assertEqual(len(pm["months"]), 3)
        self.assertTrue(all(m["reconcilable"] and not m["assumed"] for m in pm["months"]))
        self.assertTrue(all("2017-01" <= m["ym"] <= "2025-06" for m in pm["months"]))

    def test_no_overlap_falls_back_to_dataset_range(self):
        pm = planner.pick_months("4-11", today=TODAY)       # 2025-05 시작, 520 과 겹치는 달이 없다
        self.assertEqual(len(pm["months"]), 3)
        for m in pm["months"]:
            self.assertGreaterEqual(m["ym"], "2025-05")
            self.assertFalse(m["reconcilable"])
            self.assertTrue(m["assumed"])
            self.assertTrue(catalog.get().month_in_range("4-11", m["ym"]))

    def test_months_follow_seasonal_index(self):
        pm = planner.pick_months("1-14", biz="I56111", today=TODAY)
        picks = P.pick_season_months(pm["seasonal"])
        self.assertEqual(sorted(int(m["ym"][5:]) for m in pm["months"]), sorted(p["month"] for p in picks))

    def test_sparse_biz_falls_back_to_all_biz_index_and_says_so(self):
        from app import catalog as C
        pub = P.get()
        span = C.ym_to_int(pub.d1048.last) - C.ym_to_int(pub.d1048.first) + 1
        sparse = [c for c, n in C.get().biz42() if len(pub.monthly_1048(n)) < span]
        if not sparse:
            self.skipTest("달이 비는 업종이 없는 공개 데이터다")
        pm = planner.pick_months("1-14", biz=sparse[0], today=TODAY)
        self.assertEqual(len(pm["months"]), 3)
        self.assertTrue(any("자료가 모자람" in n and "전체 업종" in n for n in pm["notes"]))
        self.assertIn("업종 합계", pm["months"][0]["basis"])
        r = planner.plan_rule(slots={"what": "biz:" + sparse[0], "when": "slot", "region": "노형동"}, today=TODAY)
        self.assertEqual(r["public_answer"]["seasonal"], [])
        self.assertEqual(r["validation"]["issues"], [])
        self.assertEqual(len(r["order"]["items"][0]["months"]), 3)

    def test_unknown_dataset(self):
        pm = planner.pick_months("9-99", today=TODAY)
        self.assertFalse(pm["available"])
        self.assertEqual(pm["months"], [])

    def test_period_missing_is_said(self):
        pm = planner.pick_months("3-02", today=TODAY)
        self.assertTrue(any("수록 기간이 명세서에 없어" in n for n in pm["notes"]))


class Validator(unittest.TestCase):
    def setUp(self):
        self.need = planner.make_need(region="노형동", biz="한식당", units=["slot", "user_type", "age", "sex"])

    def test_bad_draft_is_caught_line_by_line(self):
        draft = {"items": [{"dataset_no": "1-13", "months": ["2016-05", "2024-08"],
                            "conditions": [{"column": "bjd", "value": "노형동"}, {"column": "biz_cd", "value": "I56111"}],
                            "use_columns": ["time", "agee", "pt_year", "age"], "why": "시간대가 있다"}]}
        b = planner.build_order(self.need, draft, today=TODAY)
        self.assertFalse(b["ok"])
        fields = sorted(set(x["field"] for x in b["issues"]))
        self.assertEqual(fields, ["condition", "month", "unit", "use_column"])
        text = " / ".join(b["validation"]["issues"])
        self.assertIn("2016-05", text)
        self.assertIn("1-13 시트에 bjd 컬럼이 없다", text)
        self.assertIn("agee", text)
        self.assertIn("파티션", text)
        self.assertIn("읍면동 단위 지역", text)
        self.assertLess(b["validation"]["passed"], b["validation"]["total"])
        # 통과한 줄만 남는다
        it = b["order"]["items"][0]
        self.assertEqual([m["ym"] for m in it["months"]], ["2024-08"])
        self.assertEqual([c["column"] for c in it["conditions"]], ["biz_cd"])
        cols = [c["en"] for c in it["use_columns"]]
        # LLM 초안의 틀린 항목(agee, 파티션 pt_year)은 빠지고, 코드가 목적에서 정한 항목은 그대로 남는다
        self.assertNotIn("agee", cols)
        self.assertNotIn("pt_year", cols)
        for en in ("time", "age", "sex", "user_type", "use_cnt", "use_amt"):
            self.assertIn(en, cols)

    def test_draft_cannot_drop_code_columns(self):
        # LLM 이 받을 항목을 줄여 내도 코드가 정한 항목은 빠지지 않는다(실행마다 주문서가 달라지는 것을 막음)
        base = planner.build_order(self.need, {"items": [{"dataset_no": "1-14"}]}, today=TODAY)
        want = [c["en"] for c in base["order"]["items"][0]["use_columns"]]
        short = planner.build_order(self.need, {"items": [{"dataset_no": "1-14", "use_columns": ["time", "age"]}]}, today=TODAY)
        got = [c["en"] for c in short["order"]["items"][0]["use_columns"]]
        self.assertEqual(got, want)
        self.assertIn("sex", got)

    def test_fixed_draft_passes(self):
        b = planner.build_order(self.need, {"items": [{"dataset_no": "1-14", "why": "법정동과 시간대"}]}, today=TODAY)
        self.assertTrue(b["ok"])
        self.assertEqual(b["order"]["items"][0]["why"], "법정동과 시간대")
        self.assertEqual(len(b["order"]["items"][0]["months"]), 3)

    def test_unknown_dataset_is_removed(self):
        b = planner.build_order(self.need, {"items": [{"dataset_no": "9-99"}]}, today=TODAY)
        self.assertFalse(b["ok"])
        self.assertEqual(b["order"]["items"], [])
        self.assertIn("명세서에 없다", b["validation"]["issues"][0])

    def test_wrong_value_and_name(self):
        order = {"items": [{"dataset_no": "1-14", "dataset_name": "카드 이용 현황(요일별)_v02", "months": [{"ym": "2024-13"}],
                            "conditions": [{"column": "bjd", "value": "노형리"}], "use_columns": [{"en": "time"}]}]}
        issues = validator.validate_order(order)
        self.assertEqual(sorted(x["field"] for x in issues), ["condition", "dataset_name", "month"])

    def test_download_cap(self):
        need = planner.make_need(region="노형동", biz="편의점", units=["dow", "slot"])      # 카드 두 종을 따로 받게 되는 목적
        b = planner.build_order(need, today=TODAY, max_downloads=4)
        self.assertLessEqual(b["order"]["total_downloads"], 4)
        self.assertTrue(b["ok"])
        self.assertEqual([len(it["months"]) for it in b["order"]["items"]], [2, 2])
        order = {"items": [{"dataset_no": "1-14", "months": [{"ym": "2024-%02d" % m} for m in range(1, 8)]}]}
        self.assertEqual([x["field"] for x in validator.validate_order(order)], ["downloads"])


class Tools(unittest.TestCase):
    def test_public_scope(self):
        sc = planner.public_scope()
        self.assertEqual(sc["d1048"]["units"], ["month", "biz", "user_type", "sex"])
        self.assertEqual(sc["d520"]["units"], ["month", "sex", "age", "slot"])
        self.assertEqual(len(sc["d1048"]["values"]["biz"]), 42)

    def test_catalog_search(self):
        r = planner.catalog_search(["slot", "age"], region="노형동", biz="I56111")
        self.assertEqual(r["mode"], "single")
        self.assertEqual(r["suggestion"]["datasets"], ["1-14"])
        top = r["candidates"][0]
        self.assertEqual((top["dataset_no"], top["full"], top["missing"]), ("1-14", True, []))
        rej = dict((x["dataset_no"], x["reason"]) for x in r["rejected"])
        self.assertIn("법정동 컬럼이 없다", rej["1-13"])
        json.dumps(r, ensure_ascii=False)

    def test_catalog_search_compromise(self):
        r = planner.catalog_search(["dow", "slot", "age"], region="애월읍", biz="카페", focus={"age": ["20대"]}, cross=True)
        self.assertEqual(r["mode"], "compromise")
        self.assertEqual([a["datasets"] for a in r["suggestion"]["alternatives"]], [["1-12", "1-14"], ["1-22"]])



class ReviewFixes(unittest.TestCase):
    """검토 지적을 고친 곳: 범위 밖 주제, 리·명소 이름, 업종을 못 찾은 소비 문장, 타협안의 포기, 빌려 온 값 목록."""

    def rule(self, text=None, slots=None):
        return planner.plan_rule(text=text, slots=slots, today=TODAY)

    def test_topic_outside_two_families_gets_no_order(self):
        r = self.rule("제주시에서 버스를 가장 많이 타는 정류장과 시간대를 알고 싶어요.")
        self.assertEqual(r["order"]["items"], [])
        oos = r["order"]["out_of_scope"]
        self.assertEqual(oos["topic"], "버스 이용")
        nos = [d["dataset_no"] for d in oos["datasets"]]
        self.assertIn("3-04", nos)                                  # 버스 정류소별 이용 현황
        self.assertIn("3-06", nos)                                  # 버스 카드 승하차 정보
        self.assertIn("짝짓지 못", oos["message"])
        self.assertEqual(r["validation"]["total"], 0)               # 통과 표시를 내지 않는다
        self.assertFalse(r["public_answer"]["available"])
        self.assertEqual(r["sent_to_llm"]["purpose_text"], False)
        json.dumps(r, ensure_ascii=False)

    def test_out_of_scope_words_do_not_swallow_business_names(self):
        self.assertIsNone(planner.out_of_scope("노형동 버스 운송업 매출을 시간대별로 보고 싶다"))
        self.assertIsNone(planner.out_of_scope(S1))
        self.assertIsNone(planner.out_of_scope(""))
        for text, subject in (("연동 와이파이 접속이 많은 곳", "wifi"), ("비 오는 날 날씨와 손님", "weather"),
                              ("애월읍 식당에서 잘 팔리는 메뉴", "menu"), ("일도이동 교통량이 많은 시간", "road")):
            self.assertEqual(planner.out_of_scope(text)["subject"], subject, text)

    def test_scope_counts_and_alias_tables_point_to_real_names(self):
        cat = catalog.get()
        sc = planner.scope_counts(cat)
        self.assertEqual((sc["spend"], sc["visit"], sc["total"], sc["reanalysis"]), (22, 9, 71, 3))
        self.assertEqual(planner.options()["scope"], sc)
        al = planner.aliases()
        subjects = set(cat.subject(no) for no in cat.order)
        for row in al["out_of_scope"]:
            for sj in row["subjects"]:
                self.assertIn(sj, subjects)
                self.assertNotIn(sj, ("spend", "visit"))
        names = set(cat.bjd_list()) | set(e.split(" ", 1)[1] for e in cat.emd_list())
        for row in al["places"]:
            self.assertIn(row["region"], names, row)

    def test_village_name_is_read_as_its_town_and_said_so(self):
        r = self.rule("함덕 해수욕장 근처에서 카페를 하려는데 어떤 손님이 오는지 알고 싶습니다.")
        self.assertEqual(r["need"]["region"], "조천읍")
        self.assertTrue(any("함덕" in w["text"] and "조천읍" in w["text"] for w in r["warnings"]))
        self.assertTrue(any(c["value"] == "조천읍" for c in r["order"]["items"][0]["conditions"]))

    def test_region_not_found_is_warned_not_silent(self):
        r = self.rule("카페를 하려는데 어떤 손님이 오는지 알고 싶습니다.")
        self.assertEqual(r["need"]["region"], "도 전체")
        self.assertTrue(any(w["kind"] == "region" for w in r["warnings"]))
        r = self.rule(slots={"what": "biz:I56220", "when": "slot"})   # 칸으로 고른 도 전체는 고른 그대로라 경고하지 않는다
        self.assertEqual(r["warnings"], [])

    def test_spend_sentence_without_known_business_is_warned(self):
        r = self.rule("대정읍 치킨집을 차리려는데 도민이 밤 시간대에 얼마나 쓰는지 성별로 보고 싶습니다.")
        self.assertEqual(r["need"]["subject"], "visit")
        w = [x for x in r["warnings"] if x["kind"] == "biz"]
        self.assertTrue(w)
        self.assertIn("업종을 42개 업종에서 찾지 못해", w[0]["text"])
        r = self.rule("서귀포시 중문동에서 숙박업을 하려는데 내국인 관광객이 어느 요일에 많이 쓰는지 궁금합니다.")
        w = [x for x in r["warnings"] if x["kind"] == "biz"]
        self.assertIn("호텔업·여관업·휴양콘도 운영업", w[0]["text"])

    def test_weekend_asks_for_day_of_week(self):
        r = self.rule("한림읍에 주말에 오는 내국인 방문객이 시간대별로 얼마나 되는지 알고 싶습니다.")
        self.assertIn("dow", [u["key"] for u in r["need"]["units"]])

    def test_split_alternative_says_it_gives_up_the_cross_table(self):
        # 교차표를 가진 데이터가 아예 없는 목적(내국인 관광객 × 카페): 가안도 교차를 포기한다고 적어야 한다.
        r = self.rule(slots={"who": "domestic", "what": "biz:I56220", "when": "dow_slot", "region": "애월읍", "units": ["age"]})
        alts = r["order"]["alternatives"]
        split = [a for a in alts if a["key"] == "split"][0]
        self.assertTrue(any("한 표에서 함께 볼 수 없다" in g for g in split["give_up"]))
        self.assertTrue(split["recommended"])
        self.assertNotIn("포기하는 것: 없음", r["order"]["items"][0]["why"])
        nd = [n for n in r["order"]["no_data"] if n["key"] == "dow_x_slot"][0]
        self.assertIn("한 표에 함께 가진 것이 없다", nd["explain"])
        self.assertNotIn("함께 컬럼", nd["explain"])
        self.assertEqual(r["validation"]["passed"], r["validation"]["total"])

    def test_value_checked_against_another_sheet_is_not_counted_as_confirmed(self):
        r = self.rule(S2)
        item = r["order"]["items"][0]
        self.assertEqual(item["dataset_no"], "1-22")
        c = [x for x in item["conditions"] if x["column"] == "bjd_nm"][0]
        self.assertIs(c["evidence"]["verified"], False)
        self.assertIn("1-07", c["evidence"]["note"])
        self.assertIn("빌려 대조", c["evidence"]["note"])
        self.assertIn("명세서로 확인되지 않", c["evidence"]["note"])
        v = r["validation"]
        self.assertGreaterEqual(v["unverified"], 1)
        self.assertTrue(any("1-22" in n and "bjd_nm" in n for n in v["unverified_notes"]))
        self.assertEqual(sum(v["breakdown"].values()), v["total"])
        self.assertIn("견준 안의 항목 포함", v["breakdown_text"])
        # 코드 값에는 그 뜻(업종 이름)이 붙는다
        self.assertEqual([x for x in item["conditions"] if x["column"] == "biz_small_cd"][0]["value_label"], "커피전문점")

    def test_evidence_row_count_and_code_label(self):
        r = self.rule(S1)
        conds = r["order"]["items"][0]["conditions"]
        bjd = [c for c in conds if c["column"] == "bjd"][0]
        self.assertIn("74행, 서로 다른 이름 73개", bjd["evidence"]["note"])
        self.assertIs(bjd["evidence"]["verified"], True)
        self.assertEqual([c for c in conds if c["column"] == "biz_cd"][0]["value_label"], "한식 음식점업")
        self.assertEqual(r["validation"]["unverified"], 0)

    def test_month_cells_are_short_and_the_shared_reason_is_written_once(self):
        it = self.rule(S1)["order"]["items"][0]
        self.assertEqual([m["index"] for m in it["months"]], [110.0, 87.7, 100.7])
        self.assertEqual(len(it["months_note"]), 2)
        self.assertIn("허브 값과 견줄 수 있는 달", it["months_note"][1])
        self.assertIn("더 최근 해가 필요하면", it["months_note"][1])
        it = self.rule(S3)["order"]["items"][0]                      # 4-15: 수록 시작(2025-04)보다 앞선 달을 가리키지 않는다
        joined = " ".join(it["months_note"]) + " ".join(m["basis"] for m in it["months"])
        self.assertNotIn("한 해 앞의 같은 달", joined)
        self.assertIn("가장 가까운 앞 달", joined)

    def test_rejected_candidates_are_marked_as_sub_rows(self):
        r = self.rule(S1)
        subs = [t for t in r["trace"] if t.get("sub")]
        self.assertTrue(subs)
        self.assertTrue(all(t["result_summary"] == "버림" for t in subs))
        self.assertEqual(len([t for t in r["trace"] if t["tool"] == "catalog_search" and not t.get("sub")]), 1)

    def test_public_notes_explain_unknown_sex_and_uneven_time_bins(self):
        r = self.rule(S1)
        notes = " ".join(r["public_answer"]["notes"])
        self.assertIn("'알수없음'은 법인과 외국인", notes)
        self.assertIn("길이가 다르다", notes)
        self.assertTrue(any("24시간을 5구간으로 묶음" in s["title"] for s in r["public_answer"]["shares"]))


if __name__ == "__main__":
    unittest.main()
