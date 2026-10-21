"""기기 연결, 버튼 검색, 원본별 스타일 저장. GUI/빌드 코드는 이 파일에 넣지 않는다."""
# 읽는 순서: execute(전체 순서) → Phone(폰 조작) → saved_result(저장 확인).
# 수정 안내: 대기시간은 click/execute, 스와이프 거리는 swipe_style에서 조정합니다.
# 실제 버튼 이름과 스타일은 현재 GUI에서 등록하며 buttons.json에 저장됩니다.
# 이 파일에 삼성 버튼 이름이 미리 입력되어 있는 것은 아닙니다.
# #으로 시작하는 줄은 설명입니다. 주석만 바꾸면 실행 동작은 바뀌지 않습니다.
# def는 함수 정의, class는 관련 기능의 묶음, raise는 오류를 알리고 중단하는 코드입니다.
# if는 조건, for는 반복, return은 결과 반환입니다. 들여쓰기는 실행 범위를 결정합니다.
from __future__ import annotations

# hashlib: 작업 조건을 짧은 식별값으로 바꿉니다. 사진 암호화나 사진 내용 검사가 아닙니다.
import hashlib
# json: 버튼 설정과 진행 기록을 사람이 읽을 수 있는 JSON 파일로 저장/복원합니다.
import json
# os: Windows 환경변수와 기록 폴더 열기 등 운영체제 기능을 사용합니다.
import os
# Path는 PC 파일 경로, PurePosixPath는 /로 구분되는 Android 파일 경로를 다룹니다.
from pathlib import Path, PurePosixPath
# re: 사진 목록/좌표 문자열이 기대한 형식인지 정규식으로 확인합니다.
import re
# shlex: 폰에 전달하는 명령 인수를 올바르게 묶습니다.
import shlex
# threading: 창을 멈추지 않고 폰 작업을 수행하거나 중지 신호를 전달합니다.
import threading
# time: 대기 제한, 실행 소요시간, 기록 시각을 계산합니다.
import time

# Android 사진 목록의 주소입니다. 사용자가 고르는 앨범 경로가 아닙니다.
# 이 주소를 임의 폴더 경로로 바꾸면 사진 조회와 원본 열기가 실패합니다.
MEDIA = 'content://media/external/images/media'
# 사진을 열 앱의 패키지 이름입니다. 화면에 보이는 앱 이름과 다릅니다.
# 다른 앱으로 바꾸면 Photo Assist 진입 과정과 버튼 설정도 맞지 않을 수 있습니다.
GALLERY = 'com.sec.android.gallery3d'


# 사용자 중지를 일반 오류와 구분하기 위한 예외입니다. 생성 취소 명령은 아닙니다.
class Stopped(Exception):
    """사용자가 중지를 요청했다. 생성 요청 자체를 취소했다는 뜻은 아니다."""


# PC에 설정·이력을 저장할 폴더를 만듭니다. 폰의 결과 사진 저장 위치와 무관합니다.
# 폴더 이름을 바꾸면 기존 설정/진행 기록을 더 이상 같은 위치에서 읽지 않습니다.
def app_data() -> Path:
    root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'PhotoAssistCollector'
    root.mkdir(parents=True, exist_ok=True)
    return root


# Python 데이터를 JSON 텍스트로 저장합니다. 임시 파일 작성 후 교체하여
# 저장 도중 기존 파일이 일부만 덮이는 가능성을 줄입니다. 사진을 저장하는 함수는 아닙니다.
def save_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


# ADB가 돌려준 사진 목록 텍스트를 {사진 ID: 사진 정보} 형태로 바꿉니다.
# id=사진 식별값, size=파일 크기, modified=수정시간, path=폰 내부 파일 경로입니다.
# 아래 정규식은 Android 출력 형식과 맞아야 합니다. 임의로 줄이면 조회 실패를 놓칠 수 있습니다.
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


