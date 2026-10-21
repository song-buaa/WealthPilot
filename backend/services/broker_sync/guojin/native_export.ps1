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
    Add-Type -AssemblyName WindowsBase
    Add-Type -AssemblyName System.Drawing
    Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
[StructLayout(LayoutKind.Sequential)]
public struct WPGuojinPoint { public int X; public int Y; }
public class WPGuojinNative {
 private delegate bool EnumWindowProc(IntPtr h, IntPtr parameter);
 [StructLayout(LayoutKind.Sequential)] private struct Rect { public int Left, Top, Right, Bottom; }
 [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowProc callback, IntPtr parameter);
 [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr h, out Rect rect);
 [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr h, out uint processId);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr h, StringBuilder text, int length);
 public static bool HasProcessWindow(IntPtr h, int processId) {
  uint actual; Rect rect;
  GetWindowThreadProcessId(h, out actual);
  return actual == processId && IsWindowVisible(h) && GetWindowRect(h, out rect)
   && rect.Right > rect.Left && rect.Bottom > rect.Top;
 }
 public static IntPtr[] VisibleProcessWindows(int processId) {
  var windows = new List<IntPtr>();
  if (!EnumWindows((h, parameter) => { if (HasProcessWindow(h, processId)) windows.Add(h); return true; }, IntPtr.Zero))
   return new IntPtr[0];
  return windows.ToArray();
 }
 public static string WindowTitle(IntPtr h) {
  var text = new StringBuilder(2048);
  GetWindowText(h, text, text.Capacity);
  return text.ToString();
 }
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] private static extern IntPtr GetWindow(IntPtr h, uint relation);
 public static IntPtr WindowOwner(IntPtr h) { return GetWindow(h, 4); }
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern bool GetCursorPos(out WPGuojinPoint point);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,UIntPtr e);
 [DllImport("user32.dll")] public static extern IntPtr OpenInputDesktop(uint f,bool inherit,uint access);
 [DllImport("user32.dll")] public static extern bool CloseDesktop(IntPtr h);
}
'@
    [void][WPGuojinNative]::SetProcessDPIAware()
    $guiProfile = $request.profile
    function Check-Deadline {
        if ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -ge $request.deadline) { throw 'AUTO_TIMEOUT' }
    }
    function Resolve-NativeWindow($processId, $pattern) {
        Check-Deadline
        # EnumWindows includes owned top-level frames. UIA root children can
        # omit them when a different process owns the native trading frame.
        $handles = @([WPGuojinNative]::VisibleProcessWindows($processId) | Where-Object {
            [WPGuojinNative]::WindowTitle($_) -match $pattern })
        if ($handles.Count -ne 1) { throw 'AUTO_GUI_CONTRACT_UNVERIFIED' }
        $handle = [IntPtr]$handles[0]
        try { $window = [System.Windows.Automation.AutomationElement]::FromHandle($handle) }
        catch { throw 'AUTO_GUI_CONTRACT_UNVERIFIED' }
        # UIA must still resolve the same native process/window. Ownership by
        # another process is not permission to accept that owner's UIA peer.
        if (-not $window -or $window.Current.IsOffscreen -or
            $window.Current.ProcessId -ne $processId -or
            $window.Current.NativeWindowHandle -ne $handle.ToInt64() -or
            $window.Current.Name -notmatch $pattern -or
            -not [WPGuojinNative]::HasProcessWindow($handle, $processId)) {
            throw 'AUTO_GUI_CONTRACT_UNVERIFIED'
        }
        return $window
    }
    function Resolve-SaveDialog($processId, $title, $owner) {
        # An owned Save dialog can be absent from UIA root children too.
        # Reuse native discovery and require its literal title and native owner
        # before any filename value or button receives input.
        $dialog = Resolve-NativeWindow $processId ('^' + [regex]::Escape($title) + '$')
        $handle = [IntPtr]$dialog.Current.NativeWindowHandle
        if ($dialog.Current.ClassName -cne '#32770' -or $dialog.Current.Name -cne $title -or
            [WPGuojinNative]::WindowTitle($handle) -cne $title -or
            [WPGuojinNative]::WindowOwner($handle).ToInt64() -ne $owner) {
            throw 'AUTO_GUI_CONTRACT_UNVERIFIED'
        }
        return $dialog
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
        # MainWindowHandle can identify a zero-size hidden IE helper. Re-find
        # the visible native frame on every call; never persist a runtime HWND.
        return Resolve-NativeWindow $qnx.Id $guiProfile.window_pattern
    }
    function Await-WinRT($operation, [Type]$type) {
        Check-Deadline
        $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
            $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
        $task = $method.MakeGenericMethod($type).Invoke($null,@($operation))
        if (-not $task.Wait(10000)) { throw 'AUTO_TIMEOUT' }
        return $task.Result
    }
    function Read-WindowIdentity($window) {
        # Reuse the Windows.Media.Ocr method already verified on Mini. OCR is
        # only identity/page evidence, never source time, SUCCESS or full count.
        Add-Type -AssemblyName System.Runtime.WindowsRuntime
        $bounds = $window.Current.BoundingRectangle
        $bitmap = New-Object Drawing.Bitmap([int]$bounds.Width,[int]$bounds.Height)
        $graphics = [Drawing.Graphics]::FromImage($bitmap)
        $path = Join-Path $request.directory 'identity.png'
        try {
            $graphics.CopyFromScreen([int]$bounds.X,[int]$bounds.Y,0,0,$bitmap.Size)
            $scale = [Math]::Min(1,2000.0/$bounds.Width)
            $small = New-Object Drawing.Bitmap($bitmap,[Drawing.Size]::new([int]($bounds.Width*$scale),[int]($bounds.Height*$scale)))
            try { $small.Save($path,[Drawing.Imaging.ImageFormat]::Png) } finally { $small.Dispose() }
        } finally { $graphics.Dispose();$bitmap.Dispose() }
        $file = Await-WinRT ([Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime])
        $stream = Await-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode,Windows.Storage,ContentType=WindowsRuntime]::Read)) ([Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime])
        try {
            $decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime])
            $image = Await-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime])
            try {
                $language = [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]::new('zh-Hans-CN')
                $engine = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]::TryCreateFromLanguage($language)
                if (-not $engine) { throw 'AUTO_GUI_CONTRACT_UNVERIFIED' }
                $result = Await-WinRT ($engine.RecognizeAsync($image)) ([Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime])
                return $result.Text
            } finally { $image.Dispose() }
        } finally { $stream.Dispose() }
    }
    function Controls($window) {
        # Hidden tabs can retain old account/page text. Only the currently
        # visible controls may prove owner, page or query completion.
        $nodes = @($window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition) | Where-Object { -not $_.Current.IsOffscreen })
        $names = (@($window.Current.Name) + @($nodes | ForEach-Object {$_.Current.Name})) -join "`n"
        if ($guiProfile.ocr_identity) {
            if ([WPGuojinNative]::GetForegroundWindow().ToInt64() -ne $window.Current.NativeWindowHandle) { throw 'AUTO_GUI_UNAVAILABLE' }
            $names += "`n" + (Read-WindowIdentity $window)
            $current = Current-Window
            if ($current.Current.NativeWindowHandle -ne $window.Current.NativeWindowHandle -or
                [WPGuojinNative]::GetForegroundWindow().ToInt64() -ne $window.Current.NativeWindowHandle) { throw 'AUTO_GUI_UNAVAILABLE' }
        }
        # Never dismiss broker/auth errors and continue with a cached screen.
        if ($names -match '120022|sys_status\s*=\s*6') { throw 'AUTO_COUNTER_120022' }
        if ($names -match '请输入.*密码|验证码|登录失效|重新登录') { throw 'AUTO_LOGIN_REQUIRED' }
        $all = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
        foreach ($w in $all) {
            if ($w.Current.ProcessId -eq $qnx.Id -and -not $w.Current.IsOffscreen -and $w.Current.ClassName -eq '#32770') {
                $dialogText = ($w.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                    [System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object {$_.Current.Name}) -join "`n"
                if ($dialogText -match '120022') { throw 'AUTO_COUNTER_120022' }
                if ($dialogText -match '密码|验证码|禁止|错误|失败') { throw 'AUTO_GUI_UNAVAILABLE' }
            }
        }
        $match = [regex]::Match($names,$guiProfile.account_pattern)
        if ($match.Success) {
            if ($match.Groups['account'].Value -cne $request.account_id) { throw 'AUTO_ACCOUNT_MISMATCH' }
        } elseif ($guiProfile.schema -ceq 'guojin.gui-profile.v2' -and $guiProfile.account_proof -ceq 'shareholder_allowlist') {
            $text = $names
            $matches = @($request.shareholder_accounts | Where-Object {
                $text -cmatch ('(?<![A-Za-z0-9-])'+[regex]::Escape($_)+'(?![A-Za-z0-9-])') })
            if ($matches.Count -eq 0) { throw 'AUTO_ACCOUNT_MISMATCH' }
        } else { throw 'AUTO_ACCOUNT_MISMATCH' }
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
    function Target-Matches($hit, $target) {
        if (-not $hit -or $hit.Current.IsOffscreen -or -not $hit.Current.IsEnabled -or
            $hit.Current.ProcessId -ne $qnx.Id -or $hit.Current.AutomationId -cne $target.id -or
            $hit.Current.ClassName -cne $target.class -or $hit.Current.Name -cne $target.name) { return $false }
        if ($target.control_type -and $hit.Current.ControlType.ProgrammaticName -cne ('ControlType.'+$target.control_type)) { return $false }
        return $true
    }
    function Select-StockNativeSave {
        # Observed native stock.txt menu: Item 32793 / 保存. No WM_COMMAND,
        # keyboard shortcut, or arbitrary broker menu invocation is permitted.
        $window = Current-Window
        $state = Controls $window
        if ($state.names -notmatch $guiProfile.markets.stock.page_pattern) { throw 'AUTO_GUI_UNAVAILABLE' }
        $found = @()
        $wins = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
        foreach ($w in $wins) {
            if ($w.Current.ProcessId -eq $qnx.Id -and -not $w.Current.IsOffscreen -and $w.Current.ClassName -ceq '#32768') {
                foreach ($node in $w.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                    [System.Windows.Automation.Condition]::TrueCondition)) {
                    if (Target-Matches $node $guiProfile.stock_save_menu) { $found += $node }
                }
            }
        }
        if ($found.Count -ne 1) { throw 'AUTO_GUI_CONTRACT_UNVERIFIED' }
        $bounds = $found[0].Current.BoundingRectangle
        $frame = $window.Current.BoundingRectangle
        Click-Observed @{expect=$guiProfile.markets.stock.page_pattern;target=$guiProfile.stock_save_menu;
            x=[int]($bounds.X+$bounds.Width/2-$frame.X);y=[int]($bounds.Y+$bounds.Height/2-$frame.Y)}
    }
    function Click-Observed($step) {
        $window = Current-Window
        $state = Controls $window
        if (-not $step.expect -or $state.names -notmatch $step.expect) { throw 'AUTO_GUI_UNAVAILABLE' }
        $rect = $window.Current.BoundingRectangle
        if ($rect.Width -ne $guiProfile.width -or $rect.Height -ne $guiProfile.height) { throw 'AUTO_GUI_UNAVAILABLE' }
        if ($guiProfile.dpi -and [WPGuojinNative]::GetDpiForWindow([IntPtr]$window.Current.NativeWindowHandle) -ne $guiProfile.dpi) {
            throw 'AUTO_GUI_CONTRACT_UNVERIFIED'
        }
        if ($step.x -lt 0 -or $step.y -lt 0 -or $step.x -ge $rect.Width -or $step.y -ge $rect.Height) {
            throw 'AUTO_GUI_CONTRACT_UNVERIFIED'
        }
        [void][WPGuojinNative]::SetForegroundWindow([IntPtr]$window.Current.NativeWindowHandle)
        if ([WPGuojinNative]::GetForegroundWindow().ToInt64() -ne $window.Current.NativeWindowHandle) { throw 'AUTO_GUI_UNAVAILABLE' }
        Check-Deadline
        $x = [int]($rect.X+$step.x); $y = [int]($rect.Y+$step.y)
        if (-not [WPGuojinNative]::SetCursorPos($x,$y)) { throw 'AUTO_GUI_UNAVAILABLE' }
        $point = New-Object WPGuojinPoint
        if (-not [WPGuojinNative]::GetCursorPos([ref]$point) -or $point.X -ne $x -or $point.Y -ne $y) {
            throw 'AUTO_GUI_UNAVAILABLE'
        }
        # Re-observe after moving: coordinates alone cannot authorize input.
        $current = Current-Window
        $state = Controls $current
        if ($current.Current.NativeWindowHandle -ne $window.Current.NativeWindowHandle -or
            [WPGuojinNative]::GetForegroundWindow().ToInt64() -ne $window.Current.NativeWindowHandle -or
            $state.names -notmatch $step.expect) { throw 'AUTO_GUI_UNAVAILABLE' }
        if (-not $step.target.id -or (-not $step.target.class -and $step.target.control_type -cne 'MenuItem') -or -not $step.target.name) {
            throw 'AUTO_GUI_CONTRACT_UNVERIFIED'
        }
        $hit = [System.Windows.Automation.AutomationElement]::FromPoint([System.Windows.Point]::new($x,$y))
        if (-not (Target-Matches $hit $step.target)) {
            throw 'AUTO_GUI_UNAVAILABLE'
        }
        Check-Deadline
        if ([WPGuojinNative]::GetForegroundWindow().ToInt64() -ne $window.Current.NativeWindowHandle -or
            -not [WPGuojinNative]::GetCursorPos([ref]$point) -or $point.X -ne $x -or $point.Y -ne $y) {
            throw 'AUTO_GUI_UNAVAILABLE'
        }
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
    if (-not $guiProfile.verified_readonly -or -not $guiProfile.evidence_ref) { throw 'AUTO_GUI_CONTRACT_UNVERIFIED' }
    if (Test-Path -LiteralPath $request.directory) { throw 'AUTO_EVIDENCE_INVALID' }
    $null = New-Item -ItemType Directory -Path $request.directory
    $observedSource = $guiProfile.schema -ceq 'guojin.gui-profile.v2'
    $schema = if ($observedSource) {'guojin.gui-observations.v2'} else {'guojin.gui-observations.v1'}
    $result = @{schema=$schema;status='EXPORTED';batch=$request.batch;markets=@{}}
    foreach ($market in @('stock','hgt','sgt')) {
        $spec = $guiProfile.markets.$market
        foreach ($step in $spec.open) { Click-Observed $step }
        $state = Controls (Current-Window)
        if (-not $spec.page_pattern -or $state.names -notmatch $spec.page_pattern) { throw 'AUTO_GUI_UNAVAILABLE' }
        if ($observedSource) {
            # The actual HK pages expose no authoritative query time/SUCCESS or
            # total count. Observe the guarded action, never manufacture them.
            $actionTime = [DateTimeOffset]::UtcNow
            Click-Observed $spec.refresh
            Start-Sleep -Milliseconds 1000
            $window = Current-Window
            $state = Controls $window
            if ($state.names -notmatch $spec.page_pattern) { throw 'AUTO_GUI_UNAVAILABLE' }
            $observationTime = [DateTimeOffset]::UtcNow
            $dpi = [int][WPGuojinNative]::GetDpiForWindow([IntPtr]$window.Current.NativeWindowHandle)
            if ($dpi -ne $guiProfile.dpi) { throw 'AUTO_GUI_CONTRACT_UNVERIFIED' }
        } else {
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
        }
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
        if ($observedSource -and $market -ceq 'stock') { Select-StockNativeSave }
        Check-Deadline
        # Only the verified native Save dialog may receive text. No general
        # SendKeys/password typing and no arbitrary command/script execution.
        $dialog = Resolve-SaveDialog $qnx.Id $guiProfile.save_title $window.Current.NativeWindowHandle
        $children = $dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)
        $file = @{stock='stock.txt';hgt='hgt.xls';sgt='sgt.xls'}[$market]
        $path = Join-Path $request.directory $file
        $edit = Field $children $guiProfile.filename
        $value = $edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        $value.SetValue($path)
        if ($value.Current.Value -cne $path) { throw 'AUTO_GUI_UNAVAILABLE' }
        $save = Field $children $guiProfile.save_button
        if ($save.Current.Name -cne $guiProfile.save_button.name) { throw 'AUTO_GUI_CONTRACT_UNVERIFIED' }
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
                if ($w.Current.ProcessId -eq $qnx.Id -and $w.Current.Name -ceq $guiProfile.excel_prompt_title) {
                    $controls = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                        [System.Windows.Automation.Condition]::TrueCondition)
                    $no = Field $controls $guiProfile.excel_no
                    if ($no.Current.Name -cne $guiProfile.excel_no.name) { throw 'AUTO_GUI_UNAVAILABLE' }
                    $no.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
                }
            }
            $state = Controls (Current-Window)
            $size = if (Test-Path -LiteralPath $path) {(Get-Item -LiteralPath $path).Length} else {0}
            if ($size -gt 0 -and $size -eq $lastSize) { break }
            $lastSize = $size
        } while ([DateTimeOffset]::UtcNow -lt $limit)
        if ($size -le 0 -or $size -ne $lastSize) { throw 'NATIVE_BATCH_INCOMPLETE' }
        if ($observedSource) {
            $current = Current-Window
            $state = Controls $current
            if ($current.Current.NativeWindowHandle -ne $window.Current.NativeWindowHandle -or
                $state.names -notmatch $spec.page_pattern) { throw 'AUTO_GUI_UNAVAILABLE' }
            $result.markets[$market] = @{account_id=$request.account_id;complete=$false;queried_at=$null;
                source_time_quality='UNKNOWN';counter_status='UNKNOWN';row_count=$null;row_count_basis='EXPORTED_ROWS';
                query_action_at=$actionTime.ToString('o');observed_at=$observationTime.ToString('o');
                query_evidence=@{page_verified=$true;account_verified=$true;readonly_action_verified=$true;
                    pid=$qnx.Id;hwnd=$current.Current.NativeWindowHandle;dpi=$dpi};
                exported_at=[DateTimeOffset]::UtcNow.ToString('o');sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower();
                evidence_sha256=(Get-FileHash -LiteralPath (Join-Path $request.directory "$market.png") -Algorithm SHA256).Hash.ToLower()}
        } else {
        $result.markets[$market] = @{account_id=$request.account_id;complete=$true;
            query_before=$before.ToString('o');queried_at=$after.ToString('o');row_count=$rows;
            exported_at=[DateTimeOffset]::UtcNow.ToString('o');sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower();
            evidence_sha256=(Get-FileHash -LiteralPath (Join-Path $request.directory "$market.png") -Algorithm SHA256).Hash.ToLower()}
        }
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
