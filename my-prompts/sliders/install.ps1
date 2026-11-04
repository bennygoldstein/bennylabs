# Taskbar Sliders installer, version 3. https://bennylabs.org/my-prompts/sliders/
# Installs for the current Windows user only, with no admin rights. It adds:
#   - the folder %LOCALAPPDATA%\ScreenSliders with the program, a plain Python script
#   - a startup entry named ScreenSliders under HKCU\...\CurrentVersion\Run
#   - Python 3.12 for this user through winget, only if no Python with tkinter exists
# It keeps existing settings, and saves any older copy beside the new one.
param([switch]$Inner, [switch]$Reinstall)

$ErrorActionPreference = 'Stop'
$Version = '3'
$CodeUrl = 'https://bennylabs.org/my-prompts/sliders/screen_sliders.pyw'
$CodeHash = '28FEF19A26EE17CCEC4474A027C1DA57EF11D80DDDDB297CAE1EA97F983A1483'
$Report = Join-Path $env:USERPROFILE '.screen-sliders-install-report.txt'
$Started = Join-Path $env:USERPROFILE '.screen-sliders-install-started.txt'
$Staged = Join-Path $env:USERPROFILE '.screen-sliders-install.ps1'

if (-not $Inner) {
    # Apps packaged for the Microsoft Store (the Claude desktop app is one) have their AppData
    # and HKCU writes redirected into private storage, so an install made from inside them is
    # not real. The work therefore runs as a child of Explorer, which sees the real ones.
    foreach ($f in $Report, $Started) { if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force } }
    Copy-Item -LiteralPath $PSCommandPath -Destination $Staged -Force
    $argText = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Staged + '" -Inner'
    if ($Reinstall) { $argText += ' -Reinstall' }
    try {
        $desk = (New-Object -ComObject Shell.Application).Windows().FindWindowSW([ref]0, [ref]0, 8, [ref]0, 1)
        $desk.Document.Application.ShellExecute('powershell.exe', $argText, '', '', 0)
    } catch {
        Start-Process powershell.exe -ArgumentList $argText -WindowStyle Hidden
    }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path -LiteralPath $Started) -and $clock.Elapsed.TotalSeconds -lt 20) { Start-Sleep -Milliseconds 300 }
    if (-not (Test-Path -LiteralPath $Started)) { Start-Process powershell.exe -ArgumentList $argText -WindowStyle Hidden }
    # Installing Python, when it is needed, can take a few minutes.
    while (-not (Test-Path -LiteralPath $Report) -and $clock.Elapsed.TotalSeconds -lt 420) { Start-Sleep -Milliseconds 500 }
    if (-not (Test-Path -LiteralPath $Report)) { Write-Output 'STATUS: FAILED'; Write-Output 'The install did not finish within 7 minutes.'; exit 1 }
    $lines = Get-Content -LiteralPath $Report
    $lines | Write-Output
    foreach ($f in $Report, $Started, $Staged) { if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force } }
    if ($lines[0] -match 'FAILED|NEED_PYTHON') { exit 1 }
    exit 0
}

function Finish([string]$status, [string[]]$more) {
    $tmp = $Report + '.tmp'
    @("STATUS: $status") + $more | Out-File -LiteralPath $tmp -Encoding utf8
    Move-Item -LiteralPath $tmp -Destination $Report -Force
    exit 0
}

function Test-Python([string]$exe, [string[]]$first) {
    try {
        $out = & $exe @first -I -c 'import tkinter,sys;print(sys.base_prefix)' 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $w = Join-Path (@($out)[-1]).Trim() 'pythonw.exe'
        if (Test-Path -LiteralPath $w) { return $w }
    } catch {}
    return $null
}

function Find-Pythonw {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { $w = Test-Python $py.Source @('-3'); if ($w) { return $w } }
    $list = @()
    $list += Get-Item "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object { $_.FullName }
    $list += Get-Item "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object { $_.FullName }
    # The WindowsApps entries are store stubs, not Python.
    $list += Get-Command python.exe -All -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch 'WindowsApps' } | ForEach-Object { $_.Source }
    foreach ($p in $list) { $w = Test-Python $p @(); if ($w) { return $w } }
    return $null
}

function Get-Copies {
    @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -match 'screen_sliders\.pyw' })
}

