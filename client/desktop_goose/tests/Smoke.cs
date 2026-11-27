using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;

namespace ClassroomGoose
{
    internal static class Smoke
    {
        [STAThread]
        private static void Main(string[] args)
        {
            Check(Schedule.IsOpen(Monday(8, 40), false), "break starts at 08:40");
            Check(!Schedule.IsOpen(Monday(8, 50), false), "class starts at 08:50");
            Check(Schedule.IsOpen(Monday(9, 30), false), "morning eye exercises");
            Check(Schedule.IsOpen(Monday(9, 35), false), "morning exercise transition");
            Check(!Schedule.IsOpen(Monday(10, 0), false), "exercise ends");
            Check(Schedule.IsOpen(Monday(13, 0), false), "rest starts at 13:00");
            Check(Schedule.IsOpen(Monday(13, 40), false), "rest is followed by a break");
            Check(!Schedule.IsOpen(Monday(13, 50), false), "afternoon class starts");
            Check(Schedule.IsOpen(Monday(14, 30), false), "afternoon eye exercises");
            Check(Schedule.IsOpen(Monday(14, 35), false), "afternoon break");
            Check(!Schedule.IsOpen(Monday(14, 45), false), "class resumes");
            Check(!Schedule.IsOpen(Monday(17, 55), false), "dinner gap remains quiet");
            Check(!Schedule.IsOpen(Monday(8, 45).AddDays(5), false), "weekend defaults to quiet");
            Check(Schedule.IsOpen(Monday(8, 45).AddDays(5), true), "weekend switch");
            Check(Schedule.IsOpen(Monday(9, 0), false, true), "debug overrides a class period");
            Check(Schedule.IsOpen(Monday(2, 0).AddDays(5), false, true), "debug overrides a weekend at night");
            Console.WriteLine("Schedule boundaries passed");

            Reply reply = DeepSeek.ParseReply("{\"choices\":[{\"message\":{\"content\":\"中文回答\"},\"finish_reason\":\"stop\"}]}");
            Check(reply.CanDeliver && reply.Text == "中文回答", "completed safe reply");
            Check(!DeepSeek.ParseReply("{\"choices\":[{\"message\":{\"content\":\"partial\"},\"finish_reason\":\"content_filter\"}]}").CanDeliver, "filtered text is not displayed");
            Check(!DeepSeek.ParseReply("{\"choices\":[{\"message\":{\"content\":\"partial\",\"refusal\":\"blocked\"},\"finish_reason\":\"stop\"}]}").CanDeliver, "refusal prevents delivery");
            Check(!DeepSeek.ParseReply("{\"choices\":[{\"message\":{\"content\":\"partial\"}}]}").CanDeliver, "unfinalized text is not displayed");
            Console.WriteLine("DeepSeek reply safety passed");

            var startupError = typeof(Program).GetMethod("StartupError", BindingFlags.NonPublic | BindingFlags.Static);
            var missing = new TargetInvocationException(new FileNotFoundException("Missing", @"C:\Goose\GooseModdingAPI.dll"));
            string diagnostic = (string)startupError.Invoke(null, new object[] { missing, "loading Desktop Goose" });
            Check(diagnostic.Contains("loading Desktop Goose") && diagnostic.Contains("GooseModdingAPI.dll") &&
                  diagnostic.Contains("0x80070002") && !diagnostic.Contains(@"C:\Goose"),
                  "missing dependency reports the stage, code and filename without the local path");

            string folder = args[0];
            Check(StockFiles.Validate(folder), "stock runtime matches expected v0.31");
            StockFiles.BindApi(folder);
            Application.EnableVisualStyles();
            if (args.Length > 1 && args[1] == "--roaming")
            {
                CheckRoaming(folder);
                return;
            }
            CheckWindows(folder, reply);
            Console.WriteLine("Stock walk, chat bubble and answer card passed");
        }

