using System;

namespace ClassroomGoose
{
    internal static class Schedule
    {
        private static readonly int[,] Slots =
        {
            { 8 * 60 + 40, 8 * 60 + 50 },
            { 9 * 60 + 30, 10 * 60 },
            { 10 * 60 + 45, 10 * 60 + 55 },
            { 11 * 60 + 35, 11 * 60 + 50 },
            { 13 * 60, 13 * 60 + 50 },
            { 14 * 60 + 30, 14 * 60 + 45 },
            { 15 * 60 + 25, 15 * 60 + 35 },
            { 16 * 60 + 15, 16 * 60 + 25 },
        };

        internal static bool IsOpen(DateTime now, bool includeWeekends)
        {
            if (!includeWeekends && (now.DayOfWeek == DayOfWeek.Saturday || now.DayOfWeek == DayOfWeek.Sunday))
                return false;

            int minute = now.Hour * 60 + now.Minute;
            for (int index = 0; index < Slots.GetLength(0); index++)
                if (minute >= Slots[index, 0] && minute < Slots[index, 1])
                    return true;
            return false;
        }
    }
}
