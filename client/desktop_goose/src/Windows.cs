using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace ClassroomGoose
{
    internal sealed class ChatWindow : Form
    {
        private readonly TextBox editor;
        private readonly Label status;
        private readonly RichTextBox answer;
        private readonly Button send;
        private readonly Button cancel;
        private readonly Button type;
        private readonly Timer transition = new Timer { Interval = 15 };
        private readonly Stopwatch animation = new Stopwatch();
        private readonly Stopwatch resizeAnimation = new Stopwatch();
        private Point anchor;
        private Point finalPosition;
        private Rectangle keyboardBounds;
        private int startHeight;
        private int targetHeight;
        private int startOffset;
        private double startOpacity;
        private bool tailAtLeft;
        private bool closing;
        private bool attached;
        private bool typing;
        private bool waiting;
        internal event Action<string> Submitted;
        internal event Action CancellationRequested;
        internal event Action CircleRequested;
        internal event Action TypingRequested;
        internal event Action ClosingBubble;
        internal event Action BubbleHidden;

        internal ChatWindow()
        {
            SetStyle(ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true);
            AutoScaleMode = AutoScaleMode.None;
            Font = new Font("Segoe UI", 10);
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;
            ShowInTaskbar = false;
            TopMost = true;
            Text = "EveTech · Ask Goose";
            KeyPreview = true;
            BackColor = Color.FromArgb(252, 249, 241);
            ClientSize = new Size(416, 110);
            var title = new Label { Text = "Ask Goose", AutoSize = true, Font = new Font(Font, FontStyle.Bold), Location = new Point(24, 14) };
            var close = new Button { Text = "Close", Size = new Size(65, 34), Location = new Point(326, 9), TabStop = false };
            close.Click += delegate { Dismiss(true); };
            var circle = new Button { Text = "Circle Screen", Bounds = new Rectangle(24, 53, 177, 42) };
            circle.Click += delegate { if (!closing && CircleRequested != null) CircleRequested(); };
            type = new Button { Text = "Type Question", Bounds = new Rectangle(211, 53, 179, 42) };
            type.Click += delegate
            {
                if (closing) return;
                typing = true;
                UpdateContent(true);
                editor.Focus();
                if (TypingRequested != null) TypingRequested();
            };
            editor = new TextBox
            {
                Multiline = true, AcceptsReturn = true, ScrollBars = ScrollBars.Vertical,
                MaxLength = DeepSeek.QuestionLimit, Location = new Point(24, 108),
                Width = 366, Height = 74, AccessibleName = "Question"
            };
            status = new Label { Location = new Point(24, 192), Width = 260, Height = 49, AutoEllipsis = true };
            send = new Button { Text = "Send", Location = new Point(312, 195), Size = new Size(78, 44) };
            send.Click += delegate { Submit(); };
            cancel = new Button { Text = "Cancel", Location = new Point(298, 195), Size = new Size(92, 44), Visible = false };
            cancel.Click += delegate { if (CancellationRequested != null) CancellationRequested(); };
            answer = new RichTextBox
            {
                ReadOnly = true, DetectUrls = false, WordWrap = true, ScrollBars = RichTextBoxScrollBars.Vertical,
                AccessibleName = "Latest answer", Location = new Point(24, 246), Size = new Size(366, 124), Visible = false
            };
            Controls.AddRange(new Control[] { title, close, circle, type, editor, status, send, cancel, answer });
            UpdateContent(false);
            transition.Tick += delegate { Animate(); };
        }

        internal bool IsAttached { get { return attached; } }

        internal void Present(Point source, bool nearGoose, Rectangle keyboard)
        {
            bool alreadyOpen = Visible && !closing;
            anchor = source;
            attached = nearGoose;
            closing = false;
            if (!alreadyOpen)
            {
                typing = false;
                UpdateContent(false);
            }
            Place(keyboard);
            if (!alreadyOpen)
            {
                Opacity = 0.12;
                startOpacity = Opacity;
                startOffset = 6;
                Location = Offset(finalPosition, 6);
                animation.Restart();
                Show();
                transition.Start();
            }
            Activate();
            if (!alreadyOpen) type.Focus();
        }

        internal void SetState(bool waiting, Reply reply)
        {
            this.waiting = waiting;
            status.Text = waiting ? "Writing a reply…" : reply != null && !reply.CanDeliver ? reply.Text : "";
            string text = reply != null && reply.CanDeliver ? reply.Text : "";
            if (answer.Text != text) answer.Text = text;
            UpdateContent(Visible && !closing);
        }

        private void UpdateContent(bool animate)
        {
            editor.Visible = typing;
            status.Visible = typing;
            send.Visible = typing && !waiting;
            cancel.Visible = typing && waiting;
            bool hasAnswer = answer.TextLength != 0;
            answer.Visible = typing && hasAnswer;
            int height = typing ? (hasAnswer ? 386 : 253) : 110;
            if (animate && ClientSize.Height != height && (!resizeAnimation.IsRunning || targetHeight != height))
            {
                startHeight = ClientSize.Height;
                targetHeight = height;
                resizeAnimation.Restart();
                transition.Start();
            }
            else if (!animate)
            {
                resizeAnimation.Reset();
                ResizeBubble(height);
            }
        }

        private void ResizeBubble(int height)
        {
            if (ClientSize.Height == height) return;
            ClientSize = new Size(ClientSize.Width, height);
            UpdateShape();
        }

        internal void Place(Rectangle keyboard)
        {
            keyboardBounds = keyboard;
            Rectangle work = Screen.FromPoint(anchor).WorkingArea;
            Rectangle blocked = Rectangle.Intersect(work, keyboard);
            int right = anchor.X + 16;
            bool nextTailAtLeft = right + Width <= work.Right || anchor.X - Width - 16 < work.Left;
            if (tailAtLeft != nextTailAtLeft || Region == null)
            {
                tailAtLeft = nextTailAtLeft;
                UpdateShape();
            }
            int left = tailAtLeft ? right : anchor.X - Width - 16;
            int top = anchor.Y - 46;
            left = Math.Max(work.Left + 6, Math.Min(work.Right - Width - 6, left));
            top = Math.Max(work.Top + 6, Math.Min(work.Bottom - Height - 6, top));
            if (blocked.IntersectsWith(new Rectangle(left, top, Width, Height)))
            {
                int above = blocked.Top - Height - 6;
                int below = blocked.Bottom + 6;
                if (above >= work.Top + 6) top = above;
                else if (below + Height <= work.Bottom - 6) top = below;
                else top = work.Top + 6;
            }
            finalPosition = new Point(left, top);
            if (!transition.Enabled && Location != finalPosition) Location = finalPosition;
        }

        internal void Dismiss(bool animate, bool immediate = false)
        {
            if (!Visible) return;
            if (closing && animate && !immediate) return;
            if (!closing && ClosingBubble != null) ClosingBubble();
            if (!animate || immediate)
            {
                FinishClosing();
                return;
            }
            closing = true;
            startOpacity = Opacity;
            startOffset = (Left - finalPosition.X) * (tailAtLeft ? -1 : 1);
            animation.Restart();
            transition.Start();
        }

        private void Animate()
        {
            if (resizeAnimation.IsRunning)
            {
                double resize = Math.Min(1, resizeAnimation.Elapsed.TotalMilliseconds / 170.0);
                ResizeBubble(startHeight + (int)Math.Round((targetHeight - startHeight) * Ease(resize)));
                Place(keyboardBounds);
                if (resize >= 1) resizeAnimation.Reset();
            }
            double t = Math.Min(1, animation.Elapsed.TotalMilliseconds / (closing ? 115 : 155));
            double progress = Ease(t);
            Opacity = closing ? startOpacity * (1 - progress) : startOpacity + progress * (1 - startOpacity);
            int endOffset = closing ? 6 : 0;
            Location = Offset(finalPosition, startOffset + (int)Math.Round((endOffset - startOffset) * progress));
            if (t >= 1 && (closing || !resizeAnimation.IsRunning))
            {
                transition.Stop();
                if (closing) FinishClosing();
            }
        }

        private static double Ease(double progress) { return 1 - Math.Pow(1 - progress, 3); }

        private Point Offset(Point position, int pixels)
        {
            return new Point(position.X + (tailAtLeft ? -pixels : pixels), position.Y);
        }

        private void FinishClosing()
        {
            transition.Stop();
            resizeAnimation.Reset();
            Hide();
            closing = false;
            if (BubbleHidden != null) BubbleHidden();
        }

        private void UpdateShape()
        {
            int left = tailAtLeft ? 10 : 1;
            int right = tailAtLeft ? Width - 1 : Width - 10;
            using (var outline = new GraphicsPath())
            {
                const int diameter = 24;
                outline.AddArc(left, 0, diameter, diameter, 180, 90);
                outline.AddArc(right - diameter, 0, diameter, diameter, 270, 90);
                outline.AddArc(right - diameter, Height - diameter, diameter, diameter, 0, 90);
                outline.AddArc(left, Height - diameter, diameter, diameter, 90, 90);
                outline.CloseFigure();
                var shape = new Region(outline);
                using (var tail = new GraphicsPath())
                {
                    int side = tailAtLeft ? left : right;
                    tail.AddPolygon(new[] { new Point(side, 34), new Point(tailAtLeft ? 0 : Width, 43), new Point(side, 54) });
                    shape.Union(tail);
                }
                Region = shape;
            }
        }

        private void Submit()
        {
            if (!send.Visible) return;
            string text = editor.Text.Trim();
            if (text.Length == 0)
            {
                editor.Focus();
                return;
            }
            if (Submitted != null) Submitted(text);
        }

        internal void ClearQuestion() { editor.Clear(); }

        protected override bool ProcessCmdKey(ref Message message, Keys keys)
        {
            if (keys == (Keys.Control | Keys.Enter))
            {
                Submit();
                return true;
            }
            if (keys == Keys.Escape)
            {
                Dismiss(true);
                return true;
            }
            return base.ProcessCmdKey(ref message, keys);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) transition.Dispose();
            base.Dispose(disposing);
        }
    }

    internal sealed class AnswerCard : Form
    {
        internal const int GripHeight = 29;
        private Rectangle lastClip;
        private readonly Button copy;
        private readonly Button close;

        internal AnswerCard(string answer, Size available)
        {
            AutoScaleMode = AutoScaleMode.None;
            Font = new Font("Segoe UI", 10);
            Text = "EveTech · Answer from Goose";
            StartPosition = FormStartPosition.Manual;
            FormBorderStyle = FormBorderStyle.None;
            BackColor = Color.FromArgb(254, 250, 236);
            TopMost = true;
            ShowInTaskbar = false;
            ClientSize = new Size(Math.Min(420, available.Width - 40), Math.Min(260, available.Height - 70));
            var title = new Label
            {
                Text = "Answer from Goose", Font = new Font(Font, FontStyle.Bold), AutoSize = true, Location = new Point(16, 14)
            };
            var body = new RichTextBox
            {
                Text = answer, ReadOnly = true, DetectUrls = false, ScrollBars = RichTextBoxScrollBars.Vertical,
                AccessibleName = "Answer", BorderStyle = BorderStyle.None, BackColor = BackColor,
                Bounds = new Rectangle(16, 46, Width - 32, Height - 106)
            };
            copy = new Button { Text = "Copy", Bounds = new Rectangle(16, Height - 48, 68, 36), Enabled = false };
            copy.Click += delegate { try { Clipboard.SetText(answer); copy.Text = "Copied"; } catch (System.Runtime.InteropServices.ExternalException) { } };
            close = new Button { Text = "Close", Bounds = new Rectangle(Width - 83, Height - 48, 68, 36), Enabled = false };
            close.Click += delegate { Close(); };
            Controls.AddRange(new Control[] { title, body, copy, close });
        }

        protected override bool ShowWithoutActivation { get { return true; } }

        internal void Follow(Point beak, bool fromRight, Rectangle screen)
        {
            Location = new Point(beak.X - (fromRight ? 0 : Width), beak.Y - GripHeight);
            var intersection = Rectangle.Intersect(Bounds, screen);
            if (intersection.IsEmpty)
            {
                if (Visible) Hide();
                return;
            }
            intersection.Offset(-Left, -Top);
            if (lastClip != intersection)
            {
                lastClip = intersection;
                Region = new Region(intersection);
            }
            if (!Visible)
            {
                Show();
                Native.RaiseWithoutFocus(Handle);
            }
        }

        internal void Release()
        {
            Region = null;
            copy.Enabled = true;
            close.Enabled = true;
        }
    }
}
