@echo off
setlocal

set "ROOT=%LOCALAPPDATA%\OysterRecorder"
set "PS1=%TEMP%\oyster_launch_mc_%RANDOM%%RANDOM%.ps1"

> "%PS1%" (
  echo $ErrorActionPreference = 'Stop'
  echo $root = Join-Path $env:LOCALAPPDATA 'OysterRecorder'
  echo $instance = Join-Path $root 'mc-instance'
  echo $profileName = 'fabric-loader-0.16.10-1.21.4'
  echo $mcVersion = '1.21.4'
  echo $java = Join-Path $root 'jre\bin\javaw.exe'
  echo $leafPath = Join-Path $instance "versions\$profileName\$profileName.json"
  echo $parentPath = Join-Path $instance "versions\$mcVersion\$mcVersion.json"
  echo $clientJar = Join-Path $instance "versions\$mcVersion\$mcVersion.jar"
  echo $natives = Join-Path $instance "versions\$mcVersion\natives"
  echo foreach ($p in @($java, $leafPath, $parentPath, $clientJar, $natives^)^) { if (-not (Test-Path $p^)^) { throw "Missing bundled Minecraft runtime path: $p" } }
  echo $options = Join-Path $instance 'options.txt'
  echo New-Item -ItemType Directory -Force -Path (Split-Path $options^) ^| Out-Null
  echo $lines = @(^)
  echo if (Test-Path $options^) { $lines = Get-Content -LiteralPath $options -ErrorAction SilentlyContinue }
  echo $patched = @(^)
  echo $saw = $false
  echo foreach ($line in $lines^) { if ($line -like 'pauseOnLostFocus:*'^) { $patched += 'pauseOnLostFocus:false'; $saw = $true } else { $patched += $line } }
  echo if (-not $saw^) { $patched += 'pauseOnLostFocus:false' }
  echo Set-Content -LiteralPath $options -Value $patched -Encoding UTF8
  echo $leaf = Get-Content -Raw -LiteralPath $leafPath ^| ConvertFrom-Json
  echo $parent = Get-Content -Raw -LiteralPath $parentPath ^| ConvertFrom-Json
  echo if ($leaf.mainClass -ne 'net.fabricmc.loader.impl.launch.knot.KnotClient'^) { throw "Fabric mainClass mismatch: $($leaf.mainClass)" }
  echo function MavenPath([string]$name^) {
  echo   $parts = $name.Split(':'^)
  echo   if ($parts.Length -lt 3^) { throw "Bad Maven coordinate: $name" }
  echo   $group = $parts[0].Replace('.', '\'^)
  echo   $artifact = $parts[1]
  echo   $version = $parts[2]
  echo   $classifier = ''
  echo   if ($parts.Length -ge 4^) { $classifier = '-' + $parts[3] }
  echo   return Join-Path (Join-Path (Join-Path $group $artifact^) $version^) "$artifact-$version$classifier.jar"
  echo }
  echo $libsRoot = Join-Path $instance 'libraries'
  echo $seen = @{}
  echo $classpath = New-Object System.Collections.Generic.List[string]
  echo foreach ($lib in @($leaf.libraries^) + @($parent.libraries^)^) {
  echo   if (-not $lib.name^) { continue }
  echo   if ($lib.PSObject.Properties.Name -contains 'natives'^) { continue }
  echo   $parts = ([string]$lib.name^).Split(':'^)
  echo   if ($parts.Length -ge 4 -and $parts[3] -like 'natives-*'^) { continue }
  echo   $key = $parts[0] + ':' + $parts[1]
  echo   if ($seen.ContainsKey($key^)^) { continue }
  echo   $seen[$key] = $true
  echo   $jar = Join-Path $libsRoot (MavenPath $lib.name^)
  echo   if (Test-Path $jar^) { $classpath.Add($jar^) }
  echo }
  echo $classpath.Add($clientJar^)
  echo $cp = [string]::Join(';', $classpath^)
  echo $assetIndex = '19'
  echo if ($parent.assetIndex -and $parent.assetIndex.id^) { $assetIndex = $parent.assetIndex.id }
  echo $username = 'Player'
  echo $uuidBytes = [System.Security.Cryptography.MD5]::Create(^).ComputeHash([Text.Encoding]::UTF8.GetBytes("OfflinePlayer:$username"^)^)
  echo $uuidBytes[6] = ($uuidBytes[6] -band 0x0f^) -bor 0x30
  echo $uuidBytes[8] = ($uuidBytes[8] -band 0x3f^) -bor 0x80
  echo $uuid = [Guid]::new($uuidBytes^).ToString(^)
  echo $cmd = New-Object System.Collections.Generic.List[string]
  echo $cmd.AddRange([string[]]@($java, '-Xmx4G', '-Xms4G', '-XX:+UnlockExperimentalVMOptions', '-XX:+UseG1GC', '-XX:G1NewSizePercent=20', '-XX:G1ReservePercent=20', '-XX:MaxGCPauseMillis=50', '-XX:G1HeapRegionSize=32M'^)^)
  echo if ($leaf.arguments -and $leaf.arguments.jvm^) { foreach ($arg in $leaf.arguments.jvm^) { if ($arg -is [string]^)^ { $cmd.Add($arg.Replace('${natives_directory}', $natives^).Replace('${classpath}', $cp^).Replace('${launcher_name}', 'OysterPlay'^).Replace('${launcher_version}', '1.0.0'^)^) } } }
  echo $cmd.Add("-Djava.library.path=$natives"^)
  echo $cmd.Add('-cp'^)
  echo $cmd.Add($cp^)
  echo $cmd.Add($leaf.mainClass^)
  echo $cmd.AddRange([string[]]@('--username', $username, '--version', $profileName, '--gameDir', $instance, '--assetsDir', (Join-Path $instance 'assets'^), '--assetIndex', $assetIndex, '--uuid', $uuid, '--accessToken', '0', '--clientId', '', '--xuid', '', '--userType', 'legacy', '--versionType', 'release'^)^)
  echo $logDir = Join-Path $root 'logs'
  echo New-Item -ItemType Directory -Force -Path $logDir ^| Out-Null
  echo $log = Join-Path $logDir ("javaw_" + [DateTimeOffset]::Now.ToUnixTimeSeconds(^) + ".log"^)
  echo Set-Content -LiteralPath $log -Value ([string]::Join(' ', $cmd^)^) -Encoding UTF8
  echo $launchArgs = $cmd.ToArray(^)[1..($cmd.Count-1^)]
  echo Start-Process -FilePath $java -ArgumentList $launchArgs -WorkingDirectory $instance
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
set "RC=%ERRORLEVEL%"
del "%PS1%" >nul 2>nul
exit /b %RC%
