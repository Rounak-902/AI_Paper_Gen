<#
.SYNOPSIS
    Boots the PaperGen FastAPI app, captures the real startup output, probes
    /health, then shuts the server down and prints a redacted transcript.

.DESCRIPTION
    Run this from anywhere; it forces its own directory (backend/) as the working
    directory, which matters because Settings.model_config uses env_file=".env",
    and pydantic-settings resolves that path relative to the CURRENT WORKING
    DIRECTORY. Launching from the repo root would read the root .env instead and
    report required fields as missing.

    All output is passed through a redactor before display, because a pydantic
    ValidationError includes the offending input_value -- which for DATABASE_URL
    would expose your Neon password. Paste the output of this script freely.

.PARAMETER Port
    Port to bind. Default 8000.

.PARAMETER Reload
    Add --reload (uvicorn's autoreloader). Off by default: the reloader forks a
    child process, which muddies output capture and clean shutdown. Boot
    validity is identical either way, since Settings() is constructed at import.

.PARAMETER KillExisting
    If something is already listening on -Port, kill it instead of aborting.

.EXAMPLE
    .\verify_boot.ps1
    .\verify_boot.ps1 -Port 8001 -Reload
    .\verify_boot.ps1 -KillExisting
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$Reload,
    [switch]$KillExisting,
    [int]$TimeoutSeconds = 45
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$envFile = Join-Path $PSScriptRoot '.env'

function Write-Section($t) {
    Write-Host ''
    Write-Host ('=' * 78) -ForegroundColor Cyan
    Write-Host $t -ForegroundColor Cyan
    Write-Host ('=' * 78) -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
# Build the redactor from the real .env values
# ---------------------------------------------------------------------------
$secretKeys = @(
    'DATABASE_URL', 'JWT_SECRET', 'JWT_REFRESH_SECRET', 'GEMINI_API_KEY',
    'REDIS_URL', 'QUEUE_REDIS_URL', 'STORAGE_ACCESS_KEY', 'STORAGE_SECRET_KEY',
    'MATHPIX_APP_ID', 'MATHPIX_APP_KEY', 'SENTRY_DSN'
)
$secretValues = @()
$envMap = @{}
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $k = $line.Substring(0, $line.IndexOf('=')).Trim()
        $v = $line.Substring($line.IndexOf('=') + 1).Trim()
        if ($k) { $envMap[$k] = $v }
        if ($secretKeys -contains $k -and $v.Length -ge 8) { $secretValues += $v }
    }
}

function Protect-Output([string]$text) {
    if ([string]::IsNullOrEmpty($text)) { return $text }
    # 1. Exact-match any known secret value from .env
    foreach ($v in ($secretValues | Sort-Object -Property Length -Descending)) {
        $text = $text.Replace($v, '<REDACTED>')
    }
    # 2. Catch truncated/reformatted DSNs that exact matching would miss
    $text = [regex]::Replace($text,
        'postgres(?:ql)?(?:\+\w+)?://[^\s''"<>]+',
        'postgresql+asyncpg://<REDACTED-DSN>')
    $text = [regex]::Replace($text, 'redis://[^\s''"<>]+', 'redis://<REDACTED>')
    return $text
}

