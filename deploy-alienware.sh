#!/bin/sh
# Copy the assist to the Alienware, where Aion 2 runs, into its own folder.
# Only this project's source files are sent. Nothing on the other side is ever
# deleted, so the owner's config.json, profile.json, approval file, journals and
# shots/ there are left alone.
#
#   ./deploy-alienware.sh               copy, byte-compile, run the read-only doctor there
#   ./deploy-alienware.sh --check       only list what would be copied
#   ./deploy-alienware.sh --setup-venv  also create .venv there and install requirements.txt
#                                       (jeepney, needed for the portal/session-bus path)
set -eu
HOST=${AION2_HOST:-alienware-bazzite}
DEST=${AION2_DEST:-Projects/aion2-cleric-assist}   # relative to the remote home
cd "$(dirname "$0")"
FILES="README.md requirements.txt config.example.json regions.example.json aion2_assist kwin_script tests tools"
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' aion2_assist/__init__.py)

case "${1:-}" in
    --check) printf '%s\n' $FILES; exit 0 ;;
    --setup-venv|"") ;;
    *) echo "usage: $0 [--check|--setup-venv]" >&2; exit 2 ;;
esac

if command -v rsync >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    rsync -az --timeout=120 --exclude='__pycache__' --exclude='*.pyc' --exclude='*.bak*' $FILES "$HOST:$DEST/"
else
    ssh "$HOST" "mkdir -p $DEST"
    # shellcheck disable=SC2086
    scp -q -r $FILES "$HOST:$DEST/"
fi

if [ "${1:-}" = "--setup-venv" ]; then
    ssh "$HOST" "cd $DEST && { [ -x .venv/bin/python ] || python3 -m venv .venv; } && .venv/bin/pip install -q -r requirements.txt"
fi

ssh "$HOST" "cd $DEST && PY=python3 && { [ -x .venv/bin/python ] && PY=.venv/bin/python || true; } \
    && \$PY -m compileall -q aion2_assist && \$PY -m aion2_assist doctor >/dev/null \
    && echo \"deployed $VERSION to \$(hostname):$DEST with \$(\$PY --version) at \$(date -u +%H:%M:%SZ)\""
