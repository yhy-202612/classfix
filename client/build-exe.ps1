param(
    [string]$Python = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    [string]$Destination = (Join-Path $PSScriptRoot 'dist'),
    [string]$BuildDirectory = (Join-Path $PSScriptRoot 'build'),
    [string]$FFprobeBundle = '',
    [string]$DesktopGooseFolder = ''
)

$ErrorActionPreference = 'Stop'
$Python = (Get-Command $Python).Source
$Destination = [IO.Path]::GetFullPath($Destination)
$BuildDirectory = [IO.Path]::GetFullPath($BuildDirectory)
New-Item -ItemType Directory -Force -Path $BuildDirectory,$Destination | Out-Null
if (Test-Path -LiteralPath (Join-Path $Destination 'EveTech')) {
    throw 'EveTech already exists in the destination. Choose a fresh destination for the new build.'
}
if ($DesktopGooseFolder) {
    & (Join-Path $PSScriptRoot 'desktop_goose\build.ps1') -DesktopGooseFolder $DesktopGooseFolder
}
$icon = Join-Path $BuildDirectory 'EveTech.ico'
& $Python (Join-Path $PSScriptRoot 'tools\build_icon.py') $icon
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }

$versionFile = Join-Path $BuildDirectory 'version_info.txt'
& $Python (Join-Path $PSScriptRoot 'tools\make_version_info.py') $versionFile
if ($LASTEXITCODE -ne 0) { throw 'Version info generation failed.' }

& $Python -m PyInstaller --noconfirm --onedir --windowed --noupx --name EveTech --icon $icon --version-file $versionFile `
    --paths $PSScriptRoot --distpath $Destination --workpath $BuildDirectory --specpath $BuildDirectory `
    --hidden-import comtypes.client.dynamic `
    --hidden-import pythoncom --hidden-import win32com.client --hidden-import win32timezone `
    (Join-Path $PSScriptRoot 'main.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
$output = Join-Path $Destination 'EveTech'
$gooseHelper = Join-Path $PSScriptRoot 'desktop_goose\dist\EveTechGoose.exe'
if (Test-Path -LiteralPath $gooseHelper -PathType Leaf) {
    $toolDirectory = Join-Path $output 'tools'
    New-Item -ItemType Directory -Force -Path $toolDirectory | Out-Null
    Copy-Item -LiteralPath $gooseHelper -Destination $toolDirectory
    $gooseMod = Join-Path $PSScriptRoot 'desktop_goose\dist\EveTechGooseMod.dll'
    if (Test-Path -LiteralPath $gooseMod -PathType Leaf) {
        Copy-Item -LiteralPath $gooseMod -Destination $toolDirectory
    }
}
if ($FFprobeBundle) {
    $FFprobeBundle = [IO.Path]::GetFullPath($FFprobeBundle)
    $ffprobe = Get-ChildItem -LiteralPath $FFprobeBundle -Recurse -File -Filter 'ffprobe.exe' | Select-Object -First 1
    if (-not $ffprobe) { throw 'FFprobeBundle does not contain ffprobe.exe.' }
    $toolDirectory = Join-Path $output 'tools'
    New-Item -ItemType Directory -Force -Path $toolDirectory | Out-Null
    Copy-Item -LiteralPath $ffprobe.FullName -Destination $toolDirectory
    Get-ChildItem -LiteralPath $ffprobe.DirectoryName -File -Filter '*.dll' | Copy-Item -Destination $toolDirectory
    $license = Get-ChildItem -LiteralPath $FFprobeBundle -Recurse -File -Filter 'LICENSE.txt' | Select-Object -First 1
    if ($license) {
        Copy-Item -LiteralPath $license.FullName -Destination (Join-Path $toolDirectory 'FFmpeg-LICENSE.txt')
    }
    else {
        Write-Warning ('No LICENSE.txt found under {0}. Copy the FFmpeg licence text to tools\FFmpeg-LICENSE.txt before distributing this build.' -f $FFprobeBundle)
    }
}

& $Python (Join-Path $PSScriptRoot 'tools\write_package_meta.py') $output
if ($LASTEXITCODE -ne 0) { throw 'Package metadata failed.' }
& $Python -m PyInstaller --noconfirm --onefile --console --noupx --name EveTechGooseSetup --icon $icon `
    --version-file $versionFile --paths $PSScriptRoot --distpath $Destination --workpath $BuildDirectory `
    --specpath $BuildDirectory (Join-Path $PSScriptRoot 'goose_setup.py')
if ($LASTEXITCODE -ne 0) { throw 'Desktop Goose downloader build failed.' }
Write-Output (Join-Path $Destination 'EveTech\EveTech.exe')
Write-Output (Join-Path $Destination 'EveTechGooseSetup.exe')
