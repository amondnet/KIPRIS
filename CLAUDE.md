# CLAUDE.md

이 저장소에서 작업할 때 참고할 컨텍스트.

## 이 저장소는 무엇인가

KIPRIS Plus Open API를 검색하는 **Claude 스킬을 만드는 저장소**다. 스킬 자체가 산출물이고, Claude Code와 Claude Desktop(cowork) 양쪽에서 동작해야 한다.

이 제약이 설계를 결정한다: 배포되는 스크립트는 **Python 표준 라이브러리만** 쓴다. Claude Desktop 실행 샌드박스에는 bun도 pip 설치 기회도 보장되지 않는다. 개발 도구(bun/TypeScript)는 저장소 안에서만 돌고 패키지에는 들어가지 않는다.

## 파일 구조

| 경로                                   | 역할                                                           |
| -------------------------------------- | -------------------------------------------------------------- |
| `skills/kipris/SKILL.md`               | 스킬 본체. Claude가 트리거 시 읽는다. 500줄 이하 유지          |
| `skills/kipris/scripts/kipris.py`      | 런타임 클라이언트. 표준 라이브러리만, 의존성 추가 금지         |
| `skills/kipris/references/catalog/`    | **생성물** — 직접 편집하지 않는다. `mise run catalog`로 재생성 |
| `skills/kipris/references/gateways.md` | 게이트웨이·공통 파라미터 레퍼런스                              |
| `tools/build-catalog.ts`               | 참고 명세(markdown) → 카탈로그(JSON) 변환. Bun/TS, 의존성 없음 |
| `tools/package-skill.sh`               | `.skill` zip 생성 (Desktop 업로드용)                           |
| `tests/test_kipris.py`                 | 클라이언트 테스트. stdlib unittest                             |
| `.claude-plugin/`                      | 배포 매니페스트. `claude plugin validate .`로 검증             |

## 카탈로그는 생성물이다

`skills/kipris/references/catalog/**`를 손으로 고치면 다음 `mise run catalog`에서 날아간다. 파싱 결과가 틀렸다면 `tools/build-catalog.ts`를 고친다.

원본은 [nuri428/kipris_skill](https://github.com/nuri428/kipris_skill)의 `docs/services/*.md` (MIT, commit `d8b929b`)다. 빌드 스크립트가 `/tmp/kipris_ref/docs/services`를 기본 입력으로 본다.

**카탈로그에 데이터를 지어넣지 않는다.** ServicePath나 파라미터명을 추측해 채우면 잘못된 URL이 조용히 만들어지고, 사용자의 월 1,000회 할당량을 태운다. 원본에 없으면 null로 두고 `path_confidence`로 확인 수준을 밝힌다.

## API 호출 규율

무료 등급 월 1,000회, 초당 50회 초과 시 IP 차단. 개발 중에도 실호출은 아껴 쓴다. 파싱·매핑 로직은 `tests/`에 저장된 XML 샘플로 검증하고, 실호출은 정말 응답 구조를 확인해야 할 때만 한다.

## 게이트웨이 이중성

같은 서비스가 두 게이트웨이(OpenAPI `accessKey` / KIPO `ServiceKey`)에 걸쳐 있고 오퍼레이션마다 다를 수 있다. `kipris.py`의 `OPERATION_GATEWAY`는 실호출로 확인된 매핑이고, 그 밖은 `get*` → KIPO 휴리스틱으로 추정한 뒤 `_meta.gateway_source`에 근거를 남긴다. **추정을 확인된 사실로 승격시키려면 실호출 검증을 거친다.**

## 검증

```bash
mise run test     # 21개 테스트
mise run catalog  # 재생성 — 멱등이어야 한다 (출력이 바이트 단위로 동일)
mise run package  # dist/kipris.skill
```

커밋은 conventional commits — `Skill("standards:commit-convention")`. husky가 commit-msg와 pre-commit(lint-staged + test)에서 검증한다.

## 하지 말 것

- 스킬 런타임에 서드파티 Python 패키지 추가 (Desktop 샌드박스에서 깨진다)
- 카탈로그 파일 직접 편집
- API 키를 로그·커밋·대화에 노출. `echo $KIPRIS_API_KEY` 대신 `kipris.py doctor`
- 검색 결과를 등록 가능성·침해 여부 같은 법적 판단으로 확장
