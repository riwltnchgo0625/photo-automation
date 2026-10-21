"""PC 사용 화면 구성. 폰 작업 로직은 automation.py에서 처리."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path, PurePosixPath
import queue
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, messagebox

from automation import Phone, Stopped, app_data, devices, execute, save_json, screen_candidates, validate


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Photo Assist · 스타일 결과 수집')
        # 창 크기: 가로 × 세로. 최소 크기는 아래 minsize에서 변경.
        self.geometry('980x790')
        self.minsize(850, 700)
        self.store = app_data()
        self.profile_path = self.store / 'buttons.json'
        # 버튼·스타일 기본값. 기존 buttons.json이 있으면 저장된 설정을 불러오기.
        self.profile = {'assist': None, 'style_tab': None, 'generate': None, 'save': None, 'more': None, 'styles': []}
        try:
            if self.profile_path.exists():
                self.profile.update(json.loads(self.profile_path.read_text(encoding='utf-8')))
        except Exception as e:
            messagebox.showerror('설정 읽기 실패', str(e))
        self.photos, self.shown, self.candidates = {}, [], []
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.busy = False
        self.buttons = []
        self.serial = tk.StringVar()
        self.album = tk.StringVar()
        self.status = tk.StringVar(value='USB 연결 후 [기기 찾기] 선택.')
        self.build_ui()
        self.refresh_profile()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    def button(self, parent, text, command, **pack):
        b = ttk.Button(parent, text=text, command=command)
        b.pack(**pack)
        self.buttons.append(b)
        return b

    # PC 화면 구성. 버튼 문구와 배치는 여기서 수정.
    def build_ui(self):
        top = ttk.Frame(self, padding=16)
        top.pack(fill='x')
        ttk.Label(top, text='Photo Assist', font=('맑은 고딕', 20, 'bold')).pack(anchor='w')
        ttk.Label(top, text='원본 사진 → 스타일별 생성 → 같은 갤러리 폴더에 저장').pack(anchor='w', pady=(4, 12))
        device_row = ttk.Frame(top)
        device_row.pack(fill='x')
        self.device_box = ttk.Combobox(device_row, textvariable=self.serial, state='readonly', width=30)
        self.device_box.pack(side='left', padx=(0, 8))
        self.device_box.bind('<<ComboboxSelected>>', lambda _: self.clear_photos())
        self.button(device_row, '기기 찾기', self.find_devices, side='left')
        ttk.Label(top, textvariable=self.status, wraplength=920).pack(anchor='w', pady=(10, 0))
        notebook = ttk.Notebook(self)
        notebook.pack(fill='both', expand=True, padx=16)
        run = ttk.Frame(notebook, padding=12)
        settings = ttk.Frame(notebook, padding=12)
        help_tab = ttk.Frame(notebook, padding=12)
        notebook.add(run, text='  실행  ')
        notebook.add(settings, text='  처음 한 번 · 버튼 설정  ')
        notebook.add(help_tab, text='  사용 방법  ')

        self.button(run, '1. 사진 목록 불러오기', self.load_photos, anchor='w')
        self.album_box = ttk.Combobox(run, textvariable=self.album, state='readonly')
        self.album_box.pack(fill='x', pady=8)
        self.album_box.bind('<<ComboboxSelected>>', lambda _: self.show_album())
        ttk.Label(run, text='2. 편집할 원본만 선택. Ctrl / Shift로 여러 장 선택 가능.').pack(anchor='w')
        self.photo_list = tk.Listbox(run, selectmode='extended', exportselection=False, height=9)
        self.photo_list.pack(fill='both', expand=True, pady=6)
        self.style_summary = tk.StringVar()
        ttk.Label(run, textvariable=self.style_summary).pack(anchor='w', pady=6)
        controls = ttk.Frame(run)
        controls.pack(fill='x')
        self.button(controls, '선택한 첫 사진 · 첫 스타일만 실행', lambda: self.start(True), side='left', padx=(0, 8))
        self.button(controls, '3. 선택한 원본 × 모든 등록 스타일 실행', lambda: self.start(False), side='left')
        # 중지 버튼은 작업 중에도 사용할 수 있도록 별도로 생성.
        stop_button = ttk.Button(controls, text='중지', command=self.request_stop)
        stop_button.pack(side='right')
        footer = ttk.Frame(run)
        footer.pack(fill='x', pady=(8, 0))
        self.button(footer, '새 작업', self.new_job, side='left')
        self.button(footer, '기록 폴더 열기', self.open_records, side='left', padx=8)

        ttk.Label(settings, text='폰에서 대상 화면 열기 → 버튼 목록 읽기 → 항목 지정. 등록 중 자동 클릭 없음.',
                  wraplength=900).pack(anchor='w')
        ttk.Label(settings, text='첫 화면 조회 시 기기 자동화용 보조 프로그램 배치·실행 가능.',
                  wraplength=900).pack(anchor='w', pady=(3, 8))
        self.button(settings, '현재 폰 화면의 버튼 읽기', self.read_screen, anchor='w')
        self.candidate_list = tk.Listbox(settings, exportselection=False, height=8)
        self.candidate_list.pack(fill='both', expand=True, pady=8)
        assignments = ttk.Frame(settings)
        assignments.pack(fill='x')
        for key, label in [('assist', '포토 어시스트'), ('style_tab', '스타일 탭'), ('generate', '생성 버튼'), ('save', '다른 파일로 저장')]:
            self.button(assignments, label + ' 지정', lambda k=key: self.assign(k), side='left', padx=(0, 5))
        self.button(settings, '목록 펼치기(선택) 지정', lambda: self.assign('more'), anchor='w', pady=(5, 0))
        self.button(settings, '목록 펼치기 설정 해제', lambda: self.assign('more', clear=True), anchor='w', pady=5)
        self.mapping = tk.StringVar()
        ttk.Label(settings, textvariable=self.mapping, wraplength=900).pack(anchor='w', pady=4)
        style_row = ttk.Frame(settings)
        style_row.pack(fill='x', pady=5)
        ttk.Label(style_row, text='스타일 이름').pack(side='left')
        self.style_name = ttk.Entry(style_row, width=16)
        self.style_name.pack(side='left', padx=5)
        ttk.Label(style_row, text='처음 목록에서 왼쪽 스와이프 횟수').pack(side='left')
        # 횟수 범위를 바꾸면 add_style과 automation.py의 validate도 함께 수정.
        self.swipes = ttk.Spinbox(style_row, from_=0, to=20, width=4)
        self.swipes.set('0')
        self.swipes.pack(side='left', padx=5)
        self.button(style_row, '선택한 버튼을 스타일로 추가', self.add_style, side='left')
        self.style_list = tk.Listbox(settings, exportselection=False, height=4)
        self.style_list.pack(fill='x', pady=4)
        self.button(settings, '선택한 스타일 삭제', self.remove_style, anchor='w')
        text = tk.Text(help_tab, wrap='word', relief='flat', font=('맑은 고딕', 10))
        text.pack(fill='both', expand=True)
        text.insert('1.0', HELP)
        text.configure(state='disabled')
        self.log = tk.Text(self, height=7, state='disabled', wrap='word')
        self.log.pack(fill='x', padx=16, pady=12)

    def say(self, text):
        self.status.set(text)
        self.log.configure(state='normal')
        self.log.insert('end', time.strftime('%H:%M:%S ') + text + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    # 폰 작업은 별도 스레드에서 실행하고, 화면 갱신은 poll에 위임.
    def job(self, func, callback=None):
        if self.busy:
            return
        self.busy = True
        self.stop.clear()
        for button in self.buttons:
            button.configure(state='disabled')
        self.device_box.configure(state='disabled')
        self.album_box.configure(state='disabled')

        def worker():
            try:
                result = func()
                self.events.put(('result', (callback, result)))
            except Stopped as e:
                self.events.put(('log', str(e)))
            except Exception as e:
                with (self.store / 'errors.log').open('a', encoding='utf-8') as f:
                    f.write(time.strftime('%Y-%m-%d %H:%M:%S\n') + traceback.format_exc() + '\n')
                self.events.put(('error', str(e)))
            finally:
                self.events.put(('done', None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == 'result':
                    callback, value = payload
                    if callback:
                        callback(value)
                elif kind == 'error':
                    self.say(payload)
                    messagebox.showerror('작업 중단', payload)
                elif kind == 'log':
                    self.say(payload)
                elif kind == 'done':
                    self.busy = False
                    for button in self.buttons:
                        button.configure(state='normal')
                    self.device_box.configure(state='readonly')
                    self.album_box.configure(state='readonly')
        except queue.Empty:
            pass
        finally:
            self.after(100, self.poll)

    def phone(self):
        serial = self.serial.get()
        if not serial:
            raise ValueError('기기 선택 필요.')
        return Phone(serial)

    def find_devices(self):
        def done(rows):
            self.clear_photos()
            ready = [d['serial'] for d in rows if d['state'] == 'device']
            self.device_box['values'] = ready
            self.serial.set(ready[0] if ready else '')
            self.say('기기 선택 완료.' if ready else '연결된 기기 없음. USB 디버깅·PC 연결 허용·USB 드라이버 확인 필요.')
            for d in rows:
                if d['state'] != 'device':
                    self.say(d['serial'] + ': ' + d['state'] + ' — 폰 화면에서 PC 연결 허용 여부 확인 필요.')
        self.job(devices, done)

    # 기기를 바꾸면 이전 기기의 사진 선택을 초기화. 실제 파일은 삭제하지 않음.
    def clear_photos(self):
        self.photos, self.shown = {}, []
        self.photo_list.delete(0, 'end')
        self.album.set('')
        self.album_box['values'] = []

    def load_photos(self):
        try:
            phone = self.phone()
        except ValueError as e:
            self.say(str(e)); return
        def done(rows):
            self.photos = rows
            folders = sorted({str(PurePosixPath(p['path']).parent) for p in rows.values()})
            self.album_box['values'] = folders
            if self.album.get() not in folders:
                self.album.set(folders[0] if folders else '')
            self.show_album()
            self.say('사진 목록 조회 완료. 폴더 및 원본 사진 선택.')
        self.job(phone.photos, done)

    # 사진 실행 순서는 파일 경로 문자순임. 정렬을 바꾸려면 아래 key를 수정.
    def show_album(self):
        self.shown = sorted([p for p in self.photos.values()
                             if str(PurePosixPath(p['path']).parent) == self.album.get()], key=lambda p: p['path'])
        self.photo_list.delete(0, 'end')
        for p in self.shown:
            self.photo_list.insert('end', f"{PurePosixPath(p['path']).name}  · ID {p['id']}")

    # 현재 폰 화면에서 버튼 후보를 읽기. 화면을 바꾼 뒤 다시 읽어야 함.
    def read_screen(self):
        try:
            phone = self.phone()
        except ValueError as e:
            self.say(str(e)); return
        def read():
            xml = phone.screen()
            return screen_candidates(xml), phone.ui.window_size()
        def done(result):
            self.candidates, self.screen_size = result
            self.candidate_list.delete(0, 'end')
            for item in self.candidates:
                self.candidate_list.insert('end', item['label'] + '  [' + item['selector']['field'] + ']')
            self.say(f'{len(self.candidates)}개 요소 조회 완료. 대상 버튼 선택 및 지정.')
        self.job(read, done)

    def selected_candidate(self):
        selection = self.candidate_list.curselection()
        if not selection:
            raise ValueError('현재 화면 목록에서 버튼 1개 선택 필요.')
        return self.candidates[selection[0]]

    # 선택한 버튼을 역할에 연결하고 설정 파일에 저장.
    def assign(self, key, clear=False):
        try:
            self.profile[key] = None if clear else self.selected_candidate()['selector']
            self.refresh_profile(save=True)
        except ValueError as e:
            self.say(str(e))

    def refresh_profile(self, save=False):
        if save:
            save_json(self.profile_path, self.profile)
        labels = [('assist', '포토 어시스트'), ('style_tab', '스타일'), ('generate', '생성'), ('save', '저장'), ('more', '목록 펼치기')]
        self.mapping.set(' / '.join(name + ': ' + (self.profile.get(k) or {}).get('value', '미지정') for k, name in labels))
        self.style_list.delete(0, 'end')
        for s in self.profile['styles']:
            self.style_list.insert('end', f"{s['name']} · 왼쪽 이동 {s.get('swipes', 0)}회")
        self.style_summary.set(f"등록된 스타일 {len(self.profile['styles'])}개 · 추가/변경은 [버튼 설정] 탭")

    # 추가한 순서대로 실행. 스타일 이름은 완료 기록에도 사용하므로 중복을 방지.
    def add_style(self):
        try:
            item = self.selected_candidate()
            name = self.style_name.get().strip() or item['label']
            count = int(self.swipes.get())
            if not 0 <= count <= 20:
                raise ValueError('이동 횟수 지정 범위: 0~20.')
            if any(s['name'] == name for s in self.profile['styles']):
                raise ValueError('동일 스타일 이름 등록 상태.')
            _, y1, _, y2 = item['bounds']
            y = max(.01, min(.99, (y1 + y2) / 2 / self.screen_size[1]))
            # 버튼의 세로 중심을 스와이프 높이로 저장. 화면 배치가 바뀌면 다시 등록.
            self.profile['styles'].append({'name': name, 'selector': item['selector'], 'swipes': count, 'swipe_y': y})
            self.refresh_profile(save=True)
            self.style_name.delete(0, 'end')
        except ValueError as e:
            self.say(str(e))

    def remove_style(self):
        selection = self.style_list.curselection()
        if selection:
            del self.profile['styles'][selection[0]]
            self.refresh_profile(save=True)

    # first_only=True이면 첫 사진·첫 스타일만 실제 생성하고 저장.
    def start(self, first_only):
        try:
            phone = self.phone()
            originals = [self.shown[i].copy() for i in self.photo_list.curselection()]
            profile = copy.deepcopy(self.profile)
            if first_only:
                originals, profile['styles'] = originals[:1], profile['styles'][:1]
            validate(profile, originals)
        except ValueError as e:
            self.say(str(e)); return
        emit = lambda text: self.events.put(('log', text))
        self.job(lambda: execute(phone, profile, originals, self.stop, emit, self.store))

    def request_stop(self):
        if self.busy:
            self.stop.set()
            self.say('중지 요청 접수. 진행 중인 통신 종료 후 다음 클릭 전 중단. 이미 시작된 생성 취소 불가.')

    # 완료 이력만 보관하고 새로 시작. 같은 조합을 다시 실행하면 결과가 중복될 수 있음.
    def new_job(self):
        path = self.store / 'progress.json'
        if path.exists():
            if not messagebox.askyesno('새 작업', '새 작업 시작 확인.\n기존 완료 이력 보관 및 저장된 사진 유지. 동일 사진·스타일 재선택 시 중복 생성 가능.'):
                return
            path.rename(self.store / f'progress_{time.time_ns()}.json')
        self.say('새 작업 준비 완료. 다음 실행 대상 원본 선택.')

    def open_records(self):
        if sys.platform == 'win32':
            os.startfile(self.store)
        else:
            self.say(str(self.store))

    # 작업 중에는 먼저 중지하고, 작업이 끝난 뒤 창을 닫기.
    def close(self):
        if self.busy:
            self.request_stop()
            return
        self.destroy()


HELP = '''처음 한 번 · 버튼 설정
1. 폰 USB 디버깅 활성화 및 PC 연결 허용.
2. [기기 찾기]에서 대상 폰 선택.
3. 폰에서 원본 사진 열기 → [현재 폰 화면의 버튼 읽기] → 포토 어시스트 항목 선택 → [포토 어시스트 지정].
4. 폰에서 포토 어시스트 진입 → 화면 읽기 → 스타일 항목 선택 → [스타일 탭 지정].
5. 스타일 화면 읽기 → 스타일 항목 선택 → 이름·왼쪽 스와이프 횟수 입력 → 스타일 추가.
   이동 횟수는 처음 목록 위치 기준. 필요한 경우에만 [목록 펼치기] 지정.
6. 폰에서 스타일 선택 → 화면 읽기 → 생성 항목 선택 → [생성 버튼 지정].
7. 폰에서 직접 생성 → 완료 대기 → 화면 읽기 → 다른 파일로 저장 항목 지정.
   원본 덮어쓰기 버튼 지정 금지. 화면 변경 시 버튼 정보 재확인 필요.

실행
사진 목록 조회 → 폴더 선택 → 원본 선택 → 첫 사진·첫 스타일 시험 실행.
저장 위치·원본 확인 후 [새 작업] 선택 및 전체 실행.
시험 조합을 다시 포함할 경우 결과 중복 생성 가능.
폰 잠금 해제 및 Photo Assist용 인터넷 연결 유지.
실행 중 다른 앱 조작·촬영·사진 다운로드 금지.

중지와 기록
중지: 이후 조작 중단. 이미 시작된 생성 취소 불가.
완료 여부가 불명확한 작업의 자동 재실행 없음.
[기록 폴더 열기]: PC의 버튼 설정·실행 이력·오류 기록 확인.
결과 사진 저장 위치: 원본과 같은 폰 폴더.

현재 확인 범위와 제한
S26+ 한 장·3d 툰 실행 성공 보고. 여러 장 연속 실행 및 화면 누적 수정 효과 확인 필요.
이름 없는 요소·세로 목록·스타일별 별도 진입 경로·추가 팝업은 추가 구현 필요.
저장 전후 사진 목록 차이로 결과 연결. 다른 이미지 생성 작업 병행 금지.
폰 자동화 보조 프로그램 실행 가능. 루팅·보안 차단 해제·원본 삭제 명령 없음.
원본 보존 확인: 파일 경로·크기·수정시간 비교. 파일 내용 해시 비교 제외.
'''


# EXE 패키징 검사임. 폰을 연결하는 실기기 테스트는 아님.
def self_test():
    import adbutils
    import uiautomator2
    from lxml import etree
    adb_file = Path(adbutils.__file__).parent / 'binaries' / ('adb.exe' if sys.platform == 'win32' else 'adb')
    assert adb_file.exists(), 'Bundled ADB missing'
    assert (Path(uiautomator2.__file__).parent / 'assets' / 'u2.jar').exists(), 'u2.jar missing'
    assert etree.fromstring(b'<ok/>').tag == 'ok'
    import tempfile
    with tempfile.TemporaryDirectory() as temp:
        old = os.environ.get('LOCALAPPDATA')
        os.environ['LOCALAPPDATA'] = temp
        try:
            root = App()
            root.update()
            root.destroy()
        finally:
            if old is None:
                os.environ.pop('LOCALAPPDATA', None)
            else:
                os.environ['LOCALAPPDATA'] = old


if __name__ == '__main__':
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')
    if '--self-test' in sys.argv:
        self_test()
    else:
        lock = (app_data() / 'app.lock').open('a+b')
        if sys.platform == 'win32':
            import msvcrt
            if lock.tell() == 0:
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                root = tk.Tk(); root.withdraw()
                messagebox.showinfo('이미 실행 중', '기존 Photo Assist 창 사용.')
                root.destroy()
                sys.exit(1)
        App().mainloop()

