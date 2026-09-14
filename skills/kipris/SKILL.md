---
name: kipris
description: 한국특허청 KIPRIS Plus Open API로 특허·실용신안·디자인·상표·심판·등록사항·해외특허를 검색합니다 (49개 서비스, 540개 오퍼레이션). 사용자가 특허나 상표를 찾아달라고 하거나, 출원번호·출원인·등록권자로 조회하거나, 선행기술·경쟁사 IP 동향을 조사하거나, 해외특허를 국가별로 찾거나, KIPRIS·특허검색·상표검색·patent search·prior art를 언급하면 코딩 작업 중이 아니더라도 반드시 이 스킬을 사용하세요. 한국어로 묻든 영어로 묻든 해당합니다.
---

# KIPRIS 한국 지식재산 검색

KIPRIS Plus Open API를 호출해 한국·해외 지식재산 정보를 검색한다. 이 스킬은 개발 도구가 아니라 검색 도구다.

모든 호출은 번들된 `scripts/kipris.py`를 거친다. 직접 `curl`을 조합하지 않는다. 스크립트가 인증키 탐색, URL 인코딩, XML→JSON 변환, KIPRIS 에러코드 판별, 월 호출량 집계를 한곳에서 처리하므로, 매번 그 로직을 다시 만들면 틀리기 쉽고 호출 할당량만 낭비된다.

아래 예시의 `$SKILL`은 이 파일이 있는 디렉터리다.

## 1. 시작 전 — 키와 할당량 확인

```bash
python3 "$SKILL/scripts/kipris.py" doctor
```

키를 찾은 경로, 이번 달 호출 횟수, 카탈로그 적재 상태를 보여준다. 키가 없다고 나오면 사용자에게 안내한다:

