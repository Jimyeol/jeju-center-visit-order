# -*- coding: utf-8 -*-
"""경로와 고정 값. 바꿀 수 있는 설정은 이 파일 한 곳에 둔다.

Key 값은 여기서 읽지도 출력하지도 않는다(LLM 설정은 agent 쪽에서 환경변수로 읽는다).
"""
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP_DIR)
DATA_DIR = os.path.join(ROOT, "data")
SPEC_DIR = os.path.join(DATA_DIR, "spec")
PUBLIC_DIR = os.path.join(DATA_DIR, "public")
SAMPLE_DIR = os.path.join(DATA_DIR, "sample_center")

SPEC_JSON = os.path.join(SPEC_DIR, "spec.json")
DIM_TAGS_JSON = os.path.join(SPEC_DIR, "dim_tags.json")
HUB_MAP_JSON = os.path.join(SPEC_DIR, "hub_center_map.json")
ALIASES_JSON = os.path.join(SPEC_DIR, "aliases.json")
SAMPLE_MANIFEST = os.path.join(SAMPLE_DIR, "manifest.json")

# 대체 공개 데이터 파일 이름 앞머리. 이 앞머리가 없는 CSV 가 data/public/ 에 있으면 실물로 본다.
PLACEHOLDER_PREFIX = "_대체_"
PLACEHOLDER_1048 = PLACEHOLDER_PREFIX + "업종별성별카드이용정보.csv"
PLACEHOLDER_520 = PLACEHOLDER_PREFIX + "제주도방문내국인유동인구추이.csv"

# 읽기 인코딩 순서: utf-8-sig 를 먼저, 실패하면 cp949.
READ_ENCODINGS = ("utf-8-sig", "cp949")

# 주문서 규칙
MAX_DOWNLOADS = 6          # 내려받기 상한(받을 종 수 × 달 수)
MONTHS_PER_ITEM = 3        # 많은 달·적은 달·평균에 가까운 달
MAX_ALTERNATIVES = 2       # 타협안은 2개까지
# '현재까지'인 데이터의 실제 최신 달은 명세서에 없다. 공개 데이터와 겹치는 달이 없을 때만
# 오늘 기준 이만큼 앞선 달까지를 수록 범위로 가정한다.
OPEN_END_LAG_MONTHS = 3

# 가리기
MASK_THRESHOLD = 10
MASK_TEXT = "10 미만"
MASK_TEXT_SECONDARY = "가림"   # 합계로 거꾸로 셈하지 못하게 함께 가린 칸

# 재분석을 지원하는 데이터(카드 v02 계열 3종)
REANALYSIS_DATASETS = ("1-08", "1-12", "1-14")

SPEC_LABEL = "명세서 {version}({date}) 기준 데이터 {datasets}종, 참고표 {ref_tables}종"

# 제주빅데이터센터 데이터 반출 정책(개정 2026-09-01)에서 옮긴 줄.
EXPORT_POLICY_SOURCE = "제주빅데이터센터 데이터 반출 정책(개정 2026. 09. 01.)"
EXPORT_NOTES_RESTRICTED = [
    "원본과 발췌(원본 형태 그대로 일부 추출)는 반출할 수 없다.",
    "원칙적으로 텍스트(csv, txt 등)와 스프레드시트(엑셀 등)는 반출할 수 없다. 응용집계(차액, 지수, 비율, 순위, 범주 등)는 수치를 넣어 반출할 수 있다.",
    "단순집계(합계, 평균값 등)는 10 미만 수치가 들어 있지 않을 때만 그림파일로 반출할 수 있다(범주처리, 삭제처리, 비식별처리).",
    "SVG·HTML 로 그린 시각화는 화면을 캡처한 그림파일(png, jpg)로만 반출할 수 있다.",
    "반출 신청 때 자료마다 출처(원본데이터 명칭)와 활용 사유를 적는다. 인용할 때는 '제주빅데이터센터, 데이터셋(이름, 기간)'으로 표기한다.",
    "영리 목적으로 쓰려면 제공기관과 협의가 필요하다.",
]
EXPORT_NOTES_OPEN = [
    "명세서 표기는 '데이터 반출 가능'이다. 반출 신청 때 출처(원본데이터 명칭)와 활용 사유를 적는다.",
    "영리 목적으로 쓰려면 제공기관과 협의가 필요하다.",
]
# 반출 정책 '각 제공처별 반출 기준'에 이름이 적힌 제공처(텍스트·엑셀 반출 원칙 불가).
EXPORT_RESTRICTED_PROVIDERS = ("비씨카드", "탐나는전", "KT", "SKT", "나이스지니데이터", "나이스지니데이타", "데이터웨이")
