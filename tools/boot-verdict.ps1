<#
  boot-verdict.ps1 -- 用「时间」判断这一卡到底开没开机

  用法（在 Windows 上直接跑，不需要 WSL）：
      pwsh -File tools\boot-verdict.ps1                 # 自动挑 G:\DEBUG 里最新的一次开机
      pwsh -File tools\boot-verdict.ps1 -DebugDir E:\x  # 指定日志目录
      pwsh -File tools\boot-verdict.ps1 -BootId 04213c6b

  它只读 kmsg-<id>.txt / ran-<id>.txt / live-*.txt，不改任何东西，
  最后打印一行结论：成功 / 还在正轨 / 失败在哪一环。

  时间基准全部是「本机 uptime」，不是墙上时间：
  第一阶段的里程碑来自 ran-<id>.txt 的 [x.xx] 前缀，
  第二阶段的里程碑来自 /dev/kmsg 的时间戳（微秒）。
#>

[CmdletBinding()]
param(
    [string]$DebugDir = 'G:\DEBUG',
    [string]$BootId   = '',
    [int]$HardFailS   = 90,    # 到这个秒数还没有 system_server，就是失败
    [int]$FullBootS   = 120    # 到这个秒数还没有 boot_completed，就算慢
)

$ErrorActionPreference = 'Stop'

function Fail($msg) { Write-Host "boot-verdict: $msg" -ForegroundColor Red; exit 2 }
function Note($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }

if (-not (Test-Path $DebugDir)) { Fail "no such directory: $DebugDir" }

# ---------------------------------------------------------------- 挑这次开机
if (-not $BootId) {
    $f = Get-ChildItem $DebugDir -Filter 'kmsg-*.txt' -File |
         Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $f) { Fail "no kmsg-*.txt in $DebugDir" }
    if ($f.Name -match '^kmsg-(.+)\.txt$') { $BootId = $Matches[1] }
}
$kmsgPath = Join-Path $DebugDir "kmsg-$BootId.txt"
$ranPath  = Join-Path $DebugDir "ran-$BootId.txt"
if (-not (Test-Path $kmsgPath)) { Fail "missing $kmsgPath" }

Note "boot id : $BootId"
Note ("kmsg    : {0:N0} bytes, {1}" -f (Get-Item $kmsgPath).Length, (Get-Item $kmsgPath).LastWriteTime)

# ---------------------------------------------------------------- 第一阶段（ran）
$handoverS = $null
$stage4bS  = $null
if (Test-Path $ranPath) {
    foreach ($line in Get-Content $ranPath) {
        if ($line -match 'hb:\s*\[([\d.]+)\] stage 9: about to execl') { $handoverS = [double]$Matches[1] }
        if ($line -match 'hb:\s*\[([\d.]+)\] stage 4b: child forked')   { $stage4bS  = [double]$Matches[1] }
    }
    if ($null -eq $handoverS) {
        # 交接后 ran 文件里还有 tb 心跳，没有 stage 9 行时就退回看最后一个 tb
        $last = Get-Content $ranPath | Select-String -Pattern 'tb: up=([\d.]+)' | Select-Object -Last 1
        if ($last -and $last.Line -match 'tb: up=([\d.]+)') { $handoverS = [double]$Matches[1] }
    }
}

# ---------------------------------------------------------------- 第二阶段（kmsg）
$re = '^\s*\d+,\s*\d+,\s*(\d+),[^;]*;(.*)$'
$t  = [System.Collections.Generic.List[double]]::new()
$m  = [System.Collections.Generic.List[string]]::new()
foreach ($line in Get-Content $kmsgPath) {
    if ($line -match $re) {
        $t.Add([double]$Matches[1] / 1e6)
        $m.Add($Matches[2])
    }
}
if ($t.Count -eq 0) { Fail "kmsg has no parsable lines (should look like '14,530,17415291,-;init: ...')" }
$lastS = $t[$t.Count - 1]

function FirstHit([string]$pattern, [int]$from = 0) {
    for ($i = $from; $i -lt $m.Count; $i++) {
        if ($m[$i] -match $pattern) { return [pscustomobject]@{ t = $t[$i]; i = $i; msg = $m[$i] } }
    }
    return $null
}
function AllHits([string]$pattern) {
    $r = @()
    for ($i = 0; $i -lt $m.Count; $i++) {
        if ($m[$i] -match $pattern) { $r += [pscustomobject]@{ t = $t[$i]; i = $i; msg = $m[$i] } }
    }
    return $r
}

