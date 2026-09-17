#!/usr/bin/env bash
# Remove Copilot-owned runtime assets only. Never user projects or Ableton prefs.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

REMOVE_VENV=0
REMOVE_CONFIG=0
for arg in "$@"; do
  case "$arg" in
    --remove-venv) REMOVE_VENV=1 ;;
    --remove-config) REMOVE_CONFIG=1 ;;
  esac
done

VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
code=0
if [[ -x "$VENV_PYTHON" ]]; then
  args=(-m copilot.cli uninstall-copilot)
  if [[ "$REMOVE_VENV" == "1" ]]; then
    args+=(--remove-venv)
  fi
  if [[ "$REMOVE_CONFIG" == "1" ]]; then
    args+=(--remove-config)
  fi
  "$VENV_PYTHON" "${args[@]}"
  code=$?
else
  echo "No .venv Python. Skipping Python-owned uninstall; filesystem cleanup is limited."
fi

if [[ "$REMOVE_VENV" == "1" && -d "$REPO_ROOT/.venv" ]]; then
  rm -rf "$REPO_ROOT/.venv"
  echo "Removed .venv"
fi

echo "Uninstall does not remove user projects, Ableton preferences, or unmanaged User Library files."
exit "$code"
