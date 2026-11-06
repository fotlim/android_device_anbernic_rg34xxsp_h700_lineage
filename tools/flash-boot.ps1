<#
.SYNOPSIS
    Flash (or restore) the 'boot' partition of an ANBERNIC RG34XX SP.

.DESCRIPTION
    The stock bootloader is NOT replaced. The unit's p4 partition already holds a
    perfectly ordinary Android boot.img, so upgrading Android here is just:
        dd a new boot.img over /dev/mmcblk0p4
    -- no U-Boot surgery, no repartitioning, and the eMMC stays otherwise untouched.

    This script refuses to write anything until it can prove that a stock p4
    snapshot exists on the host, so the operation is always reversible.

    Only the boot partition is written. Nothing else is touched.

.PARAMETER Image
    Path to the boot.img to flash (built by `mka bacon`, lands in out/.../boot.img).

.PARAMETER Restore
    Put the stock boot.img back. Uses -BackupDir (p4.img or p4.img.zst).

.PARAMETER BackupDir
    Directory holding the stock snapshot pulled by tools/backup-stock.sh.

.PARAMETER DryRun
    Do every check, push nothing, write nothing.

.EXAMPLE
    .\flash-boot.ps1 -Image out\target\product\rg34xxsp\boot.img

.EXAMPLE
    .\flash-boot.ps1 -Restore

.NOTES
    ASCII only: Windows PowerShell 5.1 reads .ps1 as ANSI without a BOM.
    Chinese notes live in docs/BUILD.md.
