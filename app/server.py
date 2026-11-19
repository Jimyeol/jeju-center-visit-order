# -*- coding: utf-8 -*-
"""로컬 서버: 화면(web/)과 API 5개를 127.0.0.1 에서만 내준다. 표준 라이브러리만 쓴다.

실행:  python -m app.server [--port N] [--no-browser] [--runtime 폴더]
    - 포트를 주지 않으면 비어 있는 포트를 운영체제가 골라 준다.
    - 올린 파일·결과·기록은 프로그램 폴더 아래 runtime/ 에만 쓴다. /api/clear 가 모두 지운다.
    - 센터 CSV 재분석(/api/center-csv)은 LLM 을 부르지 않는다(agent 모듈을 거치지 않는다).
"""
import os
import sys

if __name__ == "__main__" and not __package__:
    # python app/server.py 로 실행해도 돌게 한다.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.server import main as _main
    sys.exit(_main())

import argparse
import collections
import datetime
import json
import posixpath
import re
import shutil
import socketserver
import tempfile
import threading
import traceback
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import agent, local_api
from . import catalog as catalog_mod
from . import center_csv
from . import config
from . import planner
from . import public_data as public_mod
from . import selfcheck

WEB_DIR = os.path.join(config.ROOT, "web")
DEFAULT_RUNTIME_DIR = os.path.join(config.ROOT, "runtime")
# 프로그램 폴더에 쓸 수 없을 때(읽기 전용 위치, 보안 프로그램의 폴더 보호) 대신 쓰는 곳.
FALLBACK_RUNTIME_DIR = os.path.join(tempfile.gettempdir(), "center-order-runtime")

MAX_JSON_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
CHUNK = 64 * 1024
MAX_RUNS_IN_MEMORY = 200

RUN_ID_RE = re.compile(r"^[0-9a-f]{6,32}$")
DATASET_NO_RE = re.compile(r"^\d{1,2}-\d{2}$")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

# 화면이 밖의 자원을 부르지 못하게 한다(외부 CDN·폰트·이미지 0개).
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class ApiError(Exception):
    def __init__(self, status, message, action=""):
        Exception.__init__(self, message)
        self.status = status
        self.message = message
        self.action = action


# ====================================================================== runtime/ 관리

