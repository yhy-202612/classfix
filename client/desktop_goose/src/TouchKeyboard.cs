using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Automation;

namespace ClassroomGoose
{
    internal enum FocusKind { Unknown, Other, Editable, Keyboard }

    internal sealed class TouchKeyboard : IDisposable
    {
        private readonly int ownProcess = Process.GetCurrentProcess().Id;
        private readonly object paneLock = new object();
        private volatile bool stopping;
        private volatile bool enabled;
        private Rectangle paneBounds;
        private PaneRequest paneRequest;
        private volatile FocusReading current = new FocusReading(FocusKind.Unknown, IntPtr.Zero, IntPtr.Zero);
        private FocusReading previous;
        private IntPtr lastEditor;
        private bool hadEditor;
        private DateTime lastAction;
        private bool ownInput;

        internal TouchKeyboard()
        {
            var worker = new Thread(WatchFocus) { IsBackground = true, Name = "Touch keyboard focus" };
            worker.SetApartmentState(ApartmentState.MTA);
            worker.Start();
            var controls = new Thread(ControlPane) { IsBackground = true, Name = "Windows touch keyboard" };
            controls.SetApartmentState(ApartmentState.STA);
            controls.Start();
        }

        internal bool Enabled { get { return enabled; } }
        internal Rectangle Bounds
        {
            get { lock (paneLock) return enabled ? paneBounds : Rectangle.Empty; }
        }

        internal void Enable(bool value)
        {
            if (enabled == value) return;
            if (!value && hadEditor) ChangeVisibility(lastEditor, false);
            enabled = value;
            previous = null;
            hadEditor = false;
        }

        internal void BeginOwnInput(IntPtr owner)
        {
            ownInput = true;
            if (!enabled) return;
            lastEditor = owner;
            hadEditor = true;
            ChangeVisibility(owner, true);
        }

        internal void EndOwnInput(IntPtr owner)
        {
            ownInput = false;
            if (enabled && lastEditor == owner)
            {
                ChangeVisibility(owner, false);
                hadEditor = false;
            }
        }

        internal void FollowFocus()
        {
            if (!enabled) return;
            FocusReading focus = current;
            if (focus.Kind == FocusKind.Unknown || focus.Kind == FocusKind.Keyboard || focus.SameAs(previous))
                return;
            if ((DateTime.UtcNow - lastAction).TotalMilliseconds < 220)
                return;
            // The chat's own TextBox is handled synchronously when shown or closed.
            if (focus.Kind == FocusKind.Editable && ownInput && focus.Foreground == lastEditor)
            {
                previous = focus;
                return;
            }
            previous = focus;
            if (focus.Kind == FocusKind.Editable)
            {
                ChangeVisibility(focus.Foreground, true);
                lastEditor = focus.Foreground;
                hadEditor = true;
            }
            else if (hadEditor)
            {
                ChangeVisibility(lastEditor, false);
                hadEditor = false;
            }
        }

        private void ChangeVisibility(IntPtr owner, bool show)
        {
            lastAction = DateTime.UtcNow;
            lock (paneLock) paneRequest = new PaneRequest(owner, show);
        }

        private void ControlPane()
        {
            using (var pane = new InputPane())
            {
                PaneRequest retry = null;
                var waitForTabTip = new Stopwatch();
                while (true)
                {
                    PaneRequest current;
                    lock (paneLock)
                    {
                        current = paneRequest;
                        paneRequest = null;
                    }
                    try
                    {
                        if (current != null)
                        {
                            retry = pane.SetVisible(current.Owner, current.Show) ? current : null;
                            waitForTabTip.Restart();
                        }
                        if (stopping) return;
                        if (retry != null && waitForTabTip.ElapsedMilliseconds >= 500)
                        {
                            PaneRequest pending = retry;
                            retry = null;
                            if (enabled && Native.GetForegroundWindow() == pending.Owner)
                                pane.SetVisible(pending.Owner, true, false);
                        }
                        if (enabled)
                        {
                            Rectangle area = pane.Bounds();
                            lock (paneLock) paneBounds = area;
                        }
                    }
                    catch (ExternalException) { }
                    catch (NotImplementedException) { }
                    catch (TypeLoadException) { }
                    catch (InvalidOperationException) { }
                    Thread.Sleep(120);
                }
            }
        }

