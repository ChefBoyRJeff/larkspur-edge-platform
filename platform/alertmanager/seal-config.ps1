<#
.SYNOPSIS
Seals the Alertmanager configuration, with the Gmail address, app password and healthchecks.io
ping URL filled in, into
platform/config/monitoring/alertmanager-config.sealedsecret.yaml. Run it from any folder;
commit the sealed file it writes. The filled-in configuration never touches the repository.
See docs/runbooks/alerts.md.
#>
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$template = Join-Path $PSScriptRoot 'alertmanager.yaml.tmpl'
$target = Join-Path $repo 'platform/config/monitoring/alertmanager-config.sealedsecret.yaml'
$kubeseal = (Get-Command kubeseal -ErrorAction SilentlyContinue).Source
if (-not $kubeseal) { $kubeseal = Join-Path $env:LOCALAPPDATA 'Programs\kubeseal\kubeseal.exe' }
if (-not (Test-Path $kubeseal)) { throw 'kubeseal was not found (see docs/runbooks/alerts.md)' }
$controller = @('--controller-name', 'sealed-secrets-controller', '--controller-namespace', 'kube-system')

$address = (Read-Host 'Gmail address that sends and receives the alerts').Trim()
if ($address -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { throw 'that does not look like an email address; nothing sealed' }
# The clipboard is read only after this pause, so pasting the command that started this script
# cannot be mistaken for the password.
Read-Host 'Now copy the Gmail app password, then press Enter' | Out-Null
$password = (Get-Clipboard -Raw) -replace '\s', ''
Set-Clipboard -Value ' '
if ($password -notmatch '^[a-z]{16}$') { throw "the clipboard does not hold a Gmail app password (16 letters); it held $($password.Length) characters. Nothing sealed." }
Read-Host 'Now copy the healthchecks.io ping URL, then press Enter' | Out-Null
$ping = (Get-Clipboard -Raw).Trim()
Set-Clipboard -Value ' '
if ($ping -notmatch '^https://hc-ping\.com/[0-9a-f-]{36}$') { throw 'the clipboard does not hold a healthchecks.io ping URL (https://hc-ping.com/<uuid>); nothing sealed' }

$config = [System.IO.File]::ReadAllText($template).Replace('@@GMAIL_ADDRESS@@', $address).Replace('@@GMAIL_APP_PASSWORD@@', $password).Replace('@@HEALTHCHECKS_PING_URL@@', $ping)
if ($config -match '@@[A-Z_]+@@') { throw 'a placeholder was not filled in; nothing sealed' }
$plainFile = New-TemporaryFile
try {
    [System.IO.File]::WriteAllText($plainFile.FullName, $config)
    $plain = kubectl create secret generic alertmanager-config -n monitoring --from-file=alertmanager.yaml=$($plainFile.FullName) --dry-run=client -o json
    $decoded = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(($plain | ConvertFrom-Json).data.'alertmanager.yaml'))
    if (-not ($decoded.Contains($password) -and $decoded.Contains($ping) -and $decoded.Contains("to: `"$address`""))) { throw 'the secret does not hold the filled-in configuration; nothing sealed' }
    $plain | & $kubeseal --format yaml @controller | Out-File -Encoding utf8 $target
}
finally {
    Remove-Item $plainFile.FullName -Force
}
Get-Content $target | & $kubeseal --validate @controller
if ($LASTEXITCODE) { throw 'the cluster cannot decrypt the sealed file' }
"Sealed and validated: $target (app password: $($password.Length) letters)"
