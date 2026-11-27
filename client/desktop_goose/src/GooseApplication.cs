using System;
using System.Diagnostics;
using System.Drawing;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Timer = System.Windows.Forms.Timer;

namespace ClassroomGoose
{
    internal sealed class GooseApplication : ApplicationContext
    {
        private enum Motion { Hidden, Entering, Wandering, Departing, Fetching, Delivering, WalkingToNest, RestingInNest }

        private readonly int ownPid = Process.GetCurrentProcess().Id;
        private readonly IGoose goose;
        private readonly GooseWindow pet;
        private readonly NestWindow nest;
        private readonly ChatWindow chat;
        private readonly TouchKeyboard keyboard;
        private DeepSeek api;
        private readonly HotkeyWindow hotkey;
        private readonly Timer policy = new Timer { Interval = 180 };
        private readonly Timer motion = new Timer { Interval = 16 };
        private readonly Stopwatch motionClock = Stopwatch.StartNew();
        private readonly Stopwatch previewClock = new Stopwatch();
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
        private bool debugAlwaysShow;
        private bool fullScreen;
        private bool fromRight;
        private bool pendingDelivery;
        private bool closed;
        private bool heldForChat;
        private bool captureAfterClose;
        private string apiKey;
        private CancellationTokenSource request;
        private AnswerCard card;
        private Reply latest;
        private IntPtr returnFocus;
        private IntPtr previewHost;
        private Rectangle previewWork;
        internal event Action CircleRequested;
        internal event Action<string> VisibilityChanged;
        internal string VisibilityStatus { get; private set; }

