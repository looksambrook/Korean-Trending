# 데이터 형식 v0.1

상태: 로컬 준비용 스키마. 연구 자료, 분석 레코드의 생성 주체, 실행 결과를 서로 구분한다. JSONL은 UTF-8로 한 줄에 한 객체이며 빈 파일은 아직 자료가 없다는 뜻이다. 코드 실행 위치는 프로젝트 루트다.

## 경로와 역할

| 경로 | 내용 / 사용 가능 범위 |
|---|---|
| data/evidence/pilot_evidence.json | 실제 웹 관찰 7건의 출처·검증 범위·중복군·이용 조건. AI가 요약한 context_paraphrase는 별도 표시 |
| data/pilot_dev/families.jsonl | 개발용 3계열의 원형 **후보**, 상위 파생 그룹, 분할, 편입 상태 |
| data/pilot_dev/supports.jsonl | 출처에서 확인한 짧은 문구와 **출처 제목만** 사용하는 로컬 지원 입력. AI 맥락 요약을 B1 원자료에 몰래 섞지 않음 |
| data/pilot_dev/scenarios.jsonl | 현재 비어 있음. 독립적으로 작성·검토할 개발 상황 |
| data/automatic/records.jsonl | 현재 비어 있음. 실제 모델 자동 추출 후 생성할 레코드 |
| data/human_reviewed/records.jsonl | 현재 비어 있음. 사람이 수정한 별도 참고 레코드 |
| data/test_locked/ | 현재 빈 상황·정답 파일. 최종 시험 자료를 개발과 분리하기 위한 위치이며 접근통제 서버가 아님 |
| data/fixtures/ | AI가 만든 소프트웨어 점검 전용 문장·상황. 실제 유행어/용례/연구자 작성으로 간주하지 않음 |
| data/splits/pilot_dev.json | 이번 3계열은 dev 전용임을 기록. 최종 분할 비율/시험 계열은 미정 |
| data/schemas/extracted_fields.schema.json | 자동 추출 출력의 8개 필드 JSON Schema |
| data/templates/ratings.csv | 실제 평가 전 빈 입력 양식 |

원문 전체, 원영상의 직접 관찰, 최초 출처를 확보했다는 뜻은 아니다. 짧은 제목·인용만으로 사용 맥락 일반화가 가능한지 판단하는 **제한된 절차 파일럿**이다. 본실험 승격 전 충분한 직접 지원 문맥, 독립 용례 및 텍스트 적합성을 보강한다.

## 기본 레코드

**계열**: family_id(고유), lineage_group_id(상위 파생 그룹), canonical_text(후보), canonical_origin_verified(bool), split(dev/validation/test), eligibility(local_pilot_only/main_ready/smoke_fixture), independence_reviewed(bool), external_model_use_cleared(bool). 계열/파생 그룹은 문자열 유사성만으로 정하지 않는다.

**지원**: support_id(고유), family_id, text, context, context_provenance, source_id, source_group_id, source_url, provenance, verification_status, verification_scope, attestation_type, published_date, collected_date, normalization, external_model_use_cleared.
- provenance: observed_web / researcher_authored / ai_synthetic / user_supplied_unverified.
- observed_web는 웹에서 해당 텍스트를 관찰했다는 뜻이다. 원 작성자가 AI를 사용하지 않았다는 보장은 아니다.
- text_verified는 지정된 페이지에서 문자를 확인했다는 뜻이며 최초 원본·독립성·사실성·이용 허가가 모두 확인됐다는 뜻이 아니다.
- 같은 문서·영상의 재게시와 인용은 source_group_id로 연결하고 실제 독립 사용 수와 문서 수를 따로 센다.
- context는 실제 관찰 문맥만 모델의 원자료에 포함한다. 별도의 AI 요약은 metadata에 보관하고 현재 B1/B2/B3 입력에서 제외한다.

**상황**: scenario_id, family_id, situation, split, provenance, independent_authorship_reviewed(bool), source_support_rephrase(bool), locked(bool), lock_id(시험일 때). 작성자/검토자 식별자와 작성·잠금일은 관리 메타데이터로 추가한다. 실제 평가 상황을 만드는 경우 상황에 정답 유행어가 드러나지 않도록 독립 검토한다. 현재 이 작업에서 실제 평가 상황·정답은 만들지 않았다.

