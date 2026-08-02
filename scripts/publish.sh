#!/usr/bin/env bash
# PyPI 發佈(前提:~/Delvin-agent/.env 有 PYPI_TOKEN)。用法: bash scripts/publish.sh
set -euo pipefail
cd "$(dirname "$0")/.."
TOK=$(grep -h '^PYPI_TOKEN=' ~/Delvin-agent/.env | head -1 | cut -d= -f2- | tr -d '"\r')
[ -n "$TOK" ] || { echo "缺 PYPI_TOKEN(pypi.org → Account settings → API tokens)"; exit 1; }
.venv/bin/pip install -q build twine
rm -rf dist
.venv/bin/python -m build
.venv/bin/python -m twine upload -u __token__ -p "$TOK" dist/*
echo "✅ published: https://pypi.org/project/quorumgate-llm/"
