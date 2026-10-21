# Guojin only. No login/password/order operations. Profile contains observed
# read-only points/selectors, never PowerShell code or arbitrary key sequences.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($WPRequest)) | ConvertFrom-Json
$mutex = New-Object Threading.Mutex($false, 'Local\WealthPilotGuojinNativeExport')
$locked = $false
try {
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'AUTO_BUSY' }
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName System.Drawing
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public class WPGuojinNative {
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,UIntPtr e);
 [DllImport("user32.dll")] public static extern IntPtr OpenInputDesktop(uint f,bool inherit,uint access);
 [DllImport("user32.dll")] public static extern bool CloseDesktop(IntPtr h);
}
'@
    [void][WPGuojinNative]::SetProcessDPIAware()
    $profile = $request.profile
    function Check-Deadline {
        if ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -ge $request.deadline) { throw 'AUTO_TIMEOUT' }
    }
    function Current-Window {
        Check-Deadline
        $desktop = [WPGuojinNative]::OpenInputDesktop(0,$false,1)
        if ($desktop -eq [IntPtr]::Zero) { throw 'AUTO_LOGIN_REQUIRED' }
        [void][WPGuojinNative]::CloseDesktop($desktop)
        $processes = @(Get-Process -Name xiadan -ErrorAction SilentlyContinue)
        if ($processes.Count -ne 1 -or -not [Environment]::UserInteractive -or
            $processes[0].SessionId -ne (Get-Process -Id $PID).SessionId) { throw 'AUTO_SESSION_UNAVAILABLE' }
        $script:qnx = $processes[0]
        # MainWindowHandle was a hidden IE window in the real probe. Locate the
        # visible trading window by process + observed title, never a saved HWND.
        $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
        $found = @($windows | Where-Object { $_.Current.ProcessId -eq $qnx.Id -and
            -not $_.Current.IsOffscreen -and $_.Current.Name -match $profile.window_pattern })
        if ($found.Count -ne 1) { throw 'AUTO_LOGIN_REQUIRED' }
        return $found[0]
    }
    function Controls($window) {
        $nodes = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)
        $names = (@($window.Current.Name) + @($nodes | ForEach-Object {$_.Current.Name})) -join "`n"
        # Never dismiss broker/auth errors and continue with a cached screen.
        if ($names -match '120022|sys_status\s*=\s*6') { throw 'AUTO_COUNTER_120022' }
        if ($names -match '请输入.*密码|验证码|登录失效|重新登录') { throw 'AUTO_LOGIN_REQUIRED' }
        $all = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
        foreach ($w in $all) {
            if ($w.Current.ProcessId -eq $qnx.Id -and $w.Current.ClassName -eq '#32770') {
                $dialogText = ($w.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                    [System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object {$_.Current.Name}) -join "`n"
                if ($dialogText -match '120022') { throw 'AUTO_COUNTER_120022' }
                if ($dialogText -match '密码|验证码|禁止|错误|失败') { throw 'AUTO_GUI_UNAVAILABLE' }
            }
        }
        $match = [regex]::Match($names,$profile.account_pattern)
        if (-not $match.Success -or $match.Groups['account'].Value -cne $request.account_id) {
            throw 'AUTO_ACCOUNT_MISMATCH'
        }
        return @{nodes=$nodes; names=$names}
    }
    function Field($nodes,$selector) {
        if (-not $selector.id -or -not $selector.class) { throw 'AUTO_GUI_CONTRACT_UNVERIFIED' }
        $found = @($nodes | Where-Object { -not $_.Current.IsOffscreen -and
            $_.Current.AutomationId -ceq $selector.id -and $_.Current.ClassName -ceq $selector.class })
        if ($found.Count -ne 1) { throw 'AUTO_GUI_UNAVAILABLE' }
        return $found[0]
    }
    function Read-Field($nodes,$selector) {
        $text = (Field $nodes $selector).Current.Name
        $match = [regex]::Match($text,$selector.pattern)
        if (-not $match.Success -or -not $match.Groups['value'].Success) { throw 'AUTO_GUI_UNAVAILABLE' }
        return $match.Groups['value'].Value
    }
    function Click-Observed($step) {
        $window = Current-Window
        $state = Controls $window
        if (-not $step.expect -or $state.names -notmatch $step.expect) { throw 'AUTO_GUI_UNAVAILABLE' }
        $rect = $window.Current.BoundingRectangle
        if ($rect.Width -ne $profile.width -or $rect.Height -ne $profile.height) { throw 'AUTO_GUI_UNAVAILABLE' }
        if ($step.x -lt 0 -or $step.y -lt 0 -or $step.x -ge $rect.Width -or $step.y -ge $rect.Height) {
            throw 'AUTO_GUI_CONTRACT_UNVERIFIED'
        }
        [void][WPGuojinNative]::SetForegroundWindow([IntPtr]$window.Current.NativeWindowHandle)
        if ([WPGuojinNative]::GetForegroundWindow().ToInt64() -ne $window.Current.NativeWindowHandle) { throw 'AUTO_GUI_UNAVAILABLE' }
        Check-Deadline
        [void][WPGuojinNative]::SetCursorPos([int]($rect.X+$step.x),[int]($rect.Y+$step.y))
        if ($step.button -eq 'right') { $down=8; $up=16 } else { $down=2; $up=4 }
        [WPGuojinNative]::mouse_event($down,0,0,0,[UIntPtr]::Zero)
        [WPGuojinNative]::mouse_event($up,0,0,0,[UIntPtr]::Zero)
        Start-Sleep -Milliseconds 250
    }
    function Source-Time($text) {
        # GUI source local China time; never substitute local Get-Date or mtime.
        return [DateTimeOffset]::ParseExact($text+' +08:00','yyyy-MM-dd HH:mm:ss zzz',
            [Globalization.CultureInfo]::InvariantCulture)
    }
    if (-not $profile.verified_readonly -or -not $profile.evidence_ref) { throw 'AUTO_GUI_CONTRACT_UNVERIFIED' }
    if (Test-Path -LiteralPath $request.directory) { throw 'AUTO_EVIDENCE_INVALID' }
    $null = New-Item -ItemType Directory -Path $request.directory
    $result = @{schema='guojin.gui-observations.v1';status='EXPORTED';batch=$request.batch;markets=@{}}
    foreach ($market in @('stock','hgt','sgt')) {
        $spec = $profile.markets.$market
        foreach ($step in $spec.open) { Click-Observed $step }
        $state = Controls (Current-Window)
        if (-not $spec.page_pattern -or $state.names -notmatch $spec.page_pattern) { throw 'AUTO_GUI_UNAVAILABLE' }
        $before = Source-Time (Read-Field $state.nodes $spec.query)
        $actionTime = [DateTimeOffset]::UtcNow
        Click-Observed $spec.refresh
        $limit = [DateTimeOffset]::UtcNow.AddSeconds(30)
        do {
            Start-Sleep -Milliseconds 400
            $window = Current-Window
            $state = Controls $window
            if ($state.names -notmatch $spec.page_pattern) { throw 'AUTO_GUI_UNAVAILABLE' }
            $after = Source-Time (Read-Field $state.nodes $spec.query)
            if ($after -gt $before -and $after -ge $actionTime.AddSeconds(-1)) { break }
        } while ([DateTimeOffset]::UtcNow -lt $limit)
        if ($after -le $before -or $after -lt $actionTime.AddSeconds(-1)) { throw 'AUTO_QUERY_UNCHANGED' }
        # Advancing query time also occurred on the real 120022 failure. Require
        # a separately observed successful-completion indicator, never time alone.
        if (-not $spec.success -or (Read-Field $state.nodes $spec.success) -cne 'SUCCESS') {
            throw 'AUTO_GUI_CONTRACT_UNVERIFIED'
        }
        # Independent complete GUI count is mandatory. Virtual-grid visible row
        # count or exported line count cannot self-certify completeness.
        $rows = [int](Read-Field $state.nodes $spec.rows)
        if ($rows -lt 0) { throw 'AUTO_EVIDENCE_INVALID' }
        if ($market -eq 'stock') { $result.account_total_assets_cny = Read-Field $state.nodes $spec.total }
        $rect = $window.Current.BoundingRectangle
        if ([WPGuojinNative]::GetForegroundWindow().ToInt64() -ne $window.Current.NativeWindowHandle) {
            throw 'AUTO_GUI_UNAVAILABLE'
        }
        $image = New-Object Drawing.Bitmap([int]$rect.Width,[int]$rect.Height)
        $graphics = [Drawing.Graphics]::FromImage($image)
        try {
            $graphics.CopyFromScreen([int]$rect.X,[int]$rect.Y,0,0,$image.Size)
            $image.Save((Join-Path $request.directory "$market.png"),[Drawing.Imaging.ImageFormat]::Png)
        } finally { $graphics.Dispose(); $image.Dispose() }
        foreach ($step in $spec.export) { Click-Observed $step }
        Check-Deadline
        # Only the verified native Save dialog may receive text. No general
        # SendKeys/password typing and no arbitrary command/script execution.
        $dialogs = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
        $dialogs = @($dialogs | Where-Object { $_.Current.ProcessId -eq $qnx.Id -and
            $_.Current.ClassName -eq '#32770' -and $_.Current.Name -ceq $profile.save_title })
        if ($dialogs.Count -ne 1) { throw 'AUTO_GUI_UNAVAILABLE' }
        $dialog = $dialogs[0]
        $children = $dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)
        $file = @{stock='stock.txt';hgt='hgt.xls';sgt='sgt.xls'}[$market]
        $path = Join-Path $request.directory $file
        $edit = Field $children $profile.filename
        $value = $edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        $value.SetValue($path)
        if ($value.Current.Value -cne $path) { throw 'AUTO_GUI_UNAVAILABLE' }
        $save = Field $children $profile.save_button
        if ($save.Current.Name -cne $profile.save_button.name) { throw 'AUTO_GUI_CONTRACT_UNVERIFIED' }
        $invoke = $save.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        Check-Deadline
        $invoke.Invoke()
        $limit = [DateTimeOffset]::UtcNow.AddSeconds(20)
        $lastSize = -1
        do {
            Start-Sleep -Milliseconds 500
            Check-Deadline
            # Only observed Excel-open prompt's exact No button can be dismissed.
            $wins = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
                [System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
            foreach ($w in $wins) {
                if ($w.Current.ProcessId -eq $qnx.Id -and $w.Current.Name -ceq $profile.excel_prompt_title) {
                    $controls = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                        [System.Windows.Automation.Condition]::TrueCondition)
                    $no = Field $controls $profile.excel_no
                    if ($no.Current.Name -cne $profile.excel_no.name) { throw 'AUTO_GUI_UNAVAILABLE' }
                    $no.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
                }
            }
            $state = Controls (Current-Window)
            $size = if (Test-Path -LiteralPath $path) {(Get-Item -LiteralPath $path).Length} else {0}
            if ($size -gt 0 -and $size -eq $lastSize) { break }
            $lastSize = $size
        } while ([DateTimeOffset]::UtcNow -lt $limit)
        if ($size -le 0 -or $size -ne $lastSize) { throw 'NATIVE_BATCH_INCOMPLETE' }
        $result.markets[$market] = @{account_id=$request.account_id;complete=$true;
            query_before=$before.ToString('o');queried_at=$after.ToString('o');row_count=$rows;
            exported_at=[DateTimeOffset]::UtcNow.ToString('o');sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower();
            evidence_sha256=(Get-FileHash -LiteralPath (Join-Path $request.directory "$market.png") -Algorithm SHA256).Hash.ToLower()}
    }
    Check-Deadline
    $json = $result | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText((Join-Path $request.directory 'observations.json'),$json,(New-Object Text.UTF8Encoding($false)))
    [Console]::WriteLine('{"status":"EXPORTED"}')
} catch {
    $code = $_.Exception.Message
    if ($code -notmatch '^(AUTO_[A-Z_0-9]+|NATIVE_BATCH_INCOMPLETE)$') { $code='AUTO_GUI_UNAVAILABLE' }
    # Best effort private diagnosis, never a usable receipt. No extra query,
    # dismissal or retry on 120022/authentication/timeouts.
    if ($locked -and (Test-Path -LiteralPath $request.directory)) {
        try {
            $diagnosis = @{status='FAILED';reason_code=$code;observed_at=[DateTimeOffset]::UtcNow.ToString('o')}
            if ($window -and [WPGuojinNative]::GetForegroundWindow().ToInt64() -eq $window.Current.NativeWindowHandle) {
                $bounds = $window.Current.BoundingRectangle
                $image = New-Object Drawing.Bitmap([int]$bounds.Width,[int]$bounds.Height)
                $graphics = [Drawing.Graphics]::FromImage($image)
                try {
                    $graphics.CopyFromScreen([int]$bounds.X,[int]$bounds.Y,0,0,$image.Size)
                    $image.Save((Join-Path $request.directory 'failure.png'),[Drawing.Imaging.ImageFormat]::Png)
                } finally { $graphics.Dispose(); $image.Dispose() }
            }
            [IO.File]::WriteAllText((Join-Path $request.directory 'failure.json'),($diagnosis|ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
        } catch { } # guest watchdog still bounds a stuck UIA/provider call
    }
    [Console]::WriteLine((@{status='FAILED';reason_code=$code} | ConvertTo-Json -Compress))
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
