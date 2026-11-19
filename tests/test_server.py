# -*- coding: utf-8 -*-
"""서버 시험: 빈 포트에 서버를 띄워 API 계약 5개, 화면 파일, 업로드, 삭제, 오류 응답을 본다. 끝나면 내린다."""
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

from tests import _base  # noqa: F401
from tests._base import ROOT, S1
from tests.mock_llm import FAKE_KEY, MockLLM, script_s1

from app import agent, config, planner, selfcheck, server

EXAMPLES = planner.EXAMPLES


def sample_csv(dataset_no):
    with open(config.SAMPLE_MANIFEST, "r", encoding="utf-8") as f:
        man = json.load(f)
    for row in man["files"]:
        if row["dataset_no"] == dataset_no:
            return os.path.join(config.SAMPLE_DIR, row["file"])
    raise AssertionError("가상 CSV 가 없다: " + dataset_no)


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="server_test_")
        cls.runtime = os.path.join(cls.tmp, "runtime")
        cls.patches = [mock.patch.object(agent, "ENV_FILE", os.path.join(cls.tmp, "none.env")),
                       mock.patch.object(agent, "RETRY_BACKOFF", (0.01, 0.01)),
                       mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "API_BASE": "", "MODEL": "", "EFFORT": ""})]
        for p in cls.patches:
            p.start()
        cls.httpd = server.make_server(0, cls.runtime, quiet=True)
        cls.port = cls.httpd.server_address[1]
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, kwargs={"poll_interval": 0.05})
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(5)
        for p in reversed(cls.patches):
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, headers=None):
        """(상태 코드, JSON 또는 bytes, 헤더)"""
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers = dict({"Content-Type": "application/json; charset=utf-8"}, **(headers or {}))
        req = urllib.request.Request(self.base + path, data=body, headers=headers or {}, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            resp = opener.open(req, timeout=60)
        except urllib.error.HTTPError as e:
            resp = e
        try:
            raw = resp.read()
            status = resp.getcode()
            hdrs = dict((k.lower(), v) for k, v in resp.headers.items())
        finally:
            resp.close()
        if hdrs.get("content-type", "").startswith("application/json"):
            return status, json.loads(raw.decode("utf-8")), hdrs
        return status, raw, hdrs

    def plan(self, **body):
        status, data, _ = self.call("POST", "/api/plan", body)
        self.assertEqual(status, 200, data)
        return data

    def upload(self, data, name="Results_20261117_101502.csv", run_id=None, extra=""):
        q = ("?run_id=" + run_id) if run_id else ""
        return self.call("POST", "/api/center-csv" + q + extra, data,
                         {"Content-Type": "application/octet-stream", "X-Filename": urllib.parse.quote(name)})

    def assert_error(self, status, data, want_status):
        self.assertEqual(status, want_status, data)
        self.assertIsInstance(data, dict)
        err = data["error"]
        self.assertTrue(any("가" <= ch <= "힣" for ch in err["message"]), err)
        self.assertTrue(any("가" <= ch <= "힣" for ch in err["action"]), err)


class TestReadApis(ServerCase):
    def test_server_listens_on_loopback_only(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")
        self.assertGreater(self.port, 0)

    def test_status_contract(self):
        status, s, _ = self.call("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(s["spec"]), ["columns", "datasets", "date", "ref_tables", "version"])
        self.assertEqual((s["spec"]["datasets"], s["spec"]["ref_tables"]), (71, 2))
        for key in ("d1048", "d520"):
            for f in ("rows", "first", "last", "placeholder"):
                self.assertIn(f, s["public"][key])
            self.assertGreater(s["public"][key]["rows"], 0)
        self.assertEqual(sorted(s["leftover"]), ["files", "results"])
        self.assertEqual(s["llm"]["enabled"], False)
        self.assertEqual(s["llm"]["model"], "claude-haiku-5-5")
        self.assertIn("ANTHROPIC_API_KEY", s["llm"]["reason"])
        self.assertGreaterEqual(len(s["selfcheck"]), 5)
        for c in s["selfcheck"]:
            self.assertEqual(sorted(c), ["detail", "name", "ok"])
            self.assertIsInstance(c["ok"], bool)
        self.assertTrue(all(c["ok"] for c in s["selfcheck"]), s["selfcheck"])
        names = " ".join(c["name"] + c["detail"] for c in s["selfcheck"])
        self.assertIn("71종", names)
        self.assertIn("규칙 모드", names)

    def test_options_contract(self):
        status, o, _ = self.call("GET", "/api/options")
        self.assertEqual(status, 200)
        for k in ("who", "what", "when", "regions", "units", "examples"):
            self.assertIn(k, o)
        self.assertEqual(sorted(o["regions"]), ["bjd", "district7", "emd"])
        self.assertEqual(len(o["examples"]), 3)
        for e in o["examples"]:
            self.assertTrue(e["title"] and e["text"])

    def test_static_files_and_headers(self):
        status, body, h = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertTrue(h["content-type"].startswith("text/html"))
        self.assertIn(b"<html", body.lower())
        self.assertIn("default-src 'self'", h["content-security-policy"])
        self.assertEqual(h["cache-control"], "no-store")
        for path, ctype in (("/app.js", "application/javascript"), ("/style.css", "text/css"), ("/index.html", "text/html")):
            status, body, h = self.call("GET", path)
            self.assertEqual(status, 200, path)
            self.assertTrue(h["content-type"].startswith(ctype))
            self.assertGreater(len(body), 100)

    def test_static_cannot_escape_web_folder(self):
        for path in ("/../HANDOFF.md", "/..%2fHANDOFF.md", "/%2e%2e/app/agent.py", "/../app/agent.py",
                     "/..%5capp%5cagent.py", "//etc/passwd", "/nope.html", "/app.py", "/.env"):
            status, data, _ = self.call("GET", path)
            self.assert_error(status, data, 404)

    def test_sample_csv_is_served_and_reads_back(self):
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/api/sample-csv?dataset=1-14" % self.port) as r:
            self.assertEqual(r.status, 200)
            name = urllib.parse.unquote(r.headers.get("X-Filename") or "")
            body = r.read()
        self.assertTrue(name.endswith(".csv"))
        self.assertGreater(len(body), 1000)
        status, s, _ = self.call("POST", "/api/sample-csv", {})
        self.assert_error(status, s, want_status=405)

    def test_local_preview_needs_emd(self):
        self.assert_error(*self.call("GET", "/api/local")[:2], want_status=400)

    def test_unknown_api_and_wrong_method(self):
        self.assert_error(*self.call("GET", "/api/nope")[:2], want_status=404)
        self.assert_error(*self.call("GET", "/api/plan")[:2], want_status=405)
        self.assert_error(*self.call("POST", "/api/status", {})[:2], want_status=405)
        self.assert_error(*self.call("DELETE", "/api/clear")[:2], want_status=405)
        self.assert_error(*self.call("POST", "/index.html", {})[:2], want_status=405)

    def test_other_host_or_origin_is_refused(self):
        self.assert_error(*self.call("GET", "/api/status", headers={"Host": "evil.example"})[:2], want_status=403)
        self.assert_error(*self.call("POST", "/api/clear", {}, {"Origin": "http://evil.example"})[:2], want_status=403)
        status, _, _ = self.call("POST", "/api/plan", {"mode": "rule", "slots": {"what": "pop"}},
                                 {"Origin": "http://127.0.0.1:%d" % self.port})
        self.assertEqual(status, 200)
        status, _, _ = self.call("GET", "/api/options", headers={"Host": "localhost:%d" % self.port})
        self.assertEqual(status, 200)


class TestPlan(ServerCase):
    def test_scenario1_slots(self):
        r = self.plan(mode="rule", text="", slots=EXAMPLES[0]["slots"])
        self.assertEqual(r["mode_used"], "rule")
        self.assertRegex(r["run_id"], r"^[0-9a-f]{12}$")
        it = r["order"]["items"][0]
        self.assertEqual(it["dataset_no"], "1-14")
        conds = dict((c["column"], c["value"]) for c in it["conditions"])
        self.assertEqual((conds["bjd"], conds["biz_cd"]), ("노형동", "I56111"))
        self.assertTrue(all(c["evidence"]["sheet"] and c["evidence"]["col_no"] for c in it["conditions"]))
        self.assertEqual(len(it["months"]), 3)
        self.assertTrue(all(m["ym"] >= "2023-08" for m in it["months"]))
        self.assertTrue(any("1-13" in s["args_summary"] and s["result_summary"] == "버림" and "법정동" in s["note"] for s in r["trace"]))
        self.assertEqual(r["validation"]["passed"], r["validation"]["total"])
        for key in ("need", "public_answer", "gaps", "order", "validation", "trace", "sent_to_llm", "limits", "fallback_reason"):
            self.assertIn(key, r)
        self.assertEqual(r["sent_to_llm"], {"purpose_text": False, "public_aggregates": 0, "spec_items": 0, "center_values": 0})

    def test_scenario2_slots_gives_two_alternatives(self):
        r = self.plan(mode="rule", slots=EXAMPLES[1]["slots"])
        alts = dict((a["key"], a) for a in r["order"]["alternatives"])
        self.assertEqual(sorted(alts), ["single", "split"])
        self.assertEqual(sorted(i["dataset_no"] for i in alts["split"]["items"]), ["1-12", "1-14"])
        self.assertEqual([i["dataset_no"] for i in alts["single"]["items"]], ["1-22"])
        for a in alts.values():
            self.assertGreater(a["downloads"], 0)
            self.assertTrue(a["gain"])
            self.assertTrue(a["give_up"])
        self.assertTrue(any("한 데이터로 필요한 단위를 다 채우는 것이 없다" in s["result_summary"] for s in r["trace"]))

    def test_scenario3_slots_no_public_answer_and_no_data_for_age(self):
        r = self.plan(mode="rule", slots=EXAMPLES[2]["slots"])
        self.assertFalse(r["public_answer"]["available"])
        self.assertTrue(r["public_answer"]["reason"])
        self.assertEqual(r["order"]["items"][0]["dataset_no"], "4-15")
        self.assertEqual([n["key"] for n in r["order"]["no_data"]], ["age"])
        self.assertEqual([g["status"] for g in r["gaps"] if g["key"] == "age"], ["메울 데이터 없음"])

    def test_sentences_in_rule_mode(self):
        for e, want in zip(EXAMPLES, ("1-14", "1-22", "4-15")):
            r = self.plan(mode="rule", text=e["text"], slots={})
            self.assertEqual(r["order"]["items"][0]["dataset_no"], want)
            self.assertEqual((r["mode_used"], r["fallback_reason"]), ("rule", ""))

    def test_auto_without_key_falls_back_and_says_why(self):
        r = self.plan(mode="auto", text=S1, slots={})
        self.assertEqual(r["mode_used"], "rule")
        self.assertIn("ANTHROPIC_API_KEY", r["fallback_reason"])
        self.assertEqual(r["order"]["items"][0]["dataset_no"], "1-14")

    def test_auto_with_mock_llm_goes_through_the_loop(self):
        m = MockLLM(script_s1())
        base = m.start()
        self.addCleanup(m.stop)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": FAKE_KEY, "API_BASE": base}):
            r = self.plan(mode="auto", text=S1, slots={})
            status, st, _ = self.call("GET", "/api/status")
        self.assertEqual(r["mode_used"], "llm")
        self.assertEqual(r["order"]["items"][0]["dataset_no"], "1-14")
        self.assertEqual(r["sent_to_llm"]["center_values"], 0)
        self.assertTrue(st["llm"]["enabled"])
        self.assertNotIn(FAKE_KEY, json.dumps(st, ensure_ascii=False))
        self.assertNotIn(FAKE_KEY, json.dumps(r, ensure_ascii=False))

    def test_empty_request_does_not_crash(self):
        status, r, _ = self.call("POST", "/api/plan", b"", {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(r["mode_used"], "rule")
        self.assertEqual(r["order"]["items"], [])

    def test_bad_requests_get_korean_errors(self):
        self.assert_error(*self.call("POST", "/api/plan", b"{not json", {"Content-Type": "application/json"})[:2], want_status=400)
        self.assert_error(*self.call("POST", "/api/plan", b"\xff\xfe\x00", {"Content-Type": "application/json"})[:2], want_status=400)
        self.assert_error(*self.call("POST", "/api/plan", [1, 2])[:2], want_status=400)
        self.assert_error(*self.call("POST", "/api/plan", {"mode": "magic"})[:2], want_status=400)
        self.assert_error(*self.call("POST", "/api/plan", {"text": 5})[:2], want_status=400)
        self.assert_error(*self.call("POST", "/api/plan", {"slots": [1]})[:2], want_status=400)
        self.assert_error(*self.call("POST", "/api/plan", {"text": "가" * 2001})[:2], want_status=400)
        self.assert_error(*self.call("POST", "/api/plan", b"x" * (server.MAX_JSON_BYTES + 1))[:2], want_status=413)

    def test_odd_slot_values_do_not_crash(self):
        r = self.plan(mode="rule", slots={"who": 3, "what": "biz:ZZZ", "when": None, "region": "없는동", "units": "age",
                                          "focus": {"age": "20대", "x": [1]}})
        self.assertEqual(r["mode_used"], "rule")

    def test_internal_error_becomes_500_with_korean_message(self):
        with mock.patch.object(agent, "plan_auto", side_effect=RuntimeError("boom")):
            with mock.patch.object(server.traceback, "print_exc"):
                status, data, _ = self.call("POST", "/api/plan", {"mode": "rule"})
        self.assert_error(status, data, 500)
        self.assertNotIn("boom", json.dumps(data, ensure_ascii=False))


class TestCenterCsvAndClear(ServerCase):
    def setUp(self):
        self.call("POST", "/api/clear", {})

    def read_sample(self, no="1-14"):
        with open(sample_csv(no), "rb") as f:
            return f.read()

    def test_reanalysis_fills_tables_masks_and_never_calls_llm(self):
        run = self.plan(mode="rule", slots=EXAMPLES[0]["slots"])
        boom = AssertionError("재분석 경로에서 LLM 을 부르면 안 된다")
        with mock.patch.object(agent, "call_messages", side_effect=boom), mock.patch.object(agent, "_open", side_effect=boom), \
                mock.patch.object(agent, "plan_auto", side_effect=boom):
            status, r, _ = self.upload(self.read_sample(), run_id=run["run_id"])
        self.assertEqual(status, 200, r)
        self.assertEqual(r["recognized"]["dataset_no"], "1-14")
        self.assertEqual(r["month"], "2024-08")
        self.assertGreater(r["rows"], 0)
        slot_tables = [t for t in r["tables"] if "시간대" in t["title"]]
        self.assertTrue(slot_tables and slot_tables[0]["rows"])
        self.assertTrue(all(t["kind"] in ("응용집계", "단순집계") for t in r["tables"]))
        self.assertGreater(r["masked_total"], 0)
        cells = [c for t in r["tables"] for row in t["rows"] for c in row]
        self.assertIn("10 미만", cells)
        self.assertTrue(r["reconcile"]["available"])
        self.assertIsNotNone(r["reconcile"]["diff_pct"])
        self.assertEqual(r["sent_to_llm"], 0)
        self.assertIs(r["virtual"], True)

    def test_run_id_makes_analysis_use_the_requested_region(self):
        run = self.plan(mode="rule", slots=EXAMPLES[0]["slots"])
        _, with_run, _ = self.upload(self.read_sample(), run_id=run["run_id"])
        _, without, _ = self.upload(self.read_sample())
        self.assertNotEqual(json.dumps(with_run["tables"], ensure_ascii=False), json.dumps(without["tables"], ensure_ascii=False))
        self.assertTrue(any("주문서를 찾지 못해" in d["detail"] for d in without["diagnostics"]))
        self.assertFalse(any("주문서를 찾지 못해" in d["detail"] for d in with_run["diagnostics"]))
        # 모르는 run_id, 이상한 run_id 도 죽지 않는다
        self.assertEqual(self.upload(self.read_sample(), run_id="0123456789ab")[0], 200)
        self.assertEqual(self.upload(self.read_sample(), run_id="..%2F..%2Fetc")[0], 200)

    def test_officially_published_row_format_is_accepted(self):
        header = "base_year_month,dstrct_se,bjd,biz_cd,biz_nm,user_type,tour_type,week,age,sex,user_cnt,use_cnt,use_amt"
        row = '202607,"서귀포시 서부","대정읍","I56111","한식 음식점업","내국인","단체","공휴일","60","여성","4","1","1000000"'
        status, r, _ = self.upload((header + "\n" + row + "\n").encode("utf-8"), name="Results_20260801_090000.csv")
        self.assertEqual(status, 200, r)
        self.assertEqual(r["recognized"]["dataset_no"], "1-12")
        self.assertEqual(r["rows"], 1)
        self.assertIs(r["virtual"], False)

    def test_strange_files_get_cause_and_action(self):
        cases = {
            "빈 파일.csv": b"",
            "그림.png": b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8,
            "문서.xlsx": b"PK\x03\x04" + b"\x00" * 600,
            "컬럼이 다른 파일.csv": "이름,나이,주소\n홍길동,30,제주\n".encode("utf-8"),
            "헤더만.csv": b"\n\n",
        }
        for name, data in cases.items():
            status, r, _ = self.upload(data, name=name)
            self.assert_error(status, r, 400)
            self.assertIn("diagnostics", r)
        # 받지 못한 파일은 임시 폴더에 남기지 않는다
        self.assertEqual(os.listdir(os.path.join(self.runtime, "uploads")), [])

    def test_upload_without_length_is_refused(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            s.sendall(("POST /api/center-csv HTTP/1.0\r\nHost: 127.0.0.1:%d\r\n\r\n" % self.port).encode("ascii"))
            raw = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                raw += chunk
        finally:
            s.close()
        self.assertTrue(raw.startswith(b"HTTP/1.0 411"), raw[:40])
        self.assertIn("error", json.loads(raw.split(b"\r\n\r\n", 1)[1].decode("utf-8")))

    def test_filename_cannot_leave_upload_folder(self):
        for name in ("../../evil.csv", "..\\..\\evil.csv", "/etc/passwd", "a/b/c.csv", "한글 이름 (1).csv", "", "...", "x" * 400 + ".csv"):
            status, _, _ = self.upload(self.read_sample("1-08"), name=name)
            self.assertEqual(status, 200, name)
        ups = os.listdir(os.path.join(self.runtime, "uploads"))
        self.assertEqual(len(ups), 8)
        self.assertTrue(all(len(u) <= 140 for u in ups))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.csv")))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.tmp), "evil.csv")))
        self.assertEqual(sorted(os.listdir(self.runtime)), ["logs", "results", "uploads"])
        self.assertTrue(any("한글 이름 (1).csv" in u for u in ups))

    def test_clear_deletes_results_files_and_logs_and_reports_counts(self):
        run = self.plan(mode="rule", slots=EXAMPLES[0]["slots"])
        self.plan(mode="rule", slots=EXAMPLES[2]["slots"])
        self.upload(self.read_sample(), run_id=run["run_id"])
        self.upload(b"", name="빈 파일.csv")
        status, r, _ = self.call("POST", "/api/clear", b"{}", {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(r, {"deleted": {"results": 3, "files": 1, "logs": 4}})
        for sub in ("uploads", "results", "logs"):
            self.assertEqual(os.listdir(os.path.join(self.runtime, sub)), [], sub)
        self.assertEqual(self.httpd.store.need_for(run["run_id"]), None)
        status, r, _ = self.call("POST", "/api/clear", {})
        self.assertEqual(r, {"deleted": {"results": 0, "files": 0, "logs": 0}})

    def test_nothing_is_written_outside_runtime(self):
        before = set(os.listdir(ROOT))
        run = self.plan(mode="rule", slots=EXAMPLES[0]["slots"])
        self.upload(self.read_sample(), run_id=run["run_id"])
        after = set(os.listdir(ROOT))
        self.assertEqual(after - before - set(["__pycache__"]), set())


class TestHelpers(unittest.TestCase):
    def test_safe_filename(self):
        self.assertEqual(server.safe_filename("Results_20261117_101502.csv"), "Results_20261117_101502.csv")
        self.assertEqual(server.safe_filename(urllib.parse.quote("../../결과 파일.csv")), "결과 파일.csv")
        self.assertEqual(server.safe_filename("C%3A%5CUsers%5Cme%5Ca.csv"), "a.csv")
        self.assertEqual(server.safe_filename(None), "upload.csv")
        self.assertEqual(server.safe_filename("%00%0a.."), "upload.csv")
        self.assertLessEqual(len(server.safe_filename("가" * 500 + ".csv")), 120)

    def test_clean_slots(self):
        self.assertEqual(server.clean_slots({"who": " all ", "what": 3, "units": ["age", 1], "focus": {"age": ["20대"], "z": 1}, "x": 1}),
                         {"who": "all", "units": ["age"], "focus": {"age": ["20대"]}})

    def test_selfcheck_rows_and_failed_llm_check(self):
        with mock.patch.object(agent, "ENV_FILE", os.path.join(ROOT, "no-such.env")), \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "API_BASE": "", "MODEL": ""}):
            tmp = tempfile.mkdtemp(prefix="selfcheck_test_")
            try:
                rows = selfcheck.run(os.path.join(tmp, "runtime"))
                self.assertFalse(os.path.exists(os.path.join(tmp, "runtime")))     # 점검만으로 폴더를 남기지 않는다
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            self.assertTrue(all(r["ok"] for r in rows), rows)
            self.assertEqual(len(selfcheck.format_lines(rows)), len(rows))
            self.assertIn("규칙 모드", rows[-1]["detail"])
            # Key 는 있는데 쓸 수 없으면(주소가 틀림) 확인이 필요하다고 알린다. 네트워크는 쓰지 않는다.
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": FAKE_KEY, "API_BASE": "http://example.com"}), \
                    mock.patch.object(agent, "_open", side_effect=AssertionError("네트워크를 쓰면 안 된다")):
                row = selfcheck.run()[-1]
            self.assertFalse(row["ok"])
            self.assertIn("API_BASE", row["detail"])
            self.assertNotIn(FAKE_KEY, row["detail"])

    def test_center_csv_module_has_no_network_or_llm_code(self):
        with open(os.path.join(config.APP_DIR, "center_csv.py"), "r", encoding="utf-8") as f:
            src = f.read()
        for word in ("urllib", "http.client", "socket", "import agent", "from . import agent", "from .agent"):
            self.assertNotIn(word, src)


class TestPackaging(unittest.TestCase):
    def test_run_bat_is_ascii_with_crlf(self):
        with open(os.path.join(ROOT, "run.bat"), "rb") as f:
            data = f.read()
        data.decode("ascii")                                   # 한글이 있으면 여기서 실패한다
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))     # 줄끝은 모두 CRLF
        self.assertNotIn(b"\r", data.replace(b"\r\n", b""))
        for word in (b'pushd "%~dp0"', b"PYTHONUTF8=1", b"py -3", b"-m app.server", b"version_info", b"3.9"):
            self.assertIn(word, data)
        self.assertLess(data.index(b"call :try_python py -3"), data.index(b"call :try_python python"))

    def test_run_sh_and_env_example(self):
        with open(os.path.join(ROOT, "run.sh"), "r", encoding="utf-8") as f:
            sh = f.read()
        self.assertTrue(sh.startswith("#!/bin/sh"))
        self.assertIn("-m app.server", sh)
        self.assertNotIn("\r", sh)
        with open(os.path.join(ROOT, ".env.example"), "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        self.assertEqual(lines, ["ANTHROPIC_API_KEY=", "MODEL=", "API_BASE=", "JEJU_HUB_KEY=", "SBIZ_STORE_KEY=", "SBIZ365_CERT_KEY="])

    def test_docs_html_matches_markdown_and_loads_nothing_from_outside(self):
        # 문서는 .md 와 같은 내용의 .html 로도 넣는다(더블클릭으로 읽게). 화면의 "사용 설명 열기"는 web/manual.html 이다.
        import subprocess
        import sys
        r = subprocess.run([sys.executable, "-X", "utf8", os.path.join(ROOT, "tools", "make_docs_html.py"), "--check"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace"))
        for rel in ("README.html", os.path.join("web", "manual.html"), os.path.join("docs", "사용자_매뉴얼.html")):
            with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
                page = f.read()
            self.assertIn("<table>", page)
            for bad in ("<script", "<link", "<img", "@import", "url("):
                self.assertNotIn(bad, page, rel)
        with open(os.path.join(ROOT, "web", "index.html"), "r", encoding="utf-8") as f:
            self.assertIn('href="manual.html"', f.read())

    def test_env_file_if_present_has_only_known_settings(self):
        # 개발 폴더에는 .env 가 없다. 제출본에는 평가용 .env 를 넣으므로, 있으면 아는 설정 줄만 있는지 본다(값은 보지 않는다).
        path = os.path.join(ROOT, ".env")
        if not os.path.exists(path):
            return
        names = set(agent.read_env_file(path))
        self.assertTrue(names <= set(["ANTHROPIC_API_KEY", "MODEL", "API_BASE", "EFFORT", "JEJU_HUB_KEY", "SBIZ_STORE_KEY", "SBIZ365_CERT_KEY"]), sorted(names))

    def test_no_secrets_or_pip_in_my_files(self):
        names = [os.path.join(ROOT, n) for n in ("run.bat", "run.sh", ".env.example")]
        for folder in ("app", "tools", "tests", os.path.join("app", "prompts")):
            d = os.path.join(ROOT, folder)
            names += [os.path.join(d, n) for n in os.listdir(d) if n.endswith((".py", ".md", ".json"))]
        key_mark = b"sk-" + b"ant-"            # 이 파일 자신이 걸리지 않게 나눠 적는다
        pip_mark = b"pip " + b"install"
        for path in names:
            with open(path, "rb") as f:
                data = f.read()
            self.assertFalse(key_mark in data, "Key 처럼 보이는 글자가 있다: " + path)
            self.assertFalse(pip_mark in data, "패키지 설치 명령이 있다: " + path)



class TestRobustness(unittest.TestCase):
    """검토 지적을 고친 곳: 쓸 수 없는 프로그램 폴더, 끊긴 연결, PORT 환경변수, 요청 줄 찍기."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="server_ro_")
        self.addCleanup(self.cleanup)
        self.patches = [mock.patch.object(agent, "ENV_FILE", os.path.join(self.tmp, "none.env")),
                        mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "API_BASE": "", "MODEL": "", "EFFORT": ""})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.httpd = None

    def cleanup(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        for root, dirs, _ in os.walk(self.tmp):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, runtime):
        self.httpd = server.make_server(0, runtime, quiet=True)
        t = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05})
        t.daemon = True
        t.start()
        return "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def post_csv(self, base):
        with open(sample_csv("1-14"), "rb") as f:
            data = f.read()
        req = urllib.request.Request(base + "/api/center-csv", data=data, method="POST",
                                     headers={"Content-Type": "application/octet-stream", "X-Filename": "Results_20261117_101502.csv"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            resp = opener.open(req, timeout=60)
        except urllib.error.HTTPError as e:
            resp = e
        try:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
        finally:
            resp.close()

    def locked_folder(self):
        ro = os.path.join(self.tmp, "readonly")
        os.makedirs(ro)
        os.chmod(ro, 0o555)
        if os.name == "nt" or os.access(ro, os.W_OK):
            self.skipTest("이 환경에서는 쓰기 금지 폴더를 만들 수 없다")
        return ro

    def test_unwritable_program_folder_falls_back_to_user_temp(self):
        ro = self.locked_folder()
        fb = os.path.join(self.tmp, "fallback-runtime")
        with mock.patch.object(server, "FALLBACK_RUNTIME_DIR", fb), mock.patch.object(tempfile, "tempdir", self.tmp):
            base = self.start(os.path.join(ro, "runtime"))
            self.assertTrue(self.httpd.store.fallback)
            self.assertEqual(self.httpd.store.dir, fb)
            status, data = self.post_csv(base)
            self.assertEqual(status, 200, data)                      # 연결이 끊기지 않고 재분석이 된다
            self.assertEqual(data["recognized"]["dataset_no"], "1-14")
            self.assertEqual(len(os.listdir(os.path.join(fb, "uploads"))), 1)
            line = [c for c in selfcheck.run(os.path.join(ro, "runtime")) if c["name"] == "임시 폴더"][0]
            self.assertTrue(line["ok"])
            self.assertIn("사용자 임시 폴더", line["detail"])           # 자가 점검 줄에 실제로 쓰는 곳을 적는다
            self.assertEqual(self.httpd.store.clear()["files"], 1)

    def test_nowhere_to_write_gives_a_korean_error_not_a_dropped_connection(self):
        ro = self.locked_folder()
        with mock.patch.object(server, "FALLBACK_RUNTIME_DIR", os.path.join(ro, "also-readonly")):
            base = self.start(os.path.join(ro, "runtime"))
            self.assertFalse(self.httpd.store.fallback)
            status, data = self.post_csv(base)                       # 2MB 본문을 다 읽은 뒤 응답한다
        self.assertEqual(status, 507, data)
        self.assertIn("임시 폴더", data["error"]["message"])
        self.assertIn("center-order", data["error"]["action"])

    def test_port_environment_variable_never_crashes_startup(self):
        for raw, want in (("abc", 0), ("", 0), ("70000", 0), ("-1", 0), ("8123", 8123)):
            with mock.patch.dict(os.environ, {"PORT": raw}):
                self.assertEqual(server._env_port(), want, raw)

    def test_aborted_connections_do_not_print_tracebacks(self):
        import io
        httpd = server.make_server(0, os.path.join(self.tmp, "rt"), quiet=False)
        try:
            for exc in (ConnectionAbortedError(10053, "aborted"), ConnectionResetError(), BrokenPipeError(), TimeoutError()):
                buf = io.StringIO()
                with mock.patch.object(server.sys, "stderr", buf):
                    try:
                        raise exc
                    except Exception:
                        httpd.handle_error(None, ("127.0.0.1", 1))
                self.assertEqual(buf.getvalue(), "", type(exc).__name__)
            buf = io.StringIO()
            with mock.patch.object(server.sys, "stderr", buf):
                try:
                    raise ValueError("x")
                except Exception:
                    httpd.handle_error(None, ("127.0.0.1", 1))
            self.assertEqual(buf.getvalue().count("\n"), 1)           # 그 밖의 오류도 한 줄만 찍는다
            self.assertNotIn("Traceback", buf.getvalue())
        finally:
            httpd.server_close()

    def test_request_lines_are_printed_only_for_errors_unless_verbose(self):
        import io
        for verbose, want_ok_line in ((False, False), (True, True)):
            httpd = server.make_server(0, os.path.join(self.tmp, "rt"), quiet=False, verbose=verbose)
            buf = io.StringIO()
            t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05})
            t.daemon = True
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = "http://127.0.0.1:%d" % httpd.server_address[1]
            try:
                with mock.patch.object(server.sys, "stderr", buf):
                    t.start()
                    opener.open(base + "/api/options", timeout=30).read()
                    try:
                        opener.open(base + "/api/nope", timeout=30).read()
                    except urllib.error.HTTPError as e:
                        e.read()
                        e.close()
                    httpd.shutdown()
            finally:
                httpd.server_close()
            out = buf.getvalue()
            self.assertEqual("/api/options" in out, want_ok_line, out)
            self.assertIn("/api/nope", out)                          # 오류(404)는 늘 찍는다

    def test_server_does_not_look_up_its_own_name(self):
        with mock.patch.object(socket, "getfqdn", side_effect=AssertionError("이름 풀이를 하면 안 된다")):
            httpd = server.make_server(0, os.path.join(self.tmp, "rt"), quiet=True)
            httpd.server_close()
        self.assertEqual(httpd.server_name, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
