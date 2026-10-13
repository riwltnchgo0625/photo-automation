"""기기 연결, 버튼 검색, 원본별 스타일 저장. GUI/빌드 코드는 이 파일에 넣지 않는다."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import threading
import time

MEDIA = 'content://media/external/images/media'
GALLERY = 'com.sec.android.gallery3d'


class Stopped(Exception):
    """사용자가 중지를 요청했다. 생성 요청 자체를 취소했다는 뜻은 아니다."""


def app_data() -> Path:
    root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'PhotoAssistCollector'
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def parse_media(output):
    rows = {}
    for line in output.splitlines():
        if not line.strip() or line.startswith('No result found'):
            continue
        m = re.fullmatch(r'Row: \d+ _id=(\d+), _size=(\d+), date_modified=(\d+), _data=(.+)', line)
        if not m:
            raise RuntimeError('사진 목록 조회 실패: ' + line[:300])
        ident, size, modified, path = m.groups()
        rows[ident] = dict(id=ident, size=int(size), modified=int(modified), path=path)
    return rows


def saved_result(before, after, original):
    if after.get(original['id']) != original:
        raise RuntimeError('원본 정보가 변경되었거나 원본을 찾지 못했어. 실행을 멈췄어.')
    added = [row for ident, row in after.items() if ident not in before]
    if len(added) > 1:
        raise RuntimeError('새 사진이 동시에 여러 장 생겼어. 갤러리에서 결과를 확인해줘.')
    if not added or added[0]['size'] == 0:
        return None
    result = added[0]
    if PurePosixPath(result['path']).parent != PurePosixPath(original['path']).parent:
        raise RuntimeError('다른 폴더에 저장됐어. 파일을 옮기지 않고 멈췄어: ' + result['path'])
    return result


def check_stop(stop):
    if stop.is_set():
        raise Stopped('중지됨. 이미 시작된 생성은 폰에서 계속될 수 있어. 마지막 결과를 확인해줘.')


def adb_client():
    # adbutils의 Windows 배포판에는 ADB와 DLL이 포함된다.
    # 전역 PATH를 바꾸거나 별도 서버를 외부 네트워크에 공개하지 않는다.
    import adbutils
    return adbutils.AdbClient(host='127.0.0.1', port=5037)


def devices():
    return [dict(serial=d.serial, state=d.state) for d in adb_client().list()]


def node_bounds(node):
    m = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', node.get('bounds', ''))
    return tuple(map(int, m.groups())) if m else (0, 0, 0, 0)


def visible(node):
    x1, y1, x2, y2 = node_bounds(node)
    return node.get('visible-to-user', 'true') != 'false' and x2 > x1 and y2 > y1


def xml_root(xml):
    from lxml import etree
    return etree.fromstring(xml.encode(), parser=etree.XMLParser(resolve_entities=False, no_network=True))


def select_nodes(xml, selector):
    field = selector.get('field')
    if field not in ('resource-id', 'content-desc', 'text') or not selector.get('value'):
        raise ValueError('버튼 이름을 먼저 지정해줘.')
    # 필드 이름은 위의 허용 목록으로 제한, 값은 XPath 변수로 전달한다.
    return [n for n in xml_root(xml).xpath(f'//*[@{field}=$v]', v=selector['value'])
            if visible(n) and n.get('enabled', 'false') == 'true']


def screen_candidates(xml):
    """화면에 노출된 고유한 text/content-desc/resource-id만 설정 후보로 제공한다."""
    root = xml_root(xml)
    nodes = [n for n in root.iter() if visible(n) and n.get('enabled', 'false') == 'true']
    candidates = []
    seen = set()
    for node in nodes:
        for field in ('content-desc', 'text', 'resource-id'):
            value = node.get(field)
            if value and sum(n.get(field) == value for n in nodes) == 1:
                selector = {'field': field, 'value': value}
                token = (field, value)
                if token not in seen:
                    candidates.append({'selector': selector, 'label': value,
                                       'bounds': node_bounds(node)})
                    seen.add(token)
                break
    return candidates


class Phone:
    def __init__(self, serial):
        self.serial = serial
        self.adb = adb_client().device(serial=serial)
        self._ui = None

    @property
    def ui(self):
        if self._ui is None:
            import uiautomator2 as u2
            self._ui = u2.connect_usb(self.serial)
            self._ui.settings['wait_timeout'] = 1
        return self._ui

    def shell(self, *args):
        result = self.adb.shell2(shlex.join(str(a) for a in args), timeout=30)
        if result.returncode or 'Exception' in result.output or 'Error:' in result.output:
            raise RuntimeError(result.output[:700])
        return result.output

    def photos(self):
        return parse_media(self.shell('content', 'query', '--uri', MEDIA,
                                      '--projection', '_id:_size:date_modified:_data'))

    def open_original(self, photo):
        # 강제 종료, 삭제, 원본 덮어쓰기 명령을 사용하지 않는다.
        self.shell('am', 'start', '-W', '-a', 'android.intent.action.VIEW',
                   '-d', MEDIA + '/' + photo['id'], '-t', 'image/*', '-p', GALLERY,
                   '--grant-read-uri-permission')

    def screen(self):
        return self.ui.dump_hierarchy(compressed=False)

    def click(self, selector, stop, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            check_stop(stop)
            nodes = select_nodes(self.screen(), selector)
            if len(nodes) > 1:
                raise RuntimeError('같은 이름의 버튼이 여러 개야: ' + selector['value'])
            if nodes:
                check_stop(stop)
                x1, y1, x2, y2 = node_bounds(nodes[0])
                self.ui.click((x1 + x2) // 2, (y1 + y2) // 2)
                return
            if time.monotonic() >= deadline:
                raise TimeoutError('버튼을 찾지 못했어: ' + selector['value'])
            if stop.wait(0.5):
                check_stop(stop)

    def swipe_style(self, y, stop):
        check_stop(stop)
        width, height = self.ui.window_size()
        self.ui.swipe(int(width * .85), int(height * y), int(width * .2), int(height * y), duration=.5)
        if stop.wait(.5):
            check_stop(stop)


def validate(profile, originals):
    if not originals or not profile.get('styles'):
        raise ValueError('원본 사진과 스타일을 각각 하나 이상 선택해줘.')
    for key in ('assist', 'style_tab', 'save'):
        if not profile.get(key):
            raise ValueError('버튼 설정이 필요해: ' + key)
    names = [s['name'] for s in profile['styles']]
    if len(names) != len(set(names)):
        raise ValueError('스타일 이름이 중복돼 있어.')
    for s in profile['styles']:
        if not isinstance(s.get('swipes', 0), int) or not 0 <= s.get('swipes', 0) <= 20:
            raise ValueError('스타일 이동 횟수는 0~20이어야 해.')
        if not 0 < s.get('swipe_y', .82) < 1:
            raise ValueError('스타일 목록 높이는 0~1 사이여야 해.')


def execute(phone, profile, originals, stop, emit, work):
    """한 번에 한 작업만 실행한다. 불명확한 저장을 자동 재시도하지 않는다."""
    validate(profile, originals)
    state_path = work / 'progress.json'
    signature = hashlib.sha256(json.dumps({'serial': phone.serial, 'profile': profile,
                                         'originals': originals}, sort_keys=True).encode()).hexdigest()
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {
        'signature': signature, 'done': {}, 'pending': None}
    if state['signature'] != signature:
        raise RuntimeError('이전 작업과 선택이 달라. 결과를 확인하고 [새 작업]을 눌러줘.')
    if state['pending']:
        raise RuntimeError('이전 작업의 완료 여부가 불명확해. 폰에서 마지막 결과를 확인하고 [새 작업]으로 다시 계획해줘.')
    current = phone.photos()
    for original in originals:
        if current.get(original['id']) != original:
            raise RuntimeError('선택한 원본 정보가 바뀌었어. 사진 목록을 다시 불러와줘.')
    for record in state['done'].values():
        if current.get(record['result']['id']) != record['result']:
            raise RuntimeError('이전에 저장된 결과가 변경됐어. 폰에서 확인해줘.')
    total = len(originals) * len(profile['styles'])
    for original in originals:
        for style in profile['styles']:
            key = original['id'] + ':' + style['name']
            if key in state['done']:
                continue
            check_stop(stop)
            state['pending'] = {'original': original, 'style': style['name']}
            save_json(state_path, state)
            emit(f"{len(state['done']) + 1}/{total}  {PurePosixPath(original['path']).name} · {style['name']}")
            phone.open_original(original)
            phone.click(profile['assist'], stop)
            phone.click(profile['style_tab'], stop)
            if profile.get('more'):
                phone.click(profile['more'], stop)
            for _ in range(style.get('swipes', 0)):
                phone.swipe_style(style.get('swipe_y', .82), stop)
            if select_nodes(phone.screen(), profile['save']):
                raise RuntimeError('편집 전부터 저장 버튼이 보여. 원본 화면 또는 버튼 설정을 확인해줘.')
            before = phone.photos()
            if before.get(original['id']) != original:
                raise RuntimeError('원본 정보가 변경되어 중단했어.')
            started = time.monotonic()
            phone.click(style['selector'], stop)
            phone.click(profile['save'], stop, timeout=180)
            deadline = time.monotonic() + 45
            previous, count = None, 0
            while time.monotonic() < deadline:
                check_stop(stop)
                result = saved_result(before, phone.photos(), original)
                count = count + 1 if result is not None and result == previous else 0
                if count >= 2:
                    state['done'][key] = {'original': original, 'style': style['name'], 'result': result,
                                          'seconds': round(time.monotonic() - started, 2)}
                    state['pending'] = None
                    save_json(state_path, state)
                    emit('같은 폴더 저장 확인: ' + PurePosixPath(result['path']).name)
                    break
                previous = result
                if stop.wait(1):
                    check_stop(stop)
            else:
                raise TimeoutError('저장된 파일을 확인하지 못했어. 폰의 결과를 확인해줘.')
    emit('완료. 저장된 사진의 품질은 갤러리에서 평가해줘.')
