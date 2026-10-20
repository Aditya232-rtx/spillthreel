#!/usr/bin/env bash
# Usage: install-subagents.sh <path-to-awesome-claude-code-subagents> <target-repo>
# User-provided Claude -> OpenCode conversion, with fail-fast validation.
set -euo pipefail
[[ $# -eq 2 ]] || { echo "Usage: $0 <source-repo> <target-repo>" >&2; exit 2; }
SRC="$1/categories"
DEST="$2/.opencode/agents"
[[ -d "$SRC" ]] || { echo "Missing categories directory: $SRC" >&2; exit 1; }
EDITORS="fastapi-developer expo-react-native-expert postgres-pro terraform-engineer test-automator debugger llm-architect prompt-engineer cloud-architect"
READONLY="code-reviewer security-auditor"
find_agent() { find "$SRC" -name "$1.md" -print -quit; }
# Validate all sources before replacing any existing agent.
for a in $EDITORS $READONLY; do
  [[ -n "$(find_agent "$a")" ]] || { echo "Missing agent: $a" >&2; exit 1; }
done
mkdir -p "$DEST"
convert() {
  local f
  f=$(find_agent "$1")
  # BSD awk rejects multiline -v values; ENVIRON preserves the permission block.
  SPILL_AGENT_PERMISSION="$2" awk '
    /^---$/ { c++; if (c==2) { print "mode: subagent"; printf "%s", ENVIRON["SPILL_AGENT_PERMISSION"] } print; next }
    c==1 && /^(tools|model):/ { next }
    { print }
  ' "$f" > "$DEST/$1.md"
  echo "installed $1"
}
for a in $EDITORS; do convert "$a" ""; done
RO=$'permission:\n  edit: deny\n  bash: deny\n'
for a in $READONLY; do convert "$a" "$RO"; done
