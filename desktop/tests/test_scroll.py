"""중첩 영역 휠 전달 검사. 실제 Windows 입력 검사는 별도 수행."""
import unittest
from types import SimpleNamespace
from app import App


class Area:
    def __init__(self, bounds=(0, 1), master=None):
        self.bounds, self.master, self.moves = bounds, master, []

    def yview(self):
        return self.bounds

    def yview_scroll(self, units, mode):
        self.moves.append((units, mode))


class WheelTests(unittest.TestCase):
    def setUp(self):
        self.card = Area((0, .5))
        self.listing = Area((0, .4), self.card)
        self.app = SimpleNamespace(scroll_targets={self.card: self.card, self.listing: self.listing})

    def wheel(self, widget, delta=-120, num=None):
        return App.on_wheel(self.app, SimpleNamespace(widget=widget, delta=delta, num=num))

    def test_list_before_card_and_no_default_double_scroll(self):
        self.assertEqual(self.wheel(self.listing), 'break')
        self.assertEqual(self.listing.moves, [(3, 'units')])
        self.assertEqual(self.card.moves, [])

    def test_end_of_list_continues_in_card(self):
        self.listing.bounds = (.6, 1)
        self.wheel(self.listing)
        self.assertEqual(self.card.moves, [(3, 'units')])
        self.assertEqual(self.listing.moves, [])

    def test_input_uses_card_without_changing_value(self):
        self.wheel(Area(master=self.card))
        self.assertEqual(self.card.moves, [(3, 'units')])

    def test_scrollbar_routes_to_its_list(self):
        bar = Area(master=self.card)
        self.app.scroll_targets[bar] = self.listing
        self.wheel(bar)
        self.assertEqual(self.listing.moves, [(3, 'units')])
        self.assertEqual(self.card.moves, [])

    def test_small_delta_and_linux_up(self):
        self.listing.bounds = (.2, .6)
        self.wheel(self.listing, delta=30)
        self.wheel(self.listing, num=4)
        self.assertEqual(self.listing.moves, [(-3, 'units'), (-3, 'units')])

    def test_full_area_and_zero_delta_do_not_scroll(self):
        self.card.bounds = self.listing.bounds = (0, 1)
        self.wheel(self.listing)
        self.wheel(self.listing, delta=0)
        self.assertEqual(self.card.moves + self.listing.moves, [])
