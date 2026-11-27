import sys
import unittest
from types import ModuleType
from unittest.mock import Mock, PropertyMock, patch

from classfix.opencheck import _com_open_direct


class FakeComError(Exception):
    pass


class OpenCheckLifetimeTest(unittest.TestCase):
    def setUp(self):
        self.user_deck = Mock()
        self.copy_deck = Mock()
        self.copy_deck.Slides.Count = 1
        self.alerts = PropertyMock(return_value=2)
        self.office = Mock()
        type(self.office).DisplayAlerts = self.alerts
        self.office.Path = "C:/Program Files/Microsoft Office/root/Office16"
        self.office.Presentations.return_value = self.user_deck
        self.office.Presentations.Open.return_value = self.copy_deck

        self.pythoncom = ModuleType("pythoncom")
        self.pythoncom.com_error = FakeComError
        self.pythoncom.CoInitialize = Mock()
        self.pythoncom.CoUninitialize = Mock()
        win32com = ModuleType("win32com")
        win32com.client = ModuleType("win32com.client")
        win32com.client.DispatchEx = Mock(return_value=self.office)
        self.modules = {"pythoncom": self.pythoncom, "win32com": win32com,
                        "win32com.client": win32com.client}
        self.watcher = Mock()
        self.watcher.text.return_value = ""

    def run_probe(self):
        with patch.dict(sys.modules, self.modules):
            return _com_open_direct("probe-course.pptx", 1, self.watcher)

    def assert_office_unchanged(self):
        self.office.Quit.assert_not_called()
        self.alerts.assert_not_called()
        self.user_deck.Close.assert_not_called()
        self.pythoncom.CoUninitialize.assert_called_once_with()

    def test_probe_closes_only_its_copy_in_the_existing_instance(self):
        result = self.run_probe()
        self.assertEqual(result.state, "ok")
        self.office.Presentations.Open.assert_called_once_with("probe-course.pptx", True, False, False)
        self.copy_deck.Close.assert_called_once_with()
        self.assert_office_unchanged()

    def test_failed_open_keeps_existing_instance_running(self):
        self.office.Presentations.Open.side_effect = FakeComError(0x80004005, "Cannot open file")
        result = self.run_probe()
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.hresult, 0x80004005)
        self.copy_deck.Close.assert_not_called()
        self.assert_office_unchanged()

    def test_failure_after_open_still_closes_probe_copy(self):
        self.copy_deck.Slides.Count = "invalid"
        with self.assertRaises(ValueError):
            self.run_probe()
        self.copy_deck.Close.assert_called_once_with()
        self.assert_office_unchanged()
