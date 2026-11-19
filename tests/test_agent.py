# -*- coding: utf-8 -*-
"""Agent 루프 시험. 실제 API 를 부르지 않고 모의 응답 서버(tests/mock_llm.py)로 돌린다."""
import json
import os
import socket
import ssl
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

from tests import _base  # noqa: F401  (import 경로와 날짜 고정)
from tests._base import S1, S2, S3, TODAY
from tests.mock_llm import FAKE_KEY, MockLLM, S1_NEED, error, msg, script_s1, text, thinking, tool_use

from app import agent, planner

GOOD_SUBMIT = {"need": S1_NEED, "items": [{"dataset_no": "1-14", "why": "법정동과 시간대를 함께 가진 데이터다."}]}
BAD_SUBMIT = {"need": S1_NEED, "items": [{"dataset_no": "1-13", "months": ["2016-05"],
                                         "conditions": [{"column": "bjd", "value": "노형동"}], "why": "시간대가 있다."}]}


class AgentCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent_test_")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        for p in (mock.patch.object(agent, "ENV_FILE", os.path.join(self.tmp, "none.env")),
                  mock.patch.object(agent, "RETRY_BACKOFF", (0.01, 0.01))):
            p.start()
            self.addCleanup(p.stop)

    def env(self, base, **more):
        e = {"ANTHROPIC_API_KEY": FAKE_KEY, "API_BASE": base, "MODEL": "", "EFFORT": ""}
        e.update(more)
        return mock.patch.dict(os.environ, e)

    def run_with(self, script, sentence=S1, **more):
        m = MockLLM(script)
        base = m.start()
        self.addCleanup(m.stop)
        with self.env(base, **more):
            r = agent.plan_auto("auto", sentence, {}, "feedfacecafe", TODAY)
        return r, m

    @staticmethod
    def results_of(request):
        """요청의 마지막 user 메시지에 든 tool_result 들을 (블록, 풀어낸 내용) 으로."""
        last = request["body"]["messages"][-1]
        return [(b, json.loads(b["content"])) for b in last["content"]]


