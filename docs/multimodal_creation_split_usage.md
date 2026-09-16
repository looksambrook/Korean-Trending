# 계열 분할과 시간 역할을 분리하는 자료 준비

이 문서는 `multimodal_creation_split.py`의 사용법이다. 실제 연구 자료·모델·독립 정답으로 실험한 결과가 아니다. 연구 설정의 미정 값을 바꾸거나 제안한 실험 방식을 사용자가 선택했다고 처리하지 않는다. 기존 적재기와 원본·실험 로그는 수정하지 않는다.

입력은 기존 [중첩 스키마](../data/schemas/multimodal_meme_record.schema.json)로 적재한 데이터셋과 이전 고정 partition의 `manifest.json`이다. 최초 고정 partition은 기존 `multimodal_meme_dataset.py snapshot`으로 준비한다. 이후에는 이 모듈이 내보낸 manifest도 연결할 수 있다. 부모 partition의 판단 시점은 새 `source_cutoff` 이하여야 한다.

```powershell
python -X utf8 -B src/multimodal_creation_split.py --dataset DATASET_DIR --partition PREVIOUS/manifest.json --output-dir NEW_DIR --mode locked_family_past_support --source-cutoff ISO_TIMESTAMP --evaluation-at ISO_TIMESTAMP --locked-family FAMILY_ID --support-permissions POLICY.json
```

`--mode`는 반드시 명시한다. `train_only`는 고정 train 계열의 과거 자료만 적응에 허용한다. `locked_family_past_support`는 train 계열과 `--locked-family`로 지정한 고정 test 계열의 과거 지원을 양쪽에 허용한다. 여러 평가 계열은 옵션을 반복한다. test 계열을 train으로 옮기지 않으며, 이 모드를 zero-shot 계열 평가라고 부르지 않는다. 어느 모드든 방법·예산·평가 절차가 잠겼다는 뜻은 아니다.

`row.split`은 방법 개발용 계열 배정이고, `creation_examples.purpose`는 지원과 평가 역할이다. `example.split`은 전달된 메타데이터로 보존하며 계열 배정을 변경하는 데 쓰지 않는다. 지원은 `source_cutoff`에서 고른 과거 레코드 전체 버전에 근거한다. 평가는 별도로 `evaluation_at`에서 레코드 버전을 고른다. 나중 레코드의 새 해설·목표·출처를 과거 지원에 끼워 넣지 않는다.

사용 정책은 자료 작성자가 선언한 데이터셋 공통값과 필요한 예외를 읽는다. 아래는 **정책 형식 예시**이며 실제 자료의 권한을 부여하지 않는다.

```json
{
  "declared_by": "자료 정책 작성자 식별자",
  "basis": "자료 묶음에 명시된 이용 범위",
  "default": {"retrieval": true, "training": true},
  "assets": {"예외 자산 ID": {"training": false}},
  "examples": {}
}
```

이미 정해진 사용 범위를 재승인받는 절차는 없다. 정책이 없거나 한쪽 용도가 미확인이면 그 자산·예시는 공통 지원 풀에서 제외하고 사유를 기록한다. 이 도구가 법적 사용권한을 판정하거나 보장하지 않는다. `available` 상태·명시 사용 정책·시각이 충족되어야 하며, source 시점에 알려진 평가 자산과 동일 바이트·명시된 후손 파생물, 평가 주석에서 유래한 지식은 제외한다. `source_group_id`는 계열 분할을 묶는 데 사용한다. 그 그룹에 나중 평가 목표가 있다는 이유만으로 이미 허용된 공개 과거 템플릿이나 별도 형제 자산을 역방향으로 골드 파생물로 취급하지 않는다.

출력은 다음과 같다.

- `shared_context.jsonl`: 허용 과거 자산·지식·연결. CR/CF가 동일 파일과 해시를 참조한다.
- `shared_supervision.jsonl`: 허용된 과거 `training_supervision`의 요청–목표 쌍. CR에도 같은 쌍을 제공한다.
- `evaluation_briefs.jsonl`: 명시한 평가 계열의 `evaluation_only` 요청. 적응 입력에는 금지한다.
- `protected_gold.jsonl`: 전달받은 목표·평가 주석의 출처와 provenance를 보존한다. 독립 정답이 검증됐다고 표시하지 않는다.
- `exclusions.jsonl`, `partition_manifest.json`, `manifest.json`: 제외 이유, 고정 배정의 후속 버전, 출처·해시·조건·공통 입력 목록.

동일 지원 풀은 실제 검색 노출량·학습 순서·반복까지 같다는 뜻이 아니다. 이 모듈은 인덱스, 학습 체크포인트, 모델 요청, 생성물 또는 평가 점수를 만들지 않는다. native 미디어 디코딩도 수행하지 않는다.

과거에 지원으로 준비한 같은 `meme_id/example_id`가 나중 버전에서 평가용으로 바뀌거나, 다른 ID로 동일 요청–목표 쌍을 다시 넣으면 새 평가로 내보내지 않는다. 현재 호출과 이전 두 축 manifest의 지원 준비 목록을 함께 확인하고 `failure.json`에 충돌 ID를 남긴다. 이 목록은 **내보내기 준비 이력**이며 실제 모델이 학습했다는 기록이 아니다. 기존 과거 지원 파일은 소급 삭제하지 않는다. 의도적인 유지력·재평가 진단은 별도 조건을 설계해야 하며 현재 모드가 자동으로 허용하지 않는다.

새 연결이 서로 다른 고정 분할을 잇는 경우 적응·평가 파일을 내보내지 않고 `failure.json`에 충돌 ID와 분할을 기록한다. 현재 구현은 영향 성분만 부분 제외하기보다 **그 호출 전체를 차단**한다. 기존 자료를 다른 분할로 옮기지 않는다. 다음 시점에는 이전 출력 manifest를 `--partition`으로 전달한다. 평가 시점에서 추가된 배정을 포함하므로 그 시점보다 앞선 source cutoff에 재사용하지 않는다.

모든 준비 실행은 검증·내보내기 전에 SQLite에 등록한다. 실패는 보존하며 동일 명세의 재시도는 새 출력 폴더와 `--retry-of RUN_ID --retry-reason "이유"`를 쓴다. DB 위치는 `--db PATH`로 지정할 수 있다. 입력 파일 자체가 파싱 불가능하거나 출력 경로가 기존 자료와 충돌하는 등 등록 이전의 기본 경로·입력 검사 오류는 CLI 오류로 반환한다.

검증 범위는 AI 합성 파일을 사용한 역할·시점·출처·고정 분할 격리다. 실제 멀티모달 연구 데이터, 방법 잠금, 모델 실행, 독립 평가, 비용 비교 및 논문 결과는 별도 작업이다.
