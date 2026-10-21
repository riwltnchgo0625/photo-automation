import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from automation import execute, parse_media, saved_result, screen_candidates, select_nodes, Stopped, Phone, MEDIA, GALLERY

ORIGINAL = dict(id='1', size=100, modified=10, path='/storage/emulated/0/Pictures/Test/dog.jpg')
RESULT = dict(id='2', size=200, modified=11, path='/storage/emulated/0/Pictures/Test/dog_edited.jpg')
XML = '''<hierarchy><node text="다른 파일로 저장" content-desc="save's button"
resource-id="pkg:id/save" enabled="true" bounds="[10,20][110,80]"/></hierarchy>'''
PROFILE = {'assist': {'field': 'text', 'value': 'assist'},
           'style_tab': {'field': 'text', 'value': 'style'},
           'generate': {'field': 'content-desc', 'value': 'generate'},
           'save': {'field': 'text', 'value': 'save'}, 'more': None,
           'styles': [{'name': 'A', 'selector': {'field': 'text', 'value': 'A'}},
                      {'name': 'B', 'selector': {'field': 'text', 'value': 'B'}}]}


class FastStop:
    def is_set(self):
        return False
    def wait(self, _):
        return False


class FakePhone:
    serial = 'test-device'
    def __init__(self):
        self.rows = {'1': ORIGINAL.copy()}
        self.opened = []
        self.clicks = []
    def photos(self):
        return {k: v.copy() for k, v in self.rows.items()}
    def open_original(self, photo):
        self.opened.append(photo['id'])
    def screen(self):
        return '<hierarchy/>'
    def swipe_style(self, y, stop):
        pass
    def click(self, selector, stop, timeout=20):
        self.clicks.append(selector['value'])
        if selector['value'] == 'save':
            ident = str(len(self.rows) + 1)
            self.rows[ident] = {**RESULT, 'id': ident, 'path': RESULT['path'].replace('.jpg', ident + '.jpg')}


class MediaTests(unittest.TestCase):
    def test_comma_and_korean_filename(self):
        r = parse_media('Row: 0 _id=1, _size=100, date_modified=10, _data=/사진/a, b.jpg')
        self.assertEqual(r['1']['path'], '/사진/a, b.jpg')
    def test_bad_response_is_not_empty_success(self):
        with self.assertRaises(RuntimeError):
            parse_media('Permission denied')
    def test_wait_for_nonempty_file(self):
        self.assertIsNone(saved_result({'1': ORIGINAL}, {'1': ORIGINAL}, ORIGINAL))
        self.assertIsNone(saved_result({'1': ORIGINAL}, {'1': ORIGINAL, '2': {**RESULT, 'size': 0}}, ORIGINAL))
    def test_same_folder(self):
        self.assertEqual(saved_result({'1': ORIGINAL}, {'1': ORIGINAL, '2': RESULT}, ORIGINAL), RESULT)
    def test_wrong_folder_stops(self):
        with self.assertRaises(RuntimeError):
            saved_result({'1': ORIGINAL}, {'1': ORIGINAL, '2': {**RESULT, 'path': '/other/x.jpg'}}, ORIGINAL)
    def test_original_changed_stops(self):
        with self.assertRaises(RuntimeError):
            saved_result({'1': ORIGINAL}, {'1': {**ORIGINAL, 'size': 999}}, ORIGINAL)
    def test_multiple_new_files_stop(self):
        with self.assertRaises(RuntimeError):
            saved_result({'1': ORIGINAL}, {'1': ORIGINAL, '2': RESULT, '3': {**RESULT, 'id': '3'}}, ORIGINAL)


class SelectorTests(unittest.TestCase):
    def test_quotes_in_description(self):
        self.assertEqual(len(select_nodes(XML, {'field': 'content-desc', 'value': "save's button"})), 1)
    def test_disabled_not_clicked(self):
        self.assertEqual(select_nodes(XML.replace('enabled="true"', 'enabled="false"'),
                                      {'field': 'text', 'value': '다른 파일로 저장'}), [])
    def test_unknown_field_rejected(self):
        with self.assertRaises(ValueError):
            select_nodes(XML, {'field': 'xpath-injection', 'value': 'x'})
    def test_duplicate_labels_not_offered(self):
        node = '<node text="중복" enabled="true" bounds="[0,0][10,10]"/>'
        self.assertEqual(screen_candidates('<hierarchy>' + node * 2 + '</hierarchy>'), [])