try {
    'started' | Out-File -LiteralPath $Started -Encoding ascii
    Set-Location -LiteralPath $env:USERPROFILE
    $dir = Join-Path $env:LOCALAPPDATA 'ScreenSliders'
    $script = Join-Path $dir 'screen_sliders.pyw'
    $link = Join-Path $dir 'StartScreenSliders.lnk'
    $how = @(
        'To hide the bar: Settings > Personalization > Taskbar > Other system tray icons, switch off "Python".',
        "To start it again: Win+R, then $link",
        'To remove it: end the pythonw.exe running screen_sliders.pyw, delete the ScreenSliders Run value, and delete that folder.'
    )

    $have = 0
    if (Test-Path -LiteralPath $script) {
        $text = [IO.File]::ReadAllText($script)
        $m = [regex]::Match($text, '(?m)^VERSION = "(\d+)"')
        if ($m.Success) { $have = [int]$m.Groups[1].Value } elseif ($text.Contains('IsPromoted')) { $have = 2 } else { $have = 1 }
    }
    if ($have -gt [int]$Version) { Finish 'NEWER' @("Version $have is installed, which is newer than this installer (version $Version). Nothing was changed.") }

    $pythonw = Find-Pythonw
    if (-not $pythonw) {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if ($winget) {
            & $winget.Source install --id Python.Python.3.12 -e --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity 2>$null | Out-Null
            $pythonw = Find-Pythonw
        }
    }
    if (-not $pythonw) { Finish 'NEED_PYTHON' @('Python could not be installed automatically. Please install it from https://www.python.org/downloads/windows/ and then paste the prompt again.') }

    if ($have -eq [int]$Version -and -not $Reinstall) {
        if ((Get-Copies).Count -gt 0) { Finish 'UP_TO_DATE' (@("Version $Version is already installed and running. Nothing was changed.") + $how) }
        Start-Process -FilePath $pythonw -ArgumentList ('"' + $script + '"')
        Finish 'UP_TO_DATE' (@("Version $Version was already installed. It was not running, so it was started.") + $how)
    }

    [void](New-Item -ItemType Directory -Force -Path $dir)
    $new = Join-Path $dir 'screen_sliders.new'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $CodeUrl -UseBasicParsing -OutFile $new
    if ((Get-FileHash -LiteralPath $new -Algorithm SHA256).Hash -ne $CodeHash) {
        Remove-Item -LiteralPath $new -Force
        Finish 'FAILED' @('The downloaded program does not match its fingerprint, so nothing was installed.')
    }

    foreach ($c in Get-Copies) { Stop-Process -Id $c.ProcessId -Force }
    if ($have -gt 0) {
        Start-Sleep 2
        $keep = Join-Path $dir "screen_sliders_v$have.pyw"
        $n = 2
        while (Test-Path -LiteralPath $keep) { $keep = Join-Path $dir "screen_sliders_v$have-$n.pyw"; $n++ }
        Copy-Item -LiteralPath $script -Destination $keep
    }
    Move-Item -LiteralPath $new -Destination $script -Force

    $run = '"' + $pythonw + '" "' + $script + '"'
    New-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'ScreenSliders' -Value $run -PropertyType String -Force | Out-Null
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
    $s.TargetPath = $pythonw
    $s.Arguments = '"' + $script + '"'
    $s.Save()

    Start-Process -FilePath $pythonw -ArgumentList ('"' + $script + '"')
    Start-Sleep 5
    if ((Get-Copies).Count -lt 1) {
        $tail = @()
        $log = Join-Path $dir 'screen_sliders.log'
        if (Test-Path -LiteralPath $log) { $tail = Get-Content -LiteralPath $log -Tail 8 }
        Finish 'FAILED' (@('The program was installed but did not stay running. Its log says:') + $tail)
    }
    if ($have -eq 0) { Finish 'INSTALLED' (@("Version $Version is installed and running. It starts with Windows.") + $how) }
    if ($have -eq [int]$Version) { Finish 'REINSTALLED' (@("Version $Version was installed again and is running. Settings were kept.") + $how) }
    Finish 'UPDATED' (@("Updated from version $have to version $Version, and it is running. Settings were kept, and the old copy was saved beside it.") + $how)
} catch {
    Finish 'FAILED' @("The install stopped with this error: $($_.Exception.Message)")
}
