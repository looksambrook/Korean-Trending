# YouTube 밈 발견과 이해를 연결하는 방법: 선행연구 검토 v01

검토일: 2026-09-15. 범위: 핵심 6편과 보조 1편의 저자 공개본 또는 공식 게재 PDF에서 아래에 적은 핵심 절을 확인했다. 전문 전체 정독, 코드 재현, 공개 데이터 다운로드·검증을 완료했다는 뜻은 아니다. 아래는 공개 문헌 검토이며 새로운 YouTube 수집·모델 실험 결과가 아니다. 현재 연구의 D0/D1 × U0/U1 정의는 `RESEARCH_HANDOFF.md`를 따른다.

**판단:** YouTube에서 반복되는 표현·영상 구간과 시간 변화를 찾는 방법적 근거는 있다. 다만 `인기 영상`, `반복 복제`, `문화적 밈`, `모델의 맥락 이해`는 서로 다른 대상이다. 한국어 관찰 자료에서 후보 발견부터 의미·의도 이해까지 가능한지는 별도 실험으로 확인해야 한다. 이 문헌 집합만으로 독창성, 최신 최고 성능, 세계 최초 발견 또는 게재 가능성을 확정하지 않는다.

## 1. 핵심 문헌과 적용 범위

| ID | 문헌·확인 범위 | 실제 입력 → 출력 | 이름 없는 발견과 연구상 한계 |
|---|---|---|---|
| YL01 | Kleinberg (2002), *Bursty and Hierarchical Structure in Streams*. 저자본 §2·§4의 모형과 문서열 적용 확인 | 시간별 사건·단어 출현 → 급증 구간·강도 계층 | 밈 이름 없이 특징별 급증 계산 가능. 급증 자체는 밈 판정이나 계열 연결이 아님 |
| YL02 | Leskovec, Backstrom, Kleinberg (2009), *Meme-tracking and the Dynamics of the News Cycle*. 저자본 §1·§2 확인 | 뉴스·블로그의 인용문 → 작은 변형들을 묶은 문구 그래프·시간 경향 | 코퍼스 안 문구를 추출하므로 알려진 밈 목록과 다름. 뉴스 인용 전파를 한국어 밈 의미로 바로 해석할 수 없음 |
| YL03 | Xie et al. (2011), *Visual Memes in Social Media: Tracking Real-world News in YouTube Videos*. 저자본 §4·§5·§7.1·결론 확인 | 주제 검색으로 모은 영상의 대표 프레임 → 재사용 구간 군집·영상/작성자 관계 | 밈 프레임 목록은 미리 주지 않지만 관찰 코퍼스는 주제어로 제한. 동작·음성의 독립 재연이나 의도 이해와 다름 |
| YL04 | Mei et al. (2024), *SLANG: New Concept Comprehension of Large Language Models*. 공식 PDF §2·§3 확인 | 이미 선택된 신조어와 사용 문맥 → 의미 설명 | 개념 사전에서 대상을 고르는 이해 평가. 자연 관찰열에서 놓친 밈을 찾는 평가가 아님 |
| YL05 | Zhao et al. (2025), *MemeReaCon: Probing Contextual Meme Understanding in Large Vision-Language Models*. 공식 PDF §3·§4.1 확인 | Reddit 밈 이미지·게시물·댓글 → 문맥 관계·설명·의도·댓글 해석 | 선택된 밈의 이해 평가. 비밈과 발견 누락이 포함된 전체 발견 평가를 대체하지 못함 |
| YL06 | Goncalves, Ng (2026), *Global YouTube Trending Dataset (2022–2025): Three Years of Platform-Curated, Cross-National Trends in Digital Culture*. 공식 PDF §4·데이터 표 확인 | 과거 국가별 인기 목록 스냅샷 → 순위·메타데이터·반응 시계열 | 특정 밈명 검색 자료는 아니지만 플랫폼이 이미 선정한 영상. 현재 YouTube 관찰·전체 한국어 밈 정답이 아님 |

## 2. 방법별로 가져올 부분과 통제

### YL01 — 빈도 증가의 기준선

