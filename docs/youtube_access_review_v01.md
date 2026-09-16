# YouTube 접근성 검토 v0.1

검토일: 2026-09-15 KST. 공식 문서 본문을 읽은 공개 문헌 조사이다. API 키 생성·가입·Data API 호출·동영상/댓글 수집·모델 실험은 수행하지 않았다. 따라서 아래의 **문서상 지원**은 **이 프로젝트에서 접근 성공**과 다르다. 비용은 쿼터와 현금을 분리한다. 최신 계획의 다중 플랫폼 목표를 유지하며 이번 단계만 YouTube를 먼저 검증한다.

## 판단과 우선 후보

**기술적으로는 공개 동영상 메타데이터와 댓글로 한국어 밈 발견·맥락 이해의 제한된 파일럿을 준비할 수 있다. 전체 YouTube 밈 발견이나 영상·음성 밈 전체 이해가 가능하다고 아직 결론 내릴 수 없다.** 특히 API 데이터의 분석·집계·보존에 관한 허용 범위가 실제 수집보다 먼저 정리되어야 한다.

| 우선순위 | 관찰 후보 | 밈 이름 없는 발견에 쓰는 방식 | 표본 한계와 현재 상태 |
|---|---|---|---|
| 1 | 사전에 선정·동결한 다양한 한국어 채널의 uploads 목록과 공개 댓글 | 밈 전문 채널만 고르지 않고 게임·예능/토크·일상 등 층별 채널을 정한 뒤 기간 내 문서를 동일 규칙으로 관찰 | 채널 선택 편향. 층·채널·기간은 제안이며 미확정. 기술 접근 미검증 |
| 2 | `search.list`의 밈 이름 없는 검색/기간 탐색 | `q` 미지정 또는 사전 고정 일반 주제어; 검색 조건·결과 순위·페이지를 기록 | 전체 업로드 흐름이나 무작위 표본이 아님. 기간·정렬을 써도 누락 가능. 기술 접근 미검증 |
| 3 | 한국 지역 `mostPopular` 차트 | 관찰 시점의 차트 선정 영상에서 용례를 찾는 보조 경로 | 현재 Music/Movies/Gaming 범위의 차트. 한국어 밈 전체의 유행 대표값으로 사용 불가 |
| 진단용 별도 | 이미 아는 밈 이름/표현 검색 | 알려진 계열의 추가 용례·접근 가능성 확인 | `known_name_lookup`으로 표시. 독립 발견의 성공 사례로 세지 않음 |
| 수동 확인 보조 | 공개 영상 재생과 `Show transcript` | 텍스트만으로 해석하기 어려운 후보의 장면·말투·자막을 사람이 확인 | 사람 관찰과 자동 입력을 구분. 대량 자막 다운로드나 미문서 API 접근 허용을 뜻하지 않음 |

우선순위는 문서에 근거한 연구 설계 제안이다. 실제 채널 목록이나 확보된 자료 수를 의미하지 않는다. 위 경로별 표본을 합칠 때 선택 경로를 유지하고 동일 영상·댓글을 중복 관측으로 처리한다.

## 접근 가능 필드·범위·비용

