#!/usr/bin/env bash
# SECOND_MACHINE_INSTALLER_V1 — macOS/Unix bootstrap. Canonical DAW/M4L logic is Python.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
mkdir -p logs

echo "SECOND_MACHINE_INSTALLER_V1"
echo "repository: $REPO_ROOT"

python_ok() {
  local exe="$1"
  "$exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)" >/dev/null 2>&1
}

find_python() {
  local cmd
  for cmd in python3.12 python3; do
    if command -v "$cmd" >/dev/null 2>&1 && python_ok "$(command -v "$cmd")"; then
      command -v "$cmd"
      return 0
    fi
  done
  if [[ -x /opt/homebrew/bin/python3.12 ]] && python_ok /opt/homebrew/bin/python3.12; then
    echo /opt/homebrew/bin/python3.12
    return 0
  fi
  if [[ -x /usr/local/bin/python3.12 ]] && python_ok /usr/local/bin/python3.12; then
    echo /usr/local/bin/python3.12
    return 0
  fi
  return 1
}

PYTHON_STATUS="ALREADY_CURRENT"
WINGET_USED=0
PYTHON="$(find_python || true)"
if [[ -z "${PYTHON}" ]]; then
  if command -v brew >/dev/null 2>&1; then
    echo "Compatible Python not found. Installing python@3.12 via Homebrew."
    brew install python@3.12
    PYTHON="$(find_python || true)"
    WINGET_USED=1
    PYTHON_STATUS="INSTALLED"
  fi
fi

if [[ -z "${PYTHON}" ]]; then
  cat > logs/second_machine_installer_v1.json <<'EOF'
{
  "status": "PYTHON_INSTALL_REQUIRED",
  "exact_command": "brew install python@3.12"
}
EOF
  echo "PYTHON_INSTALL_REQUIRED"
  echo "exact_command: brew install python@3.12"
  exit 2
fi

VENV_DIR="$REPO_ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_STATUS="ALREADY_CURRENT"
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Creating virtual environment at .venv"
  "$PYTHON" -m venv "$VENV_DIR"
  VENV_STATUS="CREATED"
elif ! python_ok "$VENV_PYTHON"; then
  echo ".venv exists but is not Python 3.12+. Delete .venv and rerun."
  exit 2
fi

echo "Installing project into .venv (declared pyproject dependencies)."
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -e "$REPO_ROOT"
"$VENV_PYTHON" -c "import copilot, pydantic, numpy, soundfile"

export COPILOT_INSTALLER_PYTHON_STATUS="$PYTHON_STATUS"
export COPILOT_INSTALLER_VENV_STATUS="$VENV_STATUS"
export COPILOT_INSTALLER_DEPS_STATUS="ALREADY_CURRENT"
if [[ "$WINGET_USED" == "1" ]]; then
  export COPILOT_INSTALLER_WINGET_USED=1
fi

echo "Installing Remote Script, M4L runtime, and config via Python."
"$VENV_PYTHON" -m copilot.cli install
code=$?

echo
echo "Use this interpreter:"
echo "  $VENV_PYTHON -m copilot.cli doctor"
echo "Or: source .venv/bin/activate"
exit "$code"