class RunTests(unittest.TestCase):
    # ADB 없이 실제 open_original이 만드는 명령을 검사함. 삼성 갤러리 동작을 검증하는 것은 아님.
    def test_open_original_requests_stack_cleanup_and_exact_uri(self):
        phone = Phone.__new__(Phone)
        with patch.object(phone, 'shell') as shell:
            for ident in ('1', '99'):
                phone.open_original({**ORIGINAL, 'id': ident})
                shell.assert_called_with(
                    'am', 'start', '-W', '-a', 'android.intent.action.VIEW',
                    '-d', MEDIA + '/' + ident, '-t', 'image/*', '-p', GALLERY,
                    '--grant-read-uri-permission', '--activity-clear-top')

    def test_wait_after_open_before_assist_and_stop_during_wait(self):
        phone = FakePhone()
        stop = threading.Event()
        def stop_in_wait(seconds):
            self.assertEqual(seconds, 2)
            self.assertEqual(phone.opened, ['1'])
            self.assertEqual(phone.clicks, [])
            stop.set()
            return True
        with tempfile.TemporaryDirectory() as d:
            with patch.object(stop, 'wait', side_effect=stop_in_wait):
                with self.assertRaises(Stopped):
                    execute(phone, PROFILE, [ORIGINAL], stop, lambda _: None, Path(d))
            self.assertEqual(phone.clicks, [])

    def test_multiple_originals_keep_photo_then_style_order(self):
        phone = FakePhone()
        second = {**ORIGINAL, 'id': '10', 'path': ORIGINAL['path'].replace('dog', 'cat')}
        phone.rows['10'] = second
        with tempfile.TemporaryDirectory() as d:
            execute(phone, PROFILE, [ORIGINAL, second], FastStop(), lambda _: None, Path(d))
            self.assertEqual(phone.opened, ['1', '1', '10', '10'])
            state = json.loads((Path(d) / 'progress.json').read_text())
            self.assertEqual(list(state['done']), ['1:A', '1:B', '10:A', '10:B'])

    # 예전 설정에는 generate 키가 없음. 폰을 조작하기 전에 등록 안내로 멈춰야 함.
    def test_missing_generate_stops_before_phone_access(self):
        for value in ('missing', None):
            profile = dict(PROFILE)
            if value == 'missing':
                profile.pop('generate')
            else:
                profile['generate'] = value
            phone = FakePhone()
            with tempfile.TemporaryDirectory() as d:
                with patch.object(phone, 'photos') as photos:
                    with self.assertRaisesRegex(ValueError, '생성 버튼 지정'):
                        execute(phone, profile, [ORIGINAL], FastStop(), lambda _: None, Path(d))
                    photos.assert_not_called()
                self.assertFalse((Path(d) / 'progress.json').exists())
            self.assertEqual(phone.opened, [])
            self.assertEqual(phone.clicks, [])

    # 생성 버튼을 찾지 못한 경우 저장 클릭으로 넘어가면 안 됨.
    def test_generate_failure_does_not_save(self):
        phone = FakePhone()
        click = phone.click
        def fail_generate(selector, stop, timeout=20):
            if selector == PROFILE['generate']:
                raise RuntimeError('생성 버튼 없음')
            click(selector, stop, timeout)
        with tempfile.TemporaryDirectory() as d:
            with patch.object(phone, 'click', side_effect=fail_generate):
                with self.assertRaisesRegex(RuntimeError, '생성 버튼 없음'):
                    execute(phone, PROFILE, [ORIGINAL], FastStop(), lambda _: None, Path(d))
            self.assertNotIn('save', phone.clicks)
            state = json.loads((Path(d) / 'progress.json').read_text())
            self.assertIsNotNone(state['pending'])
            self.assertEqual(state['done'], {})

    def test_each_style_reopens_original_and_resume_skips_done(self):
        phone = FakePhone()
        with tempfile.TemporaryDirectory() as d:
            execute(phone, PROFILE, [ORIGINAL], FastStop(), lambda _: None, Path(d))
            self.assertEqual(phone.opened, ['1', '1'])
            self.assertEqual(phone.clicks, ['assist', 'style', 'A', 'generate', 'save',
                                            'assist', 'style', 'B', 'generate', 'save'])
            state = json.loads((Path(d) / 'progress.json').read_text())
            self.assertIsNone(state['pending'])
            self.assertEqual(len(state['done']), 2)
            execute(phone, PROFILE, [ORIGINAL], FastStop(), lambda _: None, Path(d))
            self.assertEqual(phone.opened, ['1', '1'])
    def test_stop_before_start_does_not_touch_phone(self):
        phone = FakePhone()
        stop = threading.Event(); stop.set()
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(Stopped):
                execute(phone, PROFILE, [ORIGINAL], stop, lambda _: None, Path(d))
        self.assertEqual(phone.opened, [])
        self.assertEqual(phone.clicks, [])
    def test_ambiguous_save_leaves_pending_and_prevents_retry(self):
        phone = FakePhone()
        with tempfile.TemporaryDirectory() as d:
            with patch('automation.saved_result', side_effect=RuntimeError('ambiguous')):
                with self.assertRaises(RuntimeError):
                    execute(phone, PROFILE, [ORIGINAL], FastStop(), lambda _: None, Path(d))
            before = phone.clicks.copy()
            with self.assertRaisesRegex(RuntimeError, '불명확'):
                execute(phone, PROFILE, [ORIGINAL], FastStop(), lambda _: None, Path(d))
            self.assertEqual(phone.clicks, before)


if __name__ == '__main__':
    unittest.main()

