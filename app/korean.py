"""한국어 조사 고르기.

화면에 나가는 문장에서 '은(는)', '(으)로' 처럼 두 꼴을 함께 적지 않도록,
앞말의 마지막 글자 받침을 보고 알맞은 조사를 고른다.
"""

_PAIRS = {
    "은": ("은", "는"), "는": ("은", "는"),
    "이": ("이", "가"), "가": ("이", "가"),
    "을": ("을", "를"), "를": ("을", "를"),
    "과": ("과", "와"), "와": ("과", "와"),
}

# 받침 없는 소리로 끝나는 숫자·영문(읽는 소리 기준)
_NO_BATCHIM_TAIL = set("2459") | set("abcdefghijkopqrsuvwxyz")
_RIEUL_TAIL = set("1780") | set("l")


def _last_char(word):
    s = str(word or "").rstrip(" '\"’”)]")
    return s[-1:] if s else ""


def _batchim(ch):
    """받침 종류: None(없음), 'ㄹ', 'other'."""
    if not ch:
        return None
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:
        jong = (code - 0xAC00) % 28
        if jong == 0:
            return None
        return "ㄹ" if jong == 8 else "other"
    low = ch.lower()
    if low in _RIEUL_TAIL:
        return "ㄹ"
    if low in _NO_BATCHIM_TAIL:
        return None
    if ch.isdigit() or low in "mn":
        return "other"
    return None


def josa(word, particle):
    """josa('노형동', '은') -> '은', josa('카페', '을') -> '를', josa('조천읍', '로') -> '으로'."""
    b = _batchim(_last_char(word))
    if particle in ("로", "으로"):
        return "로" if b in (None, "ㄹ") else "으로"
    pair = _PAIRS.get(particle)
    if not pair:
        return particle
    return pair[0] if b else pair[1]


def attach(word, particle):
    """앞말에 조사를 붙여 돌려준다. attach('노형동', '은') -> '노형동은'."""
    return "%s%s" % (word, josa(word, particle))
