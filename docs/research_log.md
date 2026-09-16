# 연구 실행 로그와 중복 방지

`src/research_log.py`는 Python 표준 라이브러리만 사용하는 로컬 실행 등록부다. 기본 원장은 `logs/research.sqlite3`이며 JSONL은 원장에서 만든 읽기용 내보내기다. 원장과 결과 파일을 보존한다. 이 도구 자체는 YouTube 수집, 모델 호출, 사람 평가를 실행하지 않는다.

## 실행 전후 절차

1. 실행 목적, 입력 스냅샷, 코드·프롬프트·설정의 실제 콘텐츠 해시로 명세를 만든다. 관찰 시점과 모델 버전을 사용한다면 함께 고정한다.
2. `check`로 같은 명세의 이력을 확인한다. **수집·API 호출·평가·검증을 실행하기 직전에 반드시 `begin`을 성공시킨다.** `check`만으로는 실행 자리를 확보하지 못한다. `begin`은 SQLite 쓰기 트랜잭션 안에서 중복을 확인하고 실행 ID와 시작 이벤트를 함께 저장한다.
3. 반환된 `run_id`에 해당하는 작업만 실행한다. 원본 결과와 실행 증거를 별도 파일로 남긴다. 인증정보·개인정보를 결과 파일이나 로그에 붙이지 않는다.
4. 실제 종료 상태, 확인한 범위, 결과 파일과 측정 비용으로 `finish`한다. 결과를 확인하지 않고 `succeeded`를 쓰지 않는다. `succeeded`는 명세의 작업이 완료되었다는 뜻이며 연구 가설이나 모델 성능이 입증되었다는 뜻은 아니다.
5. 필요하면 새로운 경로로 `export`한다. 기존 JSONL을 수정해도 원장의 상태는 바뀌지 않는다.

시작 전 확인과 실행을 한 프로그램에서 연결할 때는 CLI 결과의 종료 코드를 확인하거나 `ResearchLog.begin()`의 예외를 처리한다. 등록에 실패한 뒤 실제 호출을 계속하면 중복 방지 규칙을 우회하게 된다. 기존의 다른 실행 스크립트에는 이 규칙이 자동으로 적용되지 않는다.

| stage | 기록할 실제 작업 | 성공으로 간주하면 안 되는 것 |
|---|---|---|
| `desk_research` | 공개 문헌·공식 문서 검토, 출처와 확인 범위 정리 | 검색 결과만 읽고 본문 전체를 확인했다고 기재 |
| `access_probe` | 정해진 범위의 실제 접근 시험과 응답 확인 | 문서에 API가 있다는 이유만으로 계정 접근 성공 기재 |
| `offline_validation` | 로컬 코드·입출력·계획 가정 검증 | 작성·합성 입력의 통과를 실제 밈 인식 성능으로 해석 |
| `model_experiment` | 버전·입력·조건이 고정된 실제 모델 실행 | 미실행 계획이나 예산 계산을 모델 결과로 기재 |
| `human_evaluation` | 실제 사람의 검수·주석·평가 | 자동 구조화 결과를 사람의 판단으로 기재 |

## 명세와 지문

필수 필드는 `objective`, `stage`, `parameters`, `data_snapshots`, `code_hashes`, `prompt_hashes`, `config_hashes`, `modality`, `provenance`다. 선택 필드는 `time_cutoff`, `model_version`이다. 그 밖의 실험 조건은 `parameters`에 둔다.

- `data_snapshots`: `[{"id": "안정적인 입력 버전 ID", "sha256": "실제 64자리 콘텐츠 해시"}]`. 입력 목록이 여러 파일이면 각 파일 또는 파일별 해시를 담은 불변 매니페스트를 해시한다. 수집 전 접근 시험은 입력 자료가 없을 수 있으므로 빈 배열이 가능하다. 이때 조회 조건·시간창·채널 목록·페이지 제한을 `parameters` 또는 설정 해시에 고정한다.
- `code_hashes`, `prompt_hashes`, `config_hashes`: `{ "안정적인 논리 이름": "실제 SHA-256" }`. 사용하지 않는 범주만 `{}`로 둔다. 작업 디렉터리·결과 파일 경로를 임의로 바꿔 새 실험인 것처럼 등록하지 않는다. 해시 값은 `python src/research_log.py hash <파일>`이나 `hash_file()`로 구한다.
- `parameters`: D/U 조건, 탐지 방식, 알려진 이름 지정 여부, 플랫폼, 난수 시드, 샘플링 설정, 예산 **가정**, 검색 범위 등 결과에 영향을 주는 조건. 같은 계획 안에서 독립 반복을 예정했다면 시드와 반복 번호를 사전에 정의한다. 중복 차단을 피하려고 임의의 시간값·난수값을 추가하지 않는다.
- `modality`: `text`, `metadata`, `audio`, `image`, `video` 등 실제 사용 범주의 목록.
- `provenance`: `actual_collected`(실제 자료), `researcher_authored`(연구자 작성), `ai_synthetic`(AI 합성), `mixed`, `not_applicable`. 혼합 자료는 해시로 고정한 입력 매니페스트에도 항목별 출처 유형을 기록한다. 테스트의 합성 입력은 실제 관찰 자료가 아니다.
- `time_cutoff`: 시간대가 있는 ISO 시각. 동일 시각은 UTC로 정규화한다. 시점 T 제한은 이 필드를 적는 것만으로 집행되지 않는다. 입력·근거·연결·지식 매니페스트의 당시 이용 가능 시각과 버전을 별도로 검증해야 한다.
- `model_version`: 실제 사용한 모델의 고정 버전 또는 공급자가 반환한 버전. 이름만 같고 내부 버전이 불명확하면 그 한계를 명시한다. RAG·문맥 제공 여부는 `parameters`에 기록하고 가중치 학습과 구분한다.

