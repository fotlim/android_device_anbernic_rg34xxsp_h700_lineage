<#
.SYNOPSIS
    把 USB-TTL 串口（UART0, 115200 8N1）的日志持续写进文件，方便随时翻。

.DESCRIPTION
    用法：
        pwsh -File watch-serial.ps1 -Port COM5
        pwsh -File watch-serial.ps1 -Port COM5 -Out d:\rg34xxsp-serial.log

    接线（H700 UART0，PH 组，3.3V 电平）：
        USB-TTL GND  -> 排针/焊盘 GND
        USB-TTL RX   -> 主板 TX (PH0)
        USB-TTL TX   -> 主板 RX (PH1)
        不要接 VCC！

    停止：Ctrl+C。脚本只在收到数据时写文件，空转不占 CPU。
#>
param(
    [Parameter(Mandatory = $true)][string]$Port,
    [int]$Baud = 115200,
    [string]$Out = "serial-$(Get-Date -Format yyyyMMdd-HHmm).log",
    [switch]$NoTimestamp
)

$sp = New-Object System.IO.Ports.SerialPort $Port, $Baud, 'None', 8, 'One'
$sp.Handshake = 'None'
$sp.ReadTimeout = 500
$sp.NewLine = "`n"
$sp.DtrEnable = $false
$sp.RtsEnable = $false

try {
    $sp.Open()
    Write-Host "listening on $Port @ $Baud 8N1 -> $Out   (Ctrl+C 停止)" -ForegroundColor Green
} catch {
    Write-Host "打不开 $Port : $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "先确认：设备管理器里的 COM 号、没被别的串口工具占用。" -ForegroundColor Yellow
    exit 1
}

$buf = New-Object System.Text.StringBuilder
try {
    while ($true) {
        try { $data = $sp.ReadExisting() } catch { $data = '' }
        if ($data) {
            if ($NoTimestamp) {
                [System.IO.File]::AppendAllText($Out, $data)
            } else {
                foreach ($line in ($data -split "`r?`n")) {
                    if ($line -ne '') {
                        [System.IO.File]::AppendAllText($Out, ("{0}  {1}`n" -f (Get-Date -Format 'HH:mm:ss.fff'), $line))
                    }
                }
            }
            Write-Host -NoNewline $data
        } else {
            Start-Sleep -Milliseconds 30
        }
    }
} finally {
    $sp.Close()
    Write-Host "`n已关闭。日志：$Out" -ForegroundColor Cyan
}