using System;
using System.Collections.Generic;
using System.Drawing;
using System.Reflection;
using System.Windows.Forms;
using GooseShared;
using SamEngine;

namespace ClassroomGoose
{
    // This DLL is loaded by the v0.31 mod loader inside GooseDesktop.exe.
    public sealed class EveTechMod : IMod
    {
        public void Init()
        {
            string pipe = Environment.GetEnvironmentVariable("EVETECH_GOOSE_PIPE");
            if (String.IsNullOrEmpty(pipe)) return;

            var stock = Assembly.GetEntryAssembly();
            var database = stock.GetType("GooseDesktop.Refactor.GooseTasks.GooseTaskDatabase", true);
            // The SDK queries the task pool but has no setter. This field is pinned to v0.31.
            var random = (List<int>)database.GetField("randomlyPickableTaskIndices",
                BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            random.Clear();
            random.Add(API.TaskDatabase.getTaskIndexByID("Wander"));

            var form = (Form)stock.GetType("GooseDesktop.Program", true).GetField("mainForm").GetValue(null);
            var config = stock.GetType("GooseDesktop.GooseConfig", true).GetField("settings").GetValue(null);
            config.GetType().GetField("FirstWanderTimeSeconds").SetValue(config, 20f);
            config.GetType().GetField("MinWanderingTimeSeconds").SetValue(config, 20f);
            config.GetType().GetField("MaxWanderingTimeSeconds").SetValue(config, 40f);
            InjectionPoints.PreTickEventHandler attach = null;
            attach = entity =>
            {
                InjectionPoints.PreTickEvent -= attach;
                var goose = new StockGoose(entity, form);
                Program.StartMod(pipe, goose, form);
            };
            InjectionPoints.PreTickEvent += attach;
        }
    }

    public sealed class ClassroomWalk : GooseTaskInfo
    {
        internal const string Id = "EveTechWalk";
        public ClassroomWalk()
        {
            taskID = Id;
            canBePickedRandomly = false;
        }

        private sealed class WalkData : GooseTaskData { }

        public override GooseTaskData GetNewTaskData(GooseEntity goose)
        {
            API.Goose.setSpeed(goose, GooseEntity.SpeedTiers.Walk);
            return new WalkData();
        }

        public override void RunTask(GooseEntity goose) { }
    }

    internal interface IGoose : IDisposable
    {
        Point Position { get; }
        Point Beak { get; }
        bool Visible { get; }
        event Action<Point> Tapped;
        event Action Frame;
        void TargetScreen(Rectangle area);
        void ResetPosition(Point point, bool facesRight);
        bool Move(Point target);
        void Wander();
        void Show();
        void Hide();
        void Draw(Graphics graphics);
        void BringNotepad(string answer, Rectangle work);
        bool IsBringingNotepad { get; }
        void FinishNotepad();
        void CloseNotepad();
    }

    internal sealed class StockGoose : IGoose
    {
        private readonly GooseEntity goose;
        private readonly Form form;
        private readonly Control canvas;
        private readonly GooseEntity.RenderFunction draw;
        private readonly int classroomWalk;
        private readonly string honkPath;
        private NotepadDelivery notepad;
        private bool audioOpen;
        private bool shown;
        private bool clickThrough = true;
        private Point origin;
        public event Action<Point> Tapped;
        public event Action Frame;

        internal StockGoose(GooseEntity goose, Form form)
        {
            this.goose = goose;
            this.form = form;
            canvas = form.Controls[0];
            honkPath = System.IO.Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                @"Assets\Sound\NotEmbedded\Honk1.mp3");
            classroomWalk = API.TaskDatabase.getTaskIndexByID(ClassroomWalk.Id);
            draw = goose.render;
            goose.render = (entity, graphics) => { if (shown) draw(entity, graphics); };
            canvas.MouseClick += Click;
            form.ShowInTaskbar = false;
            form.Text = "EveTech · Desktop Goose";
            Native.SetGooseWindowStyle(form.Handle, true);
            InjectionPoints.PostUpdateRigEvent += RenderFrame;
        }

        private Point ScreenPoint(Vector2 value)
        {
            return new Point(origin.X + (int)Math.Round(value.x), origin.Y + (int)Math.Round(value.y));
        }

