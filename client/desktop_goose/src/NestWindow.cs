using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace ClassroomGoose
{
    internal sealed class NestWindow : Form
    {
        private readonly IGoose goose;
        private Rectangle area;
        private Point? draggedOffset;
        private Point dragStart;
        private Point windowStart;
        private Point? occupiedOrigin;
        private bool dragging;

        internal NestWindow(IGoose goose)
        {
            this.goose = goose;
            Text = "Goose Nest";
            AccessibleName = "Goose Nest";
            AutoScaleMode = AutoScaleMode.None;
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;
            ShowInTaskbar = false;
            TopMost = true;
            ClientSize = new Size(124, 94);
            BackColor = Color.Magenta;
            TransparencyKey = BackColor;
            Cursor = Cursors.SizeAll;
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer, true);
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

        internal void ShowOn(Rectangle bounds)
        {
            if (Visible && area == bounds) return;
            area = bounds;
            MoveTo(draggedOffset.HasValue
                ? new Point(area.Left + draggedOffset.Value.X, area.Top + draggedOffset.Value.Y)
                : new Point(area.Right - Width - 24, area.Bottom - Height - 32));
            if (!Visible)
            {
                occupiedOrigin = null;
                Show();
                Native.RaiseWithoutFocus(Handle);
            }
        }

        internal Point RestPoint { get { return new Point(Left + 57, Top + 70); } }
        internal bool Occupied { get { return occupiedOrigin.HasValue; } }

        internal void Settle()
        {
            occupiedOrigin = Location;
            Invalidate();
        }

        private void MoveTo(Point target)
        {
            Location = new Point(Math.Max(area.Left, Math.Min(target.X, area.Right - Width)),
                                 Math.Max(area.Top, Math.Min(target.Y, area.Bottom - Height)));
        }

        protected override void WndProc(ref Message message)
        {
            if (message.Msg == 0x0021) // WM_MOUSEACTIVATE
            {
                message.Result = new IntPtr(3); // MA_NOACTIVATE
                return;
            }
            base.WndProc(ref message);
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            base.OnMouseDown(e);
            if (e.Button != MouseButtons.Left) return;
            dragStart = Cursor.Position;
            windowStart = Location;
            dragging = true;
            Capture = true;
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            if (!dragging) return;
            Point pointer = Cursor.Position;
            MoveTo(new Point(windowStart.X + pointer.X - dragStart.X, windowStart.Y + pointer.Y - dragStart.Y));
            draggedOffset = new Point(Left - area.Left, Top - area.Top);
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            base.OnMouseUp(e);
            if (e.Button != MouseButtons.Left) return;
            dragging = false;
            Capture = false;
        }

        protected override void OnMouseCaptureChanged(EventArgs e)
        {
            dragging = false;
            base.OnMouseCaptureChanged(e);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var graphics = e.Graphics;
            // A color-key window must preserve the exact transparent color at its outer edge.
            graphics.SmoothingMode = SmoothingMode.None;
            using (var edge = new Pen(Color.FromArgb(155, 105, 61), 2))
            using (var rim = new SolidBrush(Color.FromArgb(224, 177, 112)))
            using (var inside = new SolidBrush(Color.FromArgb(177, 124, 73)))
            using (var strand = new Pen(Color.FromArgb(240, 198, 127), 2.5f) { StartCap = LineCap.Round, EndCap = LineCap.Round })
            {
                graphics.FillEllipse(rim, 6, 51, 112, 38);
                graphics.DrawEllipse(edge, 6, 51, 112, 38);
                graphics.FillEllipse(inside, 13, 54, 98, 25);
                if (occupiedOrigin.HasValue)
                {
                    var state = graphics.Save();
                    graphics.TranslateTransform(-occupiedOrigin.Value.X, -occupiedOrigin.Value.Y);
                    goose.Draw(graphics);
                    graphics.Restore(state);
                }

                using (var front = new GraphicsPath())
                {
                    front.AddBezier(6, 69, 27, 82, 98, 83, 118, 69);
                    front.AddBezier(118, 69, 112, 95, 13, 96, 6, 69);
                    front.CloseFigure();
                    graphics.FillPath(rim, front);
                    graphics.DrawPath(edge, front);
                }
                graphics.DrawArc(strand, 14, 62, 91, 23, 24, 142);
                graphics.DrawLine(strand, 19, 80, 43, 87);
                graphics.DrawLine(strand, 84, 86, 105, 79);
            }
        }
    }
}
