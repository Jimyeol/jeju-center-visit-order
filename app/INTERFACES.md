# 코어 함수 사용법 (서버·Agent 담당용)

코어는 표준 라이브러리만 쓴다. 아래 함수는 모두 예외를 던지지 않도록 만들었고(입력이 틀리면 결과 안에 이유를 담는다), 수치는 전부 코드가 계산한다. 시험은 `python3 -m unittest discover -s tests -v`.

```python
from app import catalog, public_data, planner, validator, center_csv, masking, config
```

## 1. API 와 함수의 짝

| API | 부를 함수 | 비고 |
|---|---|---|
| `GET /api/status` 의 `spec` | `catalog.get().counts()` | `{"version","date","datasets","ref_tables","columns"}` 그대로 |
| `GET /api/status` 의 `public` | `public_data.get().status()` | `{"d1048": {"rows","first","last","placeholder","file","error"}, "d520": {...}}` |
| 대체 데이터 사용 중인가 | `public_data.get().placeholder` | True 면 화면 맨 위에 "대체 데이터 사용 중" |
| `GET /api/options` | `planner.options()` | 아래 2절 |
| `POST /api/plan` (규칙 모드) | `planner.plan_rule(slots=..., text=..., run_id=..., fallback_reason=...)` | 응답 전체를 만든다 |
| `POST /api/plan` (LLM 모드) | 도구 5개 + `planner.make_need` + `planner.build_order` + `planner.compose_response` | 아래 4절 |
| `POST /api/center-csv` | `center_csv.analyze(path, filename, request=need, dataset_no=None)` | 아래 5절 |

- `plan_rule` 은 `slots` 에 내용이 있으면 세 칸 선택으로, 없고 `text` 가 있으면 문장을 낱말 사전으로 푼다. 둘 다 비어도 죽지 않는다(도 전체 흐름만 낸다).
- `run_id` 를 주지 않으면 12자리 값을 만들어 넣는다. 서버는 `run_id → 응답["need"]` 를 기억해 두었다가 CSV 를 받을 때 `request=need` 로 넘긴다.
- 시험에서 날짜를 고정하려면 `plan_rule(..., today=datetime.date(2026, 11, 17))`.

## 2. `/api/options` 모양

```json
{"who":   [{"key": "all", "label": "전체 이용자"}, {"key": "resident", ...}, {"key": "domestic", ...}, {"key": "foreign", ...}],
 "what":  [{"key": "pop", "label": "방문·유동인구"}, {"key": "biz:I56111", "label": "한식 음식점업"}, ... 42개 업종],
 "when":  [{"key": "month", "label": "월별 흐름만"}, {"key": "dow", ...}, {"key": "slot", ...}, {"key": "dow_slot", "label": "요일과 시간대를 함께"}],
 "regions": {"bjd": ["강정동", ... 73개], "emd": ["제주시 한림읍", ... 43개], "district7": [... 7개]},
 "units": [{"key": "age", "label": "연령대"}, {"key": "sex", ...}, {"key": "user_type", ...}, {"key": "nationality", ...}],
 "focus": {"dow": ["월요일", ...], "slot": ["오전", ...], "age": ["10대", ...]},
 "examples": [{"title": "...", "text": "...", "slots": {...}}]}
```

`slots` 는 `{"who": 열쇠, "what": 열쇠, "when": 열쇠, "region": 지역 이름 또는 "", "units": [열쇠…], "focus": {"age": ["20대"], ...}}`. `focus`(눈여겨볼 값)는 없어도 된다. 없으면 연령대는 20대~60대를 각각 따로 볼 수 있어야 채워진 것으로 본다(`dim_tags.json` 의 `need_units.age.core_values`). 그래서 화면의 세 칸 선택만으로도 시나리오 2의 두 안이 나온다.

## 3. `/api/plan` 응답에서 계약에 더한 것

계약(HANDOFF.md)의 이름은 그대로다. 아래는 더 들어 있는 칸이며, 쓰지 않아도 된다.

