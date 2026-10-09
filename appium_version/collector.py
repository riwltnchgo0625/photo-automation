"""Photo Assist 스타일별 결과 저장. 기기별 locator 설정 후 사용한다."""
import argparse
import hashlib
import json
import re
import shlex
import subprocess
import time
from pathlib import Path, PurePosixPath

MEDIA = 'content://media/external/images/media'


def parse_media(output):
    """파일 경로를 마지막 컬럼으로 받아 공백·쉼표가 있는 이름도 보존한다."""
    rows = {}
    for line in output.splitlines():
        if not line.strip() or line.startswith('No result found'):
            continue
        match = re.fullmatch(r'Row: \d+ _id=(\d+), _size=(\d+), date_modified=(\d+), _data=(.+)', line)
        if not match:
            raise RuntimeError('MediaStore 응답을 해석하지 못함: ' + line)
        ident, size, modified, path = match.groups()
        rows[ident] = dict(id=ident, size=int(size), modified=int(modified), path=path)
    return rows


def new_result(before, after, original):
    """원본 변경 및 여러 파일 생성은 성공으로 오인하지 않고 중단한다."""
    if after.get(original['id']) != original:
        raise RuntimeError('원본 메타데이터가 변경되었거나 원본을 찾을 수 없음')
    added = [v for k, v in after.items() if k not in before]
    if len(added) > 1:
        raise RuntimeError('여러 이미지가 동시에 생성됨. 결과를 수동 확인해야 함')
    if not added or added[0]['size'] <= 0:
        return None
    result = added[0]
    if PurePosixPath(result['path']).parent != PurePosixPath(original['path']).parent:
        raise RuntimeError('결과가 원본과 다른 폴더에 저장됨: ' + result['path'])
    return result


class Device:
    def __init__(self, cfg):
        self.cfg = cfg
        if not cfg.get('udid') or cfg['udid'].startswith('CHANGE'):
            raise ValueError('config.json의 udid를 adb devices에 나온 값으로 바꿔줘')

    def shell(self, *args):
        # Windows의 cmd.exe를 거치지 않고, Android 셸 인수는 별도로 인용한다.
        command = shlex.join(str(x) for x in args)
        p = subprocess.run([self.cfg.get('adb', 'adb'), '-s', self.cfg['udid'], 'shell', command],
                           capture_output=True, encoding='utf-8', errors='replace', timeout=60)
        if p.returncode or p.stderr.strip() or 'Exception' in p.stdout or 'Error:' in p.stdout:
            raise RuntimeError(p.stderr or p.stdout)
        return p.stdout

    def media(self):
        return parse_media(self.shell('content', 'query', '--uri', MEDIA,
                                      '--projection', '_id:_size:date_modified:_data'))

    def open_original(self, ident):
        # 인덱스/썸네일 위치 대신 원본의 MediaStore URI로 연다.
        self.shell('am', 'force-stop', self.cfg['gallery_package'])
        self.shell('am', 'start', '-W', '-a', 'android.intent.action.VIEW', '-d',
                   f'{MEDIA}/{ident}', '-t', 'image/*', '-p', self.cfg['gallery_package'],
                   '--grant-read-uri-permission')


from backend import connect


def locator(spec):
    """Inspector에서 확인한 id/accessibility id/xpath를 그대로 사용한다."""
    if spec.get('by') not in ('id', 'accessibility id', 'xpath') or not spec.get('value'):
        raise ValueError('빈 locator 또는 지원하지 않는 by: ' + str(spec))
    if 'CHANGE' in spec['value']:
        raise ValueError('실기기에서 확인한 locator로 CHANGE 값을 바꿔줘')
    return spec['by'], spec['value']


def wait_element(driver, spec, timeout):
    # 두 실행 방식에서 같은 대기 로직을 사용한다. Selenium 의존성이 없다.
    deadline = time.monotonic() + timeout
    while True:
        elements = driver.find_elements(*locator(spec))
        visible = [e for e in elements if e.is_displayed() and e.is_enabled()]
        if len(visible) > 1:
            raise RuntimeError('locator가 여러 요소에 일치함: ' + str(spec))
        if visible:
            return visible[0]
        if time.monotonic() >= deadline:
            raise TimeoutError('요소 대기 시간 초과: ' + str(spec))
        time.sleep(0.5)


def steps(driver, actions, timeout):
    """스타일 패널의 더보기/스크롤 등 기기별 동작을 설정으로 표현한다."""
    for action in actions:
        if action['action'] == 'click':
            wait_element(driver, action['locator'], timeout).click()
        elif action['action'] == 'swipe':
            rect = driver.get_window_size()
            x1, y1, x2, y2 = action['points']
            driver.swipe(int(x1 * rect['width']), int(y1 * rect['height']),
                         int(x2 * rect['width']), int(y2 * rect['height']), 500)
        else:
            raise ValueError('지원하지 않는 action: ' + str(action))


def validate_steps(actions):
    for a in actions:
        if a['action'] == 'click':
            locator(a['locator'])
        elif a['action'] == 'swipe':
            if len(a['points']) != 4 or not all(0 <= x <= 1 for x in a['points']):
                raise ValueError('swipe points는 0~1 범위 좌표 4개여야 함')
        else:
            raise ValueError('알 수 없는 action')