class Store(object):
    """runtime/ 아래의 올린 파일(uploads), 결과(results), 기록(logs)과 run_id → 목적(need) 기억."""

    def __init__(self, runtime_dir=None):
        self.lock = threading.Lock()
        self.runs = collections.OrderedDict()
        self.wanted_dir = os.path.abspath(runtime_dir or DEFAULT_RUNTIME_DIR)
        self.fallback = False
        self._set_dir(self.wanted_dir)
        # 시작할 때 쓰기 시험을 한다. 안 되면 사용자 임시 폴더로 넘긴다(자가 점검 줄에 실제 경로를 적는다).
        if not writable(self.wanted_dir) and writable(FALLBACK_RUNTIME_DIR):
            self._set_dir(FALLBACK_RUNTIME_DIR)
            self.fallback = True

    def _set_dir(self, d):
        self.dir = d
        self.uploads = os.path.join(d, "uploads")
        self.results = os.path.join(d, "results")
        self.logs = os.path.join(d, "logs")
        self.log_file = os.path.join(self.logs, "activity.jsonl")

    def ensure(self):
        for d in (self.uploads, self.results, self.logs):
            os.makedirs(d, exist_ok=True)

    def leftover(self):
        """지난 이용에서 남은 것: {"files": 올린 파일 수, "results": 결과 파일 수}. 이번 실행에서 만든 것도 센다."""
        out = {}
        for key, folder in (("files", self.uploads), ("results", self.results)):
            try:
                out[key] = len([n for n in os.listdir(folder) if not n.startswith(".")])
            except OSError:
                out[key] = 0
        return out

    def _write_json(self, path, obj):
        try:
            self.ensure()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)
            return True
        except OSError:
            return False

    def save_plan(self, resp):
        run_id = resp.get("run_id")
        with self.lock:
            self.runs[run_id] = resp.get("need")
            while len(self.runs) > MAX_RUNS_IN_MEMORY:
                self.runs.popitem(last=False)
        self._write_json(os.path.join(self.results, "plan_%s.json" % run_id), resp)

    def need_for(self, run_id):
        if not run_id:
            return None
        with self.lock:
            if run_id in self.runs:
                return self.runs[run_id]
        try:
            with open(os.path.join(self.results, "plan_%s.json" % run_id), "r", encoding="utf-8") as f:
                return json.load(f).get("need")
        except (OSError, ValueError):
            return None

    def new_upload_path(self, filename):
        self.ensure()
        return os.path.join(self.uploads, "%s_%s" % (uuid.uuid4().hex[:8], filename))

    def save_csv_result(self, run_id, result):
        name = "csv_%s_%s.json" % (run_id or "norun", uuid.uuid4().hex[:8])
        self._write_json(os.path.join(self.results, name), result)

    def log(self, **event):
        event["t"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.ensure()
            with self.lock:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass

    @staticmethod
    def _wipe(folder):
        """폴더 안의 파일을 지우고 지운 개수를 돌려준다."""
        n = 0
        try:
            names = os.listdir(folder)
        except OSError:
            return 0
        for name in names:
            p = os.path.join(folder, name)
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
                n += 1
            except OSError:
                pass
        return n

    def clear(self):
        """결과·올린 파일·기록을 지운다. {"results", "files", "logs"} 는 지운 개수(기록은 줄 수)."""
        with self.lock:
            log_lines = 0
            try:
                with open(self.log_file, "r", encoding="utf-8") as f:
                    log_lines = sum(1 for line in f if line.strip())
            except OSError:
                pass
            in_memory = len(self.runs)
            self.runs.clear()
            n_results = self._wipe(self.results)
            n_files = self._wipe(self.uploads)
            self._wipe(self.logs)
        return {"results": max(n_results, in_memory), "files": n_files, "logs": log_lines}


def writable(folder):
    """폴더를 만들고 파일을 써 볼 수 있는가(시험 파일은 지운다. 새로 만든 빈 폴더도 지운다)."""
    created = not os.path.isdir(folder)
    probe = os.path.join(folder, ".write_test_" + uuid.uuid4().hex[:8])
    try:
        os.makedirs(folder, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
    except OSError:
        return False
    if created:
        try:
            os.rmdir(folder)
        except OSError:
            pass
    return True


def safe_filename(raw):
    """X-Filename 헤더 → 폴더를 벗어나지 못하는 파일 이름."""
    try:
        name = urllib.parse.unquote(raw or "", errors="replace")
    except Exception:
        name = ""
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*').strip().strip(".")
    if not name:
        name = "upload.csv"
    if len(name) > 120:
        root, ext = os.path.splitext(name)
        name = root[:120 - len(ext)] + ext[:20]
    return name


# ====================================================================== 요청 처리

class Handler(BaseHTTPRequestHandler):
    server_version = "CenterOrder/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    # ------------------------------------------------------------ 공통
    def log_request(self, code="-", size="-"):
        # 요청마다 실행 창에 쓰면, 옛 콘솔에서 창을 마우스로 끌어 '선택' 상태가 됐을 때 응답이 멈춘다.
        # 그래서 --verbose 일 때만 모든 요청을 찍고, 평소에는 오류(4xx·5xx)만 찍는다.
        try:
            is_error = int(getattr(code, "value", code)) >= 400
        except (TypeError, ValueError):
            is_error = True
        if is_error or getattr(self.server, "verbose", False):
            BaseHTTPRequestHandler.log_request(self, code, size)

    def log_message(self, fmt, *args):
        if getattr(self.server, "quiet", False):
            return
        try:
            sys.stderr.write("[%s] %s\n" % (datetime.datetime.now().strftime("%H:%M:%S"), fmt % args))
        except Exception:
            pass

    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def send_api_error(self, status, message, action="", extra=None):
        body = {"error": {"message": message, "action": action}}
        if extra:
            body.update(extra)
        self.send_json(status, body)

    def _port(self):
        return self.server.server_address[1]

    def _check_origin(self):
        """다른 사이트가 이 PC 의 프로그램을 몰래 부르지 못하게 Host 와 Origin 을 확인한다."""
        port = self._port()
        hosts = ("127.0.0.1:%d" % port, "localhost:%d" % port, "[::1]:%d" % port)
        host = self.headers.get("Host")
        if host is not None and host.strip().lower() not in hosts:
            raise ApiError(403, "이 주소로는 프로그램을 쓸 수 없습니다.", "실행 창에 표시된 주소(http://127.0.0.1:%d)로 여십시오." % port)
        origin = self.headers.get("Origin")
        if origin and origin.strip().lower() not in tuple("http://" + h for h in hosts):
            raise ApiError(403, "다른 사이트에서 온 요청은 받지 않습니다.", "실행 창에 표시된 주소에서 직접 쓰십시오.")

    def _content_length(self):
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            raise ApiError(411, "본문 길이(Content-Length)가 없는 요청은 받지 않습니다.", "화면을 새로 고친 뒤 다시 해 보십시오.")
        raw = self.headers.get("Content-Length")
        if raw is None:
            return None
        try:
            n = int(raw)
        except ValueError:
            n = -1
        if n < 0:
            raise ApiError(400, "본문 길이(Content-Length)가 올바르지 않습니다.", "화면을 새로 고친 뒤 다시 해 보십시오.")
        self._body_left = n
        return n

    def _read_body(self, k):
        """본문에서 k 바이트까지 읽고, 아직 읽지 않은 길이를 기억한다."""
        chunk = self.rfile.read(k)
        self._body_left = max(0, getattr(self, "_body_left", 0) - len(chunk))
        return chunk

    def _drain(self, n=None):
        """읽지 않은 본문을 버린다(본문이 남은 채 응답하고 닫으면 브라우저가 연결 끊김으로 본다)."""
        left = getattr(self, "_body_left", 0) if n is None else min(n or 0, getattr(self, "_body_left", n or 0))
        while left > 0:
            chunk = self._read_body(min(CHUNK, left))
            if not chunk:
                break
            left -= len(chunk)

    def _read_json(self):
        n = self._content_length()
        if n is None or n == 0:
            return {}
        if n > MAX_JSON_BYTES:
            self._drain(n)
            raise ApiError(413, "요청이 너무 큽니다.", "목적 문장을 줄여 다시 해 보십시오.")
        raw = self._read_body(n)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "요청 본문을 읽지 못했습니다(JSON 형식이 아닙니다).", "화면을 새로 고친 뒤 다시 해 보십시오.")
        if not isinstance(obj, dict):
            raise ApiError(400, "요청 본문의 모양이 다릅니다(객체가 아닙니다).", "화면을 새로 고친 뒤 다시 해 보십시오.")
        return obj

    def _dispatch(self, routes):
        store = self.server.store
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path
        query = urllib.parse.parse_qs(parts.query)
        self._body_left = 0
        try:
            self._check_origin()
            fn = routes.get(path)
            if fn is not None:
                return fn(store, query)
            if path.startswith("/api/"):
                known = ("/api/status", "/api/options", "/api/plan", "/api/center-csv", "/api/clear", "/api/sample-csv", "/api/local")
                if path in known:
                    raise ApiError(405, "이 주소는 %s 방식으로 부를 수 없습니다." % self.command, "화면을 새로 고친 뒤 다시 해 보십시오.")
                raise ApiError(404, "없는 주소입니다(%s)." % path[:80], "화면을 새로 고친 뒤 다시 해 보십시오.")
            if self.command in ("GET", "HEAD"):
                return self._static(path)
            raise ApiError(405, "이 주소는 %s 방식으로 부를 수 없습니다." % self.command, "화면을 새로 고친 뒤 다시 해 보십시오.")
        except ApiError as e:
            try:
                self._drain()
                self.send_api_error(e.status, e.message, e.action)
            except (ConnectionError, TimeoutError):
                pass
        except (ConnectionError, TimeoutError):
            # 브라우저가 요청을 중간에 끊었다(새로 고침, 탭 닫기). Windows 는 ConnectionAbortedError 로 올라온다.
            pass
        except Exception as e:
            traceback.print_exc()
            try:
                self._drain()
                self.send_api_error(500, "프로그램 안에서 오류가 났습니다(%s)." % type(e).__name__,
                                    "같은 일이 되풀이되면 실행 창의 글을 확인하고, 창을 닫은 뒤 다시 실행하십시오.")
            except Exception:
                pass

    def do_GET(self):
        self._dispatch({"/api/status": self.api_status, "/api/options": self.api_options, "/api/sample-csv": self.api_sample_csv, "/api/local": self.api_local})

    def do_HEAD(self):
        self._dispatch({})

    def do_POST(self):
        self._dispatch({"/api/plan": self.api_plan, "/api/center-csv": self.api_center_csv, "/api/clear": self.api_clear})

    def _not_allowed(self):
        try:
            self._drain(self._content_length())
        except ApiError:
            pass
        self.send_api_error(405, "이 주소는 %s 방식으로 부를 수 없습니다." % self.command, "화면을 새로 고친 뒤 다시 해 보십시오.")

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_allowed

    # ------------------------------------------------------------ 화면 파일
    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        rel = posixpath.normpath(urllib.parse.unquote(path)).lstrip("/")
        full = os.path.realpath(os.path.join(WEB_DIR, *rel.split("/")))
        root = os.path.realpath(WEB_DIR)
        ext = os.path.splitext(full)[1].lower()
        inside = full == root or full.startswith(root + os.sep)
        if not inside or ext not in MIME or not os.path.isfile(full):
            raise ApiError(404, "없는 화면 파일입니다.", "주소를 확인하거나 실행 창에 표시된 주소로 다시 여십시오.")
        with open(full, "rb") as f:
            body = f.read()
        self._send(200, body, MIME[ext], {"Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer"})

    # ------------------------------------------------------------ API
    def api_local(self, store, query):
        """동네 미리 보기(공개 Open API). 읍면동 이름과 업종 코드만 받는다."""
        emd = (query.get("emd") or [""])[0].strip()[:20]
        biz = (query.get("biz") or [""])[0].strip()[:10]
        if not emd:
            raise ApiError(400, "읍면동 이름이 없습니다.", "받을 목록을 다시 만들어 보십시오.")
        self.send_json(200, local_api.preview(emd, biz))

    def api_sample_csv(self, store, query):
        """동봉한 연습용 가상 CSV 하나를 그대로 돌려준다(샘플로 해 보기). manifest 에 있는 파일만."""
        man = catalog_mod.read_json(config.SAMPLE_MANIFEST) or {}
        files = [f for f in (man.get("files") or []) if isinstance(f, dict) and f.get("file")]
        want = (query.get("dataset") or [""])[0]
        pick = next((f for f in files if want and f.get("dataset_no") == want), files[0] if files else None)
        full = os.path.join(config.SAMPLE_DIR, os.path.basename(pick["file"])) if pick else ""
        if not pick or not os.path.isfile(full):
            raise ApiError(404, "연습용 샘플 파일이 없습니다.", "data/sample_center/ 폴더가 있는지 확인하십시오.")
        with open(full, "rb") as f:
            body = f.read()
        self._send(200, body, "application/octet-stream",
                   {"X-Filename": urllib.parse.quote(os.path.basename(full)), "Cache-Control": "no-store"})

    def api_status(self, store, query):
        cat = catalog_mod.get()
        self.send_json(200, {
            "spec": cat.counts(),
            "public": public_mod.get().status(),
            "llm": agent.llm_status(wait=3.0),
            "selfcheck": selfcheck.run(store.wanted_dir),
            "leftover": store.leftover(),
            "spec_label": cat.label(),
            "placeholder": public_mod.get().placeholder,
        })

    def api_options(self, store, query):
        self.send_json(200, planner.options())

    def api_plan(self, store, query):
        body = self._read_json()
        mode = body.get("mode") or "auto"
        if mode not in ("auto", "rule"):
            raise ApiError(400, "mode 는 auto 또는 rule 이어야 합니다.", "화면을 새로 고친 뒤 다시 해 보십시오.")
        text = body.get("text") or ""
        if not isinstance(text, str):
            raise ApiError(400, "목적 문장(text)이 글자가 아닙니다.", "문장을 다시 적어 주십시오.")
        if len(text) > agent.MAX_TEXT_CHARS:
            raise ApiError(400, "목적 문장이 너무 깁니다(%d자까지)." % agent.MAX_TEXT_CHARS, "한두 문장으로 줄여 다시 해 보십시오.")
        slots = body.get("slots")
        if slots is None:
            slots = {}
        if not isinstance(slots, dict):
            raise ApiError(400, "'직접 고르기'(slots)의 모양이 다릅니다.", "화면을 새로 고친 뒤 다시 골라 주십시오.")
        slots = clean_slots(slots)
        run_id = uuid.uuid4().hex[:12]
        resp = agent.plan_auto(mode, text, slots, run_id)
        store.save_plan(resp)
        store.log(action="plan", run_id=run_id, mode_used=resp.get("mode_used"), fallback=bool(resp.get("fallback_reason")),
                  validation="%s/%s" % (resp["validation"].get("passed"), resp["validation"].get("total")))
        self.send_json(200, resp)

    def api_center_csv(self, store, query):
        n = self._content_length()
        if n is None:
            raise ApiError(411, "파일 크기(Content-Length)를 알 수 없어 받지 못했습니다.", "화면에서 파일을 다시 골라 주십시오.")
        if n > MAX_UPLOAD_BYTES:
            self._drain(n)
            raise ApiError(413, "파일이 너무 큽니다(%dGB 까지)." % (MAX_UPLOAD_BYTES // (1024 ** 3)),
                           "센터에서 받을 때 달과 조건(지역·업종)을 좁혀 다시 받으십시오.")
        run_id = (query.get("run_id") or [""])[0]
        if not RUN_ID_RE.match(run_id):
            run_id = ""
        dataset_no = (query.get("dataset_no") or [""])[0]
        if not DATASET_NO_RE.match(dataset_no):
            dataset_no = None
        filename = safe_filename(self.headers.get("X-Filename"))
        cant_write = ApiError(507, "올린 파일을 임시 폴더(runtime)에 쓰지 못했습니다.",
                              "프로그램 폴더를 C:\\center-order 처럼 바탕 화면·문서 폴더가 아닌 곳으로 옮겨 다시 실행하십시오. 디스크 빈 공간도 확인하십시오.")
        try:
            path = store.new_upload_path(filename)
        except OSError:
            raise cant_write            # 남은 본문은 _dispatch 가 버린 뒤 응답한다
        got = 0
        try:
            with open(path, "wb") as f:
                while got < n:
                    chunk = self._read_body(min(CHUNK, n - got))
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
        except OSError:
            _remove(path)
            raise cant_write
        if got < n:
            _remove(path)
            raise ApiError(400, "파일이 끝까지 오지 않았습니다.", "파일을 다시 올려 주십시오.")
        need = store.need_for(run_id)
        # 재분석에는 LLM 호출이 없다. center_csv 는 네트워크를 쓰지 않는다.
        result = center_csv.analyze(path, filename, request=need, dataset_no=dataset_no)
        if result.get("error"):
            _remove(path)
            err = result["error"]
            store.log(action="center-csv", run_id=run_id, file=filename, ok=False)
            return self.send_api_error(400, err.get("message", "파일을 읽지 못했습니다."), err.get("action", ""),
                                       {"diagnostics": result.get("diagnostics", []), "recognized": result.get("recognized")})
        if need is None:
            result.setdefault("diagnostics", []).append({
                "level": "info",
                "detail": "이 파일과 이어진 주문서를 찾지 못해 파일 전체로 집계했습니다. 주문서를 먼저 만든 뒤 올리면 그 지역·업종으로 집계합니다."})
        store.save_csv_result(run_id, result)
        store.log(action="center-csv", run_id=run_id, file=filename, ok=True,
                  dataset_no=(result.get("recognized") or {}).get("dataset_no"), rows=result.get("rows"))
        self.send_json(200, result)

    def api_clear(self, store, query):
        try:
            self._drain(min(self._content_length() or 0, MAX_JSON_BYTES))
        except ApiError:
            pass
        self.send_json(200, {"deleted": store.clear()})


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def clean_slots(slots):
    """화면에서 온 세 칸 선택에서 아는 칸만, 알맞은 모양으로 남긴다."""
    out = {}
    for k in ("who", "what", "when", "region"):
        v = slots.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:100]
    units = slots.get("units")
    if isinstance(units, list):
        out["units"] = [u for u in units if isinstance(u, str)][:10]
    focus = slots.get("focus")
    if isinstance(focus, dict):
        f = {}
        for k in ("dow", "slot", "age"):
            v = focus.get(k)
            if isinstance(v, list):
                f[k] = [x for x in v if isinstance(x, str)][:10]
        if f:
            out["focus"] = f
    return out


# ====================================================================== 서버 띄우기

class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 에서는 주소 재사용을 켜면 같은 포트를 두 프로그램이 함께 잡을 수 있어 끈다.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        # http.server 의 server_bind 는 socket.getfqdn 으로 이름을 거꾸로 찾는다. 이름 풀이가 느린 망에서는
        # 주소가 뜨기까지 몇 초가 걸릴 수 있어, 이름을 찾지 않고 바로 묶는다(server_name 은 쓰지 않는다).
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]

    def handle_error(self, request, client_address):
        # 브라우저가 연결을 중간에 끊은 것은 오류가 아니다. 실행 창에 추적문을 찍지 않는다.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return
        if not getattr(self, "quiet", False):
            try:
                sys.stderr.write("[요청 처리 중 오류] %s\n" % type(exc).__name__)
            except Exception:
                pass