상태별 발생률과 상태 전환 비용으로 짧은 잡음과 지속적 급증을 구별한다. 사건 간격 모형뿐 아니라 문서 묶음 안의 특징 출현을 다루는 확장이 있다. [저자 PDF](https://www.cs.cornell.edu/home/kleinber/bhs.pdf)

**본 연구 제안:** D0는 최신 계획서대로 단순 문구 빈도·증가 기반으로 유지한다. 먼저 관찰 문서 수 대비 출현 비율·이전 창 대비 증가를 구현하고, Kleinberg 모형은 추가 비교로 둔다. 정확한 모형을 구현하지 않았다면 “Kleinberg 구현”이라고 쓰지 않는다. 미래 관찰 구간으로 기저 발생률·상태를 재추정한 결과를 과거 T의 발견으로 사용하지 않는다. 광고·뉴스 속보도 급증할 수 있으므로 비밈 오탐 검수가 필요하다.

### YL02 — 반복 문구를 변형 계열로 묶기

인용문 사이의 근사 포함·연속 단어 중첩을 그래프로 만들고 분할한다. 원 논문은 최소 문구 길이·출현 수와 단일 도메인 집중도 조건을 사용한다. [저자 PDF §2](https://cs.stanford.edu/~jure/pubs/quotes-kdd09.pdf)

**본 연구 제안:** D1에 문자/형태소 중첩 후보와 주변 문맥 비교를 결합한다. 영어 뉴스의 길이·빈도 임계값을 한국어 댓글에 복사하지 않는다. 채널/작성자 식별자를 독립 사용의 대리 지표로 삼되 서로 다른 계정이 독립 창작을 보장하지는 않는다. 동일 문장 복사와 문맥을 바꾼 변형을 별도 관계로 기록한다. 그래프가 연쇄 연결로 다른 계열을 합치는 오류와 같은 계열을 쪼개는 오류를 함께 평가한다.

### YL03 — 반복 영상 구간 확인

색상 correlogram, 근사 최근접 검색, 프레임별 임계값과 연결 집합 계산으로 시각적 복제를 탐지한다. 코퍼스는 주제 관련 질의로 모은 뉴스 영상이며 당시 API 조건을 이용했다. 의미 주석과 뉴스 외 장르는 후속 과제로 남긴다. [저자 PDF](https://users.cecs.anu.edu.au/~xlx/papers/acmmm11-meme.pdf), [저자 프로젝트](https://users.cecs.anu.edu.au/~xlx/proj/visualmemes.html)

**본 연구 제안:** 허용된 영상 접근을 확보한 뒤 재게시/같은 구간 재사용 식별에 활용한다. 영상 접근 없이 제목·설명·댓글만 분석하면 “YouTube 텍스트 관찰”로 한정한다. 같은 화면이 있어도 의미가 다를 수 있으므로 프레임 일치와 밈 계열 일치를 분리한다. 독립 연기·동작과 반복 음원 검출은 이 논문을 근거로 구현 완료했다고 주장하지 않는다. 음성 motif 방법은 이번 검토에서 직접 검증한 핵심 문헌이 없다.

### YL04 — 문맥으로 의미를 추론하는지 진단

UrbanDictionary의 개념·설명·용례를 시간 및 품질 조건으로 고른다. FOCUS는 원문 질의, 표적 표현 마스킹, 개체 치환, 종합을 수행한다. 반사실 자료에는 GPT-4로 만든 문맥이 포함된다. [공식 PDF §2–3](https://aclanthology.org/2024.emnlp-main.698.pdf)

**본 연구 제안:** U0의 실제 용례·문맥과 U1의 동일 용례·자동 구조화를 비교하고, 별도 진단에서 표적 표현을 가렸을 때와 문맥이 달라졌을 때 설명이 어떻게 변하는지 본다. 치환 문장은 합성 자료로 격리한다. 사전 등재일이나 모델의 실패만으로 사전학습 미노출을 확증하지 않는다. 문맥 제공의 효과를 가중치 학습으로 쓰지 않는다. FOCUS라는 이름의 단순 차용 또는 인과 효과 입증 주장은 하지 않는다.

### YL05 — 같은 밈도 어디에 쓰였는지 평가

영어 Reddit 5개 커뮤니티의 이미지·게시물·상위 댓글을 함께 구성한다. 문맥과 밈의 관계, 댓글의 태도/문자적 해석 여부, 게시물 연결 설명과 의도를 구분한다. 비밈 이미지는 구축 과정에서 제외된다. [공식 PDF §3–4.1](https://aclanthology.org/2025.emnlp-main.176.pdf)

**본 연구 제안:** YouTube에서는 해당 댓글의 부모 영상·부모 댓글·시각을 남겨 문자적 의미와 발화 의도를 분리해 채점한다. 상위 댓글은 공동체 전체의 해석 정답으로 취급하지 않는다. 같은 계열의 다른 사용 문맥, 비밈, 근거 부족 사례를 포함한다. 의미 설명 문장 유사도는 보조로 사용하고 사람의 근거·의도 판단과 구분한다. 이 자료의 사람 평가·모델 점수를 한국어 YouTube 기대 성능으로 옮기지 않는다.

### YL06 — 과거 인기 목록을 활용하는 보조 경로

2022-07-01부터 2025-06-30까지 104개 국가의 `chart=mostPopular` 목록을 하루 4회 수집한 자료이다. 스냅샷 시각·국가·순위·영상/채널 ID·제목·설명·태그·게시일·언어·누적 조회/댓글 수 등이 보고된다. 댓글 **본문**, 영상·음성 파일, 전사는 제공 스키마가 아니다. [공식 PDF §4](https://ojs.aaai.org/index.php/ICWSM/article/download/42784/50344/46885), [공식 게재 정보](https://ojs.aaai.org/index.php/ICWSM/article/view/42784)

**본 연구 제안:** 저장소에서 한국 지역 포함 여부와 실제 파일·라이선스를 별도 확인한 뒤, 과거 제목/설명 반복의 제한적 예비 분석 후보로 삼는다. 국가별 순위에 같은 영상이 반복되어도 독립 변형 수로 세지 않는다. 추가 영상·댓글 접근은 보장하지 않는다. 현행 API 범위로 과거 수집법을 그대로 재현할 수 있다고 가정하지 않는다. 아카이브 시점과 우리가 접근한 시점을 따로 기록하며, 과거 시뮬레이션은 소급 실험으로 명시한다.

## 3. 보조 문헌: 알려진 표현 검색과 발견을 구별하기

YL07: Sweed와 Shahaf의 *Catchphrase: Automatic Detection of Cultural References*는 주어진 원문(seed)과 후보 문장 사이의 문화적 참조 여부를 분류한다. 공식 PDF §4에서 seed–candidate 입력 정의, §5의 자료 구성, §6의 Reddit 사용자 연구 절을 확인했다. 알려진 인용문에서 변형을 찾는 비교군으로 사용할 수 있지만, 밈 이름/원문을 주지 않은 코퍼스 발견으로 계산하지 않는다. snowclone을 다루는 접근이므로 고정 인용·신조어·동작·음성 밈을 모두 빈칸 치환형으로 강제하는 근거가 되지 않는다. [공식 PDF](https://aclanthology.org/2021.acl-short.1.pdf), [공식 게재 정보](https://aclanthology.org/2021.acl-short.1/)

## 4. 현재 파일럿에 적용할 연구자 제안

아래 항목은 위 논문의 실험 결과가 아니라 이 프로젝트에 맞게 제안한 통제이다.

1. **관찰 코퍼스:** 알려진 밈명 검색·밈 소개 채널·현재 인기 목록·고정 채널 표본을 서로 다른 `sample_method`로 기록한다. 채널 표본에도 선정 편향이 있으며 YouTube 전체로 일반화하지 않는다.
2. **D 비교:** D0는 문구 빈도·증가, D1은 변형 구조·독립 사용 대리 지표·문맥을 결합한다. 같은 문서·마감 T·후보 검수 예산으로 비교한다. 결과에 없는 정답 계열도 재현율 분모에 남긴다.
3. **U 비교:** U0와 U1은 같은 D 결과 및 같은 원시 근거를 쓴다. U1의 자동 구조화에 사람 정답 설명을 넣지 않는다. 사람 교정은 별도 조건으로 시간·비용을 남긴다. 외부 검색을 추가하면 자료·시점·예산을 별도 조건으로 통제한다.
4. **시간·계열:** 개발/검증/시험을 시간 순으로 나누고 새 계열·기존 계열 변형·재유행을 분리한다. `posted_at`, `collected_at`, `available_at`, `linked_at`, 지식 버전을 저장한다. 현재 API로 얻은 과거 게시물은 과거에 실제 발견 가능했다는 증거가 아니다.
5. **독립 정답:** 알고리즘 상위 후보뿐 아니라 고정 관찰 자료 전체 또는 명시된 확률표본을 사람이 검토한다. 비밈·놓친 밈·복제·과잉 병합·과잉 분리를 기록한다. 다른 계열/다른 문맥을 시험 질문에 포함한다.
6. **보고:** 후보 정밀도, 기준 집합 계열 재현율, 발견 지연, 연결 정확도, 비밈 거절, 근거·의미·의도 이해, 발견+이해 동시 성공을 구분한다. 후속 플랫폼에서는 플랫폼별 분모와 동일 재게시 중복 제거 규칙을 유지한다.

## 5. 인용 정보와 확인 제한

- **YL01:** Jon Kleinberg. 2002. *Bursty and Hierarchical Structure in Streams*. KDD 2002. 저자본 첫 페이지에서 학회·연도 확인. DOI 후보 `10.1145/775047.775061`은 이번 공식 게재 페이지/DOI 접근이 실패하여 **서지 확정 전 재확인 필요**. 본문의 방법 설명은 위 Cornell 저자 PDF에만 근거한다. 같은 제목의 2003년 저널판과 혼용하지 않는다.
- **YL02:** Jure Leskovec, Lars Backstrom, Jon Kleinberg. 2009. *Meme-tracking and the Dynamics of the News Cycle*. KDD 2009. 저자본에서 학회·연도 확인. DOI 후보 `10.1145/1557019.1557077`은 이번 공식 게재 페이지/DOI 접근이 실패하여 **서지 확정 전 재확인 필요**. 위 Stanford 저자 PDF를 본문 출처로 사용한다.
- **YL03:** Lexing Xie, Apostol Natsev, John R. Kender, Matthew Hill, John R. Smith. 2011. ACM Multimedia, 53–62. [DOI·저자 소속기관의 서지](https://researchportalplus.anu.edu.au/en/publications/visual-memes-in-social-media-tracking-real-world-news-in-youtube-/): `10.1145/2072298.2072307`.
- **YL04:** Lingrui Mei, Shenghua Liu, Yiwei Wang, Baolong Bi, Xueqi Cheng. 2024. EMNLP, 12558–12575. [공식 서지·DOI](https://aclanthology.org/2024.emnlp-main.698/): `10.18653/v1/2024.emnlp-main.698`.
- **YL05:** Zhengyi Zhao, Shubo Zhang, Yuxi Zhang, Yanxi Zhao, Yifan Zhang, Zezhong Wang, Huimin Wang, Yutian Zhao, Bin Liang, Yefeng Zheng, Binyang Li, Kam-Fai Wong, Xian Wu. 2025. EMNLP, 3559–3582. [공식 서지·DOI](https://aclanthology.org/2025.emnlp-main.176/): `10.18653/v1/2025.emnlp-main.176`.
- **YL06:** Alexandre Goncalves, Yee Man Margaret Ng. 2026. ICWSM 20(1), 2817–2827. [공식 서지·DOI](https://ojs.aaai.org/index.php/ICWSM/article/view/42784): `10.1609/icwsm.v20i1.42784`. 논문에 제시된 데이터 식별자 `10.13012/B2IDB-9307654_V1`의 실제 파일은 이 검토에서 다운로드하지 않았다.
- **YL07:** Nir Sweed, Dafna Shahaf. 2021. ACL-IJCNLP Volume 2: Short Papers, 1–7. [공식 서지·DOI](https://aclanthology.org/2021.acl-short.1/): `10.18653/v1/2021.acl-short.1`.

이번 확인 범위에서 직접 재현한 알고리즘·평가 수치는 없다. 기존 논문의 최신 API 접근·라이선스·비용은 보장하지 않는다. 후속 문헌 확인은 `data/research/youtube_literature_sources_v01.json`의 ID와 확인 절을 먼저 조회해 중복 검토를 줄인다.
