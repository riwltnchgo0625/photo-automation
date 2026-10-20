"""사용 화면만 담당한다. 휴대전화 작업 로직은 automation.py에 둔다."""
# 이 파일은 PC 창을 담당하고, 폰 조작은 automation.py에 맡깁니다.
# 읽는 순서: build_ui(화면 구성) → start(실행 버튼) → automation.py의 execute(폰 조작).
# 자주 바꿀 부분: 창 크기는 __init__, 버튼 글자는 build_ui, 사진 정렬은 show_album입니다.
# 폰에서 누를 버튼/스타일은 GUI에서 등록합니다. 화면 버튼의 글자를 바꾼다고 폰 버튼이 바뀌지는 않습니다.
# #으로 시작하는 줄은 설명입니다. 들여쓰기된 코드의 위치를 바꾸면 실행 흐름이 바뀔 수 있습니다.
from __future__ import annotations

# copy: 실행 시 설정을 별도로 복사하여 진행 중 계획이 바뀌지 않게 합니다.
import copy
# json: 버튼 설정과 진행 기록을 사람이 읽을 수 있는 JSON 파일로 저장/복원합니다.
import json
# os: Windows 환경변수와 기록 폴더 열기 등 운영체제 기능을 사용합니다.
import os
# Path는 PC 파일 경로, PurePosixPath는 /로 구분되는 Android 파일 경로를 다룹니다.
from pathlib import Path, PurePosixPath
# queue: 작업 스레드에서 화면으로 결과를 안전하게 전달합니다.
import queue
# sys: Windows 여부, 실행 인수(--self-test), 출력 스트림을 확인합니다.
import sys
# threading: 창을 멈추지 않고 폰 작업을 수행하거나 중지 신호를 전달합니다.
import threading
# time: 대기 제한, 실행 소요시간, 기록 시각을 계산합니다.
import time
# traceback: 오류가 난 코드 위치까지 errors.log에 남깁니다.
import traceback
# tkinter: PC 창·입력칸·목록을 만드는 기본 GUI 도구입니다. tk는 짧게 쓰는 별명입니다.
import tkinter as tk
# ttk는 버튼/탭 등의 화면 부품, messagebox는 확인/오류 팝업입니다.
from tkinter import ttk, messagebox

# 같은 폴더의 automation.py에서 필요한 기능을 가져옵니다.
# 파일명/함수명을 바꾸면 이 import와 아래 호출 위치도 함께 수정해야 합니다.
from automation import Phone, Stopped, app_data, devices, execute, save_json, screen_candidates, validate


