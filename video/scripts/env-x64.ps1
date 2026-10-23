# Dot-source in a PowerShell session:  . .\scripts\env-x64.ps1
# Puts the portable x64 Node first on PATH so npm/npx/remotion/playwright run as x64.
$env:Path = (Join-Path (Split-Path -Parent $PSScriptRoot) '.tools\node-x64') + ';' + $env:Path
node -p "'node ' + process.version + ' ' + process.arch"
