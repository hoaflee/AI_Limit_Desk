"""Mouse resize semantics; no Tk display required."""
import unittest
from layout import FOOTER_HEIGHT, HEADER_HEIGHT
from resize import (DEFAULT_WIDTH, MIN_WIDTH, WindowRect, hit_test_resize,
                    layout_for_size, minimum_height, resize_rect)


class ResizeGeometryTest(unittest.TestCase):
    def setUp(self):
        self.box = WindowRect(100, 100, 440, 750)
        self.area = (0, 0, 1600, 1000)
        self.minimum = minimum_height([(2, False), (3, False)])

    def test_all_edges_and_corners_detected(self):
        self.assertEqual(hit_test_resize(0, 0, 440, 750), 'nw')
        self.assertEqual(hit_test_resize(439, 0, 440, 750), 'ne')
        self.assertEqual(hit_test_resize(0, 749, 440, 750), 'sw')
        self.assertEqual(hit_test_resize(439, 749, 440, 750), 'se')
        self.assertEqual(hit_test_resize(420, 731, 440, 750), 'se')
        self.assertEqual(hit_test_resize(0, 300, 440, 750), 'w')
        self.assertEqual(hit_test_resize(439, 300, 440, 750), 'e')
        self.assertEqual(hit_test_resize(200, 0, 440, 750), 'n')
        self.assertEqual(hit_test_resize(200, 749, 440, 750), 's')
        self.assertEqual(hit_test_resize(200, 200, 440, 750), '')

    def test_corner_resizing_keeps_its_opposite_corner_anchored(self):
        rect = resize_rect(self.box, 'nw', -45, -50, self.area, self.minimum)
        self.assertEqual((rect.x,rect.y), (55,50))
        self.assertEqual((rect.width,rect.height),(485,800))
        rect = resize_rect(self.box, 'se', 100, 45, self.area, self.minimum)
        self.assertEqual((rect.x,rect.y,rect.width,rect.height),(100,100,540,795))

    def test_minimum_never_hides_chart_or_quotas(self):
        rect = resize_rect(self.box, 'se', -1000, -1000, self.area, self.minimum)
        self.assertEqual(rect.width, MIN_WIDTH)
        self.assertEqual(rect.height, self.minimum)
        layout, height = layout_for_size([(2,False),(3,False)], 984, rect.height)
        self.assertEqual(height, layout.window_height([(2,False),(3,False)]))
        self.assertEqual(height-HEADER_HEIGHT-FOOTER_HEIGHT, layout.body_height([(2,False),(3,False)]))

    def test_manual_taller_height_allocates_space_to_chart(self):
        cards=[(2,False),(3,False)]
        baseline_layout, baseline_height = layout_for_size(cards,984)
        taller, height = layout_for_size(cards,984,900)
        self.assertEqual(height,900)
        self.assertGreater(taller.chart_height,baseline_layout.chart_height)
        self.assertEqual(taller.window_height(cards),900)
        self.assertEqual(layout_for_size(cards,984,None)[1],baseline_height)

    def test_manual_small_height_changes_density_instead_of_clipping(self):
        cards=[(2,False),(3,False)]
        min_h=minimum_height(cards)
        layout,height=layout_for_size(cards,984,min_h+12)
        self.assertEqual(height,min_h+12)
        self.assertEqual(layout.window_height(cards),height)

    def test_resize_stops_at_monitor_edges(self):
        a=resize_rect(self.box, 'se', 3000,3000,self.area,self.minimum)
        self.assertEqual(a.x+a.width,1596)
        self.assertEqual(a.y+a.height,996)

if __name__=='__main__':
    unittest.main()
