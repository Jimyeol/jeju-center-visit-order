# -*- coding: utf-8 -*-
"""Agent 루프: Claude Messages API 를 urllib 로 직접 불러 도구 5개를 돌린다(SDK 를 쓰지 않는다).

역할 나눔
    LLM   목적 문장을 읽고, 도구를 부르고, 데이터를 고른 이유를 글로 쓴다.
    코드  수치 계산, 명세서 대조, 받을 달·조건·컬럼 채우기, 검증. 화면에 나가는 숫자는 전부 코드가 만든다.

지키는 것
    - 응답의 content 를 한 글자도 바꾸지 않고 다음 요청에 붙인다(thinking 블록 포함). 대화는 덧붙이기만 한다.
    - 한 응답에 도구 호출이 여럿이면 결과를 한 user 메시지에 모아 돌려준다.
    - 도구 입력은 prompts/tools.json 의 형식으로 검사하고, 어긋나면 is_error 결과로 돌려준다.
    - submit_order 가 검증기에 걸리면 어긋난 줄을 돌려주어 고치게 한다(최대 2회).
    - 호출 상한 8턴, 요청마다 시간 초과 60초, 429·5xx·연결 실패는 두 번 다시 시도한다.
    - 실패하면 LLMError 를 던지고, plan_auto 가 규칙 모드로 넘기면서 fallback_reason 에 원인과 조치를 적는다.
    - 센터 CSV 값은 이 모듈로 들어오지 않는다(재분석 경로는 이 모듈을 부르지 않는다).
    - Key 값은 요청 헤더에만 넣는다. 출력·기록·오류 문장에 넣지 않는다.

설정(.env 또는 환경변수. 환경변수가 먼저다)
    ANTHROPIC_API_KEY   없으면 규칙 모드
    MODEL               기본 claude-haiku-5-5 (Claude 의 저가 등급인 Haiku 계열)
    API_BASE            기본 https://api.anthropic.com (모의 서버를 물릴 때 바꾼다)
    EFFORT              선택. low|medium|high|xhigh|max. 비우면 보내지 않는다(모델 기본값)
"""
import datetime
import hashlib
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import catalog as catalog_mod
from . import config
from .korean import josa as kj
from . import planner
from . import public_data as public_mod

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_API_BASE = "https://api.anthropic.com"
API_VERSION = "2026-10-21"

MAX_TURNS = 8              # LLM 호출 상한
MAX_ORDER_FIXES = 2        # submit_order 를 고쳐 다시 낼 수 있는 횟수
REQUEST_TIMEOUT = 60       # 요청 한 번의 시간 초과(초)
MAX_RETRIES = 2            # 429·5xx·연결 실패·시간 초과일 때 다시 시도하는 횟수
RETRY_BACKOFF = (1.0, 2.0)
TOTAL_BUDGET = 240         # 한 번의 계획에 쓰는 전체 시간 상한(초)
MAX_TOKENS = 16000
PROBE_TIMEOUT = 10
MAX_TEXT_CHARS = 2000

ENV_FILE = os.path.join(config.ROOT, ".env")
PROMPT_DIR = os.path.join(config.APP_DIR, "prompts")
SYSTEM_PROMPT_FILE = os.path.join(PROMPT_DIR, "system.md")
TOOLS_FILE = os.path.join(PROMPT_DIR, "tools.json")
MAC_CA_FILE = "/etc/ssl/cert.pem"

EFFORT_VALUES = ("low", "medium", "high", "xhigh", "max")
TOOL_NAMES = ("public_scope", "public_monthly", "catalog_search", "pick_months", "submit_order")

NO_KEY_REASON = ("ANTHROPIC_API_KEY 가 없어 규칙 모드로 실행한다. "
                 "조치: 프로그램 폴더의 .env 파일(.env.example 을 복사)에 Key 를 넣고 다시 실행하면 LLM 모드가 된다.")


class LLMError(Exception):
    """LLM 을 쓰지 못한 이유. reason() 이 fallback_reason 에 들어간다."""

    def __init__(self, kind, message, action="", retryable=False, status=None, delivered=False):
        Exception.__init__(self, message)
        self.kind = kind
        self.message = message
        self.action = action
        self.retryable = retryable
        self.status = status
        self.delivered = delivered     # 요청이 서버까지 갔는가(갔을 수 있으면 True). '보낸 내용' 집계에 쓴다
        self.sent = None               # 실패하기 전까지 LLM 으로 보낸 내용(plan 이 채운다)

    def reason(self):
        return self.message + ((" 조치: " + self.action) if self.action else "")


# ====================================================================== 설정

_env_cache = {"path": None, "mtime": None, "values": {}, "unreadable": False}

ENV_UNREADABLE_REASON = (".env 파일을 읽지 못했다(글자 인코딩을 알아보지 못함). 그래서 Key 가 없는 것으로 보고 규칙 모드로 실행한다. "
                         "조치: 메모장에서 .env 를 열어 '다른 이름으로 저장' → 인코딩 'UTF-8' 로 다시 저장한 뒤 다시 실행한다.")


