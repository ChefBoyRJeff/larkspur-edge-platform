<#
.SYNOPSIS
Adds sample test users and a warehouse shipment history to one environment of Larkspur Track.
The users come from <repo>\test-users-<environment>.local.json (gitignored by *.local.*). If that
file already exists it is used as it is; otherwise the script makes it, with random fictional
names and random 24-character passwords. The file is piped into larkspur-track-admin seed inside
the web pod; no password is printed, passed on a command line or sent anywhere else. Save the
passwords in Bitwarden, then delete the file.
Runs once per environment: the app refuses a second run. See docs/runbooks/user-administration.md.
#>
param(
    [Parameter(Mandatory)][ValidateSet('dev', 'prod')][string]$Environment
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$file = Join-Path $repo "test-users-$Environment.local.json"
$plan = @{
    dev  = @{ Days = 60; Shipments = 400; Roles = @('admin', 'supervisor', 'supervisor', 'clerk', 'clerk', 'clerk') }
    prod = @{ Days = 30; Shipments = 200; Roles = @('admin', 'supervisor', 'clerk', 'clerk') }
}[$Environment]

$made = -not (Test-Path $file)
if ($made) {
    $first = 'Avery', 'Blake', 'Casey', 'Dana', 'Elliot', 'Frankie', 'Harper', 'Jordan', 'Kendall', 'Logan', 'Morgan', 'Parker', 'Quinn', 'Reese', 'Rowan', 'Skyler'
    $last = 'Ashby', 'Barlow', 'Calloway', 'Dunmore', 'Ellison', 'Fairbanks', 'Garrison', 'Holloway', 'Kingsley', 'Langford', 'Merriweather', 'Northcutt', 'Pemberton', 'Radcliffe', 'Sinclair', 'Thornton'
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    function Get-RandomIndex([int]$Count) {
        # Rejection sampling, so every index is equally likely.
        $limit = 256 - (256 % $Count)
        $byte = [byte[]]::new(1)
        do { $rng.GetBytes($byte) } while ($byte[0] -ge $limit)
        return $byte[0] % $Count
    }
    $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_.!#%'
    $people = foreach ($role in $plan.Roles) {
        do {
            $given = $first[(Get-RandomIndex $first.Count)]; $family = $last[(Get-RandomIndex $last.Count)]
            $username = "$given.$family".ToLower()
        } while ($username -in $taken)
        $taken = @($taken) + $username
        $password = -join (1..24 | ForEach-Object { $alphabet[(Get-RandomIndex $alphabet.Length)] })
        [ordered]@{ username = $username; display_name = "$given $family"; role = $role; password = $password }
    }
    $rng.Dispose()
    [IO.File]::WriteAllText($file, (ConvertTo-Json @($people) -Depth 2))
}
$users = Get-Content -Raw $file | ConvertFrom-Json
$count = @($users).Count
"$(if ($made) { 'Wrote' } else { 'Using' }) $count users in $file (passwords not shown)."

"Adding $($plan.Shipments) shipments over $($plan.Days) days to larkspur-$Environment (about a minute)..."
Get-Content -Raw $file | kubectl exec -i -n "larkspur-$Environment" deploy/larkspur-track -c web -- `
    larkspur-track-admin seed --days $plan.Days --shipments $plan.Shipments
if ($LASTEXITCODE) {
    # Nothing was added. A file this run made holds passwords that belong to no one.
    if ($made) { Remove-Item $file; $note = "$file was deleted" } else { $note = "$file was kept" }
    throw "seed failed (exit $LASTEXITCODE); nothing was added and $note. See the message above."
}
"Done. Save each username and password from $file in Bitwarden, then run: Remove-Item '$file'"