class TestLoop(AgentCase):
    def test_s1_runs_five_tools_and_passes_validation(self):
        r, m = self.run_with(script_s1())
        self.assertEqual(r["mode_used"], "llm")
        self.assertEqual(r["fallback_reason"], "")
        self.assertEqual(r["run_id"], "feedfacecafe")
        it = r["order"]["items"][0]
        self.assertEqual(it["dataset_no"], "1-14")
        conds = dict((c["column"], c["value"]) for c in it["conditions"])
        self.assertEqual(conds.get("bjd"), "노형동")
        self.assertEqual(conds.get("biz_cd"), "I56111")
        for c in it["conditions"]:
            self.assertTrue(c["evidence"]["sheet"])
            self.assertTrue(c["evidence"]["col_no"])
        self.assertEqual(len(it["months"]), 3)
        self.assertTrue(all(mm["ym"] >= "2023-08" for mm in it["months"]))
        v = r["validation"]
        self.assertEqual(v["passed"], v["total"])
        self.assertGreater(v["total"], 0)
        self.assertEqual(r["llm"]["turns"], 5)
        self.assertEqual(len(m.requests), 5)
        tools = [s["tool"] for s in r["trace"]]
        for name in agent.TOOL_NAMES:
            self.assertIn(name, tools)
        self.assertEqual([s["step"] for s in r["trace"]], list(range(1, len(r["trace"]) + 1)))

    def test_trace_keeps_why_v01_was_dropped(self):
        r, _ = self.run_with(script_s1(with_bad_draft=False))
        rows = [s for s in r["trace"] if s["args_summary"].startswith("1-13") and s["result_summary"] == "버림"]
        self.assertEqual(len(rows), 1)
        self.assertIn("v01", rows[0]["args_summary"])
        self.assertIn("법정동 컬럼이 없다", rows[0]["note"])

    def test_request_follows_api_rules(self):
        _, m = self.run_with(script_s1(with_bad_draft=False))
        h, b = m.requests[0]["headers"], m.requests[0]["body"]
        self.assertEqual(h["x-api-key"], FAKE_KEY)
        self.assertEqual(h["anthropic-version"], "2026-10-21")
        self.assertEqual(h["content-type"], "application/json")
        self.assertEqual(b["model"], "claude-haiku-5-5")
        self.assertIsInstance(b["max_tokens"], int)
        self.assertEqual(sorted(t["name"] for t in b["tools"]), sorted(agent.TOOL_NAMES))
        self.assertEqual(b["system"][0]["type"], "text")
        self.assertIn("수치를 말하지 않는다", b["system"][0]["text"])
        self.assertEqual(b["messages"][0]["role"], "user")
        self.assertIn(S1, b["messages"][0]["content"])
        # 현행 모델에서 400 이 나는 매개변수를 보내지 않는다.
        for banned in ("temperature", "top_p", "top_k", "fallbacks", "tool_choice", "output_format"):
            self.assertNotIn(banned, b)
        self.assertNotIn("budget_tokens", json.dumps(b))
        self.assertNotIn("output_config", b)            # EFFORT 를 주지 않으면 보내지 않는다
        self.assertNotEqual(b["messages"][-1]["role"], "assistant")   # 미리 채우기(prefill) 없음

    def test_effort_is_sent_only_when_set(self):
        _, m = self.run_with(script_s1(with_bad_draft=False), EFFORT="low", MODEL="claude-sonnet-5-5")
        b = m.requests[0]["body"]
        self.assertEqual(b["output_config"], {"effort": "low"})
        self.assertEqual(b["model"], "claude-sonnet-5-5")

    def test_response_content_is_appended_unchanged_and_history_only_grows(self):
        script = script_s1()
        _, m = self.run_with(script)
        for i in range(1, len(m.requests)):
            prev, cur = m.requests[i - 1]["body"], m.requests[i]["body"]
            self.assertEqual(cur["messages"][:len(prev["messages"])], prev["messages"])   # 앞부분은 그대로
            self.assertEqual(cur["system"], prev["system"])
            self.assertEqual(cur["tools"], prev["tools"])
            # 바로 앞 응답의 content 가 한 글자도 바뀌지 않고 붙었다(thinking 블록 포함)
            self.assertEqual(cur["messages"][len(prev["messages"])],
                             {"role": "assistant", "content": script[i - 1]["body"]["content"]})
        self.assertEqual(m.requests[1]["body"]["messages"][1]["content"][0], thinking("sig-1"))

    def test_parallel_tool_results_come_back_in_one_user_message(self):
        script = script_s1(with_bad_draft=False)
        _, m = self.run_with(script)
        msgs = m.requests[1]["body"]["messages"]
        self.assertEqual([x["role"] for x in msgs], ["user", "assistant", "user"])
        blocks = msgs[2]["content"]
        self.assertEqual([b["type"] for b in blocks], ["tool_result", "tool_result"])
        want = [b["id"] for b in script[0]["body"]["content"] if b["type"] == "tool_use"]
        self.assertEqual([b["tool_use_id"] for b in blocks], want)
        self.assertIn("d1048", json.loads(blocks[0]["content"]))
        self.assertTrue(json.loads(blocks[1]["content"])["available"])

    def test_validator_issues_are_returned_to_the_model(self):
        r, m = self.run_with(script_s1())
        block, out = self.results_of(m.requests[4])[0]
        self.assertFalse(out["ok"])
        self.assertEqual(out["fixes_left"], 1)
        joined = " ".join(out["issues"])
        self.assertIn("수록 범위", joined)
        self.assertIn("bjd 컬럼이 없다", joined)
        self.assertIn("agee", joined)
        self.assertIn("파티션", joined)
        self.assertTrue(any("되돌려 보냄" in s["result_summary"] for s in r["trace"]))
        self.assertEqual(r["order"]["items"][0]["dataset_no"], "1-14")

    def test_month_not_chosen_by_code_is_bounced(self):
        bad = {"need": S1_NEED, "items": [{"dataset_no": "1-14", "months": ["2024-01"], "why": "이유"}]}
        r, m = self.run_with([msg([tool_use("submit_order", bad)]), msg([tool_use("submit_order", GOOD_SUBMIT)])])
        _, out = self.results_of(m.requests[1])[0]
        self.assertFalse(out["ok"])
        self.assertIn("pick_months 가 고른 달", out["issues"][0])
        self.assertEqual([x["ym"] for x in r["order"]["items"][0]["months"]], ["2024-08", "2025-02", "2025-04"])

    def test_unknown_biz_or_region_is_bounced(self):
        need = dict(S1_NEED, biz="우주선 판매업", region="없는동")
        r, m = self.run_with([msg([tool_use("submit_order", {"need": need, "items": []})]),
                              msg([tool_use("submit_order", GOOD_SUBMIT)])])
        _, out = self.results_of(m.requests[1])[0]
        self.assertFalse(out["ok"])
        self.assertIn("42개 업종에서 찾지 못했다", " ".join(out["issues"]))
        self.assertIn("없는동", " ".join(out["issues"]))
        self.assertEqual(r["mode_used"], "llm")

    def test_after_two_fixes_code_rebuilds_items_by_rule(self):
        r, m = self.run_with([msg([tool_use("submit_order", BAD_SUBMIT)])] * 3)
        self.assertEqual(len(m.requests), 3)
        self.assertEqual(r["mode_used"], "llm")
        self.assertEqual(r["order"]["items"][0]["dataset_no"], "1-14")
        self.assertEqual(r["order"]["items"][0]["why_source"], "rule")
        self.assertEqual(r["validation"]["passed"], r["validation"]["total"])
        self.assertTrue(any("고칠 기회" in s["result_summary"] for s in r["trace"]))
        self.assertTrue(any("규칙으로 다시 짰다" in x for x in r["limits"]))

    def test_bad_tool_input_and_unknown_tool_get_error_results(self):
        r, m = self.run_with([
            msg([tool_use("public_monthly", {"source": "9999", "extra": 1}), tool_use("no_such_tool", {})]),
            msg([tool_use("catalog_search", {"units": "slot", "subject": "spend"})]),
            msg([tool_use("submit_order", GOOD_SUBMIT)]),
        ])
        (b1, o1), (b2, o2) = self.results_of(m.requests[1])
        self.assertTrue(b1["is_error"])
        self.assertIn("9999", " ".join(o1["problems"]))
        self.assertIn("모르는 칸", " ".join(o1["problems"]))
        self.assertTrue(b2["is_error"])
        self.assertIn("도구는 없다", o2["error"])
        b3, o3 = self.results_of(m.requests[2])[0]
        self.assertTrue(b3["is_error"])
        self.assertIn("목록", " ".join(o3["problems"]))
        self.assertEqual(r["mode_used"], "llm")

    def test_null_optional_fields_are_treated_as_missing(self):
        r, m = self.run_with([
            msg([tool_use("public_monthly", {"source": "1048", "biz": None, "who": None})]),
            msg([tool_use("submit_order", GOOD_SUBMIT)])])
        block, out = self.results_of(m.requests[1])[0]
        self.assertNotIn("is_error", block)
        self.assertTrue(out["available"])

    def test_second_submit_in_same_response_is_not_run(self):
        r, m = self.run_with([msg([tool_use("submit_order", BAD_SUBMIT), tool_use("submit_order", GOOD_SUBMIT)]),
                              msg([tool_use("submit_order", GOOD_SUBMIT)])])
        (b1, o1), (b2, o2) = self.results_of(m.requests[1])
        self.assertFalse(o1["ok"])
        self.assertTrue(b2["is_error"])
        self.assertIn("한 번에 하나", o2["error"])
        self.assertEqual(r["order"]["items"][0]["dataset_no"], "1-14")

    def test_model_that_stops_without_order_is_nudged_once(self):
        r, m = self.run_with([msg([text("1-14 를 받으면 된다.")]), msg([tool_use("submit_order", GOOD_SUBMIT)])])
        last = m.requests[1]["body"]["messages"][-1]
        self.assertEqual(last["role"], "user")
        self.assertIn("submit_order", last["content"])
        self.assertEqual(r["mode_used"], "llm")
        r2, m2 = self.run_with([msg([text("끝.")]), msg([text("정말 끝.")])])
        self.assertEqual(r2["mode_used"], "rule")
        self.assertIn("주문서(submit_order)를 내지 않고", r2["fallback_reason"])
        self.assertEqual(len(m2.requests), 2)

    def test_numbers_and_public_values_only_come_from_code(self):
        r, m = self.run_with(script_s1())
        why = r["order"]["items"][0]["why"]
        self.assertIn("1-13", why)
        self.assertIn("v01", why)
        self.assertNotIn("12억", why)
        self.assertEqual(r["order"]["items"][0]["why_source"], "llm")
        self.assertTrue(any(s["tool"] == "글 다듬기" for s in r["trace"]))
        # 공개 데이터 칸은 LLM 이 받은 요약이 아니라 코드가 다시 계산한 값이다.
        self.assertEqual(r["public_answer"], planner.public_answer(r["need"]))
        self.assertEqual(len(r["public_answer"]["series"]), 90)
        # LLM 에게는 월별 값 전체를 보내지 않는다.
        _, pm = self.results_of(m.requests[1])[1]
        self.assertNotIn("series", pm)

    def test_sent_to_llm_counts(self):
        r, _ = self.run_with(script_s1())
        s = r["sent_to_llm"]
        self.assertIs(s["purpose_text"], True)
        self.assertGreater(s["public_aggregates"], 0)
        self.assertGreater(s["spec_items"], 0)
        self.assertEqual(s["center_values"], 0)

    def test_s2_model_chooses_between_two_alternatives(self):
        need = {"subject": "spend", "region": "애월읍", "biz": "비알콜 음료점업", "who": "all", "units": ["dow", "slot", "age"],
                "focus": {"dow": ["토요일"], "slot": ["점심"], "age": ["20대"]}, "cross": True}
        search = dict(need)
        script = [
            msg([tool_use("catalog_search", search)]),
            msg([tool_use("submit_order", {"need": need, "choose": "split", "items": [
                {"dataset_no": "1-12", "why": "요일과 연령대를 본다."}, {"dataset_no": "1-14", "why": "시간대와 연령대를 본다."}]})]),
        ]
        r, m = self.run_with(script, S2)
        _, out = self.results_of(m.requests[1])[0]
        self.assertEqual(out["mode"], "compromise")
        self.assertEqual(sorted(a["key"] for a in out["suggestion"]["alternatives"]), ["single", "split"])
        self.assertEqual(r["mode_used"], "llm")
        self.assertEqual(sorted(i["dataset_no"] for i in r["order"]["items"]), ["1-12", "1-14"])
        alts = dict((a["key"], a) for a in r["order"]["alternatives"])
        self.assertTrue(alts["split"]["recommended"])
        self.assertFalse(alts["single"]["recommended"])
        self.assertEqual([i["dataset_no"] for i in alts["single"]["items"]], ["1-22"])
        self.assertEqual(alts["split"]["downloads"], 6)
        self.assertEqual(r["validation"]["passed"], r["validation"]["total"])
        self.assertTrue(any(s["tool"] == "타협안 비교" for s in r["trace"]))
        self.assertTrue(any("한 데이터로 필요한 단위를 다 채우는 것이 없다" in s["result_summary"] for s in r["trace"]))

    def test_s2_items_and_choose_must_point_to_the_same_alternative(self):
        need = {"subject": "spend", "region": "애월읍", "biz": "카페", "who": "all", "units": ["dow", "slot", "age"],
                "focus": {"age": ["20대"]}, "cross": True}
        mismatch = {"need": need, "choose": "single", "items": [
            {"dataset_no": "1-12", "why": "요일"}, {"dataset_no": "1-14", "why": "시간대"}]}
        fixed = {"need": need, "choose": "single", "items": [{"dataset_no": "1-22", "why": "요일과 시간대를 한 표에서 본다."}]}
        r, m = self.run_with([msg([tool_use("submit_order", mismatch)]), msg([tool_use("submit_order", fixed)])], S2)
        _, out = self.results_of(m.requests[1])[0]
        self.assertFalse(out["ok"])
        self.assertEqual([i["dataset_no"] for i in r["order"]["items"]], ["1-22"])

    def test_s3_no_public_answer_and_age_has_no_data(self):
        need = {"subject": "visit", "region": "성산읍", "who": "foreign", "units": ["age", "slot"]}
        script = [
            msg([tool_use("public_monthly", {"source": "520", "who": "foreign"})]),
            msg([tool_use("catalog_search", dict(need))]),
            msg([tool_use("submit_order", {"need": need, "items": [{"dataset_no": "4-15", "why": "외국인 유동인구를 행정동과 시간대로 가진 데이터다."}]})]),
        ]
        r, m = self.run_with(script, S3)
        _, pm = self.results_of(m.requests[1])[0]
        self.assertFalse(pm["available"])
        _, cs = self.results_of(m.requests[2])[0]
        self.assertIn("연령대", cs["no_data"])
        self.assertEqual(r["mode_used"], "llm")
        self.assertFalse(r["public_answer"]["available"])
        self.assertEqual(r["order"]["items"][0]["dataset_no"], "4-15")
        self.assertEqual([n["key"] for n in r["order"]["no_data"]], ["age"])
        gap = [g for g in r["gaps"] if g["key"] == "age"][0]
        self.assertEqual(gap["status"], "메울 데이터 없음")
        self.assertEqual(r["validation"]["passed"], r["validation"]["total"])