# 저장 전후 사진 목록을 비교하여 새 결과 한 장을 찾습니다.
# 원본 정보가 바뀌거나 결과가 여러 장/다른 폴더이면 중단합니다.
# None 반환은 아직 저장된 파일이 없거나 크기가 0이라는 뜻이며 이후 다시 확인합니다.
# 이 검사를 제거하면 잘못된 저장을 성공으로 기록할 수 있습니다.
# 사진 내용이나 스타일 품질을 평가하지는 않습니다.
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


# 중지 버튼이 설정한 신호를 확인합니다. 신호가 있으면 예외로 반복 작업을 빠져나갑니다.
def check_stop(stop):
    if stop.is_set():
        raise Stopped('중지됨. 이미 시작된 생성은 폰에서 계속될 수 있어. 마지막 결과를 확인해줘.')


# PC에서 실행되는 ADB 서비스에 연결합니다. 127.0.0.1은 이 PC 자신입니다.
# 5037은 ADB의 기본 포트이며 Photo Assist 생성 서버 주소가 아닙니다.
def adb_client():
    # adbutils의 Windows 배포판에는 ADB와 DLL이 포함된다.
    # 전역 PATH를 바꾸거나 별도 서버를 외부 네트워크에 공개하지 않는다.
    import adbutils
    return adbutils.AdbClient(host='127.0.0.1', port=5037)


# USB 디버깅 기기 목록을 GUI에 전달합니다.
# state가 device일 때만 앱 화면에서 선택 가능한 기기로 취급합니다.
def devices():
    return [dict(serial=d.serial, state=d.state) for d in adb_client().list()]


# 화면 요소의 [왼쪽,위][오른쪽,아래] 좌표 문자열을 숫자 4개로 바꿉니다.
# 형식이 맞지 않으면 크기 0인 영역으로 돌려주어 클릭 후보에서 제외합니다.
def node_bounds(node):
    m = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', node.get('bounds', ''))
    return tuple(map(int, m.groups())) if m else (0, 0, 0, 0)


# 화면 정보 기준으로 숨겨져 있지 않고 면적이 있는 요소인지 확인합니다.
# 실제 다른 창에 가려졌는지까지 이미지로 판단하는 기능은 아닙니다.
def visible(node):
    x1, y1, x2, y2 = node_bounds(node)
    return node.get('visible-to-user', 'true') != 'false' and x2 > x1 and y2 > y1


# 폰의 화면 구조(XML)를 읽습니다. 외부 파일/네트워크 참조는 허용하지 않습니다.
# 파서 보안 옵션은 화면 버튼 선택과 무관하므로 그대로 두는 편이 좋습니다.
def xml_root(xml):
    from lxml import etree
    return etree.fromstring(xml.encode(), parser=etree.XMLParser(resolve_entities=False, no_network=True))


# 등록한 속성(field)과 값(value)이 정확히 일치하는 요소를 찾습니다.
# resource-id=요소 ID, content-desc=접근성 설명, text=표시 글자입니다.
# 부분 일치로 바꾸면 이름이 비슷한 다른 버튼까지 선택될 수 있습니다.
def select_nodes(xml, selector):
    field = selector.get('field')
    if field not in ('resource-id', 'content-desc', 'text') or not selector.get('value'):
        raise ValueError('버튼 이름을 먼저 지정해줘.')
    # 필드 이름은 위의 허용 목록으로 제한, 값은 XPath 변수로 전달한다.
    return [n for n in xml_root(xml).xpath(f'//*[@{field}=$v]', v=selector['value'])
            if visible(n) and n.get('enabled', 'false') == 'true']


# 설정 화면에 보여줄 후보를 만듭니다. 설명 → 글자 → ID 순서로 고유한 값을 찾습니다.
# 중복되거나 이름이 없는 요소는 후보가 안 될 수 있습니다. 모든 요소가 버튼인 것은 아닙니다.
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


