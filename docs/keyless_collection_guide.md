# 키 없이 실제 트렌드·밈 근거를 확인하는 경로

2026-09-15 로컬 실행 결과. 모든 수량은 이번에 실제 받은 응답에서 센 값이다. 공개 접근 성공을 장기 제공 보장, 재사용 허가 또는 연구 성능으로 확대하지 않는다.

## 확인한 결과

| 자료 경로 | 실제 결과 | 무엇을 확인할 수 있는가 |
|---|---|---|
| YouTube 채널 Atom | 3개 채널, 영상 메타데이터 45건 | 정한 채널에서 밈 이름을 지정하지 않고 제목·설명 관측 |
| YouTube 공개 검색 HTML | 선택한 표현 1개, 검색 결과 20건 | 관측한 후보의 추가 사용례 탐색. 이름을 지정한 사후 확장 |
| YouTube 공개 영상 HTML | 검색 결과 중 영상 3개 | 원 제작자 설명·채널·게시 시각을 확인하여 표현의 관계 해석 |
| Google Trends KR RSS | HTTP 200, 급상승 검색 주제 10개 | 제공자가 선별한 Google 검색 트렌드와 관련 뉴스 메타데이터 |

이번 수집에는 API 키·로그인·서버가 필요하지 않았다. 수집 코드의 유료 API 호출과 모델 API 호출은 0회다. 에이전트가 읽고 해석한 작업은 별도 AI 보조 검토로 기록했으며, 통제된 모델 이해 실험의 호출 수·성능으로 세지 않았다.

YouTube 댓글·자막·영상·음성은 이 경로로 분석하지 않았다. 공식 Data API용 코드도 준비했으나 사용자 API 키가 없어 라이브 API 접속은 미검증이다.

## 실제 밈 관련 사례

