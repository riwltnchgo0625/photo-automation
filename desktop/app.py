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


# 디자인 수정: 색상은 THEME, 글꼴·여백·창 크기는 아래 값 변경.
THEME = {
    'background': '#0C0C0C',
    'card': '#191919',
    'input': '#111111',
    'hero': '#2C2C2C',
    'accent': '#ffffff',
    'hover': '#ffffff',
    'text': '#ffffff',
    'muted': '#ffffff',
    'border': '#333333',
    'disabled': '#737373',
}
FONT_FAMILY = '맑은 고딕'
FONT_SIZE = 10
PAGE_PAD = 20
WINDOW_SIZE = '1240x940'
CARD_RADIUS = 22


# 둥근 카드: 테두리는 Canvas, 내부 배치는 Frame에서 처리.
class Card(tk.Canvas):
    def __init__(self, parent, color=None, scrollable=False, **kwargs):
        self.color = color or THEME['card']
        super().__init__(parent, background=THEME['background'], highlightthickness=0, **kwargs)
        self.body = tk.Frame(self, background=self.color)
        self.window = self.create_window(18, 16, anchor='nw', window=self.body)
        self.bind('<Configure>', self.redraw)
        if scrollable:
            # 카드 내부만 스크롤. 본문 전체는 창 크기에 맞춰 고정.
            host = self.body
            self.scroller = tk.Canvas(host, bg=self.color, highlightthickness=0)
            self.scrollbar = ttk.Scrollbar(host, command=self.scroller.yview)
            self.scrollbar.pack(side='right', fill='y', padx=(14, 0))
            self.scroller.pack(side='left', fill='both', expand=True)
            self.scroller.configure(yscrollcommand=self.scrollbar.set)
            self.body = tk.Frame(self.scroller, bg=self.color)
            inner = self.scroller.create_window(0, 0, anchor='nw', window=self.body)
            def fit(event=None):
                width = self.scroller.winfo_width()
                height = max(self.scroller.winfo_height(), self.body.winfo_reqheight(), 440)
                self.scroller.itemconfigure(inner, width=width, height=height)
                self.scroller.configure(scrollregion=(0, 0, width, height))
            self.scroller.bind('<Configure>', fit)
            self.body.bind('<Configure>', fit)


    def redraw(self, event):
        w, h, r = event.width-1, event.height-1, CARD_RADIUS
        r = min(r, w/2, h/2)
        self.delete('shape')
        self.create_polygon(r, 1, w-r, 1, w, 1, w, r, w, h-r, w, h,
                            w-r, h, r, h, 1, h, 1, h-r, 1, r, 1, 1,
                            smooth=True, splinesteps=24, fill=self.color,
                            outline=THEME['border'], tags='shape')
        self.tag_lower('shape')
        self.itemconfigure(self.window, width=max(1,w-36), height=max(1,h-32))