# Tkinter는 Python의 PC 창 도구입니다. App은 창과 그 창의 상태를 묶은 클래스입니다.
# self.이름은 이 창이 계속 기억할 데이터 또는 화면 부품입니다.
class App(tk.Tk):
    # python app.py로 창을 만들 때 한 번 실행되는 초기 준비입니다.
    def __init__(self):
        super().__init__()
        self.title('Photo Assist · 스타일 결과 수집')
        # 처음 열릴 창 크기(가로x세로)입니다. 화면 공간만 바뀌고 폰 해상도에는 영향이 없습니다.
        self.geometry('980x790')
        # 사용자가 줄일 수 있는 최소 창 크기입니다. 너무 작게 하면 버튼이 가려질 수 있습니다.
        self.minsize(850, 700)
        self.store = app_data()
        self.profile_path = self.store / 'buttons.json'
        # 설정 기본값: assist=포토어시스트, style_tab=스타일 탭, save=새 파일 저장,
        # more=선택적 목록 펼치기, styles=순서대로 실행할 스타일 목록입니다.
        # None은 미지정입니다. 기존 buttons.json이 있으면 아래에서 그 값으로 덮어씁니다.
        # 기본값만 수정해도 이미 저장된 사용자의 설정이 자동으로 바뀌지는 않습니다.
        # generate=생성 버튼. 예전 설정은 유지하고 이 버튼만 추가 등록합니다.
        self.profile = {'assist': None, 'style_tab': None, 'generate': None, 'save': None, 'more': None, 'styles': []}
        try:
            if self.profile_path.exists():
                self.profile.update(json.loads(self.profile_path.read_text(encoding='utf-8')))
        except Exception as e:
            messagebox.showerror('설정 읽기 실패', str(e))
        # photos=조회한 전체 사진, shown=선택한 폴더에서 화면에 표시할 사진,
        # candidates=현재 폰 화면에서 읽은 버튼 후보입니다.
        self.photos, self.shown, self.candidates = {}, [], []
        # 느린 폰 작업과 화면 갱신 사이에 메시지를 전달하는 대기열입니다.
        # Tkinter 화면은 주 스레드에서 갱신해야 하므로 작업 스레드에서 직접 수정하지 않습니다.
        self.events = queue.Queue()
        # 중지 요청을 공유하는 신호입니다. 버튼을 누르면 설정되고 새 작업 시작 시 해제됩니다.
        self.stop = threading.Event()
        self.busy = False
        self.buttons = []
        # StringVar는 값이 바뀌면 연결된 화면 표시도 바뀌는 Tkinter 변수입니다.
        self.serial = tk.StringVar()
        self.album = tk.StringVar()
        self.status = tk.StringVar(value='USB 연결 후 [기기 찾기]를 눌러줘.')
        self.build_ui()
        self.refresh_profile()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    # 공통 버튼 생성 도우미입니다. 생성한 버튼을 기억해 작업 중 비활성화합니다.
    # command는 클릭 시 실행할 함수이며 여기서 함수 뒤에 ()를 붙이면 즉시 실행될 수 있습니다.
    def button(self, parent, text, command, **pack):
        b = ttk.Button(parent, text=text, command=command)
        b.pack(**pack)
        self.buttons.append(b)
        return b

    # 화면 배치와 버튼 역할을 정의합니다. text는 표시 문구, command는 실제 동작입니다.
    # padx/pady는 여백, width/height는 부품 크기이며 폰 조작 좌표와 무관합니다.
    # pack은 배치 방식입니다. 같은 영역의 배치 방식을 섞으면 창 구성이 깨질 수 있습니다.
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
        # Notebook은 탭 묶음입니다. 실행/설정/도움말의 이름만 바꾸려면 text를 수정합니다.
        notebook.add(run, text='  실행  ')
        notebook.add(settings, text='  처음 한 번 · 버튼 설정  ')
        notebook.add(help_tab, text='  사용 방법  ')

        self.button(run, '1. 사진 목록 불러오기', self.load_photos, anchor='w')
        self.album_box = ttk.Combobox(run, textvariable=self.album, state='readonly')
        self.album_box.pack(fill='x', pady=8)
        self.album_box.bind('<<ComboboxSelected>>', lambda _: self.show_album())
        ttk.Label(run, text='2. 편집할 원본만 선택해줘. Ctrl / Shift로 여러 장 선택할 수 있어.').pack(anchor='w')
        # extended는 Ctrl/Shift 다중 선택을 허용합니다.
        # exportselection=False는 다른 목록을 눌러도 원본 사진 선택을 유지하도록 합니다.
        self.photo_list = tk.Listbox(run, selectmode='extended', exportselection=False, height=9)
        self.photo_list.pack(fill='both', expand=True, pady=6)
        self.style_summary = tk.StringVar()
        ttk.Label(run, textvariable=self.style_summary).pack(anchor='w', pady=6)
        controls = ttk.Frame(run)
        controls.pack(fill='x')
        self.button(controls, '선택한 첫 사진 · 첫 스타일만 실행', lambda: self.start(True), side='left', padx=(0, 8))
        self.button(controls, '3. 선택한 원본 × 모든 등록 스타일 실행', lambda: self.start(False), side='left')
        # 중지는 공통 buttons 목록에 넣지 않습니다. 작업 중에도 누를 수 있어야 합니다.
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
        # key는 내부 설정 키, label은 화면의 이름입니다.
        # lambda k=key는 각 버튼에 해당 키를 고정합니다. 없애면 모두 마지막 키를 가리킬 수 있습니다.
        for key, label in [('assist', '포토 어시스트'), ('style_tab', '스타일 탭'), ('generate', '생성 버튼'), ('save', '다른 파일로 저장')]:
            self.button(assignments, label + ' 지정', lambda k=key: self.assign(k), side='left', padx=(0, 5))
        # 선택 기능은 다음 줄에 배치해 작은 창에서도 버튼이 잘리지 않게 합니다.
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
        # 0~20은 가로로 넘길 횟수의 입력 범위입니다.
        # 범위를 바꾸려면 add_style 및 automation.py의 validate 검사도 함께 맞춰야 합니다.
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
        # 맨 아래 진행 기록 영역입니다. height는 표시 줄 수이며 저장 이력과 별개입니다.
        self.log = tk.Text(self, height=7, state='disabled', wrap='word')
        self.log.pack(fill='x', padx=16, pady=12)

    # 상태 문구와 하단 로그에 같은 메시지를 표시합니다. 시각은 이 PC의 현재 시간입니다.
    # 여기 표시한 로그 전체를 파일로 저장하지는 않으며 오류 상세만 errors.log에 저장합니다.
    def say(self, text):
        self.status.set(text)
        self.log.configure(state='normal')
        self.log.insert('end', time.strftime('%H:%M:%S ') + text + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    # 오래 걸리는 폰 통신을 별도 스레드에서 실행하여 PC 창이 굳지 않게 합니다.
    # 작업 중에는 설정/실행 버튼을 막고 중지 버튼만 사용할 수 있게 합니다.
    # func=실제 작업 함수, callback=성공한 결과를 화면에 반영할 함수입니다.
    def job(self, func, callback=None):
        if self.busy:
            return
        self.busy = True
        self.stop.clear()
        for button in self.buttons:
            button.configure(state='disabled')
        self.device_box.configure(state='disabled')
        self.album_box.configure(state='disabled')

        # 작업 스레드는 폰 통신을 실행하고 결과·오류를 대기열에 넣습니다.
        # 여기서 화면 부품을 직접 변경하면 불안정해질 수 있으므로 poll에서 처리합니다.
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

    # 주 스레드가 대기열을 읽어 화면에 반영합니다.
    # after(100, ...)은 100밀리초 뒤 다시 확인한다는 뜻이며 AI 생성 대기시간이 아닙니다.
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

    # 선택된 기기 번호로 Phone 객체를 만듭니다. 기기가 없으면 실행 대신 안내합니다.
    def phone(self):
        serial = self.serial.get()
        if not serial:
            raise ValueError('기기를 먼저 선택해줘.')
        return Phone(serial)

    # 기기 찾기 버튼의 동작입니다. 승인된 기기만 선택 상자에 넣습니다.
    # 첫 기기를 기본 선택하므로 여러 대 연결했다면 사용자가 대상을 확인해야 합니다.
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

    # 이전 폰의 사진 ID를 다른 폰에 사용하지 않도록 화면의 사진 데이터를 초기화합니다.
    # 폰의 실제 사진을 삭제하는 함수가 아닙니다.
    def clear_photos(self):
        # photos=조회한 전체 사진, shown=선택한 폴더에서 화면에 표시할 사진,
        # candidates=현재 폰 화면에서 읽은 버튼 후보입니다.
        self.photos, self.shown = {}, []
        self.photo_list.delete(0, 'end')
        self.album.set('')
        self.album_box['values'] = []

    # 폰 사진 정보를 가져와 부모 폴더별 목록을 만듭니다. 사진 파일 다운로드는 하지 않습니다.
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

    # 선택한 폴더의 사진만 표시하고 경로(같은 폴더에서는 파일명) 문자순으로 정렬합니다.
    # key=lambda p: p['path']가 순서를 결정합니다. 날짜순으로 바꾸려면 날짜 정보와 원하는 기준이 필요합니다.
    # 현재 modified는 촬영일이 아니라 파일 수정시간입니다. 둘을 같은 뜻으로 쓰지 마세요.
    def show_album(self):
        self.shown = sorted([p for p in self.photos.values()
                             if str(PurePosixPath(p['path']).parent) == self.album.get()], key=lambda p: p['path'])
        self.photo_list.delete(0, 'end')
        for p in self.shown:
            self.photo_list.insert('end', f"{PurePosixPath(p['path']).name}  · ID {p['id']}")

    # 현재 폰 화면의 요소를 가져와 설정 후보 목록에 표시합니다.
    # 폰에서 화면을 바꾼 뒤 다시 눌러야 합니다. 읽기 자체는 버튼을 누르지 않습니다.
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

    # 위쪽 버튼 후보 목록에서 선택한 한 항목을 가져옵니다. 미선택이면 안내합니다.
    def selected_candidate(self):
        selection = self.candidate_list.curselection()
        if not selection:
            raise ValueError('현재 화면 목록에서 버튼 하나를 선택해줘.')
        return self.candidates[selection[0]]

    # 후보를 포토어시스트/스타일/저장 등 역할에 연결하고 즉시 설정 파일에 저장합니다.
    # clear=True는 목록 펼치기 설정만 비우는 데 사용합니다.
    def assign(self, key, clear=False):
        try:
            self.profile[key] = None if clear else self.selected_candidate()['selector']
            self.refresh_profile(save=True)
        except ValueError as e:
            self.say(str(e))

    # 현재 설정을 창의 요약 문구와 등록 스타일 목록에 다시 표시합니다.
    # save=True일 때만 buttons.json에 기록합니다. 사진이나 진행 이력을 바꾸지 않습니다.
    def refresh_profile(self, save=False):
        if save:
            save_json(self.profile_path, self.profile)
        labels = [('assist', '포토 어시스트'), ('style_tab', '스타일'), ('generate', '생성'), ('save', '저장'), ('more', '목록 펼치기')]
        self.mapping.set(' / '.join(name + ': ' + (self.profile.get(k) or {}).get('value', '미지정') for k, name in labels))
        self.style_list.delete(0, 'end')
        for s in self.profile['styles']:
            self.style_list.insert('end', f"{s['name']} · 왼쪽 이동 {s.get('swipes', 0)}회")
        self.style_summary.set(f"등록된 스타일 {len(self.profile['styles'])}개 · 추가/변경은 [버튼 설정] 탭")

    # 선택한 후보를 처리할 스타일로 추가합니다. 추가 순서가 실제 실행 순서입니다.
    # 같은 이름을 허용하지 않는 이유는 완료 기록에서 스타일 이름을 조합 식별에 쓰기 때문입니다.
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
            # 선택 요소의 세로 중심을 화면 높이에 대한 비율로 저장합니다.
            # 예: 0.8이면 화면 위에서 80% 높이에서 가로로 넘깁니다.
            # 폰 화면 배치가 바뀌면 스타일을 다시 등록해 높이도 갱신해야 할 수 있습니다.
            y = max(.01, min(.99, (y1 + y2) / 2 / self.screen_size[1]))
            self.profile['styles'].append({'name': name, 'selector': item['selector'], 'swipes': count, 'swipe_y': y})
            self.refresh_profile(save=True)
            self.style_name.delete(0, 'end')
        except ValueError as e:
            self.say(str(e))

    # 등록 목록에서 선택한 스타일만 제거합니다. 폰의 스타일 기능이나 결과 사진은 삭제하지 않습니다.
    def remove_style(self):
        selection = self.style_list.curselection()
        if selection:
            del self.profile['styles'][selection[0]]
            self.refresh_profile(save=True)

    # 두 실행 버튼의 공통 진입점입니다. first_only=True이면 한 장/한 스타일 시험입니다.
    # 선택과 설정을 복사해 실행 도중 화면 선택이 바뀌어도 진행 중 계획은 고정합니다.
    def start(self, first_only):
        try:
            phone = self.phone()
            # curselection()은 선택한 순서가 아닌 목록 인덱스 순서를 돌려줍니다.
            # 따라서 사진은 화면의 정렬 순서대로 처리됩니다. 원본만 선택했는지 확인해야 합니다.
            originals = [self.shown[i].copy() for i in self.photo_list.curselection()]
            profile = copy.deepcopy(self.profile)
            # [:1]은 첫 항목 하나만 남기는 Python 문법입니다.
            # 이 시험도 실제 생성/저장을 수행합니다. 모의 실행이 아닙니다.
            if first_only:
                originals, profile['styles'] = originals[:1], profile['styles'][:1]
            validate(profile, originals)
        except ValueError as e:
            self.say(str(e)); return
        emit = lambda text: self.events.put(('log', text))
        self.job(lambda: execute(phone, profile, originals, self.stop, emit, self.store))

    # stop 신호만 설정합니다. 연결을 강제로 끊거나 이미 보낸 생성 요청을 취소하지 않습니다.
    def request_stop(self):
        if self.busy:
            self.stop.set()
            self.say('중지 요청됨. 진행 중인 통신이 끝나면 다음 클릭 전에 멈춰. 이미 시작한 생성은 취소하지 않아.')

    # 진행 이력을 이름을 바꿔 보관하고 새 계획으로 실행할 수 있게 합니다.
    # 이미 만든 결과 사진은 남아 있어 같은 조합을 다시 실행하면 중복 생성될 수 있습니다.
    # 버튼 설정은 초기화하지 않습니다.
    def new_job(self):
        path = self.store / 'progress.json'
        if path.exists():
            if not messagebox.askyesno('새 작업', '기존 완료 이력을 보관하고 새 작업을 시작할까?\n이미 저장된 사진은 남아 있어. 같은 사진·스타일을 고르면 중복 생성될 수 있어.'):
                return
            path.rename(self.store / f'progress_{time.time_ns()}.json')
        self.say('새 작업을 준비했어. 다음 실행에서 사용할 원본을 선택해줘.')

    # PC의 설정/진행/오류 파일 폴더를 엽니다. 폰 갤러리 결과 폴더를 여는 기능은 아닙니다.
    def open_records(self):
        if sys.platform == 'win32':
            os.startfile(self.store)
        else:
            self.say(str(self.store))

    # 작업 중 닫기를 누르면 우선 중지를 요청합니다.
    # 작업 종료 후 다시 닫아야 창이 종료되며, 이를 없애면 저장 확인 도중 종료될 수 있습니다.
    def close(self):
        if self.busy:
            self.request_stop()
            return
        self.destroy()


# '사용 방법' 탭에 표시하는 안내문입니다. 여기 글을 바꿔도 자동화 로직은 바뀌지 않습니다.
# 동작을 변경했다면 이 안내문과 USER_GUIDE도 함께 수정해서 설명을 맞춰 주세요.
HELP = '''처음 한 번
1. 폰에서 USB 디버깅을 켜고 PC 연결을 허용해줘.
2. [기기 찾기]로 연결할 폰을 선택해줘.
3. 폰에서 원본 사진을 직접 열고 [버튼 설정] 탭의 [현재 폰 화면의 버튼 읽기]를 눌러.
   목록에서 포토 어시스트 버튼을 선택하고 [포토 어시스트 지정]을 눌러.
4. 폰에서 직접 포토 어시스트로 들어간 뒤 같은 방법으로 스타일 탭을 지정해줘.
5. 스타일 화면에서 목록을 읽어 각 스타일 버튼을 등록해줘.
   목록을 가로로 넘겨야 한다면 초기 목록부터 왼쪽으로 넘긴 횟수도 입력해줘.
   매번 더보기/분류 버튼을 눌러야 한다면 [목록 펼치기]로 지정할 수 있어.
6. 폰에서 스타일 하나를 선택한 뒤 화면의 버튼을 다시 읽어.
   ‘생성’ 항목을 선택하고 [생성 버튼 지정]을 눌러줘.
7. 폰에서 직접 [생성]을 누르고 기다린 다음, 결과 화면의
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


# 개발/EXE 제작 때 사용하는 검사입니다. 포함된 라이브러리와 자료, 창 생성만 확인합니다.
# 폰에 연결해 Photo Assist를 끝까지 실행하는 실기기 테스트는 아닙니다.
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


# 이 파일을 python app.py로 직접 실행할 때만 아래를 수행합니다.
# 일반 실행은 App().mainloop()로 창을 유지하며 사용자 클릭을 기다립니다.
# --self-test 인수가 있으면 배포 검사만 수행합니다. 평소 실행에는 붙이지 않습니다.
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
