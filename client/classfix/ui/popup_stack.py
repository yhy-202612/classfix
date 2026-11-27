"""Per-screen placement and collision-free animations for classroom prompts."""

from collections import deque

from PySide6.QtCore import QObject, QParallelAnimationGroup, QPoint
from PySide6.QtGui import QRegion

from ..monitor import screen_for_window


class PopupStack(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.lanes = {}
        self.placements = {}
        app.screenRemoved.connect(self._screen_removed)

    def show(self, popup, location):
        if popup in self.placements:
            return True
        screen = screen_for_window(self.app, location)
        if screen not in self.lanes:
            self.lanes[screen] = _ScreenStack(screen, self)
        lane = self.lanes[screen]
        self.placements[popup] = lane
        lane.waiting.append(popup)
        lane.advance()
        return True

    def remove(self, popup):
        lane = self.placements.pop(popup, None)
        if lane is not None:
            lane.remove(popup)

    def slide_out(self, popup):
        lane = self.placements.get(popup)
        if lane is not None and popup in lane.visible:
            lane.closing.add(popup)
            lane.advance()

    def _screen_removed(self, screen):
        lane = self.lanes.pop(screen, None)
        if lane is None:
            return
        lane._detached = True
        lane.animation.stop()
        for popup in (*lane.visible, *lane.waiting):
            if popup in lane.closing:
                popup.close()
                continue
            self.placements.pop(popup, None)
            popup._moving_screens = True
            popup.hide()
            popup._moving_screens = False
            self.show(popup, 0)
        lane.deleteLater()


class _ScreenStack(QObject):
    MARGIN = 18
    GAP = 10

    def __init__(self, screen, stack):
        super().__init__(stack)
        self.screen = screen
        self.visible = []
        self.waiting = deque()
        self.closing = set()
        self.leaving = None
        self._detached = False
        self.animation = QParallelAnimationGroup(self)
        self.animation.finished.connect(self.advance)
        screen.availableGeometryChanged.connect(self._resize)

    def remove(self, popup):
        # Leave other cards at their current animated positions and start the
        # next layout from there. A still-waiting card has no window to hide.
        self._interrupt()
        self.closing.discard(popup)
        if popup in self.visible:
            self.visible.remove(popup)
            popup.hide()
            popup.clearMask()
        else:
            self.waiting.remove(popup)
        self.advance()

    def _interrupt(self):
        self.animation.stop()
        self.animation.clear()
        self.leaving = None

    def _resize(self, _geometry):
        self._interrupt()
        geometry = self.screen.availableGeometry()
        height = sum(item.height() + self.GAP for item in self.visible)
        expired = []
        while len(self.visible) > 1 and height - self.GAP > geometry.height() - 2 * self.MARGIN:
            popup = self.visible.pop()
            height -= popup.height() + self.GAP
            self.waiting.appendleft(popup)
            popup._moving_screens = True
            popup.hide()
            popup._moving_screens = False
            if popup in self.closing:
                expired.append(popup)
        for popup in expired:
            popup.close()
        self.advance()

    def advance(self):
        if self._detached or self.animation.state() == self.animation.State.Running:
            return
        self.animation.clear()
        geometry = self.screen.availableGeometry()
        if self.leaving is not None:
            popup, self.leaving = self.leaving, None
            popup.close()
            return
        leaving = next((popup for popup in self.visible if popup in self.closing), None)
        if leaving is not None:
            self.leaving = leaving
            self._add_motion(leaving, QPoint(geometry.right(), leaving.y()), geometry.right() + 1)
            self.animation.start()
            return
        incoming = self.waiting[0] if self.waiting else None
        height = sum(item.height() + self.GAP for item in self.visible)
        if incoming and self.visible and height + incoming.height() > geometry.height() - 2 * self.MARGIN:
            incoming = None
        bottom = geometry.bottom() + 1 - self.MARGIN
        if incoming:
            bottom -= incoming.height() + self.GAP
        for popup in reversed(self.visible):
            bottom -= popup.height()
            end = QPoint(max(geometry.left(), geometry.right() + 1 - popup.width() - self.MARGIN), bottom)
            if popup.pos() != end:
                self._add_motion(popup, end, geometry.right() + 1)
            bottom -= self.GAP
        if self.animation.animationCount():
            self.animation.start()
        elif incoming:
            self._enter(self.waiting.popleft(), geometry)

    def _enter(self, popup, geometry):
        # Open a full slot first. The new panel then moves horizontally at
        # that y-coordinate, so even a slow frame cannot cross another panel.
        edge = geometry.right() + 1
        y = max(geometry.top(), geometry.bottom() + 1 - self.MARGIN - popup.height())
        end = QPoint(max(geometry.left(), edge - self.MARGIN - popup.width()), y)
        origin = QPoint(edge - 1, y)
        popup.move(origin)
        popup.setMask(QRegion(0, 0, 1, popup.height()))
        self.visible.append(popup)
        popup.show_without_activation()
        self._add_motion(popup, end, edge)
        self.animation.start()

    def _add_motion(self, popup, end, edge):
        animation = popup.animation_to(end)

        def clip(position):
            width = max(1, min(popup.width(), edge - position.x()))
            if width == popup.width():
                popup.clearMask()
            else:
                popup.setMask(QRegion(0, 0, width, popup.height()))

        if max(popup.x(), end.x()) + popup.width() > edge:
            clip(popup.pos())
            animation.valueChanged.connect(clip)
        else:
            popup.clearMask()
        self.animation.addAnimation(animation)
