#!/usr/bin/env bash
# Runs once after the dev container is created: install n2y in editable mode
# and print the toolchain versions so drift is obvious at a glance.
set -euo pipefail

# The named volume mounted at ~/.claude is owned by root on first creation.
sudo chown -R "$(id -un)":"$(id -gn)" "$HOME/.claude"

pip install --user -e '.[dev]'

echo
echo "=== Toolchain versions ==="
python --version
pandoc --version | head -1
echo "mermaid-cli $(mmdc --version)"
node --version
bd version || true
claude --version || true
