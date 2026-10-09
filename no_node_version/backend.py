"""Node.js/Appium/Selenium 없이 Python uiautomator2로 UI를 조작한다."""
import re


class Element:
    def __init__(self, driver, spec, node):
        self.driver, self.spec, self.node = driver, spec, node

    def bounds(self):
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', self.node.get('bounds', ''))
        return tuple(map(int, match.groups())) if match else (0, 0, 0, 0)

    def is_displayed(self):
        x1, y1, x2, y2 = self.bounds()
        return self.node.get('visible-to-user', 'true') != 'false' and x2 > x1 and y2 > y1

    def is_enabled(self):
        return self.node.get('enabled', 'false') == 'true'

    def click(self):
        # 화면 전환 뒤 오래된 좌표를 클릭하지 않도록 직전에 다시 찾는다.
        found = [e for e in self.driver.find_elements(*self.spec)
                 if e.is_displayed() and e.is_enabled()]
        if len(found) != 1:
            raise RuntimeError('클릭 직전 요소가 사라졌거나 중복됨')
        x1, y1, x2, y2 = found[0].bounds()
        self.driver.device.click((x1 + x2) // 2, (y1 + y2) // 2)


class Driver:
    def __init__(self, device):
        self.device = device

    @property
    def page_source(self):
        return self.device.dump_hierarchy(compressed=False)

    def find_elements(self, by, value):
        from lxml import etree
        root = etree.fromstring(self.page_source.encode('utf-8'),
                                parser=etree.XMLParser(resolve_entities=False, no_network=True))
        # XPath 변수로 따옴표가 포함된 접근성 이름도 안전하게 비교한다.
        if by == 'id':
            nodes = root.xpath('//*[@resource-id=$value]', value=value)
        elif by == 'accessibility id':
            nodes = root.xpath('//*[@content-desc=$value]', value=value)
        elif by == 'xpath':
            nodes = root.xpath(value)
        else:
            raise ValueError('지원하지 않는 locator: ' + by)
        if not isinstance(nodes, list) or any(not isinstance(n, etree._Element) for n in nodes):
            raise ValueError('XPath는 화면 요소 목록을 반환해야 함')
        return [Element(self, (by, value), node) for node in nodes]

    def get_window_size(self):
        width, height = self.device.window_size()
        return {'width': width, 'height': height}

    def swipe(self, x1, y1, x2, y2, milliseconds):
        self.device.swipe(x1, y1, x2, y2, duration=milliseconds / 1000)

    def save_screenshot(self, path):
        self.device.screenshot(path)

    def quit(self):
        # 갤러리나 ADB 전역 서버를 종료하지 않는다. 기기 보조 서비스는 남을 수 있다.
        pass


def connect(cfg):
    import uiautomator2 as u2
    return Driver(u2.connect_usb(cfg['udid']))
