# Isolated native-helper tests. Fake UIA/user32 only. No desktop/broker calls.
param([switch]$CompileOnly,[switch]$PortableFake)
$ErrorActionPreference='Stop'
$fake=@'
namespace System.Windows { public struct Point{public double X,Y;public Point(double x,double y){X=x;Y=y;}} }
namespace System.Windows.Automation {
 public enum TreeScope{Children,Descendants} public class Condition{public static Condition TrueCondition=new Condition();}
 public struct Rect{public double X,Y,Width,Height;public static bool operator ==(Rect a,Rect b){return a.X==b.X&&a.Y==b.Y&&a.Width==b.Width&&a.Height==b.Height;}public static bool operator !=(Rect a,Rect b){return !(a==b);}public override bool Equals(object b){return b is Rect&&(Rect)b==this;}public override int GetHashCode(){return X.GetHashCode();}}
 public class ControlType{public static ControlType TreeItem=new ControlType(),TitleBar=new ControlType(),MenuItem=new ControlType();}
 public class State{public string Name="",ClassName="",AutomationId="";public int ProcessId=7,NativeWindowHandle=10;public bool IsOffscreen=false,IsEnabled=true;public ControlType ControlType=new ControlType();public Rect BoundingRectangle=new Rect{X=-13,Y=-13,Width=3866,Height=1774};}
 public class SelectionItemPattern{public static object Pattern=new object();public Selected Current=new Selected();public class Selected{public bool IsSelected=true;}}
 public class ValuePattern{public static object Pattern=new object();public ValueState Current=new ValueState();public class ValueState{public string Value="";}public void SetValue(string v){Current.Value=v;}}
 public class InvokePattern{public static object Pattern=new object();public void Invoke(){}}
 public class TreeWalker{public static TreeWalker ControlViewWalker=new TreeWalker();public AutomationElement GetParent(AutomationElement n){return n.Parent;}}
 public class AutomationElement{public static AutomationElement Frame,Hit,RootElement=new AutomationElement();public State Current=new State();public AutomationElement Parent;public List<AutomationElement> Children=new List<AutomationElement>();public static AutomationElement FromHandle(IntPtr h){return Frame;}public static AutomationElement FromPoint(System.Windows.Point p){return Hit;}public AutomationElement[] FindAll(TreeScope s,Condition c){return Children.ToArray();}public bool TryGetCurrentPattern(object p,out object o){o=new SelectionItemPattern();return true;}public object GetCurrentPattern(object p){return p==ValuePattern.Pattern?(object)new ValuePattern():new InvokePattern();}}
}
public struct WPGuojinPoint{public int X,Y;}
public class WPGuojinNative{
 public static int Clicks=0,X,Y;public static bool Move=true,Clip=false,Read=true,Steal=false,Native=true;public static long Foreground=10;
 public static bool HasProcessWindow(IntPtr h,int p){return Native;}public static uint GetDpiForWindow(IntPtr h){return 192;}
 public static bool SetForegroundWindow(IntPtr h){Foreground=10;return true;}public static IntPtr GetForegroundWindow(){return new IntPtr(Foreground);}
 public static bool SetCursorPos(int x,int y){X=Clip?x-1:x;Y=y;if(Steal)Foreground=99;return Move;}public static bool GetCursorPos(out WPGuojinPoint p){p=new WPGuojinPoint{X=X,Y=Y};return Read;}
 public static void mouse_event(uint f,uint x,uint y,uint d,UIntPtr p){Clicks++;}public static IntPtr[] VisibleProcessWindows(int p){return new IntPtr[0];}public static string WindowTitle(IntPtr h){return "";}public static IntPtr WindowOwner(IntPtr h){return new IntPtr(10);}
}
'@
$source=Get-Content -Raw (Join-Path $PSScriptRoot '../backend/services/broker_sync/guojin/native_gui.cs')
# Non-Windows has no COM apartment support. Only this fake-only harness can
# omit the apartment selection; production source and Windows tests retain it.
if($PortableFake){$source=$source.Replace('thread.SetApartmentState(ApartmentState.MTA);','')}
elseif(!$CompileOnly -and [Environment]::OSVersion.Platform -ne 'Win32NT'){throw 'Run on Windows, or explicitly select PortableFake'}
# Using directives must precede the synthetic declarations in the same assembly.
$using=($source -split "`n"|Where-Object{$_ -match '^using '}) -join "`n"
$body=($source -split "`n"|Where-Object{$_ -notmatch '^using '}) -join "`n"
Add-Type -TypeDefinition ($using+"`n"+$fake+"`n"+$body)
if($CompileOnly){'Native helper compile: PASS (synthetic types only)';exit}
function N($name,$id='',$class=''){$n=New-Object System.Windows.Automation.AutomationElement;$n.Current.Name=$name;$n.Current.AutomationId=$id;$n.Current.ClassName=$class;return $n}
$frame=N '国金全能行远航版';[System.Windows.Automation.AutomationElement]::Frame=$frame
$owner=N 'synthetic-owner' '1004' 'ComboBox';$frame.Children.Add($owner)
$tree=N '' '129' 'SysTreeView32';$branch=N '查询[F4]';$branch.Current.ControlType=[System.Windows.Automation.ControlType]::TreeItem;$branch.Parent=$tree
$query=N '资金股票';$query.Current.ControlType=[System.Windows.Automation.ControlType]::TreeItem;$query.Parent=$branch;$frame.Children.Add($query)
[System.Windows.Automation.AutomationElement]::Hit=$query
$passed=0
function Call { [WPGuojinReadonly]::Click(10,7,3866,1774,192,[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()+60,@('synthetic-owner'),'query','stock') }
function Fail($code){$before=[WPGuojinNative]::Clicks;try{Call;throw 'MISSING_FAILURE'}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};if($e.Message -cne $code){throw $_}};if([WPGuojinNative]::Clicks -ne $before){throw 'UNSAFE_INPUT'};$script:passed++}
Call;if([WPGuojinNative]::Clicks -ne 2){throw 'NORMAL_CLICK_FAILED'};$passed++
[WPGuojinNative]::Move=$false;Fail 'AUTO_GUI_UNAVAILABLE';[WPGuojinNative]::Move=$true
[WPGuojinNative]::Clip=$true;Fail 'AUTO_GUI_UNAVAILABLE';[WPGuojinNative]::Clip=$false
[WPGuojinNative]::Read=$false;Fail 'AUTO_GUI_UNAVAILABLE';[WPGuojinNative]::Read=$true
[WPGuojinNative]::Steal=$true;Fail 'AUTO_GUI_UNAVAILABLE';[WPGuojinNative]::Steal=$false
$owner.Current.Name='other-owner';Fail 'AUTO_ACCOUNT_MISMATCH';$owner.Current.Name='synthetic-owner'
$query.Current.Name='买入';Fail 'AUTO_GUI_CONTRACT_UNVERIFIED';$query.Current.Name='资金股票'
$branch.Current.Name='交易';Fail 'AUTO_GUI_CONTRACT_UNVERIFIED';$branch.Current.Name='查询[F4]'
[System.Windows.Automation.AutomationElement]::Hit=N '买入';Fail 'AUTO_GUI_UNAVAILABLE'
[System.Windows.Automation.AutomationElement]::Hit=$query
$query.Current.ProcessId=99;Fail 'AUTO_GUI_CONTRACT_UNVERIFIED';$query.Current.ProcessId=7
$query.Current.IsOffscreen=$true;Fail 'AUTO_GUI_CONTRACT_UNVERIFIED';$query.Current.IsOffscreen=$false
[WPGuojinNative]::Native=$false;Fail 'AUTO_GUI_CONTRACT_UNVERIFIED';[WPGuojinNative]::Native=$true
$frame.Current.BoundingRectangle=[System.Windows.Automation.Rect]@{X=-13;Y=-13;Width=100;Height=100};Fail 'AUTO_GUI_CONTRACT_UNVERIFIED'
"Native helper safety probes: $passed PASS; synthetic types, no desktop access"