$zygoteStarts = AllHits "init: starting service 'zygote'"
# 有些开机里 init 的 "starting service" 行被 printk 限速吃掉，只剩 socket / audit 痕迹
$zygoteMarks  = AllHits "Created socket '/dev/socket/zygote'|comm=`"app_process64`"|scontext=u:r:zygote:s0"
# zygote 每死一次，init 就跑一遍它的 onrestart 组（<Service 'zygote' onrestart>:N），
# 这条在日志里比 "starting service" 更不容易丢，用来数重启次数。
$zygoteRestarts = AllHits "action=onrestart \(<Service 'zygote' onrestart>:1\)"
$zygoteFirst = $null
foreach ($cand in @(
        $(if ($zygoteStarts.Count) { $zygoteStarts[0].t }),
        $(if ($zygoteMarks.Count)  { $zygoteMarks[0].t }))) {
    if ($null -ne $cand -and ($null -eq $zygoteFirst -or $cand -lt $zygoteFirst)) { $zygoteFirst = $cand }
}
# 只有「system_server 自己说话了」才算数：abort 文本里也带着 (system_server) 这个词，
# 9c7489bb 就靠它骗出过一次「还在正轨」的结论。
$systemServer = FirstHit 'Entered the Android system server|SystemServer\s*:'
$ssCrashes    = AllHits '\(system_server\)'
$bootAnim     = FirstHit "starting service 'bootanim'"
$bootDone     = FirstHit 'boot_completed=|sys\.boot_completed=1|Boot is finished'
$netdFirst    = FirstHit "starting service 'netd'"
$exits        = AllHits 'exited with status|killed by signal'

# ---- 卡上的 live 证据（快照会在 45 s 和 120 s 各写一次） ----
# kmsg 只收内核 printk；system_server 自己的话只在 logcat 里，所以只看 kmsg 会把
# 18c6c7be（明明已经 "Entered the Android system server!"）判成"从未出现"。
$liveLate = Join-Path $DebugDir 'live-logcat-late.txt'
$liveMain = Join-Path $DebugDir 'live-logcat.txt'
$livePath = if (Test-Path $liveLate) { $liveLate } else { $liveMain }
$liveText = if (Test-Path $livePath) { Get-Content $livePath -Raw } else { '' }
$propsPath = Join-Path $DebugDir 'live-props.txt'
$propsText = if (Test-Path $propsPath) { Get-Content $propsPath -Raw } else { '' }

$ssLive   = ($liveText -match 'Entered the Android system server')
$bootLive = ($propsText -match 'boot_completed\]\s*:\s*\[\s*1\s*\]') -or
            ($liveText -match 'sys\.boot_completed=1|Boot is finished')
$ssOk     = ($null -ne $systemServer) -or $ssLive
$bootOk   = ($null -ne $bootDone) -or $bootLive

# ---------------------------------------------------------------- 账本
$rows = New-Object System.Collections.Generic.List[object]
function Add-Row($stage, $time, $expect, $state, $detail = '') {
    $rows.Add([pscustomobject]@{ 阶段 = $stage; 时间 = $time; 预期 = $expect; 判定 = $state; 证据 = $detail })
}

if ($null -ne $handoverS) {
    $ok = if ($handoverS -le 25) { 'OK' } elseif ($handoverS -le 40) { '偏慢' } else { 'FAIL' }
    Add-Row '1 阶段交接 init.android' ('{0:N1}s' -f $handoverS) '<= 25s' $ok 'ran: stage 9 about to execl'
} else {
    Add-Row '1 阶段交接 init.android' '-' '<= 25s' '???' 'ran 里没有 stage 9 行'
}

if ($netdFirst) { Add-Row 'netd 第一次启动' ('{0:N1}s' -f $netdFirst.t) '20-45s' 'OK' '' }

if ($zygoteFirst) {
    $ok = if ($zygoteFirst -le 50) { 'OK' } else { '偏慢' }
    Add-Row 'zygote 第一次启动' ('{0:N1}s' -f $zygoteFirst) '20-50s' $ok ''
} else {
    Add-Row 'zygote 第一次启动' '-' '20-50s' 'FAIL' 'kmsg 里没有 starting service zygote'
}

if ($systemServer) {
    $lag = if ($zygoteFirst) { $systemServer.t - $zygoteFirst } else { $null }
    $ok  = if ($null -eq $lag -or $lag -le 10) { 'OK' } else { '偏慢' }
    $ev  = if ($lag) { 'zygote 之后 {0:N1}s' -f $lag } else { '' }
    Add-Row 'system_server 出现' ('{0:N1}s' -f $systemServer.t) 'zygote +10s 内' $ok $ev
} elseif ($ssLive) {
    Add-Row 'system_server 出现' '见 live-logcat' 'zygote +10s 内' 'OK' 'kmsg 里没有，logcat 里有 Entered the Android system server'
} else {
    Add-Row 'system_server 出现' '-' 'zygote +10s 内' 'FAIL' 'kmsg 和 live-logcat 都没有'
}

if ($bootAnim) { Add-Row 'bootanim 启动' ('{0:N1}s' -f $bootAnim.t) '25-40s' 'OK' '' }
if ($bootDone) { Add-Row 'boot_completed' ('{0:N1}s' -f $bootDone.t) '45-120s' 'OK' '' }
elseif ($bootLive) { Add-Row 'boot_completed' '见 live-props' '45-120s' 'OK' 'sys.boot_completed=1' }

# 快照里的属性表：哪些服务真的在跑，比日志更硬
if ($propsText) {
    $running = @()
    foreach ($line in ($propsText -split "`r?`n")) {
        if ($line -match '\[init\.svc\.([^\]\[]+)\]:\s*\[(\S+)\]') {
            if ($Matches[2] -eq 'running') { $running += $Matches[1] }
        }
    }
    if ($running.Count) { Add-Row '在跑的服务 (live-props)' ('{0} 个' -f $running.Count) '' 'OK' ($running -join ', ') }
    else { Add-Row '在跑的服务 (live-props)' '0 个' '' 'FAIL' 'init.svc.* 里没有 running' }
}

if ($ssCrashes.Count) {
    $first = $ssCrashes[0].msg
    if ($first.Length -gt 88) { $first = $first.Substring(0, 88) + '...' }
    Add-Row 'system_server 崩溃' ('{0} 次，首次 {1:N1}s' -f $ssCrashes.Count, $ssCrashes[0].t) '0 次' 'FAIL' $first
}

if ($zygoteRestarts.Count) {
    $per = if ($zygoteRestarts.Count -gt 1) { ($zygoteRestarts[-1].t - $zygoteRestarts[0].t) / ($zygoteRestarts.Count - 1) } else { 0 }
    Add-Row 'zygote 重启组' ('{0} 次' -f ($zygoteRestarts.Count + 1)) '应为 1 次' $(if ($zygoteRestarts.Count -ge 2) { 'FAIL' } else { 'OK' }) ('周期 {0:N2}s' -f $per)
}

Add-Row '日志最后一行' ('{0:N1}s' -f $lastS) '' '' ''

Note ''
$rows | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
Note ''

# ---------------------------------------------------------------- 结论
$zygoteCount = [Math]::Max($zygoteStarts.Count, $zygoteRestarts.Count + 1)
$restartLoop = ($zygoteCount -ge 3 -and -not $ssOk)

if ($bootOk -and $ssOk) {
    Note '结论: 成功 —— system_server 起来了，boot_completed 也拿到了。' 'Green'
    exit 0
}
if ($ssOk) {
    Note '结论: 还在正轨 —— system_server 已经启动，但还没有 boot_completed。' 'Cyan'
    Note '        如果 live-props 里 sys.boot_completed 为空，看 live-logcat-late.txt 里 SystemServer 停在哪一步。' 'Cyan'
    exit 0
}
if ($ssCrashes.Count -and -not $ssOk) {
    Note ('结论: 失败 —— system_server 一次都没起来，被 abort 打死 {0} 次（首次 {1:N1}s）。' -f $ssCrashes.Count, $ssCrashes[0].t) 'Red'
    Note ('        Abort message: {0}' -f $ssCrashes[0].msg) 'Red'
    Note '        这一层不用等：看 live-logcat.txt 的 Abort message 和崩溃栈，改代码再上卡。' 'Red'
    exit 1
}
if ($restartLoop) {
    # 用最干净的一组数：优先 "starting service"，没有就退回 onrestart 组
    $list = if ($zygoteStarts.Count -ge 2) { $zygoteStarts } else { $zygoteRestarts }
    $period = 0
    if ($list.Count -ge 2) { $period = ($list[-1].t - $list[0].t) / ($list.Count - 1) }
    $from  = if ($null -ne $zygoteFirst) { $zygoteFirst } else { 0 }
    $span  = $lastS - $from
    Note ('结论: 失败 —— zygote 启动 {0} 次、没有任何一次 fork 出 system_server。' -f $zygoteCount) 'Red'
    Note ('        循环周期 {0:N2}s，已经空转 {1:N0}s（日志最后 {2:N1}s）。' -f $period, $span, $lastS) 'Red'
    Note '        这一环再等也不会自己好，直接断电取 log。' 'Red'
    exit 1
}
if ($lastS -ge $HardFailS) {
    Note ('结论: 失败 —— 跑满 {0:N0}s 也没有 system_server（判据是 {1}s）。' -f $lastS, $HardFailS) 'Red'
    exit 1
}
Note ('结论: 还没到判点 —— 日志只到 {0:N1}s，等过 {1}s 再看。' -f $lastS, $HardFailS) 'Yellow'
exit 0