        private void WatchFocus()
        {
            FocusReading candidate = null;
            bool automationAvailable = true;
            while (!stopping)
            {
                if (enabled)
                {
                    FocusReading result;
                    try
                    {
                        result = automationAvailable ? ReadFocus(ownProcess) : ReadNativeFocus();
                    }
                    catch (FileNotFoundException)
                    {
                        automationAvailable = false;
                        result = ReadNativeFocus();
                    }
                    catch (TypeLoadException)
                    {
                        automationAvailable = false;
                        result = ReadNativeFocus();
                    }
                    if (result.SameAs(candidate))
                        current = result;
                    candidate = result;
                }
                else
                {
                    candidate = null;
                }
                Thread.Sleep(170);
            }
        }

        private static FocusReading ReadNativeFocus()
        {
            IntPtr foreground = Native.GetForegroundWindow();
            return new FocusReading(Native.HasEditableCaret(foreground) ? FocusKind.Editable : FocusKind.Unknown,
                                    foreground, Native.FocusWindow());
        }

        private static FocusReading ReadFocus(int ownProcess)
        {
            IntPtr foreground = Native.GetForegroundWindow();
            IntPtr focusWindow = Native.FocusWindow();
            if (foreground == IntPtr.Zero)
                return new FocusReading(FocusKind.Unknown, foreground, focusWindow);

            int processId;
            Native.GetWindowThreadProcessId(foreground, out processId);
            string foregroundClass = Native.ClassName(foreground);
            if (foregroundClass == "IPTip_Main_Window" || foregroundClass == "IPTipHost")
                return new FocusReading(FocusKind.Keyboard, foreground, focusWindow);
            if (processId != ownProcess && IsKeyboardProcess(processId))
                return new FocusReading(FocusKind.Keyboard, foreground, focusWindow);

            try
            {
                var element = AutomationElement.FocusedElement;
                if (element != null)
                {
                    var view = element.Current;
                    if (view.ProcessId != ownProcess && view.ProcessId != processId && IsKeyboardProcess(view.ProcessId))
                        return new FocusReading(FocusKind.Keyboard, foreground, focusWindow);

                        FocusKind kind = IsEditable(element, view) || Native.HasEditableCaret(foreground)
                            ? FocusKind.Editable : FocusKind.Other;
                    return new FocusReading(kind, foreground, focusWindow, element.GetRuntimeId());
                }
            }
            catch (ElementNotAvailableException) { }
            catch (COMException) { }
            catch (InvalidOperationException) { }
            catch (UnauthorizedAccessException) { }
            catch (NotImplementedException) { }
            catch (TypeInitializationException) { }

            return new FocusReading(Native.HasEditableCaret(foreground) ? FocusKind.Editable : FocusKind.Unknown,
                                    foreground, focusWindow);
        }

        private static bool IsEditable(AutomationElement element, AutomationElement.AutomationElementInformation view)
        {
            if (!view.IsEnabled || !view.HasKeyboardFocus)
                return false;

            object value;
            if (element.TryGetCurrentPattern(ValuePattern.Pattern, out value))
                return !((ValuePattern)value).Current.IsReadOnly;
            if (element.TryGetCurrentPattern(TextPattern.Pattern, out value))
            {
                // Query only the read-only flag, never the value or document text.
                object readOnly = ((TextPattern)value).DocumentRange.GetAttributeValue(TextPattern.IsReadOnlyAttribute);
                if (readOnly is bool) return !(bool)readOnly;
            }
            return view.ControlType == ControlType.Edit;
        }