**자동 레코드 봉투**: schema_version, record_id, family_id, record_origin(automatic/human_reviewed/smoke_fixture), support_ids, support_bundle_hash, request_id, response_id, model_id, created_at, raw_response_hash, fields.
- fields에는 form_labels, functions, preservation_constraints, editable_elements, adaptation_modes, usage_context, modality_dependency, uncertainty_notes.
- 각 필드는 원자 사실 배열: value(문자열), evidence_ids(지원 ID 목록), certainty(supported/tentative).
- editable_elements를 포함해 모든 필드의 빈 목록을 허용한다. 없는 슬롯을 만들지 않는다.
- uncertainty_notes의 tentative 주장 외에는 적어도 하나의 지원 ID가 필요하다.
- ID의 존재는 **의미적 근거의 정확성**을 보증하지 않는다. 근거 없는 일반화는 자동 추출 품질 평가 대상이며 사람이 몰래 고치지 않는다.
- 자동 추출 구조와 수동 주석은 별도 자료다. 독립 주석자 기록·합의 전 기록·합의 근거는 annotation_version, annotator_id, adjudication_log 등의 별도 관리 파일로 보존한다.

## 요청·응답·실행 기록

prepare-extraction은 계열 원형 후보와 support_id/text/context만 허용 목록으로 전달한다. 상황 파일 인자가 없다. prepare-generation은 상황의 situation만 전달하고 정답·평점·관리 메타데이터를 넣지 않는다.

오프라인 요청은 request_id, stage, messages.system, messages.user, model_id, temperature, max_output_tokens, seed_requested 등을 가진다. 제공자 API 그대로의 요청 포맷은 아니다. 모델 선택 후 어댑터가 실제 지원 파라미터로 변환하며 effective parameters를 기록해야 한다. execution_ready는 항상 false다. 현재 도구에 네트워크 실행 기능은 없다.

응답 JSONL에 필요한 키:

- request_id: 저장된 요청 ID
- record_origin: 실제 모델 응답은 automatic, 테스트 fixture는 smoke_fixture
- model_id: 설정 및 요청과 같은 정확한 모델 ID/버전
- response_id, created_at: 실제 제공자/실행 로그의 값
- usage: 실제 input_tokens/output_tokens. 제공자가 주지 않는 값은 null
- status: ok 또는 error
- output_text: 가공하지 않은 원출력 문자열
- error, retry_count, latency_ms, effective_parameters, provider_metadata: 실제 호출 시 함께 수집

추출 output_text는 8개 fields 객체의 JSON 문자열이다. 코드펜스·오류 JSON은 실패로 보존하며 자동으로 사람이 복구하지 않는다. 재시도 정책은 모델 실행 전 고정한다. 생성 output_text는 응용문 한 개다. 응답의 automatic 라벨은 실행자의 출처 선언이지 암호학적 제공자 인증이 아니므로, 외부 호출 어댑터·원응답 보관을 통해 검증한다.

manifest는 입력·프롬프트·코드·설정·출력 해시와 input_snapshots의 입력 사본을 저장한다. generation 요청 ID에는 실제 프롬프트 내용도 포함한다. 사람이 원자료/상황을 바꾸면 같은 실행의 평가 내보내기는 해시 불일치로 거부한다. 사용한 원본 파일 버전 자체도 보존해야 하며 해시만으로 파일을 복원할 수는 없다.

## 평가 데이터

내보내기는 stage_a.jsonl(상황/응용문), stage_b.jsonl(응용문/공통 원문·지원), PRIVATE_key.jsonl(조건·계열·상황·실패 상태)을 분리한다. PRIVATE_key를 평가자에게 배포하지 않는다.

이는 설문 서비스나 평가자 배정기가 아니다. 운영자는 지침에 따라 조건을 균형 배정하고 모든 A 완료 후 B를 공개해야 한다. 현재 파일 분리만으로 접근 권한·블록 순서·회귀 수정 금지가 자동 시행되지는 않는다.

ratings.csv는 item_id, rater_id, situation_fit, identity_preservation, naturalness, willingness_to_use, missing_reason, prior_familiarity, session_id, annotation_version 등을 담는다. 숫자와 결측 사유를 구분한다. 분석 명령은 두 주요 지표의 **기술적 완결쌍 요약**만 제공한다. 다중 검정, 순서형 일치도, 평가자 임의효과, 실패 최저점 민감도 분석은 분석 계획 확정 후 추가한다.

## 검증 범위

로컬 검증기는 ID 중복, 증거 ID 연결, 출처 종류, 상위 그룹/원문 완전중복의 분할 충돌, 레코드 출처, 자료 해시 변경을 검사한다. 의미적 근접 중복, 원형 진위, 저작물 이용 범위, 실제 독립 용례 여부, 평가 상황의 정답 누출은 사람의 자료 감사도 필요하다. 스키마 통과를 수집·주석의 완료로 보고하지 않는다.

B3의 모델 표시용 키는 B2와 같은 한국어 라벨로 결정적으로 변환한다. 내부 스키마의 영어 필드명과 영어 certainty 코드는 한국어 표시와 일대일 대응한다. 값·근거·확실성은 그대로 보존하며 테스트에서 역변환하여 대조한다. 따라서 영어 JSON 키와 한국어 설명의 언어 차이를 줄이지만 문장 프레이밍·길이까지 같아지는 것은 아니다.
