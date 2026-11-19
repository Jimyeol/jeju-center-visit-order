# -*- coding: utf-8 -*-
"""문서(.md)를 더블클릭으로 읽을 수 있는 HTML 로 바꾼다. 표준 라이브러리만 쓴다.

마크다운 원문은 Windows 에서 더블클릭하면 메모장에 표가 세로줄 기호로 풀려 나온다. 그래서 같은 내용을
외부 자원이 하나도 없는 HTML(글꼴·그림·스크립트 링크 0개)로도 넣는다. 화면의 "사용 설명 열기"는 web/manual.html 을 연다.

    python3 tools/make_docs_html.py           README.html, docs/*.html, web/manual.html 을 다시 만든다
    python3 tools/make_docs_html.py --check   지금 있는 HTML 이 .md 와 맞는지만 본다(다르면 종료 코드 1)

다루는 문법: 제목(#), 문단, 목록(-, 1.), 표(|), 코드 블록(```), 인용(>), 가로줄(---), `코드`, **굵게**, [글](주소).
"""
import html
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (원문, 만들 파일들)
DOCS = [
    ("README.md", ["README.html"]),
    (os.path.join("docs", "사용자_매뉴얼.md"), [os.path.join("docs", "사용자_매뉴얼.html"), os.path.join("web", "manual.html")]),
    (os.path.join("docs", "데이터_활용_명세.md"), [os.path.join("docs", "데이터_활용_명세.html")]),
    (os.path.join("docs", "결과_설명자료.md"), [os.path.join("docs", "결과_설명자료.html")]),
]

CSS = """
body { font-family: "Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
       line-height: 1.6; color: #1c1c1c; background: #fff; max-width: 60em; margin: 0 auto; padding: 1.5em 1.2em 4em; }
h1 { font-size: 1.7em; border-bottom: 2px solid #1c1c1c; padding-bottom: .2em; }
h2 { font-size: 1.3em; margin-top: 2em; border-bottom: 1px solid #999; padding-bottom: .15em; }
h3 { font-size: 1.1em; margin-top: 1.6em; }
h4 { font-size: 1em; margin-top: 1.3em; }
table { border-collapse: collapse; margin: .8em 0; width: 100%; }
th, td { border: 1px solid #888; padding: .3em .6em; vertical-align: top; text-align: left; }
th { background: #eee; }
code { font-family: Consolas, "D2Coding", Menlo, monospace; background: #f1f1f1; padding: 0 .25em; font-size: .93em; }
pre { background: #f1f1f1; border: 1px solid #ccc; padding: .7em 1em; overflow-x: auto; }
pre code { background: none; padding: 0; }
blockquote { margin: .8em 0; padding: .1em 1em; border-left: 4px solid #999; background: #f7f7f7; }
hr { border: 0; border-top: 1px solid #999; margin: 1.6em 0; }
ul, ol { padding-left: 1.6em; }
a { color: #0b4f9e; }
.made { color: #555; font-size: .85em; margin-top: 3em; border-top: 1px solid #ccc; padding-top: .5em; }
@media print { body { max-width: none; padding: 0; font-size: 10pt; } a { color: inherit; text-decoration: none; } pre, table, blockquote { break-inside: avoid; } }
""".strip()

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_LIST = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def inline(text):
    """한 줄 안의 문법. 코드 조각은 그 안의 기호를 건드리지 않게 먼저 떼어 둔다."""
    codes = []

    def keep(m):
        codes.append(m.group(1))
        return "\x00%d\x00" % (len(codes) - 1)

    text = _CODE.sub(keep, text)
    text = html.escape(text, quote=False)

    def link(m):
        label, url = m.group(1), m.group(2)
        if not re.match(r"^[a-z]+:", url) and url.endswith(".md"):
            url = url[:-3] + ".html"
        return '<a href="%s">%s</a>' % (html.escape(html.unescape(url), quote=True), label)

    text = _LINK.sub(link, text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    return re.sub("\x00(\\d+)\x00", lambda m: "<code>%s</code>" % html.escape(codes[int(m.group(1))], quote=False), text)


def split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells, buf, in_code = [], "", False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "`":
            in_code = not in_code
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            buf += "|"
            i += 2
            continue
        if ch == "|" and not in_code:
            cells.append(buf.strip())
            buf = ""
        else:
            buf += ch
        i += 1
    cells.append(buf.strip())
    return cells


def to_html(md):
    """마크다운 글 → (본문 HTML, 첫 제목)."""
    lines = md.replace("\r\n", "\n").split("\n")
    out = []
    title = ""
    i = 0
    n = len(lines)

    def close_lists(stack, upto=0):
        while len(stack) > upto:
            out.append("</li></%s>" % stack.pop()[1])

    stack = []      # [(들여쓰기, "ul"|"ol")]
    para = []

    def flush_para():
        if para:
            out.append("<p>%s</p>" % "<br>\n".join(inline(x) for x in para))
            del para[:]

    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_para()
            close_lists(stack)
            body = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(body), quote=False))
            continue
        if not stripped:
            flush_para()
            # 빈 줄 뒤에 목록이 이어지면 닫지 않는다.
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if not (j < n and (_LIST.match(lines[j]) or (stack and lines[j].startswith("  ")))):
                close_lists(stack)
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m and not line.startswith(" "):
            flush_para()
            close_lists(stack)
            level = len(m.group(1))
            text = m.group(2).rstrip("#").strip()
            if not title and level == 1:
                title = text
            out.append("<h%d>%s</h%d>" % (level, inline(text), level))
            i += 1
            continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush_para()
            close_lists(stack)
            out.append("<hr>")
            i += 1
            continue
        if "|" in stripped and i + 1 < n and _TABLE_SEP.match(lines[i + 1]) and "|" in lines[i + 1]:
            flush_para()
            close_lists(stack)
            head = split_row(stripped)
            i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(split_row(lines[i]))
                i += 1
            out.append("<table><thead><tr>%s</tr></thead><tbody>" % "".join("<th>%s</th>" % inline(c) for c in head))
            for r in rows:
                r = (r + [""] * len(head))[:max(len(head), len(r))]
                out.append("<tr>%s</tr>" % "".join("<td>%s</td>" % inline(c) for c in r))
            out.append("</tbody></table>")
            continue
        if stripped.startswith(">"):
            flush_para()
            close_lists(stack)
            body = []
            while i < n and lines[i].strip().startswith(">"):
                body.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote><p>%s</p></blockquote>" % "<br>\n".join(inline(x) for x in body if x))
            continue
        m = _LIST.match(line)
        if m:
            flush_para()
            indent = len(m.group(1).replace("\t", "    "))
            kind = "ol" if m.group(2)[0].isdigit() else "ul"
            while stack and stack[-1][0] > indent:
                out.append("</li></%s>" % stack.pop()[1])
            if stack and stack[-1][0] == indent:
                if stack[-1][1] != kind:
                    out.append("</li></%s>" % stack.pop()[1])
                    out.append("<%s>" % kind)
                    stack.append((indent, kind))
                else:
                    out.append("</li>")
            else:
                out.append("<%s>" % kind)
                stack.append((indent, kind))
            out.append("<li>%s" % inline(m.group(3)))
            i += 1
            continue
        if stack and line.startswith("  "):
            out.append("<br>\n" + inline(stripped))      # 목록 항목에 이어지는 줄
            i += 1
            continue
        close_lists(stack)
        para.append(stripped)
        i += 1
    flush_para()
    close_lists(stack)
    return "\n".join(out), title