# ---------------------------------------------------------------------------
# Process helpers
#
# Do NOT rely on the PID returned by Start-Process. The venv's
# Scripts\python.exe hands off and exits, so that PID is a launcher that dies
# immediately while uvicorn keeps running under a different PID -- which is how
# an earlier version of this script leaked a server holding the port. Instead
# ask the OS which process owns the listening socket, and cross-check against
# the PID uvicorn prints in its own log.
# ---------------------------------------------------------------------------
function Get-ListenerPids([int]$p) {
    $pids = @()
    try {
        $pids = @(Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction Stop |
                  Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        # Fallback for hosts without the NetTCPIP module
        $pids = @(netstat -ano 2>$null |
            Select-String -Pattern "TCP\s+\S+:$p\s+\S+\s+LISTENING\s+(\d+)" |
            ForEach-Object { [int]$_.Matches[0].Groups[1].Value } |
            Sort-Object -Unique)
    }
    return @($pids | Where-Object { $_ -and $_ -ne 0 })
}

function Stop-Tree([int]$targetPid) {
    if (-not $targetPid) { return }
    try {
        $kids = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$targetPid" -ErrorAction SilentlyContinue)
        foreach ($k in $kids) { Stop-Tree ([int]$k.ProcessId) }
    } catch { }
    try { Stop-Process -Id $targetPid -Force -ErrorAction Stop } catch { }
}

function Get-ProcName([int]$p) {
    try { return (Get-Process -Id $p -ErrorAction Stop).ProcessName } catch { return '<gone>' }
}

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
Write-Section 'ENVIRONMENT'
Write-Host "working directory : $(Get-Location)"
Write-Host "python            : $py"
if (-not (Test-Path -LiteralPath $py)) {
    Write-Host "FATAL: venv python not found at $py" -ForegroundColor Red
    exit 2
}
& $py --version
Write-Host "uvicorn           : $(& $py -c 'import uvicorn; print(uvicorn.__version__)')"
Write-Host ".env present      : $(Test-Path -LiteralPath $envFile)"

Write-Section 'PRE-FLIGHT .env VALIDATION (dependency-free, rules read from config.py)'
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'preflight_env_check.py')) {
    $pf = & $py 'preflight_env_check.py' 2>&1 | Out-String
    Write-Host (Protect-Output $pf)
} else {
    Write-Host 'preflight_env_check.py not found - skipping' -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Port availability
# ---------------------------------------------------------------------------
$existing = Get-ListenerPids $Port
if ($existing.Count -gt 0) {
    Write-Host ''
    foreach ($p in $existing) {
        Write-Host "port $Port is already held by PID $p ($(Get-ProcName $p))" -ForegroundColor Yellow
    }
    if ($KillExisting) {
        foreach ($p in $existing) { Stop-Tree $p }
        Start-Sleep -Milliseconds 800
        if ((Get-ListenerPids $Port).Count -gt 0) {
            Write-Host "FATAL: could not free port $Port." -ForegroundColor Red
            exit 2
        }
        Write-Host "freed port $Port" -ForegroundColor Green
    } else {
        Write-Host "FATAL: re-run with -KillExisting to reclaim it, or use -Port 8001." -ForegroundColor Red
        exit 2
    }
}

# ---------------------------------------------------------------------------
# Boot uvicorn
# ---------------------------------------------------------------------------
Write-Section 'BOOTING UVICORN'
$outFile = Join-Path $env:TEMP "papergen_boot_out_$PID.log"
$errFile = Join-Path $env:TEMP "papergen_boot_err_$PID.log"
foreach ($f in @($outFile, $errFile)) { if (Test-Path $f) { Remove-Item $f -Force } }

$uvArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port")
if ($Reload) { $uvArgs += '--reload' }
Write-Host "command: `"$py`" $($uvArgs -join ' ')"
Write-Host ''

$env:PYTHONUNBUFFERED = '1'
$proc = Start-Process -FilePath $py -ArgumentList $uvArgs `
    -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru `
    -RedirectStandardOutput $outFile -RedirectStandardError $errFile

# Poll for readiness or early death
$healthJson = $null
$healthStatus = $null
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$startedOk = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 700
    if ($proc.HasExited) { break }
    $sofar = ''
    if (Test-Path $errFile) { $sofar += (Get-Content $errFile -Raw -ErrorAction SilentlyContinue) }
    if (Test-Path $outFile) { $sofar += (Get-Content $outFile -Raw -ErrorAction SilentlyContinue) }
    if ($sofar -match 'Application startup complete') {
        $startedOk = $true
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 10
            $healthStatus = $r.StatusCode
            $healthJson = $r.Content
        } catch {
            $healthStatus = "request failed: $($_.Exception.Message)"
        }
        break
    }
}

Start-Sleep -Milliseconds 400
$stdout = if (Test-Path $outFile) { Get-Content $outFile -Raw } else { '' }
$stderr = if (Test-Path $errFile) { Get-Content $errFile -Raw } else { '' }
$combined = "$stderr`n$stdout"

# uvicorn prints its own PID; that is authoritative, unlike $proc.Id
$uvicornPid = $null
$m = [regex]::Match($combined, 'Started server process \[(\d+)\]')
if ($m.Success) { $uvicornPid = [int]$m.Groups[1].Value }

Write-Section 'VERBATIM STDERR (uvicorn logs here by default)'
if ([string]::IsNullOrWhiteSpace($stderr)) { Write-Host '<empty>' } else { Write-Host (Protect-Output $stderr) }

Write-Section 'VERBATIM STDOUT'
if ([string]::IsNullOrWhiteSpace($stdout)) { Write-Host '<empty>' } else { Write-Host (Protect-Output $stdout) }

# ---------------------------------------------------------------------------
# Shut down
# ---------------------------------------------------------------------------
Write-Section 'SHUTTING DOWN'

# Kill by three independent identities, because any one of them can be wrong:
#   1. $proc.Id            - the process Start-Process launched (may be a launcher
#                            that already exited, in which case this is a no-op)
#   2. $uvicornPid         - the PID uvicorn printed for itself
#   3. Get-ListenerPids    - whoever the OS says actually owns the socket
# Stop-Tree walks children first, so a --reload parent takes its worker with it.
$targets = @()
try { if (-not $proc.HasExited) { $targets += [int]$proc.Id } } catch { $targets += [int]$proc.Id }
if ($uvicornPid) { $targets += $uvicornPid }
$targets += (Get-ListenerPids $Port)
$targets = @($targets | Where-Object { $_ } | Sort-Object -Unique)

if ($targets.Count -eq 0) {
    Write-Host 'nothing left to kill (process already gone, port not listening)'
} else {
    foreach ($t in $targets) {
        $n = Get-ProcName $t
        if ($n -eq '<gone>') {
            Write-Host "PID $t already exited"
        } else {
            Write-Host "killing PID $t ($n) and its children"
            Stop-Tree $t
        }
    }
}

# Verify the port was actually released instead of assuming the kill worked.
$portFreed = $false
for ($i = 0; $i -lt 20; $i++) {
    if ((Get-ListenerPids $Port).Count -eq 0) { $portFreed = $true; break }
    Start-Sleep -Milliseconds 250
}
if ($portFreed) {
    Write-Host "port $Port released" -ForegroundColor Green
} else {
    $stuck = Get-ListenerPids $Port
    Write-Host "WARNING: port $Port is STILL held by PID(s) $($stuck -join ', '). Kill manually:" -ForegroundColor Yellow
    Write-Host "  Get-NetTCPConnection -LocalPort $Port -State Listen | ForEach-Object { Stop-Process -Id `$_.OwningProcess -Force }" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
Write-Section 'VERDICT'
$hasTraceback = $combined -match 'Traceback \(most recent call last\)'
$hasValidationError = $combined -match 'ValidationError'

# $proc.ExitCode is null when the launcher handed off rather than exiting with a
# status, so report that case explicitly instead of printing a blank.
$exitCodeText = if (-not $proc.HasExited) {
    'n/a (still running when we killed it)'
} elseif ($null -eq $proc.ExitCode) {
    'null (launcher process handed off; see uvicorn PID below)'
} else {
    "$($proc.ExitCode)"
}

Write-Host ("startup complete seen : {0}" -f $startedOk)
Write-Host ("traceback present     : {0}" -f $hasTraceback)
Write-Host ("ValidationError       : {0}" -f $hasValidationError)
Write-Host ("launched PID          : {0}" -f $proc.Id)
Write-Host ("uvicorn self-reported : {0}" -f $(if ($uvicornPid) { $uvicornPid } else { 'not found in log' }))
Write-Host ("launcher exit code    : {0}" -f $exitCodeText)
Write-Host ("port {0} released     : {1}" -f $Port, $portFreed)
Write-Host ("/health status        : {0}" -f $(if ($null -ne $healthStatus) { $healthStatus } else { 'not probed' }))
if ($healthJson) { Write-Host ("/health body          : {0}" -f (Protect-Output $healthJson)) }

Write-Host ''
if ($startedOk -and -not $hasTraceback -and $healthStatus -eq 200) {
    Write-Host 'PASS - app booted cleanly and /health returned 200.' -ForegroundColor Green
    $exit = 0
    if (-not $portFreed) {
        Write-Host "      (but a server is still holding port $Port - see warning above)" -ForegroundColor Yellow
    }
} elseif ($hasValidationError) {
    Write-Host 'FAIL - Settings validation rejected the configuration (see stderr above).' -ForegroundColor Red
    $exit = 1
} elseif ($hasTraceback) {
    Write-Host 'FAIL - app raised an exception during import/startup (see stderr above).' -ForegroundColor Red
    $exit = 1
} else {
    Write-Host "INCONCLUSIVE - no 'Application startup complete' within ${TimeoutSeconds}s and no traceback." -ForegroundColor Yellow
    $exit = 3
}

foreach ($f in @($outFile, $errFile)) { if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue } }
Write-Host ''
Write-Host "(all output above passed through a redactor; safe to paste)" -ForegroundColor DarkGray
exit $exit
