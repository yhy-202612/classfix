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
        public bool DebugAlwaysShow { get; set; }
        public bool Paused { get; set; }
        public bool Slideshow { get; set; }
        public long Window { get; set; }
    }

    internal static class Program
    {
        internal static void Trace(string message)
        {
            string log = Environment.GetEnvironmentVariable("QA_REPORT");
            if (String.IsNullOrEmpty(log)) return;
            lock (typeof(Program))
                File.AppendAllText(Path.Combine(log, "helper-trace.txt"), DateTime.Now.ToString("HH:mm:ss.fff") + " " + message + "\n");
        }

        [STAThread]
        private static void Main(string[] args)
        {
            if (args.Length != 1 || args[0] != "--evetech") return;
            using (var input = new StreamReader(Console.OpenStandardInput(), Encoding.UTF8))
            using (var output = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true })
            {
                var json = new JavaScriptSerializer { MaxJsonLength = 16384 };
                string stage = "reading desktop pet settings";
                try
                {
                    HostMessage settings = Read(input, json);
                    if (settings == null || settings.Action != "configure") return;
                    Trace("initial configure read");

                    stage = "initializing Windows graphics";
                    Native.SetProcessDPIAware();
                    Application.SetUnhandledExceptionMode(UnhandledExceptionMode.ThrowException);
                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);

                    string folder = settings.Folder;
                    stage = "loading GooseModdingAPI.dll";
                    if (StockFiles.Validate(folder))
                        StockFiles.BindApi(folder);
                    else
                        folder = null;

                    stage = "initializing the desktop pet";
                    using (var app = new GooseApplication(folder, settings))
                    using (var dispatcher = new Control())
                    {
                        app.CircleRequested += () => Send(output, json, "circle_screen", "");
                        app.VisibilityChanged += message => Send(output, json, "visibility", message);
                        var ignored = dispatcher.Handle;
                        Action<HostMessage> preview = command =>
                        {
                            Trace("preview callback running");
                            Send(output, json, "preview", app.Preview(new IntPtr(command.Window)));
                            Trace("preview reply sent");
                        };
                        dispatcher.BeginInvoke(new MethodInvoker(delegate
                        {
                            Send(output, json, "ready", settings.Goose ? app.VisibilityStatus : "Touch keyboard helper is ready.");
                            var reader = new Thread(() => Listen(input, app, dispatcher, preview))
                            {
                                IsBackground = true,
                                Name = "EveTech desktop pet commands"
                            };
                            reader.Start();
                            Trace("reader started");
                        }));
                        stage = "running the desktop pet";
                        Application.Run(app);
                    }
                }
                catch (Exception error)
                {
                    Trace(error.ToString());
                    Send(output, json, "error", StartupError(error, stage));
                }
            }
        }

        private static string StartupError(Exception error, string stage)
        {
            while (error.InnerException != null &&
                   (error is TargetInvocationException || error is TypeInitializationException))
                error = error.InnerException;
            if (error is FileLoadException && error.HResult == unchecked((int)0x80131515))
                return "Windows blocked a downloaded Desktop Goose file (0x80131515). " +
                       "Open Desktop Pet and click Download & Set Up, or import the official Windows ZIP.";
            var missing = error as FileNotFoundException;
            var failedLoad = error as FileLoadException;
            string file = missing != null ? missing.FileName : failedLoad != null ? failedLoad.FileName : null;
            string component = String.IsNullOrWhiteSpace(file) ? "" : " · " + Path.GetFileName(file);
            return "The desktop pet helper failed while " + stage + " (" + error.GetType().Name +
                   ", 0x" + error.HResult.ToString("X8") + component + ").";
        }

        private static void Listen(StreamReader input, GooseApplication app, Control dispatcher, Action<HostMessage> preview)
        {
            try
            {
                var json = new JavaScriptSerializer { MaxJsonLength = 16384 };
                HostMessage command;
                Trace("reading subsequent commands");
                while ((command = Read(input, json)) != null)
                {
                    Trace("read " + command.Action);
                    if (command.Action == "stop") break;
                    if (command.Action == "configure")
                    {
                        HostMessage current = command;
                        dispatcher.BeginInvoke(new MethodInvoker(() => app.Configure(current)));
                    }
                    else if (command.Action == "ask")
                        dispatcher.BeginInvoke(new MethodInvoker(app.OpenFromHost));
                    else if (command.Action == "preview")
                    {
                        HostMessage current = command;
                        dispatcher.BeginInvoke(new MethodInvoker(() => preview(current)));
                        Trace("preview enqueued");
                    }
                }
            }
            catch (IOException error) { Trace(error.ToString()); }
            catch (InvalidOperationException error) { Trace(error.ToString()); }
            catch (ArgumentException error) { Trace(error.ToString()); }
            finally
            {
                Trace("reader exiting");
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
        private bool registered;
        private readonly Action open;

        internal HotkeyWindow(Action open)
        {
            this.open = open;
            CreateHandle(new CreateParams { Caption = "EveTech desktop pet shortcuts", Parent = new IntPtr(-3) });
        }

        internal void SetEnabled(bool enabled)
        {
            if (enabled == registered) return;
            if (enabled)
                registered = Native.RegisterHotKey(Handle, 1, 0x4000 | 0x0001 | 0x0002, (uint)Keys.G);
            else
            {
                Native.UnregisterHotKey(Handle, 1);
                registered = false;
            }
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
            SetEnabled(false);
            DestroyHandle();
        }
    }
}
