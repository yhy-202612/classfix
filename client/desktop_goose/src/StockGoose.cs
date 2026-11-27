using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using GooseShared;
using SamEngine;

namespace ClassroomGoose
{
    internal static class StockFiles
    {
        internal const string ExecutableHash = "943fd1ea44266c5d7fa02f2b292db095a4e6ba8027a1f6c73fd60d1165e63aff";
        internal const string ApiHash = "efa31df7ab1394092395365805f913dd023cdcd21796603f133641524fb9ad79";

        internal static bool Validate(string folder)
        {
            if (String.IsNullOrWhiteSpace(folder)) return false;
            try
            {
                return HasHash(Path.Combine(folder, "GooseDesktop.exe"), ExecutableHash) &&
                       HasHash(Path.Combine(folder, "GooseModdingAPI.dll"), ApiHash) &&
                       File.Exists(Path.Combine(folder, @"Assets\Sound\NotEmbedded\Honk1.mp3"));
            }
            catch (IOException) { return false; }
            catch (UnauthorizedAccessException) { return false; }
            catch (ArgumentException) { return false; }
        }

        private static bool HasHash(string file, string expected)
        {
            using (var stream = File.OpenRead(file))
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").Equals(expected, StringComparison.OrdinalIgnoreCase);
        }
    }

    internal interface IGoose : IDisposable
    {
        Point Position { get; }
        Point Beak { get; }
        void ResetPosition(Point point, bool facesRight);
        bool Move(Point target, bool holding);
        void Draw(Graphics graphics);
        void Honk();
    }

    internal sealed class StockGoose : IGoose
    {
        private readonly GooseEntity goose;
        private readonly string honkPath;
        private bool audioOpen;

        internal StockGoose(string folder)
        {
            var stock = Assembly.LoadFrom(Path.Combine(folder, "GooseDesktop.exe"));
            Type config = stock.GetType("GooseDesktop.GooseConfig", true);
            var safe = Activator.CreateInstance(config.GetNestedType("ConfigSettings"));
            safe.GetType().GetField("SilenceSounds").SetValue(safe, true);
            safe.GetType().GetField("Task_CanAttackMouse").SetValue(safe, false);
            config.GetField("settings").SetValue(null, safe);

            // The factory selects “Wander” by ID. Register only this inert task
            // before building the rig, and leave the stock host loop unstarted.
            Type database = stock.GetType("GooseDesktop.Refactor.GooseTasks.GooseTaskDatabase", true);
            database.GetMethod("RegisterTask").Invoke(null, new object[] { new PassiveTask() });
            database.GetMethod("LockDatabase").Invoke(null, null);
            stock.GetType("GooseDesktop.Sound", true).GetMethod("Init").Invoke(null, null);
            goose = (GooseEntity)stock.GetType("GooseDesktop.Refactor.GooseFunctions", true)
                .GetMethod("InitGoose").Invoke(null, null);
            goose.currentTaskData = new Pose();
            honkPath = Path.Combine(folder, @"Assets\Sound\NotEmbedded\Honk1.mp3");
        }

        public Point Position
        {
            get { return new Point((int)Math.Round(goose.position.x), (int)Math.Round(goose.position.y)); }
        }

        public Point Beak
        {
            get { return new Point((int)Math.Round(goose.rig.head2EndPoint.x), (int)Math.Round(goose.rig.head2EndPoint.y)); }
        }

        public void ResetPosition(Point point, bool facesRight)
        {
            goose.position = new Vector2(point.X, point.Y);
            goose.velocity = Vector2.zero;
            goose.direction = facesRight ? 0 : 180;
            // Reset the feet along with the body when moving the hidden rig to an entry edge.
            goose.rig.feets = new ProceduralFeets();
            goose.rig.feets.lFootPos = goose.position;
            goose.rig.feets.rFootPos = goose.position;
            goose.rig.neckLerpPercent = 0;
            goose.updateRig(goose.rig, goose.position, goose.direction);
        }

        public bool Move(Point target, bool holding)
        {
            double dx = target.X - goose.position.x;
            double dy = target.Y - goose.position.y;
            bool reached = dx * dx + dy * dy <= 7 * 7;
            var pose = (Pose)goose.currentTaskData;
            pose.Holding = holding;
            if (reached)
            {
                goose.velocity = Vector2.zero;
                goose.currentAcceleration = 0;
                goose.targetPos = goose.position + Vector2.GetFromAngleDegrees(goose.direction);
            }
            else
            {
                goose.currentAcceleration = goose.parameters.AccelerationNormal;
                goose.currentSpeed = goose.parameters.WalkSpeed;
                goose.targetPos = new Vector2(target.X, target.Y);
            }
            Time.TickTime();
            goose.tick(goose);
            goose.updateRig(goose.rig, goose.position, goose.direction);
            return reached;
        }

        public void Draw(Graphics graphics)
        {
            goose.render(goose, graphics);
        }

        public void Honk()
        {
            if (!audioOpen)
            {
                audioOpen = Native.mciSendString("open \"" + honkPath + "\" type mpegvideo alias classroomGooseDelivery",
                    null, 0, IntPtr.Zero) == 0;
                if (audioOpen)
                    Native.mciSendString("setaudio classroomGooseDelivery volume to 240", null, 0, IntPtr.Zero);
            }
            if (!audioOpen) return;
            Native.mciSendString("seek classroomGooseDelivery to start", null, 0, IntPtr.Zero);
            Native.mciSendString("play classroomGooseDelivery", null, 0, IntPtr.Zero);
        }

        public void Dispose()
        {
            if (audioOpen)
                Native.mciSendString("close classroomGooseDelivery", null, 0, IntPtr.Zero);
        }

        private sealed class Pose : GooseTaskData
        {
            internal bool Holding;
        }

        private sealed class PassiveTask : GooseTaskInfo
        {
            internal PassiveTask()
            {
                taskID = "Wander";
                canBePickedRandomly = false;
            }

            public override GooseTaskData GetNewTaskData(GooseEntity g) { return new Pose(); }
            public override void RunTask(GooseEntity g)
            {
                g.extendingNeck = ((Pose)g.currentTaskData).Holding;
            }
        }
    }
}
