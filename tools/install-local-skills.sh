#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
if ! GIT_COMMON_DIR=$(git -C "$REPO_ROOT" rev-parse \
    --path-format=absolute --git-common-dir 2>/dev/null)
then
    printf 'ERROR: installer is not inside a Git checkout: %s\n' "$REPO_ROOT" >&2
    exit 1
fi
GIT_COMMON_DIR=$(CDPATH= cd -- "$GIT_COMMON_DIR" && pwd -P)
case "$GIT_COMMON_DIR" in
    */.git) PRIMARY_ROOT=${GIT_COMMON_DIR%/.git} ;;
    *)
        printf 'ERROR: cannot derive primary checkout from Git common dir: %s\n' \
            "$GIT_COMMON_DIR" >&2
        exit 1
        ;;
esac
SOURCE=$PRIMARY_ROOT/.claude/skills/iterating-esp32-amoled-ui
CODEX_ROOT=${CODEX_HOME:-${HOME:?HOME is required when CODEX_HOME is unset}/.codex}
SKILLS_DIR=$CODEX_ROOT/skills
TARGET=$SKILLS_DIR/iterating-esp32-amoled-ui

if [ ! -d "$SOURCE" ]; then
    printf 'ERROR: canonical skill is missing from the primary checkout: %s\n' \
        "$SOURCE" >&2
    printf '%s\n' \
        'Merge the skill, then run tools/install-local-skills.sh from the primary checkout.' >&2
    exit 1
fi

mkdir -p -- "$SKILLS_DIR"
if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
    if [ ! -L "$TARGET" ]; then
        printf 'ERROR: refusing to overwrite non-symlink target: %s\n' "$TARGET" >&2
        exit 1
    fi
    rm -- "$TARGET"
fi

ln -s -- "$SOURCE" "$TARGET"
printf 'Installed shared skill: %s -> %s\n' "$TARGET" "$SOURCE"
printf 'Start a new Codex session if the skill is not discovered immediately.\n'