def _decode_env(raw):
    """.env 의 바이트 → 글자. UTF-8(BOM 있어도 됨) → UTF-16(BOM 이 있거나 NUL 이 섞였을 때) → CP949 순. 못 읽으면 None.

    Windows 에서 PowerShell 5.1 의 '> .env' 는 UTF-16 으로, 메모장의 ANSI 저장은 CP949 로 만든다.
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        order = ("utf-16",)
    elif b"\x00" in raw:
        order = ("utf-16-le", "utf-16-be")
    else:
        order = ("utf-8-sig", "cp949")
    for enc in order:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def read_env_file(path=None):
    """.env 를 읽는다(KEY=VALUE 줄, # 은 주석). 없으면 빈 dict."""
    path = path or ENV_FILE
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        _env_cache.update({"path": path, "mtime": None, "values": {}, "unreadable": False})
        return {}
    if _env_cache["path"] == path and _env_cache["mtime"] == mtime:
        return _env_cache["values"]
    values = {}
    unreadable = False
    try:
        with open(path, "rb") as f:
            text = _decode_env(f.read(1024 * 1024))
    except OSError:
        text = None
    if text is None:
        unreadable = True
    else:
        for line in text.splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k.startswith("export "):
                k = k[7:].strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            values[k] = v
    _env_cache.update({"path": path, "mtime": mtime, "values": values, "unreadable": unreadable})
    return values


def no_key_reason():
    """Key 가 없을 때 화면에 적을 사유. .env 가 있는데 읽지 못한 경우를 따로 알린다."""
    read_env_file()
    return ENV_UNREADABLE_REASON if _env_cache.get("unreadable") else NO_KEY_REASON


def settings():
    """{"key", "model", "api_base", "effort"}. 환경변수가 .env 보다 먼저다. Key 는 이 dict 밖으로 내보내지 않는다."""
    f = read_env_file()

    def pick(name):
        v = os.environ.get(name)
        if v is None or not v.strip():
            v = f.get(name, "")
        return (v or "").strip()

    effort = pick("EFFORT").lower()
    return {
        "key": pick("ANTHROPIC_API_KEY"),
        "model": pick("MODEL") or DEFAULT_MODEL,
        "api_base": (pick("API_BASE") or DEFAULT_API_BASE).rstrip("/"),
        "effort": effort if effort in EFFORT_VALUES else "",
    }


def _is_loopback(host):
    return host in ("127.0.0.1", "localhost", "::1") or (host or "").endswith(".localhost")


def _check_base(cfg):
    """API_BASE 가 쓸 수 있는 주소인지. 문제가 있으면 LLMError 를 돌려준다(던지지 않는다)."""
    u = urllib.parse.urlsplit(cfg["api_base"])
    if u.scheme == "https" and u.hostname:
        return None
    if u.scheme == "http" and _is_loopback(u.hostname):
        return None
    return LLMError("config", "API_BASE 주소를 쓸 수 없다(https 주소이거나, 이 PC 안의 http 주소여야 한다).",
                    ".env 의 API_BASE 를 비우거나 https 주소로 고친다.")


_prompt_cache = {}


def system_prompt():
    if "system" not in _prompt_cache:
        with open(SYSTEM_PROMPT_FILE, "r", encoding="utf-8") as f:
            _prompt_cache["system"] = f.read().strip()
    return _prompt_cache["system"]


def tool_defs():
    if "tools" not in _prompt_cache:
        with open(TOOLS_FILE, "r", encoding="utf-8") as f:
            _prompt_cache["tools"] = json.load(f)
    return _prompt_cache["tools"]


# ====================================================================== HTTP

_ssl_state = {"ctx": None, "stage": 0, "mac_fallback": False}
_ssl_lock = threading.Lock()


def _ssl_context(use_mac_bundle=False, relaxed=False):
    """인증서 검증을 끄지 않는다(verify_mode=CERT_REQUIRED, 호스트 이름 확인은 늘 켠 채다).

    relaxed=True 는 파이썬 3.13 부터 기본으로 켜진 VERIFY_X509_STRICT 한 가지만 끈다. 3.12 이하의 기본 동작과 같다.
    기관 망의 보안 장비가 쓰는 루트 인증서가 이 엄격 검사(기관 키 식별자 없음 등)에만 걸리는 경우가 있어서다.
    """
    ctx = ssl.create_default_context(cafile=MAC_CA_FILE) if use_mac_bundle else ssl.create_default_context()
    if relaxed:
        strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
        if strict:
            ctx.verify_flags &= ~strict
    return ctx


def _ssl_stages():
    """인증서 검증에 실패했을 때 차례로 해 볼 설정: (macOS 시스템 묶음을 쓰는가, 엄격 검사를 푸는가)."""
    stages = [(False, False)]
    # 파이썬 3.13 부터 ssl.create_default_context() 가 VERIFY_X509_STRICT 를 켠다.
    has_strict = bool(getattr(ssl, "VERIFY_X509_STRICT", 0)) and sys.version_info >= (3, 13)
    if has_strict:
        stages.append((False, True))
    if sys.platform == "darwin" and os.path.exists(MAC_CA_FILE):
        # python.org 맥 설치판은 인증서 묶음이 비어 있다. 시스템 묶음으로 한 번 더 한다.
        stages.append((True, False))
        if has_strict:
            stages.append((True, True))
    return stages


def _is_cert_error(exc):
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    return isinstance(reason, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(reason)


def _is_timeout(exc):
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower()


def _open(req, timeout):
    """urlopen. https 는 검증을 켠 채로, 이 PC 안의 주소는 프록시를 거치지 않고 연다."""
    u = urllib.parse.urlsplit(req.full_url)
    if _is_loopback(u.hostname):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(req, timeout=timeout)
    with _ssl_lock:
        if _ssl_state["ctx"] is None:
            _ssl_state.update({"ctx": _ssl_context(False, False), "stage": 0, "mac_fallback": False})
        ctx = _ssl_state["ctx"]
        stage = _ssl_state["stage"]
    stages = _ssl_stages()
    while True:
        try:
            return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, ssl.SSLError) as e:
            if not _is_cert_error(e) or stage + 1 >= len(stages):
                raise
            # 검증은 그대로 둔 채 다음 설정으로 한 번 더 한다. 넘어간 단계는 기억해 두고 되돌아가지 않는다.
            stage += 1
            mac, relaxed = stages[stage]
            ctx = _ssl_context(mac, relaxed)
            with _ssl_lock:
                if stage > _ssl_state["stage"]:
                    _ssl_state.update({"ctx": ctx, "stage": stage, "mac_fallback": mac})


def _cert_error(exc, url):
    """인증서 검증 실패 → 운영체제에 맞는 원인과 조치."""
    reason = getattr(exc, "reason", exc)
    detail = _one_line(getattr(reason, "verify_message", "") or "", 80)
    tail = (" 파이썬이 알려 준 까닭: %s." % detail) if detail else ""
    u = urllib.parse.urlsplit(url)
    site = "%s://%s" % (u.scheme, u.hostname or "")
    if sys.platform.startswith("win"):
        return LLMError("cert", "Claude API 서버의 인증서를 확인하지 못했다(Windows 인증서 저장소의 인증서로 검증되지 않는다).%s" % tail,
                        "기관·회사 망이면 망의 보안 장비 인증서가 Windows 인증서 저장소(신뢰할 수 있는 루트 인증 기관)에 "
                        "들어 있는지 전산 담당자에게 확인한다. Edge 로 %s 을 열어 인증서 경고가 뜨는지 본다." % site)
    if sys.platform == "darwin":
        return LLMError("cert", "Claude API 서버의 인증서를 확인하지 못했다(이 PC 의 파이썬에 인증서 묶음이 없다).%s" % tail,
                        "macOS 는 파이썬 설치 폴더의 'Install Certificates.command' 를 한 번 실행한다. "
                        "회사·기관 망이면 보안 프로그램의 인증서를 시스템에 등록한다.")
    return LLMError("cert", "Claude API 서버의 인증서를 확인하지 못했다(시스템의 인증서 묶음으로 검증되지 않는다).%s" % tail,
                    "시스템의 CA 인증서 묶음(ca-certificates)을 설치하거나 갱신한다. 회사·기관 망이면 보안 장비의 인증서를 시스템에 등록한다.")


def _http_error(e):
    """HTTPError → LLMError. 본문의 error.type 만 쓴다(본문 전체를 옮기지 않는다)."""
    status = e.code
    etype, emsg = "", ""
    try:
        raw = e.read(8192)
        body = json.loads(raw.decode("utf-8", "replace"))
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            etype = str(err.get("type") or "")
            emsg = str(err.get("message") or "")
    except (ValueError, OSError, AttributeError):
        pass
    try:
        e.close()
    except Exception:
        pass
    tag = "HTTP %d%s" % (status, (" " + etype) if etype else "")
    if status == 401:
        return LLMError("auth", "Claude API 가 Key 를 받아 주지 않았다(%s)." % tag,
                        ".env 의 ANTHROPIC_API_KEY 가 맞는지, 만료되지 않았는지 확인한다.", False, status, True)
    if status == 403:
        return LLMError("blocked", "Claude API 가 요청을 막았다(%s)." % tag,
                        "Key 의 권한과 조직 설정, 접속 지역 제한을 확인한다.", False, status, True)
    if status == 404:
        return LLMError("model", "Claude API 가 모델이나 주소를 찾지 못했다(%s)." % tag,
                        ".env 의 MODEL(기본 %s)과 API_BASE 를 확인한다." % DEFAULT_MODEL, False, status, True)
    if status == 429:
        return LLMError("rate", "Claude API 요청 한도를 넘었다(%s)." % tag,
                        "잠시 뒤에 다시 하거나 Key 의 사용 한도를 확인한다.", True, status, True)
    if status in (408, 409) or status >= 500:
        return LLMError("server", "Claude API 서버가 지금 응답하지 못한다(%s)." % tag,
                        "잠시 뒤에 다시 한다.", True, status, True)
    detail = (" " + _one_line(emsg, 160)) if emsg else ""
    return LLMError("request", "Claude API 가 요청을 받아 주지 않았다(%s).%s" % (tag, detail),
                    ".env 의 MODEL 과 EFFORT 값을 확인한다.", False, status, True)


def _request_json(method, url, headers, data=None, timeout=REQUEST_TIMEOUT):
    """요청 한 번. 성공하면 JSON(dict), 실패하면 LLMError."""
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = _open(req, timeout)
        try:
            raw = resp.read()
        finally:
            resp.close()
    except urllib.error.HTTPError as e:
        raise _http_error(e)
    except (urllib.error.URLError, ssl.SSLError, OSError) as e:
        if _is_cert_error(e):
            raise _cert_error(e, url)
        if _is_timeout(e):
            raise LLMError("timeout", "Claude API 가 %d초 안에 응답하지 않았다." % int(timeout),
                           "인터넷 연결을 확인하고 다시 한다.", True, None, True)
        # 연결 자체가 안 된 경우(이름 풀이 실패, 거절)는 본문이 나가지 않았다. 응답을 읽다가 끊긴 경우는 나갔다.
        sent = not isinstance(e, urllib.error.URLError)
        raise LLMError("connect", "Claude API 에 연결하지 못했다(%s)." % type(getattr(e, "reason", e)).__name__,
                       "인터넷 연결과 방화벽·프록시 차단 여부를 확인한다.", True, None, sent)
    except Exception as e:  # http.client 의 끊김(RemoteDisconnected, IncompleteRead 등)
        raise LLMError("connect", "Claude API 와의 연결이 끊겼다(%s)." % type(e).__name__,
                       "인터넷 연결을 확인하고 다시 한다.", True, None, True)
    try:
        out = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise LLMError("server", "Claude API 의 응답을 읽지 못했다(JSON 이 아니다).", "잠시 뒤에 다시 한다.", True, None, True)
    if not isinstance(out, dict):
        raise LLMError("server", "Claude API 의 응답 모양이 다르다.", "API_BASE 를 확인한다.", False, None, True)
    return out


def _headers(cfg):
    return {
        "content-type": "application/json",
        "x-api-key": cfg["key"],
        "anthropic-version": API_VERSION,
        "user-agent": "center-order-mvp",
    }


def call_messages(body, cfg, deadline=None):
    """POST /v1/messages. 다시 시도할 수 있는 실패는 MAX_RETRIES 번까지 다시 한다."""
    url = cfg["api_base"] + "/v1/messages"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last = None
    for attempt in range(MAX_RETRIES + 1):
        timeout = REQUEST_TIMEOUT
        if deadline is not None:
            left = deadline - time.time()
            if left <= 1:
                raise LLMError("timeout", "LLM 계획이 전체 시간 상한(%d초)을 넘었다." % TOTAL_BUDGET, "다시 하거나 '직접 고르기'를 쓴다.")
            timeout = min(timeout, left)
        try:
            return _request_json("POST", url, _headers(cfg), data, timeout)
        except LLMError as e:
            last = e
            if not e.retryable or attempt >= MAX_RETRIES:
                break
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            time.sleep(wait)
    if last.retryable:
        last.message = last.message.rstrip(".") + ". %d번 다시 시도했으나 같았다." % MAX_RETRIES
    raise last


# ====================================================================== 연결 확인(상태 표시용)

_probe = {"fp": None, "state": "none", "error": None, "thread": None}
_probe_lock = threading.Lock()


def _fingerprint(cfg):
    return hashlib.sha256(("%s|%s|%s" % (cfg["key"], cfg["model"], cfg["api_base"])).encode("utf-8")).hexdigest()


def check_connection(cfg=None):
    """Key·모델·인증서가 되는지 한 번 물어본다(GET /v1/models/{model}. 토큰을 쓰지 않는다)."""
    cfg = cfg or settings()
    fp = _fingerprint(cfg)
    state, err = "ok", None
    bad = _check_base(cfg)
    if not cfg["key"]:
        state = "none"
    elif bad:
        state, err = "failed", bad
    else:
        url = cfg["api_base"] + "/v1/models/" + urllib.parse.quote(cfg["model"], safe="")
        try:
            _request_json("GET", url, _headers(cfg), None, PROBE_TIMEOUT)
        except LLMError as e:
            # Key 거부·차단·인증서는 다시 해도 같다. 그 밖(시간 초과, 모델 조회 실패 등)은 요청 때 다시 해 본다.
            state = "failed" if e.kind in ("auth", "blocked", "cert", "config") else "unsure"
            err = e
    with _probe_lock:
        _probe.update({"fp": fp, "state": state, "error": err})
    return state


def start_probe():
    """서버가 뜰 때 뒤에서 연결을 확인한다. Key 가 없으면 아무것도 하지 않는다."""
    cfg = settings()
    if not cfg["key"]:
        return None
    t = threading.Thread(target=check_connection, args=(cfg,), name="llm-probe")
    t.daemon = True
    with _probe_lock:
        _probe.update({"fp": _fingerprint(cfg), "state": "pending", "error": None, "thread": t})
    t.start()
    return t


def llm_status(wait=0.0):
    """/api/status 의 llm: {"enabled", "model", "reason"} (+ "checked")."""
    cfg = settings()
    out = {"enabled": False, "model": cfg["model"], "reason": "", "checked": "none"}
    if not cfg["key"]:
        out["reason"] = no_key_reason()
        if _env_cache.get("unreadable"):
            out["checked"] = "failed"
        return out
    bad = _check_base(cfg)
    if bad:
        out["reason"] = bad.reason()
        out["checked"] = "failed"
        return out
    with _probe_lock:
        t = _probe["thread"] if _probe["fp"] == _fingerprint(cfg) else None
    if t is not None and t.is_alive() and wait > 0:
        t.join(wait)
    with _probe_lock:
        same = _probe["fp"] == _fingerprint(cfg)
        state = _probe["state"] if same else "none"
        err = _probe["error"] if same else None
    out["checked"] = state
    if state == "failed":
        out["reason"] = err.reason() if err else "연결 확인 실패"
        return out
    out["enabled"] = True
    if state == "unsure" and err:
        out["reason"] = "연결 확인을 마치지 못했다(%s) 요청할 때 다시 해 본다." % err.message
    return out


# ====================================================================== 글 다듬기(숫자·금지 표현 빼기)

_HUB_NUM = re.compile(r"허브\s?(?:1048|520)")
_DATASET_NO = re.compile(r"(?<![\d-])(\d{1,2}-\d{2})(?![\d-])")
_DATASET_NO_FULL = re.compile(r"^\d{1,2}-\d{2}$")
# 이름으로 쓰는 숫자: 판 표기(v01, v02), 연령대 이름(10대~70대), '10 미만', '10세 미만'
_NAME_NUM = re.compile(r"[vV]0[1-9]|[1-7]0대|10\s?미만|10세 미만")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_BANNED = ("국내 최초", "유일", "최적의 달", "맞는 데이터를 찾아 드립니다")
# 숫자 없이 수량·순위를 말하는 한글 표현. 수치는 코드만 말한다는 규칙을 한글 수사로 돌아가지 못하게 한다.
_KO_QUANTITY = re.compile(
    r"절반|갑절|곱절|퍼센트|과반|대부분"
    r"|(?<![가-힣])(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|몇|수)\s?배"
    r"|(?<![가-힣])[일이삼사오육칠팔구십백천만억조]+\s?(?:원|명|건|배|퍼센트|프로)(?![가-힣])"
    r"|(?<![가-힣])[일이삼사오육칠팔구십백천]+\s?(?:억|만|천)\s?(?:원|명|건)?"
    r"|(?<![가-힣])(?:수|몇)\s?(?:십|백|천|만|억)"
    r"|(?<![가-힣])(?:일등|이등|삼등|일위|이위|삼위|몇\s?위|몇\s?등|으뜸)"
    r"|(?:첫|둘|셋|넷)째로\s?(?:많|높|적|낮|크|작)"
    r"|도내\s?(?:최고|최대|최저|최소|일등|으뜸)|최고치|최저치")
# 내부에서 쓰는 영문 열쇠가 글에 섞여 나오면 한국어로 바꾼다.
_INTERNAL_WORDS = (("no_data", "메울 데이터 없음"), ("split", "여러 종을 따로 받기"), ("single", "한 종으로 함께 보기"),
                   ("compromise", "타협안"))
_INTERNAL_RE = dict((w, re.compile(r"(?<![A-Za-z_])%s(?![A-Za-z_])" % w)) for w, _ in _INTERNAL_WORDS)


def _strip_symbols(s):
    out = []
    for ch in s:
        o = ord(ch)
        if o >= 0x1F000 or 0x2600 <= o <= 0x27BF or o in (0xFE0F, 0x200D) or (o < 32 and ch not in "\n\t"):
            continue
        out.append(ch)
    return "".join(out)


def _one_line(s, limit):
    s = " ".join(str(s or "").split())
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def has_claims(text, spec_label=None, cat=None):
    """글에 수치·수량 표현이 들어 있는가. 이름으로 쓰는 숫자(실재하는 데이터 번호, v02, 20대, 허브 번호, 명세서 종수 표기)는 뺀다."""
    cat = cat or catalog_mod.get()
    if spec_label is None:
        spec_label = cat.label()
    probe = text.replace(spec_label, "") if spec_label else text
    probe = _HUB_NUM.sub("", probe)

    def keep_real_number(m):
        # 명세서에 실재하는 번호만 이름으로 본다. 뒤에 '월'이 붙으면 달 범위(8-12월)라 숫자로 본다.
        after = probe[m.end():m.end() + 1]
        if after != "월" and cat.has(m.group(1)):
            return ""
        return m.group(0)

    probe = _DATASET_NO.sub(keep_real_number, probe)
    probe = _NAME_NUM.sub("", probe)
    if re.search(r"\d", probe):
        return True
    return bool(_KO_QUANTITY.search(probe))


def clean_text(text, limit=400, spec_label=None):
    """LLM 이 쓴 글에서 수치·수량 표현이 든 문장과 금지 표현이 든 문장을 뺀다. (남은 글, 뺀 문장 수)

    수치는 코드만 말한다는 규칙을 코드로 지키는 곳이다. 빼는 것: 아라비아 숫자(전각 포함)가 든 문장,
    수량·순위를 말하는 한글 표현(절반, 두 배, 삼십억 원, 일등 등)이 든 문장. 남기는 것: 명세서에 실재하는
    데이터 번호(1-14), 판 표기(v02), 연령대 이름(20대), 허브 번호, 명세서 종수 표기.
    숫자 없이 쓴 서술(예: '여름에 많다')까지는 걸러 내지 못한다.
    """
    if not isinstance(text, str) or not text.strip():
        return "", 0
    cat = catalog_mod.get()
    if spec_label is None:
        spec_label = cat.label()
    text = _strip_symbols(text).replace("`", "").replace("*", "")
    for w, ko in _INTERNAL_WORDS:
        text = _INTERNAL_RE[w].sub(ko, text)
    kept, dropped = [], 0
    for sent in _SENT_SPLIT.split(text):
        s = " ".join(sent.split())
        if not s:
            continue
        if has_claims(s, spec_label, cat) or any(b in s for b in _BANNED):
            dropped += 1
            continue
        kept.append(s)
    out = " ".join(kept)
    if len(out) > limit:
        out = out[:limit - 1].rstrip() + "…"
    return out, dropped


# ====================================================================== 도구 입력 형식 검사

def _join(path, key):
    return "%s.%s" % (path, key) if path else str(key)


def drop_nulls(value):
    """선택 칸에 null 을 넣어 보내는 경우가 있어, null 인 칸은 없는 것으로 본다."""
    if isinstance(value, dict):
        return dict((k, drop_nulls(v)) for k, v in value.items() if v is not None)
    if isinstance(value, list):
        return [drop_nulls(v) for v in value]
    return value


def check_schema(value, schema, path=""):
    """JSON Schema 의 일부(type, enum, properties, required, additionalProperties, items, maxItems)만 검사한다."""
    errs = []
    where = path or "입력"
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            return ["%s: 객체({…})여야 한다" % where]
        props = schema.get("properties") or {}
        for k in schema.get("required") or []:
            if k not in value:
                errs.append("%s: 꼭 있어야 하는 칸이 없다" % _join(path, k))
        for k, v in value.items():
            if k not in props:
                if schema.get("additionalProperties") is False:
                    errs.append("%s: 모르는 칸이다(쓸 수 있는 칸: %s)" % (_join(path, k), ", ".join(sorted(props)) or "없음"))
                continue
            errs.extend(check_schema(v, props[k], _join(path, k)))
    elif t == "array":
        if not isinstance(value, list):
            return ["%s: 목록([…])이어야 한다" % where]
        mx = schema.get("maxItems")
        if mx is not None and len(value) > mx:
            errs.append("%s: %d개까지만 넣을 수 있다" % (where, mx))
        item_schema = schema.get("items")
        if item_schema:
            for i, v in enumerate(value[:50]):
                errs.extend(check_schema(v, item_schema, "%s[%d]" % (where if path else "입력", i)))
    elif t == "string":
        if not isinstance(value, str):
            return ["%s: 글자여야 한다" % where]
        if len(value) > 1000:
            errs.append("%s: 너무 길다(1000자까지)" % where)
        enum = schema.get("enum")
        if enum and value not in enum:
            errs.append("%s: '%s'%s 쓸 수 없다(쓸 수 있는 값: %s)" % (where, _one_line(value, 30), kj(_one_line(value, 30), "은"), ", ".join(enum)))
    elif t == "boolean":
        if not isinstance(value, bool):
            return ["%s: true 또는 false 여야 한다" % where]
    elif t == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return ["%s: 정수여야 한다" % where]
    return errs


# ====================================================================== 한 번의 계획(루프 상태)

_NO_REGION_WORDS = ("", "도 전체", "도전체", "제주도", "제주", "전체", "제주특별자치도")


class _Run(object):
    def __init__(self, text, slots, today, cfg):
        self.text = text
        self.slots = slots or {}
        self.today = today
        self.cfg = cfg
        self.cat = catalog_mod.get()
        self.trace = []                # LLM 이 부른 도구의 기록
        self.public_aggregates = 0     # LLM 으로 보낸 공개 데이터 집계 값의 수
        self.spec_items = 0            # LLM 으로 보낸 명세서 항목(후보·버린 후보·검증 줄)의 수
        self.fixes_used = 0
        self.submits = 0
        self.accepted = None           # (need, built)
        self.final_step = None
        self.rule_filled = False       # 검증을 끝내 통과하지 못해 주문 항목을 규칙으로 다시 짰는가
        self.dropped_sentences = 0
        self.turns = 0                 # 응답을 받은 LLM 호출 수
        self.requests = 0              # 보낸(보내려 한) LLM 요청 수
        self.input_tokens = 0
        self.output_tokens = 0
        self.pm_sources = set()        # LLM 이 public_monthly 로 값을 받아 본 공개 데이터(1048, 520)
        self.last_region_arg = ""      # 마지막으로 낸 목적의 지역 낱말(명세서에서 못 찾았을 때 알리려고)

    # ------------------------------------------------------------ 기록
    def step(self, tool, args, result, note="", meta=None):
        row = {"step": 0, "tool": tool, "args_summary": _one_line(args, 200),
               "result_summary": _one_line(result, 300), "note": _one_line(note, 400)}
        if meta:
            row["_meta"] = meta
        self.trace.append(row)
        return row

    def clean(self, text, limit=400):
        out, dropped = clean_text(text, limit, self.cat.label())
        self.dropped_sentences += dropped
        return out

    # LLM 이 도구 인자에 적은 글자는 검사를 통과한 것만 기록과 검증 문장에 옮긴다(인자를 통해 수치가 화면에 나가지 않게).
    def safe_no(self, value):
        v = str(value or "").strip()
        return v if _DATASET_NO_FULL.match(v) else "형식이 틀린 번호"

    def shown_no(self, value):
        v = self.safe_no(value)
        if v == "형식이 틀린 번호" or self.cat.has(v):
            return v
        return "명세서에 없는 번호"

    def safe_word(self, value, limit=40):
        """자유롭게 적는 인자(업종·지역 낱말). 수치·수량 표현이 들었으면 그대로 옮기지 않는다."""
        v = _one_line(_strip_symbols(str(value or "")), limit)
        if not v:
            return ""
        return "(숫자가 든 값)" if has_claims(v, self.cat.label(), self.cat) else v

    # ------------------------------------------------------------ 도구
    def run_tool(self, name, args, said=""):
        """(tool_result 에 넣을 dict, is_error)"""
        schema = None
        for t in tool_defs():
            if t["name"] == name:
                schema = t["input_schema"]
        if schema is None:
            self.step("없는 도구", "", "없는 도구를 불러 되돌려 보냄", "쓸 수 있는 도구: " + ", ".join(TOOL_NAMES))
            return {"error": "'%s' 라는 도구는 없다. 쓸 수 있는 도구: %s" % (_one_line(name, 40), ", ".join(TOOL_NAMES))}, True
        args = drop_nulls(args) if isinstance(args, dict) else args
        errs = check_schema(args, schema)
        if errs:
            self.step(name, "입력 형식이 어긋남", "도구를 돌리지 않고 되돌려 보냄", " / ".join(errs[:6]))
            return {"error": "입력 형식이 맞지 않아 도구를 돌리지 않았다. 아래를 고쳐 다시 부른다.", "problems": errs[:12]}, True
        fn = getattr(self, "_t_" + name)
        try:
            return fn(args, said), False
        except Exception as e:  # 도구 안의 예상하지 못한 오류도 대화를 끊지 않고 돌려준다
            self.step(name, "", "도구가 오류로 멈춤", type(e).__name__)
            return {"error": "도구가 오류로 멈췄다(%s). 입력을 바꿔 다시 부르거나 다른 도구로 진행한다." % type(e).__name__}, True

    def _t_public_scope(self, a, said):
        sc = planner.public_scope()
        pub = public_mod.get()
        self.step("public_scope", "", "1048: %s~%s %d줄 / 520: %s~%s %d줄%s" % (
            sc["d1048"]["period"]["first"], sc["d1048"]["period"]["last"], pub.d1048.rows,
            sc["d520"]["period"]["first"], sc["d520"]["period"]["last"], pub.d520.rows,
            " (대체 데이터)" if sc.get("placeholder") else ""), said)
        sc["d520"].get("values", {}).pop("hour", None)   # 0~24 숫자 나열은 뜻이 없어 뺀다
        return sc

    def _t_public_monthly(self, a, said):
        who = a.get("who") or "all"
        r = planner.public_monthly(a["source"], a.get("biz"), who, a.get("sex"), a.get("age"), a.get("metric"))
        bd = planner.resolve_biz(a["biz"], self.cat) if a.get("biz") else None
        biz_shown = (bd["name"] if bd else "42개 업종에 없는 이름") if a.get("biz") else ""
        args = "허브 %s%s%s%s%s" % (a["source"], (" · " + biz_shown) if biz_shown else "",
                                  (" · " + planner.WHO_LABELS[who]) if who != "all" else "",
                                  (" · " + a["sex"]) if a.get("sex") else "", (" · " + a["age"]) if a.get("age") else "")
        if not r["available"]:
            self.step("public_monthly", args, "공개 데이터로 답할 수 없음", r["reason"] + ((" / " + said) if said else ""))
            return {"available": False, "reason": r["reason"], "notes": r["notes"]}
        sm = r.get("season_months") or []
        self.step("public_monthly", r["source"], "%s 월별 %d달, 계절 지수 %s" % (
            r["metric"], len(r["series"]),
            ", ".join("%s %d월(%s)" % (m["role"], m["month"], m["index"]) for m in sm) or "내지 않음(자료가 모자람)"), said)
        self.pm_sources.add(str(a["source"]))
        out = {"available": True, "source": r["source"], "metric": r["metric"],
               "period": {"first": r["series"][0]["ym"], "last": r["series"][-1]["ym"], "months": len(r["series"])},
               "seasonal": r["seasonal"], "season_months": sm, "shares": r["shares"], "notes": r["notes"]}
        n = len(r["seasonal"]) + len(sm) + sum(len(s["rows"]) for s in r["shares"])
        if r.get("seasonal_before_split"):
            out["seasonal_before_split"] = r["seasonal_before_split"]
            n += len(r["seasonal_before_split"])
        self.public_aggregates += n
        return out

    def _need_from(self, a):
        # 문장에 요일·시간대를 가리키는 말이 없는데 LLM 이 그 단위를 넣었으면 뺀다(문장에 없는 단위는 넣지 않는다).
        units = list(a.get("units") or [])
        focus = dict(a.get("focus") or {})
        notes = []
        for unit, label in (("dow", "요일"), ("slot", "시간대")):
            if (unit in units or focus.get(unit)) and not planner.text_supports_unit(self.text, unit):
                units = [u for u in units if u != unit]
                focus.pop(unit, None)
                notes.append("문장에 %s 가리키는 말이 없어 LLM 이 넣은 '%s' 단위를 뺐다" % (planner.josa(label, "을를"), label))
        cross = bool(a.get("cross")) and "dow" in units and "slot" in units
        return planner.make_need(subject=a.get("subject"), region=a.get("region"), biz=a.get("biz"),
                                 who=a.get("who") or "all", units=units, focus=focus, cross=cross,
                                 text=self.text, source="llm", notes=notes, cat=self.cat)

    def _need_issues(self, a, need):
        out = []
        if a.get("biz") and a.get("subject") != "visit" and not need["biz_detail"]:
            out.append("업종 '%s'%s 42개 업종에서 찾지 못했다. public_scope 가 돌려준 업종 이름 가운데 하나로 적는다."
                       % (self.safe_word(a["biz"]), kj(self.safe_word(a["biz"]), "을")))
        region = (a.get("region") or "").strip()
        if region not in _NO_REGION_WORDS and need["region_detail"]["level"] == "all":
            out.append("지역 '%s'%s 명세서의 법정동·행정동·권역·시 이름에서 찾지 못했다. 문장에 적힌 지역 이름을 그대로 적는다."
                       % (self.safe_word(region), kj(self.safe_word(region), "을")))
        return out

    def _understood(self, a, need):
        return {"subject": need["subject_label"], "region": need["region"],
                "region_level": need["region_detail"]["level"],
                "region_same_name_in": need["region_detail"]["ambiguous"],
                "biz": need["biz"], "biz_code": need["biz_detail"]["code"] if need["biz_detail"] else "",
                "who": need["who"],
                "units_in_public": [u["label"] for u in need["units"] if u["in_public"]],
                "units_not_in_public": [u["label"] for u in need["units"] if not u["in_public"]],
                "problems": self._need_issues(a, need) + list(need["notes"])}

    def _t_catalog_search(self, a, said):
        need = self._need_from(a)
        r = planner.search_for(need, limit=6, cat=self.cat)
        d = planner.decide(need, self.cat)
        rej = [{"dataset_no": x["dataset_no"], "dataset_name": self.cat.name(x["dataset_no"]), "reason": x["reason"]}
               for x in planner.sorted_rejected(d, self.cat)[:8]]
        unit_names = "·".join(u["label"] for u in need["units"]) or "월별 흐름만"
        hard = [no for no in d["candidates"] if d["evaluations"][no]["hard_ok"]]
        if d["mode"] == "single":
            res = "후보 %d종 가운데 %s 단위를 모두 가진 데이터: %s %s" % (
                len(hard), "메울 수 있는" if r["no_data"] else "필요한", d["choice"][0], self.cat.name(d["choice"][0]))
        elif d["mode"] == "compromise":
            res = "후보 %d종 가운데 한 데이터로 필요한 단위를 다 채우는 것이 없다 → 타협안 %d개를 비교" % (len(hard), len(d["alternatives"]))
        elif d["mode"] == "not_needed":
            res = "필요한 단위가 모두 공개 데이터에 있어 센터 데이터를 고르지 않았다"
        else:
            res = "조건에 맞는 데이터가 없다"
        note = ("메울 데이터 없음: %s" % "·".join(r["no_data"])) if r["no_data"] else ""
        self.step("catalog_search", "필요한 단위: %s / 지역: %s%s / 대조한 데이터 %d종(%s 계열)" % (
            unit_names, need["region"], (" / 업종: " + need["biz"]) if need["biz"] else "", len(d["candidates"]),
            need["subject_label"]),
            res, " / ".join(x for x in (note, said) if x),
            meta={"subject": need["subject"], "units": sorted(u["key"] for u in need["units"])})
        out = {"understood": self._understood(a, need), "need_units": r["need_units"], "mode": r["mode"],
               "suggestion": r["suggestion"], "candidates": r["candidates"], "no_data": r["no_data"],
               "rejected": rej, "counted": r["counted"]}
        if r["mode"] == "compromise":
            out["hint"] = "한 데이터로는 다 채울 수 없다. suggestion.alternatives 의 두 안 가운데 하나를 골라 submit_order 의 choose 와 items 에 같은 안을 적는다."
        self.spec_items += len(r["candidates"]) + len(rej)
        return out

    def _t_pick_months(self, a, said):
        r = planner.pick_months(a["dataset_no"], biz=a.get("biz"), who=a.get("who") or "all",
                                subject=a.get("subject"), today=self.today)
        self.step("pick_months", self.shown_no(a["dataset_no"]),
                  ", ".join("%s(%s)" % (m["ym"], m["role"]) for m in r["months"]) or "고르지 못함",
                  " / ".join(x for x in (("수록 범위: %s" % r["range"]["text"]) if r.get("range") else "", said) if x))
        self.public_aggregates += len(r["months"])
        self.spec_items += 1
        return {"dataset_no": self.shown_no(r["dataset_no"]), "available": r["available"], "source": r["source"],
                "reference_only": r["reference_only"],
                "months": [{"ym": m["ym"], "role": m["role"], "index": m["index"],
                            "reconcilable": m["reconcilable"], "assumed": m["assumed"]} for m in r["months"]],
                "range": r["range"], "notes": r["notes"]}

    def _month_issues(self, need, items):
        """받을 달은 코드가 계절 지수로 고른 달만 받는다."""
        out = []
        for i, it in enumerate(items):
            no = it.get("dataset_no")
            if not it.get("months") or not self.cat.has(no):
                continue
            picked = set(m["ym"] for m in planner.pick_months(no, need=need, today=self.today)["months"])
            for ym in it["months"]:
                try:
                    ym_n = catalog_mod.norm_ym(ym)
                except (ValueError, TypeError):
                    continue        # 형식이 틀린 달은 검증기가 잡는다
                if picked and ym_n not in picked:
                    out.append("%d번 항목: 받을 달 %s 은 pick_months 가 고른 달(%s)이 아니다. pick_months 결과를 그대로 쓰거나 months 를 비운다."
                               % (i + 1, ym_n, ", ".join(sorted(picked))))
        return out

    def _t_submit_order(self, a, said):
        self.submits += 1
        na = a["need"]
        need = self._need_from(na)
        self.last_region_arg = (na.get("region") or "").strip()
        items = []
        col_re = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
        ym_re = re.compile(r"^\d{4}\s?[-./]?\s?\d{1,2}$")

        def col_name(c):
            c = str(c).strip()
            return c if col_re.match(c) else "형식이 틀린 컬럼"

        for it in a["items"]:
            no = self.safe_no(it["dataset_no"])
            row = {"dataset_no": no, "why": self.clean(it.get("why", ""))}
            if it.get("months"):
                row["months"] = [(str(m).strip() if ym_re.match(str(m).strip()) else "형식이 틀린 달") for m in it["months"]]
            if it.get("conditions"):
                conds = []
                for c in it["conditions"]:
                    col, val = col_name(c["column"]), c["value"].strip()
                    # 명세서의 값 목록에 있는 값은 그대로, 없는 값은 수치·수량 표현이 없을 때만 그대로 옮긴다.
                    if not (self.cat.has(no) and self.cat.value_evidence(no, col, val)["ok"]):
                        val = self.safe_word(val) or "(빈 값)"
                    conds.append({"column": col, "value": val})
                row["conditions"] = conds
            if it.get("use_columns"):
                row["use_columns"] = [col_name(c) for c in it["use_columns"]]
            items.append(row)
        draft = {"items": items}
        if a.get("choose"):
            draft["choose"] = a["choose"]
        built = planner.build_order(need, draft, today=self.today, cat=self.cat)
        extra = self._need_issues(na, need) + self._month_issues(need, items)
        issues = extra + list(built["validation"]["issues"])
        ok = built["ok"] and not extra
        v = built["validation"]
        choose_ko = {"split": "여러 종을 따로 받기", "single": "한 종으로 함께 보기"}
        args = "주문 항목 %d개(%s)%s" % (len(items), ", ".join(self.shown_no(i["dataset_no"]) for i in items) or "없음",
                                     (" · 먼저 권하는 안: " + choose_ko.get(a["choose"], "")) if a.get("choose") else "")
        self.spec_items += len(items) + len(issues)
        order = built["order"]
        summary = {
            "items": [{"dataset_no": it["dataset_no"], "dataset_name": it.get("dataset_name"),
                       "months": [m["ym"] for m in it.get("months", [])],
                       "conditions": ["%s=%s" % (c["column"], c["value"]) for c in it.get("conditions", [])],
                       "use_columns": [c["en"] for c in it.get("use_columns", [])]} for it in order["items"]],
            "alternatives": [{"key": x["key"], "datasets": [i["dataset_no"] for i in x["items"]],
                              "recommended": x["recommended"]} for x in order["alternatives"]],
            "no_data": [x["unit"] for x in order["no_data"]],
        }
        if ok:
            self.accepted = (need, built)
            self.final_step = self.step("submit_order", args, "검증 통과 %d/%d" % (v["passed"], v["total"]), said)
            return {"ok": True, "validation": "통과", "order": summary,
                    "instruction": "주문서가 확정됐다. 도구를 더 부르지 않는다."}
        if self.fixes_used < MAX_ORDER_FIXES:
            self.fixes_used += 1
            left = MAX_ORDER_FIXES - self.fixes_used
            self.step("submit_order", args, "검증기가 되돌려 보냄(어긋난 줄 %d개) → 고쳐서 다시 냄 %d/%d" % (
                len(issues), self.fixes_used, MAX_ORDER_FIXES), " / ".join(issues[:6]))
            return {"ok": False, "issues": issues[:12], "order_after_removing_failed_lines": summary,
                    "fixes_left": left,
                    "instruction": "issues 에 적힌 줄만 고쳐 submit_order 를 다시 부른다. 도구 결과에 없는 번호·컬럼·값을 쓰지 않는다. "
                                   "조건과 쓸 컬럼은 비워 두면 코드가 채운다."}
        # 고칠 기회를 다 썼다: LLM 이 읽은 목적은 살리고, 주문 항목은 규칙으로 다시 짠다.
        rule_built = planner.build_order(need, today=self.today, cat=self.cat)
        self.rule_filled = True
        self.accepted = (need, rule_built)
        self.step("submit_order", args, "검증기가 되돌려 보냄(어긋난 줄 %d개), 고칠 기회 %d번을 다 씀" % (len(issues), MAX_ORDER_FIXES),
                  " / ".join(issues[:6]))
        rv = rule_built["validation"]
        self.final_step = self.step("submit_order", "LLM 초안 대신 코드가 규칙으로 다시 짠 주문 항목 %d개" % len(rule_built["order"]["items"]),
                                    "검증 통과 %d/%d" % (rv["passed"], rv["total"]), " / ".join(rv["issues"]))
        return {"ok": False, "issues": issues[:12], "instruction": "고칠 기회를 다 썼다. 코드가 주문 항목을 다시 짰다. 도구를 더 부르지 않는다."}

    # ------------------------------------------------------------ 마무리
    def finish(self, run_id):
        need, built = self.accepted
        cat = self.cat
        order = built["order"]
        d = built["decision"]
        # LLM 이 이유를 비웠거나 숫자 문장이 빠져 빈 줄이 되면 코드의 이유 문장을 넣는다.
        rule_why = {}
        if not self.rule_filled and any(not it.get("why") for it in order["items"]):
            for it in planner.build_order(need, today=self.today, cat=cat)["order"]["items"]:
                rule_why[it["dataset_no"]] = it.get("why", "")
        for it in order["items"]:
            if self.rule_filled:
                it["why_source"] = "rule"
            elif it.get("why"):
                it["why_source"] = "llm"
            else:
                it["why"] = rule_why.get(it["dataset_no"], "")
                it["why_source"] = "rule"

        final = self.final_step
        raw_steps = [s for s in self.trace if s is not final]

        def row(tool, args, result, note="", sub=False):
            r = {"step": 0, "tool": tool, "args_summary": _one_line(args, 200),
                 "result_summary": _one_line(result, 300), "note": _one_line(note, 400)}
            if sub:
                r["sub"] = True
            return r

        def more(step, text):
            step["note"] = _one_line(" / ".join(x for x in (step.get("note", ""), text) if x), 400)

        # 같은 인자로 되풀이한 명세서 검색은 한 줄로 묶는다.
        steps = []
        for st in raw_steps:
            prev = steps[-1] if steps else None
            if (prev is not None and st["tool"] == "catalog_search" and prev["tool"] == "catalog_search"
                    and st["args_summary"] == prev["args_summary"] and st["result_summary"] == prev["result_summary"]):
                prev["_repeat"] = prev.get("_repeat", 1) + 1
                continue
            steps.append(st)
        # 최종 주문서와 다른 계열·다른 단위로 한 검색은 그렇다고 적는다(검색 결과와 주문서가 어긋나 보이지 않게).
        unit_order = ("dow", "slot", "age", "sex", "user_type", "nationality", "dow_x_slot")
        final_units = set(u["key"] for u in need["units"] if u["key"] in unit_order)
        last_search = None
        for st in steps:
            meta = st.get("_meta")
            if st.get("_repeat", 1) > 1:
                more(st, "같은 검색을 %d번 했다(한 줄로 묶음)" % st["_repeat"])
            if st["tool"] != "catalog_search" or not meta:
                continue
            if meta["subject"] != need["subject"]:
                more(st, "최종 주문서는 '%s' 계열로 정했다. 이 검색은 '%s' 계열을 본 것이라 그 후보는 쓰지 않았다(측정값이 다른 데이터다)" % (
                    need["subject_label"], planner.SUBJECT_LABELS.get(meta["subject"], meta["subject"])))
                continue
            last_search = st
            asked = set(k for k in meta["units"] if k in unit_order)
            if asked != final_units:
                wider = [cat.unit_label(k) for k in unit_order if k in asked - final_units]
                fewer = [cat.unit_label(k) for k in unit_order if k in final_units - asked]
                parts = []
                if wider:
                    parts.append("목적보다 넓혀서 찾음(더 넣은 단위: %s)" % "·".join(wider))
                if fewer:
                    parts.append("빠뜨리고 찾음(뺀 단위: %s)" % "·".join(fewer))
                more(st, "%s. 주문서는 마지막에 낸 단위(%s)로 짰고 아래 판정도 그 단위 기준이다" % (
                    ", ".join(parts), "·".join(cat.unit_label(k) for k in unit_order if k in final_units) or "월별 흐름만"))

        unit_names = "·".join(u["label"] for u in need["units"]) or "월별 흐름만"
        head = [row("해석", "LLM 이 문장을 풂", "무엇을: %s%s / 누구를: %s / 지역: %s / 필요한 단위: %s" % (
            need["subject_label"], (" · " + need["biz"]) if need["biz"] else "", need["who"], need["region"], unit_names),
            " / ".join(need["notes"]))]
        if need["region_detail"]["ambiguous"]:
            head.append(row("해석", need["region_detail"]["bjd"],
                            "같은 이름의 법정동이 두 곳: %s" % ", ".join(need["region_detail"]["ambiguous"]),
                            "조건에 권역을 함께 적는다"))
        # 버린 후보·타협 판정·없음 판정은 그 판정이 나온 명세서 검색 바로 아래에 붙인다.
        judged = []
        rej = planner.sorted_rejected(d, cat)
        for r in rej[:6]:
            judged.append(row("catalog_search", "%s %s" % (r["dataset_no"], cat.name(r["dataset_no"])), "버림", r["reason"], sub=True))
        if len(rej) > 6:
            judged.append(row("catalog_search", "그 밖의 후보 %d종" % (len(rej) - 6), "버림", "필요한 단위가 더 많이 빠진다", sub=True))
        if order["alternatives"]:
            judged.append(row("타협 판정", "필요한 단위: %s" % unit_names,
                              "한 데이터로 필요한 단위를 다 채우는 것이 없다 → 타협안 %d개를 비교" % len(order["alternatives"])))
        for alt in order["alternatives"]:
            judged.append(row("타협안 비교", alt["title"], "내려받기 %s%s" % (alt["downloads_calc"], " · 먼저 권함" if alt["recommended"] else ""),
                              "얻는 것: %s / 포기하는 것: %s" % (" · ".join(alt["gain"]) or "-", " · ".join(alt["give_up"]) or "없음"), sub=True))
        for nd in order["no_data"]:
            judged.append(row("없음 판정", nd["unit"], "메울 데이터 없음", nd["explain"]))
        tail = []
        want_src = "1048" if need["subject"] == "spend" else "520"
        if want_src not in self.pm_sources:
            tail.append(row("코드가 채움", "왼쪽 '공개 데이터로 채운 칸'",
                            "허브 %s 의 월별 추이·계절 지수·비중은 LLM 이 부르지 않아 코드가 따로 계산해 채웠다" % want_src,
                            "LLM 으로 보내지 않은 값이다"))
        if self.dropped_sentences:
            tail.append(row("글 다듬기", "LLM 이 쓴 글", "수치·수량 표현이나 금지 표현이 든 문장 %d개를 뺌" % self.dropped_sentences,
                            "수치는 코드가 계산한 도구 결과로만 보인다"))
        if last_search is not None:
            at = [i for i, st in enumerate(steps) if st is last_search][0] + 1
            body = steps[:at] + judged + steps[at:]
        else:
            body = steps + judged
        trace = head + body + tail + ([final] if final else [])
        for i, st in enumerate(trace):
            st.pop("_meta", None)
            st.pop("_repeat", None)
            st["step"] = i + 1
        sent = {"purpose_text": True, "public_aggregates": self.public_aggregates,
                "spec_items": self.spec_items, "center_values": 0}
        raw_region = self.last_region_arg if self.last_region_arg not in _NO_REGION_WORDS else ""
        resp = planner.compose_response(need, built, trace, "llm", "", sent, run_id,
                                        raw_region=self.safe_word(raw_region))
        resp["llm"] = {"model": self.cfg["model"], "turns": self.turns,
                       "input_tokens": self.input_tokens, "output_tokens": self.output_tokens}
        line = ("LLM 모드: 문장 해석과 데이터를 고른 이유는 LLM이 썼고(모델 %s), 수치·받을 달·조건·컬럼·검증은 코드가 채웠다. "
                "LLM 의 글에서 수치·수량 표현이 든 문장은 뺀다." % self.cfg["model"])
        if self.rule_filled:
            line += " 이번에는 LLM 초안이 검증기를 통과하지 못해 주문 항목을 코드가 규칙으로 다시 짰다."
        resp["limits"] = list(resp.get("limits") or []) + [line]
        return resp


def _user_message(text, slots):
    lines = ["목적 문장:", "<<<", text.strip(), ">>>"]
    s = slots or {}
    picked = []
    for k, label in (("who", "누구를"), ("what", "무엇을"), ("when", "어느 시기"), ("region", "지역")):
        v = s.get(k)
        if isinstance(v, str) and v.strip():
            picked.append("%s=%s" % (label, _one_line(v, 40)))
    if picked:
        lines.append("화면에서 함께 고른 값(참고): " + ", ".join(picked))
    lines.append("위 목적에 맞는 센터 방문 주문서를 도구로 짜서 submit_order 로 낸다.")
    return "\n".join(lines)


def plan(text, slots=None, run_id=None, today=None, cfg=None):
    """LLM 모드로 /api/plan 응답을 만든다. 쓰지 못하면 LLMError 를 던진다."""
    cfg = cfg or settings()
    if not cfg["key"]:
        raise LLMError("no_key", "ANTHROPIC_API_KEY 가 없다.",
                       "프로그램 폴더의 .env 파일(.env.example 을 복사)에 Key 를 넣고 다시 실행한다.")
    bad = _check_base(cfg)
    if bad:
        raise bad
    if not isinstance(text, str) or not text.strip():
        raise LLMError("input", "목적 문장이 비어 있다.", "문장을 적거나 '직접 고르기'를 쓴다.")
    text = text.strip()[:MAX_TEXT_CHARS]
    run = _Run(text, slots, today or datetime.date.today(), cfg)
    try:
        return _loop(run, text, slots, run_id, cfg)
    except LLMError as e:
        e.sent = _sent_before_failure(run, e.delivered)
        raise
    except Exception as e:  # 루프 안의 예상하지 못한 오류: 그때까지 보낸 내용을 실어 올린다
        err = LLMError("internal", "LLM 처리 중 예상하지 못한 오류가 났다(%s)." % type(e).__name__)
        err.sent = _sent_before_failure(run, run.requests > 0)
        raise err


def _sent_before_failure(run, delivered):
    """LLM 을 부르다 실패했을 때 그때까지 나간 내용. 도구 결과는 다음 요청에 실려 나가므로 센 값 그대로 적는다(모자라게 적지 않는다)."""
    went = run.turns > 0 or bool(delivered)
    return {"purpose_text": went, "public_aggregates": run.public_aggregates if went else 0,
            "spec_items": run.spec_items if went else 0, "center_values": 0,
            "llm_calls": run.turns, "llm_requests": run.requests}


def _loop(run, text, slots, run_id, cfg):
    body = {
        "model": cfg["model"],
        "max_tokens": MAX_TOKENS,
        # 도구 정의와 시스템 프롬프트는 턴마다 그대로 되풀이되므로 여기까지를 캐시한다(비용을 줄인다).
        "system": [{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
        "tools": tool_defs(),
        "messages": [{"role": "user", "content": _user_message(text, slots)}],
    }
    if cfg["effort"]:
        body["output_config"] = {"effort": cfg["effort"]}
    messages = body["messages"]
    deadline = time.time() + TOTAL_BUDGET
    nudged = False

    while run.accepted is None:
        if run.turns >= MAX_TURNS:
            raise LLMError("turns", "LLM 이 호출 상한 %d턴 안에 주문서를 내지 못했다." % MAX_TURNS,
                           "문장을 더 짧고 분명하게 적거나 '직접 고르기'를 쓴다.")
        run.requests += 1
        resp = call_messages(body, cfg, deadline)
        run.turns += 1
        usage = resp.get("usage") or {}
        for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            if isinstance(usage.get(k), int):
                run.input_tokens += usage[k]
        if isinstance(usage.get("output_tokens"), int):
            run.output_tokens += usage["output_tokens"]

        stop = resp.get("stop_reason")
        content = resp.get("content")
        if resp.get("type") == "error" or not isinstance(content, list):
            raise LLMError("server", "Claude API 의 응답 모양이 다르다(content 가 없다).", "API_BASE 와 MODEL 을 확인한다.")
        if stop == "refusal":
            raise LLMError("refusal", "모델이 이 요청에 답하기를 거절했다(안전 분류기).", "문장을 바꿔 다시 하거나 '직접 고르기'를 쓴다.")
        if stop == "max_tokens":
            raise LLMError("max_tokens", "모델의 응답이 길이 상한에서 잘렸다.", "다시 하거나 '직접 고르기'를 쓴다.")
        if not content:
            raise LLMError("server", "모델이 빈 응답을 돌려주었다.", "다시 하거나 '직접 고르기'를 쓴다.")
        # 응답의 content 를 그대로 붙인다(thinking 블록과 순서를 건드리지 않는다).
        messages.append({"role": "assistant", "content": content})

        tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        if not tool_uses:
            if nudged:
                raise LLMError("no_order", "LLM 이 주문서(submit_order)를 내지 않고 끝냈다.", "문장을 더 분명하게 적거나 '직접 고르기'를 쓴다.")
            nudged = True
            messages.append({"role": "user", "content": "아직 주문서를 내지 않았다. 글로 답하지 말고 submit_order 를 불러 주문서를 낸다."})
            continue

        said = run.clean(" ".join(b.get("text", "") for b in content
                                  if isinstance(b, dict) and b.get("type") == "text"), 200)
        results = []
        submitted = False
        for b in tool_uses:
            name = b.get("name")
            if run.accepted is not None or (name == "submit_order" and submitted):
                out, is_err = {"error": "주문서는 한 번에 하나만 낸다. 이 호출은 돌리지 않았다."}, True
            else:
                if name == "submit_order":
                    submitted = True
                out, is_err = run.run_tool(name, b.get("input"), said)
                said = ""
            block = {"type": "tool_result", "tool_use_id": b.get("id"),
                     "content": json.dumps(out, ensure_ascii=False, separators=(",", ":"))}
            if is_err:
                block["is_error"] = True
            results.append(block)
        # 한 응답의 도구 결과는 모두 한 user 메시지에 담는다.
        messages.append({"role": "user", "content": results})

    return run.finish(run_id)


def plan_auto(mode="auto", text="", slots=None, run_id=None, today=None):
    """서버가 부르는 입구. LLM 을 쓸 수 있으면 쓰고, 안 되면 규칙 모드로 넘기며 이유를 적는다."""
    text = text if isinstance(text, str) else ""
    slots = slots if isinstance(slots, dict) else {}
    if mode != "auto" or not text.strip():
        return planner.plan_rule(slots=slots, text=text, today=today, run_id=run_id)
    if planner.out_of_scope(text) and not planner._has_slots(slots):
        # 이 프로그램이 짝짓지 못하는 주제다. LLM 을 부르지 않고 코드가 그렇다고 답한다(억지로 짝짓지 않는다).
        return planner.plan_rule(slots=slots, text=text, today=today, run_id=run_id)
    cfg = settings()
    if not cfg["key"]:
        return planner.plan_rule(slots=slots, text=text, today=today, run_id=run_id, fallback_reason=no_key_reason())
    sent = None
    try:
        return plan(text, slots, run_id, today, cfg)
    except LLMError as e:
        reason = e.reason()
        sent = e.sent
    except Exception as e:  # 루프 밖의 예상하지 못한 오류도 화면을 죽이지 않고 규칙 모드로 넘긴다
        reason = "LLM 처리 중 예상하지 못한 오류가 났다(%s)." % type(e).__name__
    return planner.plan_rule(slots=slots, text=text, today=today, run_id=run_id, fallback_reason=reason, sent_to_llm=sent)
