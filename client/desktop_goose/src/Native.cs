using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

namespace ClassroomGoose
{
    internal static class Native
    {
        internal const int NoActivate = 0x08000000;
        internal const int ToolWindow = 0x00000080;
        internal const int Transparent = 0x00000020;
        private const uint NoMove = 0x0002;
        private const uint NoSize = 0x0001;
        private const uint NoActivatePosition = 0x0010;

        [StructLayout(LayoutKind.Sequential)]
        internal struct Rect
        {
            internal int Left, Top, Right, Bottom;
            internal Rectangle Bounds { get { return Rectangle.FromLTRB(Left, Top, Right, Bottom); } }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct GuiThreadInfo
        {
            internal int Size, Flags;
            internal IntPtr Active, Focus, Capture, MenuOwner, MoveSize, Caret;
            internal Rect CaretRectangle;
        }

        [DllImport("user32.dll")]
        internal static extern bool SetProcessDPIAware();
        [DllImport("user32.dll")]
        internal static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")]
        internal static extern IntPtr GetDesktopWindow();
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        internal static extern IntPtr FindWindow(string className, string name);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern IntPtr FindWindowEx(IntPtr parent, IntPtr after, string className, string name);
        [DllImport("user32.dll")]
        internal static extern bool IsWindowVisible(IntPtr window);
        [DllImport("user32.dll")]
        internal static extern bool IsWindowEnabled(IntPtr window);
        [DllImport("user32.dll", EntryPoint = "GetWindowLongW")]
        private static extern int GetWindowLong(IntPtr window, int index);
        [DllImport("user32.dll")]
        internal static extern bool IsIconic(IntPtr window);
        [DllImport("user32.dll")]
        internal static extern bool SetForegroundWindow(IntPtr window);
        [DllImport("user32.dll")]
        internal static extern uint GetWindowThreadProcessId(IntPtr window, out int process);
        [DllImport("user32.dll")]
        private static extern bool GetGUIThreadInfo(uint thread, ref GuiThreadInfo info);
        [DllImport("user32.dll")]
        private static extern bool GetWindowRect(IntPtr window, out Rect rect);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetClassName(IntPtr window, StringBuilder name, int size);
        [DllImport("dwmapi.dll")]
        private static extern int DwmGetWindowAttribute(IntPtr window, int property, out int value, int size);
        [DllImport("user32.dll")]
        private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int w, int h, uint flags);
        [DllImport("user32.dll")]
        internal static extern bool RegisterHotKey(IntPtr window, int id, uint modifiers, uint key);
        [DllImport("user32.dll")]
        internal static extern bool UnregisterHotKey(IntPtr window, int id);
        [DllImport("user32.dll")]
        internal static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
        [DllImport("winmm.dll", CharSet = CharSet.Unicode)]
        internal static extern int mciSendString(string command, StringBuilder response, int length, IntPtr callback);

        internal static string ClassName(IntPtr window)
        {
            var buffer = new StringBuilder(160);
            GetClassName(window, buffer, buffer.Capacity);
            return buffer.ToString();
        }

        internal static IntPtr FocusWindow()
        {
            var info = new GuiThreadInfo { Size = Marshal.SizeOf(typeof(GuiThreadInfo)) };
            return GetGUIThreadInfo(0, ref info) ? info.Focus : IntPtr.Zero;
        }

        internal static bool HasEditableCaret(IntPtr foreground)
        {
            var info = new GuiThreadInfo { Size = Marshal.SizeOf(typeof(GuiThreadInfo)) };
            int pid;
            uint thread = GetWindowThreadProcessId(foreground, out pid);
            if (thread == 0 || !GetGUIThreadInfo(thread, ref info) || info.Focus == IntPtr.Zero ||
                info.Caret == IntPtr.Zero || !IsWindowEnabled(info.Focus))
                return false;

            string type = ClassName(info.Focus);
            const int windowStyle = -16;
            const int readOnlyEdit = 0x0800;
            return (type.Equals("Edit", StringComparison.OrdinalIgnoreCase) ||
                    type.StartsWith("RichEdit", StringComparison.OrdinalIgnoreCase) ||
                    type.StartsWith("WindowsForms10.EDIT.", StringComparison.OrdinalIgnoreCase)) &&
                   (GetWindowLong(info.Focus, windowStyle) & readOnlyEdit) == 0;
        }

        internal static void RaiseWithoutFocus(IntPtr window)
        {
            SetWindowPos(window, new IntPtr(-1), 0, 0, 0, 0, NoMove | NoSize | NoActivatePosition);
        }

        internal static IntPtr SlideshowWindow()
        {
            IntPtr slide = GetForegroundWindow();
            if (ClassName(slide) == "screenClass" && Visible(slide)) return slide;
            slide = IntPtr.Zero;
            while ((slide = FindWindowEx(IntPtr.Zero, slide, "screenClass", null)) != IntPtr.Zero)
                if (Visible(slide)) return slide;
            return IntPtr.Zero;
        }

        internal static bool IsFullScreen(int ownPid)
        {
            IntPtr window = GetForegroundWindow();
            if (!Visible(window)) return false;
            int pid;
            GetWindowThreadProcessId(window, out pid);
            if (pid == ownPid) return false;
            string type = ClassName(window);
            if (type == "Progman" || type == "WorkerW" || type == "Shell_TrayWnd" || type == "Shell_SecondaryTrayWnd")
                return false;
            const int windowStyle = -16;
            const int caption = 0x00C00000;
            if ((GetWindowLong(window, windowStyle) & caption) == caption)
                return false;
            Rect windowRect;
            if (!GetWindowRect(window, out windowRect)) return false;
            Rectangle screen = Screen.FromRectangle(windowRect.Bounds).Bounds;
            return windowRect.Left <= screen.Left + 1 && windowRect.Top <= screen.Top + 1 &&
                   windowRect.Right >= screen.Right - 1 && windowRect.Bottom >= screen.Bottom - 1;
        }

        internal static bool Visible(IntPtr window)
        {
            if (window == IntPtr.Zero || !IsWindowVisible(window) || IsIconic(window)) return false;
            int cloaked;
            return DwmGetWindowAttribute(window, 14, out cloaked, sizeof(int)) != 0 || cloaked == 0;
        }
    }
}