- `need`: 계약의 `region, biz, who, units` 에 더해 `subject("spend"|"visit")`, `subject_label`, `who_key`, `region_detail`, `biz_detail({"code","name","alias","basis","pos"})`, `focus`, `cross`, `center_needed`, `notes`.
- `need.units[].key`: `dong`(읍면동 단위 지역), `district7`, `city`, `biz`, `who`, `dow`, `slot`, `age`, `sex`, `user_type`, `nationality`, `dow_x_slot`(요일과 시간대를 한 표에서 함께).
- `public_answer`: `season_months`(많은 달·적은 달·평균에 가까운 달), 외국인 목적일 때 `reference`(내국인 계절 지수, 참고값).
  `available` 이 false 면 `series`·`seasonal`·`shares` 는 빈 목록이고 `reason` 에 이유가 있다.
- `gaps[]`: `by`(메우는 데이터 번호 목록), `note`.
- `order.items[]`: 계약의 칸에 더해 `focus`(눈여겨볼 값을 이 데이터에서 어떻게 고르는지), `caveat`, `month_notes`, `reanalysis`(다시 돌리기 지원 여부).
  - `conditions[]`: `value_label`(코드의 이름) 이 있을 수 있다. 같은 컬럼이 여러 줄이면 `evidence.note` 에 "둘 가운데 하나만" 또는 "함께 고른다"가 적혀 있다.
  - `months[]`: `{"ym": "2024-08", "role": "많은 달", "basis": "…"}`.
  - `export`: `{"allowed": bool, "notes": [문장…]}`.
  - `evidence`: 문장 목록(수록 기간·컬럼·제공처의 명세서 근거).
- `order.alternatives[]`: `{"title", "key": "split"|"single", "items": [주문 항목과 같은 모양…], "downloads": 정수, "downloads_calc": "2종 × 3달 = 6회", "gain": [문장…], "give_up": [문장…], "give_up_keys": [...], "recommended": bool}`.
  타협이 필요 없으면 빈 목록이다. 타협일 때 `order.items` 는 먼저 권한 안의 항목이다.
- `order.no_data[]`: `{"unit": "연령대", "key": "age", "explain": "…", "instead": [문장…]}`.
- `order.note`: 센터에서 받을 것이 없을 때만.
- `validation.issues`: 문장 목록.
- `spec_label`("명세서 v13(2026-11-04) 기준 데이터 71종, 참고표 2종"), `placeholder`(bool).

## 4. LLM 도구 5개가 부를 함수

모두 JSON 으로 바꿀 수 있는 dict 를 돌려준다. 도구 입력의 형식 검사는 Agent 쪽에서 한다.

### public_scope()
입력 없음. 출력 `{"d1048": {"name","available","placeholder","grain","units","missing_units","period":{"first","last"},"values":{"biz":[…],"user_type":[…],"sex":[…]},"metrics","notes"}, "d520": {...}, "placeholder": bool, "notice"}`.

### public_monthly(source="1048", biz=None, who="all", sex=None, age=None, metric=None)
- `source`: `"1048"` 또는 `"520"`. `biz`: 업종코드·업종명·낱말(카페). `who`: all|resident|domestic|foreign. `age`: "20대" 꼴(520 만). `metric`: use_amt|use_cnt|user_cnt(1048 만).
- 출력 `{"available","reason","source","metric","series":[{"ym","value"}],"seasonal":[{"month","index"}],"season_months":[{"month","role","index"}],"shares":[{"title","rows":[{"label","pct"}]}],"notes":[…]}`.
- 520 은 `seasonal` 이 2022-07 이후 구간이고 `seasonal_before_split` 이 그 앞 구간이다.
- 1048 에서 달이 비는 업종(버스 운송업, 택시 운송업, 기타 갬블링 및 베팅업)은 `available` 은 true 지만 `seasonal`·`season_months` 가 빈 목록이고 `notes` 에 "자료가 모자람"이 적힌다. 이때 `pick_months` 는 업종 합계의 계절 지수로 달을 고르고 그렇게 적는다.