class TestFallback(AgentCase):
    def assert_rule(self, r, *words):
        self.assertEqual(r["mode_used"], "rule")
        for w in words:
            self.assertIn(w, r["fallback_reason"])
        self.assertNotIn(FAKE_KEY, json.dumps(r, ensure_ascii=False))
        # 규칙 모드로 넘어가도 주문서는 끝까지 나온다.
        self.assertEqual(r["order"]["items"][0]["dataset_no"], "1-14")
        self.assertEqual(r["sent_to_llm"]["center_values"], 0)

    def test_no_key_goes_to_rule_mode_without_network(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "API_BASE": "", "MODEL": ""}):
            with mock.patch.object(agent, "_open", side_effect=AssertionError("네트워크를 쓰면 안 된다")):
                r = agent.plan_auto("auto", S1, {}, None, TODAY)
                st = agent.llm_status()
        self.assert_rule(r, "ANTHROPIC_API_KEY", "조치")
        self.assertFalse(st["enabled"])
        self.assertEqual(st["model"], "claude-haiku-5-5")

    def test_rule_mode_never_calls_llm_even_with_key(self):
        with self.env("http://127.0.0.1:9"):
            with mock.patch.object(agent, "_open", side_effect=AssertionError("네트워크를 쓰면 안 된다")):
                r = agent.plan_auto("rule", S1, {}, None, TODAY)
                r2 = agent.plan_auto("auto", "", {"what": "biz:I56111", "region": "노형동", "when": "slot"}, None, TODAY)
        self.assertEqual((r["mode_used"], r["fallback_reason"]), ("rule", ""))
        self.assertEqual((r2["mode_used"], r2["fallback_reason"]), ("rule", ""))

    def test_429_is_retried_twice_then_falls_back(self):
        r, m = self.run_with([error(429, "rate_limit_error")] * 5)
        self.assertEqual(len(m.requests), 3)
        self.assert_rule(r, "429", "2번 다시 시도")

    def test_5xx_is_retried_and_then_succeeds(self):
        r, m = self.run_with([error(500, "api_error"), error(529, "overloaded_error")] + script_s1(with_bad_draft=False))
        self.assertEqual(r["mode_used"], "llm")
        self.assertEqual(len(m.requests), 6)
        self.assertEqual(r["llm"]["turns"], 4)

    def test_401_is_not_retried(self):
        r, m = self.run_with([error(401, "authentication_error")] * 3)
        self.assertEqual(len(m.requests), 1)
        self.assert_rule(r, "401", "ANTHROPIC_API_KEY")

    def test_403_and_404_and_400_are_not_retried(self):
        for status, etype, word in ((403, "permission_error", "막았다"), (404, "not_found_error", "MODEL"),
                                    (400, "invalid_request_error", "받아 주지 않았다")):
            r, m = self.run_with([error(status, etype, "detail text")] * 3)
            self.assertEqual(len(m.requests), 1, status)
            self.assert_rule(r, str(status), word)

    def test_connection_refused_falls_back(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.env("http://127.0.0.1:%d" % port):
            r = agent.plan_auto("auto", S1, {}, None, TODAY)
        self.assert_rule(r, "연결하지 못했다", "방화벽")

    def test_dropped_connection_falls_back(self):
        r, m = self.run_with([{"drop": True}] * 3)
        self.assertEqual(len(m.requests), 3)
        self.assert_rule(r, "2번 다시 시도")

    def test_timeout_falls_back(self):
        with mock.patch.object(agent, "REQUEST_TIMEOUT", 0.3):
            r, m = self.run_with([dict(msg([tool_use("public_scope", {})]), sleep=1.2)] * 3)
        self.assertEqual(len(m.requests), 3)
        self.assert_rule(r, "응답하지 않았다")

    def test_non_json_response_falls_back(self):
        r, m = self.run_with([{"status": 200, "raw": b"<html>proxy</html>"}] * 3)
        self.assert_rule(r, "JSON 이 아니다")

    def test_turn_cap_of_eight(self):
        r, m = self.run_with(lambda body, i: msg([tool_use("public_scope", {})]))
        self.assertEqual(len(m.requests), 8)
        self.assert_rule(r, "8턴")

    def test_refusal_and_max_tokens_stop_the_loop_without_running_tools(self):
        r, m = self.run_with([msg([tool_use("submit_order", GOOD_SUBMIT)], stop_reason="refusal")])
        self.assertEqual(len(m.requests), 1)
        self.assert_rule(r, "거절")
        r, m = self.run_with([msg([tool_use("submit_order", GOOD_SUBMIT)], stop_reason="max_tokens")])
        self.assertEqual(len(m.requests), 1)
        self.assert_rule(r, "잘렸다")

    def test_plain_http_to_other_host_is_refused_before_sending_key(self):
        with self.env("http://example.com"):
            with mock.patch.object(agent, "_open", side_effect=AssertionError("보내면 안 된다")):
                r = agent.plan_auto("auto", S1, {}, None, TODAY)
                st = agent.llm_status()
        self.assert_rule(r, "API_BASE")
        self.assertFalse(st["enabled"])

    def test_certificate_failure_says_cause_and_action(self):
        err = urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer"))
        with self.env("https://api.anthropic.com"):
            with mock.patch.object(agent, "_open", side_effect=err) as op:
                r = agent.plan_auto("auto", S1, {}, None, TODAY)
        self.assertEqual(op.call_count, 1)             # 인증서 문제는 다시 해도 같으므로 되풀이하지 않는다
        self.assert_rule(r, "인증서", "조치")
        self.assertEqual(r["sent_to_llm"]["purpose_text"], False)      # 연결이 맺어지지 않았으니 나간 것이 없다

    def test_unexpected_error_in_loop_still_returns_an_order(self):
        with self.env("http://127.0.0.1:9"):
            with mock.patch.object(agent, "call_messages", side_effect=RuntimeError("boom")):
                r = agent.plan_auto("auto", S1, {}, None, TODAY)
        self.assert_rule(r, "RuntimeError")


class TestSsl(AgentCase):
    CERT_FAIL = staticmethod(lambda: urllib.error.URLError(ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED")))

    def fresh_state(self):
        return mock.patch.dict(agent._ssl_state, {"ctx": None, "stage": 0, "mac_fallback": False})

    def test_mac_retries_with_system_bundle_and_never_disables_verification(self):
        calls = []
        ok_after = [3]

        class FakeOpener(object):
            def open(self_, req, timeout=None):
                calls.append("open")
                if len(calls) <= ok_after[0]:
                    raise self.CERT_FAIL()
                return "response"

        made = []
        real = agent._ssl_context

        def fake_ctx(use_mac_bundle=False, relaxed=False):
            made.append((use_mac_bundle, relaxed))
            return real(False, relaxed)

        req = agent.urllib.request.Request("https://api.anthropic.com/v1/models")
        with self.fresh_state(), \
                mock.patch.object(agent, "_ssl_stages", return_value=[(False, False), (False, True), (True, False), (True, True)]), \
                mock.patch.object(agent, "_ssl_context", side_effect=fake_ctx), \
                mock.patch.object(agent.urllib.request, "build_opener", return_value=FakeOpener()):
            self.assertEqual(agent._open(req, 5), "response")
            # 기본 → 엄격 검사만 푼 설정 → macOS 시스템 묶음 → 시스템 묶음 + 엄격 검사 풂. 네 번째에서 됐다.
            self.assertEqual(made, [(False, False), (False, True), (True, False), (True, True)])
            self.assertTrue(agent._ssl_state["mac_fallback"])
            self.assertEqual(agent._ssl_state["stage"], 3)
            # 끝 단계까지 간 뒤에 또 실패하면 그대로 올려 보낸다(끝없이 되풀이하지 않는다).
            calls[:] = []
            ok_after[0] = 99
            with self.assertRaises(urllib.error.URLError):
                agent._open(req, 5)
            self.assertEqual(len(calls), 1)

    def test_windows_retries_once_without_strict_flag_only(self):
        # 파이썬 3.13 부터 켜진 엄격 검사(VERIFY_X509_STRICT)만 풀어 한 번 더 한다. macOS 묶음으로는 가지 않는다.
        calls = []

        class FakeOpener(object):
            def open(self_, req, timeout=None):
                calls.append("open")
                if len(calls) == 1:
                    raise self.CERT_FAIL()
                return "response"

        req = agent.urllib.request.Request("https://api.anthropic.com/v1/models")
        with self.fresh_state(), \
                mock.patch.object(agent, "_ssl_stages", return_value=[(False, False), (False, True)]), \
                mock.patch.object(agent.urllib.request, "build_opener", return_value=FakeOpener()):
            self.assertEqual(agent._open(req, 5), "response")
            self.assertEqual(agent._ssl_state["stage"], 1)
            self.assertFalse(agent._ssl_state["mac_fallback"])
            ctx = agent._ssl_state["ctx"]
            self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)       # 검증은 그대로 켜져 있다
            self.assertTrue(ctx.check_hostname)
            if getattr(ssl, "VERIFY_X509_STRICT", 0):
                self.assertFalse(ctx.verify_flags & ssl.VERIFY_X509_STRICT)

    def test_stages_by_platform(self):
        strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
        has_strict = bool(strict and (ssl.create_default_context().verify_flags & strict))
        self.assertEqual(has_strict, sys.version_info >= (3, 13))      # 3.13 부터 기본으로 켜진다
        with mock.patch.object(agent.sys, "platform", "win32"):
            self.assertEqual(agent._ssl_stages(), [(False, False), (False, True)] if has_strict else [(False, False)])
        with mock.patch.object(agent.sys, "platform", "darwin"), mock.patch.object(agent.os.path, "exists", return_value=True):
            st = agent._ssl_stages()
            self.assertIn((True, False), st)
            self.assertEqual(st[0], (False, False))

    def test_no_stage_left_raises(self):
        class FakeOpener(object):
            def open(self_, req, timeout=None):
                raise self.CERT_FAIL()

        req = agent.urllib.request.Request("https://api.anthropic.com/v1/models")
        with self.fresh_state(), \
                mock.patch.object(agent, "_ssl_stages", return_value=[(False, False)]), \
                mock.patch.object(agent.urllib.request, "build_opener", return_value=FakeOpener()):
            with self.assertRaises(urllib.error.URLError):
                agent._open(req, 5)
            self.assertFalse(agent._ssl_state["mac_fallback"])

    def test_certificate_message_by_platform(self):
        e = ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED")
        e.verify_message = "Missing Authority Key Identifier"
        err = urllib.error.URLError(e)
        with mock.patch.object(agent.sys, "platform", "win32"):
            x = agent._cert_error(err, "https://api.anthropic.com/v1/messages")
        self.assertIn("Windows 인증서 저장소", x.reason())
        self.assertIn("Edge", x.reason())
        self.assertIn("https://api.anthropic.com", x.reason())
        self.assertIn("Missing Authority Key Identifier", x.reason())   # 파이썬이 알려 준 까닭을 붙인다
        self.assertNotIn("Install Certificates", x.reason())
        with mock.patch.object(agent.sys, "platform", "darwin"):
            self.assertIn("Install Certificates.command", agent._cert_error(err, "https://api.anthropic.com/v1/messages").reason())

    def test_source_has_no_switch_that_turns_verification_off(self):
        with open(agent.__file__.replace(".pyc", ".py"), "r", encoding="utf-8") as f:
            src = f.read()
        for bad in ("CERT_NONE", "_create_unverified_context", "check_hostname = False", "verify=False"):
            self.assertNotIn(bad, src.replace("(CERT_NONE, verify=False)", ""))
        for relaxed in (False, True):
            ctx = agent._ssl_context(False, relaxed)
            self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(ctx.check_hostname)


class TestSettingsAndStatus(AgentCase):
    def test_env_file_is_read_and_environment_wins(self):
        path = os.path.join(self.tmp, "t.env")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 주석\nANTHROPIC_API_KEY=\"from-file\"\nMODEL=claude-sonnet-5-5\nAPI_BASE=http://127.0.0.1:1/\nEFFORT=HIGH\nexport X=1\n")
        with mock.patch.object(agent, "ENV_FILE", path):
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "MODEL": "", "API_BASE": "", "EFFORT": ""}):
                s = agent.settings()
                self.assertEqual((s["key"], s["model"], s["api_base"], s["effort"]),
                                 ("from-file", "claude-sonnet-5-5", "http://127.0.0.1:1", "high"))
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "from-env", "MODEL": "claude-opus-5-5", "API_BASE": "", "EFFORT": "bogus"}):
                s = agent.settings()
                self.assertEqual((s["key"], s["model"], s["effort"]), ("from-env", "claude-opus-5-5", ""))

    def test_defaults(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "MODEL": "", "API_BASE": "", "EFFORT": ""}):
            s = agent.settings()
        self.assertEqual(s, {"key": "", "model": "claude-haiku-5-5", "api_base": "https://api.anthropic.com", "effort": ""})

    def test_probe_ok_failed_and_unsure(self):
        for status, enabled, checked in ((200, True, "ok"), (401, False, "failed"), (404, True, "unsure"), (500, True, "unsure")):
            m = MockLLM([], models_status=status)
            base = m.start()
            try:
                with self.env(base):
                    agent.check_connection()
                    st = agent.llm_status()
            finally:
                m.stop()
            self.assertEqual((st["enabled"], st["checked"]), (enabled, checked), status)
            self.assertEqual(len(m.model_requests), 1)
            self.assertEqual(len(m.requests), 0)           # 확인은 토큰을 쓰는 호출을 하지 않는다
            self.assertNotIn(FAKE_KEY, json.dumps(st, ensure_ascii=False))
            if status == 401:
                self.assertIn("ANTHROPIC_API_KEY", st["reason"])

    def test_background_probe(self):
        m = MockLLM([], models_status=200)
        base = m.start()
        self.addCleanup(m.stop)
        with self.env(base):
            t = agent.start_probe()
            st = agent.llm_status(wait=5.0)
            t.join(5)
        self.assertEqual((st["enabled"], st["checked"]), (True, "ok"))


