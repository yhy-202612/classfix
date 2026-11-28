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
        private enum Motion { Hidden, Entering, Wandering, Departing, Delivering, WalkingToNest, RestingInNest }

        private readonly int ownPid = Process.GetCurrentProcess().Id;
        private readonly IGoose goose;
        private readonly NestWindow nest;
        private readonly ChatWindow chat;
        private readonly TouchKeyboard keyboard;
        private DeepSeek api;
        private readonly HotkeyWindow hotkey;
        private readonly Timer policy = new Timer { Interval = 180 };
        private readonly Stopwatch previewClock = new Stopwatch();
        
        private Motion phase;
        private Point target;
        private bool enabled;
        private bool paused;
        private bool slideshow;
        private bool weekends;
        private bool debugAlwaysShow;
        private bool fullScreen;
        private bool pendingDelivery;
        private bool closed;
        private bool heldForChat;
        private bool captureAfterClose;
        private string apiKey;
        private CancellationTokenSource request;
        private Reply latest;
        private IntPtr returnFocus;
        private IntPtr previewHost;
        private Rectangle previewWork;
        internal event Action CircleRequested;
        internal event Action<string> VisibilityChanged;
        internal string VisibilityStatus { get; private set; }

        internal GooseApplication(IGoose original, HostMessage configuration)
        {
            if (original != null)
            {
                goose = original;
                goose.Frame += Advance;
                nest = new NestWindow(goose);
                goose.Tapped += point =>
                {
                    if (CanAppear && phase != Motion.Delivering)
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
                if (heldForChat && phase == Motion.Wandering) goose.Wander();
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
            Configure(configuration);
            policy.Start();
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
            bool nearGoose = CanAppear && goose.Visible && phase != Motion.Delivering;
            ShowChat(nearGoose ? goose.Beak : Cursor.Position, nearGoose);
        }

        internal string Preview(IntPtr window)
        {
            if (!enabled || goose == null || paused || slideshow || Native.SlideshowWindow() != IntPtr.Zero ||
                Native.IsFullScreen(ownPid, window))
            {
                ApplyPolicy();
                return VisibilityStatus;
            }
            previewHost = window;
            previewWork = (window == IntPtr.Zero ? Screen.PrimaryScreen : Screen.FromHandle(window)).WorkingArea;
            previewClock.Restart();
            ApplyPolicy();
            if (CanAppear && (!goose.Visible || !Work.Contains(goose.Position)))
            {
                StopDelivery(CanRetainDelivery());
                int x = Work.Right - Math.Min(160, Work.Width / 2);
                goose.TargetScreen(Work);
                goose.ResetPosition(new Point(x, WalkY()), false);
                goose.Show();
                ChooseWander();
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
                goose.TargetScreen(screen.Bounds);
                nest.ShowOn(screen.Bounds);
                if (phase != Motion.WalkingToNest && phase != Motion.RestingInNest)
                {
                    StopDelivery(CanRetainDelivery());
                    if (!goose.Visible)
                    {
                        Point rest = nest.RestPoint;
                        Rectangle bounds = screen.Bounds;
                        bool fromLeft = rest.X < bounds.Left + bounds.Width / 2;
                        goose.ResetPosition(new Point(fromLeft ? bounds.Left - 104 : bounds.Right + 104,
                                                      rest.Y), fromLeft);
                    }
                    StartMotion(Motion.WalkingToNest);
                    goose.Show();
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
                goose.TargetScreen(Work);
                if (phase == Motion.Hidden)
                    goose.ResetPosition(Outside(true), false);
                goose.Show();
                target = new Point(Work.Right - 125, WalkY());
                StartMotion(Motion.Entering);
            }
            UpdateVisibilityStatus(Previewing ? "Desktop Goose preview is active for 30 seconds."
                : goose.Visible ? "Desktop Goose is visible on the primary display."
                : "Desktop Goose is walking in from the right edge of the primary display.");
        }

        private void StartMotion(Motion next) { phase = next; }

        private bool CanRetainDelivery() { return InSchedule && phase == Motion.Delivering && goose.IsBringingNotepad; }

        private void Advance()
        {
            if (phase == Motion.Hidden || phase == Motion.RestingInNest) return;

            if (heldForChat)
            {
                goose.Move(goose.Position);
                return;
            }
            if (phase == Motion.Delivering)
            {
                if (!goose.IsBringingNotepad)
                {
                    goose.FinishNotepad();
                    phase = Motion.Wandering;
                }
                return;
            }
            if (phase == Motion.Wandering)
            {
                if (pendingDelivery && CanAppear) TryBeginDelivery();
                return;
            }

            Point destination = phase == Motion.WalkingToNest ? nest.RestPoint : target;
            if (!goose.Move(destination)) return;

            switch (phase)
            {
                case Motion.Entering: ChooseWander(); break;
                case Motion.Departing: HideGoose(); break;
                case Motion.WalkingToNest:
                    nest.Settle();
                    goose.Hide();
                    phase = Motion.RestingInNest;
                    break;
            }
        }

        private void ChooseWander()
        {
            phase = Motion.Wandering;
            goose.Wander();
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
            goose.BringNotepad(latest.Text, Work);
            phase = Motion.Delivering;
        }

        private void StopDelivery(bool keep)
        {
            if (keep) pendingDelivery = latest != null && latest.CanDeliver;
            if (!InSchedule) pendingDelivery = false;
            goose.CloseNotepad();
        }

        private void HideGoose()
        {
            phase = Motion.Hidden;
            if (goose != null) goose.Hide();
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

            var current = new CancellationTokenSource();
            request = current;
            latest = null;
            chat.ClearQuestion();
            chat.SetState(true, null);
            try
            {
                var response = await AskDeepSeek(question, current.Token);
                if (closed || request != current || current.IsCancellationRequested) return;
                latest = response;
                pendingDelivery = response.CanDeliver && mayDeliver && InSchedule && goose != null &&
                                  Work.Width >= 460 && Work.Height >= 350;
                if (pendingDelivery && CanAppear) chat.Dismiss(true);
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
                closed = true;
                if (request != null) request.Cancel();
                policy.Dispose();
                keyboard.Dispose();
                hotkey.Dispose();
                chat.Dispose();
                if (nest != null) nest.Dispose();
                if (goose != null) goose.Dispose();
                if (api != null) api.Dispose();
            }
            base.Dispose(disposing);
        }
    }
}