### catalog_search(units, subject=None, region=None, biz=None, who="all", focus=None, cross=False, limit=8)
- `units`: `["dow","slot","age","sex","user_type","nationality"]` 가운데 필요한 것. `subject`: spend|visit(비우면 biz 가 있으면 spend). `region`: 지역 이름. `focus`: `{"age": ["20대"]}`. `cross`: 요일과 시간대를 한 표에서 봐야 하면 True.
- 출력 `{"need_units":[…], "subject", "candidates":[{"dataset_no","dataset_name","covers":[…],"missing":[…],"missing_notes":[…],"value_gaps":[…],"period_text","export","provider","full":bool,"chosen":bool}], "no_data":[단위 이름…], "mode":"single"|"compromise"|"none"|"not_needed", "suggestion":{"mode","datasets":[…],"alternatives":[…]}, "rejected":[{"dataset_no","dataset_name","reason"}], "counted"}`.

### pick_months(dataset_no, biz=None, who="all", subject=None, today=None, count=None, need=None)
- 출력 `{"dataset_no","available","source","reference_only":bool,"months":[{"ym","role","basis","index","reconcilable","assumed"}],"seasonal":[…],"range":{"start","end","open_ended","text"},"notes":[…]}`.
- 연도 규칙: (1) 대상 데이터의 수록 범위와 공개 데이터 기간이 겹치면 그 범위에서 가장 최근 해(공개 값과 대조할 수 있다, `reconcilable=true`). (2) 겹치는 달이 없으면 수록 범위 안에서 가장 최근 해. 끝이 "현재까지"면 오늘 기준 `config.OPEN_END_LAG_MONTHS` 개월 앞선 달까지 있다고 가정하고 `assumed=true` 로 표시한다.

### submit_order → build_order(need, draft=None, today=None, max_downloads=None)
1. LLM 이 푼 목적을 `need = planner.make_need(subject=..., region=..., biz=..., who=..., units=[...], focus={...}, cross=bool, text=원문, source="llm")` 로 만든다.
2. 초안을 `draft = {"items": [{"dataset_no": "1-14", "months": ["2024-08", ...], "conditions": [{"column","value"}], "use_columns": ["time", ...], "why": "이유 문장"}], "choose": "split"|"single"}` 로 넘긴다. `months`·`conditions`·`use_columns` 를 비우면 코드가 채운다. `why` 만 LLM 의 글이다(숫자는 넣지 않게 한다).
3. 출력 `{"order": 통과한 줄만 남긴 주문서, "validation": {"passed","total","issues":[문장…]}, "issues": [{"item","alt","field","ref","message"}], "ok": bool, "decision": {...}}`.
   - `ok` 가 false 면 `validation.issues` 를 도구 결과로 돌려주고 LLM 이 고쳐서 다시 낸다(최대 2회). 마지막에도 false 면 `order`(어긋난 줄을 뺀 것)를 그대로 쓰거나 규칙 모드로 넘긴다.
   - 검사하는 것: 데이터 번호·이름이 명세서에 있는가, 달이 수록 범위 안인가, 조건의 컬럼과 값이 명세서에 있는가(파티션 컬럼은 안 됨), 쓸 컬럼이 있는가, 내려받기 횟수가 상한(기본 6) 안인가, 필요한 단위마다 채우는 항목이나 "메울 데이터 없음" 표시가 있는가.
4. 응답 조립: `planner.compose_response(need, built, trace=[{"step","tool","args_summary","result_summary","note"}…], mode_used="llm", fallback_reason="", sent_to_llm={"purpose_text": True, "public_aggregates": n, "spec_items": n, "center_values": 0}, run_id=...)`.
   `built` 은 `build_order` 의 출력이다. `public_answer`·`gaps`·`limits` 는 이 함수가 코드로 채운다.

`validator.validate_order(order, need)` 는 어긋난 줄 목록만 돌려준다(`build_order` 가 안에서 부른다).

## 5. `center_csv.analyze(path, filename=None, request=None, dataset_no=None)`