지문은 정규화한 전체 명세의 SHA-256이다. JSON 필드 순서, 해시 대소문자, 시간대 표현, 스냅샷·모달리티 목록 순서, 동등한 정수/실수 표기는 중복 차단을 회피하지 못한다. 일반 매개변수 배열의 순서는 의미가 있을 수 있어 보존한다. 목적 문구나 논리 ID를 바꾼 실행까지 의미적으로 추론하여 같은 실험으로 판정하지는 않으므로 팀 전체가 안정적인 이름과 같은 원장 경로를 사용한다.

## 로컬 검증 예시

다음은 **테스트 실행을 등록하는 사용 예시**이며 이 문서의 명령을 적었다는 이유만으로 실행 기록이 생기지는 않는다. 임시 테스트 자료는 AI 합성 자료로 표시한다. 프로젝트 루트에서 아래 Python 코드로 명세 파일을 만든다.

```python
import json
import sys
from pathlib import Path
sys.path.insert(0, "src")
from research_log import hash_file

spec = {
    "objective": "연구 로그의 동시 실행 및 원자적 기록 검증",
    "stage": "offline_validation",
    "parameters": {
        "command": "python -m unittest discover -s tests -p test_research_log.py -v",
        "purpose": "TEST_ONLY; 실제 연구 데이터나 모델 호출 없음",
    },
    "data_snapshots": [],
    "code_hashes": {
        "research_log": hash_file("src/research_log.py"),
        "test_research_log": hash_file("tests/test_research_log.py"),
    },
    "prompt_hashes": {},
    "config_hashes": {},
    "modality": ["text"],
    "provenance": "ai_synthetic",
}
Path("logs").mkdir(exist_ok=True)
Path("logs/local-check-spec.json").write_text(
    json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
)
```

PowerShell에서 등록 성공 후에만 검증을 실행하고, 실제 종료 코드에 따라 마친다. 아래 결과 파일 경로가 이미 있다면 먼저 기존 기록을 확인하고 새 실행 ID에 맞는 새 경로를 사용한다.

```powershell
python src/research_log.py check --spec logs/local-check-spec.json
$run = python src/research_log.py begin --spec logs/local-check-spec.json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $run.run_id) { throw '등록 실패: 검증을 실행하지 않음' }
$resultPath = "logs/check-$($run.run_id).txt"
python -m unittest discover -s tests -p test_research_log.py -v *> $resultPath
$testExit = $LASTEXITCODE
if ($testExit -eq 0) {
    python src/research_log.py finish --run-id $run.run_id --status succeeded --notes '로컬 레지스트리 테스트 종료 코드 0. 실제 밈 성능 실험은 수행하지 않음.' --artifact $resultPath
} else {
    python src/research_log.py finish --run-id $run.run_id --status failed --notes "로컬 테스트 종료 코드 $testExit. 결과 파일에서 실패 원인을 확인해야 함." --artifact $resultPath
}
python src/research_log.py status --run-id $run.run_id
python src/research_log.py list
```

모든 명령은 전역 옵션 `--db <경로>`를 **하위 명령 앞**에 넣어 원장을 지정할 수 있다. 기본 원장은 현재 작업 디렉터리 기준이므로 프로젝트 루트에서 실행한다. `begin` 중복 종료 코드는 `3`, 일반 검증·입출력 오류는 `2`, 성공은 `0`이다. `check`는 중복 여부를 JSON으로 반환하며 중복이어도 조회 자체가 성공하면 종료 코드는 `0`이다.

라이브러리에서도 같은 규칙을 쓴다.

```python
from research_log import ResearchLog, DuplicateRunError
log = ResearchLog("logs/research.sqlite3")
run = log.begin(spec)  # 중복이면 DuplicateRunError: 후속 작업을 실행하지 않는다.
# 여기서 명세에 정의한 작업을 실제로 수행하고 증거 파일을 보존한다.
# 작업 결과를 확인한 뒤 log.finish(run["run_id"], status=..., notes=..., artifacts=[...])
```

