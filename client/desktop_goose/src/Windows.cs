using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace ClassroomGoose
{
    internal sealed class GooseWindow : Form
    {
        private readonly IGoose goose;
        private Rectangle clip;
        internal event Action<Point> Tapped;

        internal GooseWindow(IGoose goose)
        {
            this.goose = goose;
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer, true);
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            TopMost = true;
            StartPosition = FormStartPosition.Manual;
            ClientSize = new Size(150, 134);
            BackColor = Color.FromArgb(255, 0, 255);
            TransparencyKey = BackColor;
            AutoScaleMode = AutoScaleMode.None;
            AccessibleName = "问鹅";
        }

        protected override bool ShowWithoutActivation { get { return true; } }

        protected override CreateParams CreateParams
        {
            get
            {
                var parameters = base.CreateParams;
                parameters.ExStyle |= Native.NoActivate | Native.ToolWindow;
                return parameters;
            }
        }

        internal void UpdatePose(Rectangle screen)
        {
            clip = screen;
            Point at = goose.Position;
            Location = new Point(at.X - 75, at.Y - 70);
            if (!Bounds.IntersectsWith(screen))
            {
                Hide();
                return;
            }
            if (!Visible) Show();
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            e.Graphics.TranslateTransform(-Left, -Top);
            e.Graphics.SetClip(clip);
            goose.Draw(e.Graphics);
        }

        protected override void OnMouseClick(MouseEventArgs e)
        {
            base.OnMouseClick(e);
            if (e.Button == MouseButtons.Left && Tapped != null)
                Tapped(goose.Beak);
        }
    }

    internal sealed class ChatWindow : Form
    {
        private readonly TextBox editor;
        private readonly Label status;
        private readonly RichTextBox answer;
        private readonly Button send;
        private readonly Button cancel;
        private readonly Timer transition = new Timer { Interval = 15 };
        private readonly Stopwatch animation = new Stopwatch();
        private Point anchor;
        private Point finalPosition;
        private bool tailAtLeft;
        private bool closing;
        private bool attached;
        internal event Action<string> Submitted;
        internal event Action CancellationRequested;
        internal event Action ClosingBubble;
        internal event Action BubbleHidden;

        internal ChatWindow()
        {
            SetStyle(ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true);
            AutoScaleMode = AutoScaleMode.None;
            Font = new Font("Microsoft YaHei UI", 10);
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;
            ShowInTaskbar = false;
            TopMost = true;
            Text = "ClassFix · 问鹅";
            KeyPreview = true;
            BackColor = Color.FromArgb(252, 249, 241);
            ClientSize = new Size(416, 215);
            var title = new Label { Text = "问鹅", AutoSize = true, Font = new Font(Font, FontStyle.Bold), Location = new Point(24, 14) };
            var close = new Button { Text = "关闭", Size = new Size(65, 34), Location = new Point(326, 9), TabStop = false };
            close.Click += delegate { Dismiss(true); };
            editor = new TextBox
            {
                Multiline = true, AcceptsReturn = true, ScrollBars = ScrollBars.Vertical,
                MaxLength = DeepSeek.QuestionLimit, Location = new Point(24, 52),
                Width = 366, Height = 74, AccessibleName = "问题"
            };
            status = new Label { Location = new Point(24, 136), Width = 260, Height = 49, AutoEllipsis = true };
            send = new Button { Text = "发送", Location = new Point(312, 139), Size = new Size(78, 44) };
            send.Click += delegate { Submit(); };
            cancel = new Button { Text = "取消生成", Location = new Point(298, 139), Size = new Size(92, 44), Visible = false };
            cancel.Click += delegate { if (CancellationRequested != null) CancellationRequested(); };
            answer = new RichTextBox
            {
                ReadOnly = true, DetectUrls = false, WordWrap = true, ScrollBars = RichTextBoxScrollBars.Vertical,
                AccessibleName = "最近回答", Location = new Point(24, 190), Size = new Size(366, 124), Visible = false
            };
            Controls.AddRange(new Control[] { title, close, editor, status, send, cancel, answer });
            transition.Tick += delegate { Animate(); };
        }

        internal bool IsAttached { get { return attached; } }

        internal void Present(Point source, bool nearGoose, Rectangle keyboard)
        {
            bool alreadyOpen = Visible && !closing;
            anchor = source;
            attached = nearGoose;
            closing = false;
            Place(keyboard);
            if (!alreadyOpen)
            {
                Opacity = 0.12;
                Location = Offset(finalPosition, 6);
                animation.Restart();
                Show();
                transition.Start();
            }
            Activate();
            editor.Focus();
        }

        internal void SetState(bool waiting, Reply reply)
        {
            send.Visible = !waiting;
            cancel.Visible = waiting;
            status.Text = waiting ? "正在生成…" : reply != null && !reply.CanDeliver ? reply.Text : "";
            string text = reply != null && reply.CanDeliver ? reply.Text : "";
            if (answer.Text != text) answer.Text = text;
            answer.Visible = text.Length != 0;
            int height = text.Length != 0 ? 330 : 197;
            if (ClientSize.Height != height)
            {
                ClientSize = new Size(ClientSize.Width, height);
                UpdateShape();
            }
        }

        internal void Place(Rectangle keyboard)
        {
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
            if (!closing && ClosingBubble != null) ClosingBubble();
            if (!animate || immediate)
            {
                FinishClosing();
                return;
            }
            closing = true;
            animation.Restart();
            transition.Start();
        }

        private void Animate()
        {
            double t = Math.Min(1, animation.Elapsed.TotalMilliseconds / (closing ? 115 : 155));
            double progress = 1 - Math.Pow(1 - t, 3);
            Opacity = closing ? Math.Max(.04, 1 - progress) : .12 + progress * .88;
            Location = Offset(finalPosition, (int)Math.Round(6 * (closing ? progress : 1 - progress)));
            if (t >= 1)
            {
                transition.Stop();
                if (closing) FinishClosing();
            }
        }

        private Point Offset(Point position, int pixels)
        {
            return new Point(position.X + (tailAtLeft ? -pixels : pixels), position.Y);
        }

        private void FinishClosing()
        {
            transition.Stop();
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
            Font = new Font("Microsoft YaHei UI", 10);
            Text = "ClassFix · 鹅送来的回答";
            StartPosition = FormStartPosition.Manual;
            FormBorderStyle = FormBorderStyle.None;
            BackColor = Color.FromArgb(254, 250, 236);
            TopMost = true;
            ShowInTaskbar = false;
            ClientSize = new Size(Math.Min(420, available.Width - 40), Math.Min(260, available.Height - 70));
            var title = new Label
            {
                Text = "鹅送来的回答", Font = new Font(Font, FontStyle.Bold), AutoSize = true, Location = new Point(16, 14)
            };
            var body = new RichTextBox
            {
                Text = answer, ReadOnly = true, DetectUrls = false, ScrollBars = RichTextBoxScrollBars.Vertical,
                AccessibleName = "回答正文", BorderStyle = BorderStyle.None, BackColor = BackColor,
                Bounds = new Rectangle(16, 46, Width - 32, Height - 106)
            };
            copy = new Button { Text = "复制", Bounds = new Rectangle(16, Height - 48, 68, 36), Enabled = false };
            copy.Click += delegate { try { Clipboard.SetText(answer); copy.Text = "已复制"; } catch (System.Runtime.InteropServices.ExternalException) { } };
            close = new Button { Text = "关闭", Bounds = new Rectangle(Width - 83, Height - 48, 68, 36), Enabled = false };
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
