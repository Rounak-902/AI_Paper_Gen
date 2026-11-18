# Shared loader for verification scripts that consume infra/docker-compose.yml.
# Credential generation is owned by verify-p1-t10.ps1; this helper only reads the
# ignored file and exports the exact values Compose, the backend, and the older
# harnesses must share. REDIS_PASSWORD in infra/.env is the one local source of
# truth; client URLs are derived from it rather than independently maintained.

function Import-PaperGenLocalInfraEnv {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$EnvFile,
        [hashtable]$Overrides = @{}
    )

    if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        throw "Local infrastructure credentials are missing at $EnvFile. Run scripts/verify-p1-t10.ps1 once or copy infra/.env.example to infra/.env and replace every placeholder."
    }

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line -match '^\s*#') {
            continue
        }
        if ($line -notmatch '^\s*([^=\s]+)=(.*)$') {
            throw "Invalid entry in $EnvFile; expected NAME=value."
        }
        $values[$Matches[1]] = $Matches[2].Trim()
    }

    foreach ($name in $Overrides.Keys) {
        if (-not [string]::IsNullOrWhiteSpace([string]$Overrides[$name])) {
            $values[$name] = [string]$Overrides[$name]
        }
    }

    $secretNames = @(
        'POSTGRES_PASSWORD',
        'PAPERGEN_APP_DB_PASSWORD',
        'REDIS_PASSWORD',
        'MINIO_ROOT_PASSWORD'
    )
    foreach ($name in $secretNames) {
        if (-not $values.ContainsKey($name)) {
            throw "$EnvFile is missing $name."
        }
        if ($values[$name] -notmatch '^[A-Za-z0-9_-]{24,}$') {
            throw "$name must be a URL-safe random value of at least 24 characters."
        }
        if ($values[$name] -match 'replace_with|test_secure|password') {
            throw "$name contains a placeholder or retired fixture value."
        }
    }
    if (-not $values.ContainsKey('MINIO_ROOT_USER') -or
        $values['MINIO_ROOT_USER'] -notmatch '^[A-Za-z0-9_-]{3,}$') {
        throw "$EnvFile is missing a valid MINIO_ROOT_USER."
    }
    if (@($secretNames | ForEach-Object { $values[$_] } | Select-Object -Unique).Count -ne 4) {
        throw 'Postgres, app_user, Redis, and MinIO must use distinct local secrets.'
    }

    foreach ($name in $secretNames + @('MINIO_ROOT_USER')) {
        [Environment]::SetEnvironmentVariable($name, [string]$values[$name], 'Process')
    }

    $redisPort = if ($values.ContainsKey('REDIS_PORT')) {
        [string]$values['REDIS_PORT']
    }
    else {
        '6379'
    }
    $encodedRedisPassword = [Uri]::EscapeDataString([string]$values['REDIS_PASSWORD'])
    [Environment]::SetEnvironmentVariable(
        'REDIS_URL',
        "redis://:${encodedRedisPassword}@127.0.0.1:${redisPort}/0",
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'QUEUE_REDIS_URL',
        "redis://:${encodedRedisPassword}@127.0.0.1:${redisPort}/1",
        'Process'
    )

    return $values
}

function Sync-PaperGenBackendRedisEnv {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$EnvFile,
        [Parameter(Mandatory = $true)][string]$BackendEnvFile
    )

    $null = Import-PaperGenLocalInfraEnv -EnvFile $EnvFile
    if (-not (Test-Path -LiteralPath $BackendEnvFile -PathType Leaf)) {
        throw "Backend environment file is missing at $BackendEnvFile. Copy .env.example to backend/.env and configure the required non-Redis values first."
    }

    $replacements = @{
        'REDIS_URL' = [Environment]::GetEnvironmentVariable('REDIS_URL', 'Process')
        'QUEUE_REDIS_URL' = [Environment]::GetEnvironmentVariable('QUEUE_REDIS_URL', 'Process')
    }
    $seen = @{}
    $updated = foreach ($line in Get-Content -LiteralPath $BackendEnvFile) {
        if ($line -match '^([^=]+)=') {
            $name = $Matches[1]
            if ($replacements.ContainsKey($name)) {
                if (-not $seen.ContainsKey($name)) {
                    $seen[$name] = $true
                    "$name=$($replacements[$name])"
                }
                continue
            }
        }
        $line
    }
    foreach ($name in @('REDIS_URL', 'QUEUE_REDIS_URL')) {
        if (-not $seen.ContainsKey($name)) {
            $updated += "$name=$($replacements[$name])"
        }
    }

    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllLines(
        (Resolve-Path -LiteralPath $BackendEnvFile).Path,
        [string[]]$updated,
        $encoding
    )
    Write-Host "Synchronized authenticated Redis URLs in $BackendEnvFile from $EnvFile."
}
