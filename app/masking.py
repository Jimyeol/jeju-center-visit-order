# -*- coding: utf-8 -*-
"""10 미만 가리기와 역산 방지.

반출 정책: 단순집계(합계·건수·금액 등)는 10 미만 수치가 들어 있으면 반출할 수 없다.
- 1차 가리기: 기준 값(건수·이용자수)이 10 미만인 칸을 '10 미만'으로 바꾼다(범주처리).
- 2차 가리기: 한 줄(또는 한 열)에 가린 칸이 하나뿐이면 합계에서 빼서 거꾸로 셈할 수 있으므로,
  그 줄에서 다음으로 작은 칸을 함께 가린다. 더 가릴 칸이 없으면 그 줄의 합계를 숨긴다.
비중(응용집계)도 같은 표에 합계가 함께 보이면 거꾸로 셈할 수 있어, 기준 값이 10 미만인 칸은 같은 방식으로 가린다.
- 합계 칸(합계·줄 합계·열 합계·총계)도 결과값이므로 기준 값이 10 미만이면 숨긴다.
- 한 화면의 표는 같은 값을 나눠 보인다(시간대별 표의 값 = 행렬 표의 줄 합계). 한 표에서 가린 값이 다른 표의
  합계로 드러나지 않게, 행렬은 줄·열 합계를 숨긴 사실(row_hidden, col_hidden)을 받아 함께 맞춘다.
- audit() 는 화면에 보이는 모든 '합 = 부분의 합' 관계를 놓고, 가린 값 가운데 거꾸로 셈할 수 있는 것을 센다(0 이어야 한다).
"""
from . import config


def mask_vector(basis, threshold=None, protect_total=True, total_basis=None):
    """한 줄짜리 표. basis: 칸마다의 기준 값(건수 등). total_basis: 합계 칸의 기준 값(주면 10 미만일 때 합계를 숨긴다).

    돌려주는 값: {"mask": [0|1|2…], "hide_total": bool, "primary": n, "secondary": n}
      mask 0 = 그대로, 1 = 10 미만이라 가림, 2 = 역산 방지로 함께 가림
    """
    th = config.MASK_THRESHOLD if threshold is None else threshold
    mask = [1 if (b is not None and b < th) else 0 for b in basis]
    hide_total = total_basis is not None and total_basis < th
    secondary = 0
    if protect_total and not hide_total and sum(1 for m in mask if m) == 1:
        rest = [i for i, m in enumerate(mask) if not m and basis[i] is not None]
        if rest:
            j = min(rest, key=lambda i: (basis[i], i))
            mask[j] = 2
            secondary = 1
        else:
            hide_total = True
    return {"mask": mask, "hide_total": hide_total,
            "primary": sum(1 for m in mask if m == 1), "secondary": secondary}


