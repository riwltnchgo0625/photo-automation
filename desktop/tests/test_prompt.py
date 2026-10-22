"""프롬프트 순서·원본 복귀·입력 실패 검사. 실기기 검증과 별개."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from automation import Phone, execute, validate, photo_key
from test_automation import FakePhone, FastStop, ORIGINAL, PROFILE


def profile():
    result = copy.deepcopy(PROFILE)
    result.update(mode='prompt', prompt_input={'field': 'text', 'value': '입력창'},
                  styles=[], prompt_serial='test-device',
                  photo_prompts={photo_key('test-device', ORIGINAL): ['모자', '목도리', '해변 배경']})
    return result


class PromptPhone(FakePhone):
    def __init__(self):
        super().__init__()
        self.steps = []

    def open_original(self, photo):
        super().open_original(photo)
        self.steps.append(('open', photo['id']))

    def input_text(self, selector, text, stop):
        self.steps.append(('input', text))

    def click(self, selector, stop, timeout=20):
        super().click(selector, stop, timeout)
        self.steps.append(('click', selector['value']))


class PromptRunTests(unittest.TestCase):
    def test_two_originals_three_prompts_and_resume(self):
        phone = PromptPhone()
        second = {**ORIGINAL, 'id': '99', 'path': ORIGINAL['path'].replace('dog', 'cat')}
        phone.rows['99'] = second
        configured = profile()
        configured['photo_prompts'][photo_key(phone.serial, second)] = ['흰 접시', '딸기 추가', '나무 식탁']
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            execute(phone, configured, [ORIGINAL, second], FastStop(), lambda _: None, work)
            self.assertEqual(phone.opened, ['1']*3 + ['99']*3)
            expected = []
            for photo in [ORIGINAL, second]:
                for text in configured['photo_prompts'][photo_key(phone.serial, photo)]:
                    expected += [('open', photo['id']), ('click', 'assist'), ('click', 'style'),
                                 ('input', text), ('click', 'generate'), ('click', 'save')]
            self.assertEqual(phone.steps, expected)
            self.assertEqual(len(json.loads((work/'progress.json').read_text())['done']), 6)
            execute(phone, configured, [ORIGINAL, second], FastStop(), lambda _: None, work)
            self.assertEqual(phone.steps, expected)

    def test_input_failure_stops_before_generation_and_blocks_retry(self):
        phone = PromptPhone()
        phone.input_text = Mock(side_effect=TimeoutError('input failed'))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TimeoutError):
                execute(phone, profile(), [ORIGINAL], FastStop(), lambda _: None, Path(tmp))
            self.assertNotIn('generate', phone.clicks)
            self.assertNotIn('save', phone.clicks)
            with self.assertRaisesRegex(RuntimeError, '불명확'):
                execute(phone, profile(), [ORIGINAL], FastStop(), lambda _: None, Path(tmp))

    def test_three_required_but_single_prompt_trial_allowed(self):
        p = profile(); p['photo_prompts'][photo_key('test-device', ORIGINAL)] = ['모자', '', '']
        with self.assertRaisesRegex(ValueError, '3개'):
            validate(p, [ORIGINAL])
        p['trial'] = True
        validate(p, [ORIGINAL])

    def test_missing_input_and_empty_prompt_rejected(self):
        p = profile(); p.pop('prompt_input')
        with self.assertRaisesRegex(ValueError, '입력칸'):
            validate(p, [ORIGINAL])
        p = profile(); p['photo_prompts'][photo_key('test-device', ORIGINAL)][0] = ' '
        with self.assertRaisesRegex(ValueError, '프롬프트 3개'):
            validate(p, [ORIGINAL])

    def test_missing_second_photo_stops_before_any_phone_action(self):
        phone = PromptPhone()
        second = {**ORIGINAL, 'id': '99'}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                execute(phone, profile(), [ORIGINAL, second], FastStop(), lambda _: None, Path(tmp))
        self.assertEqual(phone.steps, [])

    def test_same_filename_different_device_id_or_path_isolated(self):
        key = photo_key('test-device', ORIGINAL)
        for serial, photo in [('other-device', ORIGINAL), ('test-device', {**ORIGINAL, 'id': '99'}),
                              ('test-device', {**ORIGINAL, 'path': '/another/dog.jpg'})]:
            self.assertNotEqual(key, photo_key(serial, photo))

    def test_prompt_change_requires_new_job(self):
        phone = PromptPhone(); p = profile()
        with tempfile.TemporaryDirectory() as tmp:
            execute(phone, p, [ORIGINAL], FastStop(), lambda _: None, Path(tmp))
            p['photo_prompts'][photo_key(phone.serial, ORIGINAL)][0] = '새로운 문구'
            with self.assertRaisesRegex(RuntimeError, '선택 조건 불일치'):
                execute(phone, p, [ORIGINAL], FastStop(), lambda _: None, Path(tmp))
            self.assertEqual(len(phone.opened), 3)

    def test_repeated_text_in_three_slots_saves_three_results(self):
        phone = PromptPhone(); p = profile()
        p['photo_prompts'][photo_key(phone.serial, ORIGINAL)] = ['모자'] * 3
        with tempfile.TemporaryDirectory() as tmp:
            execute(phone, p, [ORIGINAL], FastStop(), lambda _: None, Path(tmp))
            self.assertEqual(len(json.loads((Path(tmp)/'progress.json').read_text())['done']), 3)

    def test_wrong_device_rejected_before_phone_action(self):
        phone = PromptPhone(); phone.serial = 'other-device'
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, '기기 불일치'):
                execute(phone, profile(), [ORIGINAL], FastStop(), lambda _: None, Path(tmp))
        self.assertEqual(phone.steps, [])


class TextEntryTests(unittest.TestCase):
    def node(self, text):
        return ('<hierarchy><node class="android.widget.EditText" focused="true" '
                'enabled="true" bounds="[0,0][100,100]" text="'+text+'"/></hierarchy>')

    def test_unicode_replacement_verified_before_return(self):
        phone = Phone.__new__(Phone); phone._ui = Mock()
        phone.click = Mock()
        phone.screen = Mock(side_effect=[self.node('이전 내용'), self.node('모자')])
        phone.input_text({'field': 'text', 'value': '입력창'}, '모자', FastStop())
        phone._ui.return_value.set_text.assert_called_once_with('모자', timeout=1)
        self.assertEqual(phone.screen.call_count, 2)

    def test_mismatched_input_times_out(self):
        phone = Phone.__new__(Phone); phone._ui = Mock(); phone.click = Mock()
        phone.screen = Mock(return_value=self.node('잘못된 내용'))
        with patch('automation.time.monotonic', side_effect=[0, 1, 2, 30]):
            with self.assertRaisesRegex(TimeoutError, '입력 확인 실패'):
                phone.input_text({'field': 'text', 'value': '입력창'}, '모자', FastStop())

    def test_ambiguous_focused_field_stops_without_writing(self):
        phone = Phone.__new__(Phone); phone._ui = Mock(); phone.click = Mock()
        node = self.node('모자').replace('<hierarchy>', '').replace('</hierarchy>', '')
        phone.screen = Mock(return_value='<hierarchy>'+node*2+'</hierarchy>')
        with self.assertRaisesRegex(RuntimeError, '중복'):
            phone.input_text({'field': 'text', 'value': '입력창'}, '모자', FastStop())
        phone._ui.assert_not_called()


class EditorTests(unittest.TestCase):
    def test_switch_saves_old_photo_then_loads_new_photo(self):
        from app import App
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        second = {**ORIGINAL, 'id': '99', 'path': '/another/dog.jpg'}
        first_key, second_key = photo_key('test-device', ORIGINAL), photo_key('test-device', second)
        with tempfile.TemporaryDirectory() as tmp:
            editor = SimpleNamespace(prompt_mode=True, edit_key=first_key, edit_photos=[ORIGINAL, second],
                                     profile={'photo_prompts': {second_key: ['A', 'B', 'C']}},
                                     profile_path=Path(tmp)/'buttons.json', busy=False,
                                     serial=Mock(get=lambda: 'test-device'),
                                     prompt_photo_box=Mock(current=lambda: 1),
                                     prompt_vars=[Mock(get=lambda t=t: t) for t in ['모자', '목도리', '배경']],
                                     prompt_entries=[MagicMock() for _ in range(3)],
                                     refresh_photo_rows=Mock(), update_metrics=Mock(), say=Mock())
            editor.save_photo_prompts = lambda notify=True: App.save_photo_prompts(editor, notify)
            App.load_prompt_editor(editor)
            saved = json.loads(editor.profile_path.read_text())['photo_prompts']
            self.assertEqual(saved[first_key], ['모자', '목도리', '배경'])
            self.assertEqual(saved[second_key], ['A', 'B', 'C'])
            self.assertEqual(editor.edit_key, second_key)
            for var, text in zip(editor.prompt_vars, ['A', 'B', 'C']):
                var.set.assert_called_once_with(text)
