# 관련연구 대조와 차별점 후보 — 통합 안내

확인일 2026-09-11. 국제 8편과 국내 5편을 대조했다. 모든 논문 본문을 완독한 문헌고찰은 아니며 확인한 절·판본·초록 범위를 아래 상세 문서에 기록했다.

- [국제 8편 상세 대조표](related_work_international.md)
- [국내 5편·실제 사례·투고처 후보](korean_sources_and_pilot.md)
- [국제 출처 확인 로그](../data/literature/international_sources.json)

| 직접 관련 축 | 선행연구에서 확인한 중복 | 검증할 차별점 후보 |
|---|---|---|
| 변형 구조/문화 인용 | [Catchphrase](https://aclanthology.org/2021.acl-short.1/)는 교체 가능 패턴·복수 원형·원형별 분할을 다룸(본문 선택 절) | 한국어의 인용·말투·대화 등을 포함한 상황별 응용문의 정체성 평가 |
| 템플릿·기능·계열 분할 | [Social Meme-ing](https://aclanthology.org/2024.naacl-long.166/), [A Template](https://aclanthology.org/2025.naacl-long.525/)(공식 서지+저자본 선택 절) | 계열 선택을 고정한 뒤 자동 정보의 생성 효과를 분리 |
| 맥락 이해 | [SLANG](https://aclanthology.org/2024.emnlp-main.698/), [MemeReaCon](https://aclanthology.org/2025.emnlp-main.176/)(본문 선택 절) | 뜻풀이·의도 설명 대신 실제 사용자 상황의 응용문 생성 |
| 상황별 밈 생성 | [Memenify](https://digitalcommons.dartmouth.edu/masters_theses/203/)(석사논문 공개 초록), [HUMOR](https://arxiv.org/html/2512.24555v2)(프리프린트 선택 절) | 같은 기반 LLM·원자료·자동 레코드 B1/B2/B3 비교; 추출 오류와 정체성 보존 분리 평가 |
| 국내 문형/지원 데이터 | [이대규·이찬규 2023](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART003044747)(KCI 서지·초록만) | 자동 구조 추출 정보의 생성 개입실험. 상세 구현의 중복은 본문 확인 후 확정 |
| 국내 표현 구조·유형 | [우성미 2026](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART003366058)(KCI 서지·초록만) | 생성형 AI 밈 콘텐츠의 유형 분석과 사용자 상황의 텍스트 생성 평가를 구분; 본문 대조 필요 |
| 프롬프트 서식 | [FormatSpread](https://arxiv.org/html/2310.11324v2)(저자 camera-ready 선택 절) | B2/B3의 원자 사실을 유지하고 특정 렌더링 결과의 일반화를 제한 |

기여 후보는 개별 기법의 발명이 아니라 **한국어 다형태 표현, 동일 자동 정보 통제, 두 주요 사람 지표, 계열·상위 파생 그룹 분할, 자동 추출 오류 분석을 결합한 재현 가능한 검증**이다. 이 조합의 독창성도 아직 확정하지 않았다. “최초”, “기존 연구 없음”, “성능 향상”은 현재 근거로 쓰지 않는다.

우선 후속 문헌 작업은 국내 2023 데이터·문형 논문 및 2026 구조·유형 논문 본문 확보, 저자 초판과 최종판 대조, 직접 관련 통제 생성·화용론 주석 문헌 확장이다. 원문 접근 실패를 초록 이상의 추정으로 채우지 않는다.
