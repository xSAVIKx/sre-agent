"""Real-mode tool results must stay small enough for the model's context window."""

import unittest

from sre_agent import gcp_tools


class TestBoundTimeSeries(unittest.TestCase):
    def test_caps_series_and_points(self) -> None:
        series = [{"metric": {"type": f"m{i}"}, "points": [{"value": p} for p in range(500)]} for i in range(100)]
        bounded = gcp_tools._bound_time_series(series)

        self.assertEqual(len(bounded), gcp_tools.MAX_TIME_SERIES + 1)
        self.assertIn("note", bounded[-1])
        first = bounded[0]
        self.assertEqual(len(first["points"]), gcp_tools.MAX_POINTS_PER_SERIES)
        self.assertEqual(first["points"][0], {"value": 0}, "keeps the newest (first) points")
        self.assertEqual(first["points_truncated"], 500)

    def test_small_results_pass_through(self) -> None:
        series = [{"metric": {"type": "m"}, "points": [{"value": 1}]}]
        self.assertEqual(gcp_tools._bound_time_series(series), series)


if __name__ == "__main__":
    unittest.main()
