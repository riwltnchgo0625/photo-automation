"""프롬프트 순서·원본 복귀·입력 실패 검사. 실기기 검증과 별개."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from automation import Phone, execute, validate
from test_automation import FakePhone, FastStop, ORIGINAL, PROFILE


def profile():
    result = copy.deepcopy(PROFILE)
    result.update(mode='prompt', prompt_input={'field': 'text', 'value': '입력창'},
                  styles=[{'name': t, 'prompt': t} for t in ['모자', '목도리', '해변 배경']])
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
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            execute(phone, profile(), [ORIGINAL, second], FastStop(), lambda _: None, work)
            self.assertEqual(phone.opened, ['1']*3 + ['99']*3)
            expected = []
            for photo in [ORIGINAL, second]:
                for text in ['모자', '목도리', '해변 배경']:
                    expected += [('open', photo['id']), ('click', 'assist'), ('click', 'style'),
                                 ('input', text), ('click', 'generate'), ('click', 'save')]
            self.assertEqual(phone.steps, expected)
            self.assertEqual(len(json.loads((work/'progress.json').read_text())['done']), 6)
            execute(phone, profile(), [ORIGINAL, second], FastStop(), lambda _: None, work)
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
        p = profile(); p['styles'] = p['styles'][:1]
        with self.assertRaisesRegex(ValueError, '3개'):
            validate(p, [ORIGINAL])
        p['trial'] = True
        validate(p, [ORIGINAL])

    def test_missing_input_and_empty_prompt_rejected(self):
        p = profile(); p.pop('prompt_input')
        with self.assertRaisesRegex(ValueError, '입력칸'):
            validate(p, [ORIGINAL])
        p = profile(); p['styles'][0]['prompt'] = ' '
        with self.assertRaisesRegex(ValueError, '빈 프롬프트'):
            validate(p, [ORIGINAL])


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