def mask_matrix(basis, threshold=None, row_hidden=None, col_hidden=None):
    """행×열 표. basis[r][c]: 칸마다의 기준 값.

    줄 합계와 열 합계가 함께 보인다고 보고, 줄마다·열마다 '가린 것'(가린 칸 + 숨긴 합계)이 0개이거나 2개 이상이 되게 한다.
    row_hidden / col_hidden: 다른 표(한 줄짜리 표)에서 이미 가려서 이 표에서도 숨겨야 하는 줄·열 합계.
      합계만 숨기고 칸이 모두 보이면 칸을 더해 합계를 알 수 있으므로, 그 줄(열)에서 가장 작은 칸을 함께 가린다.
    돌려주는 값: {"mask": [[0|1|2]], "hide_row_total": [bool], "hide_col_total": [bool], "primary": n, "secondary": n}
    """
    th = config.MASK_THRESHOLD if threshold is None else threshold
    nr = len(basis)
    nc = len(basis[0]) if nr else 0
    mask = [[1 if (basis[r][c] is not None and basis[r][c] < th) else 0 for c in range(nc)] for r in range(nr)]
    hide_row = [bool(x) for x in (row_hidden or [False] * nr)]
    hide_col = [bool(x) for x in (col_hidden or [False] * nc)]
    changed = True
    guard = 0
    while changed and guard < 1000:
        changed = False
        guard += 1
        for r in range(nr):
            n_mask = sum(1 for c in range(nc) if mask[r][c])
            if n_mask + (1 if hide_row[r] else 0) != 1:
                continue
            rest = [c for c in range(nc) if not mask[r][c] and basis[r][c] is not None]
            if rest:
                # 이미 가린 칸이 있는 열을 먼저 고르면 더 가릴 칸이 줄어든다.
                j = min(rest, key=lambda c: (0 if (hide_col[c] or any(mask[x][c] for x in range(nr))) else 1, basis[r][c], c))
                mask[r][j] = 2
            elif not hide_row[r]:
                hide_row[r] = True
            else:
                continue
            changed = True
        for c in range(nc):
            n_mask = sum(1 for r in range(nr) if mask[r][c])
            if n_mask + (1 if hide_col[c] else 0) != 1:
                continue
            rest = [r for r in range(nr) if not mask[r][c] and basis[r][c] is not None]
            if rest:
                i = min(rest, key=lambda r: (0 if (hide_row[r] or any(mask[r][x] for x in range(nc))) else 1, basis[r][c], r))
                mask[i][c] = 2
            elif not hide_col[c]:
                hide_col[c] = True
            else:
                continue
            changed = True
    primary = sum(1 for r in range(nr) for c in range(nc) if mask[r][c] == 1)
    secondary = sum(1 for r in range(nr) for c in range(nc) if mask[r][c] == 2)
    return {"mask": mask, "hide_row_total": hide_row, "hide_col_total": hide_col,
            "primary": primary, "secondary": secondary}


def cell_text(value, flag, formatter=None):
    """가리기 표시에 따라 칸에 보일 글자."""
    if flag == 1:
        return config.MASK_TEXT
    if flag == 2:
        return config.MASK_TEXT_SECONDARY
    if value is None:
        return "-"
    return formatter(value) if formatter else value


def recoverable_cells(mask, hide_row_total, hide_col_total, hide_grand=None):
    """가린 뒤에도 합계에서 빼서 알아낼 수 있는 값의 수(0 이어야 한다). 시험·지표용.

    - 가린 칸이 줄(열)에 하나뿐인데 그 줄(열) 합계가 보이면 그 칸
    - 줄(열) 합계를 숨겼는데 그 줄(열)의 칸이 모두 보이면 그 합계
    - hide_grand 를 주면(총계를 숨겼는지): 숨긴 줄(열) 합계가 하나뿐인데 총계가 보이면 그 합계,
      총계를 숨겼는데 줄(열) 합계가 모두 보이면 그 총계
    """
    nr = len(mask)
    nc = len(mask[0]) if nr else 0
    n = 0
    for r in range(nr):
        for c in range(nc):
            if not mask[r][c]:
                continue
            row_alone = (not hide_row_total[r]) and sum(1 for x in range(nc) if mask[r][x]) == 1
            col_alone = (not hide_col_total[c]) and sum(1 for x in range(nr) if mask[x][c]) == 1
            if row_alone or col_alone:
                n += 1
    for r in range(nr):
        if hide_row_total[r] and nc and not any(mask[r][c] for c in range(nc)):
            n += 1
    for c in range(nc):
        if hide_col_total[c] and nr and not any(mask[r][c] for r in range(nr)):
            n += 1
    if hide_grand is not None:
        for hidden in (hide_row_total, hide_col_total):
            k = sum(1 for x in hidden if x)
            if (not hide_grand and k == 1) or (hide_grand and hidden and k == 0):
                n += 1
    return n


def audit(relations, hidden):
    """화면 전체의 역산 점검. relations: [(합계 id, [부분 id…])], hidden: 가리거나 숨긴 값의 id 집합.

    '합 = 부분의 합' 관계에서 모르는 값이 하나뿐이면 그 값을 알아낼 수 있다. 알아낸 값을 아는 값으로 바꿔 가며
    더 알아낼 수 있는 값이 없을 때까지 되풀이하고, 알아낸 값의 수를 돌려준다(0 이어야 한다).
    """
    unknown = set(hidden)
    found = 0
    changed = True
    while changed:
        changed = False
        for total, members in relations:
            u = [i for i in [total] + list(members) if i in unknown]
            if len(u) == 1:
                unknown.discard(u[0])
                found += 1
                changed = True
    return found
