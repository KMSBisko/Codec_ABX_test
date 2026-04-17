$ErrorActionPreference = 'Stop'

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
python -m pip install --upgrade pyinstaller

Write-Host "[3/4] Building one-file executable..."
python -m PyInstaller --clean --noconfirm run_abx.spec

Write-Host "[4/4] Build complete."
Write-Host "Output: dist/run_abx.exe"
