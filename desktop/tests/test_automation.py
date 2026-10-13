import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from automation import execute, parse_media, saved_result, screen_candidates, select_nodes, Stopped

ORIGINAL = dict(id='1', size=100, modified=10, path='/storage/emulated/0/Pictures/Test/dog.jpg')
RESULT = dict(id='2', size=200, modified=11, path='/storage/emulated/0/Pictures/Test/dog_edited.jpg')
XML = '''<hierarchy><node text="다른 파일로 저장" content-desc="save's button"
resource-id="pkg:id/save" enabled="true" bounds="[10,20][110,80]"/></hierarchy>'''
PROFILE = {'assist': {'field': 'text', 'value': 'assist'},
           'style_tab': {'field': 'text', 'value': 'style'},
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
    def test_each_style_reopens_original_and_resume_skips_done(self):
        phone = FakePhone()
        with tempfile.TemporaryDirectory() as d:
            execute(phone, PROFILE, [ORIGINAL], FastStop(), lambda _: None, Path(d))
            self.assertEqual(phone.opened, ['1', '1'])
            self.assertEqual(phone.clicks, ['assist', 'style', 'A', 'save', 'assist', 'style', 'B', 'save'])
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