#>
[CmdletBinding(DefaultParameterSetName = 'Flash')]
param(
    [Parameter(ParameterSetName = 'Flash',   Mandatory = $true, Position = 0)]
    [string] $Image,

    [Parameter(ParameterSetName = 'Restore', Mandatory = $true)]
    [switch] $Restore,

    [string] $DeviceHost = '192.168.137.30',
    [string] $DeviceUser = 'root',
    [string] $Password   = 'root',
    [string] $Part       = '/dev/mmcblk0p4',
    [int]    $PartBytes  = 67108864,
    [string] $BackupDir  = 'F:\dsh-backup\rg34xxsp-stock-20261106',
    [switch] $DryRun,
    [switch] $NoReboot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ssh = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
$scp = Join-Path $env:SystemRoot 'System32\OpenSSH\scp.exe'
$SSH_OPTS = @('-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=NUL',
              '-o', 'ConnectTimeout=10', '-o', 'LogLevel=ERROR')

function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok ($m) { Write-Host "    $m" -ForegroundColor Green }
function Die($m) { Write-Host "!!! $m" -ForegroundColor Red; exit 1 }

# --- askpass plumbing: Windows OpenSSH has no sshpass, use an askpass helper ----
$askpass = Join-Path $env:TEMP 'dsh-askpass.bat'
Set-Content -Path $askpass -Value "@echo off`r`necho %DSH_SSH_PASS%" -Encoding ASCII
$env:DSH_SSH_PASS        = $Password
$env:SSH_ASKPASS         = $askpass
$env:SSH_ASKPASS_REQUIRE = 'force'
$env:DISPLAY             = ':0'

function DeviceRun {
    param([string] $Command, [switch] $Quiet)
    $out = & $ssh @SSH_OPTS "$DeviceUser@$DeviceHost" $Command 2>&1
    $code = $LASTEXITCODE
    if (-not $Quiet) { $out | ForEach-Object { Write-Host "    | $_" -ForegroundColor DarkGray } }
    if ($code -ne 0) { throw "remote command failed (exit $code): $Command" }
    return $out
}

function Read-BootImage([string] $Path) {
    if (-not (Test-Path $Path)) { Die "image not found: $Path" }
    $m = [System.IO.File]::OpenRead($Path)
    try {
        $h = New-Object byte[] 64
        [void]$m.Read($h, 0, 64)
        $total = $m.Length
    } finally { $m.Dispose() }

    $magic = [System.Text.Encoding]::ASCII.GetString($h, 0, 8)
    if ($magic -ne 'ANDROID!') { Die "not an Android boot image (magic='$magic'): $Path" }

    $kernel = [BitConverter]::ToUInt32($h,  8)
    $kaddr  = [BitConverter]::ToUInt32($h, 12)
    $rd     = [BitConverter]::ToUInt32($h, 16)
    $raddr  = [BitConverter]::ToUInt32($h, 20)
    $second = [BitConverter]::ToUInt32($h, 24)
    $taddr  = [BitConverter]::ToUInt32($h, 32)
    $page   = [BitConverter]::ToUInt32($h, 36)
    $name   = [System.Text.Encoding]::ASCII.GetString($h, 48, 16).TrimEnd([char]0)

    return [pscustomobject]@{
        Path=$Path; Total=[int64]$total; PageSize=[int]$page
        KernelSize=$kernel; KernelAddr=$kaddr; RamdiskSize=$rd; RamdiskAddr=$raddr
        SecondSize=$second; TagsAddr=$taddr; Name=$name
        Base = [uint64]($kaddr) - ([uint64]($kaddr) % 0x100000)
    }
}

Say 'RG34XX SP boot partition writer'
Write-Host "    part    $Part  ($([math]::Round($PartBytes/1MB)) MiB)"
Write-Host "    device  ${DeviceUser}@${DeviceHost}"

# --- 1. pick the payload -------------------------------------------------------
if ($Restore) {
    Say 'restore mode: looking for the stock p4 snapshot'
    $raw = Join-Path $BackupDir 'p4.img'
    $zst = Join-Path $BackupDir 'p4.img.zst'
    if (Test-Path $raw) {
        Ok "using $raw"
    } elseif (Test-Path $zst) {
        Ok "decompressing $zst with 7-Zip"
        $sz = 'C:\Program Files\7-Zip\7z.exe'
        if (-not (Test-Path $sz)) { Die 'need 7-Zip to expand p4.img.zst' }
        & $sz e $zst ("-o" + $BackupDir) -y | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $raw)) { Die '7-Zip failed to expand p4.img.zst' }
        Ok "expanded to $raw"
    } else {
        Die "no stock snapshot in $BackupDir -- run tools/backup-stock.sh on the device first"
    }
    $img = Read-BootImage $raw
    $payload = $raw
} else {
    Say 'happy path: flashing a new boot.img'
    $img = Read-BootImage $Image
    $payload = (Resolve-Path $Image).Path
}

