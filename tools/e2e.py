# -*- coding: utf-8 -*-
"""처음부터 끝까지 재현: 서버를 띄워 시나리오 3개 → 가상 CSV 재분석 → 이상한 파일 → 삭제까지 돌리고 기대값을 확인한다.

    python tools/e2e.py            Key 없이(규칙 모드). 기본.
    python tools/e2e.py --mock     모의 응답 서버를 물려 LLM 루프까지(실제 API 를 부르지 않는다).
    python tools/e2e.py --live     실제 Claude API 로 시나리오 3개(ANTHROPIC_API_KEY 가 있어야 한다. 비용이 든다).

빈 포트에 서버를 띄우고, 임시 폴더를 runtime 으로 쓰며, 끝나면 서버를 내리고 임시 폴더를 지운다.
모두 통과하면 종료 코드 0, 하나라도 어긋나면 1.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

S1 = "노형동에 저녁 장사 위주로 한식당을 열려고 합니다. 어느 시간대에 어떤 손님이 많은지 알고 싶어요."
S2 = "토요일 점심에 애월읍 카페에 20대가 얼마나 오는지, 요일과 시간대를 함께 보고 싶습니다."
S3 = "성산읍에 오는 외국인 관광객을 연령대별, 시간대별로 알고 싶습니다."


class Checks(object):
    def __init__(self):
        self.rows = []

    def ok(self, cond, name, detail=""):
        self.rows.append((bool(cond), name))
        print("[%s] %s%s" % ("통과" if cond else "실패", name, (" - " + str(detail)) if detail and not cond else ""), flush=True)
        return bool(cond)

    def note(self, text):
        print("       " + text, flush=True)

    @property
    def failed(self):
        return [n for c, n in self.rows if not c]


class Server(object):
    """app.server 를 별도 프로세스로 띄운다."""

    def __init__(self, env_extra=None):
        self.runtime = tempfile.mkdtemp(prefix="center_order_e2e_")
        env = dict(os.environ)
        env.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "NO_BROWSER": "1"})
        env.pop("PORT", None)
        env.update(env_extra or {})
        self.lines = []
        self.url = None
        self.proc = subprocess.Popen([sys.executable, "-m", "app.server", "--no-browser", "--runtime", self.runtime],
                                     cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self._found = threading.Event()
        t = threading.Thread(target=self._read)
        t.daemon = True
        t.start()

    def _read(self):
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            self.lines.append(line)
            m = re.search(r"http://127\.0\.0\.1:(\d+)/", line)
            if m and not self.url:
                self.url = "http://127.0.0.1:%s" % m.group(1)
                self._found.set()
        self._found.set()

    def wait(self, timeout=90):
        self._found.wait(timeout)
        if not self.url:
            raise RuntimeError("서버가 뜨지 않았다:\n" + "\n".join(self.lines[-30:]))
        # 주소가 찍힌 뒤 실제로 받기 시작할 때까지 잠깐 기다린다.
        for _ in range(100):
            try:
                self.call("GET", "/api/options")
                return self.url
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        raise RuntimeError("서버가 응답하지 않는다")

    def call(self, method, path, body=None, headers=None, timeout=600):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers = dict({"Content-Type": "application/json; charset=utf-8"}, **(headers or {}))
        req = urllib.request.Request(self.url + path, data=body, headers=headers or {}, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            resp = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            resp = e
        try:
            raw = resp.read()
            status = resp.getcode()
            ctype = resp.headers.get("Content-Type", "")
        finally:
            resp.close()
        if ctype.startswith("application/json"):
            return status, json.loads(raw.decode("utf-8"))
        return status, raw

    def upload(self, data, name, run_id=None):
        q = ("?run_id=" + run_id) if run_id else ""
        return self.call("POST", "/api/center-csv" + q, data,
                         {"Content-Type": "application/octet-stream", "X-Filename": urllib.parse.quote(name)})

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)
        try:
            self.proc.stdout.close()
        except OSError:
            pass
        shutil.rmtree(self.runtime, ignore_errors=True)
        return self.proc.returncode


def has_korean(s):
    return any("가" <= ch <= "힣" for ch in str(s or ""))


def check_s1(c, r, label):
    items = r["order"]["items"]
    it = items[0] if items else {}
    conds = dict((x["column"], x["value"]) for x in it.get("conditions", []))
    c.ok(it.get("dataset_no") == "1-14", "%s: 주문서에 1-14 가 나온다" % label, [i.get("dataset_no") for i in items])
    c.ok(conds.get("bjd") == "노형동" and conds.get("biz_cd") == "I56111", "%s: 조건 bjd=노형동, 한식 음식점업 코드 I56111" % label, conds)
    months = [m["ym"] for m in it.get("months", [])]
    c.ok(len(months) == 3 and all(m >= "2023-08" for m in months), "%s: 받을 달 3개가 모두 2023-08 이후" % label, months)
    c.ok(any("1-13" in s["args_summary"] and "v01" in s["args_summary"] and "버림" in s["result_summary"] and s["note"]
             for s in r["trace"]), "%s: v01(1-13)을 버린 이유가 과정에 남는다" % label)
    c.ok(all(x["evidence"].get("sheet") and x["evidence"].get("col_no") for x in it.get("conditions", [])) and it.get("conditions"),
         "%s: 모든 조건 줄에 명세서 시트·컬럼 근거가 붙는다" % label)
    v = r["validation"]
    c.ok(v["total"] > 0 and v["passed"] == v["total"], "%s: 검증 통과 %s/%s" % (label, v["passed"], v["total"]), v["issues"])


def check_s2(c, r, label):
    alts = dict((a["key"], a) for a in r["order"]["alternatives"])
    c.ok(sorted(alts) == ["single", "split"], "%s: 두 안을 낸다" % label, list(alts))
    split = sorted(i["dataset_no"] for i in alts.get("split", {}).get("items", []))
    single = [i["dataset_no"] for i in alts.get("single", {}).get("items", [])]
    c.ok(split == ["1-12", "1-14"] and single == ["1-22"], "%s: 카드 1-12 + 1-14 따로 / 포스 1-22 한 종" % label, (split, single))
    c.ok(all(a.get("downloads", 0) > 0 and a.get("gain") and a.get("give_up") for a in alts.values()) and bool(alts),
         "%s: 안마다 내려받기 횟수·얻는 것·포기하는 것이 있다" % label)
    text = json.dumps(r["trace"], ensure_ascii=False) + json.dumps([i.get("why") for i in r["order"]["items"]], ensure_ascii=False)
    c.ok("한 데이터로" in text, "%s: 한 데이터로 다 채울 수 없다고 밝힌다" % label)
    chosen = sorted(i["dataset_no"] for i in r["order"]["items"])
    c.ok(chosen in (["1-22"], ["1-12", "1-14"]), "%s: 먼저 권하는 안이 두 안 가운데 하나다" % label, chosen)
    v = r["validation"]
    c.ok(v["total"] > 0 and v["passed"] == v["total"], "%s: 검증 통과 %s/%s" % (label, v["passed"], v["total"]), v["issues"])


def check_s3(c, r, label):
    pa = r["public_answer"]
    c.ok(pa["available"] is False and has_korean(pa["reason"]), "%s: 공개 데이터로 답할 수 없다고 밝힌다" % label, pa.get("reason"))
    items = r["order"]["items"]
    c.ok(bool(items) and items[0]["dataset_no"] == "4-15", "%s: 4-15 를 짝짓는다" % label, [i.get("dataset_no") for i in items])
    c.ok([n.get("key") for n in r["order"]["no_data"]] == ["age"], "%s: 연령대는 '메울 데이터 없음'" % label, r["order"]["no_data"])
    c.ok(any(g["key"] == "age" and g["status"] == "메울 데이터 없음" for g in r["gaps"]), "%s: 빈칸 표에도 연령대가 '메울 데이터 없음'" % label)
    v = r["validation"]
    c.ok(v["total"] > 0 and v["passed"] == v["total"], "%s: 검증 통과 %s/%s" % (label, v["passed"], v["total"]), v["issues"])


def sample_file(dataset_no):
    with open(os.path.join(ROOT, "data", "sample_center", "manifest.json"), "r", encoding="utf-8") as f:
        man = json.load(f)
    for row in man["files"]:
        if row["dataset_no"] == dataset_no:
            return os.path.join(ROOT, "data", "sample_center", row["file"])
    raise RuntimeError("가상 CSV 가 없다: " + dataset_no)


def run_rule(c, srv):
    status, st = srv.call("GET", "/api/status")
    c.ok(status == 200 and st["spec"]["datasets"] == 71 and st["spec"]["ref_tables"] == 2 and st["spec"]["columns"] > 0,
         "상태: 명세서 데이터 71종, 참고표 2종, 컬럼 %s개" % st["spec"].get("columns"))
    c.ok(st["public"]["d1048"]["rows"] > 0 and st["public"]["d520"]["rows"] > 0,
         "상태: 공개 데이터 1048 %s행(%s~%s), 520 %s행(%s~%s)" % (
             st["public"]["d1048"]["rows"], st["public"]["d1048"]["first"], st["public"]["d1048"]["last"],
             st["public"]["d520"]["rows"], st["public"]["d520"]["first"], st["public"]["d520"]["last"]))
    c.ok(isinstance(st["selfcheck"], list) and st["selfcheck"] and all(x["ok"] for x in st["selfcheck"]),
         "상태: 자가 점검 %d개 모두 통과" % len(st["selfcheck"]), [x for x in st["selfcheck"] if not x["ok"]])
    c.ok(set(["enabled", "model", "reason"]) <= set(st["llm"]), "상태: LLM 모드 표시(%s)" % ("LLM 모드" if st["llm"]["enabled"] else "규칙 모드"))
    status, page = srv.call("GET", "/")
    c.ok(status == 200 and b"<html" in page.lower(), "화면: 첫 화면이 뜬다")
    status, opt = srv.call("GET", "/api/options")
    c.ok(status == 200 and len(opt["examples"]) == 3 and opt["regions"]["bjd"], "선택지: 예시 3개와 지역 목록")
    ex = opt["examples"]

    # 시나리오 3개: 세 칸 선택과 문장, 둘 다 규칙 모드
    runs = {}
    for i, (sentence, check) in enumerate(((S1, check_s1), (S2, check_s2), (S3, check_s3))):
        for kind, body in (("세 칸", {"mode": "rule", "text": "", "slots": ex[i]["slots"]}),
                           ("문장", {"mode": "rule", "text": sentence, "slots": {}})):
            status, r = srv.call("POST", "/api/plan", body)
            label = "시나리오 %d(%s)" % (i + 1, kind)
            if not c.ok(status == 200 and r.get("mode_used") == "rule", "%s: 규칙 모드로 끝까지 돈다" % label, r if status != 200 else ""):
                continue
            check(c, r, label)
            runs[(i, kind)] = r
    if not st["llm"]["enabled"]:
        status, r = srv.call("POST", "/api/plan", {"mode": "auto", "text": S1, "slots": {}})
        c.ok(status == 200 and r["mode_used"] == "rule" and has_korean(r["fallback_reason"]),
             "Key 없이 문장을 넣으면 규칙 모드로 넘기고 이유를 적는다")
    else:
        c.note("이 PC 에는 Key 가 있어 'Key 없음' 넘김 확인은 건너뛰었다(시나리오는 mode=rule 로 돌렸다).")

    # 시나리오 1에 이어 1-14 가상 CSV 재분석
    run1 = runs.get((0, "세 칸"))
    if run1:
        with open(sample_file("1-14"), "rb") as f:
            data = f.read()
        t0 = time.time()
        status, r = srv.upload(data, os.path.basename(sample_file("1-14")), run1["run_id"])
        if c.ok(status == 200, "재분석: 1-14 가상 CSV 를 받는다(%.1f초)" % (time.time() - t0), r):
            c.ok(r["recognized"]["dataset_no"] == "1-14", "재분석: 헤더로 1-14 를 알아본다", r["recognized"])
            slot = [t for t in r["tables"] if "시간대" in t["title"] and t["rows"]]
            c.ok(bool(slot), "재분석: 시간대 표가 채워진다(표 %d개)" % len(r["tables"]))
            cells = [x for t in r["tables"] for row in t["rows"] for x in row]
            c.ok(r["masked_total"] > 0 and "10 미만" in cells, "재분석: 10 미만 칸이 가려진다(가린 칸 %s개)" % r["masked_total"])
            rc = r["reconcile"]
            c.ok(rc["available"] and rc["diff_pct"] is not None, "재분석: 대조 줄이 나온다(차이 %s%%)" % rc.get("diff_pct"), rc)
            c.ok(r["sent_to_llm"] == 0, "재분석: LLM 으로 보낸 값 0")
            c.ok(r["virtual"] is True, "재분석: 가상 데이터로 표시된다")

    # 운영진이 공개한 실제 CSV 형식 한 줄(1-12, 파티션 컬럼 없이 13열)
    header = "base_year_month,dstrct_se,bjd,biz_cd,biz_nm,user_type,tour_type,week,age,sex,user_cnt,use_cnt,use_amt"
    row = '202607,"서귀포시 서부","대정읍","I56111","한식 음식점업","내국인","단체","공휴일","60","여성","4","1","1000000"'
    status, r = srv.upload((header + "\n" + row + "\n").encode("utf-8"), "Results_20260801_090000.csv")
    c.ok(status == 200 and r["recognized"]["dataset_no"] == "1-12" and r["rows"] == 1, "공개된 실제 CSV 형식 한 줄을 1-12 로 읽는다", r)

    # 이상한 파일 3종
    for name, data in (("빈 파일.csv", b""),
                       ("CSV 가 아닌 파일.png", b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8),
                       ("컬럼이 다른 파일.csv", "이름,나이,주소\n홍길동,30,제주\n".encode("utf-8"))):
        status, r = srv.upload(data, name)
        err = r.get("error") if isinstance(r, dict) else None
        c.ok(status == 400 and err and has_korean(err.get("message")) and has_korean(err.get("action")),
             "이상한 파일(%s): 죽지 않고 원인과 조치를 보여 준다" % name, r)
        if err:
            c.note("원인: %s / 조치: %s" % (err.get("message"), err.get("action")))
    status, _ = srv.call("GET", "/api/status")
    c.ok(status == 200, "이상한 파일을 넣은 뒤에도 서버가 살아 있다")

    # 삭제
    def count(sub):
        d = os.path.join(srv.runtime, sub)
        return len(os.listdir(d)) if os.path.isdir(d) else 0

    before = (count("results"), count("uploads"), count("logs"))
    status, r = srv.call("POST", "/api/clear", {})
    d = r.get("deleted", {}) if isinstance(r, dict) else {}
    c.ok(status == 200 and d.get("results", 0) >= 7 and d.get("files", 0) >= 2 and d.get("logs", 0) >= 10,
         "삭제: 지운 개수를 돌려준다(결과 %s, 파일 %s, 기록 %s)" % (d.get("results"), d.get("files"), d.get("logs")), (r, before))
    c.ok((count("results"), count("uploads"), count("logs")) == (0, 0, 0), "삭제: runtime 의 결과·올린 파일·기록이 비었다")
    status, r = srv.call("POST", "/api/clear", {})
    c.ok(r.get("deleted") == {"results": 0, "files": 0, "logs": 0}, "삭제: 한 번 더 누르면 0건")


def run_llm(c, srv, label, sentences):
    status, st = srv.call("GET", "/api/status")
    c.ok(status == 200 and st["llm"]["enabled"], "%s: 상태가 LLM 모드(%s)" % (label, st["llm"].get("model")), st["llm"].get("reason"))
    for i, (sentence, check) in enumerate(sentences):
        t0 = time.time()
        status, r = srv.call("POST", "/api/plan", {"mode": "auto", "text": sentence, "slots": {}})
        name = "%s 시나리오 %d" % (label, i + 1)
        if not c.ok(status == 200 and r.get("mode_used") == "llm", "%s: LLM 모드로 끝까지 돈다(%.1f초)" % (name, time.time() - t0),
                    r.get("fallback_reason") if isinstance(r, dict) else r):
            continue
        check(c, r, name)
        s = r["sent_to_llm"]
        c.ok(s["purpose_text"] is True and s["center_values"] == 0 and s["public_aggregates"] >= 0,
             "%s: 보낸 내용 집계(공개 집계 %s, 명세 항목 %s, 센터 값 0)" % (name, s["public_aggregates"], s["spec_items"]))
        usage = r.get("llm") or {}
        c.note("도구 호출 순서: %s" % " → ".join(x["tool"] for x in r["trace"] if x["result_summary"] != "버림" and x["tool"] in (
            "public_scope", "public_monthly", "catalog_search", "pick_months", "submit_order")))
        c.note("호출 %s턴, 입력 %s토큰, 출력 %s토큰" % (usage.get("turns"), usage.get("input_tokens"), usage.get("output_tokens")))
        for it in r["order"]["items"]:
            c.note("%s 를 고른 이유(%s): %s" % (it["dataset_no"], it.get("why_source"), it.get("why")))
    srv.call("POST", "/api/clear", {})


def main(argv=None):
    ap = argparse.ArgumentParser(description="센터 방문 주문서 - 처음부터 끝까지 재현")
    ap.add_argument("--mock", action="store_true", help="모의 응답 서버로 LLM 루프까지 돌린다")
    ap.add_argument("--live", action="store_true", help="실제 Claude API 로 시나리오 3개를 돌린다(Key 필요)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    c = Checks()
    started = time.time()

    print("== 규칙 모드(Key 없이) ==", flush=True)
    srv = Server({"ANTHROPIC_API_KEY": "", "API_BASE": "", "MODEL": ""})
    try:
        srv.wait()
        run_rule(c, srv)
    finally:
        code = srv.stop()
    c.ok(not os.path.exists(srv.runtime), "서버를 내리고 임시 폴더를 지웠다(종료 코드 %s)" % code)

    if args.mock:
        print("== 모의 응답 서버로 LLM 루프 ==", flush=True)
        from tests import mock_llm
        mock = mock_llm.MockLLM(mock_llm.script_s1())
        base = mock.start()
        srv = Server({"ANTHROPIC_API_KEY": mock_llm.FAKE_KEY, "API_BASE": base, "MODEL": ""})
        try:
            srv.wait()
            run_llm(c, srv, "모의", [(S1, check_s1)])
            c.ok(len(mock.requests) == 5, "모의: 검증기에 한 번 걸린 뒤 고쳐서 통과(요청 %d번)" % len(mock.requests))
        finally:
            srv.stop()
            mock.stop()

    if args.live:
        print("== 실제 Claude API ==", flush=True)
        from app import agent
        if not agent.settings()["key"]:
            c.ok(False, "실호출: ANTHROPIC_API_KEY 가 없어 돌리지 못했다")
        else:
            srv = Server({})
            try:
                srv.wait()
                run_llm(c, srv, "실호출", [(S1, check_s1), (S2, check_s2), (S3, check_s3)])
            finally:
                srv.stop()

    total, failed = len(c.rows), c.failed
    print("")
    print("e2e %d/%d 통과 (%.1f초)" % (total - len(failed), total, time.time() - started), flush=True)
    for name in failed:
        print("  실패: " + name, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