def make_server(port=0, runtime_dir=None, quiet=False, verbose=False):
    """서버를 만든다(아직 돌리지 않는다). port=0 이면 빈 포트를 잡는다.

    quiet=True 면 실행 창에 아무것도 쓰지 않는다(시험용). verbose=True 면 요청마다 한 줄씩 쓴다.
    둘 다 아니면 오류(4xx·5xx)만 쓴다.
    """
    httpd = LocalServer(("127.0.0.1", int(port or 0)), Handler)
    httpd.store = Store(runtime_dir)
    httpd.quiet = quiet
    httpd.verbose = verbose
    return httpd


def _env_port():
    """환경변수 PORT. 숫자가 아니면 0(빈 포트를 고른다)."""
    try:
        n = int(os.environ.get("PORT") or 0)
    except ValueError:
        return 0
    return n if 0 <= n <= 65535 else 0


def _say(text=""):
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def _banner(url):
    bar = "=" * 64
    _say("")
    _say(bar)
    _say("")
    _say("    " + url)
    _say("")
    _say("  브라우저에서 위 주소를 엽니다. 자동으로 열리지 않으면 주소를 복사해 붙여 넣으십시오.")
    _say("  Open the address above in your browser.")
    _say("  끝내려면 이 창을 닫거나 Ctrl+C 를 누릅니다. (Close this window to stop.)")
    _say("")
    _say(bar)
    _say("")