        private static void CheckRoaming(string folder)
        {
            var settings = new HostMessage { Goose = true, DebugAlwaysShow = true, Slideshow = true };
            using (var host = new Form { Text = "EveTech roaming test" })
            using (var app = new GooseApplication(folder, settings))
            {
                host.Show();
                host.Activate();
                Application.DoEvents();
                Func<GooseWindow> visibleGoose = () => Application.OpenForms.OfType<GooseWindow>()
                    .FirstOrDefault(window => window.Visible);
                Func<NestWindow> visibleNest = () => Application.OpenForms.OfType<NestWindow>()
                    .FirstOrDefault(window => window.Visible);
                Check(visibleGoose() == null, "goose starts offscreen during a slideshow");
                Check(visibleNest() != null, "the nest appears during the slideshow");
                NestWindow nest = visibleNest();
                Check(!nest.Occupied, "the nest is empty before arrival");
                WaitUntil(() => visibleGoose() != null, 6, "goose walks in from the screen edge");
                Check(!nest.Occupied, "the nest stays empty while the goose approaches");
                DragNest(nest);
                WaitUntil(() => nest.Occupied, 10, "goose reaches the moved nest");
                Check(visibleGoose() == null, "the walking window is hidden after settling");
                CheckNestArtwork(nest);
                DragNest(nest);
                Check(nest.Occupied, "goose stays in the nest while it is dragged");
                settings.Slideshow = false;
                app.Configure(settings);
                Check(visibleNest() == null, "the nest hides after the slideshow");
                WaitUntil(() => visibleGoose() != null, 10, "goose enters without a DeepSeek key");

                GooseWindow goose = visibleGoose();
                int edge = Screen.PrimaryScreen.WorkingArea.Right;
                WaitUntil(() => goose.Left < edge - 215, 18, "goose walks inward past its entry destination");
                Point last = goose.Location;
                var still = Stopwatch.StartNew();
                WaitUntil(() =>
                {
                    if (last != goose.Location)
                    {
                        last = goose.Location;
                        still.Restart();
                    }
                    return still.ElapsedMilliseconds >= 450;
                }, 12, "goose pauses at a roaming destination");
                WaitUntil(() => goose.Visible && Math.Abs(goose.Left - last.X) + Math.Abs(goose.Top - last.Y) > 15,
                          12, "goose resumes roaming after its pause");
                settings.DebugAlwaysShow = false;
                app.Configure(settings);
                app.Preview();
                Check(app.VisibilityStatus.Contains("preview"), "a preview ignores the school schedule");
                WaitUntil(() => visibleGoose() != null, 10, "a preview renders the goose");
                settings.DebugAlwaysShow = true;
                app.Configure(settings);
                using (var frame = new Bitmap(goose.Width, goose.Height))
                {
                    goose.DrawToBitmap(frame, new Rectangle(Point.Empty, frame.Size));
                    int lightPixels = 0;
                    for (int x = 0; x < frame.Width; x++)
                        for (int y = 0; y < frame.Height; y++)
                        {
                            Color pixel = frame.GetPixel(x, y);
                            if (pixel.R > 220 && pixel.G > 220 && pixel.B > 220) lightPixels++;
                        }
                    Check(lightPixels > 40, "the stock goose draws a visible light body");
                }

                settings.Paused = true;
                app.Configure(settings);
                Check(visibleGoose() == null, "manual pause hides a roaming goose");
                app.Preview();
                Check(app.VisibilityStatus.Contains("paused"), "preview honors manual pause");
                settings.Paused = false;
                app.Configure(settings);
                WaitUntil(() => visibleGoose() != null, 10, "resuming starts another offscreen entry");
                Point beforeSlideshow = visibleGoose().Location;
                settings.Slideshow = true;
                app.Configure(settings);
                Check(visibleGoose() != null && visibleGoose().Location == beforeSlideshow,
                      "the visible goose keeps its position when the slideshow begins");
                Check(visibleNest() != null, "slideshow shows the nest again");
                Check(!visibleNest().Occupied, "a new slideshow starts with an empty nest");

                app.OpenFromHost();
                Func<bool> chatVisible = () => Application.OpenForms.OfType<ChatWindow>().Any(window => window.Visible);
                Check(chatVisible(), "Ask Goose is available while its animation is hidden");
                bool requested = false;
                app.CircleRequested += () => { Check(!chatVisible(), "bubble closes before capture"); requested = true; };
                ChatWindow bubble = Application.OpenForms.OfType<ChatWindow>().Single(window => window.Visible);
                bubble.Controls.OfType<Button>().Single(button => button.Text == "Circle Screen").PerformClick();
                Check(!requested, "capture waits for the bubble transition");
                WaitUntil(() => requested, 2, "Circle Screen requests capture");
                app.OpenFromHost();
                settings.Goose = false;
                settings.Keyboard = true;
                app.Configure(settings);
                Check(!chatVisible(), "turning off Desktop Goose closes its chat");
                Check(visibleNest() == null, "turning off Desktop Goose hides the nest");
                app.OpenFromHost();
                Check(!chatVisible(), "keyboard-only mode cannot open Ask Goose");
            }
            Console.WriteLine("Stock goose rendered, entered and roamed without a key. Pause, resume and slideshow passed.");
        }

