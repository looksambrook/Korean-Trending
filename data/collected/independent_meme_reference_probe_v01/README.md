# 외부 인간 주석: MemeInterpret 접근 확인

선정 자료는 [저자 공식 MemeInterpret 저장소](https://github.com/npnkhoi/MemeInterpret)다. 사전 등록 `703814ca7d6b46dd8b000d50aae54517` 후 접근했으며, 고정 commit `5d12d11321e5190cf142c68a8152c5b1c07688c3`의 README와 실제 `test_data.json`을 받았다. 원응답·수신시각·해시는 `raw/`와 `manifest.json`에 보존했다.

- `raw/test_data.upstream.json`: 실제 공개 시험 주석 1,000행. **평가 참고 전용이며 CR/CF 적응 풀 편입 금지**.
- `protected_reference_sample.json`: 원본 순서의 첫 3행, 스키마 확인용. 이미 노출된 probe 표본이며 독립 잠금 시험으로 주장하지 않는다.
- 실제 8필드: `img_path`(str), `text`(str), `label`(int), `auto_image_caption`(str), `image_caption`(str), `surface_message`(str), `background_knowledge_list`(list), `meme_caption_list`(list).

저장소는 주석의 CC-BY 4.0을 명시한다. 라이선스 선언의 실제 원문은 `raw/README.upstream.md`에 있다. 코드의 별도 이용 허락은 이 점검에서 확인하지 않았고 외부 코드는 가져오지 않았다. 원이미지는 Facebook Hateful Memes의 별도 조건을 따르며 이번에는 받지 않았다. 제3자 미러의 라이선스로 원자료의 허락을 대신하지 않는다. 먼저 확인한 MemeCap 원저장소에는 이번 확인 범위에서 명시 데이터 라이선스를 찾지 못해 선정하지 않았다.

[저자 논문](https://aclanthology.org/2025.findings-emnlp.871/) §3.1–3.3, 윤리·이용 조건 문단, Appendix A의 본문을 읽었다. 논문은 사람의 수집→수정→판정 절차를 설명한다. 저장소 기준 `image_caption`, `surface_message`, `background_knowledge_list`, `meme_caption_list`는 사람 주석이고, **`auto_image_caption`은 기계 산출**이다. 원저자의 품질 보고를 이 연구에서 재검증한 것은 아니다. PDF 원문은 웹 도구로 읽었으며 로컬 PDF 복사는 남은 크기 제한에 걸려 보존하지 않았다.

이 자료는 영어권 혐오 밈 벤치마크 기반으로, 현재 한국어 콘텐츠 제작 과제와 영역이 다르다. 독립 외부 해석·캡션 참고로 사용할 수 있지만 새 콘텐츠의 창작 품질, 현재성, 사용자 적합성 평가를 대신하지 못한다. 새 사람 평가를 수행한 것도 아니다. 의미 정답이나 정답에서 파생된 설명을 같은 사례의 모델 입력·RAG·FT에 넣은 뒤 독립 평가라고 주장하면 안 된다. 원이미지가 없으므로 지금 native 이미지 이해 평가도 실행하지 않았다.

주석별 작성 시각과 모델의 사전학습 중복은 미확인이다. 지금 받은 버전은 지금부터 이용 가능한 자료로 기록하며 과거 시점으로 소급하지 않는다. 원자료 출처와 저자 인용, CC-BY 4.0 링크와 변경 사항을 보존한다. 모델 변경·학습·새 평가자 모집·유료 호출은 하지 않았다.

성공적으로 보존한 HTTP 원응답은 합계 925,573 bytes다. 별도로 PDF 제한 확인 과정에서 남은 제한량보다 1 byte까지 읽고 중단한 실패가 있어, 이를 전체 네트워크 수신량이라고 부르지 않는다. 실패와 처리 범위는 `probe_attempts.json`에 기록한다.
