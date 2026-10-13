"""사용 화면만 담당한다. 휴대전화 작업 로직은 automation.py에 둔다."""
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
        self.geometry('980x790')
        self.minsize(850, 700)
        self.store = app_data()
        self.profile_path = self.store / 'buttons.json'
        self.profile = {'assist': None, 'style_tab': None, 'save': None, 'more': None, 'styles': []}
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
        self.status = tk.StringVar(value='USB 연결 후 [기기 찾기]를 눌러줘.')
        self.build_ui()
        self.refresh_profile()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    def button(self, parent, text, command, **pack):
        b = ttk.Button(parent, text=text, command=command)
        b.pack(**pack)
        self.buttons.append(b)
        return b

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
        ttk.Label(run, text='2. 편집할 원본만 선택해줘. Ctrl / Shift로 여러 장 선택할 수 있어.').pack(anchor='w')
        self.photo_list = tk.Listbox(run, selectmode='extended', exportselection=False, height=9)
        self.photo_list.pack(fill='both', expand=True, pady=6)
        self.style_summary = tk.StringVar()
        ttk.Label(run, textvariable=self.style_summary).pack(anchor='w', pady=6)
        controls = ttk.Frame(run)
        controls.pack(fill='x')
        self.button(controls, '선택한 첫 사진 · 첫 스타일만 실행', lambda: self.start(True), side='left', padx=(0, 8))
        self.button(controls, '3. 선택한 원본 × 모든 등록 스타일 실행', lambda: self.start(False), side='left')
        stop_button = ttk.Button(controls, text='중지', command=self.request_stop)
        stop_button.pack(side='right')
        footer = ttk.Frame(run)
        footer.pack(fill='x', pady=(8, 0))
        self.button(footer, '새 작업', self.new_job, side='left')
        self.button(footer, '기록 폴더 열기', self.open_records, side='left', padx=8)

        ttk.Label(settings, text='폰에서 해당 화면을 직접 연 뒤 버튼 목록을 읽어 지정해줘. 이 화면에서는 자동 클릭하지 않아.',
                  wraplength=900).pack(anchor='w')
        ttk.Label(settings, text='처음 화면을 읽을 때 기기 자동화용 보조 프로그램이 배치·실행될 수 있어.',
                  wraplength=900).pack(anchor='w', pady=(3, 8))
        self.button(settings, '현재 폰 화면의 버튼 읽기', self.read_screen, anchor='w')
        self.candidate_list = tk.Listbox(settings, exportselection=False, height=8)
        self.candidate_list.pack(fill='both', expand=True, pady=8)
        assignments = ttk.Frame(settings)
        assignments.pack(fill='x')
        for key, label in [('assist', '포토 어시스트'), ('style_tab', '스타일 탭'), ('save', '다른 파일로 저장'), ('more', '목록 펼치기(선택)')]:
            self.button(assignments, label + ' 지정', lambda k=key: self.assign(k), side='left', padx=(0, 5))
        self.button(settings, '목록 펼치기 설정 해제', lambda: self.assign('more', clear=True), anchor='w', pady=5)
        self.mapping = tk.StringVar()
        ttk.Label(settings, textvariable=self.mapping, wraplength=900).pack(anchor='w', pady=4)
        style_row = ttk.Frame(settings)
        style_row.pack(fill='x', pady=5)
        ttk.Label(style_row, text='스타일 이름').pack(side='left')
        self.style_name = ttk.Entry(style_row, width=16)
        self.style_name.pack(side='left', padx=5)
        ttk.Label(style_row, text='처음 목록에서 왼쪽 스와이프 횟수').pack(side='left')
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
                    messagebox.showerror('작업을 멈췄어', payload)
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
            raise ValueError('기기를 먼저 선택해줘.')
        return Phone(serial)

    def find_devices(self):
        def done(rows):
            self.clear_photos()
            ready = [d['serial'] for d in rows if d['state'] == 'device']
            self.device_box['values'] = ready
            self.serial.set(ready[0] if ready else '')
            self.say('기기 선택 완료.' if ready else '연결된 기기가 없어. USB 디버깅·폰의 연결 허용·USB 드라이버를 확인해줘.')
            for d in rows:
                if d['state'] != 'device':
                    self.say(d['serial'] + ': ' + d['state'] + ' — 폰 화면에서 연결 허용 여부를 확인해줘.')
        self.job(devices, done)

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
            self.say('사진 목록을 불러왔어. 폴더와 원본 사진을 선택해줘.')
        self.job(phone.photos, done)

    def show_album(self):
        self.shown = sorted([p for p in self.photos.values()
                             if str(PurePosixPath(p['path']).parent) == self.album.get()], key=lambda p: p['path'])
        self.photo_list.delete(0, 'end')
        for p in self.shown:
            self.photo_list.insert('end', f"{PurePosixPath(p['path']).name}  · ID {p['id']}")

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
            self.say(f'{len(self.candidates)}개 요소를 읽었어. 원하는 버튼을 선택해서 지정해줘.')
        self.job(read, done)

    def selected_candidate(self):
        selection = self.candidate_list.curselection()
        if not selection:
            raise ValueError('현재 화면 목록에서 버튼 하나를 선택해줘.')
        return self.candidates[selection[0]]

    def assign(self, key, clear=False):
        try:
            self.profile[key] = None if clear else self.selected_candidate()['selector']
            self.refresh_profile(save=True)
        except ValueError as e:
            self.say(str(e))

    def refresh_profile(self, save=False):
        if save:
            save_json(self.profile_path, self.profile)
        labels = [('assist', '포토 어시스트'), ('style_tab', '스타일'), ('save', '저장'), ('more', '목록 펼치기')]
        self.mapping.set(' / '.join(name + ': ' + (self.profile.get(k) or {}).get('value', '미지정') for k, name in labels))
        self.style_list.delete(0, 'end')
        for s in self.profile['styles']:
            self.style_list.insert('end', f"{s['name']} · 왼쪽 이동 {s.get('swipes', 0)}회")
        self.style_summary.set(f"등록된 스타일 {len(self.profile['styles'])}개 · 추가/변경은 [버튼 설정] 탭")

    def add_style(self):
        try:
            item = self.selected_candidate()
            name = self.style_name.get().strip() or item['label']
            count = int(self.swipes.get())
            if not 0 <= count <= 20:
                raise ValueError('이동 횟수는 0~20으로 지정해줘.')
            if any(s['name'] == name for s in self.profile['styles']):
                raise ValueError('같은 스타일 이름이 이미 있어.')
            _, y1, _, y2 = item['bounds']
            y = max(.01, min(.99, (y1 + y2) / 2 / self.screen_size[1]))
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
            self.say('중지 요청됨. 진행 중인 통신이 끝나면 다음 클릭 전에 멈춰. 이미 시작한 생성은 취소하지 않아.')

    def new_job(self):
        path = self.store / 'progress.json'
        if path.exists():
            if not messagebox.askyesno('새 작업', '기존 완료 이력을 보관하고 새 작업을 시작할까?\n이미 저장된 사진은 남아 있어. 같은 사진·스타일을 고르면 중복 생성될 수 있어.'):
                return
            path.rename(self.store / f'progress_{time.time_ns()}.json')
        self.say('새 작업을 준비했어. 다음 실행에서 사용할 원본을 선택해줘.')

    def open_records(self):
        if sys.platform == 'win32':
            os.startfile(self.store)
        else:
            self.say(str(self.store))

    def close(self):
        if self.busy:
            self.request_stop()
            return
        self.destroy()


