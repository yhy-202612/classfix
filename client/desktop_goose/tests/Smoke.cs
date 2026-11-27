using System;
using System.Drawing;
using System.IO;
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
            Console.WriteLine("Schedule boundaries passed");

            Reply reply = DeepSeek.ParseReply("{\"choices\":[{\"message\":{\"content\":\"中文回答\"},\"finish_reason\":\"stop\"}]}");
            Check(reply.CanDeliver && reply.Text == "中文回答", "completed safe reply");
            Check(!DeepSeek.ParseReply("{\"choices\":[{\"message\":{\"content\":\"partial\"},\"finish_reason\":\"content_filter\"}]}").CanDeliver, "filtered text is not displayed");
            Check(!DeepSeek.ParseReply("{\"choices\":[{\"message\":{\"content\":\"partial\",\"refusal\":\"blocked\"},\"finish_reason\":\"stop\"}]}").CanDeliver, "refusal prevents delivery");
            Check(!DeepSeek.ParseReply("{\"choices\":[{\"message\":{\"content\":\"partial\"}}]}").CanDeliver, "unfinalized text is not displayed");
            Console.WriteLine("DeepSeek reply safety passed");

            string folder = args[0];
            Check(StockFiles.Validate(folder), "stock runtime matches expected v0.31");
            Assembly.LoadFrom(Path.Combine(folder, "GooseModdingAPI.dll"));
            Application.EnableVisualStyles();
            CheckWindows(folder, reply);
            Console.WriteLine("Stock walk, chat bubble and answer card passed");
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
