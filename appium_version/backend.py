"""로컬 Appium 서버를 필요할 때 시작하고, 직접 시작한 서버만 종료한다."""
import json
import socket
from urllib.parse import urlparse
from urllib.request import urlopen


class OwnedDriver:
    def __init__(self, driver, service, logfile):
        self.driver, self.service, self.logfile = driver, service, logfile

    def __getattr__(self, name):
        return getattr(self.driver, name)

    def quit(self):
        try:
            self.driver.quit()
        finally:
            try:
                if self.service:
                    self.service.stop()
            finally:
                if self.logfile:
                    self.logfile.close()


def connect(cfg):
    from appium import webdriver
    from appium.options.android import UiAutomator2Options
    from appium.webdriver.appium_service import AppiumService
    url = cfg['server_url'].rstrip('/')
    parsed = urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path:
        raise ValueError('server_url은 http://127.0.0.1:포트 형식이어야 함')
    port = parsed.port or 4723
    service, logfile = None, None
    try:
        with socket.socket() as sock:
            sock.settimeout(1)
            listening = sock.connect_ex(('127.0.0.1', port)) == 0
        if listening:
            # 다른 프로그램이 점유한 포트라면 종료하거나 교체하지 않는다.
            with urlopen(url + '/status', timeout=3) as response:
                status = json.load(response)
            if status.get('value', {}).get('ready') is not True:
                raise RuntimeError('해당 포트의 Appium 서버가 준비되지 않음')
        else:
            logfile = open('appium_server.log', 'a', encoding='utf-8')
            service = AppiumService()
            service.start(args=['--address', '127.0.0.1', '--port', str(port)],
                          timeout_ms=60000, stdout=logfile, stderr=logfile)
            if not service.is_listening:
                raise RuntimeError('Appium 시작 실패. appium_server.log를 확인해줘')
        options = UiAutomator2Options().load_capabilities({
            'platformName': 'Android', 'appium:automationName': 'UiAutomator2',
            'appium:udid': cfg['udid'], 'appium:noReset': True,
            'appium:autoLaunch': False, 'appium:newCommandTimeout': 600,
        })
        driver = webdriver.Remote(url, options=options)
        return OwnedDriver(driver, service, logfile)
    except BaseException:
        try:
            if service:
                service.stop()
        finally:
            if logfile:
                logfile.close()
        raise
