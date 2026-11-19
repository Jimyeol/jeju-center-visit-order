# -*- coding: utf-8 -*-
"""자가 점검: 프로그램이 돌 준비가 됐는지 스스로 확인한다. 화면 첫 줄과 실행 창에 나온다.

실행:  python -m app.selfcheck      (문제가 있으면 종료 코드 1)
네트워크를 쓰지 않는다. LLM 연결 확인은 서버가 뜰 때 agent.start_probe() 가 따로 한다.
"""
import os
import sys
import tempfile
import uuid

from . import agent
from . import catalog as catalog_mod
from . import config
from . import public_data as public_mod

WEB_FILES = ("index.html", "app.js", "style.css")


def _row(name, ok, detail):
    return {"name": name, "ok": bool(ok), "detail": detail}


def _check_python():
    v = sys.version_info
    return _row("파이썬 버전", v >= (3, 9), "%d.%d.%d (3.9 이상이어야 한다)" % (v[0], v[1], v[2]))


def _check_spec():
    try:
        cat = catalog_mod.get()
        c = cat.counts()
        ok = c["datasets"] > 0 and c["columns"] > 0
        return _row("데이터 명세서", ok, "%s, 컬럼 %d개" % (cat.label(), c["columns"]))
    except Exception as e:
        return _row("데이터 명세서", False, "data/spec/spec.json 을 읽지 못했다(%s). python tools/build_spec.py 로 다시 만든다." % type(e).__name__)


def _check_public(key, title):
    try:
        st = public_mod.get().status().get(key) or {}
    except Exception as e:
        return _row(title, False, "읽지 못했다(%s)" % type(e).__name__)
    rows = st.get("rows") or 0
    if not rows:
        return _row(title, False, "data/public/ 에서 파일을 읽지 못했다%s" % ((": " + str(st["error"])) if st.get("error") else ""))
    detail = "%s행, %s ~ %s" % (format(rows, ","), st.get("first"), st.get("last"))
    if st.get("placeholder"):
        detail += " (대체 데이터 사용 중)"
    return _row(title, True, detail)


def _check_web():
    web = os.path.join(config.ROOT, "web")
    missing = [f for f in WEB_FILES if not os.path.isfile(os.path.join(web, f))]
    return _row("화면 파일", not missing, "web/ 의 %d개 파일" % len(WEB_FILES) if not missing else "없는 파일: " + ", ".join(missing))


def _check_sample():
    try:
        files = [f for f in os.listdir(config.SAMPLE_DIR) if f.lower().endswith(".csv")]
    except OSError:
        files = []
    if files:
        return _row("가상 센터 CSV", True, "data/sample_center/ 에 %d개(명세서대로 만든 시연용)" % len(files))
    return _row("가상 센터 CSV", False, "data/sample_center/ 에 CSV 가 없다. python tools/make_sample_center.py 로 다시 만든다.")


def _writable(folder):
    """폴더를 만들고 파일을 써 볼 수 있는가(시험 파일과 새로 만든 빈 폴더는 지운다)."""
    probe = os.path.join(folder, ".write_test_" + uuid.uuid4().hex[:8])
    created = not os.path.isdir(folder)
    try:
        os.makedirs(folder, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        return False, type(e).__name__
    if created:
        # 점검만 하고 끝나는 경우 빈 폴더를 남기지 않는다(쓸 때 다시 만든다).
        try:
            os.rmdir(folder)
        except OSError:
            pass
    return True, ""


def _check_runtime(runtime_dir):
    d = runtime_dir or os.path.join(config.ROOT, "runtime")
    ok, why = _writable(d)
    if ok:
        return _row("임시 폴더", True, "runtime/ 에 쓸 수 있다(올린 파일·결과·기록은 여기에만 둔다)")
    # 프로그램 폴더에 쓸 수 없으면 서버는 사용자 임시 폴더로 넘긴다(server.Store 와 같은 경로).
    fb = os.path.join(tempfile.gettempdir(), "center-order-runtime")
    ok2, _ = _writable(fb)
    if ok2:
        return _row("임시 폴더", True, "프로그램 폴더의 runtime/ 에 쓰지 못해(%s) 사용자 임시 폴더를 쓴다: %s (결과 삭제 버튼이 이 폴더를 비운다)" % (why, fb))
    return _row("임시 폴더", False, "runtime/ 에도 사용자 임시 폴더에도 쓰지 못한다(%s). 프로그램 폴더를 C:\\center-order 처럼 바탕 화면·문서 폴더가 아닌 곳으로 옮긴다." % why)


def _check_prompts():
    try:
        tools = agent.tool_defs()
        names = [t.get("name") for t in tools]
        ok = bool(agent.system_prompt()) and sorted(names) == sorted(agent.TOOL_NAMES)
        return _row("프롬프트와 도구 정의", ok, "app/prompts/ 의 시스템 프롬프트와 도구 %d개" % len(names))
    except Exception as e:
        return _row("프롬프트와 도구 정의", False, "app/prompts/ 를 읽지 못했다(%s)" % type(e).__name__)


def _check_llm():
    st = agent.llm_status()
    if st["enabled"]:
        detail = "LLM 모드(%s)" % st["model"]
        if st.get("checked") == "ok":
            detail += ", 연결 확인됨"
        elif st.get("reason"):
            detail += ". " + st["reason"]
        return _row("실행 모드", True, detail)
    if st.get("checked") == "failed":
        # Key 는 있는데 쓸 수 없는 상태다. 규칙 모드로는 돌지만 확인이 필요하다고 알린다.
        return _row("실행 모드", False, "규칙 모드로 실행한다. LLM 을 쓰지 못하는 이유: " + st["reason"])
    return _row("실행 모드", True, "규칙 모드(Key 없음). 문장 입력과 '직접 고르기'로 끝까지 쓸 수 있다.")


def run(runtime_dir=None):
    """[{"name", "ok", "detail"}]"""
    return [
        _check_python(),
        _check_spec(),
        _check_public("d1048", "공개 데이터 허브 1048(카드)"),
        _check_public("d520", "공개 데이터 허브 520(유동인구)"),
        _check_web(),
        _check_sample(),
        _check_prompts(),
        _check_runtime(runtime_dir),
        _check_llm(),
    ]


def format_lines(checks):
    return ["[%s] %s - %s" % ("통과" if c["ok"] else "확인 필요", c["name"], c["detail"]) for c in checks]


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    checks = run()
    for line in format_lines(checks):
        print(line)
    bad = [c for c in checks if not c["ok"]]
    print("자가 점검 %d/%d 통과" % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
