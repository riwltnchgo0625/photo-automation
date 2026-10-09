"""기기 없이 결과 오인/원본 변경 방지 로직을 검사한다."""
import unittest
from collector import parse_media, new_result


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.original = dict(id='10', size=100, modified=5, path='/storage/emulated/0/Pictures/Test/a.jpg')
        self.before = {'10': self.original}
        self.result = dict(id='11', size=200, modified=6, path='/storage/emulated/0/Pictures/Test/a_style.jpg')

    def test_filename_with_comma_and_korean(self):
        rows = parse_media('Row: 0 _id=10, _size=100, date_modified=5, _data=/storage/emulated/0/사진/a, b.jpg\n')
        self.assertEqual(rows['10']['path'], '/storage/emulated/0/사진/a, b.jpg')

    def test_success(self):
        self.assertEqual(new_result(self.before, {**self.before, '11': self.result}, self.original), self.result)

    def test_no_file_is_not_success(self):
        self.assertIsNone(new_result(self.before, self.before, self.original))

    def test_zero_size_is_not_success(self):
        self.assertIsNone(new_result(self.before, {**self.before, '11': {**self.result, 'size': 0}}, self.original))

    def test_wrong_folder(self):
        with self.assertRaisesRegex(RuntimeError, '다른 폴더'):
            new_result(self.before, {**self.before, '11': {**self.result, 'path': '/elsewhere/a.jpg'}}, self.original)

    def test_original_changed(self):
        with self.assertRaisesRegex(RuntimeError, '원본'):
            new_result(self.before, {'10': {**self.original, 'size': 101}}, self.original)

    def test_multiple_new_images(self):
        with self.assertRaisesRegex(RuntimeError, '여러 이미지'):
            new_result(self.before, {**self.before, '11': self.result, '12': {**self.result, 'id': '12'}}, self.original)

    def test_permission_error_not_empty_list(self):
        with self.assertRaises(RuntimeError):
            parse_media('Error while accessing provider: permission denied')


if __name__ == '__main__':
    unittest.main()
