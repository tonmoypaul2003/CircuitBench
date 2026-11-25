$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$work = Join-Path $project '.build'
$runtime = Join-Path $project 'runtime'
$exe = Join-Path $runtime 'ngspice\bin\ngspice_con.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw 'Missing runtime\ngspice\bin\ngspice_con.exe.'
}
New-Item -ItemType Directory -Path $work -Force | Out-Null
$oldCache = $env:PYINSTALLER_CONFIG_DIR
$env:PYINSTALLER_CONFIG_DIR = Join-Path $work 'cache'
try {
    $arguments = @(
        '-m', 'PyInstaller', '--name', 'CircuitBench', '--onefile', '--windowed',
        '--noconfirm', '--noupx', '--distpath', $project, '--workpath', (Join-Path $work 'work'),
        '--specpath', $work, '--icon', (Join-Path $runtime 'CircuitBench.ico'),
        '--add-data', "$runtime;runtime",
        '--add-data', "$(Join-Path $project 'LICENSE');.",
        '--add-data', "$(Join-Path $project 'README.md');.",
        (Join-Path $project 'app.py')
    )
    & python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
} finally {
    $env:PYINSTALLER_CONFIG_DIR = $oldCache
}