class TestTextAndSchema(unittest.TestCase):
    def test_sentences_with_numbers_are_dropped(self):
        out, n = agent.clean_text("1-14 는 법정동이 있다. 매출은 3,500만원이다. 20대는 v02 에서 본다. 비중은 41.3%다.")
        self.assertEqual(out, "1-14 는 법정동이 있다. 20대는 v02 에서 본다.")
        self.assertEqual(n, 2)

    def test_spec_label_and_hub_names_are_kept(self):
        label = "명세서 v13(2026-11-04) 기준 데이터 71종, 참고표 2종"
        out, n = agent.clean_text(label + " 가운데 골랐다. 허브 1048 과 같은 값 체계다. 65종 가운데 골랐다.", spec_label=label)
        self.assertIn(label, out)
        self.assertIn("허브 1048", out)
        self.assertNotIn("65종", out)
        self.assertEqual(n, 1)

    def test_banned_words_symbols_and_length(self):
        out, n = agent.clean_text("국내 최초의 방법이다. 최적의 달을 골랐다. 평균에 가까운 달을 골랐다 \U0001F600.")
        self.assertEqual(out, "평균에 가까운 달을 골랐다 .")
        self.assertEqual(n, 2)
        out, _ = agent.clean_text("가" * 1000, limit=50)
        self.assertEqual(len(out), 50)
        self.assertEqual(agent.clean_text(None), ("", 0))
        self.assertEqual(agent.clean_text(123), ("", 0))

    def test_schema_check(self):
        tools = dict((t["name"], t["input_schema"]) for t in agent.tool_defs())
        self.assertEqual(agent.check_schema({}, tools["public_scope"]), [])
        self.assertEqual(agent.check_schema({"source": "1048", "who": "all"}, tools["public_monthly"]), [])
        self.assertTrue(agent.check_schema({}, tools["public_monthly"]))
        self.assertTrue(agent.check_schema({"source": 1048}, tools["public_monthly"]))
        self.assertTrue(agent.check_schema([], tools["public_monthly"]))
        self.assertTrue(agent.check_schema({"units": ["weekday"], "subject": "spend"}, tools["catalog_search"]))
        self.assertTrue(agent.check_schema({"units": [], "subject": "spend", "cross": "yes"}, tools["catalog_search"]))
        self.assertTrue(agent.check_schema({"units": [], "subject": "spend", "focus": {"age": ["25세"]}}, tools["catalog_search"]))
        self.assertEqual(agent.check_schema(GOOD_SUBMIT, tools["submit_order"]), [])
        self.assertTrue(agent.check_schema({"need": S1_NEED}, tools["submit_order"]))
        self.assertTrue(agent.check_schema({"need": S1_NEED, "items": [{"dataset_no": "1-14"}]}, tools["submit_order"]))
        self.assertTrue(agent.check_schema({"need": S1_NEED, "items": [{"dataset_no": "1-14", "why": "x"}] * 5}, tools["submit_order"]))

    def test_tool_definitions_are_five_with_descriptions(self):
        tools = agent.tool_defs()
        self.assertEqual([t["name"] for t in tools], list(agent.TOOL_NAMES))
        for t in tools:
            self.assertGreater(len(t["description"]), 40)
            self.assertEqual(t["input_schema"]["type"], "object")
            self.assertIs(t["input_schema"]["additionalProperties"], False)

    def test_system_prompt_has_the_required_rules(self):
        p = agent.system_prompt()
        for word in ("역할", "수치를 말하지 않는다", "없으면 없다고 한다", "두 안을 비교", "종수", "submit_order",
                     "명세서 v13(2026-11-04) 기준 데이터 71종, 참고표 2종"):
            self.assertIn(word, p)
        for emoji_range in (range(0x1F000, 0x1FAFF), range(0x2600, 0x27BF)):
            self.assertFalse(any(ord(ch) in emoji_range for ch in p))



