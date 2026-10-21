# Portable isolated probes against production function ASTs. No user32 or Windows GUI calls.
$ErrorActionPreference='Stop'
$source=Join-Path $PSScriptRoot '../backend/services/broker_sync/guojin/native_export.ps1'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'PARSE_FAILED'}
Add-Type @'
using System;
using System.Collections.Generic;
namespace System.Windows { public struct Point{public double X,Y; public Point(double x,double y){X=x;Y=y;}} }
namespace System.Windows.Automation {
 public enum TreeScope{Children,Descendants}
 public class Condition{ public static Condition TrueCondition=new Condition(); }
 public class NodeState{public string Name="",ClassName="",AutomationId="";public int ProcessId=7,NativeWindowHandle=10;public bool IsOffscreen=false,IsEnabled=true;public Rect BoundingRectangle=new Rect();}
 public class Rect{public double X=10,Y=20,Width=100,Height=100;}
 public class AutomationElement{
  public static AutomationElement RootElement=new AutomationElement();
  public static AutomationElement Hit; public static AutomationElement FromPoint(System.Windows.Point p){return Hit;}
  public NodeState Current=new NodeState();
  public List<AutomationElement> Children=new List<AutomationElement>();
  public AutomationElement[] FindAll(TreeScope scope,Condition c){return Children.ToArray();}
 }
}
public struct WPGuojinPoint {public int X,Y;}
public class WPGuojinNative {
 public static long Foreground=10; public static int X,Y,Clicks; public static bool Move=true,Read=true,Clip=false,StealFocus=false;
 public static bool SetForegroundWindow(IntPtr p){return true;}
 public static IntPtr GetForegroundWindow(){return new IntPtr(Foreground);}
 public static bool SetCursorPos(int x,int y){if(!Move)return false;X=Clip?x-1:x;Y=y;if(StealFocus)Foreground=999;return true;}
 public static bool GetCursorPos(out WPGuojinPoint p){p=new WPGuojinPoint{X=X,Y=Y};return Read;}
 public static void mouse_event(uint f,uint x,uint y,uint d,UIntPtr p){Clicks++;}
}
'@
foreach($definition in $ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]},$true)){
 Invoke-Expression $definition.Extent.Text
}
function Node($text,$id='0',$class='Static'){
 $n=New-Object System.Windows.Automation.AutomationElement
 $n.Current.Name=$text;$n.Current.AutomationId=$id;$n.Current.ClassName=$class;return $n
}
$script:window=Node 'Guojin synthetic'
$window.Children.Add((Node 'Account:synthetic'))
$window.Children.Add((Node '42' 'rows'))
$guiProfile=@{account_pattern='Account:(?<account>\S+)';width=100;height=100}
$request=@{account_id='synthetic';deadline=([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()+120)}
$qnx=@{Id=7}
# Override enumeration only, never the actual guards under test.
function Current-Window{return $script:window}
$script:passed=0
$target=@{id='readonly';class='Button';name='Read-only refresh'}
[System.Windows.Automation.AutomationElement]::Hit=Node 'Read-only refresh' 'readonly' 'Button'
$step=@{expect='Account:synthetic';x=5;y=6;target=$target}

function Expect($code,$action){
 try{& $action;throw 'EXPECTED_FAILURE_MISSING'}catch{
  if($_.Exception.Message -cne $code){throw $_}
  $script:passed++
 }
}
if((Source-Time '2026-10-21 17:30:00').ToUniversalTime().ToString('HH:mm') -ne '09:30'){throw 'SOURCE_TIME_CHANGED'}
$passed++
$selector=@{id='rows';class='Static';pattern='^(?<value>[0-9]+)$'}
$state=Controls $window
if((Read-Field $state.nodes $selector) -ne '42'){throw 'COUNT_READ_FAILED'}
$passed++
$request.account_id='other';Expect 'AUTO_ACCOUNT_MISMATCH' {Controls $window};$request.account_id='synthetic'
$window.Children[0].Current.Name='No account';Expect 'AUTO_ACCOUNT_MISMATCH' {Controls $window}
$window.Children[0].Current.Name='Account:synthetic'
$window.Children[0].Current.IsOffscreen=$true
$window.Children.Add((Node 'Account:another'))
Expect 'AUTO_ACCOUNT_MISMATCH' {Controls $window}
$window.Children.RemoveAt(2);$window.Children[0].Current.IsOffscreen=$false
$window.Children.Add((Node '120022 sys_status=6'));Expect 'AUTO_COUNTER_120022' {Controls $window};$window.Children.RemoveAt(2)
$window.Children.Add((Node '请输入交易密码'));Expect 'AUTO_LOGIN_REQUIRED' {Controls $window};$window.Children.RemoveAt(2)
$window.Children.Add((Node '42' 'rows'));Expect 'AUTO_GUI_UNAVAILABLE' {Read-Field $window.Children.ToArray() $selector};$window.Children.RemoveAt(2)
Expect 'AUTO_GUI_UNAVAILABLE' {Read-Field $window.Children.ToArray() @{id='missing';class='Static';pattern='(?<value>.*)'}}
Click-Observed $step
if([WPGuojinNative]::X -ne 15 -or [WPGuojinNative]::Y -ne 26 -or [WPGuojinNative]::Clicks -ne 2){throw 'NATIVE_COORDINATES_CHANGED'}
$passed++
Expect 'AUTO_GUI_UNAVAILABLE' {Click-Observed @{expect='Unexpected page';x=5;y=6;target=$target}}
$window.Current.BoundingRectangle.Width=200
Expect 'AUTO_GUI_UNAVAILABLE' {Click-Observed $step}
$window.Current.BoundingRectangle.Width=100
[WPGuojinNative]::Foreground=999
Expect 'AUTO_GUI_UNAVAILABLE' {Click-Observed $step}
[WPGuojinNative]::Foreground=10
$request.deadline=0
Expect 'AUTO_TIMEOUT' {Check-Deadline}
if([WPGuojinNative]::Clicks -ne 2){throw 'FAILED_GUARD_SENT_INPUT'}


$request.deadline=([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()+120)
function No-Click($action){
 $before=[WPGuojinNative]::Clicks
 Expect 'AUTO_GUI_UNAVAILABLE' $action
 if([WPGuojinNative]::Clicks -ne $before){throw 'FAILED_GUARD_SENT_INPUT'}
}
[WPGuojinNative]::Move=$false
No-Click {Click-Observed $step}
[WPGuojinNative]::Move=$true;[WPGuojinNative]::Clip=$true
No-Click {Click-Observed $step}
[WPGuojinNative]::Clip=$false;[WPGuojinNative]::Read=$false
No-Click {Click-Observed $step}
[WPGuojinNative]::Read=$true;[WPGuojinNative]::StealFocus=$true
No-Click {Click-Observed $step}
[WPGuojinNative]::StealFocus=$false;[WPGuojinNative]::Foreground=10
$hit=[System.Windows.Automation.AutomationElement]::Hit
$hit.Current.AutomationId='trade-button'
No-Click {Click-Observed $step}
$hit.Current.AutomationId='readonly';$hit.Current.ProcessId=999
No-Click {Click-Observed $step}
$hit.Current.ProcessId=7;$hit.Current.IsEnabled=$false
No-Click {Click-Observed $step}
$hit.Current.IsEnabled=$true;$hit.Current.IsOffscreen=$true
No-Click {Click-Observed $step}
$hit.Current.IsOffscreen=$false
$window.Children.Add((Node '120022 sys_status=6'))
$before=[WPGuojinNative]::Clicks
Expect 'AUTO_COUNTER_120022' {Click-Observed $step}
if([WPGuojinNative]::Clicks -ne $before){throw 'COUNTER_REJECTION_SENT_INPUT'}
$window.Children.RemoveAt(2)
# A page change after movement is re-observed before emitting input.
$script:windowChecks=0
function Current-Window{
 $script:windowChecks++
 if($script:windowChecks -eq 2){$window.Children[0].Current.Name='Account:synthetic different-page'}
 return $script:window
}
$oldExpect=$step.expect;$step.expect='^Account:synthetic$'
# Names include title, so use an exact newline-delimited page token.
$step.expect='Account:synthetic\n42'
No-Click {Click-Observed $step}
$step.expect=$oldExpect;$window.Children[0].Current.Name='Account:synthetic'
function Current-Window{return $script:window}
$before=[WPGuojinNative]::Clicks
Click-Observed $step
if([WPGuojinNative]::Clicks -ne $before+2){throw 'NORMAL_CLICK_FAILED'}
$passed++
"Portable production-function probes: $passed PASS; fake nodes/native calls only, no desktop accessed"