        private static bool IsKeyboardProcess(int id)
        {
            if (id <= 0) return false;
            try
            {
                using (var process = Process.GetProcessById(id))
                {
                    string name = process.ProcessName.ToLowerInvariant();
                    return name == "textinputhost" || name == "tabtip" || name == "inputapp" || name == "osk";
                }
            }
            catch (ArgumentException) { return false; }
            catch (InvalidOperationException) { return false; }
            catch (System.ComponentModel.Win32Exception) { return false; }
        }

        public void Dispose()
        {
            Enable(false);
            stopping = true;
        }

        private sealed class PaneRequest
        {
            internal readonly IntPtr Owner;
            internal readonly bool Show;

            internal PaneRequest(IntPtr owner, bool show)
            {
                Owner = owner;
                Show = show;
            }
        }

        private sealed class FocusReading
        {
            internal readonly FocusKind Kind;
            internal readonly IntPtr Foreground;
            private readonly IntPtr window;
            private readonly int[] runtimeId;

            internal FocusReading(FocusKind kind, IntPtr foreground, IntPtr window, int[] runtimeId = null)
            {
                Kind = kind;
                Foreground = foreground;
                this.window = window;
                this.runtimeId = runtimeId;
            }

            internal bool SameAs(FocusReading other)
            {
                if (other == null || Kind != other.Kind || Foreground != other.Foreground || window != other.window)
                    return false;
                if (runtimeId == null || other.runtimeId == null)
                    return runtimeId == other.runtimeId;
                if (runtimeId.Length != other.runtimeId.Length)
                    return false;
                for (int index = 0; index < runtimeId.Length; index++)
                    if (runtimeId[index] != other.runtimeId[index]) return false;
                return true;
            }
        }
    }

    internal sealed class InputPane : IDisposable
    {
        private readonly Guid inputPaneInteropId = new Guid("75CF2C57-9195-4931-8332-F0B409E916AF");
        private readonly Guid inputPane2Id = new Guid("8a6b3f26-7090-4793-944c-c3f2cde26276");
        private readonly Guid windowPaneClass = new Guid("D5120AA3-46BA-44C5-822D-CA8092C1FC72");
        private readonly Guid tipClass = new Guid("4CE576FA-83DC-4F88-951C-9D0782B4E376");
        private IFrameworkInputPane view;
        private bool triedView;
        private bool initialized;
        private bool initializedWinRt;

        [DllImport("combase.dll")]
        private static extern int RoInitialize(int type);
        [DllImport("combase.dll")]
        private static extern void RoUninitialize();
        [DllImport("combase.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        private static extern int WindowsCreateString(string text, int count, out IntPtr result);
        [DllImport("combase.dll")]
        private static extern int WindowsDeleteString(IntPtr text);
        [DllImport("combase.dll")]
        private static extern int RoGetActivationFactory(IntPtr name, ref Guid type, out IntPtr result);

        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        private delegate int GetForWindow(IntPtr self, IntPtr window, ref Guid type, out IntPtr result);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        private delegate int ChangePane(IntPtr self, out byte changed);

        internal Rectangle Bounds()
        {
            Rectangle area;
            return TryGetBounds(out area) ? area : Rectangle.Empty;
        }

        private bool TryGetBounds(out Rectangle bounds)
        {
            bounds = Rectangle.Empty;
            if (!triedView)
            {
                triedView = true;
                try { view = (IFrameworkInputPane)CreateComObject(windowPaneClass); }
                catch (InvalidCastException) { }
            }
            try
            {
                Native.Rect area;
                if (view != null && view.Location(out area) >= 0)
                {
                    if (area.Right > area.Left && area.Bottom > area.Top) bounds = area.Bounds;
                    return true;
                }
            }
            catch (COMException) { }
            return false;
        }

        // Returns true if TabTip was started and the caller should check again after it initializes.
        internal bool SetVisible(IntPtr owner, bool show, bool startIfNeeded = true)
        {
            if (owner == IntPtr.Zero || (show && owner != Native.GetForegroundWindow())) return false;
            Rectangle extent;
            bool boundsKnown = TryGetBounds(out extent);
            IntPtr legacy = Native.FindWindow("IPTip_Main_Window", null);
            bool legacyVisible = Native.Visible(legacy) && Native.IsWindowEnabled(legacy);
            bool visible = !extent.IsEmpty || legacyVisible;
            if ((show && visible) || SetWinRtVisibility(owner, show) || (!show && !visible)) return false;
            if (!show && legacyVisible &&
                Native.PostMessage(legacy, 0x0112, new IntPtr(0xF060), IntPtr.Zero))
                return false;

            // Toggle only if we know the pane's current state.
            if ((boundsKnown || legacy != IntPtr.Zero) && TryToggle()) return false;
            return show && startIfNeeded && StartTabTip();
        }

        private bool TryToggle()
        {
            try
            {
                var tip = (ITipInvocation)CreateComObject(tipClass);
                if (tip == null) return false;
                try { return tip.Toggle(Native.GetDesktopWindow()) >= 0; }
                finally { Marshal.ReleaseComObject(tip); }
            }
            catch (COMException) { }
            catch (InvalidCastException) { }
            return false;
        }

        private static bool StartTabTip()
        {
            string file = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonProgramFiles),
                                       @"Microsoft Shared\Ink\TabTip.exe");
            if (!File.Exists(file)) return false;
            try
            {
                using (Process.Start(file)) { }
                return true;
            }
            catch (System.ComponentModel.Win32Exception) { }
            return false;
        }

