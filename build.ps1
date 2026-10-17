# Builds dist/Flash32/Flash32.exe (one folder; zip it to ship).
# Uses its own virtual environment (.venv-build) so nothing else installed
# on this machine ends up in the app.
#   .\build.ps1
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($PSScriptRoot)
Set-Location -LiteralPath $root

$venv = Join-Path $root '.venv-build'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $base = (Get-Command python -CommandType Application | Select-Object -First 1).Source
    & $base -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the build environment.' }
}
& $python -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller==6.13.0
if ($LASTEXITCODE -ne 0) { throw 'Could not install build requirements.' }

# Qt modules the app doesn't use: QML/Quick, PDF, web, multimedia, 3D, ...
$qtUnused = @('QtQml', 'QtQuick', 'QtQuickWidgets', 'QtQuickControls2', 'QtPdf', 'QtPdfWidgets',
    'QtWebEngineCore', 'QtWebEngineWidgets', 'QtWebChannel', 'QtWebSockets', 'QtNetwork',
    'QtMultimedia', 'QtMultimediaWidgets', 'Qt3DCore', 'QtCharts', 'QtDataVisualization',
    'QtOpenGL', 'QtOpenGLWidgets', 'QtSql', 'QtSvg', 'QtTest', 'QtXml', 'QtBluetooth',
    'QtPositioning', 'QtSensors', 'QtSerialPort', 'QtDesigner', 'QtHelp', 'QtUiTools')
$excludes = @()
foreach ($m in $qtUnused) { $excludes += @('--exclude-module', "PySide6.$m") }

$originalPath = $env:PATH
try {
    # Qt uses Windows' own ICU. Development tools on PATH (Poppler and the
    # like) can ship an incompatible icuuc.dll that would get bundled.
    $env:PATH = "$venv\Scripts;$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"
    & $python -m PyInstaller `
        --noconfirm --clean --onedir --windowed `
        --name Flash32 `
        --icon assets/app.ico `
        --add-data 'data;data' `
        --add-data 'assets;assets' `
        --collect-data esptool `
        --collect-submodules esptool `
        --exclude-module tkinter `
        @excludes `
        app.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    $internal = "$root\dist\Flash32\_internal"
    if (Test-Path -LiteralPath "$internal\icuuc.dll") {
        throw 'An unexpected ICU DLL was bundled; Qt must use the Windows one.'
    }
    # Software OpenGL fallback and Qt's own translations: unused, ~25 MB.
    Remove-Item -Force -ErrorAction SilentlyContinue "$internal\PySide6\opengl32sw.dll"
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "$internal\PySide6\translations"
}
finally {
    $env:PATH = $originalPath
}
$size = (Get-ChildItem "$root\dist\Flash32" -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Built: $root\dist\Flash32\Flash32.exe ({0:N0} MB)" -f $size)