class TestReviewFixes(AgentCase):
    """검토 지적을 고친 곳: 실패 전에 보낸 내용, 도구 인자의 글자, 한글 수량 표현, 기록의 앞뒤, .env 인코딩."""

    def test_fallback_after_some_calls_reports_what_was_already_sent(self):
        script = script_s1(with_bad_draft=False)[:2] + [error(500, "api_error")] * 5
        r, m = self.run_with(script)
        self.assertEqual(r["mode_used"], "rule")
        self.assertEqual(len(m.requests), 5)                         # 두 번은 응답, 세 번째는 500 을 두 번 더 다시 시도
        sent = r["sent_to_llm"]
        self.assertIs(sent["purpose_text"], True)                    # 목적 문장은 이미 나갔다
        self.assertGreater(sent["public_aggregates"], 0)
        self.assertGreater(sent["spec_items"], 0)
        self.assertEqual(sent["center_values"], 0)
        self.assertEqual((sent["llm_calls"], sent["llm_requests"]), (2, 3))
        self.assertEqual(r["order"]["items"][0]["dataset_no"], "1-14")   # 주문서는 규칙 모드로 끝까지 나온다

    def test_failure_before_anything_was_delivered_reports_nothing_sent(self):
        with self.env("http://127.0.0.1:9"):                         # 연결 거절: 본문이 나가지 않았다
            r = agent.plan_auto("auto", S1, {}, None, TODAY)
        self.assertEqual(r["mode_used"], "rule")
        self.assertIs(r["sent_to_llm"]["purpose_text"], False)
        self.assertEqual(r["sent_to_llm"]["public_aggregates"], 0)

    def test_first_request_rejected_still_counts_the_purpose_text(self):
        r, m = self.run_with([error(401, "authentication_error")])
        self.assertEqual(len(m.requests), 1)
        self.assertIs(r["sent_to_llm"]["purpose_text"], True)        # 서버가 받아서 거절한 것이므로 나간 것으로 센다
        self.assertEqual(r["sent_to_llm"]["llm_calls"], 0)

    def test_numbers_cannot_reach_the_screen_through_tool_arguments(self):
        script = [
            msg([text("저녁 매출이 두 배다."),
                 tool_use("pick_months", {"dataset_no": "9-99 매출 300억", "subject": "spend"})]),
            msg([tool_use("submit_order", {"need": dict(S1_NEED, biz="매출 300억 업종"), "items": [{
                "dataset_no": "1-99", "months": ["8-12월"],
                "conditions": [{"column": "bjd 300억", "value": "매출 삼십억 원"}], "why": "이유"}]})]),
            msg([tool_use("submit_order", GOOD_SUBMIT)]),
        ]
        r, m = self.run_with(script)
        self.assertEqual(r["mode_used"], "llm")
        flat = json.dumps(r["trace"], ensure_ascii=False)
        for bad in ("300억", "삼십억", "두 배", "8-12월", "9-99"):
            self.assertNotIn(bad, flat, bad)
        self.assertIn("형식이 틀린 번호", flat)
        self.assertIn("명세서에 없는 번호", flat)                    # 1-99 는 형식은 맞지만 명세서에 없다
        self.assertEqual(r["order"]["items"][0]["dataset_no"], "1-14")

    def test_quantity_words_without_digits_are_dropped(self):
        for sent in ("8-12월에 매출이 가장 높다.", "겨울에는 절반으로 준다.", "노형동은 저녁 매출이 도내 일등이다.",
                     "이용금액은 삼십억 원쯤이다.", "1-99 데이터도 있다.", "매출 90대 기업이다.", "v9 판이 최신이다.",
                     "저녁 매출이 두 배다.", "약 １２억 원이다.", "수천 건이다.", "관광객이 과반이다."):
            self.assertEqual(agent.clean_text(sent), ("", 1), sent)
        keep = ("법정동과 시간대를 함께 가진 데이터는 1-14 뿐이다. v01 인 1-13 은 권역까지만 있어 버렸다. "
                "요일만 본다. 이용건수만 본다. 요일 위주로 본다. 20대와 70대를 따로 본다. 허브 1048 과 값 체계가 같다.")
        self.assertEqual(agent.clean_text(keep), (keep, 0))

    def test_internal_english_keys_are_translated(self):
        out, _ = agent.clean_text("연령대는 메울 데이터가 없다(no_data). split 안을 권한다.")
        self.assertNotIn("no_data", out)
        self.assertNotIn("split", out)
        self.assertIn("여러 종을 따로 받기", out)

    def test_trace_explains_searches_that_differ_from_the_final_order(self):
        wide = {"units": ["slot", "nationality", "user_type", "age", "sex"], "subject": "spend", "region": "노형동",
                "biz": "한식 음식점업", "who": "all"}
        visit = {"units": ["slot", "age"], "subject": "visit", "region": "노형동", "who": "all"}
        same = {"units": ["slot", "user_type", "age", "sex"], "subject": "spend", "region": "노형동",
                "biz": "한식 음식점업", "who": "all", "focus": {"slot": ["저녁"]}}
        script = [msg([tool_use("catalog_search", visit)]), msg([tool_use("catalog_search", wide)]),
                  msg([tool_use("catalog_search", same)]), msg([tool_use("catalog_search", same)]),
                  msg([tool_use("submit_order", dict(GOOD_SUBMIT, choose="single"))])]
        r, _ = self.run_with(script)
        self.assertEqual(r["mode_used"], "llm")
        searches = [t for t in r["trace"] if t["tool"] == "catalog_search" and not t.get("sub")]
        self.assertEqual(len(searches), 3)                           # 같은 검색 두 번은 한 줄로 묶인다
        self.assertIn("그 후보는 쓰지 않았다", searches[0]["note"])   # 다른 계열(방문·유동인구)을 본 검색
        self.assertIn("넓혀서 찾음", searches[1]["note"])
        self.assertIn("국적", searches[1]["note"])
        self.assertIn("같은 검색을 2번", searches[2]["note"])
        # 버린 후보는 마지막 검색 바로 아래에 붙는다
        i = r["trace"].index(searches[2])
        self.assertTrue(r["trace"][i + 1].get("sub"))
        self.assertEqual(r["trace"][i + 1]["result_summary"], "버림")
        # LLM 이 공개 집계를 부르지 않았으면 코드가 채웠다고 적는다
        self.assertTrue(any(t["tool"] == "코드가 채움" for t in r["trace"]))
        flat = json.dumps(r["trace"], ensure_ascii=False)
        self.assertNotIn("single", flat)
        self.assertIn("먼저 권하는 안: 한 종으로 함께 보기", flat)
        for t in r["trace"]:
            self.assertNotIn("_meta", t)

    def test_units_the_sentence_never_mentioned_are_dropped(self):
        # 시나리오 1 문장에는 요일을 가리키는 말이 없다. LLM 이 요일과 교차를 넣어도 받을 데이터가 늘지 않는다.
        need = dict(S1_NEED, units=["slot", "dow", "user_type", "age", "sex"], cross=True, focus={"slot": ["저녁"], "dow": ["토요일"]})
        r, m = self.run_with([msg([tool_use("catalog_search", dict(need))]),
                              msg([tool_use("submit_order", {"need": need, "items": [{"dataset_no": "1-14", "why": "시간대가 있다."}]})])])
        self.assertEqual(r["mode_used"], "llm")
        self.assertEqual([i["dataset_no"] for i in r["order"]["items"]], ["1-14"])
        self.assertNotIn("dow", [u["key"] for u in r["need"]["units"]])
        self.assertNotIn("dow", r["need"]["focus"])
        self.assertTrue(any("'요일' 단위를 뺐다" in n for n in r["need"]["notes"]))
        _, out = self.results_of(m.requests[1])[0]
        self.assertTrue(any("'요일' 단위를 뺐다" in p for p in out["understood"]["problems"]))   # LLM 에게도 알린다
        # 문장에 요일이 있는 시나리오 2 는 그대로 둔다
        self.assertTrue(planner.text_supports_unit(S2, "dow"))
        self.assertTrue(planner.text_supports_unit(S2, "slot"))
        self.assertTrue(planner.text_supports_unit(S3, "slot"))
        self.assertFalse(planner.text_supports_unit(S1, "dow"))

    def test_out_of_scope_sentence_never_calls_the_llm(self):
        with self.env("http://127.0.0.1:9"):
            with mock.patch.object(agent, "_open", side_effect=AssertionError("네트워크를 쓰면 안 된다")):
                r = agent.plan_auto("auto", "제주시에서 버스를 가장 많이 타는 정류장과 시간대를 알고 싶어요.", {}, None, TODAY)
        self.assertEqual(r["order"]["items"], [])
        self.assertEqual(r["order"]["out_of_scope"]["topic"], "버스 이용")
        self.assertEqual(r["fallback_reason"], "")

    def test_region_the_model_could_not_place_is_warned_on_top(self):
        need = {"subject": "visit", "region": "", "who": "all", "units": ["age", "sex"]}
        bad = dict(need, region="없는마을")
        submit = {"need": need, "items": [{"dataset_no": "4-11", "why": "읍면동과 연령대를 가진 데이터다."}]}
        r, _ = self.run_with([msg([tool_use("submit_order", dict(submit, need=bad))]),
                              msg([tool_use("submit_order", dict(submit, need=bad))]),
                              msg([tool_use("submit_order", dict(submit, need=bad))])],
                             sentence="없는마을 근처에 어떤 사람이 오는지 알고 싶다")
        self.assertEqual(r["need"]["region"], "도 전체")
        w = [x for x in r["warnings"] if x["kind"] == "region"]
        self.assertTrue(w, r["warnings"])
        self.assertIn("없는마을", w[0]["text"])

    def test_env_file_in_windows_encodings(self):
        path = os.path.join(self.tmp, "win.env")
        body = "# 평가용 키\r\nANTHROPIC_API_KEY=%s\r\nMODEL=claude-test\r\n" % FAKE_KEY
        for enc in ("cp949", "utf-16", "utf-8-sig", "utf-16-le"):
            with open(path, "w", encoding=enc, newline="") as f:
                f.write(body)
            os.utime(path, (1 + len(enc), 1 + len(enc)))             # 수정 시각을 바꿔 캐시를 지나친다
            with mock.patch.object(agent, "ENV_FILE", path), \
                    mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "MODEL": "", "API_BASE": "", "EFFORT": ""}):
                cfg = agent.settings()
                self.assertEqual((cfg["key"], cfg["model"]), (FAKE_KEY, "claude-test"), enc)

    def test_unreadable_env_file_is_said_so(self):
        path = os.path.join(self.tmp, "bad.env")
        with open(path, "wb") as f:
            f.write(b"ANTHROPIC_API_KEY=abc\n# \x81\x00\xff\xfe broken \x80\x80\n")
        with mock.patch.object(agent, "ENV_FILE", path), \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "MODEL": "", "API_BASE": "", "EFFORT": ""}):
            st = agent.llm_status()
            r = agent.plan_auto("auto", S1, {}, None, TODAY)
        self.assertFalse(st["enabled"])
        self.assertIn(".env 파일을 읽지 못했다", st["reason"])
        self.assertIn("UTF-8", st["reason"])
        self.assertIn(".env 파일을 읽지 못했다", r["fallback_reason"])


if __name__ == "__main__":
    unittest.main()