        private static object CreateComObject(Guid clsid)
        {
            try { return Activator.CreateInstance(Type.GetTypeFromCLSID(clsid)); }
            catch (COMException) { return null; }
            catch (TargetInvocationException error)
            {
                if (error.InnerException is COMException) return null;
                throw;
            }
        }

        private bool SetWinRtVisibility(IntPtr owner, bool show)
        {
            if (!initialized)
            {
                initialized = true;
                try { initializedWinRt = RoInitialize(0) >= 0; }
                catch (EntryPointNotFoundException) { }
            }
            if (!initializedWinRt) return false;

            const string name = "Windows.UI.ViewManagement.InputPane";
            IntPtr className;
            if (WindowsCreateString(name, name.Length, out className) < 0) return false;
            IntPtr factory = IntPtr.Zero, inputPanel = IntPtr.Zero;
            try
            {
                Guid interop = inputPaneInteropId, version = inputPane2Id;
                if (RoGetActivationFactory(className, ref interop, out factory) < 0) return false;
                var get = (GetForWindow)GetMethod(factory, 6, typeof(GetForWindow));
                if (get(factory, owner, ref version, out inputPanel) < 0 || inputPanel == IntPtr.Zero) return false;
                var change = (ChangePane)GetMethod(inputPanel, show ? 6 : 7, typeof(ChangePane));
                byte changed;
                return change(inputPanel, out changed) >= 0 && changed != 0;
            }
            finally
            {
                if (inputPanel != IntPtr.Zero) Marshal.Release(inputPanel);
                if (factory != IntPtr.Zero) Marshal.Release(factory);
                WindowsDeleteString(className);
            }
        }

        private static Delegate GetMethod(IntPtr instance, int slot, Type signature)
        {
            return Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(Marshal.ReadIntPtr(instance), slot * IntPtr.Size), signature);
        }

        public void Dispose()
        {
            if (view != null) Marshal.ReleaseComObject(view);
            if (initializedWinRt) RoUninitialize();
        }

        [ComImport, Guid("5752238B-24F0-495A-82F1-2FD593056796"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IFrameworkInputPane
        {
            [PreserveSig] int Advise(IntPtr window, IntPtr handler, out uint cookie);
            [PreserveSig] int AdviseWithHWND(IntPtr window, IntPtr handler, out uint cookie);
            [PreserveSig] int Unadvise(uint cookie);
            [PreserveSig] int Location(out Native.Rect rect);
        }

        [ComImport, Guid("37C994E7-432B-4834-A2F7-DCE1F13B834B"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface ITipInvocation
        {
            [PreserveSig] int Toggle(IntPtr desktop);
        }
    }
}
