# Use the workspace-local verification dependencies when available.
$workspace = Split-Path -Parent $PSScriptRoot
$qt = Join-Path $workspace '.pyqt-test-deps'
$previousPythonPath = $env:PYTHONPATH
try {
    if (Test-Path -LiteralPath $qt) {
        $env:PYTHONPATH = if ($previousPythonPath) { "$qt;$previousPythonPath" } else { $qt }
    }
    python (Join-Path $PSScriptRoot 'app.py')
    $appExitCode = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
exit $appExitCode