## 재시도, 종료, 비용

`started`, `succeeded`, `failed`, `cancelled` 어느 상태든 동일 명세를 조용히 재실행할 수 없다. 재시도는 최신 실행이 종료 상태일 때만 가능하며 이전 실행 ID와 구체적인 사유가 모두 필요하다.

```powershell
python src/research_log.py begin --spec logs/local-check-spec.json --retry-of <최신_종료_run_id> --retry-reason '<확인한 실패 원인 또는 의도한 반복 사유>'
```

오래된 `started`를 시간 경과만으로 해제하지 않는다. 실제 프로세스·세션·작업 핸들과 결과 파일을 확인한다. 작업이 살아 있다면 같은 핸들을 관찰한다. 종료 또는 핸들 부재를 확인한 경우에만 사실과 증거 파일을 남기고 `failed`/`cancelled`로 종료한 뒤 명시적으로 재시도한다. 원래 실행은 보존되며 재시도는 `retry_of`로 연결된 새 실행이다. 완료 기록의 수정·삭제는 제공하지 않는다. 잘못 기록한 사실은 별도 정정 작업과 정정 문서를 남기고 원래 ID를 참조한다.

`finish --artifact`는 여러 번 지정할 수 있으며 파일의 절대 경로·SHA-256·바이트 수를 기록한다. 원장 자체는 결과 파일로 등록할 수 없다. 아티팩트를 나중에 바꾸면 기록된 해시와 달라지므로 원본을 보존한다. 파일 내용 전체는 DB에 복사하지 않는다. 결과 파일과 원장을 함께 백업해야 한다.

비용은 `finish --cost-json <실제비용.json>` 또는 `actual_cost={...}`로 넣는다. 허용 필드는 `amount`, `currency`, `api_units`, `input_tokens`, `output_tokens`, `human_minutes`이며 숫자는 0 이상 또는 `null`이다. 금액에는 `USD` 같은 세 글자 통화 코드가 필요하다. `api_units`는 호출 수나 돈과 별개의 쿼터 단위로 정의하고 작업 명세에 단위를 적는다. API마다 다른 쿼터를 근거 없이 합산하지 않는다. 자동 작업과 사람 검수는 각각 별도 실행으로 기록하며 실제 사람 투입 시간을 구분한다. 모르는 비용은 생략하거나 `null`로 둔다. **미확인은 0원이 아니다.** 예산 추정은 `parameters`/계획 산출물에만 두고 실제비용 칸에 넣지 않는다.

```powershell
python src/research_log.py export --output logs/research-events-<새로운_내보내기_ID>.jsonl
```

처음 로그 체계를 도입하면서 이미 수행한 문헌 검토를 기록할 때는 명세의 `parameters.retrospective_registration=true`와 실제 작업 범위·근거를 명시한다. 자동 생성 `started_at`은 등록 시각이며 과거의 실제 작업 시작 시각으로 바꾸지 않는다. 사후 등록을 사전등록이라고 부르지 않는다.

## 보장 범위와 검증

SQLite의 `BEGIN IMMEDIATE`, 고유 인덱스, 전체 동기화 모드와 트랜잭션으로 같은 로컬 원장의 동시 등록과 기록 실패를 처리한다. 시작/종료 이벤트는 추가만 가능하며 완료 실행은 API 및 DB 트리거에서 덮어쓰기를 막는다. 이 도구가 임의 DB 편집·파일 삭제를 막는 보안 장치는 아니다. 서로 다른 DB, 복제된 원장, 네트워크 드라이브의 여러 노드 간 중복은 막지 못한다. 단일 로컬 원장을 기준으로 사용한다.

비밀 값은 수집하지 않는다. 인증 필드 이름, Bearer/API 키 형태, 인증정보가 담긴 URL 등 인식 가능한 패턴은 저장 전에 거절한다. 모든 임의 문자열의 비밀 여부를 완벽히 판별할 수는 없으므로 원본 HTTP 헤더·환경변수·계정 토큰을 명세·메모·파일경로에 넣지 않는다. 인증정보는 별도 환경에서 주입하고 로그에서는 제외한다.

`tests/test_research_log.py`는 임시 디렉터리에서만 동작한다. 6개 독립 프로세스의 동일 명세 동시 시작 시 한 개만 성공하는지, 입력 검증 실패 시 DB를 만들지 않는지, 이벤트 쓰기 실패 시 실행 레코드까지 롤백되는지, 재시도 연결·완료 불변성·아티팩트 해시·비용·비밀 패턴 거절·JSONL 독립성을 확인한다. 이 검증은 로컬 로그의 동작 증거이며 YouTube 접근성이나 밈 식별 정확도의 증거가 아니다.
