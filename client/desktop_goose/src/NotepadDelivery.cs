using System;
using System.Drawing;
using System.Reflection;
using System.Windows.Forms;
using GooseShared;
using SamEngine;

namespace ClassroomGoose
{
    internal sealed class NotepadDelivery
    {
        private readonly Form window;
        private readonly FieldInfo stage;
        private readonly Rectangle work;
        private volatile bool closed;
        private volatile bool closing;
        private bool released;

        internal GooseTaskData Task { get; private set; }

        internal NotepadDelivery(GooseTaskData task, string answer, Rectangle work, Point origin)
        {
            Task = task;
            this.work = work;
            // CollectNotepad creates and moves this form in the hash-verified v0.31 host.
            Type type = task.GetType();
            window = (Form)type.GetField("mainForm").GetValue(task);
            stage = type.GetField("stage");
            var direction = (ScreenDirection)type.GetField("screenDirection").GetValue(task);

            window.Text = "Goose \"Not-epad\" · Answer";
            window.ShowInTaskbar = false;
            // The original task centers its destination using margins based on the window width.
            window.Size = new Size(Math.Min(420, (work.Width - 110) / 2), Math.Min(300, work.Height - 100));
            var text = (TextBox)window.Controls[0];
            text.Text = answer.Replace("\r\n", "\n").Replace("\r", "\n").Replace("\n", "\r\n");
            text.ReadOnly = true;
            text.ScrollBars = ScrollBars.Vertical;
            text.Dock = DockStyle.Fill;
            text.Select(0, 0);

            bool fromRight = direction == ScreenDirection.Right;
            type.GetField("windowOffsetToBeak").SetValue(task, new Vector2(
                (fromRight ? 0 : window.Width) - origin.X, window.Height / 2 - origin.Y));
            window.StartPosition = FormStartPosition.Manual;
            window.Location = new Point(fromRight ? work.Right + 50 : work.Left - window.Width - 50, work.Top);
            ClipToDisplay();
            window.Load += delegate
            {
                Native.SetWindowActivation(window.Handle, false);
                if (closing) window.BeginInvoke(new MethodInvoker(window.Close));
            };
            window.LocationChanged += delegate { if (!released) ClipToDisplay(); };
            window.FormClosed += delegate { closed = true; };
        }

        private void ClipToDisplay()
        {
            Rectangle clip = Rectangle.Intersect(window.Bounds, work);
            clip.Offset(-window.Left, -window.Top);
            window.Region = new Region(clip);
        }

        private bool Dispatch(MethodInvoker action)
        {
            // The stock task runs the note's dialog on its own WinForms thread.
            if (closed || !window.IsHandleCreated) return false;
            try
            {
                window.BeginInvoke(action);
                return true;
            }
            catch (InvalidOperationException) { return false; }
        }

        internal bool Release()
        {
            return Dispatch(delegate
            {
                released = true;
                window.Region = null;
                Native.SetWindowActivation(window.Handle, true);
            });
        }

        internal void Close()
        {
            closing = true;
            if (Dispatch(new MethodInvoker(window.Close))) return;
            // A dialog thread may have just started, with no handle to post to yet.
            if (closed || stage.GetValue(Task).ToString() != "DraggingWindowBack")
                window.Dispose();
        }
    }
}