        private static void CheckNestArtwork(NestWindow nest)
        {
            using (var frame = new Bitmap(nest.Width, nest.Height))
            {
                nest.DrawToBitmap(frame, new Rectangle(Point.Empty, frame.Size));
                Color straw = frame.GetPixel(60, 82);
                Check(straw.R > straw.G && straw.G > straw.B, "resting nest has a straw-colored rim");
                int feathers = 0;
                for (int x = 35; x < 94; x++)
                    for (int y = 15; y < 62; y++)
                    {
                        Color pixel = frame.GetPixel(x, y);
                        if (pixel.R > 220 && pixel.G > 220 && pixel.B > 220) feathers++;
                    }
                Check(feathers > 40, "original goose is visible in the nest");
            }
        }

        private static void DragNest(NestWindow nest)
        {
            Point start = nest.Location;
            IntPtr previousFocus = Native.GetForegroundWindow();
            Point oldCursor = Cursor.Position;
            try
            {
                Cursor.Position = new Point(start.X + 60, start.Y + 81);
                Native.PostMessage(nest.Handle, 0x0201, new IntPtr(1), new IntPtr(60 | (81 << 16)));
                WaitUntil(() => nest.Capture, 2, "nest captures the pointer for dragging");
                Cursor.Position = new Point(Cursor.Position.X - 105, Cursor.Position.Y - 65);
                Native.PostMessage(nest.Handle, 0x0200, new IntPtr(1), IntPtr.Zero);
                WaitUntil(() => nest.Left <= start.X - 100 && nest.Top <= start.Y - 60, 2, "nest follows the dragged pointer");
                Native.PostMessage(nest.Handle, 0x0202, IntPtr.Zero, IntPtr.Zero);
                WaitUntil(() => !nest.Capture, 2, "nest releases the pointer");
                Check(Native.GetForegroundWindow() == previousFocus, "dragging the nest preserves foreground focus");
            }
            finally { Cursor.Position = oldCursor; }
            Console.WriteLine("Dragging the slideshow nest preserves foreground focus.");
        }

        private static void WaitUntil(Func<bool> condition, double seconds, string description)
        {
            var elapsed = Stopwatch.StartNew();
            while (!condition() && elapsed.Elapsed.TotalSeconds < seconds)
            {
                Application.DoEvents();
                Thread.Sleep(15);
            }
            Check(condition(), description);
        }

        private static void CheckWindows(string folder, Reply reply)
        {
            using (var goose = new StockGoose(folder))
            using (var window = new GooseWindow(goose))
            using (var chat = new ChatWindow())
            using (var card = new AnswerCard(reply.Text, new Size(900, 700)))
            {
                var screen = new Rectangle(0, 0, 900, 700);
                goose.ResetPosition(new Point(730, 360), false);
                var destination = new Point(490, 360);
                for (int i = 0; i < 650 && !goose.Move(destination, true); i++) { }
                Check(Math.Abs(goose.Position.X - destination.X) < 12, "the original goose locomotion moves toward goal");
                window.UpdatePose(screen);
                var beak = goose.Beak;
                card.Follow(beak, true, screen);
                Check(card.Left == beak.X && card.Top + AnswerCard.GripHeight == beak.Y, "beak attaches to card");
                card.Release();
                chat.SetState(false, reply);
                chat.Present(beak, true, new Rectangle(0, 420, 900, 280));
                for (int i = 0; i < 20; i++) { Application.DoEvents(); Thread.Sleep(15); }
                Check(chat.Visible && chat.Height == 110, "bubble opens with action buttons");
                Check(!chat.Controls.OfType<TextBox>().Single().Visible, "text field is hidden until typing is chosen");
                chat.Controls.OfType<Button>().Single(button => button.Text == "Type Question").PerformClick();
                for (int i = 0; i < 20; i++) { Application.DoEvents(); Thread.Sleep(15); }
                Check(chat.Visible && chat.Height >= 300, "chat opens with room for latest answer");
                Check(chat.Bottom <= 420, "chat sits above the keyboard");
                Check(chat.Opacity >= .99, "opening animation finishes");
                chat.Dismiss(true);
                for (int i = 0; i < 20; i++) { Application.DoEvents(); Thread.Sleep(15); }
                Check(!chat.Visible, "closing animation finishes");
            }
        }

        private static DateTime Monday(int hour, int minute) { return new DateTime(2026, 11, 23, hour, minute, 0); }
        private static void Check(bool condition, string description)
        {
            if (!condition) throw new Exception("Failed: " + description);
        }
    }
}