def _open_browser(url):
    try:
        ok = webbrowser.open(url)
    except Exception:
        ok = False
    if not ok:
        _say("브라우저를 자동으로 열지 못했습니다. 아래 주소를 브라우저 주소창에 직접 넣으십시오.")
        _say("Could not open a browser automatically. Open this address yourself:")
        _banner(url)


def main(argv=None):
    if sys.version_info < (3, 9):
        sys.stderr.write("Python 3.9 or newer is required. Found %d.%d.\n" % sys.version_info[:2])
        return 1
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="센터 방문 주문서 - 로컬 서버")
    ap.add_argument("--port", type=int, default=_env_port(), help="포트(주지 않으면 빈 포트를 고른다)")
    ap.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않는다")
    ap.add_argument("--runtime", default=None, help="임시 폴더(기본: 프로그램 폴더 아래 runtime)")
    ap.add_argument("--verbose", action="store_true", help="요청마다 실행 창에 한 줄씩 쓴다(기본은 오류만)")
    args = ap.parse_args(argv)

    _say("센터 방문 주문서를 준비하고 있습니다. (Starting...)")
    checks = selfcheck.run(args.runtime or DEFAULT_RUNTIME_DIR)
    for line in selfcheck.format_lines(checks):
        _say("  " + line)
    try:
        httpd = make_server(args.port, args.runtime, verbose=args.verbose)
    except OSError as e:
        _say("서버를 띄우지 못했습니다(%s). --port 를 빼거나 다른 번호로 다시 실행하십시오." % type(e).__name__)
        _say("Could not start the local server.")
        return 1
    url = "http://127.0.0.1:%d/" % httpd.server_address[1]
    agent.start_probe()
    _banner(url)
    if not (args.no_browser or os.environ.get("NO_BROWSER")):
        t = threading.Thread(target=_open_browser, args=(url,), name="open-browser")
        t.daemon = True
        t.start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _say("프로그램을 끝냅니다. (Stopped.)")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