- `path`: 서버가 임시로 저장한 파일. `request`: `/api/plan` 응답의 `need`(없으면 파일 전체로 집계). `dataset_no`: `recognized.ask` 에 답한 번호.
- 출력은 계약의 `/api/center-csv` 모양에 `error`, `scope` 를 더한 것이다.
  - `error`: 문제가 없으면 `None`, 있으면 `{"message": 원인, "action": 조치}`. 서버는 이것이 있으면 HTTP 400 과 `{"error": …}` 로 응답하면 된다(같은 문장이 `diagnostics` 에도 `level: "error"` 로 들어 있다).
  - `recognized.ask`: 헤더가 같은 데이터가 여럿이면 `[{"dataset_no","dataset_name"}…]` 이고 `tables` 는 비어 있다. 사용자가 고르면 `dataset_no` 를 넣어 다시 부른다.
  - `diagnostics[].level`: `ok | info | warn | ask | error`.
  - `tables[]`: `{"title", "kind": "응용집계"|"단순집계", "columns": [문자열…], "rows": [[칸…]…], "masked_cells": 정수, "note"}`. 첫 칸(이름)은 문자열이고, 값 칸은 보이는 값이면 숫자(건수·금액은 정수, 비중은 소수 한 자리), 가린 칸이면 문자열이다: `"10 미만"`(기준 값이 10 미만), `"가림"`(역산 방지로 함께 가림), `"숨김"`(숨긴 합계). 단순집계 표의 마지막 줄은 `"합계"` 줄이다.
  - 표 순서: 응용집계(시간대 또는 요일별 비중 → 이용자구분별 → 연령대별 → 법정동별 비중과 순위) 다음에 단순집계. 가리기 기준 값은 그 칸의 이용건수 합·이용금액 합·이용자수 합(0 이면 뺌) 가운데 가장 작은 값이다. 건수만 보이는 교차표의 칸은 이용자수 합과 이용건수 합 가운데 작은 쪽이다.
  - `reconcile`: `{"available", "public_value", "center_sum", "diff_pct", "note"}`. 허브 1048 의 같은 달·같은 업종 도 전체 이용금액과 파일 합계의 차이(%).
  - `virtual`: 동봉한 가상 CSV(`data/sample_center/manifest.json` 의 해시와 같음)면 true.
  - `sent_to_llm`: 언제나 0. 이 함수는 네트워크를 쓰지 않는다.
- 지원: 1-08, 1-12, 1-14. 다른 데이터는 인식 결과와 줄 수만 내고 `tables` 는 비운다.
- 파일을 지우지 않는다. 지우기(`/api/clear`)는 서버가 한다.

## 6. 설정과 데이터 파일

- `config.MAX_DOWNLOADS`(6), `config.MONTHS_PER_ITEM`(3), `config.MASK_THRESHOLD`(10), `config.OPEN_END_LAG_MONTHS`(3), `config.REANALYSIS_DATASETS`.
- 공개 데이터: `data/public/` 에 `_대체_` 로 시작하지 않는 CSV 가 있고 헤더가 맞으면 실물로 쓴다(파일 이름은 상관없다). 실물을 넣은 뒤에는 `public_data.reset()` 또는 프로세스 재시작. 가상 CSV 의 합계를 다시 맞추려면 `python3 tools/make_sample_center.py`.
  2026-11-17 현재 실물 2종이 들어 있어 `placeholder` 는 false 다. 대체 파일(`_대체_*.csv`)은 실물이 읽히는 것을 확인한 뒤 폴더에서 뺐다. 실물이 없는 곳에서 돌려야 하면 `python3 tools/make_placeholder_public.py` 로 다시 만든다(실물이 있으면 만들어도 읽히지 않는다).
- 허브 실물을 읽을 때 하는 전처리(`public_data.py`): 업종명은 공백과 쉼표·물음표를 지우고 맞춘다(1048 의 `기타음?식료품위주종합소매업` 한 달을 `기타음식료품위주종합소매업`에 합치고, 쉼표 없는 `피자 햄버거 샌드위치 및 유사 음식점업`을 센터 참고표의 쉼표 있는 이름과 같은 업종으로 본다). 520 은 같은 (연월, 성별, 연령대, 시간) 키의 여러 줄을 더한다. 520 의 `24시`(2022-06 까지)와 `00시`(2022-07 부터)는 둘 다 자정으로 보고 밤 구간에 넣는다.
- 명세 변환: `python3 tools/build_spec.py`(다시 만들기), `--check`(기존 spec.json 과 같은지).
- 낱말 사전 `data/spec/aliases.json`, 단위 표시 `data/spec/dim_tags.json`, 허브·센터 컬럼 대응 `data/spec/hub_center_map.json`.

