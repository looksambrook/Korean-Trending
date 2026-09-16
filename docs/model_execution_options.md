# 모델·실행 비용 선택안

확인일: 2026-09-11. 상태: **선택·예산 승인 전 제안**. 실제 모델 호출·한국어 밈 생성 품질 평가는 수행하지 않았다. `configs/model_candidates.json`은 견적과 후보의 출처를 기록하는 자료이며 실행 승인이 아니다. 기존 `configs/pilot.json`은 변경하지 않았다.

## 1. 후보와 선택 근거

아래는 official OpenAI documentation에서 확인한 API 후보다. 모델 일반 설명을 한국어 유행어 이해·정체성 보존 성능으로 바꾸어 주장하지 않는다. 계정별 접근 권한은 확인되지 않았다.

| 후보 | 문서에 기재된 호출 ID / 스냅숏 | 검토 이유 | 재현성상 확인 사항 |
|---|---|---|---|
| GPT-5.6 Luna | `gpt-5.6-luna` | 낮은 토큰 단가의 파일럿 후보. 공식 모델 페이지는 비용에 민감한 대량 작업용으로 설명한다. | 현재 스냅숏 목록에는 날짜가 없는 동일 ID만 있다. 별도 날짜 고정 ID나 장기 불변성을 확인하지 못했다. |
| GPT-4.1 Mini | `gpt-4.1-mini-2025-04-14` | 비추론 모델이고 날짜가 있는 스냅숏을 명시할 수 있어 설정 통제가 단순한 후보. | 별칭 `gpt-4.1-mini` 대신 날짜 ID 사용을 제안한다. 지식 기준일 차이 때문에 최신 밈 품질은 별도 확인해야 한다. |
| GPT-5.6 Terra | `gpt-5.6-terra` | Luna보다 높은 비용의 선택적 비교 후보. 공식 설명은 지능과 비용의 균형을 강조한다. | Luna와 같이 날짜가 없는 현재 스냅숏만 확인했다. 한국어 과제의 우월성은 미검증이다. |