        internal GooseApplication(string originalFolder, HostMessage configuration)
        {
            if (originalFolder != null)
            {
                goose = LoadGoose(originalFolder);
                pet = new GooseWindow(goose);
                nest = new NestWindow(goose);
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
            chat.TypingRequested += () => keyboard.BeginOwnInput(chat.Handle);
            chat.CircleRequested += () =>
            {
                captureAfterClose = true;
                chat.Dismiss(true);
            };
            chat.ClosingBubble += () => keyboard.EndOwnInput(chat.Handle);
            chat.BubbleHidden += () =>
            {
                heldForChat = false;
                if (returnFocus != IntPtr.Zero && OwnForeground())
                    Native.SetForegroundWindow(returnFocus);
                returnFocus = IntPtr.Zero;
                if (captureAfterClose)
                {
                    captureAfterClose = false;
                    if (enabled && CircleRequested != null) CircleRequested();
                }
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

        private bool Previewing { get { return previewClock.IsRunning && previewClock.Elapsed.TotalSeconds < 30; } }
        private bool InSchedule { get { return Schedule.IsOpen(DateTime.Now, weekends, debugAlwaysShow || Previewing); } }
        private bool CanAppear { get { return goose != null && enabled && InSchedule && !paused && !fullScreen; } }
        private Rectangle Work { get { return Previewing ? previewWork : Screen.PrimaryScreen.WorkingArea; } }

        internal void Configure(HostMessage value)
        {
            bool wasEnabled = enabled;
            enabled = value.Goose;
            paused = value.Paused;
            slideshow = value.Slideshow;
            weekends = value.Weekends;
            debugAlwaysShow = value.DebugAlwaysShow;
            string updatedKey = value.Token ?? "";
            if (!enabled || apiKey != updatedKey) CancelQuestion();
            apiKey = updatedKey;
            keyboard.Enable(value.Keyboard);
            if (wasEnabled != enabled)
            {
                hotkey.SetEnabled(enabled);
                if (!enabled)
                {
                    previewClock.Reset();
                    chat.Dismiss(false);
                }
            }
            ApplyPolicy();
        }

        internal void OpenFromHost()
        {
            if (!enabled) return;
            bool nearGoose = CanAppear && pet != null && pet.Visible &&
                             phase != Motion.Delivering && phase != Motion.Fetching;
            ShowChat(nearGoose ? goose.Beak : Cursor.Position, nearGoose);
        }

        internal string Preview(IntPtr window)
        {
            Program.Trace("Preview entered " + window);
            if (!enabled || goose == null || paused || slideshow || Native.SlideshowWindow() != IntPtr.Zero ||
                Native.IsFullScreen(ownPid, window))
            {
                ApplyPolicy();
                Program.Trace("Preview blocked: " + VisibilityStatus);
                return VisibilityStatus;
            }
            previewHost = window;
            previewWork = (window == IntPtr.Zero ? Screen.PrimaryScreen : Screen.FromHandle(window)).WorkingArea;
            Program.Trace("Preview work area " + previewWork);
            previewClock.Restart();
            ApplyPolicy();
            Program.Trace("Preview policy applied");
            if (CanAppear && (!pet.Visible || !Work.Contains(goose.Position)))
            {
                StopDelivery(CanRetainDelivery());
                int x = Work.Right - Math.Min(160, Work.Width / 2);
                goose.ResetPosition(new Point(x, WalkY()), false);
                Program.Trace("Preview position reset");
                pet.UpdatePose(Work);
                Program.Trace("Preview pose updated");
                ChooseWander();
                motion.Start();
            }
            if (CanAppear && pet.Visible)
            {
                Native.RaiseWithoutFocus(pet.Handle);
                Program.Trace("Preview window raised");
                pet.Update();
                Program.Trace("Preview window painted");
            }
            return VisibilityStatus;
        }

        private void UpdateVisibilityStatus(string message)
        {
            if (message == VisibilityStatus) return;
            VisibilityStatus = message;
            if (VisibilityChanged != null) VisibilityChanged(message);
        }

        private void ShowChat(Point anchor, bool nearGoose)
        {
            if (!OwnForeground()) returnFocus = Native.GetForegroundWindow();
            heldForChat = nearGoose;
            chat.SetState(request != null, latest);
            chat.Present(anchor, nearGoose, keyboard.Bounds);
        }

        private bool OwnForeground()
        {
            int pid;
            Native.GetWindowThreadProcessId(Native.GetForegroundWindow(), out pid);
            return pid == ownPid;
        }

        private void ApplyPolicy()
        {
            IntPtr slide = Native.SlideshowWindow();
            bool presenting = slideshow || slide != IntPtr.Zero;
            fullScreen = presenting || Native.IsFullScreen(ownPid, Previewing ? previewHost : IntPtr.Zero);
            // After a preview on another display, resume the normal primary-screen route.
            if (!Previewing && previewClock.IsRunning)
            {
                previewClock.Reset();
                if (previewWork != Work && !presenting)
                {
                    StopDelivery(CanRetainDelivery());
                    HideGoose();
                }
            }
            keyboard.FollowFocus();
            if (chat.Visible)
            {
                if (chat.IsAttached && !CanAppear) chat.Dismiss(false);
                else chat.Place(keyboard.Bounds);
            }

            if (goose == null)
            {
                UpdateVisibilityStatus(enabled
                    ? "The original goose files could not be loaded. Choose the extracted Desktop Goose v0.31 folder."
                    : "Desktop Goose is off.");
                return;
            }
            if (presenting && enabled && !paused)
            {
                var screen = slide != IntPtr.Zero ? Screen.FromHandle(slide) : Screen.PrimaryScreen;
                nest.ShowOn(screen.Bounds);
                if (phase != Motion.WalkingToNest && phase != Motion.RestingInNest)
                {
                    StopDelivery(CanRetainDelivery());
                    if (!pet.Visible)
                    {
                        Point rest = nest.RestPoint;
                        Rectangle bounds = screen.Bounds;
                        bool fromLeft = rest.X < bounds.Left + bounds.Width / 2;
                        goose.ResetPosition(new Point(fromLeft ? bounds.Left - 104 : bounds.Right + 104,
                                                      rest.Y), fromLeft);
                    }
                    StartMotion(Motion.WalkingToNest);
                    if (pet.Visible) Native.RaiseWithoutFocus(pet.Handle);
                }
                UpdateVisibilityStatus(phase == Motion.RestingInNest
                    ? "Desktop Goose is resting in its PowerPoint nest."
                    : "Desktop Goose is walking to its PowerPoint nest.");
                return;
            }
            if (nest.Visible)
            {
                nest.Hide();
                HideGoose();
            }
            if (fullScreen || paused || !enabled)
            {
                StopDelivery(CanRetainDelivery());
                if (!enabled) pendingDelivery = false;
                HideGoose();
                UpdateVisibilityStatus(!enabled ? "Desktop Goose is off."
                    : paused ? "Desktop Goose is paused. Clear Pause Desktop Goose in the tray."
                    : "Desktop Goose is hidden by a full-screen window. Exit full screen to see it.");
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
                UpdateVisibilityStatus("Outside the school schedule. Use Preview Goose to see it for 30 seconds.");
                return;
            }

            if (phase == Motion.Hidden || phase == Motion.Departing)
            {
                if (phase == Motion.Hidden)
                    goose.ResetPosition(Outside(true), false);
                target = new Point(Work.Right - 125, WalkY());
                StartMotion(Motion.Entering);
            }
            UpdateVisibilityStatus(Previewing ? "Desktop Goose preview is active for 30 seconds."
                : pet.Visible ? "Desktop Goose is visible on the primary display."
                : "Desktop Goose is walking in from the right edge of the primary display.");
        }

        private void StartMotion(Motion next)
        {
            phase = next;
            lastFrame = motionClock.Elapsed.TotalSeconds;
            carrySeconds = 0;
            motion.Start();
        }

        private bool CanRetainDelivery() { return InSchedule && card != null && phase == Motion.Delivering; }

        private void Advance()
        {
            if (phase == Motion.Hidden || phase == Motion.RestingInNest) return;
            const double step = 1.0 / 120;
            double now = motionClock.Elapsed.TotalSeconds;
            carrySeconds += Math.Min(.07, now - lastFrame);
            lastFrame = now;
            // Timer ticks without a movement step must not reset the wander pause.
            if (carrySeconds < step) return;
            bool arrived = false;
            Point destination = phase == Motion.WalkingToNest ? nest.RestPoint : target;
            while (carrySeconds >= step)
            {
                arrived = goose.Move(heldForChat ? goose.Position : destination, phase == Motion.Delivering);
                carrySeconds -= step;
            }

            if (phase == Motion.WalkingToNest)
            {
                if (arrived)
                {
                    nest.Settle();
                    pet.Hide();
                    phase = Motion.RestingInNest;
                    motion.Stop();
                }
                else
                {
                    pet.UpdatePose(Screen.FromPoint(goose.Position).Bounds);
                    if (pet.Visible) Native.RaiseWithoutFocus(pet.Handle);
                }
                return;
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
                latest = new Reply("To get answers, configure a DeepSeek API key in EveTech Desktop Pet settings.", false);
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
                var response = await AskDeepSeek(question, current.Token);
                if (closed || request != current || current.IsCancellationRequested) return;
                latest = response;
                pendingDelivery = response.CanDeliver && mayDeliver && InSchedule && goose != null;
            }
            catch (OperationCanceledException) { }
            catch (Exception)
            {
                if (request == current && !closed)
                    latest = new Reply("The answer could not be read. Please try again.", false);
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

        [MethodImpl(MethodImplOptions.NoInlining)]
        private Task<Reply> AskDeepSeek(string question, CancellationToken cancellation)
        {
            if (api == null) api = new DeepSeek();
            return api.Ask(question, apiKey, cancellation);
        }

        private void CancelQuestion()
        {
            if (request == null) return;
            request.Cancel();
            request = null;
            latest = new Reply("Response canceled.", false);
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
                if (nest != null) nest.Dispose();
                if (goose != null) goose.Dispose();
                if (api != null) api.Dispose();
            }
            base.Dispose(disposing);
        }
    }
}