# 한 대의 폰을 조작하는 기능을 모은 클래스입니다.
# self는 이 폰 객체 자신이며 serial로 여러 기기 중 대상을 구분합니다.
class Phone:
    # Phone을 만들 때 대상 기기만 정합니다. UI 연결은 처음 필요할 때 수행합니다.
    def __init__(self, serial):
        self.serial = serial
        self.adb = adb_client().device(serial=serial)
        self._ui = None

    # phone.ui를 처음 사용할 때 uiautomator2를 연결하고 이후 같은 객체를 재사용합니다.
    # 첫 연결 시 폰에 보조 프로그램이 배치·실행될 수 있습니다.
    @property
    def ui(self):
        if self._ui is None:
            import uiautomator2 as u2
            self._ui = u2.connect_usb(self.serial)
            self._ui.settings['wait_timeout'] = 1
        return self._ui

    # Android 명령을 실행하고 오류를 확인하는 공통 통로입니다.
    # shlex.join은 파일명 등의 공백/특수문자를 명령 인수로 안전하게 전달하기 위한 처리입니다.
    # timeout=30은 한 shell 명령의 통신 제한이며 이미지 생성 대기시간이 아닙니다.
    def shell(self, *args):
        result = self.adb.shell2(shlex.join(str(a) for a in args), timeout=30)
        if result.returncode or 'Exception' in result.output or 'Error:' in result.output:
            raise RuntimeError(result.output[:700])
        return result.output

    # 폰의 MediaStore에서 사진 ID·크기·수정시간·경로만 조회합니다.
    # 이미지 파일을 PC로 다운로드하지 않습니다. 폴더 필터링은 app.py가 담당합니다.
    def photos(self):
        return parse_media(self.shell('content', 'query', '--uri', MEDIA,
                                      '--projection', '_id:_size:date_modified:_data'))

    # 썸네일 위치 대신 사진 ID 주소로 정확한 원본 열기를 요청합니다.
    # 이 요청 뒤 실제 갤러리가 의도한 사진을 여는지는 기기에서 확인해야 합니다.
    def open_original(self, photo):
        # --activity-clear-top: 기존 사진 Activity 위의 편집 화면을 정리한 뒤 원본을 엽니다.
        # 이 옵션을 빼면 반복 실행마다 사진/편집 화면이 뒤로가기 기록에 쌓일 수 있습니다.
        # 사진 파일이나 앱 데이터를 지우는 옵션이 아닙니다. 강제 종료도 하지 않습니다.
        # standard Activity는 새 인스턴스로 교체됩니다. 다른 원본 URI 처리 여부는 실기기로 확인합니다.
        # 저장 확인 전 오류/중지 시에는 execute가 다음 원본을 열지 않고 멈춥니다.
        self.shell('am', 'start', '-W', '-a', 'android.intent.action.VIEW',
                   '-d', MEDIA + '/' + photo['id'], '-t', 'image/*', '-p', GALLERY,
                   '--grant-read-uri-permission', '--activity-clear-top')

    # 현재 화면 구조를 읽습니다. 스크린샷을 찍거나 저장하는 함수는 아닙니다.
    def screen(self):
        return self.ui.dump_hierarchy(compressed=False)

    # 요소가 나타날 때까지 찾다가 현재 좌표의 중심을 한 번 누릅니다.
    # timeout=20을 늘리면 느린 화면을 더 기다립니다. 잘못된 버튼 설정은 해결되지 않습니다.
    # 같은 요소가 둘 이상이면 잘못 누르지 않도록 중단합니다.
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
            # 0.5초마다 재탐색합니다. 줄이면 탐색은 잦아지지만 폰/통신 부하가 늘어납니다.
            # time.sleep 대신 stop.wait를 써서 대기 중에도 중지 신호를 받을 수 있게 합니다.
            if stop.wait(0.5):
                check_stop(stop)

    # 스타일이 있는 높이(y: 화면 높이의 비율)에서 가로로 넘깁니다.
    # .85→.2는 화면 너비의 85% 지점에서 20% 지점까지 왼쪽으로 이동한다는 뜻입니다.
    # duration=.5는 손가락 이동 시간입니다. 거리/시간을 바꾸면 스타일 이동량도 달라집니다.
    # 실제 스타일 목록에서 확인한 뒤 조정하세요. 반복 횟수는 GUI에서 지정합니다.
    def swipe_style(self, y, stop):
        check_stop(stop)
        width, height = self.ui.window_size()
        self.ui.swipe(int(width * .85), int(height * y), int(width * .2), int(height * y), duration=.5)
        if stop.wait(.5):
            check_stop(stop)


