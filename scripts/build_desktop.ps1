$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到 .venv，请先创建虚拟环境并安装 requirements-desktop.txt"
}

Push-Location $repoRoot
try {
    & $python -m PyInstaller --noconfirm --clean PaperMind.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 构建失败，退出码 $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

