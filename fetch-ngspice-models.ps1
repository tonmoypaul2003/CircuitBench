param([string] $ArchivePath)

$ErrorActionPreference = 'Stop'
$library = Join-Path $PSScriptRoot 'runtime\models'
$cache = Join-Path $PSScriptRoot '.build\model-downloads'
$expected = 'D0F78C38C3A396BBA47F76A9B7637CF6325A993F9925472D8712FCA3F33E9608'
New-Item -ItemType Directory -Path $library -Force | Out-Null

if (-not $ArchivePath) {
    New-Item -ItemType Directory -Path $cache -Force | Out-Null
    $ArchivePath = Join-Path $cache 'basic_models.7z'
    if (-not (Test-Path -LiteralPath $ArchivePath)) {
        $download = Join-Path $cache "basic_models-$([guid]::NewGuid()).download"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri 'https://ngspice.sourceforge.io/model-parameters/basic_models.7z' -OutFile $download
            if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne $expected) {
                throw 'The upstream model archive has changed. Review the new archive before importing files.'
            }
            Move-Item -LiteralPath $download -Destination $ArchivePath
        } finally {
            if (Test-Path -LiteralPath $download) {
                Remove-Item -LiteralPath $download
            }
        }
    }
}

$archive = (Resolve-Path -LiteralPath $ArchivePath).Path
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
    throw 'Unexpected SHA-256 for the ngspice starter archive.'
}
$files = @(
    'basic_models/bipolar/BC546.lib',
    'basic_models/bipolar/BC556.lib',
    'basic_models/JFet/2SJ74.mod',
    'basic_models/JFet/2SK170.mod',
    'basic_models/MOS power/IRFP240_NVDMOS.mod',
    'basic_models/MOS power/IRFP9240_PVDMOS.mod',
    'basic_models/OpAmps/generic_opamp.lib',
    'basic_models/OpAmps/Op07.mod'
)
$missing = @($files | Where-Object { -not (Test-Path -LiteralPath (Join-Path $library $_) -PathType Leaf) })
if ($missing.Count) {
    & tar.exe -xkf $archive -C $library @missing
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not extract the starter models. Existing files are preserved.'
    }
}
Write-Output "Starter models are available in $library"
