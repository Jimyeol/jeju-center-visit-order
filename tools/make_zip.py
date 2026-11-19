# -*- coding: utf-8 -*-
"""제출용 zip 을 묶는다. 표준 라이브러리(zipfile)만 쓴다.

맥의 Finder 나 ditto 로 묶으면 한글 파일 이름이 자모로 풀리거나(NFD) ._이름 같은 딸린 파일이 섞일 수 있다.
이 스크립트는 이름을 NFC 로 맞춰 UTF-8 표시(0x800)와 함께 넣고, 개발용 문서와 실행 찌꺼기를 뺀 뒤, 묶은 것을 다시 열어 확인한다.

    python3 tools/make_zip.py 나올파일.zip                 프로그램 폴더를 center-order/ 아래로 묶는다
    python3 tools/make_zip.py 나올파일.zip --env 파일      그 파일을 center-order/.env 로 함께 넣는다(평가용 Key. 내용은 출력하지 않는다)

빼는 것: HANDOFF.md, HANDOFF_근거.json(개발용 내부 문서), __pycache__, *.pyc, runtime/, .env, .DS_Store, ._*, __MACOSX, .git, .claude
"""
import argparse
import os
import sys
import unicodedata
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOP = "center-order"

SKIP_DIRS = ("__pycache__", "runtime", "__MACOSX", ".git", ".claude", ".idea", ".vscode")
SKIP_FILES = ("HANDOFF.md", "HANDOFF_근거.json", ".env", ".DS_Store", "Thumbs.db")
SKIP_SUFFIX = (".pyc", ".pyo")


def nfc(s):
    return unicodedata.normalize("NFC", s)


def wanted(rel_parts):
    name = nfc(rel_parts[-1])
    if any(nfc(p) in SKIP_DIRS for p in rel_parts[:-1]):
        return False
    if name in SKIP_FILES or name in SKIP_DIRS or name.startswith("._") or name.startswith("~$"):
        return False
    return not name.endswith(SKIP_SUFFIX)


def collect():
    """[(디스크 경로, zip 안 이름)] 이름순."""
    out = []
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if nfc(d) not in SKIP_DIRS)
        for f in sorted(files):
            full = os.path.join(folder, f)
            rel = os.path.relpath(full, ROOT).split(os.sep)
            if wanted(rel):
                out.append((full, "/".join([TOP] + [nfc(p) for p in rel])))
    return out


def verify(path, expect_env):
    """묶은 zip 을 다시 열어 본다. 문제 목록을 돌려준다(없으면 빈 목록)."""
    problems = []
    with zipfile.ZipFile(path, "r") as z:
        if z.testzip() is not None:
            problems.append("깨진 항목이 있다: %s" % z.testzip())
        for info in z.infolist():
            n = info.filename
            if not unicodedata.is_normalized("NFC", n):
                problems.append("이름이 NFC 가 아니다: %r" % n)
            if any(ord(ch) > 127 for ch in n) and not (info.flag_bits & 0x800):
                problems.append("한글 이름에 UTF-8 표시가 없다: %s" % n)
            parts = n.split("/")
            base = parts[-1]
            if (any(p in SKIP_DIRS for p in parts) or base.startswith("._") or base in (".DS_Store", "HANDOFF.md", "HANDOFF_근거.json")
                    or base.endswith(SKIP_SUFFIX)):
                problems.append("빼야 할 것이 들어 있다: %s" % n)
            if any(ch in '<>:"|?*' for ch in n) or len(n) > 200:
                problems.append("Windows 에서 풀 수 없는 이름이다: %s" % n)
        names = z.namelist()
        envs = [n for n in names if n.split("/")[-1] == ".env"]
        if expect_env and envs != [TOP + "/.env"]:
            problems.append(".env 가 한 개(%s/.env)여야 한다: %s" % (TOP, envs))
        if not expect_env and envs:
            problems.append(".env 가 들어 있다: %s" % envs)
        for must in ("run.bat", "run.sh", "README.md", "app/server.py", "web/index.html", "data/spec/spec.json"):
            if TOP + "/" + must not in names:
                problems.append("꼭 있어야 할 파일이 없다: %s" % must)
    return problems


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="센터 방문 주문서 - 제출용 zip 묶기")
    ap.add_argument("out", help="만들 zip 파일 경로")
    ap.add_argument("--env", default=None, help="center-order/.env 로 넣을 파일(평가용 Key). 내용은 출력하지 않는다")
    args = ap.parse_args(argv)
    out = os.path.abspath(args.out)
    if os.path.commonpath([out, ROOT]) == ROOT:
        print("zip 은 프로그램 폴더 밖에 만든다(안에 만들면 다음에 묶을 때 함께 들어간다): %s" % out)
        return 1
    if args.env and not os.path.isfile(args.env):
        print(".env 로 넣을 파일이 없다: %s" % args.env)
        return 1
    files = collect()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for full, name in files:
            z.write(full, name)
        if args.env:
            z.write(args.env, TOP + "/.env")
    problems = verify(out, bool(args.env))
    with zipfile.ZipFile(out, "r") as z:
        names = z.namelist()
    tops = {}
    for n in names:
        key = n.split("/")[1] if n.count("/") >= 1 else n
        tops[key] = tops.get(key, 0) + 1
    print("만듦: %s (%s바이트, 파일 %d개)" % (out, format(os.path.getsize(out), ","), len(names)))
    for k in sorted(tops):
        print("  %s%s" % (k, (" (%d개)" % tops[k]) if tops[k] > 1 else ""))
    hangul = [n for n in names if any(ord(ch) > 127 for ch in n)]
    print("한글 이름 %d개: 모두 NFC, UTF-8 표시 있음" % len(hangul) if not problems else "확인에서 걸린 것 %d개:" % len(problems))
    for p in problems:
        print("  - " + p)
    print(".env: %s" % ("넣음(%s/.env)" % TOP if args.env else "넣지 않음"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
