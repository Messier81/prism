#!/bin/bash
# Prism install script
# Installs Prism commands into any project's .claude/ directory
# Usage: ./install.sh [path/to/project]

set -e

PRISM_REPO="https://github.com/Messier81/prism"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo -e "${BLUE} Prism — Review intelligence from your team's PR history${NC}"
echo ""

TARGET_DIR="${1:-$(pwd)}"

if [ ! -d "$TARGET_DIR" ]; then
  echo "Error: Directory $TARGET_DIR does not exist."
  exit 1
fi

echo -e "Installing into: ${GREEN}$TARGET_DIR${NC}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/commands/prism-init.md" ]; then
  SOURCE_DIR="$SCRIPT_DIR"
  echo -e "${YELLOW}Using local files from $SOURCE_DIR${NC}"
else
  echo "Cloning Prism from GitHub..."
  TMPDIR_PRISM=$(mktemp -d)
  trap "rm -rf $TMPDIR_PRISM" EXIT

  if ! git clone --depth=1 "$PRISM_REPO" "$TMPDIR_PRISM/prism" 2>/dev/null; then
    echo "Error: Could not clone $PRISM_REPO"
    echo "Try: git clone $PRISM_REPO && cd prism && ./install.sh"
    exit 1
  fi

  SOURCE_DIR="$TMPDIR_PRISM/prism"
fi

if [ -f "$TARGET_DIR/.claude/commands/prism-init.md" ]; then
  echo -e "${YELLOW}Prism is already installed in this project.${NC}"
  printf "Overwrite existing Prism files? [y/N] "
  read -r CONFIRM
  if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Aborted. Existing installation unchanged."
    exit 0
  fi
fi

mkdir -p "$TARGET_DIR/.claude/commands"
mkdir -p "$TARGET_DIR/.claude/agents"
mkdir -p "$TARGET_DIR/.prism/scripts"

cp "$SOURCE_DIR/commands/prism-init.md" "$TARGET_DIR/.claude/commands/"
cp "$SOURCE_DIR/commands/prism-review.md" "$TARGET_DIR/.claude/commands/"
cp "$SOURCE_DIR/commands/prism-patterns.md" "$TARGET_DIR/.claude/commands/"
cp "$SOURCE_DIR/commands/prism-learn.md" "$TARGET_DIR/.claude/commands/"

cp "$SOURCE_DIR/agents/reviewer.md" "$TARGET_DIR/.claude/agents/"

cp "$SOURCE_DIR/scripts/scrape.py" "$TARGET_DIR/.prism/scripts/"

cat > "$TARGET_DIR/.prism/.gitignore" << 'GITIGNORE'
history/
scripts/
GITIGNORE

echo ""
echo -e "${GREEN}Done! Prism installed into $TARGET_DIR${NC}"
echo -e "  4 commands · 1 agent · 1 script"
echo ""
echo "Quick start:"
echo ""
echo -e "  1. Open Claude Code in ${GREEN}$TARGET_DIR${NC}"
echo -e "  2. Run ${GREEN}/prism-init${NC} to scrape your team's PR review history"
echo -e "  3. Run ${GREEN}/prism-review <PR#>${NC} to review a PR using learned patterns"
echo ""
echo "Commands:"
echo -e "  ${GREEN}/prism-init${NC}          Scrape PR history and learn team patterns (run once)"
echo -e "  ${GREEN}/prism-review <PR#>${NC}  Review a PR using learned patterns"
echo -e "  ${GREEN}/prism-patterns${NC}      View, add, or edit review patterns"
echo -e "  ${GREEN}/prism-learn${NC}         Update patterns from recent PRs"
echo ""
echo "Files:"
echo -e "  ${GREEN}.prism/patterns.json${NC}     Learned patterns — commit this"
echo -e "  ${GREEN}.prism/calibration.json${NC}  Feedback data — commit this"
echo -e "  ${GREEN}.prism/summary.json${NC}      Stats — commit this"
echo -e "  ${GREEN}.prism/PATTERNS.md${NC}       Human-readable — commit this"
echo -e "  .prism/history/            Raw data — gitignored"
echo ""
