# 一键打包脚本：PyInstaller 产出免安装目录 dist\AI简历筛选助手\（含 Python 运行时、随包 Tesseract、前端静态资源）
# 用法：.\scripts\build_exe.ps1 [-TesseractDir "C:\Program Files\Tesseract-OCR"]
param([string]$TesseractDir = "")

$ErrorActionPreference = "Stop"

function Fail([string]$msg) {
    Write-Host "[构建失败] $msg" -ForegroundColor Red
    exit 1
}

# uv 解析：优先 PATH，回退本机默认安装位置
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv -and (Test-Path "C:\Users\boyanx1\.local\bin\uv.exe")) {
    $uv = "C:\Users\boyanx1\.local\bin\uv.exe"
}
if (-not $uv) { Fail "未找到 uv（PATH 与 C:\Users\boyanx1\.local\bin 均不存在）" }

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    # ---- 1. 探测 Tesseract：tesseract.exe 与 tessdata\chi_sim.traineddata 允许分处不同目录 ----
    $candidates = @()
    if ($TesseractDir) { $candidates += $TesseractDir }
    if ($env:TESSERACT_CMD) { $candidates += (Split-Path -Parent $env:TESSERACT_CMD) }
    $candidates += "C:\Program Files\Tesseract-OCR"
    $candidates += "$env:LOCALAPPDATA\Tesseract-OCR"
    $candidates = @($candidates | Where-Object { $_ } | Select-Object -Unique)

    # 可执行目录（tesseract.exe 所在）与语言包目录（tessdata\chi_sim.traineddata 所在）分开解析
    $tessExeDir = $null
    $tessDataDir = $null
    foreach ($c in $candidates) {
        if (-not $tessExeDir -and (Test-Path (Join-Path $c "tesseract.exe"))) { $tessExeDir = $c }
        if (-not $tessDataDir -and (Test-Path (Join-Path $c "tessdata\chi_sim.traineddata"))) { $tessDataDir = $c }
    }
    if (-not $tessExeDir -or -not $tessDataDir) {
        Fail ("未找到可用的 Tesseract（需 tesseract.exe 与 tessdata\chi_sim.traineddata）。`n已探测路径：`n  " +
            ($candidates -join "`n  ") +
            "`n可传入参数 -TesseractDir 指定安装目录。")
    }
    Write-Host "[1/5] tesseract.exe 目录: $tessExeDir"
    Write-Host "      tessdata 目录     : $tessDataDir\tessdata"

    # ---- 2. 确保 PyInstaller 可用 ----
    Write-Host "[2/5] 检查 PyInstaller ..."
    $ErrorActionPreference = "Continue"
    $null = & $uv run python -m PyInstaller --version 2>&1
    $ok = ($LASTEXITCODE -eq 0)
    if (-not $ok) {
        Write-Host "未检测到 PyInstaller，执行 uv add --dev pyinstaller ..."
        & $uv add --dev pyinstaller
        if ($LASTEXITCODE -ne 0) { Fail "pyinstaller 安装失败" }
        $null = & $uv run python -m PyInstaller --version 2>&1
        if ($LASTEXITCODE -ne 0) { Fail "PyInstaller 安装后仍不可用" }
    }
    $ErrorActionPreference = "Stop"
    Write-Host "PyInstaller 就绪"

    # ---- 3. 图标：缺失时用 make_icon.py 生成（失败仅告警，继续无图标构建）----
    if (-not (Test-Path "assets\hr-cv.ico")) {
        Write-Host "[3/5] assets/hr-cv.ico 不存在，运行 make_icon.py 生成 ..."
        & $uv run python scripts/make_icon.py
        if ($LASTEXITCODE -ne 0) { Write-Warning "图标生成失败，继续无图标构建" }
    }
    else {
        Write-Host "[3/5] 图标已存在: assets/hr-cv.ico"
    }
    $iconArgs = @()
    if (Test-Path "assets\hr-cv.ico") { $iconArgs += @("--icon", "assets/hr-cv.ico") }

    # ---- 4. 清理 build/ dist/ 并执行 PyInstaller 构建 ----
    Write-Host "[4/5] 清理 build/ dist/ 并开始构建（首次运行需解析依赖，可能耗时数分钟）..."
    Remove-Item build, dist -Recurse -Force -ErrorAction SilentlyContinue

    # 说明：Tesseract 相关文件（tesseract.exe、运行时 DLL、tessdata）不用 --add-data 打进
    # PyInstaller：PyInstaller 6 会把 add-data 里的 PE 文件重新按二进制分类并递归解析依赖，
    # 把 tesseract 自带的旧版 libcrypto-3-x64.dll 等收集到 _internal 根目录，遮蔽 Python
    # 运行时自带的 OpenSSL，导致 _ssl 导入失败。改为构建后直接复制（见步骤 4.5）。
    & $uv run python -m PyInstaller --noconfirm --clean --onedir --noconsole --name AI简历筛选助手 `
        --paths src `
        --add-data "src/hr_cv/static;hr_cv/static" `
        --collect-all uvicorn `
        --collect-all webview `
        --collect-all pypdfium2 `
        @iconArgs `
        scripts/run_app.py
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller 构建失败" }

    # ---- 4.5 随包 Tesseract：构建后复制到 _internal\tesseract\（自包含子目录）----
    $tessDest = "dist\AI简历筛选助手\_internal\tesseract"
    New-Item -ItemType Directory -Force $tessDest | Out-Null
    Copy-Item "$tessExeDir\tesseract.exe" "$tessDest\" -Force
    Copy-Item "$tessExeDir\*.dll" "$tessDest\" -Force
    Copy-Item "$tessDataDir\tessdata" "$tessDest\" -Recurse -Force

    # ---- 5. 校验产物并输出摘要 ----
    $exe = "dist\AI简历筛选助手\AI简历筛选助手.exe"
    if (-not (Test-Path $exe)) { Fail "构建产物缺失: $exe（PyInstaller 6 onedir 应为 dist\AI简历筛选助手\AI简历筛选助手.exe + dist\AI简历筛选助手\_internal\）" }
    $internalTess = "dist\AI简历筛选助手\_internal\tesseract"
    $sizeMB = [math]::Round((Get-ChildItem "dist\AI简历筛选助手" -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
    Write-Host ""
    Write-Host "========== 构建摘要 ==========" -ForegroundColor Green
    Write-Host "产物路径: $((Resolve-Path $exe).Path)"
    Write-Host "总体积  : $sizeMB MB"
    Write-Host "随包 Tesseract: $((Resolve-Path 'dist\AI简历筛选助手\_internal').Path)\tesseract\"
    Write-Host ("  tesseract.exe        : " + (Test-Path "$internalTess\tesseract.exe"))
    Write-Host ("  tessdata chi_sim     : " + (Test-Path "$internalTess\tessdata\chi_sim.traineddata"))
    Write-Host ("  运行时 DLL 数量      : " + (Get-ChildItem "$internalTess\*.dll" -ErrorAction SilentlyContinue).Count)
    Write-Host "==============================" -ForegroundColor Green
}
finally {
    Pop-Location
}
