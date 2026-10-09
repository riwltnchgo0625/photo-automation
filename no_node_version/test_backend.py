import unittest
from unittest.mock import Mock
from backend import Driver
from collector import wait_element

XML = '''<hierarchy><node resource-id="pkg:id/save" content-desc="save's button"
text="다른 파일로 저장" enabled="true" bounds="[10,20][110,80]"/></hierarchy>'''


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.device = Mock()
        self.device.dump_hierarchy.return_value = XML
        self.driver = Driver(self.device)

    def test_id_click_uses_current_bounds(self):
        element = self.driver.find_elements('id', 'pkg:id/save')[0]
        self.device.dump_hierarchy.return_value = XML.replace('[10,20][110,80]', '[20,30][120,90]')
        element.click()
        self.device.click.assert_called_once_with(70, 60)

    def test_accessibility_quote(self):
        self.assertEqual(len(self.driver.find_elements('accessibility id', "save's button")), 1)

    def test_xpath_disabled_never_ready(self):
        self.device.dump_hierarchy.return_value = XML.replace('enabled="true"', 'enabled="false"')
        with self.assertRaises(TimeoutError):
            wait_element(self.driver, {'by': 'xpath', 'value': "//*[@text='다른 파일로 저장']"}, 0)

    def test_disappeared_element_not_clicked(self):
        element = self.driver.find_elements('id', 'pkg:id/save')[0]
        self.device.dump_hierarchy.return_value = '<hierarchy/>'
        with self.assertRaises(RuntimeError):
            element.click()
        self.device.click.assert_not_called()

    def test_duplicate_element_not_clicked(self):
        element = self.driver.find_elements('id', 'pkg:id/save')[0]
        node = XML[XML.index('<node'):XML.index('</hierarchy>')]
        self.device.dump_hierarchy.return_value = '<hierarchy>' + node * 2 + '</hierarchy>'
        with self.assertRaises(RuntimeError):
            element.click()
        self.device.click.assert_not_called()

    def test_swipe_duration_conversion(self):
        self.driver.swipe(10, 20, 30, 40, 500)
        self.device.swipe.assert_called_once_with(10, 20, 30, 40, duration=0.5)
