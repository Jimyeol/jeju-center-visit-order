# -*- coding: utf-8 -*-
"""모의 Claude API 서버(시험용). 정해 둔 응답을 차례로 돌려주고, 받은 요청을 적어 둔다.

실제 API 를 흉내 내는 범위: POST /v1/messages, GET /v1/models/{id}. 127.0.0.1 의 빈 포트에만 뜬다.
Key 가 없는 환경에서 루프·검증기 되돌림·대체 경로를 시험하려고 쓴다. 실호출을 대신하지는 못한다.

    mock = MockLLM([msg([tool_use("public_scope", {})]), ...])
    base = mock.start()          # "http://127.0.0.1:포트"
    ...                          # API_BASE=base 로 agent 를 돌린다
    mock.stop()
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FAKE_KEY = "mock-key-not-real"

_counter = {"n": 0}
_lock = threading.Lock()


def _next_id(prefix):
    with _lock:
        _counter["n"] += 1
        return "%s_mock%04d" % (prefix, _counter["n"])


def text(t):
    return {"type": "text", "text": t}


def thinking(signature="sig-mock"):
    """실제 응답처럼 글이 빈 thinking 블록. 다음 요청에 그대로 돌아오는지 보려고 넣는다."""
    return {"type": "thinking", "thinking": "", "signature": signature}


def tool_use(name, tool_input, tool_id=None):
    return {"type": "tool_use", "id": tool_id or _next_id("toolu"), "name": name, "input": tool_input}


def msg(content, stop_reason=None, model="claude-haiku-5-5"):
    """성공 응답 한 번."""
    if stop_reason is None:
        stop_reason = "tool_use" if any(b.get("type") == "tool_use" for b in content) else "end_turn"
    return {"status": 200, "body": {
        "id": _next_id("msg"), "type": "message", "role": "assistant", "model": model,
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 80, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0}}}


def error(status, etype, message="mock error", headers=None):
    return {"status": status, "headers": headers or {},
            "body": {"type": "error", "error": {"type": etype, "message": message}, "request_id": "req_mock"}}


class MockLLM(object):
    def __init__(self, script=None, models_status=200):
        """script: 응답 목록, 또는 (요청 본문, 몇 번째 요청인지) → 응답을 돌려주는 함수.

        응답 한 개: {"status", "body", "headers"(선택), "sleep"(선택, 초), "drop"(선택, 응답 없이 끊기)}
        """
        self.script = script if script is not None else []
        self.models_status = models_status
        self.requests = []          # POST /v1/messages 로 받은 것: {"headers", "body"}
        self.model_requests = []    # GET /v1/models/… 로 받은 것
        self._i = 0
        self._lock = threading.Lock()
        self._httpd = None
        self._thread = None

    def _next(self, body):
        with self._lock:
            i = self._i
            self._i += 1
        if callable(self.script):
            return self.script(body, i)
        if i < len(self.script):
            return self.script[i]
        return error(500, "api_error", "mock script exhausted")

    def start(self):
        mock = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, fmt, *args):
                pass

            def _reply(self, step):
                if step.get("sleep"):
                    time.sleep(step["sleep"])
                if step.get("drop"):
                    self.connection.close()
                    return
                raw = step.get("raw")
                data = raw if raw is not None else json.dumps(step.get("body", {}), ensure_ascii=False).encode("utf-8")
                try:
                    self.send_response(step.get("status", 200))
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    for k, v in (step.get("headers") or {}).items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                if self.path.startswith("/v1/models/"):
                    mock.model_requests.append({"path": self.path, "headers": dict(self.headers.items())})
                    if mock.models_status == 200:
                        return self._reply({"status": 200, "body": {"type": "model", "id": self.path.rsplit("/", 1)[-1]}})
                    etype = {401: "authentication_error", 404: "not_found_error"}.get(mock.models_status, "api_error")
                    return self._reply(error(mock.models_status, etype))
                self._reply(error(404, "not_found_error"))

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw.decode("utf-8"))
                except ValueError:
                    body = None
                if self.path != "/v1/messages":
                    return self._reply(error(404, "not_found_error"))
                mock.requests.append({"headers": dict((k.lower(), v) for k, v in self.headers.items()), "body": body})
                self._reply(mock._next(body))

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, name="mock-llm")
        self._thread.daemon = True
        self._thread.start()
        return "http://127.0.0.1:%d" % self._httpd.server_address[1]

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._thread.join(5)
            self._httpd = None


# ---------------------------------------------------------------------- 시나리오 1의 정해진 대화

S1_NEED = {"subject": "spend", "region": "노형동", "biz": "한식 음식점업", "who": "all",
           "units": ["slot", "user_type", "age", "sex"], "focus": {"slot": ["저녁"]}}


def script_s1(with_bad_draft=True):
    """시나리오 1(노형동 한식당): 도구 다섯 개를 차례로 부르고, 한 번은 틀린 초안을 내 검증기에 걸리게 한다."""
    steps = [
        msg([thinking("sig-1"), text("공개 데이터가 가진 단위부터 확인한다."),
             tool_use("public_scope", {}),
             tool_use("public_monthly", {"source": "1048", "biz": "한식 음식점업"})]),
        msg([tool_use("catalog_search", {"units": ["slot", "user_type", "age", "sex"], "subject": "spend",
                                         "region": "노형동", "biz": "한식 음식점업", "who": "all",
                                         "focus": {"slot": ["저녁"]}})]),
        msg([text("법정동과 시간대를 함께 가진 1-14 로 받을 달을 고른다."),
             tool_use("pick_months", {"dataset_no": "1-14", "subject": "spend", "biz": "한식 음식점업"})]),
    ]
    if with_bad_draft:
        # 명세서에 없는 조건(1-13 에는 법정동 컬럼이 없다), 수록 범위 밖의 달, 없는 컬럼, 파티션 컬럼
        steps.append(msg([tool_use("submit_order", {"need": S1_NEED, "items": [{
            "dataset_no": "1-13", "months": ["2016-05"],
            "conditions": [{"column": "bjd", "value": "노형동"}],
            "use_columns": ["time", "agee", "pt_year"],
            "why": "시간대가 있는 데이터다. 금액은 대략 3,500만원이다."}]})]))
    steps.append(msg([text("검증기가 돌려준 줄을 고친다."), tool_use("submit_order", {"need": S1_NEED, "items": [{
        "dataset_no": "1-14", "months": ["2024-08", "2025-02", "2025-04"],
        "why": "법정동과 시간대, 이용자구분을 한 파일에 가진 데이터다. 같은 이름의 v01 인 1-13 은 법정동 컬럼이 없어 버렸다. 매출은 약 12억 원으로 보인다."}]})]))
    return steps
