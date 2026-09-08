"""Unit and subprocess tests; no third-party dependencies."""

from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from validator import Stop, TimetableError, find_conflicts, format_conflict, load_timetable, parse_time


ROOT = Path(__file__).resolve().parents[1]
HEADER = "Train,Station,Platform,Arrival,Departure\n"
EXPECTED = (
    "[CONFLICT]\nStation: Taipei\nPlatform: 1\n"
    "Train 101 overlaps with Train 103\nOverlap: 08:03 - 08:05"
)


class CsvTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "timetable.csv"

    def load(self, content):
        self.path.write_text(content, encoding="utf-8")
        return load_timetable(self.path)

    def test_sample_exact_conflict(self):
        conflicts = find_conflicts(load_timetable(ROOT / "examples/sample_timetable.csv"))
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(format_conflict(conflicts[0]), EXPECTED)

    def test_bom_whitespace_blank_lines_and_leading_zeros(self):
        stops = self.load("\ufeff" + HEADER + "\n 001 , Taipei , 01 , 08:00 , 08:05 \n")
        self.assertEqual(stops, [Stop("001", "Taipei", "01", 480, 485)])

    def test_reordered_columns_quoted_names_and_unicode(self):
        stops = self.load('Station,Departure,Train,Arrival,Platform\n"台北,東",08:05,101,08:00,1\n')
        self.assertEqual(stops, [Stop("101", "台北,東", "1", 480, 485)])

    def test_header_only_is_empty_timetable(self):
        self.assertEqual(self.load(HEADER), [])

    def test_bad_headers(self):
        for content in ("", "Train,Station\n",
                        "Train,Station,Platform,Arrival,Arrival\n",
                        HEADER.rstrip() + ",Extra\n",
                        HEADER.replace("Train", "train")):
            with self.subTest(content=content):
                with self.assertRaises(TimetableError):
                    self.load(content)

    def test_missing_extra_or_empty_values(self):
        for row in ("101,Taipei,1,08:00",
                    "101,Taipei,1,08:00,08:05,extra",
                    "101,Taipei,1,,08:05",
                    ",Taipei,1,08:00,08:05",
                    "101, ,1,08:00,08:05",
                    "101,Taipei,,08:00,08:05",
                    "101,Taipei,1,08:00,"):
            with self.subTest(row=row):
                with self.assertRaisesRegex(TimetableError, "line 2:"):
                    self.load(HEADER + row + "\n")

    def test_invalid_times(self):
        for value in ("8:00", "24:00", "08:60", "-1:00", "08:00:00", "text", "０８:００"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(TimetableError, "line 2: invalid time"):
                    self.load(HEADER + f"101,Taipei,1,{value},09:00\n")

    def test_invalid_departure(self):
        with self.assertRaisesRegex(TimetableError, "line 2: invalid time"):
            self.load(HEADER + "101,Taipei,1,08:00,25:00\n")

    def test_overnight_and_zero_duration_rejected(self):
        for arrival, departure in (("23:55", "00:05"), ("08:00", "08:00")):
            with self.subTest(arrival=arrival):
                with self.assertRaisesRegex(TimetableError, "Departure must be later"):
                    self.load(HEADER + f"101,Taipei,1,{arrival},{departure}\n")

    def test_malformed_csv(self):
        with self.assertRaisesRegex(TimetableError, "invalid CSV"):
            self.load(HEADER + '101,"Taipei,1,08:00,08:05\n')

    def test_clock_boundaries(self):
        self.assertEqual(parse_time("00:00"), 0)
        self.assertEqual(parse_time("23:59"), 1439)


class ConflictTests(unittest.TestCase):
    def test_touching_and_separated_intervals(self):
        self.assertEqual(find_conflicts([
            Stop("1", "Taipei", "1", 480, 485),
            Stop("2", "Taipei", "1", 485, 490),
            Stop("3", "Taipei", "1", 500, 505),
        ]), [])

    def test_different_stations_platforms_and_same_train(self):
        first = Stop("101", "Taipei", "1", 480, 490)
        for other in (
            Stop("102", "Banqiao", "1", 480, 490),
            Stop("102", "Taipei", "2", 480, 490),
            Stop("101", "Taipei", "1", 482, 488),
            Stop("102", "Taipei", "01", 480, 490),
            Stop("102", "taipei", "1", 480, 490),
        ):
            with self.subTest(other=other):
                self.assertEqual(find_conflicts([first, other]), [])

    def test_nested_intervals_and_unsorted_input(self):
        stops = [
            Stop("1", "Taipei", "1", 480, 510),
            Stop("2", "Taipei", "1", 483, 485),
            Stop("3", "Taipei", "1", 486, 490),
        ]
        conflicts = find_conflicts(reversed(stops))
        self.assertEqual([(c.first.train, c.second.train, c.start, c.end) for c in conflicts],
                         [("1", "2", 483, 485), ("1", "3", 486, 490)])

    def test_three_simultaneous_trains_report_all_pairs(self):
        conflicts = find_conflicts([Stop(str(i), "Taipei", "1", 480, 490) for i in range(3)])
        self.assertEqual({(c.first.train, c.second.train) for c in conflicts},
                         {("0", "1"), ("0", "2"), ("1", "2")})
        self.assertTrue(all((c.start, c.end) == (480, 490) for c in conflicts))

    def test_empty_input(self):
        self.assertEqual(find_conflicts([]), [])

    def test_matches_brute_force_for_random_valid_stops(self):
        rng = random.Random(2026)
        stops = []
        for i in range(150):
            start = rng.randrange(1439)
            stops.append(Stop(str(i), rng.choice(["Taipei", "Banqiao"]),
                              rng.choice(["1", "2"]), start, rng.randrange(start + 1, 1440)))
        expected = set()
        for i, first in enumerate(stops):
            for second in stops[i + 1:]:
                start = max(first.arrival, second.arrival)
                end = min(first.departure, second.departure)
                if (first.station, first.platform) == (second.station, second.platform) and start < end:
                    expected.add((frozenset((first.train, second.train)), start, end))
        rng.shuffle(stops)
        actual = find_conflicts(stops)
        self.assertEqual(len(actual), len(expected))
        self.assertEqual({(frozenset((c.first.train, c.second.train)), c.start, c.end)
                          for c in actual}, expected)


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "validator.py"), *map(str, args)],
            capture_output=True, text=True, check=False,
        )

    def test_sample_output_and_exit_code(self):
        result = self.run_cli(ROOT / "examples/sample_timetable.csv")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, EXPECTED + "\n")
        self.assertEqual(result.stderr, "")

    def test_clean_timetable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clean timetable.csv"
            path.write_text(HEADER + "101,Taipei,1,08:00,08:05\n", encoding="utf-8")
            result = self.run_cli(path)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "No conflicts found.\n")
        self.assertEqual(result.stderr, "")

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_cli(Path(directory) / "missing.csv")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("[ERROR]"))
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_input_and_encoding(self):
        for content in (b"wrong,header\n", b"\xff\xfe", (HEADER + "101,Taipei,1,bad,08:05\n").encode()):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "invalid.csv"
                path.write_bytes(content)
                result = self.run_cli(path)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr.startswith("[ERROR]"))
                self.assertNotIn("Traceback", result.stderr)

    def test_help(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("csv_file", result.stdout)

    def test_missing_argument(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
