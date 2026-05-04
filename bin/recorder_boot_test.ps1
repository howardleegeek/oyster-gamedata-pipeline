param([int]$Reps = 10)

$bundle = "C:\Users\howar\gd-x64"
$exe = Join-Path $bundle gamedata-recorder.exe
$results = @()

Write-Host "### Boot reliability test -- $Reps runs ###"
for ($i = 1; $i -le $Reps; $i++) {
    $outLog = Join-Path $bundle "rep_$i.out"
    $errLog = Join-Path $bundle "rep_$i.err"
    $t0 = Get-Date
    $p = Start-Process -FilePath $exe -WorkingDirectory $bundle -PassThru `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    $exited = $p.WaitForExit(8000)
    $ms = [int]((Get-Date) - $t0).TotalMilliseconds
    $obsInit = $false
    if (Test-Path $outLog) {
        $obsInit = (Select-String -Path $outLog -Pattern "OBS 32.0" -Quiet)
    }
    $trayErr = $false
    if (Test-Path $errLog) {
        $trayErr = (Select-String -Path $errLog -Pattern "tray_icon.rs" -Quiet)
    }
    $results += [pscustomobject]@{
        Run = $i
        Exited = $exited
        ElapsedMs = $ms
        ExitCode = $p.ExitCode
        OBSInit = $obsInit
        TrayErr = $trayErr
    }
    if (-not $exited) { Stop-Process -Id $p.Id -Force }
}

Write-Host ""
Write-Host "### Per-run table ###"
$results | Format-Table -AutoSize | Out-String | Write-Host

$obsCount = ($results | Where-Object OBSInit).Count
$trayCount = ($results | Where-Object TrayErr).Count
$avg = [int](($results | Measure-Object ElapsedMs -Average).Average)
$min = ($results | Measure-Object ElapsedMs -Minimum).Minimum
$max = ($results | Measure-Object ElapsedMs -Maximum).Maximum

Write-Host "### Summary ###"
Write-Host "  reps=$Reps"
Write-Host "  OBS_inited_count=$obsCount/$Reps"
Write-Host "  tray_icon_err_count=$trayCount/$Reps"
Write-Host "  boot_to_die_ms: avg=$avg min=$min max=$max"
Write-Host ""
if ($obsCount -eq $Reps -and $trayCount -eq $Reps) {
    Write-Host "VERDICT: 100% deterministic boot+tray-fail. Recorder reliable; only blocker is interactive desktop session."
} else {
    Write-Host "VERDICT: NON-DETERMINISTIC -- investigate"
}
