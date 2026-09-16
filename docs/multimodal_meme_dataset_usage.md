# 멀티모달 밈 자료 적재·시점별 준비 사용법

이 도구는 사용자가 준비한 로컬 자료를 보존하고 비교 실험용 입력을 나누는 준비 도구다. 밈 수집, 의미 판정, RAG 검색, 파인튜닝, 콘텐츠 생성은 실행하지 않는다. 테스트용 AI 합성 자료를 실제 밈 자료로 사용하지 않는다.

## 입력

UTF-8 JSONL의 정확한 필드는 [스키마](../data/schemas/multimodal_meme_record.schema.json)를 따른다. [연구 프로토콜](multimodal_creation_protocol_v01.md)의 개념 테이블과 이 중첩 JSONL 계약은 구별한다.

각 밈 레코드에 ID·계열·버전·가용 시각·출처 구분을, 각 자산에 프로젝트 안의 실제 파일 경로·모달리티·MIME·원출처 그룹·이용 근거·목적을 기록한다. 게시 시각과 실제 확보 시각은 다르다. 모르는 출처 URL·게시 시각은 허용된 `null`로 남기며 임의로 만들지 않는다.

원자료는 `source_evidence`, 제작용 목표 자산은 `training_target`, 평가 전용 자산은 `evaluation_only`로 구분한다. 제작 예시에도 `training_supervision` 또는 `evaluation_only`를 명시한다. 실제 자료, 연구자가 작성한 예시, AI 합성 자료를 각각 `actual_collected`, `researcher_authored`, `ai_synthetic`으로 기록한다.

## 실행

아래 `<...>`는 실제 입력에 맞춰 채우는 자리표시자다. 이 문서는 해당 파일이나 스냅샷이 존재한다고 주장하지 않는다. 모든 출력 디렉터리는 프로젝트 안의 **아직 없는 경로**여야 한다.

```powershell
python -X utf8 -B src/multimodal_meme_dataset.py validate --input "<입력 JSONL>"
python -X utf8 -B src/multimodal_meme_dataset.py ingest --input "<입력 JSONL>" --output-dir "<새 데이터 디렉터리>"
python -X utf8 -B src/multimodal_meme_dataset.py snapshot --dataset "<데이터 디렉터리>" --cutoff "<시간대 포함 ISO 시각>" --output-dir "<첫 스냅샷 디렉터리>"
```

지속 갱신의 다음 스냅샷에는 직전 스냅샷을 전달한다. CLI의 선택 인자이지만 지속 비교 프로토콜에서는 생략하지 않는다.

```powershell
python -X utf8 -B src/multimodal_meme_dataset.py snapshot --dataset "<갱신 데이터 디렉터리>" --cutoff "<다음 시각>" --output-dir "<다음 스냅샷 디렉터리>" --prior-partition "<직전 스냅샷>/manifest.json"
```

`--split-config`에는 `seed`와 train/dev/test `fractions`를 제공할 수 있다. 기본 70/15/15는 준비 도구의 기본값이며 연구자가 확정한 표본 설계나 KCI 조건이 아니다. 기존 분할과 연결된 새 자료는 배정을 계승한다. 서로 다른 기존 분할을 잇는 연결은 자동 재배정하지 않고 내보내기를 차단하며, 충돌 ID·분할을 `partition_conflict.json`에 남긴다.

## 출력과 재시도

- `rag_context.jsonl`과 `shared_training_examples.jsonl`: 양쪽에 허용하는 적응 자료. `rag_examples.jsonl`과 `finetune_examples.jsonl`의 제작 학습 쌍은 같은 내용으로 내보낸다.
- `evaluation_inputs.jsonl`: 평가 요청. `evaluation_targets.protected.jsonl`과 전체 `records.jsonl`은 평가 정답을 포함할 수 있으므로 검색·학습에 넣지 않는다. `.protected`라는 이름 자체가 OS 접근 제어를 설정하는 것은 아니다.
- `manifest.json`과 `split_config.json`: 입력·출력 해시, 시점, 출처 구분, 고정 분할 이력과 검사 한계.
- 등록 후 실패한 작업은 `failure.json`과 SQLite 실패 기록을 남긴다. JSON 파싱 전 오류 등 등록에 도달하지 않은 사용 오류는 CLI 오류로 확인한다.

기존 작업과 동일한 명세는 차단한다. 실패 원인을 확인한 뒤 같은 명세로 재시도할 이유가 있으면 새 출력 경로와 `--retry-of "<직전 run_id>" --retry-reason "<구체적 이유>"`를 전달한다. 코드·자료·설정이 바뀌면 다른 명세다. 출력 경로를 바꾸는 것만으로 같은 실험의 중복 방지를 우회하지 않는다.

## 확인한 범위

원파일 복사와 바이트 해시·선언된 MIME/모달리티 일치는 확인한다. **이미지 디코딩·음성 재생·영상 트랙 검사는 아직 하지 않으므로 `media_decode_verified:false`다.** 타임스탬프는 자료 작성자가 제공한 값이며 역사적 가용성을 독립 검증한 것은 아니다.

현재 내보내기는 준비 단계의 계열 train/dev/test 분할이다. 평가 계열 안에서 과거 적응 지원과 이후 평가 요청을 나누는 두 축, 실제 모델 실행, 시점별 비용·독립 품질 평가는 별도로 구현·검증해야 한다.

최종 집중 테스트 기록은 [v02 검증 결과](../logs/multimodal_meme_dataset_validation_20260915_v02.json), 연구 로그 ID는 `5cf51061295943018366e3a278f09d2f`다. AI 합성 입력으로 시간·정답 격리, 원본 보존·해시 오류·재시도, 고정 분할·새 연결 충돌의 3개 테스트를 통과했다. 실제 밈으로 제작 성능을 평가한 결과는 아니다.
