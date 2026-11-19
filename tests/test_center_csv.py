# -*- coding: utf-8 -*-
"""내려받은 CSV 로 다시 돌리기: 헤더 인식, 형식 흡수, 가리기, 대조 줄, 죽지 않기."""
import json
import os
import shutil
import tempfile
import unittest

from tests._base import ROOT, S1, S2, TODAY  # noqa: F401

from app import catalog, center_csv, config, planner
from app import public_data as P

# 운영진이 게시판(47번 답변)에 공개한 실제 CSV 형식 한 줄. 1-12 에서 파티션 2개를 뺀 13열이고 첫 값만 따옴표가 없다.
OPERATOR_LINE = '202607,"서귀포시 서부","대정읍","I56111","한식 음식점업","내국인","단체","공휴일","60","여성","4","1","1000000"'
H12 = "base_year_month,dstrct_se,bjd,biz_cd,biz_nm,user_type,tour_type,week,age,sex,user_cnt,use_cnt,use_amt"
H14 = "base_year_month,dstrct_se,bjd,biz_cd,biz_nm,user_type,tour_type,time,age,sex,user_cnt,use_cnt,use_amt"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="csv_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text, name="Results_20261109_111652.csv", encoding="utf-8", raw=None):
        path = os.path.join(self.tmp, name)
        if raw is not None:
            with open(path, "wb") as f:
                f.write(raw)
        else:
            with open(path, "w", encoding=encoding, newline="") as f:
                f.write(text)
        return path

    def assertFriendlyError(self, res):
        self.assertIsNotNone(res["error"])
        self.assertTrue(res["error"]["message"])
        self.assertTrue(res["error"]["action"])
        self.assertEqual(res["tables"], [])
        self.assertEqual(res["sent_to_llm"], 0)
        self.assertTrue(any(d["level"] == "error" for d in res["diagnostics"]))
        json.dumps(res, ensure_ascii=False)


