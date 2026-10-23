# Downloads a portable x64 Node (same version as the system Node) into video/.tools/node-x64.
# Needed on Windows-on-ARM (Snapdragon): Remotion ships no win32-arm64 compositor, so Remotion
# must run under x64 Node (Prism emulation). Verifies the SHA-256 against nodejs.org SHASUMS256.txt.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$ver = 'v24.21.0'
$tools = Join-Path $root '.tools'
New-Item -ItemType Directory -Force $tools | Out-Null
$zip = Join-Path $tools "node-$ver-win-x64.zip"
Invoke-WebRequest "https://nodejs.org/dist/$ver/node-$ver-win-x64.zip" -OutFile $zip
$sums = (Invoke-WebRequest "https://nodejs.org/dist/$ver/SHASUMS256.txt").Content
$expected = ($sums -split "`n" | Where-Object { $_ -match "node-$ver-win-x64.zip" }).Split(' ')[0]
$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($expected -ne $actual) { throw "SHA-256 mismatch: $actual vs $expected" }
Expand-Archive $zip -DestinationPath $tools -Force
if (Test-Path (Join-Path $tools 'node-x64')) { Remove-Item -Recurse -Force (Join-Path $tools 'node-x64') }
Rename-Item (Join-Path $tools "node-$ver-win-x64") 'node-x64'
Remove-Item $zip
& (Join-Path $tools 'node-x64\node.exe') -p "process.version + ' ' + process.arch"
