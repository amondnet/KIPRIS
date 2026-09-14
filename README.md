# KIPRIS 한국 지식재산 검색 — Claude Skill

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![KIPRIS Plus](https://img.shields.io/badge/KIPRIS-Plus%20Open%20API-003DA5.svg)](https://plus.kipris.or.kr)
[![skills.sh](https://skills.sh/b/amondnet/KIPRIS)](https://skills.sh/amondnet/KIPRIS)

> Search Korean patents, utility models, designs, trademarks, trials and foreign patents from Claude — via the KIPRIS Plus Open API.

한국특허청(KIPRIS) Plus Open API로 특허·실용신안·디자인·상표·심판·등록사항·해외특허를 검색하는 Claude 스킬입니다. **Claude Code와 Claude Desktop(cowork) 양쪽에서 동작합니다** — 실행 스크립트가 Python 표준 라이브러리만 쓰기 때문에 별도 설치나 빌드가 필요 없습니다.

```
사용자: AI 관련 특허 중 최근 등록된 걸 5건만 보여줘

Claude: KIPRIS 자유검색을 실행합니다...
        총 106,285건 중 상위 5건 (이번 달 API 호출 12/1000회)

        | # | 발명의명칭 | 출원번호 | 출원인 | 등록상태 |
        |---|---|---|---|---|
        | 1 | 의미론적 정보와 공간적 정보를 보강하여 ... | 1020240124164 | 아주대학교산학협력단 | 공개 |
```

## 무엇이 들어 있나

| 경로                                   | 내용                                                                    |
| -------------------------------------- | ----------------------------------------------------------------------- |
| `skills/kipris/SKILL.md`               | 스킬 본체 — 트리거, 호출량 규율, 오퍼레이션 매핑, 결과 제시 규칙        |
| `skills/kipris/scripts/kipris.py`      | KIPRIS 클라이언트. 인증키 탐색·URL 인코딩·XML→JSON·에러코드·호출량 집계 |
| `skills/kipris/references/catalog/`    | 49개 서비스 · 호출 가능한 오퍼레이션 511개의 기계 판독 카탈로그         |
| `skills/kipris/references/gateways.md` | 두 게이트웨이의 차이, 공통 파라미터, 응답 구조                          |
| `.claude-plugin/`                      | 플러그인·마켓플레이스 매니페스트 — 위 두 설치 경로가 읽는다             |

카탈로그 덕분에 Claude가 10만 자짜리 명세 문서를 읽지 않고 `describe trademark --op ...`로 필요한 오퍼레이션만 꺼내 씁니다.

## 설치

### skills CLI — Claude Code · Codex · Cursor

```bash
npx skills add amondnet/KIPRIS
```

설치 위치는 에이전트가 정합니다 (Claude Code는 `.claude/skills/`, Codex·Cursor 등은 `.agents/skills/`). 전역 설치는 `-g`, 갱신은 `npx skills update kipris`입니다.

### Claude Code 플러그인

```
/plugin marketplace add amondnet/KIPRIS
/plugin install kipris@amondnet
```

저장소가 자기 자신을 마켓플레이스로 싣고 있어 별도 등록 없이 붙습니다.

### Claude Desktop / cowork

```bash
bun run package        # → dist/kipris.skill
```

생성된 `dist/kipris.skill`을 Claude Desktop의 스킬 업로드에 올립니다. (`mise run package` 또는 `bash tools/package-skill.sh`도 동일합니다.)

### 직접 연결

```bash
git clone https://github.com/amondnet/KIPRIS.git
ln -s "$(pwd)/KIPRIS/skills/kipris" ~/.claude/skills/kipris
```

프로젝트 단위로 쓰려면 `.claude/skills/kipris`에 두어도 됩니다.

## API 키

KIPRIS Plus는 게이트웨이가 두 개이고 발급처가 다릅니다.

| 게이트웨이 | 인증 파라미터 | 발급처                                            |
| ---------- | ------------- | ------------------------------------------------- |
| OpenAPI    | `accessKey`   | [KIPRIS Plus](https://plus.kipris.or.kr) 회원가입 |
| KIPO       | `ServiceKey`  | [공공데이터포털](https://www.data.go.kr) 활용신청 |

키는 아래 순서로 탐색합니다. 셸 환경변수가 없는 Claude Desktop에서는 파일 경로를 씁니다.

1. `KIPRIS_ACCESS_KEY` / `KIPRIS_SERVICE_KEY` (게이트웨이별)
2. `KIPRIS_API_KEY` (공용)
3. 작업 디렉터리의 `.env.local` 또는 `.env`
4. `~/.config/kipris/api_key` (게이트웨이별로 나누려면 `openapi_key` / `kipo_key`)

```bash
mkdir -p ~/.config/kipris
printf '%s' '발급받은키' > ~/.config/kipris/api_key
chmod 600 ~/.config/kipris/api_key

python3 skills/kipris/scripts/kipris.py doctor   # 키 탐색 결과와 이번 달 호출량 확인
```

**무료 등급은 월 1,000회입니다.** 스킬이 호출량을 집계해 매 응답에 누적치를 싣고, 결과마다 추가 호출이 필요한 작업은 실행 전에 총 호출 수를 알리고 동의를 구합니다.

## 직접 써보기

```bash
S=skills/kipris/scripts/kipris.py

python3 $S services --grep 상표                    # 서비스 찾기
python3 $S describe trademark                      # 오퍼레이션 목록
python3 $S describe trademark --op trademarkInfoSearch   # 파라미터 전체

python3 $S call patent_utility freeSearchInfo \
  -p word=인공지능 -p patent=true -p docsCount=5 -p docsStart=1
```

## 개발

```bash
mise trust && mise install
bun install

mise run test       # 스킬 클라이언트 테스트 (표준 라이브러리 unittest)
mise run catalog    # 참고 명세에서 카탈로그 재생성
mise run package    # .skill 패키지 생성
```

## 한계

- **카탈로그 49개 서비스 중 실제 호출 가능한 것은 20개입니다.** 나머지 29개는 KIPRIS 포털에 API는 있으나 ServicePath가 공개 문서에서 확인되지 않아 URL을 만들 수 없습니다. `path_confidence` 필드가 각 서비스의 확인 수준을 밝힙니다.
- 오퍼레이션 540개 중 29개는 원본 명세에 오퍼레이션 ID가 없어 호출할 수 없습니다.
- **검색 결과는 사실 조회이지 법적 판단이 아닙니다.** 등록 가능성·침해 여부·권리 범위 판단은 변리사의 영역입니다.

## 출처와 감사

서비스 경로와 오퍼레이션 명세는 [nuri428/kipris_skill](https://github.com/nuri428/kipris_skill) (MIT)이 정리한 KIPRIS Plus 공개 명세에서 파생했습니다. 원본 저장소는 49개 서비스의 명세를 마크다운으로 문서화했고, 이 저장소는 그것을 기계 판독 카탈로그로 변환해 스크립트가 직접 소비하도록 재구성했습니다.

## 라이선스

MIT — [LICENSE](LICENSE)
