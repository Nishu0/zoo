<#
Installs the Zoo control plane on Windows with Docker Desktop:

  powershell -ExecutionPolicy Bypass -c "& ([scriptblock]::Create((irm https://github.com/chann44/zoo/releases/latest/download/install.ps1))) -AdminEmail 'you@example.com'"

Docker Desktop has no KVM for Kata, so Linux sandboxes run as plain containers inside its VM. For Kata micro-VMs,
run install.sh in a WSL2 Ubuntu with nested virtualization (/dev/kvm), or on a Linux server. With -AdminEmail it
also adds this machine as a server for Windows sandboxes (install-node.ps1, as Servers > Add server > Windows would;
-NoNode skips that). Remote Linux, Mac and Windows servers are added from Servers > Add server.
#>
param(
    [string]$AdminEmail = '',
    [string]$Version = '',
    [string]$Dir = "$HOME\zoo",
    [string]$Registry = 'docker.io/chann44',
    [switch]$NoNode,
    [switch]$Check
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Repo = 'chann44/zoo'
$script:Failed = $false

function Say([string]$m) { Write-Host "==> $m" -ForegroundColor White }
function Ok([string]$m) { Write-Host '  ok    ' -ForegroundColor Green -NoNewline; Write-Host $m }
function Note([string]$m) { Write-Host '  note  ' -ForegroundColor Yellow -NoNewline; Write-Host $m }
function Fail([string]$m) { Write-Host '  fail  ' -ForegroundColor Red -NoNewline; Write-Host $m; $script:Failed = $true }
function RandomHex([int]$bytes) {
    $buffer = New-Object byte[] $bytes
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
    -join ($buffer | ForEach-Object { $_.ToString('x2') })
}

$elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($AdminEmail -and -not $NoNode -and -not $elevated) {
    throw 'run this in an elevated PowerShell (Run as administrator) to add this machine for Windows sandboxes, or pass -NoNode'
}

Say 'Checking this machine'
if (Get-Command docker -ErrorAction SilentlyContinue) { Ok 'Docker CLI' }
else { Fail 'install Docker Desktop (https://www.docker.com/products/docker-desktop/), start it, then run this again' }
docker info *> $null
if ($LASTEXITCODE -eq 0) { Ok 'Docker Desktop is running' } else { Fail 'start Docker Desktop, then run this again' }
docker compose version *> $null
if ($LASTEXITCODE -eq 0) { Ok 'Docker Compose' } else { Fail 'Docker Compose is missing; update Docker Desktop' }
$os = Get-CimInstance Win32_OperatingSystem
$memory = [math]::Floor($os.TotalVisibleMemorySize / 1MB)
if ($memory -lt 8) { Fail "$memory GB of memory, needs 8 GB" }
elseif ($memory -lt 16) { Note "$memory GB of memory; 16 GB or more recommended" }
else { Ok "$memory GB of memory" }
$drive = (Split-Path -Qualifier $Dir).TrimEnd(':')
$free = [math]::Floor((Get-PSDrive $drive).Free / 1GB)
if ($free -ge 20) { Ok "$free GB free on ${drive}:" } else { Fail "$free GB free on ${drive}:, needs 20 GB" }
if (Test-Path "$Dir\.env") { Fail "$Dir\.env already exists; use docker compose in $Dir to upgrade" }
if ($script:Failed) { throw 'fix the failed checks above, then run this again' }
if ($Check) { Say 'All checks passed'; return }

if (-not $Version) {
    $Version = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest").tag_name -replace '^v', ''
}
if ($Version -notmatch '^\d+\.\d+\.\d+([.-][A-Za-z0-9.-]+)?$') { throw "invalid Zoo release version '$Version'" }

Say "Downloading Zoo $Version"
New-Item -ItemType Directory -Force $Dir | Out-Null
$release = "https://github.com/$Repo/releases/download/v$Version"
Invoke-WebRequest -UseBasicParsing "$release/compose.yml" -OutFile "$Dir\compose.yml"
$example = (Invoke-WebRequest -UseBasicParsing "$release/env.example").Content -split '\r?\n'

# the LAN address, so remote servers and this machine's VMs can reach the API
$address = (Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } |
    Select-Object -First 1).IPv4Address.IPAddress
