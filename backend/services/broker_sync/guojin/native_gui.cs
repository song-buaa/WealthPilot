// QNX's verified native read-only path. UIA providers must be called on a
// dedicated MTA thread (the PowerShell pipeline omits native tree/tab peers).
using System;
using System.Collections.Generic;
using System.Text;
using System.Threading;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Automation;

public static class WPGuojinReadonly {
    static readonly ThreadLocal<CancellationToken> Stop = new ThreadLocal<CancellationToken>();
    static T Mta<T>(Func<T> work) {
        T result = default(T); Exception error = null;
        var cancellation = new CancellationTokenSource(); var token = cancellation.Token;
        var thread = new Thread(() => { Stop.Value=token; try { result = work(); } catch (Exception e) { error = e; } });
        thread.IsBackground = true; thread.SetApartmentState(ApartmentState.MTA); thread.Start();
        // The existing guest watchdog owns this process. A timed-out operation
        // cannot proceed to input: every input rechecks the absolute deadline.
        if (!thread.Join(12000)) { cancellation.Cancel(); throw new Exception("AUTO_TIMEOUT"); }
        cancellation.Dispose();
        if (error != null) throw error;
        return result;
    }
    static void Deadline(long deadline) {
        if (Stop.Value.IsCancellationRequested || DateTimeOffset.UtcNow.ToUnixTimeSeconds() >= deadline) throw new Exception("AUTO_TIMEOUT");
    }
    static Exception Contract(string field, string actual, string expected) {
        var error = new Exception("AUTO_GUI_CONTRACT_UNVERIFIED");
        error.Data["field"] = field; error.Data["actual"] = actual; error.Data["expected"] = expected;
        return error;
    }
    static AutomationElement Frame(long hwnd, int pid, int width, int height, int dpi, long deadline) {
        Deadline(deadline);
        var w = AutomationElement.FromHandle(new IntPtr(hwnd)); var a = w.Current;
        if (a.ProcessId != pid || a.NativeWindowHandle != hwnd || a.IsOffscreen ||
            a.Name != "国金全能行远航版" || a.BoundingRectangle.Width != width || a.BoundingRectangle.Height != height ||
            WPGuojinNative.GetDpiForWindow(new IntPtr(hwnd)) != dpi ||
            !WPGuojinNative.HasProcessWindow(new IntPtr(hwnd), pid))
            throw Contract("frame", a.ProcessId+"/"+a.NativeWindowHandle+"/"+a.Name+"/"+a.IsOffscreen+"/"+a.BoundingRectangle+"/"+WPGuojinNative.GetDpiForWindow(new IntPtr(hwnd)),
                pid+"/"+hwnd+"/国金全能行远航版/False/"+width+"x"+height+"/"+dpi);
        return w;
    }
    static AutomationElement[] Nodes(AutomationElement w) {
        var result = new List<AutomationElement>();
        foreach (AutomationElement n in w.FindAll(TreeScope.Descendants, Condition.TrueCondition))
            if (!n.Current.IsOffscreen) result.Add(n);
        return result.ToArray();
    }
    public static string Names(long hwnd) { return Mta(() => {
        var b = new StringBuilder();
        foreach (var n in Nodes(AutomationElement.FromHandle(new IntPtr(hwnd)))) b.AppendLine(n.Current.Name);
        return b.ToString();
    }); }
    static bool Visible(AutomationElement n, int pid) {
        return n != null && n.Current.ProcessId == pid && !n.Current.IsOffscreen && n.Current.IsEnabled;
    }
    static string Parent(AutomationElement n) {
        var p = TreeWalker.ControlViewWalker.GetParent(n); return p == null ? "" : p.Current.Name;
    }
    static bool Tree(AutomationElement n, int pid, string name, string parent) {
        if (!Visible(n, pid) || n.Current.ControlType != ControlType.TreeItem || n.Current.Name != name ||
            (parent != "" && Parent(n) != parent)) return false;
        var p = TreeWalker.ControlViewWalker.GetParent(n);
        while (p != null && p.Current.ControlType == ControlType.TreeItem) p = TreeWalker.ControlViewWalker.GetParent(p);
        return p != null && p.Current.ProcessId == pid && p.Current.ClassName == "SysTreeView32";
    }
    static string Branch(string market) {
        if (market == "stock") return "查询[F4]";
        if (market == "hgt") return "沪港通查询";
        if (market == "sgt") return "深港通查询";
        throw new Exception("AUTO_GUI_CONTRACT_UNVERIFIED");
    }
    static bool Selected(AutomationElement n) {
        object pattern; return n.TryGetCurrentPattern(SelectionItemPattern.Pattern, out pattern) &&
            ((SelectionItemPattern)pattern).Current.IsSelected;
    }
    public static bool Page(long hwnd, int pid, string market) { return Mta(() => {
        foreach (var n in Nodes(AutomationElement.FromHandle(new IntPtr(hwnd))))
            if (Tree(n, pid, "资金股票", Branch(market)) && Selected(n)) return true;
        return false;
    }); }
    static AutomationElement Unique(List<AutomationElement> nodes) {
        if (nodes.Count != 1) throw new Exception("AUTO_GUI_CONTRACT_UNVERIFIED"); return nodes[0];
    }
    public static bool HasQuery(long hwnd, int pid, string market) { return Mta(() => {
        int count = 0; foreach (var n in Nodes(AutomationElement.FromHandle(new IntPtr(hwnd))))
            if (Tree(n, pid, "资金股票", Branch(market))) count++;
        return count == 1;
    }); }
    public static bool DismissExcel(long hwnd, int pid, int width, int height, int dpi, long deadline) { return Mta(() => {
        Frame(hwnd,pid,width,height,dpi,deadline);
        var dialogs = new List<AutomationElement>();
        foreach(var h in WPGuojinNative.VisibleProcessWindows(pid)) {
            if(WPGuojinNative.WindowOwner(h).ToInt64()!=hwnd) continue;
            var d=AutomationElement.FromHandle(h);
            if(Visible(d,pid) && d.Current.ClassName=="#32770") dialogs.Add(d);
        }
        if(dialogs.Count==0) return false;
        var dialog=Unique(dialogs); var buttons=new List<AutomationElement>(); bool evidence=false;
        foreach(var n in Nodes(dialog)) {
            if(n.Current.AutomationId=="1040" && n.Current.ClassName=="Static" &&
               Regex.Replace(n.Current.Name,@"\s","")=="Excel导出成功是否需要直接打开Excel?") evidence=true;
            if(Visible(n,pid) && n.Current.AutomationId=="7" && n.Current.ClassName=="Button" && n.Current.Name=="否(N)") buttons.Add(n);
        }
        if(!evidence) throw new Exception("AUTO_GUI_UNAVAILABLE");
        var no=Unique(buttons); var handle=new IntPtr(dialog.Current.NativeWindowHandle);
        WPGuojinNative.SetForegroundWindow(handle);
        Frame(hwnd,pid,width,height,dpi,deadline);
        if(WPGuojinNative.WindowOwner(handle).ToInt64()!=hwnd || WPGuojinNative.GetForegroundWindow()!=handle)
            throw new Exception("AUTO_GUI_UNAVAILABLE");
        ((InvokePattern)no.GetCurrentPattern(InvokePattern.Pattern)).Invoke(); return true;
    }); }
    public static void Save(long hwnd, int pid, int width, int height, int dpi, long deadline, string path) { Mta(() => {
        Frame(hwnd,pid,width,height,dpi,deadline);
        var dialogs = new List<AutomationElement>();
        foreach (var h in WPGuojinNative.VisibleProcessWindows(pid)) {
            if (WPGuojinNative.WindowTitle(h)!="另存为" || WPGuojinNative.WindowOwner(h).ToInt64()!=hwnd) continue;
            var d=AutomationElement.FromHandle(h);
            if (Visible(d,pid) && d.Current.ClassName=="#32770" && d.Current.Name=="另存为") dialogs.Add(d);
        }
        var dialog=Unique(dialogs); var edits=new List<AutomationElement>(); var buttons=new List<AutomationElement>();
        foreach(var n in Nodes(dialog)) {
            if(n.Current.AutomationId=="1001" && n.Current.ClassName=="Edit") edits.Add(n);
            if(n.Current.AutomationId=="1" && n.Current.ClassName=="Button" && n.Current.Name=="保存(S)") buttons.Add(n);
        }
        var edit=Unique(edits); var save=Unique(buttons);
        if (!Visible(edit,pid) || !Visible(save,pid)) throw new Exception("AUTO_GUI_CONTRACT_UNVERIFIED");
        WPGuojinNative.SetForegroundWindow(new IntPtr(dialog.Current.NativeWindowHandle));
        if(WPGuojinNative.GetForegroundWindow().ToInt64()!=dialog.Current.NativeWindowHandle) throw new Exception("AUTO_GUI_UNAVAILABLE");
        Deadline(deadline); var value=(ValuePattern)edit.GetCurrentPattern(ValuePattern.Pattern); value.SetValue(path);
        if(value.Current.Value!=path) throw new Exception("AUTO_GUI_UNAVAILABLE");
        Frame(hwnd,pid,width,height,dpi,deadline);
        if(WPGuojinNative.WindowOwner(new IntPtr(dialog.Current.NativeWindowHandle)).ToInt64()!=hwnd ||
           WPGuojinNative.GetForegroundWindow().ToInt64()!=dialog.Current.NativeWindowHandle) throw new Exception("AUTO_GUI_UNAVAILABLE");
        ((InvokePattern)save.GetCurrentPattern(InvokePattern.Pattern)).Invoke(); return true;
    }); }
    public static void Click(long hwnd, int pid, int width, int height, int dpi, long deadline,
                             string[] owners, string kind, string market) { Mta(() => {
        var w = Frame(hwnd, pid, width, height, dpi, deadline); var nodes = Nodes(w);
        // Reuse the explicit shareholder allowlist; never fill a source identity
        // from target configuration. No login/password or transaction controls.
        int ownerCount = 0;
        foreach (var n in nodes) if (n.Current.ClassName == "ComboBox" &&
            (n.Current.AutomationId == "1003" || n.Current.AutomationId == "1004") &&
            Array.IndexOf(owners, n.Current.Name) >= 0) ownerCount++;
        if (ownerCount != 1) throw new Exception("AUTO_ACCOUNT_MISMATCH");
        var found = new List<AutomationElement>(); double x = 0, y = 0; bool right = kind == "grid";
        string id = "", name = "", cls = "", parent = "";
        if (kind == "channel") {
            foreach (var n in nodes) if (Visible(n,pid) && n.Current.AutomationId == "1001" && n.Current.ClassName == "CCustomTabCtrl") found.Add(n);
            if (found.Count != 1) throw Contract("channel.count", found.Count.ToString(), "1");
            var tab = Unique(found); var b = tab.Current.BoundingRectangle;
            // Actual native tab region verified on Mini; layout changes fail
            // closed. These are read-only channel tabs, never order controls.
            if (b.X != 1 || b.Y != 1623 || b.Width != 340 || b.Height != 80) throw Contract("channel.bounds", b.ToString(), "1,1623,340,80");
            x = b.X + (market == "stock" ? 39 : 139); y = b.Y + (market == "stock" ? 20 : 60);
            id = "1001"; cls = "CCustomTabCtrl";
        } else if (kind == "query" || kind == "branch") {
            name = kind == "query" ? "资金股票" : Branch(market); parent = kind == "query" ? Branch(market) : "";
            foreach (var n in nodes) if (Tree(n,pid,name,parent)) found.Add(n);
        } else if (kind == "gear") {
            var b = w.Current.BoundingRectangle; x = b.X+3625; y = b.Y+32;
            id = "TitleBar"; name = "国金全能行远航版";
        } else if (kind == "grid") {
            foreach (var n in nodes) if (Visible(n,pid) && n.Current.AutomationId == "100" && n.Current.ClassName == "CVirtualGridCtrl" && n.Current.Name == "") found.Add(n);
            id = "100"; cls = "CVirtualGridCtrl";
        } else if (kind == "refresh" || kind == "save_stock" || kind == "save_hk") {
            id = kind == "refresh" ? "Item 32833" : kind == "save_stock" ? "Item 32793" : "Item 32827";
            name = kind == "refresh" ? "刷新" : kind == "save_stock" ? "保存" : "保存(S)...";
            foreach (AutomationElement top in AutomationElement.RootElement.FindAll(TreeScope.Children,Condition.TrueCondition))
                if (Visible(top,pid) && top.Current.ClassName == "#32768") foreach (var n in Nodes(top))
                    if (Visible(n,pid) && n.Current.AutomationId == id && n.Current.Name == name && n.Current.ControlType == ControlType.MenuItem) found.Add(n);
        } else throw new Exception("AUTO_GUI_CONTRACT_UNVERIFIED");
        AutomationElement target = null;
        if (found.Count > 0) { target = Unique(found); if (kind != "channel") {var b = target.Current.BoundingRectangle; x=b.X+b.Width/2; y=b.Y+b.Height/2;} }
        else if (kind != "gear") {
            var observed = new StringBuilder();
            if (kind == "branch" || kind == "query") foreach (var n in nodes)
                if (n.Current.ControlType == ControlType.TreeItem && (n.Current.Name.Contains("查询") || n.Current.Name == "资金股票"))
                    observed.Append("["+n.Current.Name+";parent="+Parent(n)+"]");
            throw Contract("target."+kind, "count=0;market="+market+";readonly_nodes="+observed, "unique "+name+";parent="+parent);
        }
        if (kind != "channel" && kind != "query" && kind != "branch") {
            bool page = false; foreach (var n in nodes) if (Tree(n,pid,"资金股票",Branch(market)) && Selected(n)) page=true;
            if (!page) throw new Exception("AUTO_GUI_UNAVAILABLE");
        }
        WPGuojinNative.SetForegroundWindow(new IntPtr(hwnd));
        if (WPGuojinNative.GetForegroundWindow().ToInt64()!=hwnd) throw new Exception("AUTO_GUI_UNAVAILABLE");
        int ix=(int)x, iy=(int)y; WPGuojinPoint point;
        if (!WPGuojinNative.SetCursorPos(ix,iy) || !WPGuojinNative.GetCursorPos(out point) || point.X!=ix || point.Y!=iy) throw new Exception("AUTO_GUI_UNAVAILABLE");
        var hit=AutomationElement.FromPoint(new Point(ix,iy));
        if (!Visible(hit,pid)) throw new Exception("AUTO_GUI_UNAVAILABLE");
        if (kind=="query" || kind=="branch") { if (!Tree(hit,pid,name,parent)) throw new Exception("AUTO_GUI_UNAVAILABLE"); }
        else if (hit.Current.AutomationId!=id || hit.Current.ClassName!=cls ||
                 (kind!="channel" && hit.Current.Name!=name) ||
                 (kind=="gear" && hit.Current.ControlType!=ControlType.TitleBar) ||
                 (kind.StartsWith("save_") || kind=="refresh") && hit.Current.ControlType!=ControlType.MenuItem)
            throw new Exception("AUTO_GUI_UNAVAILABLE");
        if (target!=null && hit.Current.BoundingRectangle!=target.Current.BoundingRectangle) throw new Exception("AUTO_GUI_UNAVAILABLE");
        Frame(hwnd,pid,width,height,dpi,deadline);
        if (WPGuojinNative.GetForegroundWindow().ToInt64()!=hwnd || !WPGuojinNative.GetCursorPos(out point) || point.X!=ix || point.Y!=iy) throw new Exception("AUTO_GUI_UNAVAILABLE");
        WPGuojinNative.mouse_event(right?8u:2u,0,0,0,UIntPtr.Zero);
        WPGuojinNative.mouse_event(right?16u:4u,0,0,0,UIntPtr.Zero); return true;
    }); }
}