HELP = '''처음 한 번
1. 폰에서 USB 디버깅을 켜고 PC 연결을 허용해줘.
2. [기기 찾기]로 연결할 폰을 선택해줘.
3. 폰에서 원본 사진을 직접 열고 [버튼 설정] 탭의 [현재 폰 화면의 버튼 읽기]를 눌러.
   목록에서 포토 어시스트 버튼을 선택하고 [포토 어시스트 지정]을 눌러.
4. 폰에서 직접 포토 어시스트로 들어간 뒤 같은 방법으로 스타일 탭을 지정해줘.
5. 스타일 화면에서 목록을 읽어 각 스타일 버튼을 등록해줘.
   목록을 가로로 넘겨야 한다면 초기 목록부터 왼쪽으로 넘긴 횟수도 입력해줘.
   매번 더보기/분류 버튼을 눌러야 한다면 [목록 펼치기]로 지정할 수 있어.
6. 폰에서 테스트 사진에 스타일 하나를 직접 생성하고, 결과 화면의
   ‘다른 파일로 저장’ 버튼을 읽어서 저장 버튼으로 지정해줘.
   다른 화면에서는 같은 역할의 버튼 정보가 달라질 수 있어.

실행
사진 목록 → 폴더 → 원본 사진 선택 → 첫 사진·첫 스타일만 실행.
저장 위치와 원본 재진입이 맞는지 확인한 뒤 [새 작업]으로 모든 스타일을 실행해줘.
폰은 잠금을 해제하고 Photo Assist 생성에 필요한 인터넷 연결을 유지해줘.
실행 중에는 다른 앱 조작·촬영·사진 다운로드를 하지 마.

중지와 기록
중지는 다음 조작을 멈추는 기능이야. 서버에 보낸 생성 요청을 취소하지는 않아.
완료 여부가 불명확한 작업은 자동으로 다시 실행하지 않아.
[기록 폴더 열기]에서 버튼 설정·실행 이력·오류 기록을 확인할 수 있어.
기록은 PC에, 결과 사진은 원본과 같은 휴대전화 폴더에 남아.

현재 한계
실제 S26+에서 전체 실행은 아직 검증하지 않았어.
UI에 이름/설명이 없는 스타일, 세로 목록, 스타일별로 다른 분류 경로는 추가 조정이 필요해.
저장된 사진을 이전/이후 목록 차이로 연결하므로 다른 이미지 생성 작업을 병행하면 안 돼.
보조 자동화 프로그램은 폰에서 실행될 수 있어. 루팅·보안 차단 해제·원본 삭제는 하지 않아.
원본 보존 검사는 파일 경로·크기·수정시간 비교이며 파일 내용의 해시 비교는 아니야.
'''


def self_test():
    # Windows에서 패키징된 EXE의 모듈/자료/GUI 포함 여부를 검사한다. 폰에는 연결하지 않는다.
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
    # windowed EXE의 stdout/stderr는 None일 수 있어 외부 라이브러리 출력용 스트림을 둔다.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')
    if '--self-test' in sys.argv:
        self_test()
    else:
        # 두 창이 동시에 같은 폰을 조작하지 않도록 Windows 프로세스 잠금을 유지한다.
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
                messagebox.showinfo('이미 실행 중', '기존 Photo Assist 창을 사용해줘.')
                root.destroy()
                sys.exit(1)
        App().mainloop()