1. [KIPRIS Plus](https://plus.kipris.or.kr) 가입 → `accessKey` 발급 (OpenAPI 게이트웨이)
2. [공공데이터포털](https://www.data.go.kr) 활용신청 → `ServiceKey` 발급 (KIPO 게이트웨이)
3. 아래 중 하나로 저장 — 스크립트가 이 순서로 찾는다

```bash
export KIPRIS_API_KEY='발급받은키'                      # 셸이 있는 환경
printf '%s' '발급받은키' > ~/.config/kipris/api_key      # Claude Desktop 등 셸 환경변수가 없는 곳
```

두 게이트웨이의 키가 다르면 `KIPRIS_ACCESS_KEY`(OpenAPI)와 `KIPRIS_SERVICE_KEY`(KIPO)를 따로 설정한다. 같은 키를 공유하는 계정도 있으니 단정하지 말고 사용자에게 확인한다.

**키를 대화에 출력하지 않는다.** `echo $KIPRIS_API_KEY`처럼 키를 노출하는 명령 대신 `doctor`를 쓴다.

## 2. 호출량 규율 — 먼저 읽을 것

무료 등급은 **월 1,000회**이고 매월 1일 초기화된다. 초당 50회를 넘기면 IP가 차단된다.

이 제약은 검색 전략을 바꾼다:

- 결과 건수는 사용자가 요청한 만큼만 가져온다. 확인도 없이 100건을 긁지 않는다.
- **결과마다 추가 호출이 필요한 작업은 실행 전에 총 호출 수를 알리고 동의를 받는다.** 예를 들어 등록권자 검색(`rightHolerSearchInfo`)은 응답에 등록권자 필드가 없어서, 권리자명을 보여주려면 출원번호마다 등록사항 서비스를 한 번씩 더 호출해야 한다. 5건이면 1+5=6회다. 사용자가 "몇 건만 보자"고 했는데 30회를 써버리면 그 달 예산의 3%가 사라진다.
- 안내 예시: "등록권자명을 표시하려면 결과 건수만큼 추가 호출이 필요합니다 (5건 요청 시 총 6회). 진행할까요?"
- 검색어를 바꿔가며 탐색할 때는 매 시도가 1회임을 인지하고, 한 번에 맞출 수 있도록 조건을 사용자와 먼저 정리한다.

`call` 결과의 `_meta.calls_this_month`로 누적 호출량을 계속 확인할 수 있다.

## 3. 자주 쓰는 오퍼레이션

대부분의 요청은 아래로 끝난다. 서비스 id는 `patent_utility`(국내 특허·실용)와 `foreign_patent`(해외특허)다.

| 사용자 요청             | 오퍼레이션                        | 핵심 파라미터                                                   |
| ----------------------- | --------------------------------- | --------------------------------------------------------------- |
| 키워드로 국내 특허 검색 | `freeSearchInfo`                  | `word`, `patent=true`, `utility=true`, `docsStart`, `docsCount` |
| 출원번호로 조회         | `applicationNumberSearchInfo`     | `applicationNumber`                                             |
| 출원인으로 검색         | `applicantNameSearchInfo`         | `applicant`                                                     |
| 등록권자로 검색         | `rightHolerSearchInfo`            | `rightHoler` (철자 주의 — API 원본이 이렇다)                    |
| 상세 서지정보           | `getBibliographyDetailInfoSearch` | `applicationNumber`                                             |
| 요약 서지정보           | `getBibliographySumryInfoSearch`  | `applicationNumber`                                             |
| 항목을 조합한 정밀검색  | `getAdvancedSearch`               | `word`, `inventionTitle`, `applicant`, `numOfRows`, `pageNo`    |
| 해외특허 키워드 검색    | `freeSearch`                      | `free`, `collectionValues`, `currentPage`                       |
| 해외특허 출원번호       | `applicationNumberSearch`         | `applicationNumber`, `collectionValues`                         |
| 해외특허 출원인         | `applicantSearch`                 | `applicant`, `collectionValues`                                 |

```bash
# 키워드 검색 10건
python3 "$SKILL/scripts/kipris.py" call patent_utility freeSearchInfo \
  -p word=인공지능 -p patent=true -p utility=true -p docsCount=10 -p docsStart=1

# 출원번호 상세 서지
python3 "$SKILL/scripts/kipris.py" call patent_utility getBibliographyDetailInfoSearch \
  -p applicationNumber=1020200123456

# 미국 특허
python3 "$SKILL/scripts/kipris.py" call foreign_patent freeSearch \
  -p free="artificial intelligence" -p collectionValues=US -p currentPage=1
```

해외특허 `collectionValues`는 한 번에 하나만 지정한다: `US` 미국, `EP` 유럽, `WO` PCT, `JP` 일본, `PJ` 일본영문초록, `CP` 중국, `CN` 중국영문초록, `TW` 대만영문초록, `RU` 러시아, `CO` 콜롬비아, `SE` 스웨덴, `ES` 스페인, `IL` 이스라엘.

페이징 파라미터는 게이트웨이마다 다르다 — OpenAPI 계열은 `docsStart`/`docsCount`, KIPO 계열은 `pageNo`/`numOfRows`.

## 4. 표에 없는 서비스 — 카탈로그에서 찾는다

디자인, 상표, 심판, 등록사항, 특허 패밀리, 인용문헌 등 나머지 서비스는 카탈로그에 오퍼레이션·파라미터·응답필드가 들어 있다. 서비스 명세 원문을 통째로 읽지 말고 스크립트에 물어본다 — 큰 서비스는 명세가 10만 자를 넘어서 컨텍스트를 다 써버린다.

```bash
python3 "$SKILL/scripts/kipris.py" services --grep 상표          # 서비스 찾기
python3 "$SKILL/scripts/kipris.py" describe trademark            # 오퍼레이션 목록
python3 "$SKILL/scripts/kipris.py" describe trademark --op trademarkInfoSearch   # IN/OUT 전체
python3 "$SKILL/scripts/kipris.py" call trademark trademarkInfoSearch -p ...
```

`path_confidence`가 `verified`가 아닌 서비스는 ServicePath가 실호출로 확인되지 않은 상태다. 호출 전에 그 사실을 사용자에게 알리고, 실패하면 추측으로 재시도하지 말고 상황을 설명한다. `service_path`가 비어 있으면 URL을 만들 수 없으므로 스크립트가 먼저 거부한다.

## 5. 결과 제시

기본 출력은 `_meta`와 `items`로 이루어진 JSON이다. 사용자에게는 **원본 JSON이 아니라 읽을 수 있는 표**로 정리해 보여준다.

국내 특허 주요 필드: `InventionName`/`inventionTitle`(발명의명칭), `ApplicationNumber`/`applicationNumber`(출원번호), `ApplicationDate`(출원일자), `Applicant`/`applicantName`(출원인), `RegistrationNumber`(등록번호), `RegistrationStatus`/`registerStatus`(등록상태), `Abstract`/`astrtCont`(초록).

해외특허 주요 필드: `inventionName`, `applicationNo`, `applicationDate`, `applicant`, `registerNo`, `colString`(국가코드), `ipc`.

같은 의미의 필드라도 오퍼레이션마다 대소문자와 이름이 다르다. 응답에 실제로 있는 키를 쓰고, 없는 값을 추측해 채우지 않는다.

제시할 때 지킬 것:

- 전체 검색 건수(`_meta.total_count`)와 지금 보여주는 건수를 함께 밝힌다. "총 106,285건 중 상위 5건"처럼.
- 출원번호는 원본 그대로 둔다. `1020240124164`를 임의로 `10-2024-0124164`로 바꾸면 사용자가 그대로 복사해 재검색할 때 실패한다.
- **검색 결과를 법적 판단으로 확장하지 않는다.** 등록 가능성, 침해 여부, 권리 범위는 이 API가 답할 수 있는 것이 아니다. 사용자가 물으면 검색된 사실만 제시하고 변리사 상담이 필요한 영역임을 밝힌다.

## 6. 실패했을 때

스크립트는 실패를 `error:`로 표준에러에 적고 종료코드 1을 낸다. 흔한 원인:

- **`KIPRIS error 101`** — 키는 유효하지만 해당 API를 신청하지 않았다. KIPRIS Plus에서 그 서비스를 활용신청해야 한다고 안내한다.
- **키 없음** — 2절의 발급·저장 절차를 안내한다.
- **네트워크 도달 불가** — 샌드박스가 외부 접속을 막고 있을 수 있다. 이때는 다른 파라미터로 재시도해도 소용없으니 환경 문제임을 밝힌다.
- **결과 0건** — 실패가 아니다. 검색어를 좁히거나 넓히는 대안을 제안하되, 사용자 동의 없이 연달아 호출하지 않는다(호출량).
- **게이트웨이 오판** — `_meta.gateway_source`가 `heuristic`이면 게이트웨이를 추정한 것이다. 응답이 이상하면 `--gateway openapi|kipo`로 반대쪽을 한 번 시도해볼 수 있다.

## 참고 파일

- `references/catalog/index.json` — 49개 서비스 요약 (스크립트가 읽는다)
- `references/catalog/services/*.json` — 서비스별 오퍼레이션·파라미터·응답필드
- `references/gateways.md` — 두 게이트웨이의 URL·인증 차이와 공통 파라미터
