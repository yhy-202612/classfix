param([Parameter(Mandatory=$true)][string]$DesktopGooseFolder)

$ErrorActionPreference = 'Stop'
$gooseFolder = (Resolve-Path $DesktopGooseFolder).Path
$gooseApi = Join-Path $gooseFolder 'GooseModdingAPI.dll'
if ((Get-FileHash -LiteralPath $gooseApi -Algorithm SHA256).Hash -ne 'EFA31DF7AB1394092395365805F913DD023CDCD21796603F133641524FB9AD79') {
    throw 'Choose the original Desktop Goose v0.31 directory.'
}

$framework = [Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()
$csc = Join-Path $framework 'csc.exe'
$wpf = Join-Path $framework 'WPF'
$dist = Join-Path $PSScriptRoot 'dist'
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$refs = @('System.dll', 'System.Core.dll', 'System.Drawing.dll', 'System.Windows.Forms.dll', 'System.Net.Http.dll', 'System.Web.Extensions.dll', 'System.Security.dll')
$refs = @($refs | ForEach-Object { '/r:' + (Join-Path $framework $_) })
$refs += @('UIAutomationClient.dll', 'UIAutomationTypes.dll', 'WindowsBase.dll') | ForEach-Object { '/r:' + (Join-Path $wpf $_) }
$refs += '/r:' + $gooseApi
$source = @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'src') -Filter '*.cs' -File | ForEach-Object { $_.FullName })
$app = Join-Path $dist 'ClassroomGoose.exe'
$smoke = Join-Path $dist 'Smoke.exe'
& $csc /nologo /langversion:5 /optimize+ /target:winexe "/out:$app" @refs @source
if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
& $csc /nologo /langversion:5 /optimize+ /target:exe /main:ClassroomGoose.Smoke "/out:$smoke" @refs @source (Join-Path $PSScriptRoot 'tests\Smoke.cs')
if ($LASTEXITCODE -ne 0) { throw 'Smoke test build failed.' }
