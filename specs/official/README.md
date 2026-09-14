# 공식 명세 (KIPRIS Plus 포털 export)

카탈로그의 **권위 있는 파라미터 출처**다. 참고 저장소(nuri428/kipris_skill)의 markdown은
한국어 설명을 영어로 옮겨 파라미터명을 지어낸 곳이 있다 — `getWordSearch`의
`articleName`/`searchYearRange`가 그렇다. 실제 이름은 `searchString`/`searchRecentYear`이고
실호출로 확인했다.

## 어디서 오는가

[KIPRIS Plus 포털](https://plus.kipris.or.kr) 서비스 상세 페이지의 **입출력값 정보** 다운로드
(JSON). 행 배열 형태이고 UTF-8 **BOM**이 붙어 있다 — 파싱할 때 선행 `﻿`를 벗겨야 한다.

- `raw/` — 내려받은 원본. **커밋하지 않는다** (`.gitignore`). 포털 자료를 재배포하지 않고,
  파일 하나가 300KB를 넘는다.
- `*.json` — `tools/extract-official-spec.ts`가 만든 정규화본. **커밋한다.** 원본이 없는
  환경에서도 `mise run catalog`가 공식 파라미터를 쓸 수 있어야 한다.

## export를 추가할 때

```bash
# 1. 포털에서 받은 JSON을 specs/official/raw/ 에 둔다
mise run official-spec   # raw/*.json → specs/official/<service-id>.json
mise run catalog         # 카탈로그 재생성 (override·불일치 보고는 stderr)
```

서비스명 → 카탈로그 service id 매핑은 `tools/extract-official-spec.ts`의
`SERVICE_ID_BY_NAME` 한 곳에 있다. 표에 없는 서비스명이 나오면 id를 추측하지 않고 그 이름을
찍으며 실패한다. 매핑을 먼저 추가한다.