출처: [모델 목록](https://developers.openai.com/api/docs/models), [Luna 모델 설명](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [GPT-4.1 Mini 모델 설명](https://developers.openai.com/api/docs/models/gpt-4.1-mini), [Terra 모델 설명](https://developers.openai.com/api/docs/models/gpt-5.6-terra). 확인한 [지원 종료 목록](https://developers.openai.com/api/docs/deprecations)에서는 이 세 후보 자체의 종료 공지를 찾지 못했다. 연구 기간 내 제공을 보장하는 뜻은 아니다.

**선택 제안:** 비용을 먼저 시험한다면 Luna, 날짜 고정 모델과 비추론 설정을 우선한다면 GPT-4.1 Mini를 첫 후보로 삼는다. 한 모델로 개발 파일럿을 수행하고, 두 번째 모델은 예산과 품질 진단 후 결정한다. Terra를 기본으로 추가하여 실험량을 세 배로 늘리지 않는다. 어떤 후보도 현재 주실험 모델로 확정하지 않았다.

## 2. 현재 토큰 단가

단위는 **USD / 1,000,000 tokens**, Standard 처리·짧은 문맥 기준이다. 환율·세금·기관 계약 가격을 가정하지 않았다. [공식 API 가격표](https://developers.openai.com/api/docs/pricing)

| 후보 | 일반 입력 | 캐시 입력 | 캐시 쓰기 | 출력 | 보수적 예약에 사용할 입력 단가 |
|---|---:|---:|---:|---:|---:|
| GPT-5.6 Luna | 0.20 | 0.02 | 0.25 | 1.20 | 0.25 |
| GPT-4.1 Mini | 0.40 | 0.10 | 별도 단가 없음 | 1.60 | 0.40 |
| GPT-5.6 Terra | 2.00 | 0.20 | 2.50 | 12.00 | 2.50 |

GPT-5.6 모델은 입력 272K 초과 시 요청 전체에 장문 가격이 적용된다. 아래 제안 상한은 요청별 12K/16K이므로 그 조건에 도달하지 않는다. 캐시 적중 할인은 견적에 반영하지 않는다. GPT-5.6의 캐시 쓰기에는 별도 요금이 있으므로 최대 입력 단가로 비용을 예약한다. `prompt_cache_options.mode: "explicit"`로 설정하고 breakpoint를 하나도 넣지 않으면 캐시를 읽거나 쓰지 않는다고 공식 문서는 설명한다. 이 설정을 사용하더라도 초기 비용 통제는 캐시 쓰기 단가로 보수적으로 계산한다. [캐시 동작·과금 문서](https://developers.openai.com/api/docs/guides/prompt-caching)

## 3. 3계열 개발 파일럿의 계산 가능한 상한

이 절의 토큰 수는 **측정치가 아니라 새로 제안하는 허용 상한**이다. 원자료·프롬프트·지원 용례가 이 안에 들어가는지는 실제 완성 요청의 토큰 수로 점검해야 한다. 상한 초과 요청은 일부 용례를 조용히 삭제하지 말고 중단한다.

| 단계 | 호출 수 | 호출당 입력 상한(가정) | 호출당 `max_output_tokens`(제안) |
|---|---:|---:|---:|
| 계열별 자동 구조 추출 | 3 | 12,000 | 4,096 |
| 3계열 × 2상황 × B1/B2/B3 생성 | 18 | 16,000 | 1,024 |
| 합계 | 21 | 324,000 | 30,720 |

출력 상한은 응용문 길이가 아니다. 추출 JSON과 출력 형식 토큰에 여유를 두기 위한 것이다. 기존 파일럿 설정의 추출 1,600·생성 128 토큰과 다른 제안이며, 채택 시 별도 실행 설정 버전을 만든다. 추론을 켜는 후속 실험은 이 상한으로 충분하다고 보장하지 않는다.

`상한 = (324,000 × 최대 입력 단가 + 30,720 × 출력 단가) / 1,000,000`

| 후보 하나로 21회 실행 | 일반 입력으로만 과금될 때의 상한 | 캐시 쓰기까지 고려한 보수적 상한 |
|---|---:|---:|
| GPT-5.6 Luna | $0.101664 | **$0.117864** |
| GPT-4.1 Mini | $0.178752 | **$0.178752** |
| GPT-5.6 Terra | $1.016640 | **$1.178640** |

이는 **조건부 모델 토큰 비용 상한**이며 연구 총예산이나 실제 청구액이 아니다. 같은 호출을 반복하거나 수리·재생성하면 별도 비용이다. 자동 재시도 0회, 도구·검색 0회, Standard 처리, 위 상한 준수, 현재 가격 유지, 일반 API endpoint를 전제로 한다. 지역 처리 추가 요금, 세금, 사람 평가 보상, 추가 데이터 수집 비용은 포함하지 않는다. 세 후보를 전부 실행하는 보수적 합은 $1.475256이지만, 전부 실행하는 계획을 승인한 것은 아니다.

평가 상황을 아직 확정하지 않았으므로 18회 생성은 **실행량 가정**이다. 각 모델에서 전체 파이프라인을 반복하면 추출 레코드도 달라질 수 있다. 그 경우 모델 간 결과 차이를 생성기만의 차이로 해석하지 않는다. 생성기 일반화만 보려면 동일 추출 레코드를 재사용하는 보조 실험을 따로 정의한다.

## 4. Responses 설정과 재현성

모든 B1/B2/B3 조건에서 생성 모델 ID·추론 설정·샘플링 설정·출력 상한·후보 수를 같게 한다. 한 계열의 자동 레코드는 한 번 추출하여 B2/B3에서 공유한다. 상황마다 다시 추출하지 않는다.

- 공통: `service_tier: "default"`, `store: false`, `truncation: "disabled"`, 동기식·비스트리밍, 도구 없음, 이전 응답/대화 연결 없음, SDK 재시도 0회. 공식 API에서 `default`는 Standard 처리를 뜻한다. [Responses Python 생성 참조](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
- Luna/Terra 초기 제안: `reasoning: {"effort": "none", "mode": "standard"}`. 모델 페이지에서 `none` 지원을 확인했다. 커스텀 `temperature`·`top_p`의 해당 모델별 허용 조합은 확인하지 못했으므로 전송하지 않는다. 추론 수준은 조건 사이에서 변경하지 않는다. 추론이 있는 설정을 나중에 선택한다면 새 파일럿 설정을 기록한다. [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra), [추론 설정 문서](https://developers.openai.com/api/docs/guides/reasoning)
- GPT-4.1 Mini 초기 제안: `reasoning`은 생략한다. `temperature`는 추출 0.0, 생성 0.7을 제안한다. `top_p`는 함께 변경하지 않는다. temperature 0도 완전한 결정성을 보장하는 것으로 서술하지 않는다. 실제 첫 호출에서 요청 수용·반환 설정을 확인한다. [모델 설명](https://developers.openai.com/api/docs/models/gpt-4.1-mini), [sampling parameter 정의](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
- 확인한 Responses 요청 명세에는 `seed` 필드가 없다. 저장소의 `seed: 20260911`은 분할·표시 순서 등 로컬 무작위화에 사용하고 Responses payload로 전달하지 않는다. “같은 seed로 모델 출력을 재현했다”고 쓰지 않는다. [Responses 요청 명세](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
- `max_output_tokens`는 보이는 출력뿐 아니라 추론·숨은 형식 토큰도 포함한다. `status: incomplete`와 출력 상한 도달을 실패로 보존한다. 품질이 낮거나 JSON이 깨진 출력을 좋은 결과로 조용히 바꾸지 않는다. [토큰 집계 문서](https://developers.openai.com/api/docs/guides/token-counting)

## 5. 토큰 집계와 비용 통제

로컬 tokenizer로 plain text 토큰 수를 진단할 수 있지만, 전체 API 입력의 정확한 토큰 수라고 보고하지 않는다. 모델별 tokenizer를 확인하지 못하면 다른 모델 encoding으로 조용히 대체하지 않는다. 특히 한국어에 `문자 수 / 4`를 적용한 값을 실측치로 사용하지 않는다. 공식 문서는 역할·경계·도구·스키마의 형식 토큰이 로컬 text 집계와 다를 수 있다고 설명한다. [Counting tokens](https://developers.openai.com/api/docs/guides/token-counting)

실행 가능해지면 **동일 모델·동일 완성 입력**을 `responses.input_tokens.count`에 전달하여 `input_tokens`를 기록한다. system/developer 지시나 출력 JSON 스키마를 추가했다면 지원되는 필드를 포함해 동일하게 집계한다. 토큰 집계 endpoint도 인증·외부 전송이 필요하므로 오프라인 준비와 구별한다. 현재 계정의 endpoint 접근과 과금 조건은 실행 전에 확인한다.

각 실제 요청 직전에 `실제 입력 수 × 최대 입력 단가 + 출력 상한 × 출력 단가`를 남은 예산에서 예약한다. 반환 usage의 일반·캐시·캐시 쓰기·출력 토큰으로 정산하며 `reasoning_tokens`를 출력 토큰에 다시 더하지 않는다. 모델·처리 tier가 예상과 다르거나 usage가 없거나 타임아웃 후 과금 여부를 모르면 해당 예약액을 유지하고 중단한다. 이 경계는 공식 [요청별 비용 통제 예제](https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api)의 원칙을 따른다. 그 예제의 가상 가격과 토큰 수는 이번 견적에 사용하지 않았다.

실제 원응답, 반환 모델 ID, 요청·응답 ID, 시각, 파라미터, usage, 실패 상태, 데이터·프롬프트 해시를 보관한다. 날짜가 없는 모델 ID는 문서 확인일과 실행일을 함께 기록해 재현성 한계를 밝힌다. 개발 결과로 모델을 선택할 수 있지만 시험 상황·정답·시험 평가 결과로 모델이나 프롬프트를 선택하지 않는다.

## 6. 다음 선택에 필요한 최소 정보

연구자에게 필요한 선택은 첫 파일럿 후보와 API 지출 상한이다. API 접근이 준비되면 계정에서 후보 접근 가능 여부를 확인하고 21회 이내 개발 파일럿의 실행 명세·자료·비용 한도를 고정한다. 비밀키는 문서·프롬프트·공개 설정에 넣지 않는다. 사람 평가 모집 인원과 기관 절차, 최종 투고처 결정은 별도의 미정 사항으로 유지한다.

문헌 출처 기록: `data/literature/model_doc_sources.json`. 이 문서는 실험 결과나 한국어 밈 생성 능력의 비교표가 아니다.
