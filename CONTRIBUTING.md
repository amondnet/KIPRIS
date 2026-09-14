# 기여 가이드

## 개발 환경

```bash
mise trust && mise install   # bun + python
bun install                  # git hooks (husky, commitlint, lint-staged)
mise run test
```

## 변경 전에 알아둘 것

**`skills/kipris/references/catalog/**` 는 생성물이다.** 직접 고치지 말고 `tools/build-catalog.ts`를 고친 뒤 `mise run catalog`로 재생성한다. 빌드는 멱등이어야 한다 — 두 번 돌렸을 때 출력이 바이트 단위로 같아야 한다.

**배포 스크립트에 의존성을 추가하지 않는다.** `skills/kipris/scripts/kipris.py`는 Python 표준 라이브러리만 쓴다. Claude Desktop 샌드박스에는 패키지를 설치할 방법이 없다.

**API 명세를 추측으로 채우지 않는다.** ServicePath나 파라미터명이 원본에 없으면 null로 두고 `path_confidence`로 확인 수준을 밝힌다. 틀린 값은 잘못된 URL을 만들고 사용자의 월 1,000회 할당량을 태운다. 실호출로 확인한 정보라면 PR 본문에 요청·응답 근거를 남긴다.

## 커밋

Conventional Commits를 따른다. commitlint가 검증한다.

```
feat: 상표 서비스 오퍼레이션 매핑 추가
fix: 해외특허 국가코드 파라미터 인코딩 수정
docs: README에 Desktop 설치 절차 보강
```

## PR

- `mise run test`가 통과해야 한다 (pre-commit 훅이 자동 실행한다)
- 동작이 바뀌면 테스트를 함께 낸다
- 스킬 문서(`SKILL.md`)와 실제 동작이 어긋나지 않게 같이 고친다
