import unittest
from unittest.mock import Mock
from backend import OwnedDriver


class BackendTests(unittest.TestCase):
    def test_owned_server_stopped(self):
        driver, service, log = Mock(), Mock(), Mock()
        OwnedDriver(driver, service, log).quit()
        driver.quit.assert_called_once()
        service.stop.assert_called_once()
        log.close.assert_called_once()

    def test_reused_server_not_owned(self):
        driver = Mock()
        OwnedDriver(driver, None, None).quit()
        driver.quit.assert_called_once()

    def test_driver_failure_still_cleans_server(self):
        driver, service, log = Mock(), Mock(), Mock()
        driver.quit.side_effect = RuntimeError('connection lost')
        with self.assertRaises(RuntimeError):
            OwnedDriver(driver, service, log).quit()
        service.stop.assert_called_once()
        log.close.assert_called_once()
