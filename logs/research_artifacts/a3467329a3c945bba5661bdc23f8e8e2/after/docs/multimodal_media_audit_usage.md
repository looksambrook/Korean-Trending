# 로컬 미디어 실제 읽기 검사

`src/multimodal_media_audit.py`는 파일의 해시와 실제 디코딩 범위를 기록한다. 원본 바이트를 변경하지 않고, 다운로드·전사·모델 로드·이미지 편집·밈 판단·생성·학습을 하지 않는다. `source_provenance_verified=false`이며 입력의 출처 주장은 `declared_provenance`로만 보존한다.

## 입력과 명령

UTF-8 JSONL에 아래처럼 자산을 한 줄씩 적거나, 기존 중첩 데이터 레코드의 `assets` 배열을 제공한다. 경로는 기본적으로 프로젝트 루트 기준이며 밖으로 빠지는 경로는 읽지 않는다. `--asset-root`로 명시한 다른 로컬 자료 루트를 사용할 수 있다. `sha256=null`이면 첫 해시를 측정하되 사전 해시와 일치했다고 주장하지 않는다.

```json
{"asset_id":"existing_local_image_1","local_path":"data/example/image.png","sha256":null,"modality":"image","provenance":null}
```

위 경로는 형식을 보여주는 예시이며 존재하는 자료가 아니다. 실행에는 실제 자산 경로가 필요하다.

```powershell
python -X utf8 -B src/multimodal_media_audit.py --input data/my_assets.jsonl --output-dir data/media_audit/run_v01 --db logs/research.sqlite3
```

출력 디렉터리는 새 경로여야 한다. 입력·코드·설정·decoder 버전이 같은 작업은 출력 경로를 바꿔도 중복으로 차단된다. 필요한 재실행만 `--retry-of <이전 실행 ID> --retry-reason <변경된 상황/재시도 이유>`로 연결한다. 시작을 SQLite에 등록한 뒤 미디어 파일을 읽고 실패·제한도 manifest와 로그에 남긴다. 일부 실패가 있으면 CLI 종료 코드는 2, 중복 차단은 3이다.

## 실제 검사 범위

| 자산 | 사용하는 현재 로컬 기능 | 결과 해석 |
|---|---|---|
| text | 표준 UTF-8 incremental decoder로 전체 바이트 읽기 | UTF-8 텍스트가 실제로 읽혔음을 확인. 의미 이해와 무관 |
| image | Pillow `Image.open`과 각 선택 프레임의 `load()` | 헤더/확장자만 확인하지 않고 픽셀을 실제 디코드. 정적 이미지 전체 또는 애니메이션의 명시적 표본 프레임 |
| audio | Python `wave`로 PCM WAV 프레임 실제 읽기 | 채널·샘플률·길이·읽은 샘플 수. 길면 앞부분만 읽고 `decoded_sample`로 기록 |
| video | 설치된 OpenCV의 로컬 VideoCapture decoder | 실제 선택 프레임의 픽셀 디코드. 프레임 수/FPS 기반 길이는 추정값이며 오디오 트랙은 별도 미검사 |

2026-09-15 환경 탐색에서는 Pillow 11.2.1, OpenCV 배포판 4.13.0.92를 찾았다. `soundfile`, PyAV, `imageio_ffmpeg`, ffmpeg/ffprobe 실행 파일은 찾지 못했다. 실행마다 설치 여부·버전을 다시 기록한다. 현재 사용하지 않는 decoder가 이후 발견되더라도 `adapter_implemented=false`로 표시하고 지원했다고 쓰지 않는다. 설치나 네트워크 호출은 자동 수행하지 않는다.

MP3/AAC 등 현재 음성 adapter가 지원하지 않는 포맷은 `decoder_unavailable`이다. PCM WAV 헤더가 있어도 잘린 샘플이나 읽기 오류는 `decode_failed`다. WAV의 모든 **검사한** PCM 값이 디지털 무음인 경우 `decoded_samples_exact_digital_silence=true`지만 실제 오디오 스트림은 `present`다.

OpenCV 영상 읽기 성공으로 **무음 영상 또는 오디오 트랙 부재를 확정하지 않는다.** `audio_stream_status=uninspected`, `audio_stream_count=null`, `audio_decoded=false`가 남는다. 영상과 음성을 모두 확인해야 하는 연구 입력은 이 상태만으로 G0의 전 모달리티 확인을 통과하지 못한다. ffprobe/PyAV 등의 스트림 점검과 오디오 decode adapter가 이후 필요하다.

## 상한과 해석