## 7. 검토 뒤 바뀐 것(2026-11-17)

계약의 칸 이름은 그대로이고, 아래 칸과 인자가 더해졌다. 없어도 화면은 돈다.

- `/api/plan` 응답
  - `warnings: [{"kind": "region|place|biz|validation", "text", "action": "slots"|""}]`: 문장에서 지역이나 업종을 뜻대로 읽지 못해 넓게 풀린 경우와 검증에서 어긋난 줄이 있는 경우. 주문서 맨 위에 띄운다.
  - `order.out_of_scope: {"topic", "word", "message", "datasets": [{"dataset_no","dataset_name","period_text","export"}]}`: 주문서를 내는 범위(카드·포스 소비, 방문·유동인구) 밖의 주제. 이때 `order.items` 는 비고 `validation.total` 은 0 이다. `planner.out_of_scope(text)` 가 낱말 사전(`aliases.json` 의 `out_of_scope`)으로 가린다.
  - `validation`: `breakdown`(종류별 줄 수), `breakdown_text`, `unverified`(통과한 줄 가운데 그 시트의 명세서로는 확인하지 못한 줄 수), `unverified_notes`.
  - `order.items[].months[]`: `index`(계절 지수), `season_month`. `order.items[].months_note`: 달마다 되풀이되는 근거 문장을 한 번만 적은 목록.
  - `order.items[].conditions[]`: `value_label`(코드 값의 뜻), `evidence.verified`(false 면 다른 시트의 목록을 빌려 대조했거나 값 목록이 명세서에 없다).
  - `trace[].sub`: true 면 바로 앞 단계에 딸린 줄(버린 후보, 타협안)이다.
  - `sent_to_llm.llm_calls`, `llm_requests`: LLM 을 부르다 실패해 규칙 모드로 넘어간 경우에만 있다. 그때 `purpose_text` 등은 실패 전까지 실제로 나간 내용이다.
- `/api/options` 응답: `scope`(주문서를 내는 범위의 종수와 안내 문장), `what[].group`(업종 묶음).
- `/api/status` 응답: `leftover: {"files", "results"}`(runtime 에 남아 있는 파일 수).
- `/api/center-csv` 응답: `recoverable`(화면 전체에서 합계로 거꾸로 셈할 수 있는 가린 값의 수. 0 이어야 한다), `small_visible`(가리지 않은 10 미만 값의 수. 0 이어야 한다), `mask_note`, `tables[].headline`(비중 표 위에 보일 한 줄).
- 함수
  - `planner.plan_rule(..., sent_to_llm=None)`, `planner.compose_response(..., raw_region="")`, `planner.warnings_for(need, text, raw_region)`, `planner.scope_counts()`, `planner.text_supports_unit(text, unit)`.
  - `masking.mask_vector(basis, total_basis=None)`: 합계의 기준 값이 10 미만이면 `hide_total`. `masking.mask_matrix(basis, row_hidden=None, col_hidden=None)`: 다른 표에서 가린 줄·열 합계를 받아 함께 맞춘다. `masking.audit(relations, hidden)`: 화면 전체의 역산 점검.
  - `agent.clean_text` 는 아라비아 숫자에 더해 수량·순위를 말하는 한글 표현이 든 문장도 뺀다. `agent.no_key_reason()`, `agent._ssl_stages()`.
  - `server.make_server(port, runtime_dir, quiet=False, verbose=False)`: 프로그램 폴더에 쓸 수 없으면 `tempfile.gettempdir()/center-order-runtime` 을 쓴다(`store.fallback`).
- 도구: `tools/make_docs_html.py`(문서 .md → .html), `tools/make_zip.py`(제출 zip 묶기와 확인).
