#!/usr/bin/env bash
# Claude Desktop / claude.ai 업로드용 .skill 패키지 생성.
# .skill은 스킬 폴더가 아카이브 루트에 오는 zip이다 (kipris/SKILL.md, kipris/scripts/...).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_DIR="${1:-skills/kipris}"
NAME="$(basename "$SKILL_DIR")"
OUT="$ROOT/dist/$NAME.skill"

cd "$ROOT"
[ -f "$SKILL_DIR/SKILL.md" ] || { echo "error: $SKILL_DIR/SKILL.md 없음" >&2; exit 1; }

# frontmatter의 name이 디렉터리명과 다르면 업로드 후 스킬이 다른 이름으로 등록된다.
FM_NAME="$(awk '/^name:/{print $2; exit}' "$SKILL_DIR/SKILL.md")"
[ "$FM_NAME" = "$NAME" ] || { echo "error: frontmatter name '$FM_NAME' != 디렉터리 '$NAME'" >&2; exit 1; }

mkdir -p "$ROOT/dist"
rm -f "$OUT"
# zip은 .gitignore를 보지 않는다. 스킬 폴더에 남은 키 파일이 업로드 아카이브에
# 그대로 실려 나가지 않도록 kipris.py가 읽는 자격 증명 파일명을 모두 제외한다.
(cd "$(dirname "$SKILL_DIR")" && zip -qr "$OUT" "$NAME" \
  -x '*/__pycache__/*' '*.pyc' '*/.DS_Store' '*/evals/*' \
     '*/.env' '*/.env.*' '*/api_key' '*/openapi_key' '*/kipo_key')

echo "created $OUT ($(du -h "$OUT" | cut -f1))"
unzip -l "$OUT" | tail -3