        private Vector2 LocalPoint(Point value) { return new Vector2(value.X - origin.X, value.Y - origin.Y); }
        public Point Position { get { return ScreenPoint(goose.position); } }
        public Point Beak { get { return ScreenPoint(goose.rig.head2EndPoint); } }
        public bool Visible { get { return shown; } }

        public void TargetScreen(Rectangle area)
        {
            if (form.Bounds == area) return;
            // Preserve the goose's position on the desktop when moving the native canvas.
            Point last = Position;
            origin = area.Location;
            form.Bounds = area;
            ResetPosition(last, goose.direction < 90 || goose.direction > 270);
        }

        public void ResetPosition(Point point, bool facesRight)
        {
            goose.position = LocalPoint(point);
            goose.velocity = Vector2.zero;
            goose.direction = facesRight ? 0 : 180;
            // Clear a step that was in progress before moving between monitors or starting a preview.
            goose.rig.feets = new ProceduralFeets();
            goose.rig.feets.lFootPos = goose.position;
            goose.rig.feets.rFootPos = goose.position;
            goose.updateRig(goose.rig, goose.position, goose.direction);
        }

        public bool Move(Point target)
        {
            if (goose.currentTask != classroomWalk)
                API.Goose.setCurrentTaskByID(goose, ClassroomWalk.Id, false);
            goose.targetPos = LocalPoint(target);
            goose.currentAcceleration = goose.parameters.AccelerationNormal;
            if (!API.Goose.isGooseAtTarget(goose, 7)) return false;
            goose.velocity = Vector2.zero;
            goose.currentAcceleration = 0;
            return true;
        }

        public void Wander()
        {
            goose.targetPos = goose.position;
            API.Goose.setTaskRoaming(goose);
        }
        public void Show() { shown = true; }
        public void Hide() { shown = false; goose.velocity = Vector2.zero; }

        private void RenderFrame(GooseEntity entity)
        {
            if (Frame != null) Frame();
            Point center = ScreenPoint(entity.rig.bodyCenter);
            Point beak = Beak;
            var body = Rectangle.FromLTRB(Math.Min(center.X, beak.X) - 19, Math.Min(center.Y, beak.Y) - 12,
                Math.Max(center.X, beak.X) + 19, Position.Y + 7);
            bool pass = !shown || !body.Contains(Cursor.Position);
            if (pass != clickThrough)
            {
                clickThrough = pass;
                Native.SetGooseWindowStyle(form.Handle, pass);
            }
        }

        private void Click(object sender, MouseEventArgs e)
        {
            if (shown && !clickThrough && e.Button == MouseButtons.Left && Tapped != null) Tapped(Beak);
        }

        public void Draw(Graphics graphics)
        {
            var saved = graphics.Save();
            graphics.TranslateTransform(origin.X, origin.Y);
            draw(goose, graphics);
            graphics.Restore(saved);
        }

        public void BringNotepad(string answer, Rectangle work)
        {
            CloseNotepad();
            API.Goose.setCurrentTaskByID(goose, "CollectNotepad", false);
            notepad = new NotepadDelivery(goose.currentTaskData, answer, work, origin);
        }

        public bool IsBringingNotepad
        {
            get { return notepad != null && ReferenceEquals(goose.currentTaskData, notepad.Task); }
        }

        public void FinishNotepad()
        {
            if (notepad != null && notepad.Release()) Honk();
        }

        public void CloseNotepad()
        {
            if (notepad == null) return;
            if (IsBringingNotepad) Wander();
            notepad.Close();
            notepad = null;
        }

        private void Honk()
        {
            if (!audioOpen)
            {
                audioOpen = Native.mciSendString("open \"" + honkPath + "\" type mpegvideo alias evetechDelivery",
                    null, 0, IntPtr.Zero) == 0;
                if (audioOpen) Native.mciSendString("setaudio evetechDelivery volume to 240", null, 0, IntPtr.Zero);
            }
            if (audioOpen)
            {
                Native.mciSendString("seek evetechDelivery to start", null, 0, IntPtr.Zero);
                Native.mciSendString("play evetechDelivery", null, 0, IntPtr.Zero);
            }
        }

        public void Dispose()
        {
            CloseNotepad();
            InjectionPoints.PostUpdateRigEvent -= RenderFrame;
            canvas.MouseClick -= Click;
            if (audioOpen) Native.mciSendString("close evetechDelivery", null, 0, IntPtr.Zero);
        }
    }
}