class App(tk.Tk):
    def __init__(self, prompt_mode=False):
        super().__init__()
        self.prompt_mode = prompt_mode
        self.title('Photo Assist · ' + ('만들기' if prompt_mode else '스타일') + ' 결과 수집')
        # 창 크기: 가로 × 세로. 최소 크기는 아래 minsize에서 변경.
        width, height = map(int, WINDOW_SIZE.split('x'))
        self.minsize(1000, 640)
        self.geometry(f'{min(width, self.winfo_screenwidth()-40)}x{min(height, self.winfo_screenheight()-80)}')
        self.store = app_data()
        if prompt_mode:
            self.store = self.store / 'prompt'
            self.store.mkdir(exist_ok=True)
        self.profile_path = self.store / 'buttons.json'
        # 버튼·스타일 기본값. 기존 buttons.json이 있으면 저장된 설정을 불러오기.
        self.profile = {'assist': None, 'style_tab': None, 'generate': None, 'save': None, 'more': None, 'styles': []}
        try:
            if self.profile_path.exists():
                self.profile.update(json.loads(self.profile_path.read_text(encoding='utf-8')))
        except Exception as e:
            messagebox.showerror('설정 읽기 실패', str(e))
        if prompt_mode:
            self.profile['mode'] = 'prompt'
        self.photos, self.shown, self.candidates = {}, [], []
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.busy = False
        self.buttons = []
        self.serial = tk.StringVar()
        self.album = tk.StringVar()
        self.status = tk.StringVar(value=self.wording('[02 버튼 · 스타일]에서 기기 찾기 및 연결 기기 선택.'))
        self.setup_theme()
        self.build_ui()
        self.style_text_widgets(self)
        self.refresh_profile()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    # ttk 색상·상태별 스타일 설정. 자동화 동작과 분리.
    def setup_theme(self):
        c = THEME
        self.configure(background=c['background'])
        self.option_add('*Font', (FONT_FAMILY, FONT_SIZE))
        self.option_add('*TCombobox*Listbox.background', c['input'])
        self.option_add('*TCombobox*Listbox.foreground', c['text'])
        self.option_add('*TCombobox*Listbox.selectBackground', c['accent'])
        self.option_add('*TCombobox*Listbox.selectForeground', c['background'])
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=(FONT_FAMILY, FONT_SIZE), background=c['card'], foreground=c['text'])
        style.configure('TFrame', background=c['card'])
        style.configure('Page.TFrame', background=c['background'])
        style.configure('Hero.TFrame', background=c['hero'])
        style.configure('TLabel', background=c['card'], foreground=c['text'])
        style.configure('Hero.TLabel', background=c['hero'], foreground=c['muted'])
        style.configure('Title.TLabel', background=c['hero'], foreground=c['text'], font=(FONT_FAMILY, 24, 'bold'))
        style.configure('Muted.TLabel', foreground=c['muted'])
        style.configure('TButton', background=c['input'], foreground=c['text'],
                        bordercolor=c['border'], lightcolor=c['input'], darkcolor=c['input'], borderwidth=0, padding=(12, 8), relief='flat')
        style.map('TButton', background=[('disabled', c['card']), ('active', c['hero'])],
                  foreground=[('disabled', c['disabled'])])
        style.configure('Accent.TButton', background=c['accent'], foreground=c['background'], font=(FONT_FAMILY, FONT_SIZE, 'bold'))
        style.configure('Vertical.TScrollbar', background=c['border'], troughcolor=c['background'],
                        arrowcolor=c['muted'], borderwidth=0, arrowsize=12)
        style.map('Accent.TButton', background=[('disabled', c['card']), ('active', c['hover'])],
                  foreground=[('disabled', c['disabled']), ('!disabled', c['background'])])
        for name in ('TEntry', 'TCombobox', 'TSpinbox'):
            style.configure(name, fieldbackground=c['input'], background=c['input'],
                            foreground=c['text'], arrowcolor=c['muted'], bordercolor=c['border'], lightcolor=c['border'], darkcolor=c['border'], padding=6)
            style.map(name, fieldbackground=[('disabled', c['card']), ('readonly', c['input'])],
                      foreground=[('disabled', c['disabled']), ('readonly', c['text'])],
                      selectbackground=[('!disabled', c['accent'])], selectforeground=[('!disabled', c['background'])])

    # tk 목록·도움말·로그 색상 적용. ttk 스타일과 별도 설정.
    def style_text_widgets(self, parent):
        for widget in parent.winfo_children():
            if isinstance(widget, (tk.Listbox, tk.Text)):
                widget.configure(background=THEME['input'], foreground=THEME['text'],
                                 selectbackground=THEME['accent'], selectforeground=THEME['background'],
                                 borderwidth=0, highlightthickness=1,
                                 highlightbackground=THEME['border'], highlightcolor=THEME['accent'],
                                 font=(FONT_FAMILY, FONT_SIZE))
                if isinstance(widget, tk.Text):
                    widget.configure(insertbackground=THEME['text'], padx=12, pady=10)
                else:
                    widget.configure(disabledforeground=THEME['disabled'])
            self.style_text_widgets(widget)

    def wording(self, text):
        return text.replace('스타일', '프롬프트') if self.prompt_mode else text

    def button(self, parent, text, command, style='TButton', **pack):
        b = ttk.Button(parent, text=self.wording(text), command=command, style=style)
        b.pack(**pack)
        self.buttons.append(b)
        return b

    # 화면 구성: 왼쪽 메뉴 / 상단 현황 / 본문 / 실행 기록.
    def build_ui(self):
        c = THEME
        self.scroll_targets = {}
        def label(parent, text='', size=10, color=None, bold=False, **kw):
            return tk.Label(parent, text=self.wording(text), bg=parent.cget('background'),
                            fg=color or c['text'], font=(FONT_FAMILY, size, 'bold' if bold else 'normal'), **kw)
        def section(parent, eyebrow, title):
            label(parent, eyebrow, 9, c['accent'], True).pack(anchor='w')
            label(parent, title, 16, bold=True).pack(anchor='w', pady=(5, 14))
        def listing(parent, height=5, multiple=False):
            area = tk.Frame(parent, bg=c['input'])
            area.pack(fill='both', expand=True, pady=(8, 10))
            box = tk.Listbox(area, height=height, selectmode='extended' if multiple else 'browse',
                             exportselection=False, activestyle='none')
            bar = ttk.Scrollbar(area, command=box.yview)
            box.configure(yscrollcommand=bar.set)
            self.scroll_targets[box] = box
            self.scroll_targets[bar] = box
            bar.pack(side='right', fill='y')
            box.pack(side='left', fill='both', expand=True)
            return box

        sidebar = tk.Frame(self, bg='#101010', width=185)
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)
        label(sidebar, 'PA', 26, c['accent'], True).pack(anchor='w', padx=26, pady=(30, 2))
        label(sidebar, 'PHOTO ASSIST', 11, bold=True).pack(anchor='w', padx=26)
        label(sidebar, 'QA WORKSPACE', 8, c['muted']).pack(anchor='w', padx=26, pady=(6, 35))
        self.nav_buttons = {}
        for key, title in [('run', '01   결과 수집'), ('settings', '02   버튼 · 스타일'), ('help', '03   사용 가이드')]:
            b = tk.Button(sidebar, text=self.wording(title), anchor='w', relief='flat', bd=0,
                          bg='#101010', fg=c['muted'], activebackground=c['hero'], activeforeground=c['text'],
                          font=(FONT_FAMILY, 10), padx=16, pady=14, cursor='hand2', highlightthickness=0,
                          command=lambda k=key: self.show_page(k))
            b.pack(fill='x', padx=12, pady=3)
            self.nav_buttons[key] = b
        label(sidebar, 'LOCAL AUTOMATION', 8, c['muted']).pack(side='bottom', anchor='w', padx=24, pady=(0, 26))
        label(sidebar, '원본 보존 · 결과 수집', 9, c['muted']).pack(side='bottom', anchor='w', padx=24, pady=8)

        main = tk.Frame(self, bg=c['background'])
        main.pack(side='left', fill='both', expand=True, padx=PAGE_PAD, pady=PAGE_PAD)
        header = tk.Frame(main, bg=c['background'])
        header.pack(fill='x', pady=(0, 10))
        label(header, 'PHOTO ASSIST', 9, c['accent'], True).pack(anchor='w')
        label(header, '만들기' if self.prompt_mode else '스타일', 30, bold=True).pack(anchor='w', pady=(3, 0))
        label(header, textvariable=self.status, size=10, color=c['muted']).pack(anchor='w', pady=(4, 0))

        stats = tk.Frame(sidebar, bg='#101010')
        stats.pack(fill='x', padx=24, pady=(24, 0))
        label(stats, '작업 현황', 9, c['muted']).pack(anchor='w', pady=(0, 8))
        self.selected_count = tk.StringVar(value='0')
        self.registered_count = tk.StringVar(value='0')
        self.combination_count = tk.StringVar(value='0')
        for title, var in [('선택한 원본', self.selected_count),
                           ('등록된 스타일', self.registered_count),
                           ('예정 조합', self.combination_count)]:
            row = tk.Frame(stats, bg='#101010')
            row.pack(fill='x', pady=5)
            label(row, title, 9, c['muted']).pack(side='left')
            label(row, textvariable=var, size=15, bold=True).pack(side='right')

        activity = Card(main,height=115); activity.pack(side='bottom',fill='x',pady=(14,0)); activity.pack_propagate(False)
        line = tk.Frame(activity.body,bg=c['card']); line.pack(fill='x')
        label(line,'ACTIVITY / 실행 기록',9,c['muted'],True).pack(side='left')
        self.button(line,'기록 폴더',self.open_records,side='right')
        self.log = tk.Text(activity.body,height=2,state='disabled',wrap='word'); self.log.pack(fill='both',expand=True,pady=(6,0))

        # 전체 페이지 스크롤 없이 남은 창 영역 사용.
        deck = tk.Frame(main, bg=c['background'])
        deck.pack(fill='both', expand=True)
        deck.rowconfigure(0, weight=1); deck.columnconfigure(0, weight=1)
        self.pages = {}
        for key in ('run', 'settings', 'help'):
            page = tk.Frame(deck, bg=c['background'])
            page.grid(row=0, column=0, sticky='nsew')
            self.pages[key] = page

        run = self.pages['run']
        run.rowconfigure(0, weight=1); run.columnconfigure(0, weight=3); run.columnconfigure(1, weight=2)
        photos = Card(run)
        photos.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        section(photos.body, '01 / SOURCE', '원본 사진')
        self.button(photos.body, '사진 목록 불러오기', self.load_photos, fill='x')
        self.album_box = ttk.Combobox(photos.body, textvariable=self.album, state='readonly', width=18)
        self.album_box.pack(fill='x', pady=(12, 0))
        self.album_box.bind('<<ComboboxSelected>>', lambda _: self.show_album())
        self.photo_list = listing(photos.body, multiple=True)
        self.photo_list.bind('<<ListboxSelect>>', lambda _: self.update_metrics())
        label(photos.body, 'Ctrl / Shift로 여러 장 선택 · 원본만 선택', 9, c['muted']).pack(anchor='w')

        control = Card(run)
        control.grid(row=0, column=1, sticky='nsew')
        section(control.body, '02 / EXECUTION', '작업 실행')
        self.style_summary = tk.StringVar()
        label(control.body, textvariable=self.style_summary, size=9, color=c['muted'], wraplength=285, justify='left').pack(anchor='w', pady=14)
        self.button(control.body, '첫 사진 · 첫 스타일 시험', lambda: self.start(True), fill='x', pady=(0, 8))
        self.button(control.body, '전체 수집 시작', lambda: self.start(False), style='Accent.TButton', fill='x')
        actions = tk.Frame(control.body, bg=c['card'])
        actions.pack(fill='x', pady=(10, 0))
        ttk.Button(actions, text='작업 중지', command=self.request_stop).pack(side='left', fill='x', expand=True, padx=(0, 4))
        self.button(actions, '새 작업', self.new_job, side='left', fill='x', expand=True, padx=(4, 0))

        settings = self.pages['settings']
        settings.rowconfigure(1, weight=1); settings.columnconfigure(0, weight=1); settings.columnconfigure(1, weight=1)
        connection = Card(settings, height=78)
        connection.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 12))
        device_row = tk.Frame(connection.body, bg=c['card'])
        device_row.pack(fill='both', expand=True)
        label(device_row, '연결 기기', 10, bold=True).pack(side='left', padx=(0, 14))
        self.device_box = ttk.Combobox(device_row, textvariable=self.serial, state='readonly', width=15)
        self.device_box.pack(side='left', fill='x', expand=True, padx=(0, 12))
        self.device_box.bind('<<ComboboxSelected>>', lambda _: self.clear_photos())
        self.button(device_row, '기기 찾기', self.find_devices, side='right')
        mapping = Card(settings, scrollable=True); mapping.grid(row=1, column=0, sticky='nsew', padx=(0, 12))
        section(mapping.body, '01 / MAPPING', '화면 버튼 등록')
        self.button(mapping.body, '현재 폰 화면의 버튼 읽기', self.read_screen, fill='x')
        self.candidate_list = listing(mapping.body, height=3)
        assignments = tk.Frame(mapping.body, bg=c['card']); assignments.pack(fill='x')
        for i, (key, title) in enumerate([('assist','포토 어시스트'),('style_tab','만들기 탭' if self.prompt_mode else '스타일 탭'),('generate','생성 버튼'),('save','다른 파일로 저장')]):
            b = ttk.Button(assignments, text=title+' 지정', command=lambda k=key: self.assign(k))
            b.grid(row=i//2, column=i%2, sticky='ew', padx=2, pady=3)
            self.buttons.append(b)
        assignments.columnconfigure(0,weight=1); assignments.columnconfigure(1,weight=1)
        self.mapping = tk.StringVar()
        label(mapping.body, textvariable=self.mapping, size=9, color=c['muted'], wraplength=350, justify='left').pack(anchor='w', pady=(10,0))
        styles = Card(settings, scrollable=True); styles.grid(row=1, column=1, sticky='nsew')
        section(styles.body, '02 / PROMPTS' if self.prompt_mode else '02 / STYLES', '스타일 구성')
        label(styles.body, '프롬프트 문구 · 총 3개' if self.prompt_mode else '스타일 이름', 9, c['muted']).pack(anchor='w')
        self.style_name = ttk.Entry(styles.body); self.style_name.pack(fill='x', pady=(5,8))
        if self.prompt_mode:
            self.button(styles.body, '프롬프트 추가', self.add_style, style='Accent.TButton', fill='x')
        else:
            row = tk.Frame(styles.body,bg=c['card']); row.pack(fill='x')
            label(row, '왼쪽 이동 횟수 · 처음 기준',9,c['muted']).pack(side='left')
            self.swipes = ttk.Spinbox(row,from_=0,to=20,width=4); self.swipes.set('0'); self.swipes.pack(side='right')
            self.button(styles.body,'선택한 버튼을 스타일로 추가',self.add_style,style='Accent.TButton',fill='x',pady=(10,0))
        self.style_list = listing(styles.body,height=2)
        self.button(styles.body,'선택한 스타일 삭제',self.remove_style,fill='x')
        optional = tk.Frame(styles.body,bg=c['card']); optional.pack(fill='x',pady=(8,0))
        if self.prompt_mode:
            self.button(optional, '입력칸 지정', lambda: self.assign('prompt_input'), side='left')
        else:
            self.button(optional,'목록 펼치기 지정',lambda:self.assign('more'),side='left')
            self.button(optional,'해제',lambda:self.assign('more',clear=True),side='right')

        help_card = Card(self.pages['help']); help_card.pack(fill='both',expand=True)
        section(help_card.body,'QUICK GUIDE','사용 가이드')
        text = tk.Text(help_card.body,wrap='word',height=5)
        bar = ttk.Scrollbar(help_card.body,command=text.yview); bar.pack(side='right',fill='y')
        text.configure(yscrollcommand=bar.set); text.pack(fill='both',expand=True)
        text.insert('1.0',PROMPT_HELP if self.prompt_mode else HELP); text.configure(state='disabled')

        self.scroll_targets[text] = text
        self.scroll_targets[bar] = text
        self.scroll_targets[self.log] = self.log
        for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            self.bind_class('PhotoAssistWheel', sequence, self.on_wheel)
        for card in (mapping, styles):
            self.scroll_targets[card.scroller] = card.scroller
            self.scroll_targets[card.scrollbar] = card.scroller
        self.install_wheel(deck)
        self.install_wheel(self.log)
        self.show_page('run')

    # 기본 위젯 처리보다 먼저 휠 처리. 목록 끝에서는 해당 카드 내부로 이동.
    def install_wheel(self, widget):
        tag = 'PhotoAssistWheel'
        widget.bindtags((tag,) + widget.bindtags())
        for child in widget.winfo_children():
            self.install_wheel(child)

    def on_wheel(self, event):
        if getattr(event, 'num', None) in (4, 5):
            units = -3 if event.num == 4 else 3
        else:
            delta = getattr(event, 'delta', 0)
            if not delta:
                return 'break'
            units = (-1 if delta > 0 else 1) * max(1, abs(int(delta / 120))) * 3
        widget = event.widget
        targets = []
        while widget is not None:
            target = self.scroll_targets.get(widget)
            if target is not None and target not in targets:
                targets.append(target)
            widget = getattr(widget, 'master', None)
        for target in targets:
            first, last = target.yview()
            if (units < 0 and first > 0) or (units > 0 and last < 1):
                target.yview_scroll(units, 'units')
                break
        return 'break'

    def show_page(self, key):
        self.pages[key].tkraise()
        for name, button in self.nav_buttons.items():
            button.configure(bg=THEME['hero'] if name==key else '#101010',
                             fg=THEME['text'] if name==key else THEME['muted'])

    # 화면의 선택과 등록 개수 기준. 완료 개수와 구분.
    def update_metrics(self):
        photos = len(self.photo_list.curselection())
        styles = len(self.profile['styles'])
        self.selected_count.set(str(photos))
        self.registered_count.set(str(styles))
        self.combination_count.set(str(photos*styles))

    def say(self, text):
        text = self.wording(text)
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
        self.update_metrics()

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

        self.update_metrics()

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
        if self.prompt_mode:
            labels = [('assist', '포토 어시스트'), ('style_tab', '만들기'), ('prompt_input', '입력칸'), ('generate', '생성'), ('save', '저장')]
        self.mapping.set(' / '.join(name + ': ' + (self.profile.get(k) or {}).get('value', '미지정') for k, name in labels))
        self.style_list.delete(0, 'end')
        for s in self.profile['styles']:
            self.style_list.insert('end', s['prompt'] if self.prompt_mode else f"{s['name']} · 왼쪽 이동 {s.get('swipes', 0)}회")
        self.style_summary.set(self.wording(f"등록된 스타일 {len(self.profile['styles'])}개 · 추가·변경: [버튼 · 스타일] 메뉴"))

        self.update_metrics()

    # 추가한 순서대로 실행. 스타일 이름은 완료 기록에도 사용하므로 중복을 방지.
    def add_style(self):
        if self.prompt_mode:
            text = self.style_name.get().strip()
            if not text:
                self.say('프롬프트 문구 입력 필요.'); return
            if len(self.profile['styles']) >= 3:
                self.say('프롬프트 최대 3개. 변경 시 기존 항목 삭제 후 추가.'); return
            if any(s['prompt'] == text for s in self.profile['styles']):
                self.say('동일 프롬프트 등록 상태.'); return
            self.profile['styles'].append({'name': text, 'prompt': text})
            self.refresh_profile(save=True)
            self.style_name.delete(0, 'end')
            return
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
            if self.prompt_mode:
                profile['trial'] = first_only
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
            if not messagebox.askyesno('새 작업', self.wording('새 작업 시작 확인.\n기존 완료 이력 보관 및 저장된 사진 유지. 동일 사진·스타일 재선택 시 중복 생성 가능.')):
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


PROMPT_HELP = """만들기 · 프롬프트 결과 수집
1. [02 버튼 · 프롬프트] → 기기 찾기 → 연결 기기 선택.
2. 폰 원본 사진에서 화면 읽기 → 포토 어시스트 지정.
3. 포토 어시스트에서 화면 읽기 → 만들기 탭 지정.
4. 만들기 화면의 비어 있는 입력창을 화면 읽기로 선택 → 입력칸 지정.
5. 폰에서 문구 직접 입력 → 화면 읽기 → 생성 버튼 지정.
6. 직접 생성 완료 → 화면 읽기 → 다른 파일로 저장 지정.
7. PC 프롬프트 문구 입력 → 추가. 서로 다른 문구 3개 등록.
8. 결과 수집 → 사진 목록 불러오기 → 원본만 선택.
9. 첫 사진·첫 프롬프트 시험 → 결과 확인 → 새 작업 → 전체 수집 시작.

각 원본에 등록 순서대로 3개 문구 적용. 매번 원본 다시 열기.
입력창 탭 → 기존 내용 교체 → 입력 확인 → 생성 → 다른 파일로 저장.
생성 결과 중 처음 표시된 결과 한 장만 저장. 다른 후보 스와이프 없음.
입력 실패·추가 팝업·저장 불명확 시 중단. 자동 생성 재시도 없음.
프롬프트 모드의 실기기 한글 입력·저장 동작 확인 필요.
설정·진행 기록은 기존 스타일 모드와 별도 보관.
스타일 모드와 동시 실행 제한. 폰 잠금 해제 및 인터넷 연결 유지.
"""

HELP = '''처음 한 번 · [버튼 · 스타일] 메뉴에서 등록
1. 폰 USB 디버깅 활성화 및 PC 연결 허용.
2. [02 버튼 · 스타일] → [기기 찾기] → 연결 기기에서 대상 폰 선택.
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
[기록 폴더]: PC의 버튼 설정·실행 이력·오류 기록 확인.
결과 사진 저장 위치: 원본과 같은 폰 폴더.

현재 확인 범위와 제한
S26+ 한 장·3d 툰 실행 성공 보고. 여러 장 연속 실행 및 화면 누적 수정 효과 확인 필요.
이름 없는 요소·세로 목록·스타일별 별도 진입 경로·추가 팝업은 추가 구현 필요.
저장 전후 사진 목록 차이로 결과 연결. 다른 이미지 생성 작업 병행 금지.
폰 자동화 보조 프로그램 실행 가능. 루팅·보안 차단 해제·원본 삭제 명령 없음.
원본 보존 확인: 파일 경로·크기·수정시간 비교. 파일 내용 해시 비교 제외.
'''


# EXE 패키징 검사임. 폰을 연결하는 실기기 테스트는 아님.
def self_test(prompt_mode=False):
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
            for mode in ([True] if prompt_mode else [False, True]):
                root = App(prompt_mode=mode)
                root.update()
                root.destroy()
        finally:
            if old is None:
                os.environ.pop('LOCALAPPDATA', None)
            else:
                os.environ['LOCALAPPDATA'] = old


def launch(prompt_mode=False):
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')
    if '--self-test' in sys.argv:
        self_test(prompt_mode)
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
        App(prompt_mode=prompt_mode).mainloop()



if __name__ == '__main__':
    launch()