if (-not $address) { $address = '127.0.0.1' }
$postgres = RandomHex 24
$values = [ordered]@{
    JWT_SECRET = RandomHex 48; ZOO_SECRETS_KEY = RandomHex 48; POSTGRES_PASSWORD = $postgres
    DATABASE_URL = "postgresql://zoo:$postgres@postgres:5432/zoo"
    AWS_ACCESS_KEY_ID = RandomHex 12; AWS_SECRET_ACCESS_KEY = RandomHex 32
    ZOO_VERSION = $Version; ZOO_REGISTRY = $Registry; ZOO_RUNTIME = 'docker'; ZOO_OBJECT_STORE = 's3://zoo'
    ADMIN_EMAILS = $AdminEmail; ZOO_DOMAIN = ''; COMPOSE_PROFILES = ''
    ZOO_API_URL = "http://${address}:8000"; CORS_ORIGINS = "http://${address}:3000,http://localhost:3000,http://127.0.0.1:3000"; ZOO_PUBLIC_IP = $address
    ZOO_GATEWAY_PEERS = 'gateway'; ZOO_S3_ENDPOINT = 'http://seaweedfs:8333'
}
$seen = @{}
$lines = foreach ($line in $example) {
    if ($line -match '^([A-Z][A-Z0-9_]*)=' -and $values.Contains($Matches[1])) { $seen[$Matches[1]] = $true; "$($Matches[1])=$($values[$Matches[1]])" }
    else { $line }
}
$lines += foreach ($key in $values.Keys) { if (-not $seen[$key]) { "$key=$($values[$key])" } }
[IO.File]::WriteAllLines("$Dir\.env", [string[]]$lines)

# no domain: Caddy serves the API and the guest connection on :8000, as install.sh does
[IO.File]::WriteAllText("$Dir\Caddyfile", @'
:80 {
    handle /guest/connect {
        reverse_proxy gateway:8001
    }
    handle {
        reverse_proxy api:8000
    }
}
'@)
[IO.File]::WriteAllText("$Dir\compose.override.yml", @'
services:
  api:
    ports: !reset []
  caddy:
    profiles: !reset []
    ports: !override ["8000:80"]
    depends_on:
      - gateway
'@)

Say "Starting Zoo $Version in Docker Desktop"
$compose = @('compose', '--project-directory', $Dir, '--env-file', "$Dir\.env", '-f', "$Dir\compose.yml", '-f', "$Dir\compose.override.yml", '-p', 'zoo')
docker @compose pull
if ($LASTEXITCODE -ne 0) { throw 'docker compose pull failed' }
docker @compose up -d --no-build --pull never --wait --wait-timeout 600
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed; see: docker compose -p zoo logs' }

Say "Zoo $Version is running (Linux sandboxes as plain containers: Docker Desktop has no KVM)"
Write-Host @"

  Dashboard:     http://${address}:3000
  Configuration: $Dir\.env
  Manage it:     cd $Dir; docker compose -p zoo ps

"@
if (-not $AdminEmail -or $NoNode) {
    Note 'this machine is not added for Windows sandboxes; sign in, then use Servers > Add server > Windows'
    return
}

# join this machine for Windows sandboxes, as Servers > Add server > Windows would (server/bootstrap.py)
$output = docker @compose exec -T api uv run --no-sync python -m apps.server.bootstrap --email ($AdminEmail.Split(',')[0].Trim()) --name $env:COMPUTERNAME
if ($LASTEXITCODE -ne 0) { throw "couldn't get a join token from Zoo; add this machine from Servers > Add server > Windows" }
$joined = @($output)[-1] | ConvertFrom-Json
if ($joined.password) { Say "Admin account: $AdminEmail with password $($joined.password) (shown once; change it after signing in)" }
else { Say "Admin account: $AdminEmail already exists; sign in with its password" }

Say 'Adding this machine for Windows sandboxes'
$node = Invoke-RestMethod "https://github.com/$Repo/releases/download/v$Version/install-node.ps1"
& ([scriptblock]::Create($node)) -Key $joined.key -ControlPlane $joined.control_plane -Token $joined.token -Release "https://github.com/$Repo/releases/download/v$Version"