class OperatorFormat(Base):
    def test_operator_line_is_recognized_as_1_12(self):
        res = center_csv.analyze(self.write(H12 + "\n" + OPERATOR_LINE + "\n"))
        self.assertIsNone(res["error"])
        self.assertEqual(res["recognized"]["dataset_no"], "1-12")
        self.assertEqual(res["recognized"]["dataset_name"], "카드 이용 현황(요일별)_v02")
        self.assertEqual(res["recognized"]["ask"], [])
        self.assertEqual(res["rows"], 1)
        self.assertEqual(res["month"], "2026-07")
        self.assertEqual(res["sent_to_llm"], 0)
        self.assertFalse(res["virtual"])
        self.assertTrue(res["tables"])
        # 이용자수 4, 이용건수 1 → 단순집계는 모두 가려지고 합계도 숨긴다
        simple = [t for t in res["tables"] if t["kind"] == "단순집계"][0]
        # 건수 1 은 '10 미만', 금액 1,000,000 은 값은 크지만 건수 때문에 가리므로 '가림'
        self.assertEqual(simple["rows"][0][1:], ["10 미만", "가림"])
        self.assertEqual(simple["rows"][-1], ["합계", "숨김", "숨김"])
        self.assertGreater(res["masked_total"], 0)
        # 한 줄뿐인 파일: 줄·열이 하나뿐인 교차표는 내지 않고, 어느 표에도 건수 1 이나 금액이 숫자로 나오지 않는다
        self.assertFalse([t for t in res["tables"] if "×" in t["title"]])
        self.assertEqual(res["recoverable"], 0)
        self.assertEqual(res["small_visible"], 0)
        flat = json.dumps([t["rows"] for t in res["tables"]], ensure_ascii=False)
        self.assertNotIn("1000000", flat)
        # 2026-07 은 허브 1048 에 없는 달이라 대조하지 않는다
        self.assertFalse(res["reconcile"]["available"])
        self.assertTrue(res["reconcile"]["note"])

    def test_values_are_parsed_from_the_line(self):
        res = center_csv.analyze(self.write(H12 + "\n" + OPERATOR_LINE + "\n"))
        self.assertEqual(res["scope"]["biz"], "한식 음식점업")
        share = [t for t in res["tables"] if t["title"].startswith("요일별 비중")][0]
        self.assertEqual(share["rows"][0][0], "공휴일")

    def test_partition_columns_present_or_absent(self):
        with_pt = "base_year_month,pt_year,pt_month,dstrct_se,bjd,biz_cd,biz_nm,user_type,tour_type,week,age,sex,user_cnt,use_cnt,use_amt\n" \
                  '"202607","2026","07","서귀포시 서부","대정읍","I56111","한식 음식점업","내국인","단체","공휴일","60","여성","4","1","1000000"\n'
        res = center_csv.analyze(self.write(with_pt))
        self.assertEqual(res["recognized"]["dataset_no"], "1-12")
        self.assertEqual(res["rows"], 1)
        self.assertTrue(any("파티션" in d["detail"] and "빼고" in d["detail"] for d in res["diagnostics"]))
        res = center_csv.analyze(self.write(H12 + "\n" + OPERATOR_LINE + "\n"))
        self.assertTrue(any("파티션" in d["detail"] and "없는 파일" in d["detail"] for d in res["diagnostics"]))

    def test_bom_cp949_spaces_and_quoted_header(self):
        body = OPERATOR_LINE + "\n"
        quoted = ",".join('"%s "' % c for c in H12.split(","))          # 따옴표 + 뒤 공백
        for path in (self.write(H12 + "\n" + body, "a.csv", "utf-8-sig"),
                     self.write(H12 + "\r\n" + body.replace("\n", "\r\n"), "b.csv", "cp949"),
                     self.write(quoted + "\n" + body, "c.csv"),
                     self.write(" " + H12.replace(",", " , ") + "\n" + body, "d.csv")):
            res = center_csv.analyze(path)
            self.assertIsNone(res["error"], path)
            self.assertEqual(res["recognized"]["dataset_no"], "1-12", path)
            self.assertEqual(res["rows"], 1, path)
            self.assertEqual(res["scope"]["biz"], "한식 음식점업", path)

    def test_na_and_excel_resaved_numbers(self):
        rows = [
            '"202408","제주시 중부(서)","노형동","I56111","한식 음식점업","내국인","NA","저녁","NA","알수없음","12","20","300000"',
            '"202408","제주시 중부(서)","노형동","I56111","한식 음식점업","제주도민","도민","저녁","30","남성","NA","NA","NA"',
            '202408,제주시 중부(서),노형동,I56111,한식 음식점업,제주도민,도민,점심,40,여성,15.0,3E+01,1.2E+06',
            '"202408","제주시 중부(서)","노형동","I56111","한식 음식점업","제주도민","도민","점심","40","여성","x","5","100"',
        ]
        res = center_csv.analyze(self.write(H14 + "\n" + "\n".join(rows) + "\n"))
        self.assertIsNone(res["error"])
        self.assertEqual(res["recognized"]["dataset_no"], "1-14")
        self.assertEqual(res["rows"], 4)
        details = " ".join(d["detail"] for d in res["diagnostics"])
        self.assertIn("결측(NA) 3개", details)
        self.assertIn("숫자가 아닌 값 1개", details)
        simple = [t for t in res["tables"] if t["title"].startswith("시간대별 이용건수")][0]
        by = dict((r[0], r[1:]) for r in simple["rows"])
        self.assertEqual(by["점심"], [35, 1200100])
        self.assertEqual(by["저녁"], [20, 300000])
        age = [t for t in res["tables"] if t["title"].startswith("연령대별 비중")][0]
        self.assertIn("NA(알 수 없음)", [r[0] for r in age["rows"]])

    def test_column_order_does_not_matter(self):
        cols = H12.split(",")
        vals = next(__import__("csv").reader([OPERATOR_LINE]))
        order = list(reversed(range(len(cols))))
        text = ",".join(cols[i] for i in order) + "\n" + ",".join('"%s"' % vals[i] for i in order) + "\n"
        res = center_csv.analyze(self.write(text))
        self.assertEqual(res["recognized"]["dataset_no"], "1-12")
        self.assertEqual(res["month"], "2026-07")


