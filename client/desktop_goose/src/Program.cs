using System;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace ClassroomGoose
{
    public sealed class HostMessage
    {
        public string Action { get; set; }
        public string Folder { get; set; }
        public string Token { get; set; }
        public bool Goose { get; set; }
        public bool Keyboard { get; set; }
        public bool Weekends { get; set; }
        public bool Paused { get; set; }
        public bool Slideshow { get; set; }
    }

    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            if (args.Length != 1 || args[0] != "--classfix") return;
            using (var input = new StreamReader(Console.OpenStandardInput(), Encoding.UTF8))
            using (var output = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true })
            {
                var json = new JavaScriptSerializer { MaxJsonLength = 16384 };
                try
                {
                    HostMessage settings = Read(input, json);
                    if (settings == null || settings.Action != "configure") return;

                    Native.SetProcessDPIAware();
                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);

                    string folder = settings.Folder;
                    if (StockFiles.Validate(folder))
                        Assembly.LoadFrom(Path.Combine(folder, "GooseModdingAPI.dll"));
                    else
                        folder = null;

                    using (var app = new GooseApplication(folder, settings))
                    using (var dispatcher = new Control())
                    {
                        var ignored = dispatcher.Handle;
                        EventHandler ready = null;
                        ready = delegate
                        {
                            Application.Idle -= ready;
                            Send(output, json, "ready", folder == null && settings.Goose
                                ? "桌鹅文件缺失或版本不符，可在 ClassFix 设置重新选择原版。" : "");
                            var reader = new Thread(() => Listen(input, json, app, dispatcher))
                            {
                                IsBackground = true,
                                Name = "ClassFix desktop pet commands"
                            };
                            reader.Start();
                        };
                        Application.Idle += ready;
                        try { Application.Run(app); }
                        finally { Application.Idle -= ready; }
                    }
                }
                catch (Exception error)
                {
                    Send(output, json, "error", "桌鹅助手启动失败（" + error.GetType().Name + "）。");
                }
            }
        }

        private static void Listen(StreamReader input, JavaScriptSerializer json, GooseApplication app, Control dispatcher)
        {
            try
            {
                HostMessage command;
                while ((command = Read(input, json)) != null)
                {
                    if (command.Action == "stop") break;
                    if (command.Action == "configure")
                    {
                        HostMessage current = command;
                        dispatcher.BeginInvoke(new MethodInvoker(() => app.Configure(current)));
                    }
                    else if (command.Action == "ask")
                        dispatcher.BeginInvoke(new MethodInvoker(app.OpenFromHost));
                }
            }
            catch (IOException) { }
            catch (InvalidOperationException) { }
            catch (ArgumentException) { }
            finally
            {
                try { if (!dispatcher.IsDisposed) dispatcher.BeginInvoke(new MethodInvoker(app.ExitThread)); }
                catch (InvalidOperationException) { }
            }
        }

        private static HostMessage Read(StreamReader input, JavaScriptSerializer json)
        {
            string line = input.ReadLine();
            return line != null ? json.Deserialize<HostMessage>(line) : null;
        }

        private static void Send(StreamWriter output, JavaScriptSerializer json, string kind, string message)
        {
            output.WriteLine(json.Serialize(new { kind, message }));
        }
    }

    internal sealed class HotkeyWindow : NativeWindow, IDisposable
    {
        private readonly bool registered;
        private readonly Action open;

        internal HotkeyWindow(Action open)
        {
            this.open = open;
            CreateHandle(new CreateParams { Caption = "ClassFix desktop pet shortcuts", Parent = new IntPtr(-3) });
            registered = Native.RegisterHotKey(Handle, 1, 0x4000 | 0x0001 | 0x0002, (uint)Keys.G);
        }

        protected override void WndProc(ref Message message)
        {
            if (message.Msg == 0x0312 && message.WParam == new IntPtr(1))
                open();
            else
                base.WndProc(ref message);
        }

        public void Dispose()
        {
            if (registered) Native.UnregisterHotKey(Handle, 1);
            DestroyHandle();
        }
    }
}
