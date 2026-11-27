using System;
using System.Diagnostics;
using System.Drawing;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Windows.Forms;
using Timer = System.Windows.Forms.Timer;

namespace ClassroomGoose
{
    internal sealed class GooseApplication : ApplicationContext
    {
        private enum Motion { Hidden, Entering, Wandering, Departing, Fetching, Delivering }

        private readonly int ownPid = Process.GetCurrentProcess().Id;
        private readonly IGoose goose;
        private readonly GooseWindow pet;
        private readonly ChatWindow chat;
        private readonly TouchKeyboard keyboard;
        private readonly DeepSeek api = new DeepSeek();
        private readonly HotkeyWindow hotkey;
        private readonly Timer policy = new Timer { Interval = 180 };
        private readonly Timer motion = new Timer { Interval = 16 };
        private readonly Stopwatch motionClock = Stopwatch.StartNew();
        private readonly Random random = new Random();
        
        private Motion phase;
        private Point target;
        private DateTime restUntil;
        private double carrySeconds;
        private double lastFrame;
        private bool enabled;
        private bool paused;
        private bool slideshow;
        private bool weekends;
        private bool fullScreen;
        private bool fromRight;
        private bool pendingDelivery;
        private bool closed;
        private bool heldForChat;
        private string apiKey;
        private CancellationTokenSource request;
        private AnswerCard card;
        private Reply latest;
        private IntPtr returnFocus;

