# restart_ch340.ps1
# 免拔插恢复 CH340 串口（温箱）error 31 / 驱动卡死状态。
# 原理等同拔插 USB：用 pnputil 重启对应的 PnP 设备（需管理员权限）。
# 通常请直接双击 restart_ch340.bat，它会自动提权后调用本脚本。
# 详见 docs/CH340_error31_fix.md

$devices = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -like 'USB\VID_1A86&PID_7523*' }

if (-not $devices) {
    Write-Host '[警告] 未找到 CH340 设备，请确认温箱 USB 已连接。'
} else {
    foreach ($d in $devices) {
        Write-Host "重启设备: $($d.InstanceId)  ($($d.FriendlyName))"
        pnputil /restart-device "$($d.InstanceId)"
        Write-Host ''
    }
    Write-Host '完成。串口应已恢复，可重新运行测试。'
}