def dump(driver, folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'screen.xml').write_text(driver.page_source, encoding='utf-8')
    driver.save_screenshot(str(folder / 'screen.png'))


def write_json(path, value):
    # 실행 중 강제 종료에도 기존 상태 파일이 반쯤 쓰이지 않게 한다.
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def make_plan(cfg, device):
    if not cfg['original_ids'] or not cfg['styles']:
        raise ValueError('original_ids와 styles를 설정해야 함')
    if len(set(cfg['original_ids'])) != len(cfg['original_ids']):
        raise ValueError('원본 ID 중복')
    names = [s['name'] for s in cfg['styles']]
    if len(set(names)) != len(names):
        raise ValueError('스타일 이름 중복')
    media = device.media()
    originals = []
    for ident in cfg['original_ids']:
        row = media[str(ident)]
        if str(PurePosixPath(row['path']).parent) != cfg['album_dir'].rstrip('/'):
            raise ValueError('선택한 원본이 album_dir 밖에 있음: ' + row['path'])
        originals.append(row)
    return originals


def run(cfg, device, root):
    # 실행 전 전체 설정을 검사해서 작업 도중 설정 오류가 나지 않게 한다.
    originals = make_plan(cfg, device)
    validate_steps(cfg['entry_steps'])
    locator(cfg['save_locator'])
    for style in cfg['styles']:
        validate_steps(style['steps'])
    signature = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    state_path = root / 'state.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {
        'config_signature': signature, 'originals': originals, 'done': {}, 'pending': None}
    if state['config_signature'] != signature or state['originals'] != originals:
        raise RuntimeError('설정/원본이 이전 실행과 다름. 상태를 확인하고 새 --work 폴더를 지정해줘')
    if state['pending']:
        raise RuntimeError('이전 작업 완료 여부 불명확. state.json의 pending과 갤러리 결과를 먼저 확인해줘')
    current = device.media()
    for record in state['done'].values():
        if current.get(record['result']['id']) != record['result']:
            raise RuntimeError('이전 저장 결과가 없어졌거나 변경됨. 자동 재생성을 중단함')
    driver = connect(cfg)
    try:
        for original in originals:
            for index, style in enumerate(cfg['styles']):
                key = f"{original['id']}:{index}"
                if key in state['done']:
                    continue
                print(f"실행: {original['path']} / {style['name']}", flush=True)
                # 생성/저장 전에 pending을 기록한다. 재실행 시 중복 생성하지 않는다.
                state['pending'] = {'key': key, 'style': style['name'], 'original': original}
                write_json(state_path, state)
                device.open_original(original['id'])
                steps(driver, cfg['entry_steps'], cfg['ui_timeout'])
                if any(e.is_displayed() for e in driver.find_elements(*locator(cfg['save_locator']))):
                    raise RuntimeError('스타일 적용 전 저장 버튼이 이미 보임. 원본 진입/locator를 확인해줘')
                before = device.media()
                if before.get(original['id']) != original:
                    raise RuntimeError('원본 변경 감지')
                started = time.monotonic()
                steps(driver, style['steps'], cfg['ui_timeout'])
                save = wait_element(driver, cfg['save_locator'], cfg['generation_timeout'])
                save.click()
                deadline = time.monotonic() + cfg['save_timeout']
                last = None
                stable = 0
                result = None
                while time.monotonic() < deadline:
                    result = new_result(before, device.media(), original)
                    stable = stable + 1 if result is not None and result == last else 0
                    if stable >= 2:
                        break
                    last = result
                    time.sleep(1)
                else:
                    raise TimeoutError('저장된 새 이미지 확인 시간 초과. 갤러리를 확인해줘')
                state['done'][key] = {'original': original, 'style': style['name'],
                                      'result': result, 'seconds': round(time.monotonic() - started, 2)}
                state['pending'] = None
                write_json(state_path, state)
                print('저장 확인: ' + result['path'], flush=True)
        print('설정한 모든 원본 × 스타일 저장 완료. 정성평가는 갤러리에서 진행해줘.')
    except BaseException:
        # 오류 당시 화면은 문제 분석용이며 생성 결과 원본을 PC로 복사하지 않는다.
        try:
            dump(driver, root / ('failure_' + time.strftime('%Y%m%d_%H%M%S')))
        except Exception:
            pass
        raise
    finally:
        driver.quit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['scan', 'dump', 'plan', 'run'])
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--work', default='run_data')
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    device = Device(cfg)
    root = Path(args.work)
    root.mkdir(parents=True, exist_ok=True)
    if args.command == 'scan':
        rows = list(device.media().values())
        write_json(root / 'media.json', rows)
        for row in rows:
            if str(PurePosixPath(row['path']).parent) == cfg['album_dir'].rstrip('/'):
                print(row['id'], row['path'])
        print('전체 사진 ID/경로 목록: ' + str(root / 'media.json'))
    elif args.command == 'dump':
        driver = connect(cfg)
        try:
            dump(driver, root / ('screen_' + time.strftime('%Y%m%d_%H%M%S')))
        finally:
            driver.quit()
    elif args.command == 'plan':
        originals = make_plan(cfg, device)
        for original in originals:
            for style in cfg['styles']:
                print(original['path'], '→', style['name'])
        print(f"총 {len(originals) * len(cfg['styles'])}건. 실행/생성하지 않았음.")
    else:
        run(cfg, device, root)


if __name__ == '__main__':
    main()
