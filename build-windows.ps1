$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$work = Join-Path $project '.build'
$versionLine = Select-String -LiteralPath (Join-Path $project 'app.py') -Pattern '^VERSION = "([0-9A-Za-z.-]+)"$'
if (-not $versionLine) { throw 'Desktop version missing from app.py' }
$version = $versionLine.Matches[0].Groups[1].Value
$distribution = Join-Path (Join-Path $project 'build') $version
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
        '--noconfirm', '--noupx', '--distpath', $distribution, '--workpath', (Join-Path $work 'work'),
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
    $built = Join-Path $distribution 'CircuitBench.exe'
    $destination = Join-Path $project 'CircuitBench.exe'
    $staged = Join-Path $work ("install-" + [guid]::NewGuid() + ".exe")
    try {
        Copy-Item -LiteralPath $built -Destination $staged
        if (Test-Path -LiteralPath $destination) {
            $previous = Join-Path $work ("previous-" + [guid]::NewGuid() + ".exe")
            [IO.File]::Replace($staged, $destination, $previous)
            Remove-Item -LiteralPath $previous -ErrorAction SilentlyContinue
        } else {
            [IO.File]::Move($staged, $destination)
        }
        if ((Get-FileHash -LiteralPath $built).Hash -ne (Get-FileHash -LiteralPath $destination).Hash) {
            throw 'Versioned and root executables differ'
        }
    } finally {
        if (Test-Path -LiteralPath $staged) { Remove-Item -LiteralPath $staged }
    }
} finally {
    $env:PYINSTALLER_CONFIG_DIR = $oldCache
}