# --- 2. sanity: geometry must match what the stock U-Boot expects ---------------
Say 'checking boot image geometry against the stock one'
Write-Host ("    page_size   {0}" -f $img.PageSize)
Write-Host ("    kernel      {0} bytes @ 0x{1:x8}" -f $img.KernelSize, $img.KernelAddr)
Write-Host ("    ramdisk     {0} bytes @ 0x{1:x8}" -f $img.RamdiskSize, $img.RamdiskAddr)
Write-Host ("    tags        @ 0x{0:x8}" -f $img.TagsAddr)
Write-Host ("    name        '{0}'" -f $img.Name)
Write-Host ("    base        0x{0:x8}  (kernel off 0x{1:x8}, ramdisk off 0x{2:x8})" -f `
    $img.Base, ($img.KernelAddr - $img.Base), ($img.RamdiskAddr - $img.Base))
Write-Host ("    total       {0} bytes ({1:N2} MiB)" -f $img.Total, ($img.Total / 1MB))

if ($img.PageSize -ne 2048) { Die "page_size must be 2048, got $($img.PageSize)" }
if ($img.Base -ne 0x40000000) { Die ("kernel base must be 0x40000000, got 0x{0:x8}" -f $img.Base) }
if ($img.Name -ne 'sun50i_arm64' -and -not $Restore) {
    Write-Host "    (warning: board name is '$($img.Name)', stock uses 'sun50i_arm64')" -ForegroundColor Yellow
}
if ($img.Total -gt $PartBytes) {
    Die ("image is {0} bytes but the partition is only {1}" -f $img.Total, $PartBytes)
}
Ok 'geometry accepted'

# --- 3. reversibility gate -----------------------------------------------------
Say 'checking that we can get back to stock'
$stockRaw = Join-Path $BackupDir 'p4.img'
$stockZst = Join-Path $BackupDir 'p4.img.zst'
if (-not ($Restore -and $payload -eq $stockRaw)) {
    if (-not (Test-Path $stockRaw) -and -not (Test-Path $stockZst)) {
        Die "no stock p4 snapshot in $BackupDir -- refusing to write. Run tools/backup-stock.sh first."
    }
    Ok "stock snapshot present: $(if (Test-Path $stockRaw) { $stockRaw } else { $stockZst })"
}

# --- 4. talk to the device -----------------------------------------------------
Say 'probing the device'
$info = DeviceRun "blockdev --getsize64 $Part; id -un; uname -r" -Quiet
$info | ForEach-Object { Write-Host "    | $_" -ForegroundColor DarkGray }
$liveBytes = [int64](($info[0] | Out-String).Trim())
if ($liveBytes -ne $PartBytes) {
    Die "partition is $liveBytes bytes on the device, script assumes $PartBytes -- abort"
}
Ok "partition size matches"

if ($DryRun) {
    Say 'dry run: stopping before any write'
    exit 0
}

if (-not $Restore) {
    Say 'saving the current p4 to the device (belt and braces)'
    DeviceRun "dd if=$Part of=/tmp/p4-before.img bs=1M status=none && sync && ls -l /tmp/p4-before.img" -Quiet |
        ForEach-Object { Write-Host "    | $_" -ForegroundColor DarkGray }
}

# --- 5. push and write ---------------------------------------------------------
$remote = '/tmp/flash-boot.img'
Say "pushing $([math]::Round($img.Total/1MB,2)) MiB to the device"
& $scp @SSH_OPTS $payload "${DeviceUser}@${DeviceHost}:$remote" 2>&1 |
    ForEach-Object { Write-Host "    | $_" -ForegroundColor DarkGray }
if ($LASTEXITCODE -ne 0) { Die 'scp failed' }
Ok 'pushed'

$localHash = (Get-FileHash -Path $payload -Algorithm SHA256).Hash.ToLower()
$remoteHash = (DeviceRun "sha256sum $remote" -Quiet | Out-String).Trim().Split(' ')[0]
if ($remoteHash -ne $localHash) { Die "transfer corrupted (host $localHash vs device $remoteHash)" }
Ok "sha256 $($localHash.Substring(0,16)) matches on both ends"

Say 'writing to the boot partition'
DeviceRun "dd if=$remote of=$Part bs=1M conv=fsync,notrunc status=none && sync && echo WRITTEN"
Ok 'written and synced'

Say 'reading back and comparing'
DeviceRun "cmp -n $($img.Total) $remote $Part && echo BYTE_FOR_BYTE_IDENTICAL" |
    ForEach-Object { Write-Host "    | $_" -ForegroundColor DarkGray }
Ok 'read-back identical'

# --- 6. reboot ----------------------------------------------------------------
if ($NoReboot) {
    Say 'done; not rebooting (-NoReboot)'
} else {
    Say 'rebooting into the new boot image'
    try { DeviceRun 'sync; reboot' -Quiet | Out-Null } catch { }
    Ok 'reboot sent (ssh will drop, that is expected)'
}
Write-Host ''
Write-Host 'If it does not boot, get back with:' -ForegroundColor Yellow
Write-Host "    .\flash-boot.ps1 -Restore" -ForegroundColor Yellow