먼저 채널 피드를 수집한 뒤 `실례카겔`이라는 표현을 관측했다. 첫 추출기는 이를 놓쳤다. 제목·해시태그를 구분하도록 고친 다음 후보로 추출하고, 표현을 지정한 공개 검색으로 확장했다. 마지막으로 스낵타운 제작 영상의 전체 설명을 가져와 **실리카겔 공연을 패러디한 프로젝트·명칭**임을 출처의 설명으로 확인했다. [NO PAIN 원 제작 영상](https://www.youtube.com/watch?v=Q9hDqYvhrOU), [BIG VOID 다음 회차](https://www.youtube.com/watch?v=jxw1Cudx5lA)

이 사례는 원문에서 맥락을 알아가는 경로가 작동했다는 증거다. 개발 자료에서 발견한 사례로 토크나이저를 수정했으므로 독립 평가 자료에서의 성공으로 보고하지 않는다. 이 명칭의 최초 기원, 독립적인 재창작 수, 유행 증가 정도, 영상의 실제 연주·연출을 검증한 것은 아니다. [근거와 해석을 분리한 기록](youtube_phrase_evidence_v03.md)

[원문·후보·사후 검색·제작자 설명 보고서](../data/derived/youtube_expand_20260915_v01/report.html)를 열면 처음 관측한 45건 전체와 후보, 별도로 확장한 검색 결과를 볼 수 있다. 프로그램명·인명·일반어·광고도 후보에 남아 있으며 숨기지 않았다.

## 실제 검색 트렌드

Google Trends는 급상승 주제를 RSS로 내보내는 기능을 제공한다. 이번 공개 KR 피드의 응답 시각은 **2026-09-15 03:35:56 KST**, 항목 수는 10개다. [공식 도움말](https://support.google.com/trends/answer/3076011?hl=en)

| 이번 응답에 있던 주제 예시 | 제공된 근사 검색량 문자열 |
|---|---|
| 그랜드 테프트 오토 vi | `500+` |
| 트리니티항공 | `100+` |
| 강균성 | `1000+` |

정확한 검색 횟수로 바꾸지 않았다. 이 값은 YouTube 조회 수·문구 빈도·밈 사용 수와 다른 척도다. 위 주제를 밈으로 판정하지 않았고, 연결된 기사 본문도 읽지 않았다. [실제 수집 manifest](../data/collected/google_trends_kr_20260915_v01/manifest.json), [주제 10건과 뉴스 메타데이터](../data/collected/google_trends_kr_20260915_v01/observations.jsonl)

## 코드와 다음 관측 회차

| 코드 | 기능 |
|---|---|
| `src/youtube_watch.py` | 피드 수집→후보 추출→원문 보고서 |
| `src/youtube_expand.py` | 후보 1개 선택→이름 검색→영상 설명 보강→보고서를 한 명령으로 연결; 기존 수집 재사용 지원 |
| `src/youtube_feed_collect.py` | 공식 채널 Atom을 작은 고정 범위로 수집 |
| `src/youtube_candidates.py` | 안내문 억제·제목/해시태그 처리·반복 표현·잠정 변형 후보 |
| `src/youtube_web_search.py` | 공개 검색 최초 HTML 1페이지의 결과 카드 |
| `src/youtube_video_metadata.py` | 선택한 영상 최초 HTML의 상세 메타데이터 |
| `src/youtube_context_bundle.py` | 같은 원문을 사용하는 U0/U1 입력 준비. 의미 추론은 아직 미실행 |
| `src/google_trends_collect.py` | 한국 Google 검색 트렌드 공개 RSS 1회 수집 |

새 수집 회차는 설정의 `snapshot_id`와 새 출력 경로를 명시해 실행한다. 출력을 바꾸기만 해서 같은 실험을 다시 돌릴 수는 없다. 같은 명세는 기존 로그를 조회하고, 실패 재시도는 기존 실행 ID와 이유를 남긴다. 원본 HTTP 응답은 관측 JSONL·후보·해석과 분리해 보존한다.

```powershell
python -X utf8 -B src/youtube_watch.py run --config configs/새_YouTube_회차.json --output-dir data/derived/새_YouTube_회차 --method D0
python -X utf8 -B src/google_trends_collect.py --config configs/새_Trends_회차.json --output-dir data/collected/새_Trends_회차
```

개발 중 시도한 경로, 실제 실패와 재시도, 입력·코드 해시, 결과 파일은 `logs/research.sqlite3`에 남겼다. 일부 도구 기반 문헌·접근 조사는 사후 등록 사실을 명시했다. [수집 실행과 수정 이력](youtube_collection_run_v01.md)

후보 확장 실행기의 필수 인자는 `--collection-manifest`, `--candidate-dir`, `--candidate-id`, `--output-dir`다. 신규 실행은 후보 표현 검색 1페이지와 해당 문구가 포함된 검색 결과의 상위 영상 최대 3개만 요청한다. 기존 자료가 있으면 `--reuse-search-manifest`와 `--reuse-metadata-manifest`를 지정한다. 서로 다른 수집의 결과를 섞지 않도록 원본 실행 ID·해시·근거 ID를 확인한다.

이번에는 보존한 45건→20건→3건을 재사용하는 통합 명령을 실제 실행했다. 새 HTTP 요청은 0회였고, 이미 만들어진 동일 보고서를 해시 확인 후 재사용했다. [통합 실행 manifest](../data/derived/youtube_expand_20260915_v01/workflow_manifest.json), [최종 연결·보고서 확인](../logs/youtube_integrated_report_check_v01.json)

## 연구에서 아직 해야 하는 것

전체 관측 자료의 독립적인 정답 작성, 누락·가짜 후보·계열 오류 평가, 독립 변형과 재게시 판별, 의미 구조를 갖춘 U1과 모델 이해 평가가 남아 있다. 이 작업은 **실제 자료를 확인할 수 있는 수집·추적 방법의 구현**이며 논문 연구 전체가 완료되었다는 뜻이 아니다. 최초 표본, 사후 이름 검색, 제작자 설명, 나중에 생성한 AI 해석을 서로 다른 시각·출처로 유지한다.
