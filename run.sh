#!/bin/sh
# 센터 방문 주문서 - macOS / Linux 실행 스크립트.
# 파이썬 3.9 이상만 있으면 된다. 따로 설치하는 패키지는 없다.
cd "$(dirname "$0")" || exit 1

export PYTHONUTF8=1
export PYTHONDONTWRITEBYTECODE=1

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 &&
     "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$c"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "파이썬 3.9 이상을 찾지 못했습니다. 파이썬 3 을 설치한 뒤 다시 실행하십시오."
  echo "Python 3.9 or newer was not found."
  exit 1
fi

# 브라우저가 스스로 열린다. 끝내려면 이 창에서 Ctrl+C 를 누르거나 창을 닫는다.
exec "$PY" -m app.server "$@"
