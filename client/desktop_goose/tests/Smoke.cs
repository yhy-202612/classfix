using System;
using System.IO;
using System.Reflection;

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

        }

        private static DateTime Monday(int hour, int minute)
        {
            return new DateTime(2026, 11, 23, hour, minute, 0);
        }

        private static void Check(bool condition, string description)
        {
            if (!condition) throw new InvalidOperationException(description);
        }
    }
}