class BadFiles(Base):
    def test_empty_file(self):
        res = center_csv.analyze(self.write("", raw=b""))
        self.assertFriendlyError(res)
        self.assertIn("빈 파일", res["error"]["message"])

    def test_not_a_csv(self):
        for raw in (b"PK\x03\x04" + b"\x00" * 200, b"%PDF-1.7 abc", bytes(range(256)) * 4):
            res = center_csv.analyze(self.write("", name="x.csv", raw=raw))
            self.assertFriendlyError(res)
            self.assertIn("CSV", res["error"]["message"])

    def test_header_only(self):
        res = center_csv.analyze(self.write(H12 + "\n"))
        self.assertFriendlyError(res)
        self.assertEqual(res["recognized"]["dataset_no"], "1-12")
        self.assertIn("데이터 줄이 없습니다", res["error"]["message"])

    def test_no_header_line(self):
        res = center_csv.analyze(self.write(OPERATOR_LINE + "\n"))
        self.assertFriendlyError(res)
        self.assertIn("헤더", res["error"]["message"])

    def test_unknown_columns(self):
        res = center_csv.analyze(self.write("이름,전화번호,주소\n홍길동,010,제주\n"))
        self.assertFriendlyError(res)
        self.assertIn("맞지 않습니다", res["error"]["message"])
        self.assertIn("71종", res["error"]["message"])
        self.assertIsNone(res["recognized"]["dataset_no"])

    def test_columns_differ_from_spec_but_still_runs(self):
        # 명세서에 없는 컬럼이 하나 더 있고 tour_type 이 빠진 파일
        head = H14.replace(",tour_type", "") + ",memo"
        row = '"202408","제주시 중부(서)","노형동","I56111","한식 음식점업","내국인","저녁","30","남성","12","20","300000","x"'
        res = center_csv.analyze(self.write(head + "\n" + row + "\n"))
        self.assertIsNone(res["error"])
        self.assertEqual(res["recognized"]["dataset_no"], "1-14")
        warn = [d["detail"] for d in res["diagnostics"] if d["level"] == "warn"]
        self.assertTrue(any("명세서와 다른 컬럼" in w and "memo" in w and "tour_type" in w for w in warn))
        self.assertTrue(res["tables"])

    def test_required_column_missing(self):
        head = H14.replace(",use_cnt", "")
        row = '"202408","제주시 중부(서)","노형동","I56111","한식 음식점업","내국인","개별","저녁","30","남성","12","300000"'
        res = center_csv.analyze(self.write(head + "\n" + row + "\n"))
        self.assertFriendlyError(res)
        self.assertIn("use_cnt", res["error"]["message"])

    def test_ragged_rows_are_skipped_not_fatal(self):
        text = H12 + "\n" + OPERATOR_LINE + "\n" + '"202607","서귀포시 서부"\n' + "\n"
        res = center_csv.analyze(self.write(text))
        self.assertIsNone(res["error"])
        self.assertEqual(res["rows"], 1)
        self.assertTrue(any("건너뛰었습니다" in d["detail"] for d in res["diagnostics"]))

    def test_missing_path(self):
        self.assertFriendlyError(center_csv.analyze(os.path.join(self.tmp, "없는파일.csv")))

    def test_same_header_pair_asks(self):
        cat = catalog.get()
        head = ",".join(c["en"] for c in cat.columns("1-02"))
        res = center_csv.analyze(self.write(head + "\n" + ",".join(["x"] * len(cat.columns("1-02"))) + "\n"))
        self.assertIsNone(res["error"])
        self.assertIsNone(res["recognized"]["dataset_no"])
        self.assertEqual([a["dataset_no"] for a in res["recognized"]["ask"]], ["1-02", "1-05"])
        res = center_csv.analyze(self.write(head + "\n" + ",".join(["x"] * len(cat.columns("1-02"))) + "\n"), dataset_no="1-05")
        self.assertEqual(res["recognized"]["dataset_no"], "1-05")
        self.assertEqual(res["tables"], [])               # 주문서까지만 지원하는 데이터

    def test_other_dataset_is_recognized_but_not_analyzed(self):
        cat = catalog.get()
        head = ",".join(c["en"] for c in cat.columns("4-15"))
        res = center_csv.analyze(self.write(head + "\n" + '"20250401","1","제주시 동부","CHN","50110256","제주시 구좌읍","12.5"\n'))
        self.assertEqual(res["recognized"]["dataset_no"], "4-15")
        self.assertEqual(res["rows"], 1)
        self.assertEqual(res["tables"], [])
        self.assertTrue(any("주문서까지만" in d["detail"] for d in res["diagnostics"]))


def manifest():
    with open(config.SAMPLE_MANIFEST, "r", encoding="utf-8") as f:
        return json.load(f)