모든 목록의 필드는 요청 `part`/`fields`, 공개 상태 및 리소스에 따라 누락될 수 있다. 공용 읽기 요청의 기본 경로는 개인 Google 계정 → Cloud 프로젝트 → YouTube Data API 활성화 → API 키이며, 사용자 권한이 필요한 요청은 OAuth가 추가된다. 서버 배포 자체는 이 단계의 필수 조건이 아니다. [Data API Overview](https://developers.google.com/youtube/v3/getting-started)

| 방법 | 문서상 얻을 수 있는 정보 | 범위·시간·정렬 제약 | 1회 쿼터 / 권한 | 연구에서의 판정 |
|---|---|---|---|---|
| `channels.list` | `id`, `snippet`의 이름·설명, `contentDetails.relatedPlaylists.uploads` | `id`/`forHandle` 등으로 채널 식별; 업로드 목록 ID를 먼저 조회 | 기타 버킷 1 unit, 공개 범위 API 키 | 채널 코호트 시작점. 구독자 수나 채널 국적만으로 한국어 밈 모집단 정의 불가 |
| `playlistItems.list` | 영상 ID, 제목·설명, 목록 내 위치, 목록 추가 시각, `contentDetails.videoPublishedAt` | 페이지 최대 50. 서버측 게시 기간 필터 없음; 페이지·시간 기록 후 영상 게시 시각으로 필터 | 기타 버킷 1 unit/페이지 | 알려진 밈 이름 없이 선정 채널 업로드를 관찰하는 우선 경로 |
| `search.list` | 결과 ID, 제목·설명·채널·게시 시각·썸네일 등 `snippet` | 페이지 최대 50; `publishedAfter/Before`, `order`, `q`, `regionCode`, `relevanceLanguage`. 색인 지연·불완전 결과 경고가 있음 | 별도 Search Queries 버킷 **1 unit/호출, 기본 100회/일** | 문서상 지원. 한국어 전체 흐름으로 간주하지 않음 |
| `videos.list(id=...)` | `snippet` 제목·설명·태그·게시 시각·채널; `contentDetails.duration/caption`; 공개 `statistics.viewCount/likeCount/commentCount`; 공개 상태 등 | 현재 메타데이터와 현재 누적 통계. 과거 시점 값이나 재게시/계열 정답을 제공하지 않음. ID 요청에는 `maxResults`/`pageToken` 미지원 | 기타 버킷 1 unit/호출 | 텍스트 문맥과 접근 상태 보강; 모든 필드가 항상 반환된다고 가정하지 않음 |
| `videos.list(chart=mostPopular)` | 해당 지역·카테고리 차트의 영상 리소스 | `regionCode`, `videoCategoryId`; 임의 과거 차트 요청용 시점 매개변수 없음. 페이지별 결과와 실제 반환 개수를 기록 | 기타 버킷 1 unit/페이지 | 인기 영상 관찰 보조. 2025년 변경 전 Trending 자료와 구분 |
| `commentThreads.list` | 최상위 댓글, 스레드/영상 관계, 일부 답글, `totalReplyCount` | 페이지 최대 100; `order=time`/`relevance`; `searchTerms`는 후보 보강용으로 분리. 게시 기간 필터 없음. 비활성 댓글은 `commentsDisabled` 등 | 기타 버킷 1 unit/페이지; 공개 댓글 기본 범위 | `textFormat=plainText`로 `textDisplay` 사용. 페이지 중단·삭제·비활성은 결측이지 비밈/댓글 0의 증거가 아님 |
| `comments.list(parentId=...)` | 지정 최상위 댓글의 공개 답글, `parentId`, 게시·수정 시각, 좋아요 | 페이지 최대 100; 최상위 댓글에 대한 답글. thread의 일부 답글만 읽어 전체라 주장하지 않음 | 기타 버킷 1 unit/페이지 | 답글 누락은 관찰 범위에 명시; 부모 문맥 유지 |
| `captions.list` | 자막 트랙 ID·메타데이터 | 실제 자막 텍스트는 포함하지 않음; 권한 부족 시 403 | 기타 버킷 **50 units**, OAuth 필요 | 공용 영상의 모든 자막을 API 키로 가져오는 경로가 아님 |
| `captions.download` | 권한 있는 트랙의 실제 자막, 지원 형식·언어 옵션 | **영상 편집 권한 필요** | 기타 버킷 **200 units**, OAuth + 영상 편집 권한 | 현재 권한으로 타인 영상의 자막 대량 확보를 전제하지 않음 |
| YouTube 공개 `Show transcript` UI | 자막이 있는 영상의 읽기용 transcript, 자막 구간 이동 | 개별 영상에서 버튼/자막 가용성 확인 필요. UI 열람 가능성과 자동 수집 권한·API 제공은 별개 | API 쿼터 해당 없음; 사람 시간 별도 | 표본의 수동 맥락 검토 후보. 이번 조사에서는 개별 영상 UI 성공도 검증하지 않음 |
| Analytics/Reporting·영상 원본 | 시청 시간·노출·고유 시청자 같은 소유자 분석, 원본 음성/영상 | 공용 Data API 필드로 가정할 수 없음. `fileDetails`는 영상 소유자만 | 소유자 승인/별도 허용 범위 필요 | 초기 공용 관찰의 전제로 사용하지 않음 |

표 근거: [Channels list](https://developers.google.com/youtube/v3/docs/channels/list), [Channels resource](https://developers.google.com/youtube/v3/docs/channels), [PlaylistItems list](https://developers.google.com/youtube/v3/docs/playlistItems/list), [PlaylistItems resource](https://developers.google.com/youtube/v3/docs/playlistItems), [Search list](https://developers.google.com/youtube/v3/docs/search/list), [Videos list](https://developers.google.com/youtube/v3/docs/videos/list), [Videos resource](https://developers.google.com/youtube/v3/docs/videos), [CommentThreads list](https://developers.google.com/youtube/v3/docs/commentThreads/list), [Comments list](https://developers.google.com/youtube/v3/docs/comments/list), [Captions list](https://developers.google.com/youtube/v3/docs/captions/list), [Captions download](https://developers.google.com/youtube/v3/docs/captions/download), [View video transcripts](https://support.google.com/youtube/answer/15930243?hl=en).

`regionCode=KR`는 검색에서 한국에서 볼 수 있는 영상을 뜻하며 한국 제작자나 한국어만을 뜻하지 않는다. `relevanceLanguage=ko`도 엄격한 언어 필터가 아니다. `channelId + type=video` 검색은 일부 소유자 필터가 없을 때 최대 500영상 제한이 있어 업로드 코호트를 검색으로 대신하지 않는다. API는 `order=date`에도 색인 지연·누락 가능성을 밝히고 최신 채널 영상에는 uploads playlist를 권한다. [Search list](https://developers.google.com/youtube/v3/docs/search/list)

댓글의 `textOriginal`은 인증된 댓글 작성자에게만 제공된다고 문서에 명시되어 있다. 일반 공개 읽기에는 `textDisplay`를 사용하며 plain text도 링크 표현 등이 바뀔 수 있다. 작성자 채널 ID는 가용할 때만 제공되며, 실명·프로필·작성자 ID 수집을 기본값으로 넣지 않는다. 사용자 식별 없이 독립 사용을 얼마나 판정할 수 있는지와 필요한 허용 범위를 먼저 확인하고, 영상/채널 다양성은 독립 사용자 수의 대용값이라고 밝힌다. [Comments resource](https://developers.google.com/youtube/v3/docs/comments), [Developer Policies Guide](https://developers.google.com/youtube/terms/developer-policies-guide)

## Shorts와 인기 수치의 해석

- 검색의 `videoDuration=short`는 **4분 미만**이며 Shorts 전용 필터가 아니다. 조사한 공용 영상 스키마에는 보편적 `isShort` 필드가 없었다. `#shorts`나 길이만으로 정답을 만들지 않는다. Shorts 여부는 `confirmed/heuristic/unknown`과 확인 근거로 나눈다. [Search list](https://developers.google.com/youtube/v3/docs/search/list), [Videos resource](https://developers.google.com/youtube/v3/docs/videos)
- 공식 도움말은 일반 채널의 2024-10-15 이후 정사각형/세로, 최대 3분 영상을 Shorts로 설명한다. Official Artist Channel에는 2025-12-08 기준이 별도로 적혀 있다. 업로드 날짜·채널 유형·비율을 보지 않는 단순 길이 분류는 불충분하다. [Three-minute Shorts](https://support.google.com/youtube/answer/15424877?hl=en)
- Shorts 공개 조회는 2025-03-31부터 재생/재재생 시작 횟수로 바뀌었다. 현재 영상 리소스 문서는 **2026-08-24부터 모든 형식**의 공개 조회를 첫 재생 기준으로 바꾼다고 명시한다. 수정 이력의 공지 항목은 2026-08-27이다. 이 두 날짜를 동일한 시행일로 섞지 않는다. 과거/현재·형식별 지표 정의 버전을 기록한다. [Revision History](https://developers.google.com/youtube/v3/revision_history), [Videos resource](https://developers.google.com/youtube/v3/docs/videos)
- `mostPopular`은 2025-07-21부터 Trending Music·Movies·Gaming 차트의 영상으로 변경되었다. 이 결과를 한국의 모든 유행, 전체 Shorts, 밈 순위로 설명하지 않는다. [Revision History](https://developers.google.com/youtube/v3/revision_history)

인기·반응 수는 밈의 정답이 아니다. 동일 복제의 대량 조회와 독립 변형·사용의 확산을 구분해야 한다. 노출 분모가 없으므로 조회당 문구 빈도를 실제 노출 기반 사용률이라고 부르지 않는다. 통계에서 파생한 사용자 점수나 플랫폼 통합 인기도를 자동 도입하지 않는다.

## 쿼터와 현금 예산

현재 공식 기본 할당은 Search Queries 100회/일, Video Uploads 100회/일, 기타 엔드포인트 합산 10,000 units/일이다. 일일 리셋은 Pacific Time 자정이며 한국 자정이 아니다. 페이지를 더 읽으면 매 페이지 과금 단위가 추가되고 잘못된 요청도 비용이 발생한다. 실제 프로젝트 할당은 Console 확인 전에는 미검증이다. [Quota Calculator](https://developers.google.com/youtube/v3/determine_quota_cost)

아래는 **실행 전 예산식 예시**이며 수집량·실험 결과가 아니다.

| 예시 호출 구성 | 계획한 호출 수 | 버킷별 예산 |
|---|---:|---:|
| 밈 이름 없는 검색 | 20페이지 | Search Queries 20/100 |
| 채널 ID/업로드 목록 확인 | 채널별 1회 × 12 | 기타 12 |
| uploads 목록 | 채널별 최대 2페이지 × 12 | 기타 최대 24 |
| 영상 상세 조회 | 최대 12회, 소규모 ID 묶음 | 기타 최대 12 |
| 최상위 댓글 | 최대 60영상 × 2페이지 | 기타 최대 120 |
| 필요한 답글 | 최대 30페이지 | 기타 최대 30 |
| 소계 | search 20 + 기타 198회 | **search 20 units + 기타 198 units** |

댓글 최대 12,000개의 페이지 용량은 확보 예상치가 아니다. 빈 응답·비활성·중복·기간 밖 자료 때문에 실제 유효 표본은 더 적으며 재시도·차트·재조회는 별도다. 예산은 버킷별로 따로 제한한다. 무계획한 재시도나 quota 우회용 복수 프로젝트를 사용하지 않는다.

문서의 `unit`은 USD가 아니다. 확인한 공식 quota/시작 문서는 호출의 금전 단가표나 자동 초과 구매 단가를 제시하지 않는다. 따라서 API 호출료를 unit에서 달러로 변환하지 않고, 현금 예산에서는 **미확인 단가/추가 쿼터 심사**로 분리한다. 이번 조사에서 유료 서비스 구매·호출 지출은 없었다. 향후 비용은 LLM 입출력 토큰, 임베딩/ASR(OCR) 사용 여부, 사람 검수 시간, 저장·서버 비용을 각각 별도 견적한다. 공개 API만으로 원본 영상 다운로드나 ASR 입력 확보가 보장되는 것은 아니다. [Quota and Compliance Audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits)

첫 접근 확인과 작은 메타데이터 파일럿은 로컬 PC에서 준비할 수 있어 서버 신청을 선행할 이유는 현재 없다. 일정한 간격의 관찰이 장기간 필요하거나 로컬 장치가 자주 꺼진다면 예약 실행 호스트의 필요성과 비용을 그때 제시한다. 이는 현재 설계 판단이며 서버 성능 실측 결과가 아니다.

## 수집·분석·보존 허용 범위

정책 본문은 API 데이터의 채널 간 집계, 새 데이터/파생 지표 생성, 비문서 API, 영상·음성의 다운로드·저장을 제한한다. 일반 공용 비인증 API 자료는 제한된 양을 최대 30일 임시 보관한 뒤 갱신/삭제해야 하며, 공용 통계의 과거 사본을 무기한 축적할 수 있다고 해석하지 않는다. 삭제 요구도 반영해야 한다. [Developer Policies III.D.7, III.E.1–4](https://developers.google.com/youtube/terms/developer-policies)

2026-06-01 추가 정책은 **심사를 거쳐 해당 사용 사례와 추가 조건이 수락된 개발자**에 대해 콘텐츠 태깅·댓글 NLP 분석 등 특정 파생 분석을 허용한다. 승인된 통계와 파생 지표는 최대 36개월 보관할 수 있지만 제목·설명·댓글 본문은 여전히 30일 갱신/삭제 대상이다. 개인 개발자 API 키만으로 이 예외가 적용된다고 가정하지 않는다. [Additional policies for derived metrics and data storage](https://developers.google.com/youtube/terms/derived-metrics-policy)

이에 따라 현재 분류는 **문서·코드·합성 입력 준비 가능 / 실제 데이터에 적용할 분석 및 보존 범위는 미확정**이다. 공개 제목·댓글의 계열화·지식 구조화가 이 예외에 어떻게 포함되는지 확인할 수 있는 구체적 사용 설명을 준비한다. 공식 가이드도 불확실한 서비스에는 사용 설명을 포함한 API Compliance Audit을 안내한다. 이 문서는 법률 자문이나 YouTube의 연구 승인으로 제시하지 않는다. [Developer Policies Guide](https://developers.google.com/youtube/terms/developer-policies-guide)

시간 T 검증과 갱신/삭제는 별개 문제다. 뒤에 갱신한 텍스트·통계를 과거 T의 스냅샷으로 바꾸어 넣으면 시간 누출이다. 연구 로그에는 요청/코드/설정 지문·관찰 시각·상태·삭제/갱신 이력을 남기고, 제한 대상 원문이나 삭제된 내용이 로그·해시 캐시·백업에 무기한 남는다고 가정하지 않는다. 장기 재현성에 필요한 보관 조건이 확보되지 않으면 기간 내 평가와 사후 재실행의 제한을 명시한다.

## Researcher Program의 현실성

공식 기준은 인가된 학위 수여 고등교육기관에 소속된 학생·연구 직원·교원이며 기관 이메일로 신청한다. 대상 국가에는 Korea가 포함된다. 공개 글로벌 영상 메타데이터에 확장된 Data API 접근을 제공하지만, 특별한 모든 자막·원본 영상 접근을 약속하지 않는다. **현재 전달받은 개인 개발 조건만으로 자격을 충족했다고 볼 수 없다.** [How it works](https://research.youtube/how-it-works/)

연구 목표와 결과 공개 의도 등 추가 조건이 있으며 선택된 연구자는 프로그램·일반 YouTube·API 약관을 따른다. 개인 개발 경로의 접근 결과를 연구 프로그램 승인을 받은 것처럼 표시하지 않는다. [Program policies](https://research.youtube/policies/)

## 문서 간 차이와 미확인 사항

| 항목 | 확인한 차이/미확인 | 처리 |
|---|---|---|
| 과거 search 비용 | 2026-06-01에 granular quota 전환. 현재 search 문서는 1 unit 별도 버킷 | 과거 100 units 공용 버킷 계산 폐기. 현재 프로젝트 Console 수치는 실제 실행 전 확인 |
| 문서 요약의 낡은 수치 | Quota Calculator 자동 Page Summary에는 upload 1,600, Audit Summary에는 단순 10,000 문구가 남아 있음 | 본문·방법별 표·개정 이력을 우선하고 본문 읽기 범위 기록 |
| `mostPopular` 범위 | 방법 문서의 일반적 '지역/분류별 인기' 설명만으로는 2025년 차트 변경을 알기 어려움 | 수정 이력의 Music/Movies/Gaming 제한을 함께 인용 |
| 공개 조회 변경 날짜 | `videos/channels` 본문 2026-08-24 시행, 수정 이력 2026-08-27 항목 | 시행일과 공지 항목일 분리; 과거 값 소급 변경 여부 미확인 |
| 파생 분석 | 가이드는 2026-05-04 개정, 추가 정책은 2026-06-01, 일반 정책은 2026-06-24. 가이드의 제한·단순 산술 예시는 추가 승인 조건보다 오래됨 | 현재 일반 정책과 승인 조건을 함께 읽음. 본 연구 자동 태깅·댓글 분석·장기 보관 허용을 확약하지 않으며 해당 사용 사례로 확인 |
| 공용 자막 | transcript UI 도움말과 captions API 편집 권한 조건은 서로 다른 경로 | UI 존재만으로 대량 자동 다운로드 성공·허용 주장 금지 |
| API 성공 및 페이지 완전성 | 실제 키/응답/Console/개별 영상은 검사하지 않음 | 접근 성공·확보 데이터 수·정확도는 모두 미측정 |
| Shorts 판별·멀티모달 | 길이 필터 ≠ Shorts; 원본·공용 대량 자막 부재 | 텍스트 관찰 범위와 미확인 장면을 분리 |

근거 레지스트리: [youtube_access_sources_v01.json](../data/research/youtube_access_sources_v01.json). 각 URL은 공식 원문 접근 범위, 지지하는 주장, 불확실성을 기록한다. 이 검토를 다시 수행하기 전에는 검토일 이후 개정 유무와 프로젝트 상태 변화부터 확인한다.
