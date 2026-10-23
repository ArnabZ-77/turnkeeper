# Source in Git Bash:  source scripts/env-x64.sh
export PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.tools/node-x64:$PATH"
node -p "'node ' + process.version + ' ' + process.arch"