class SampleFiles(unittest.TestCase):
    """동봉한 가상 CSV 3종."""

    @classmethod
    def setUpClass(cls):
        cls.man = manifest()
        cls.by = dict((f["dataset_no"], f) for f in cls.man["files"])
        cls.pub = P.get()
        cls.fresh = cls.man["public_file"] == cls.pub.d1048.file

    def path(self, no):
        return os.path.join(config.SAMPLE_DIR, self.by[no]["file"])

    def test_three_files_named_like_downloads(self):
        self.assertEqual(sorted(self.by), ["1-08", "1-12", "1-14"])
        for f in self.man["files"]:
            self.assertRegex(f["file"], r"^Results_\d{8}_\d{6}\.csv$")
            self.assertTrue(os.path.exists(os.path.join(config.SAMPLE_DIR, f["file"])))
        self.assertTrue(os.path.exists(os.path.join(config.SAMPLE_DIR, "가상데이터_설명.md")))

    def test_format_follows_operator_answer(self):
        cat = catalog.get()
        for no in self.by:
            with open(self.path(no), "rb") as f:
                head = f.read(4000)
            self.assertFalse(head.startswith(b"\xef\xbb\xbf"), "BOM 이 없어야 한다")
            lines = head.decode("utf-8", "ignore").split("\n")
            self.assertEqual(lines[0].split(","), [c["en"] for c in cat.columns(no)])       # 파티션 컬럼 없음
            self.assertNotIn("pt_year", lines[0])
            cells = lines[1].split('","')
            self.assertTrue(lines[1].startswith('"') and lines[1].rstrip().endswith('"'))    # 모든 값 따옴표
            self.assertEqual(len(cells), len(cat.columns(no)))

    def test_values_come_from_reference_tables(self):
        import csv
        cat = catalog.get()
        no = "1-14"
        allowed = {}
        for col in ("dstrct_se", "bjd", "biz_cd", "biz_nm", "user_type", "time", "age", "sex"):
            allowed[col] = set(cat.column_values(no, col)["values"])
        allowed["tour_type"] = set(["개별", "단체", "도민", "NA"])
        seen_bjd = set()
        with open(self.path(no), "r", encoding="utf-8", newline="") as f:
            for i, row in enumerate(csv.DictReader(f)):
                for col, ok in allowed.items():
                    self.assertIn(row[col], ok, "%d번째 줄 %s" % (i + 2, col))
                seen_bjd.add(row["bjd"])
                self.assertEqual((row["biz_cd"], row["biz_nm"]), ("I56111", "한식 음식점업"))
        self.assertEqual(len(seen_bjd), 73)

    def test_scenario1_reanalysis(self):
        need = planner.plan_rule(text=S1, today=TODAY)["need"]
        res = center_csv.analyze(self.path("1-14"), self.by["1-14"]["file"], request=need)
        self.assertIsNone(res["error"])
        self.assertEqual(res["recognized"]["dataset_no"], "1-14")          # 파일 이름이 아니라 헤더로 알아본다
        self.assertEqual(res["rows"], self.by["1-14"]["rows"])
        self.assertEqual(res["month"], self.by["1-14"]["month"])
        self.assertTrue(res["virtual"])
        self.assertEqual(res["sent_to_llm"], 0)
        self.assertEqual(res["scope"]["region"], "노형동")
        self.assertEqual(res["scope"]["biz"], "한식 음식점업")
        kinds = [t["kind"] for t in res["tables"]]
        self.assertEqual(kinds[0], "응용집계")                               # 비중·순위를 먼저 보인다
        self.assertIn("단순집계", kinds)
        slot = res["tables"][0]
        self.assertEqual([r[0] for r in slot["rows"]], ["오전", "점심", "오후", "저녁", "밤"])
        self.assertAlmostEqual(sum(r[1] for r in slot["rows"]), 100.0, delta=0.3)
        self.assertEqual(sorted(r[3] for r in slot["rows"]), [1, 2, 3, 4, 5])
        for t in res["tables"]:
            for k in ("title", "kind", "columns", "rows", "masked_cells"):
                self.assertIn(k, t)
            for row in t["rows"]:
                self.assertEqual(len(row), len(t["columns"]))
        json.dumps(res, ensure_ascii=False)

    def test_cells_below_ten_are_masked_and_not_recoverable(self):
        need = planner.plan_rule(text=S1, today=TODAY)["need"]
        res = center_csv.analyze(self.path("1-14"), request=need)
        self.assertGreater(res["masked_total"], 0)
        self.assertEqual(res["masked_total"], sum(t["masked_cells"] for t in res["tables"]))
        mats = [t for t in res["tables"] if "recoverable" in t]
        self.assertTrue(mats)
        self.assertTrue(any(t["masked_primary"] > 0 for t in mats))
        for t in mats:
            self.assertEqual(t["recoverable"], 0)
            for row in t["rows"][:-1]:
                masked = [c for c in row[1:-1] if c in ("10 미만", "가림")]
                if row[-1] != "숨김":
                    self.assertNotEqual(len(masked), 1, row)          # 한 줄에 가린 칸이 하나만 남지 않는다
            for row in t["rows"]:
                for c in row[1:]:
                    if isinstance(c, int):
                        self.assertGreaterEqual(c, 10, row)           # 보이는 단순집계 칸은 모두 10 이상
        flat = json.dumps(res["tables"], ensure_ascii=False)
        self.assertIn("10 미만", flat)

    def test_reconcile_line(self):
        if not self.fresh:
            self.skipTest("공개 데이터 파일이 바뀌었다. python3 tools/make_sample_center.py 를 다시 돌려야 합계가 맞는다")
        need = planner.plan_rule(text=S1, today=TODAY)["need"]
        res = center_csv.analyze(self.path("1-14"), request=need)
        rc = res["reconcile"]
        self.assertTrue(rc["available"])
        self.assertEqual(rc["public_value"], self.pub.value_1048(res["month"], "한식 음식점업"))
        self.assertEqual(rc["center_sum"], rc["public_value"])
        self.assertEqual(rc["diff_pct"], 0.0)
        self.assertIn("차이", rc["note"])
        self.assertIn("추정", rc["note"])
        # 가상 파일의 0.0% 는 만든 방식 때문이라는 말이 대조 줄 맨 앞에 나온다
        self.assertTrue(res["virtual"])
        self.assertTrue(rc["note"].startswith("이 파일은 허브 값을 쪼개 만든 가상 파일"))
        self.assertIn("근거가 아니다", rc["note"])

    def test_conditioned_download_is_explained_in_reconcile(self):
        # 노형동 줄만 남긴 파일(조건을 걸어 받은 꼴) → 차이가 크고, 왜 그런지 적는다.
        tmp = tempfile.mkdtemp(prefix="csv_")
        try:
            out = os.path.join(tmp, "Results_20261117_120000.csv")
            with open(self.path("1-14"), "r", encoding="utf-8", newline="") as f, open(out, "w", encoding="utf-8", newline="") as g:
                for i, line in enumerate(f):
                    if i == 0 or '"노형동"' in line:
                        g.write(line)
            res = center_csv.analyze(out, request=planner.plan_rule(text=S1, today=TODAY)["need"])
            self.assertFalse(res["virtual"])
            rc = res["reconcile"]
            self.assertTrue(rc["available"])
            self.assertIsNone(rc["diff_pct"])                      # 조건을 걸어 받은 파일은 차이(%)를 내지 않는다
            self.assertIn("73곳 가운데 1곳만", rc["note"])
            self.assertIn("견주지 않았다", rc["note"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_week_file_and_region_file(self):
        need = planner.plan_rule(text=S2, today=TODAY)["need"]
        res = center_csv.analyze(self.path("1-12"), request=need)
        self.assertEqual(res["recognized"]["dataset_no"], "1-12")
        self.assertEqual(res["scope"]["region"], "애월읍")
        self.assertEqual(res["scope"]["biz"], "비알콜 음료점업")
        self.assertEqual([r[0] for r in res["tables"][0]["rows"]],
                         ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일", "공휴일"])
        res = center_csv.analyze(self.path("1-08"))                    # 요청 없이 넣으면 파일 전체로 집계
        self.assertEqual(res["recognized"]["dataset_no"], "1-08")
        self.assertIn("73곳", res["scope"]["region"])
        self.assertTrue(res["tables"])

    def test_request_not_in_file_falls_back_with_warning(self):
        need = planner.plan_rule(text=S2, today=TODAY)["need"]           # 비알콜 음료점업을 바랐는데 한식 파일을 넣음
        res = center_csv.analyze(self.path("1-14"), request=need)
        self.assertIsNone(res["error"])
        self.assertTrue(any("파일에 없어" in d["detail"] for d in res["diagnostics"] if d["level"] == "warn"))
        self.assertEqual(res["scope"]["biz"], "한식 음식점업")

    def test_sums_match_public_value_when_fresh(self):
        if not self.fresh:
            self.skipTest("공개 데이터 파일이 바뀌었다. python3 tools/make_sample_center.py 를 다시 돌려야 한다")
        for f in self.man["files"]:
            self.assertEqual(f["sum_use_amt"], self.pub.value_1048(f["month"], f["biz_nm"], "use_amt"), f["file"])
            self.assertEqual(f["sum_use_cnt"], self.pub.value_1048(f["month"], f["biz_nm"], "use_cnt"), f["file"])


def _row14(time, user_type, age, users, cnt, amt, bjd="노형동", district="제주시 중부(동)"):
    return '"202408","%s","%s","I56111","한식 음식점업","%s","개별","%s","%s","남성","%s","%s","%s"' % (
        district, bjd, user_type, time, age, users, cnt, amt)


class TotalsAndCrossTables(Base):
    """합계 칸의 10 미만과, 한 표에서 가린 값이 다른 표의 합계로 드러나는 일(검토 지적)."""

    def analyze(self, lines, **kw):
        return center_csv.analyze(self.write(H14 + "\n" + "\n".join(lines) + "\n"), **kw)

    def table(self, res, head):
        return [t for t in res["tables"] if t["title"].startswith(head)][0]

    def assertNoSmallNumbers(self, res):
        for t in res["tables"]:
            if t["kind"] != "단순집계":
                continue
            for row in t["rows"]:
                for c in row[1:]:
                    if isinstance(c, (int, float)):
                        self.assertGreaterEqual(c, 10, (t["title"], row))
        self.assertEqual(res["recoverable"], 0)
        self.assertEqual(res["small_visible"], 0)

    def test_total_below_ten_is_hidden(self):
        res = self.analyze([_row14("오전", "내국인", "30", 3, 3, 30000), _row14("점심", "내국인", "30", 4, 4, 40000)])
        t = self.table(res, "시간대별 이용건수")
        self.assertEqual(t["rows"][-1], ["합계", "숨김", "숨김"])       # 합계 7 은 10 미만이라 숨긴다
        self.assertNoSmallNumbers(res)

    def test_row_and_column_totals_follow_the_one_line_tables(self):
        lines = [_row14("밤", "내국인", "30", 4, 4, 40000), _row14("밤", "제주도민", "40", 3, 3, 30000),
                 _row14("오전", "내국인", "30", 60, 60, 600000), _row14("오전", "제주도민", "40", 42, 42, 420000),
                 _row14("오전", "일본", "30", 3, 3, 30000),
                 _row14("점심", "내국인", "30", 200, 200, 2000000), _row14("점심", "제주도민", "40", 150, 150, 1500000),
                 _row14("저녁", "내국인", "20", 300, 300, 3000000), _row14("저녁", "제주도민", "50", 250, 250, 2500000)]
        res = self.analyze(lines)
        one = dict((r[0], r[1]) for r in self.table(res, "시간대별 이용건수")["rows"])
        self.assertEqual(one["밤"], "10 미만")                          # 밤 합계 7
        self.assertEqual(one["오전"], "가림")                           # 한 칸만 가리면 합계에서 빼서 알 수 있어 함께 가린 칸
        mat = self.table(res, "시간대 × 이용자구분")
        rows = dict((r[0], r) for r in mat["rows"])
        self.assertEqual(rows["밤"][-1], "숨김")                        # 행렬의 줄 합계 7 도 숨긴다
        self.assertEqual(rows["오전"][-1], "숨김")                      # 한 줄짜리 표에서 가린 105 가 줄 합계로 드러나지 않는다
        self.assertTrue(any(c in ("10 미만", "가림") for c in rows["오전"][1:-1]))   # 칸을 더해 알 수도 없다
        cols = mat["columns"]
        foot = rows["합계"]
        self.assertEqual(foot[cols.index("일본")], "숨김")              # 일본 합계 3
        share = dict((r[0], r[1]) for r in self.table(res, "이용자구분별 비중")["rows"])
        for name, v in share.items():                                   # 비중 표에서 가린 이용자구분은 열 합계에서도 숨긴다
            if v in ("10 미만", "가림"):
                self.assertEqual(foot[cols.index(name)], "숨김", name)
        self.assertEqual(mat["recoverable"], 0)
        self.assertNoSmallNumbers(res)

    def test_cell_hidden_because_of_user_count_is_labelled_differently(self):
        res = self.analyze([_row14("오전", "내국인", "30", 3, 40, 400000), _row14("점심", "내국인", "30", 50, 60, 600000),
                            _row14("저녁", "내국인", "30", 80, 90, 900000)])
        one = dict((r[0], r[1:]) for r in self.table(res, "시간대별 이용건수")["rows"])
        self.assertEqual(one["오전"], ["가림", "가림"])                 # 건수 40 은 10 미만이 아니다(이용자수 3 때문에 가린다)
        self.assertNoSmallNumbers(res)

    def test_amount_below_ten_is_masked_too(self):
        res = self.analyze([_row14("오전", "내국인", "30", 20, 20, 5), _row14("점심", "내국인", "30", 50, 60, 600000),
                            _row14("저녁", "내국인", "30", 80, 90, 900000)])
        one = dict((r[0], r[1:]) for r in self.table(res, "시간대별 이용건수")["rows"])
        self.assertEqual(one["오전"], ["가림", "10 미만"])              # 금액 5 도 최종 표시값이라 가린다
        self.assertEqual(res["small_visible"], 0)

    def test_region_share_table_masks_a_partner_when_all_regions_are_shown(self):
        res = self.analyze([_row14("점심", "내국인", "30", 1000, 1035, 10350000, "노형동"),
                            _row14("점심", "내국인", "30", 600, 635, 6350000, "연동"),
                            _row14("점심", "내국인", "30", 4, 4, 40000, "우도면", "제주시 동부")])
        t = self.table(res, "법정동별 비중")
        by = dict((r[1], r[2:]) for r in t["rows"])
        self.assertEqual(by["우도면"][1], "10 미만")
        self.assertEqual(by["연동"], ["가림", "가림"])                  # 100 에서 나머지를 빼면 우도면이 나오므로 함께 가린다
        self.assertEqual(res["recoverable"], 0)

    def test_two_regions_with_request_hides_file_sum(self):
        need = planner.plan_rule(text=S1, today=TODAY)["need"]
        res = self.analyze([_row14("점심", "내국인", "30", 1000, 1035, 10350000, "노형동"),
                            _row14("저녁", "내국인", "30", 900, 935, 9350000, "노형동"),
                            _row14("점심", "내국인", "30", 4, 4, 40000, "우도면", "제주시 동부")], request=need)
        self.assertIsNone(res["reconcile"]["center_sum"])               # 파일 합계 - 노형동 = 우도면이 되므로 합계를 숨긴다
        self.assertEqual(res["recoverable"], 0)

    def test_same_name_regions_merged_is_warned(self):
        need = planner.plan_rule(slots={"what": "biz:I56111", "when": "slot", "region": "월평동"}, today=TODAY)["need"]
        res = self.analyze([_row14("점심", "내국인", "30", 100, 100, 1000000, "월평동", "제주시 중부(동)"),
                            _row14("점심", "내국인", "30", 100, 100, 1000000, "월평동", "서귀포시 중부")], request=need)
        self.assertTrue(any("두 곳을 합쳐" in d["detail"] for d in res["diagnostics"] if d["level"] == "warn"))


class Recognition(unittest.TestCase):
    def test_every_spec_header_is_recognized(self):
        cat = catalog.get()
        dup = set(no for g in cat.spec["duplicate_headers"] for no in g)
        for no in cat.order:
            header = [c["en"] for c in cat.columns(no)]
            r = center_csv.recognize_header(header, cat)
            self.assertTrue(r["exact"], no)
            self.assertIn(no, r["matches"])
            if no not in dup:
                self.assertEqual(r["matches"], [no])
            with_pt = ["pt_year", "pt_month"] + header
            self.assertEqual(center_csv.recognize_header(with_pt, cat)["matches"], r["matches"])

    def test_header_normalization(self):
        r = center_csv.recognize_header(['﻿"base_year_month" '] + [" %s " % c.upper() for c in H14.split(",")[1:]])
        self.assertEqual(r["matches"], ["1-14"])


if __name__ == "__main__":
    unittest.main()