        internal GooseApplication(string originalFolder, HostMessage configuration)
        {
            if (originalFolder != null)
            {
                goose = LoadGoose(originalFolder);
                pet = new GooseWindow(goose);
                pet.Tapped += point =>
                {
                    if (CanAppear && phase != Motion.Delivering && phase != Motion.Fetching)
                        ShowChat(point, true);
                };
            }

            keyboard = new TouchKeyboard();

            chat = new ChatWindow();
            chat.Submitted += Submit;
            chat.CancellationRequested += CancelQuestion;
            chat.ClosingBubble += () => keyboard.EndOwnInput(chat.Handle);
            chat.BubbleHidden += () =>
            {
                heldForChat = false;
                if (returnFocus != IntPtr.Zero && OwnForeground())
                    Native.SetForegroundWindow(returnFocus);
                returnFocus = IntPtr.Zero;
            };

            hotkey = new HotkeyWindow(OpenFromHost);

            policy.Tick += delegate { ApplyPolicy(); };
            motion.Tick += delegate { Advance(); };
            Configure(configuration);
            policy.Start();
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static IGoose LoadGoose(string folder)
        {
            return new StockGoose(folder);
        }

        private bool InSchedule { get { return Schedule.IsOpen(DateTime.Now, weekends); } }
        private bool CanAppear { get { return goose != null && enabled && InSchedule && !paused && !fullScreen; } }
        private Rectangle Work { get { return Screen.PrimaryScreen.WorkingArea; } }

        internal void Configure(HostMessage value)
        {
            enabled = value.Goose;
            paused = value.Paused;
            slideshow = value.Slideshow;
            weekends = value.Weekends;
            string updatedKey = value.Token ?? "";
            if (apiKey != updatedKey && request != null) CancelQuestion();
            apiKey = updatedKey;
            keyboard.Enable(value.Keyboard);
            ApplyPolicy();
        }

        internal void OpenFromHost()
        {
            bool nearGoose = CanAppear && pet != null && pet.Visible &&
                             phase != Motion.Delivering && phase != Motion.Fetching;
            ShowChat(nearGoose ? goose.Beak : Cursor.Position, nearGoose);
        }

        private void ShowChat(Point anchor, bool nearGoose)
        {
            if (!OwnForeground()) returnFocus = Native.GetForegroundWindow();
            heldForChat = nearGoose;
            chat.SetState(request != null, latest);
            chat.Present(anchor, nearGoose, keyboard.Bounds);
            keyboard.BeginOwnInput(chat.Handle);
        }

        private bool OwnForeground()
        {
            int pid;
            Native.GetWindowThreadProcessId(Native.GetForegroundWindow(), out pid);
            return pid == ownPid;
        }

        private void ApplyPolicy()
        {
            fullScreen = slideshow || Native.IsFullScreen(ownPid);
            keyboard.FollowFocus();
            if (chat.Visible)
            {
                if (chat.IsAttached && !CanAppear) chat.Dismiss(false);
                else chat.Place(keyboard.Bounds);
            }

            if (goose == null) return;
            if (fullScreen || paused || !enabled)
            {
                StopDelivery(CanRetainDelivery());
                HideGoose();
                return;
            }
            if (!InSchedule)
            {
                StopDelivery(false);
                if (phase != Motion.Hidden && phase != Motion.Departing)
                {
                    phase = Motion.Departing;
                    target = Outside(goose.Position.X > Work.Left + Work.Width / 2);
                }
                return;
            }

            if (phase == Motion.Hidden || phase == Motion.Departing)
            {
                if (phase == Motion.Hidden)
                    goose.ResetPosition(Outside(true), false);
                phase = Motion.Entering;
                target = new Point(Work.Right - 125, WalkY());
                lastFrame = motionClock.Elapsed.TotalSeconds;
                carrySeconds = 0;
                motion.Start();
            }
        }

        private bool CanRetainDelivery() { return InSchedule && card != null && phase == Motion.Delivering; }

        private void Advance()
        {
            if (phase == Motion.Hidden) return;
            double now = motionClock.Elapsed.TotalSeconds;
            carrySeconds += Math.Min(.07, now - lastFrame);
            lastFrame = now;
            bool arrived = false;
            while (carrySeconds >= 1.0 / 120)
            {
                arrived = goose.Move(heldForChat ? goose.Position : target, phase == Motion.Delivering);
                carrySeconds -= 1.0 / 120;
            }

            if (phase == Motion.Delivering && card != null)
                card.Follow(goose.Beak, fromRight, Work);
            pet.UpdatePose(Work);
            if (phase == Motion.Delivering && pet.Visible && card != null && card.Visible)
                Native.RaiseWithoutFocus(pet.Handle);

            if (heldForChat) return;
            if (phase == Motion.Wandering)
            {
                if (pendingDelivery && CanAppear)
                    TryBeginDelivery();
                else if (!arrived)
                    restUntil = DateTime.MinValue;
                else if (restUntil == DateTime.MinValue)
                    restUntil = DateTime.UtcNow.AddSeconds(random.Next(1, 4));
                else if (DateTime.UtcNow >= restUntil)
                    ChooseWander();
                return;
            }

            if (!arrived) return;
            switch (phase)
            {
                case Motion.Entering: ChooseWander(); break;
                case Motion.Departing: HideGoose(); break;
                case Motion.Fetching: StartDrag(); break;
                case Motion.Delivering:
                    if (card != null)
                    {
                        card.Release();
                        goose.Honk();
                    }
                    ChooseWander();
                    break;
            }
        }

        private void ChooseWander()
        {
            var work = Work;
            int padding = Math.Min(work.Width / 4, 90);
            int lane = Math.Min(work.Width - padding, Math.Max(padding + 1, 370));
            target = new Point(work.Right - random.Next(padding, lane),
                Math.Max(work.Top + 70, Math.Min(work.Bottom - 42, WalkY() + random.Next(-48, 70))));
            phase = Motion.Wandering;
            restUntil = DateTime.MinValue;
        }

        private int WalkY()
        {
            Rectangle area = Work;
            return area.Top + area.Height * 3 / 4;
        }

        private Point Outside(bool right)
        {
            Rectangle area = Work;
            return new Point(right ? area.Right + 104 : area.Left - 104, WalkY());
        }

        private void TryBeginDelivery()
        {
            pendingDelivery = false;
            if (latest == null || !latest.CanDeliver || Work.Width < 460 || Work.Height < 350) return;
            CloseCard();
            fromRight = goose.Position.X >= Work.Left + Work.Width / 2;
            phase = Motion.Fetching;
            target = Outside(fromRight);
        }

        private void StartDrag()
        {
            if (!CanAppear || latest == null || !latest.CanDeliver)
            {
                ChooseWander();
                return;
            }
            Rectangle area = Work;
            card = new AnswerCard(latest.Text, area.Size);
            card.FormClosed += delegate { card = null; };
            int margin = card.Width + 42;
            int y = Math.Min(WalkY(), area.Bottom - card.Height + AnswerCard.GripHeight - 19);
            target = new Point(fromRight ? area.Right - margin : area.Left + margin, y);
            phase = Motion.Delivering;
        }

        private void StopDelivery(bool keep)
        {
            if (keep || (phase == Motion.Fetching && InSchedule))
                pendingDelivery = latest != null && latest.CanDeliver;
            if (!InSchedule) pendingDelivery = false;
            CloseCard();
        }

        private void CloseCard()
        {
            if (card == null) return;
            var previous = card;
            card = null;
            previous.Dispose();
        }

        private void HideGoose()
        {
            phase = Motion.Hidden;
            motion.Stop();
            if (pet != null && pet.Visible) pet.Hide();
        }

        private async void Submit(string question)
        {
            if (request != null || closed) return;
            if (String.IsNullOrWhiteSpace(apiKey))
            {
                latest = new Reply("请在 ClassFix 的 AI 设置填入 DeepSeek API 密钥。", false);
                chat.SetState(false, latest);
                return;
            }

            bool mayDeliver = enabled && InSchedule;
            pendingDelivery = false;
            CloseCard();
            if (phase == Motion.Fetching || phase == Motion.Delivering)
                ChooseWander();

            var current = new CancellationTokenSource();
            request = current;
            latest = null;
            chat.ClearQuestion();
            chat.SetState(true, null);
            chat.Dismiss(true);
            try
            {
                var response = await api.Ask(question, apiKey, current.Token);
                if (closed || request != current || current.IsCancellationRequested) return;
                latest = response;
                pendingDelivery = response.CanDeliver && mayDeliver && InSchedule && goose != null;
            }
            catch (OperationCanceledException) { }
            catch (Exception)
            {
                if (request == current && !closed)
                    latest = new Reply("回答暂时无法读取，请重试。", false);
            }
            finally
            {
                if (request == current)
                {
                    request = null;
                    if (!closed) chat.SetState(false, latest);
                }
                current.Dispose();
            }
        }

        private void CancelQuestion()
        {
            if (request == null) return;
            request.Cancel();
            request = null;
            latest = new Reply("已取消生成。", false);
            chat.SetState(false, latest);
        }

        protected override void ExitThreadCore()
        {
            closed = true;
            if (request != null) request.Cancel();
            base.ExitThreadCore();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                policy.Dispose();
                motion.Dispose();
                keyboard.Dispose();
                hotkey.Dispose();
                chat.Dispose();
                CloseCard();
                if (pet != null) pet.Dispose();
                if (goose != null) goose.Dispose();
                api.Dispose();
            }
            base.Dispose(disposing);
        }
    }
}
