$ErrorActionPreference = 'Stop'

$venvPython = ".venv/Scripts/python.exe"
if (Test-Path $venvPython) {
    $pythonCmd = $venvPython
} else {
    $pythonCmd = "python"
}

Write-Host "[1/4] Checking required files..."
$ffmpegPath = "third_party/ffmpeg/bin/ffmpeg.exe"
$ffprobePath = "third_party/ffmpeg/bin/ffprobe.exe"
if (!(Test-Path $ffmpegPath)) {
    throw "Missing $ffmpegPath. Place ffmpeg.exe there before building."
}
if (!(Test-Path $ffprobePath)) {
    throw "Missing $ffprobePath. Place ffprobe.exe there before building."
}

Write-Host "[2/4] Installing build dependency (PyInstaller)..."
& $pythonCmd -m pip install --upgrade pyinstaller

Write-Host "[3/4] Building one-file executable..."
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$workPath = "build/pyi_work_$stamp"
$distPath = "dist/pyi_dist_$stamp"

# Use unique work/dist paths per build to avoid permission errors on locked folders.
# NOTE: --specpath is only valid when generating a spec (not when building from one).
& $pythonCmd -m PyInstaller --noconfirm --workpath $workPath --distpath $distPath run_abx.spec

$builtExe = Join-Path $distPath "run_abx.exe"
if (!(Test-Path $builtExe)) {
    throw "Build finished but executable not found at $builtExe"
}

Copy-Item -Force $builtExe "dist/run_abx.exe"

Write-Host "[4/4] Build complete."
Write-Host "Output: dist/run_abx.exe"
