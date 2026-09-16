# 실제 자료: 무야호·Doge 개발 파일럿

이름을 먼저 지정한 `known_seed_retrieval`로 2026-09-15 수집했다. 자동 발견 성과나 대표 표본으로 세지 않는다. 한국어 Wikipedia 설명 2개와 Doge 계열의 실제 공개 이미지 2개다. 각 파일의 수신시각·해시·원주소는 `manifest.json`, 원 HTTP 응답은 `raw/`와 각 `*.receipt.json`에 있다. `collect_sources.py`가 수집 코드다.

| 계열 | 실제 확보 | 확인 범위·이용 근거 |
|---|---|---|
| 무야호 | `text/muyaho_lead_ko.txt` | [한국어 Wikipedia 본문](https://ko.wikipedia.org/wiki/무야호)의 첫 문단 직접 추출. 문서 기여자들의 편집 설명이며 원방송 직접 관찰은 아니다. 문서판 41962291, CC-BY-SA 4.0 |
| Doge | `text/doge_lead_ko.txt` | [한국어 Wikipedia 본문](https://ko.wikipedia.org/wiki/도지_(밈)) 첫 문단 직접 추출. 문서판 41680033, CC-BY-SA 4.0 |
| Doge/Taiki | `images/doge_taiki_original.jpg` | [Roberto Vasarri의 Taiki 사진](https://commons.wikimedia.org/wiki/File:Shiba_inu_taiki.jpg). Commons의 `Own work`, 저자 표시, 저작권자의 public-domain 공개 선언을 직접 확인. 원본 2,175×2,563, 707,853 bytes |
| Doge/Taiki | `images/doge_taiki_macro.png` | [기존에 게시된 Doge 문구 이미지](https://commons.wikimedia.org/wiki/File:Doge_meme.png). 원 사진 출처와 CC-BY-SA 4.0 명시 확인. 원본 509×600, 344,647 bytes |

두 이미지는 직접 표시하여 Taiki 사진과 같은 사진 위의 `wow / many readers / such knowledge` 문구를 확인했다. 이는 AI의 파일 확인이며 독립 사람 평가가 아니다. Taiki는 이 자료의 개체 이름이며 대표 Doge 사진의 Kabosu와 동일하다고 주장하지 않는다. 사진과 문구 파생본은 동일 `source_group_id`를 공유하므로 독립 원본 2개나 별도 평가 표본으로 세지 않는다.

CC 자료를 재배포하거나 개작물을 공유할 때 저자·원문 및 파일 페이지·[라이선스](https://creativecommons.org/licenses/by-sa/4.0/)·변경 사실을 함께 남기고 동일조건변경허락 요건을 유지한다. 직접 읽은 [한국어 라이선스 안내](https://creativecommons.org/licenses/by-sa/4.0/deed.ko)도 `raw/`에 보존했다. 이미지의 출처 페이지가 선언한 이용 근거를 기록한 것이며, 방송·영상·음악이나 링크된 외부 기사까지 허락된 것으로 확대하지 않는다.

이 자료는 지원 후보이며 제작 요청–정답 작품 쌍, 독립 사람 주석, 보류 평가 자료가 아니다. 한국어 설명은 실제 관찰된 편집 문서이고 AI가 새로 쓴 밈 설명이 아니다. 공개 영상·오디오는 이번 묶음에 없다. 제한된 Commons 검색으로 무야호/Doge에 적합한 오디오·영상 자료를 확보하지 못했으며, 검색 실패가 전세계 부재를 뜻하지 않는다. 영상 생성 결과가 추후 만들어져도 실제 밈 원영상 수집으로 기록하지 않는다.

과거 유행 연도와 현재 확보 시점은 다르다. 지금 받은 기사판·추출 텍스트·라이선스 확인은 현재부터 사용 가능하며, 2010년 또는 2013년 당시 이용 가능했던 지식으로 소급하지 않는다.

연구 로그 등록은 `41c48389e93647b39f50244f7480f433`이다. 최초 웹 탐색이 등록보다 앞섰다는 사실은 `registration.json`에 명시했다. 원자료 다운로드는 등록 후 수행했다. 이 하위작업의 등록 실패는 없었으며, 기본 쉘 소켓 실패와 최초 Commons 링크 파싱 실패는 `earlier_attempts.json`에 보존했다. 유료 이용·훈련·독립 사람 평가 비용은 0이다.