def page(md, source):
    body, title = to_html(md)
    return ("<!DOCTYPE html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>%s</title>\n<style>\n%s\n</style>\n</head>\n<body>\n%s\n"
            "<p class=\"made\">이 파일은 %s 를 tools/make_docs_html.py 로 바꾼 것입니다. 내용은 같습니다.</p>\n"
            "</body>\n</html>\n") % (html.escape(title or "문서", quote=False), CSS, body, html.escape(source.replace(os.sep, "/"), quote=False))


def build():
    """{만들 파일의 상대 경로: 내용}"""
    made = {}
    for src, outs in DOCS:
        with open(os.path.join(ROOT, src), "r", encoding="utf-8") as f:
            text = page(f.read(), src)
        for o in outs:
            made[o] = text
    return made


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    made = build()
    if "--check" in argv:
        stale = []
        for rel, text in sorted(made.items()):
            try:
                with open(os.path.join(ROOT, rel), "r", encoding="utf-8", newline="") as f:
                    same = f.read() == text
            except OSError:
                same = False
            if not same:
                stale.append(rel)
        if stale:
            print("문서 HTML 이 .md 와 다르다: %s" % ", ".join(stale))
            print("python3 tools/make_docs_html.py 로 다시 만든다.")
            return 1
        print("문서 HTML %d개가 .md 와 같음" % len(made))
        return 0
    for rel, text in sorted(made.items()):
        with open(os.path.join(ROOT, rel), "w", encoding="utf-8", newline="") as f:
            f.write(text)
        print("만듦: %s (%d바이트)" % (rel.replace(os.sep, "/"), len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
