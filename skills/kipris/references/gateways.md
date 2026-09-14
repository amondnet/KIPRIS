# KIPRIS 게이트웨이와 공통 파라미터

`SKILL.md`로 해결되지 않을 때 — 게이트웨이를 잘못 골랐거나, 카탈로그에 없는 파라미터를 써야 하거나, 응답 구조가 예상과 다를 때 읽는다.

## 두 개의 게이트웨이

KIPRIS Plus는 같은 데이터를 두 경로로 제공하고, 각각 인증 파라미터 이름이 다르다.

|               | OpenAPI                                                             | KIPO                                                                 |
| ------------- | ------------------------------------------------------------------- | -------------------------------------------------------------------- |
| URL           | `http://plus.kipris.or.kr/openapi/rest/{ServicePath}/{operationId}` | `http://plus.kipris.or.kr/kipo-api/kipi/{ServicePath}/{operationId}` |
| 인증 파라미터 | `accessKey`                                                         | `ServiceKey`                                                         |
| 키 발급처     | KIPRIS Plus 회원가입                                                | 공공데이터포털 활용신청                                              |
| 페이징        | `docsStart` / `docsCount`                                           | `pageNo` / `numOfRows`                                               |

같은 서비스라도 오퍼레이션마다 게이트웨이가 다를 수 있다. 확인된 범위에서 KIPO 쪽 오퍼레이션은 이름이 `get`으로 시작하고(`getAdvancedSearch`, `getBibliographyDetailInfoSearch`), OpenAPI 쪽은 그렇지 않다(`freeSearchInfo`, `applicantNameSearchInfo`). `kipris.py`는 확인된 오퍼레이션 매핑을 먼저 쓰고, 없으면 이 규칙으로 추정한 뒤 `_meta.gateway_source`에 어느 근거였는지 남긴다. 추정이 틀린 것 같으면 `--gateway`로 명시한다.

두 키가 같은 값일 수도, 다를 수도 있다. 계정마다 다르므로 사용자에게 확인한다.

## 응답 구조

XML이 기본이다. 데이터가 담기는 루트 키가 오퍼레이션마다 다르다:

- `response.body.items.item` — KIPO 계열 다수
- `response.body.items.PatentUtilityInfo` — 국내 특허·실용 OpenAPI 검색
- `response.body.items.searchResult` — 해외특허
- `response.body.item` — 서지상세

`kipris.py`는 `items` 또는 `item`을 깊이 우선으로 찾아 `items` 키에 넣는다. 구조를 직접 봐야 하면 `--full`로 파싱된 전체 엔벨로프를, `--raw`로 원본 XML을 얻는다.

상태 필드:

- `resultCode` — `00`이 정상. `101`은 해당 API 미신청.
- `resultMsg` — 오류 설명
- `successYN` — `N`이면 실패
- `totalCount` / `TotalSearchCount` — 전체 검색 건수

## 공통 파라미터

| 파라미터                 | 의미                                                    |
| ------------------------ | ------------------------------------------------------- |
| `patent` / `utility`     | `true`로 특허·실용신안 포함 여부 지정 (국내 검색)       |
| `lastvalue`              | 행정처분 필터 — 공백=전체, `A` 공개, `R` 등록, `J` 거절 |
| `sortSpec` / `sortField` | 정렬 기준 항목                                          |
| `descSort` / `sortState` | 내림차순 여부                                           |
| `collectionValues`       | 해외특허 국가코드. 한 번에 하나만                       |

## 인증키 인코딩

키에 `/`, `+`, `=`가 들어갈 수 있다. URL 인코딩하지 않으면 인증이 조용히 실패한다. `kipris.py`는 모든 파라미터를 `safe=''`로 인코딩하므로 호출자가 따로 처리할 필요가 없다 — 직접 URL을 만들 때만 주의한다.

## 출처

서비스 경로와 오퍼레이션 명세는 [nuri428/kipris_skill](https://github.com/nuri428/kipris_skill) (MIT) 이 정리한 KIPRIS Plus 공개 명세에서 파생했다. `path_confidence`는 그 출처가 밝힌 확인 수준을 그대로 옮긴 것이다:

- `verified` — 실제 API 키로 호출 검증됨
- `portal` — KIPRIS 포털 문서에서 확인
- `github` — 공개 코드에서 추출, 미검증
- `unknown` — 경로 미확인. 호출 불가
