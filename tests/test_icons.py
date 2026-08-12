import math
import os
import tempfile
import unittest
from pathlib import Path

from quota_ring.icons import RING_RADII, prune_icons, rings_svg, write_icon


class RingsSvgTests(unittest.TestCase):
    def test_all_rings_render_track_and_arc(self):
        svg = rings_svg((80, 30, 2))
        self.assertEqual(svg.count("<circle"), 6)
        self.assertIn("#43a047", svg)  # green for 80%
        self.assertIn("#fdd835", svg)  # yellow for 30%
        self.assertIn("#ef3e32", svg)  # red for 2%

    def test_arc_length_matches_remaining_percent(self):
        svg = rings_svg((50, None, None))
        circumference = 2 * math.pi * RING_RADII[0]
        self.assertIn(
            f'stroke-dasharray="{circumference / 2:.2f} {circumference:.2f}"',
            svg,
        )

    def test_none_state_renders_track_only(self):
        svg = rings_svg((None, None, None))
        self.assertEqual(svg.count("<circle"), 3)
        self.assertNotIn("dasharray", svg)

    def test_pulse_light_shades_only_critical_rings(self):
        svg = rings_svg((2, 50, 1), pulse_light=True)
        self.assertEqual(svg.count("#ffc9b8"), 2)
        svg = rings_svg((2, 50, 1))
        self.assertNotIn("#ffc9b8", svg)


class WriteIconTests(unittest.TestCase):
    def test_content_addressed_name_and_no_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            svg = rings_svg((10, 20, 30))
            name = write_icon(svg, cache_dir)
            self.assertTrue(name.startswith("quota-ring-"))
            path = cache_dir / f"{name}.svg"
            self.assertEqual(path.read_text(), svg)
            path.write_text("sentinel")
            self.assertEqual(write_icon(svg, cache_dir), name)
            self.assertEqual(path.read_text(), "sentinel")

    def test_different_states_get_different_names(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            first = write_icon(rings_svg((10, 20, 30)), cache_dir)
            second = write_icon(rings_svg((10, 20, 31)), cache_dir)
            self.assertNotEqual(first, second)


class PruneIconsTests(unittest.TestCase):
    def test_keeps_newest_and_never_removes_the_unknown_icon(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            unknown = cache_dir / "quota-ring-unknown.svg"
            unknown.write_text("<svg/>")
            names = []
            for index in range(10):
                path = cache_dir / f"quota-ring-{index:012d}.svg"
                path.write_text(f"<svg>{index}</svg>")
                os.utime(path, (1000 + index, 1000 + index))
                names.append(path)

            self.assertEqual(prune_icons(cache_dir, keep=4), 6)
            remaining = sorted(p.name for p in cache_dir.glob("*.svg"))
            self.assertIn("quota-ring-unknown.svg", remaining)
            self.assertEqual(
                [p.name for p in names[6:]],
                sorted(n for n in remaining if n != "quota-ring-unknown.svg"),
            )

    def test_no_op_when_under_the_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            write_icon(rings_svg((10, 20, 30)), cache_dir)
            self.assertEqual(prune_icons(cache_dir, keep=64), 0)
            self.assertEqual(len(list(cache_dir.glob("*.svg"))), 1)


if __name__ == "__main__":
    unittest.main()
