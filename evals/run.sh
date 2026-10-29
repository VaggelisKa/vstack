#!/usr/bin/env bash
# Runs the plugin evals on this machine with your Claude Code login. Extra arguments go to
# `claude plugin eval`, for example:
#   evals/run.sh --tag smoke --runs 1          # the quick set, once each
#   evals/run.sh --case 'babysit-*'            # one skill
#   evals/run.sh --case babysit-19-land-it --keep-temp
# Each run costs API usage: babysit and autonomous-merge cases are long. Add --max-cost-usd to cap it.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$root/evals/_harness/sync.py" --check

# The skills' scripts need Python 3.10+, and the eval sandbox hides your home directory, so a Python
# installed there (pyenv, uv) can't run. Without a usable one, install one into evals/.python with uv.
# A uv-managed Python (a relocatable standalone build) is copied there instead when uv itself is gone.
if ! python3 "$root/evals/_harness/scaffold.py" --find-python >/dev/null; then
  managed=$(ls -d "$HOME"/.local/share/uv/python/cpython-3.1[0-9]*-none 2>/dev/null | sort -V | tail -1 || true)
  if command -v uv >/dev/null; then
    echo "Installing Python 3.13 into evals/.python for the eval sandbox (once)..." >&2
    uv python install 3.13 --install-dir "$root/evals/.python" >&2
  elif [ -n "$managed" ]; then
    echo "Copying $managed into evals/.python for the eval sandbox (once)..." >&2
    mkdir -p "$root/evals/.python" && cp -R "$managed" "$root/evals/.python/"
  else
    echo "evals/run.sh: no Python 3.10+ outside your home directory. Install one (or uv), or set VSTACK_EVAL_PYTHON." >&2
    exit 1
  fi
fi

exec claude plugin eval "$root" \
  --scaffold --trust-plugin \
  --allow-tools Bash Write Edit \
  --judge-model sonnet \
  --no-publish \
  "$@"
