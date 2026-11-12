"""No-scroll, adaptive-height calculation tests (no GUI required)."""
import unittest

from layout import FOOTER_HEIGHT, HEADER_HEIGHT, PROFILES, fit_layout


class LayoutTest(unittest.TestCase):
    def test_standard_two_providers_fills_entire_window(self):
        cards = [(2, False), (2, False)]
        layout, height = fit_layout(cards, 1040 - 16)
        self.assertEqual(layout.name, 'comfortable')
        self.assertEqual(height, 774)
        self.assertEqual(height - HEADER_HEIGHT - FOOTER_HEIGHT,
                         layout.body_height(cards))

    def test_768p_auto_fits_without_scroll(self):
        cards = [(2, False), (2, False)]
        layout, height = fit_layout(cards, 728 - 16)
        self.assertEqual(layout.name, 'medium')
        self.assertLessEqual(height, 712)
        self.assertGreaterEqual(layout.chart_height, 190)

    def test_three_providers_three_quotas_on_small_screen(self):
        cards = [(3, False)] * 3
        layout, height = fit_layout(cards, 728 - 16)
        self.assertEqual(layout.name, 'ultra')
        self.assertLessEqual(height, 712)

    def test_quota_count_and_stale_increase_height(self):
        cards = [(2, False), (2, False)]
        comfy = PROFILES[0]
        self.assertGreater(comfy.window_height([(3, False), (2, False)]),
                           comfy.window_height(cards))
        self.assertEqual(comfy.window_height([(2, True), (2, False)]) -
                         comfy.window_height(cards),17)

    def test_no_services_still_displays_chart(self):
        layout, height = fit_layout([], 800)
        self.assertGreater(layout.chart_height,0)
        self.assertEqual(height, layout.window_height([]))

    def test_fallback_preserves_content_instead_of_clipping(self):
        cards = [(7, False)] * 6
        layout, height = fit_layout(cards, 320)
        self.assertEqual(layout.name, 'minimum')
        self.assertEqual(height, layout.window_height(cards))


if __name__=='__main__':
    unittest.main()