기본값은 최대 자산 20개, 파일당 32 MiB, 합계 128 MiB, 입력 JSONL 4 MiB, 자산별 decoder 프로세스 15초, 이미지/프레임당 1,200만 픽셀이다. 이미지 최대 12프레임, 음성 최대 30초/디코드 64 MiB, 영상 최대 12프레임을 읽는다. 라이브러리 디코딩은 별도 프로세스에서 이루어져 제한 시간을 넘기면 종료되고 `decode_timeout`을 기록한다. 크기·범위를 넘으면 `skipped_limit`이다.

영상 기본 `sample_frames`는 알려진 프레임 수에서 균등하게 선택한다. `--video-mode sequential`은 처음부터 순차 읽지만 같은 프레임 수 상한을 지킨다. 전체라고 표기하는 경우도 `all_reported_video_frames`이며, OpenCV가 보고한 모든 영상 프레임을 읽었다는 범위이다. 컨테이너 전체 무결성이나 오디오까지 디코드했다고 쓰지 않는다. 표본 프레임 사이의 손상은 놓칠 수 있다.

읽기 전후의 bounded SHA-256을 비교하며 바이트 변경이 보이면 성공을 취소한다. 이미지/영상의 저장·리사이즈·색상 변환 파일 생성은 하지 않는다. 테스트에서 만드는 작은 PNG/WAV/AVI는 모두 `ai_synthetic` 표본이고 실제 밈 데이터와 섞이지 않는다.

`manifest.json`에는 자산별 상태·측정 해시·범위·decoder 버전·경과/CPU 시간, 실패 사유를 저장한다. 유료 API 사용은 0이지만 로컬 계산 가격은 측정하지 않았으므로 `local_compute_cost=null`이다. 디코더 프로세스의 실제 피크 RAM은 측정하지 않는다. 이 검사는 디코딩 가능성만 확인하며 독립 평가 정답이나 논문 성능 결과가 아니다.

실행 등록 전 식별 준비 단계에서는 같은 자산 수·바이트·경로 제한 안에서 실제 파일 SHA-256을 읽어 `data_snapshots`에 넣는다. 따라서 JSONL을 그대로 둔 채 파일 내용만 바꾸어도 새 조건으로 기록된다. 디코드는 `ResearchLog.begin` 이후에 시작하고, 등록 때 읽은 해시와 실행 직전 해시가 다르면 `source_changed_before_audit`로 중단한다. 입력 JSONL 변경도 검출한다. 자산이 없는 빈 입력은 `empty_input` 오류이며 완료로 처리하지 않는다.

## 실행 증거: 2026-09-15

기존 로컬 JPEG 3장을 `data/research/media_audit_existing_images_v01.jsonl`로 지정하여 읽었다. 실행 ID는 `919155a8770243e08d5c1d833f63d53b`, 결과는 `data/derived/media_audit_existing_images_20260915_v01/manifest.json`이다. 세 파일 모두 Pillow 11.2.1로 1280×720 픽셀의 단일 프레임을 실제 디코드했고, 사전 기록된 SHA-256 및 읽기 전후 SHA-256이 모두 일치했다. 총 파일 크기는 114,983 bytes, 이 실행의 경과 시간은 1.373918초였다. 이 수치는 3개 파일의 관측 결과이며 모델 성능·밈 이해·생성 실험 결과가 아니다. 기존 로컬 파일의 원출처나 권리 주장은 재검증하지 않았다. 실제 연구용 오디오·영상의 읽기 성공 사례는 이번 실행에 없다. 이 실행은 후속 자산 식별·빈 입력 수정 전의 코드 버전에 해당하며, 원래 결과와 코드 해시를 보존하고 재실행하지 않았다.

AI가 작성한 합성 fixtures의 최초 6개 테스트는 모두 통과했다(`19ef8e8e7b214257a32292dbb1521e32`, `logs/media_audit_tests_19ef8e8e7b214257a32292dbb1521e32.txt`). 이후 추가한 GIF 경계 테스트 1개도 통과했다(`8ac37a6cb544443c8a04540dcf0f4f19`, `logs/media_audit_animation_test_8ac37a6cb544443c8a04540dcf0f4f19.txt`). 3프레임 GIF에서 첫 프레임만 읽으면 `frame_count_declared=3`, `decoded_frame_indices=[0]`, `status=decoded_sample`, `coverage=selected_image_frames`가 된다. GIF/WebP 등의 선언된 전체 프레임 수와 실제 읽은 프레임은 구별한다. 첫 프레임 성공을 전체 애니메이션 검증으로 보고하지 않는다.

파일 내용만 바뀐 경우 새 실행 ID가 생기고 빈 입력이 실패하는 집중 테스트 1개도 통과했다(`346d31e7bcb54d7581f72ff48227c43f`, `logs/media_audit_identity_test_346d31e7bcb54d7581f72ff48227c43f.txt`). 이 마지막 수정 후에는 해당 경계 테스트만 실행했고 전체 테스트를 다시 실행하지 않았다.
