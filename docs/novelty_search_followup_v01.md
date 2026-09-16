# 신규 중복 가능성 제한검색 v0.1

확인일·기준일: **2026-09-11**. 기존 [국제 검토](related_work_international.md)와 [주장-근거 지도](../paper/claim_evidence_map_v01.json)를 대조한 후 신규 문헌을 검토했다. 목적은 기여 후보의 범위를 점검하는 것이다. **체계적 문헌고찰, 선행연구 부재 증명, 최초 주장 검증은 아니다.** 현재 데이터·평가 상황·프롬프트·주장 지도는 변경하지 않았다.

## 결론

**자동으로 보존/변경 부분을 분해하여 익숙한 문구를 새 상황의 텍스트로 재작성하는 연구를 추가 확인했다.** 따라서 자동 구조 추출→응용 생성 자체는 신규 기여로 내세우기 어렵다. 아래 두 논문의 확인 절을 기준으로, 본 연구의 검증 대상은 동일한 원형·지원 용례·기반 모델을 고정한 B1/B2/B3 비교 및 한국어 유행어 계열 정체성 평가로 계속 좁혀야 한다. 해당 비교가 모든 기존 연구에 없다는 뜻은 아니다. [명언 재맥락화 원문](https://arxiv.org/html/2602.06049v1), [문화 간 밈 재창작 원문](https://arxiv.org/html/2602.02510v1)

## 검색 범위와 절차

웹 검색은 **2개 묶음, 7개 검색어**로 제한했다. 직접 관련성이 높은 신규 원문 2편의 방법·평가 및 공개 프롬프트를 읽었다. 검색 결과의 재게시·요약 사이트는 발견 경로로만 사용하고 내용 판단은 arXiv 원문으로 전환했다. 날짜 필터 대신 공식 버전 이력에서 기준일 이전 여부를 확인했다. 검색 순위·검색 결과는 재실행 시 달라질 수 있다.

| 묶음 | 실제 검색어 |
|---|---|
| 1 | `site.arxiv.org meme generation contextual template semantic structure rules 2026` |
| 1 | `site.aclanthology.org meme generation template intent preservation 2025 2026` |
| 1 | `site.arxiv.org catchphrase slang generation template adaptation 2025 2026` |
| 2 | `"Recontextualizing Famous Quotes for Brand Slogan Generation" publication` |
| 2 | `"Beyond Translation: Cross-Cultural Meme Transcreation" conference` |
| 2 | `site.arxiv.org "meme generation" "preserve" "structure"` |
| 2 | `site.aclanthology.org "meme generation" "template" 2026` |

## 직접 검토 1: 명언 재맥락화

[Recontextualizing Famous Quotes for Brand Slogan Generation](https://arxiv.org/abs/2602.06049), Ziao Yang, Zizhang Chen, Lei Zhang, Hongfu Liu. **arXiv:2602.06049v1**, DOI `10.48550/arXiv.2602.06049`. 게재 논문으로 확정하지 않았다.

| 대조 항목 | 확인 내용 |
|---|---|
| 입력 | 브랜드·persona. Table 4에서는 모델이 원문 후보를 제안하고 선택한다. |
| 중간 분석 | §3에서 고정/편집 가능 부분을 나누고 구문·운율·수사 구조를 보존한다. |
| 출력 | 어휘 교체 후 영어 슬로건. 후속 정제·검증과 후보 탈락도 설명한다. |
| 비교 | §4.1의 제안법은 LoRA를 적용한 QwQ-32B, 기준선은 다른 LLM들이다. 공유 입력은 브랜드·persona다. |
| 평가 | 다양성·novelty·첫인상 효과 및 사람의 첫인상/persona 적합 선택. |
| 보존 평가 해석 | Table 5의 faithfulness는 브랜드·persona 문맥과 슬로건의 함의 관계다. 원 계열 식별성 척도로 인용하지 않는다. |
| 우리 통제와 대조 | 확인한 §3·§4.1·§4.4·Table 4–6에는 같은 자동 레코드의 서술형/필드형 비교가 제시되지 않는다. 전체 연구의 부재 주장으로 확대하지 않는다. |

확인 범위: [HTML](https://arxiv.org/html/2602.06049v1) §3, §4.1, §4.4, 부록 Table 4–6; [PDF](https://arxiv.org/pdf/2602.06049) 제목·저자·버전 표기 및 부록의 해당 프롬프트. 효과 수치는 채택하지 않았고 실행·자료를 재현하지 않았다.

**날짜 주의:** 공식 abs 제출이력은 `2026-01-12 04:56:48 UTC`, HTML 및 PDF 버전 표기는 `12 Jan 2026`다. 셋은 일치하지만 식별번호의 `2602`와 달라 원인을 미확인으로 남긴다. 임의로 2월 제출일을 만들거나 최초 공개 우선권의 근거로 쓰지 않는다. 검색에 나타난 저자 SNS의 학회 수락 언급은 원 게시물·공식 게재 기록을 확인하지 않아 출판 상태에 반영하지 않았다.

## 직접 검토 2: 문화 간 밈 재창작

[Beyond Translation: Cross-Cultural Meme Transcreation with Vision-Language Models](https://arxiv.org/abs/2602.02510), Yuming Zhao, Peiyi Zhang, Oana Ignat. **arXiv:2602.02510v1**, DOI `10.48550/arXiv.2602.02510`. 공식 abs는 2026-01-23 제출로 표시한다. HTML도 같은 날짜이며 `2602` 식별번호와의 차이는 미해결이다. 후속 게재 여부는 미확인이다.

| 대조 항목 | 확인 내용 |
|---|---|
| 입력·중간 분석 | 원 밈 이미지와 목표 문화. §3·부록 F에서 문화적 참조·유머·의도를 분석하고 문화 간 대응을 찾는다. |
| 보존/변경·출력 | 공통 유머/의도를 유지하면서 문화 특수 요소를 바꾼 caption과 새 시각 템플릿을 만든다. LLaVA와 FLUX를 결합하며 최종 수동 품질 확인도 명시한다. |
| 평가 | §5는 Caption/Image Quality, Synergy, Cultural Fit, Intent Preservation을 구분하고 사람과 VLM 평가를 비교한다. |
| 중복 | 보존 요소와 바꿀 요소를 구분한 적응 및 적합성·보존의 별도 평가는 이미 제시된다. |
| 남는 검증 | 원 의미의 문화 간 전달과 한국어 유행어 계열을 알아보게 하는 상황 응용은 다른 목표다. 확인한 방법·평가 절에서 우리 B2/B3 렌더링 통제를 확인하지 못했다. |

확인 범위: [원문](https://arxiv.org/html/2602.02510v1) §3–3.1, §4.3, §5.1–5.3, 부록 F. 수동 검토가 포함된 산출물을 전 과정 무인 자동 시스템 성능으로 재서술하지 않는다. [OpenReview 레코드](https://openreview.net/forum?id=IaZseCdyuD)는 브라우저 검증 화면으로 본문·결정 정보에 접근하지 못했다. 논문의 성능 수치는 옮기지 않았다.

## 선별만 한 신규 후보

- [MemeCraft](https://arxiv.org/abs/2403.14652): 공식 arXiv 서지·초록만 읽음. 사용자 문맥·입장에 맞춘 멀티모달 생성이라는 인접 과제. 상세 방법·통제·최종 게재판은 후속 검토 대상이다.
- [MemeIntent](https://aclanthology.org/2024.sigdial-1.54/): ACL 공식 초록의 의도 설명 생성 과제로 선별. 새 응용문 생성과 구별하였으며 이번 본문 독해 대상에서는 제외했다.
- 검색에 나타난 유해 밈 탐지 연구와 오래된 slang 생성 연구는 이번의 좁은 생성 통제 비교와 직접성이 낮아 확장 독해하지 않았다. 검색 노출만으로 모두 검토했다고 계산하지 않는다.

## 주장 지도에 반영할 제안

기존 C26의 `candidate_not_proven_novel_or_effective` 상태를 유지한다. 새 자료는 '구조 분해 기반 생성'과 '적합성/보존 평가'의 선행 근거를 강화한다. 기여 문장은 **동일 자동 정보 통제 아래 한국어의 여러 표현 방식에서 어떤 조건이 언제 도움이 되는지 평가한다**는 계획 수준으로 제한한다. 개선, 보편적 형식 우위, 검증된 정체성 척도는 실제 연구 근거가 갖춰진 뒤에만 서술한다. 이번 파일은 제안 기록이며 원 주장 지도를 직접 수정하지 않았다.

세부 검색 로그·출처별 확인 상태는 [JSON 기록](../data/literature/novelty_search_followup_v01.json)에 있다.