# 원본/스타일/필수 버튼이 준비됐는지 실행 전에 확인합니다.
# 범위를 변경할 때는 app.py의 입력 범위와 설명서도 함께 맞춰야 합니다.
def validate(profile, originals):
    if not originals or not profile.get('styles'):
        raise ValueError('원본 사진과 스타일을 각각 하나 이상 선택해줘.')
    for key in ('assist', 'style_tab', 'generate', 'save'):
        if not profile.get(key):
            raise ValueError('버튼 설정이 필요해: ' + ('생성 버튼 지정' if key == 'generate' else key))
    names = [s['name'] for s in profile['styles']]
    if len(names) != len(set(names)):
        raise ValueError('스타일 이름이 중복돼 있어.')
    for s in profile['styles']:
        if not isinstance(s.get('swipes', 0), int) or not 0 <= s.get('swipes', 0) <= 20:
            raise ValueError('스타일 이동 횟수는 0~20이어야 해.')
        if not 0 < s.get('swipe_y', .82) < 1:
            raise ValueError('스타일 목록 높이는 0~1 사이여야 해.')


# 전체 자동화의 시작점입니다. app.py의 실행 버튼이 이 함수를 호출합니다.
# phone=대상 폰, profile=버튼/스타일 설정, originals=시작 시 선택한 원본 목록,
# stop=중지 신호, emit=화면에 진행 문구를 보내는 함수, work=PC 기록 폴더입니다.
def execute(phone, profile, originals, stop, emit, work):
    """한 번에 한 작업만 실행한다. 불명확한 저장을 자동 재시도하지 않는다."""
    validate(profile, originals)
    state_path = work / 'progress.json'
    # 기기·설정·원본 정보를 묶은 작업 식별값입니다. 사진 내용의 해시가 아닙니다.
    # 조건이 달라지면 이전 완료 기록을 섞어 쓰지 않고 새 작업을 요구합니다.
    signature = hashlib.sha256(json.dumps({'serial': phone.serial, 'profile': profile,
                                         'originals': originals}, sort_keys=True).encode()).hexdigest()
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {
        'signature': signature, 'done': {}, 'pending': None}
    if state['signature'] != signature:
        raise RuntimeError('이전 작업과 선택이 달라. 결과를 확인하고 [새 작업]을 눌러줘.')
    # pending은 시작했지만 저장 완료를 확정하지 못한 조합입니다.
    # 자동으로 재시도하면 이미 저장된 결과가 중복 생성될 수 있어 여기서 멈춥니다.
    if state['pending']:
        raise RuntimeError('이전 작업의 완료 여부가 불명확해. 폰에서 마지막 결과를 확인하고 [새 작업]으로 다시 계획해줘.')
    current = phone.photos()
    for original in originals:
        if current.get(original['id']) != original:
            raise RuntimeError('선택한 원본 정보가 바뀌었어. 사진 목록을 다시 불러와줘.')
    # 과거 완료 결과가 여전히 같은 정보로 존재하는지 확인합니다.
    # 결과를 이동/수정/삭제한 뒤에는 이전 기록으로 계속 실행하지 못할 수 있습니다.
    for record in state['done'].values():
        if current.get(record['result']['id']) != record['result']:
            raise RuntimeError('이전에 저장된 결과가 변경됐어. 폰에서 확인해줘.')
    # 총 작업 수 = 원본 수 × 등록 스타일 수입니다.
    # 바깥 반복이 사진, 안쪽 반복이 스타일이므로 한 사진의 스타일을 모두 끝낸 후 다음 사진으로 갑니다.
    total = len(originals) * len(profile['styles'])
    for original in originals:
        for style in profile['styles']:
            key = original['id'] + ':' + style['name']
            if key in state['done']:
                continue
            check_stop(stop)
            # 실제 폰 조작 전에 진행 중임을 저장합니다.
            # 실행 중 창이 종료되더라도 다음 실행에서 불명확한 작업을 감지하기 위한 기록입니다.
            state['pending'] = {'original': original, 'style': style['name']}
            save_json(state_path, state)
            emit(f"{len(state['done']) + 1}/{total}  {PurePosixPath(original['path']).name} · {style['name']}")
            # 매 스타일마다 같은 원본부터 다시 시작합니다.
            # 이 줄을 사진 반복 바깥으로 옮기면 이전 결과에 다음 스타일을 적용할 위험이 있습니다.
            phone.open_original(original)
            # 사진 열기 명령이 끝나도 화면 애니메이션은 진행 중일 수 있습니다.
            # 2는 클릭 전 대기 초입니다. 늘리면 매 스타일마다 더 오래 기다립니다.
            # stop.wait를 사용하므로 2초 대기 중에도 중지할 수 있습니다.
            if stop.wait(2):
                check_stop(stop)
            phone.click(profile['assist'], stop)
            phone.click(profile['style_tab'], stop)
            # 선택 사항인 목록 펼치기 버튼은 모든 스타일에 공통으로 한 번 누릅니다.
            if profile.get('more'):
                phone.click(profile['more'], stop)
            for _ in range(style.get('swipes', 0)):
                phone.swipe_style(style.get('swipe_y', .82), stop)
            if select_nodes(phone.screen(), profile['save']):
                raise RuntimeError('편집 전부터 저장 버튼이 보여. 원본 화면 또는 버튼 설정을 확인해줘.')
            # 저장 결과를 찾기 위한 기준 목록입니다. 폴더 한 곳이 아니라 전체 사진 목록이므로
            # 다른 앱의 다운로드/촬영으로 새 사진이 생겨도 결과 판정에 영향을 줍니다.
            before = phone.photos()
            if before.get(original['id']) != original:
                raise RuntimeError('원본 정보가 변경되어 중단했어.')
            started = time.monotonic()
            phone.click(style['selector'], stop)
            # 스타일 선택만으로 생성되지 않으므로 등록한 생성 버튼을 한 번 누릅니다.
            # 버튼 이름이 바뀌면 코드 대신 버튼 설정을 다시 지정합니다.
            phone.click(profile['generate'], stop)
            # 생성 요청 후 저장 버튼이 활성화될 때까지 최대 180초 기다립니다.
            # 느린 생성으로 시간 초과가 나면 아래 timeout을 조정합니다. 추가 확인 팝업은 별도 구현입니다.
            phone.click(profile['save'], stop, timeout=180)
            # 저장 클릭 후 새 파일 확인 제한(초)입니다. 저장이 느린 기기라면 이 값을 조정합니다.
            # 파일을 저장하는 명령은 갤러리 UI가 수행하며 이 코드는 저장 여부만 확인합니다.
            deadline = time.monotonic() + 45
            previous, count = None, 0
            while time.monotonic() < deadline:
                check_stop(stop)
                result = saved_result(before, phone.photos(), original)
                # 같은 파일 정보가 연속 3번 관측되어야 완료합니다.
                # 첫 관측은 0, 두 번째는 1, 세 번째는 2입니다. 크기 변화 중인 파일을 기다립니다.
                count = count + 1 if result is not None and result == previous else 0
                if count >= 2:
                    # 저장 확인이 끝난 조합을 완료로 기록합니다. seconds는 스타일 조작부터 저장 확인까지 시간입니다.
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
    # 모든 조합 처리/건너뛰기가 끝났습니다. 완료 메시지는 이미지 품질 합격을 의미하지 않습니다.
    emit('완료. 저장된 사진의 품질은 갤러리에서 평가해줘.')

