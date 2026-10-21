"""기기 연결, 버튼 검색, 원본별 스타일 저장. GUI·빌드 코드 제외."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import threading
import time

# Android 사진 목록 주소와 삼성 갤러리 패키지임. 앨범 경로로 바꾸지 않음.
MEDIA = 'content://media/external/images/media'
GALLERY = 'com.sec.android.gallery3d'


class Stopped(Exception):
    """사용자 중지 요청. 이미 시작된 생성 취소와는 별개."""


# PC의 설정·진행 기록 저장 위치임. 폰 사진 저장 위치와는 별개임.
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


# 원본 정보가 유지되고 같은 폴더에 새 파일 한 장이 생겼는지 확인. 품질 평가는 하지 않음.
def saved_result(before, after, original):
    if after.get(original['id']) != original:
        raise RuntimeError('원본 정보 변경 또는 원본 조회 실패. 실행 중단.')
    added = [row for ident, row in after.items() if ident not in before]
    if len(added) > 1:
        raise RuntimeError('새 사진 여러 장 감지. 갤러리 결과 확인 필요.')
    if not added or added[0]['size'] == 0:
        return None
    result = added[0]
    if PurePosixPath(result['path']).parent != PurePosixPath(original['path']).parent:
        raise RuntimeError('저장 폴더 불일치. 파일 이동 없이 중단: ' + result['path'])
    return result


def check_stop(stop):
    if stop.is_set():
        raise Stopped('중지 완료. 이미 시작된 생성의 지속 가능성 있음. 폰의 마지막 결과 확인 필요.')


def adb_client():
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


# 등록한 content-desc/text/resource-id 값과 정확히 일치하는 요소를 탐색.
def select_nodes(xml, selector):
    field = selector.get('field')
    if field not in ('resource-id', 'content-desc', 'text') or not selector.get('value'):
        raise ValueError('버튼 이름 지정 필요.')
    return [n for n in xml_root(xml).xpath(f'//*[@{field}=$v]', v=selector['value'])
            if visible(n) and n.get('enabled', 'false') == 'true']


# 중복되지 않는 설명 → 글자 → ID 순서로 등록 후보를 생성.
def screen_candidates(xml):
    """화면에 노출된 고유 text/content-desc/resource-id를 설정 후보로 제공."""
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

    # 사진 ID·크기·수정시간·경로만 조회함. 사진 파일은 PC로 가져오지 않음.
    def photos(self):
        return parse_media(self.shell('content', 'query', '--uri', MEDIA,
                                      '--projection', '_id:_size:date_modified:_data'))

    # 사진 ID로 원본을 열기. clear-top은 위에 쌓인 편집 화면 정리를 요청하며 실기기 확인이 필요.
    def open_original(self, photo):
        self.shell('am', 'start', '-W', '-a', 'android.intent.action.VIEW',
                   '-d', MEDIA + '/' + photo['id'], '-t', 'image/*', '-p', GALLERY,
                   '--grant-read-uri-permission', '--activity-clear-top')

    def screen(self):
        return self.ui.dump_hierarchy(compressed=False)

    # 버튼 탐색 제한은 기본 20초임. 같은 이름이 여러 개면 중단.
    def click(self, selector, stop, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            check_stop(stop)
            nodes = select_nodes(self.screen(), selector)
            if len(nodes) > 1:
                raise RuntimeError('동일 이름 버튼 중복: ' + selector['value'])
            if nodes:
                check_stop(stop)
                x1, y1, x2, y2 = node_bounds(nodes[0])
                self.ui.click((x1 + x2) // 2, (y1 + y2) // 2)
                return
            if time.monotonic() >= deadline:
                raise TimeoutError('버튼 탐색 실패: ' + selector['value'])
            if stop.wait(0.5):
                check_stop(stop)

    # 화면 너비 85% → 20% 지점을 0.5초 동안 왼쪽으로 이동. y는 등록한 스타일 높이임.
    def swipe_style(self, y, stop):
        check_stop(stop)
        width, height = self.ui.window_size()
        self.ui.swipe(int(width * .85), int(height * y), int(width * .2), int(height * y), duration=.5)
        if stop.wait(.5):
            check_stop(stop)


# 필수 버튼·원본·스타일과 스와이프 범위를 실행 전에 확인.
def validate(profile, originals):
    if not originals or not profile.get('styles'):
        raise ValueError('원본 사진 및 스타일 각각 1개 이상 선택 필요.')
    for key in ('assist', 'style_tab', 'generate', 'save'):
        if not profile.get(key):
            raise ValueError('버튼 설정 필요: ' + ('생성 버튼 지정' if key == 'generate' else key))
    names = [s['name'] for s in profile['styles']]
    if len(names) != len(set(names)):
        raise ValueError('스타일 이름 중복.')
    for s in profile['styles']:
        if not isinstance(s.get('swipes', 0), int) or not 0 <= s.get('swipes', 0) <= 20:
            raise ValueError('스타일 이동 횟수 범위: 0~20.')
        if not 0 < s.get('swipe_y', .82) < 1:
            raise ValueError('스타일 목록 높이 범위: 0 초과, 1 미만.')


# 원본별로 모든 스타일을 처리. 저장이 불명확하면 자동 재시도하지 않음.
def execute(phone, profile, originals, stop, emit, work):
    """단일 작업 실행. 저장 여부 불명확 시 자동 재시도 없음."""
    validate(profile, originals)
    state_path = work / 'progress.json'
    # 기기·설정·원본이 바뀌면 이전 완료 기록을 섞지 않도록 새 작업을 요구.
    signature = hashlib.sha256(json.dumps({'serial': phone.serial, 'profile': profile,
                                         'originals': originals}, sort_keys=True).encode()).hexdigest()
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {
        'signature': signature, 'done': {}, 'pending': None}
    if state['signature'] != signature:
        raise RuntimeError('이전 작업과 선택 조건 불일치. 결과 확인 후 [새 작업] 선택 필요.')
    # 이전 저장 여부가 불명확한 조합은 중복 생성 방지를 위해 자동 재시도하지 않음.
    if state['pending']:
        raise RuntimeError('이전 작업 완료 여부 불명확. 폰의 마지막 결과 확인 후 [새 작업] 선택 필요.')
    current = phone.photos()
    for original in originals:
        if current.get(original['id']) != original:
            raise RuntimeError('선택한 원본 정보 변경. 사진 목록 다시 불러오기 필요.')
    for record in state['done'].values():
        if current.get(record['result']['id']) != record['result']:
            raise RuntimeError('이전 저장 결과 변경. 폰에서 확인 필요.')
    total = len(originals) * len(profile['styles'])
    for original in originals:
        for style in profile['styles']:
            key = original['id'] + ':' + style['name']
            if key in state['done']:
                continue
            check_stop(stop)
            # 폰 조작 전에 기록해 두면 비정상 종료 후에도 미완료 작업을 감지 가능.
            state['pending'] = {'original': original, 'style': style['name']}
            save_json(state_path, state)
            emit(f"{len(state['done']) + 1}/{total}  {PurePosixPath(original['path']).name} · {style['name']}")
            # 매 스타일마다 원본을 다시 열기. 직전 생성 결과에 다음 스타일을 적용하지 않음.
            phone.open_original(original)
            # 화면 안정화 대기 2초. 숫자를 늘리면 더 기다리며, 대기 중에도 중지 가능.
            if stop.wait(2):
                check_stop(stop)
            phone.click(profile['assist'], stop)
            phone.click(profile['style_tab'], stop)
            if profile.get('more'):
                phone.click(profile['more'], stop)
            for _ in range(style.get('swipes', 0)):
                phone.swipe_style(style.get('swipe_y', .82), stop)
            if select_nodes(phone.screen(), profile['save']):
                raise RuntimeError('편집 전 저장 버튼 감지. 원본 화면 및 버튼 설정 확인 필요.')
            # 저장 전후 전체 사진 목록을 비교하므로 다른 촬영·다운로드를 병행하지 않음.
            before = phone.photos()
            if before.get(original['id']) != original:
                raise RuntimeError('원본 정보 변경으로 실행 중단.')
            started = time.monotonic()
            phone.click(style['selector'], stop)
            # 스타일 선택 후 생성 버튼을 별도로 클릭.
            phone.click(profile['generate'], stop)
            # 생성 완료 후 저장 버튼 대기: 최대 180초. 생성이 느리면 이 값을 증가.
            phone.click(profile['save'], stop, timeout=180)
            # 저장 클릭 후 새 파일 확인 제한: 45초.
            deadline = time.monotonic() + 45
            previous, count = None, 0
            while time.monotonic() < deadline:
                check_stop(stop)
                result = saved_result(before, phone.photos(), original)
                # 같은 파일 정보가 연속 3번 관측되어야 저장 완료로 기록.
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
                raise TimeoutError('저장 파일 확인 실패. 폰의 결과 확인 필요.')
    emit('작업 완료. 갤러리에서 결과 사진 품질 평가 필요.')

