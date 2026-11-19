# -*- coding: utf-8 -*-
"""시험 공통: 프로젝트 폴더를 import 경로에 넣고, 날짜를 고정한다."""
import datetime
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TODAY = datetime.date(2026, 11, 17)

S1 = "노형동에 저녁 장사 위주로 한식당을 열려고 합니다. 어느 시간대에 어떤 손님이 많은지 알고 싶어요."
S2 = "토요일 점심에 애월읍 카페에 20대가 얼마나 오는지, 요일과 시간대를 함께 보고 싶습니다."
S3 = "성산읍에 오는 외국인 관광객을 연령대별, 시간대별로 알고 싶습니다."
