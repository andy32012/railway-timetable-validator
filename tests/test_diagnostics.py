"""Traceability, input completeness, reporting and exit-code acceptance tests."""

import csv
import io
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from validator import (
    SCHEMA_VERSION, SourceRange, Stop, __version__, find_conflicts,
    format_report, safe_text, validate_timetable,
)


ROOT = Path(__file__).resolve().parents[1]
HEADER = "Train,Station,Platform,Arrival,Departure\n"
ROW = "101,Taipei,1,08:00,08:05\n"


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "input.csv"

    def write(self, text):
        self.path.write_bytes(text.encode("utf-8"))

    def report(self, text, require_data=False):
        self.write(text)
        return validate_timetable(self.path, require_data)

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "validator.py"), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )

    def test_every_valid_record_has_source_and_unmodified_raw_fields(self):
        self.write("\ufeff" + HEADER + "\n\t \n 001 , 台北 , 01 , 08:00 , 08:05 \n")
        from validator import read_timetable
        result = read_timetable(self.path)
        stop = result.stops[0]
        self.assertEqual(stop.source_file, str(self.path))
        self.assertEqual(stop.source, SourceRange(4, 4))
        self.assertEqual((stop.train, stop.station, stop.platform), ("001", "台北", "01"))
        self.assertEqual(stop.raw_fields["Train"], " 001 ")

    def test_multiline_sources_and_blank_lines_inside_quotes(self):
        report = self.report(HEADER + '101,"台北\n\n\t東",1,08:00,08:05\n'
                             + '103,"台北\n\n\t東",1,08:03,08:08\n')
        diagnostic = report.diagnostics[0]
        self.assertEqual(diagnostic.sources, [SourceRange(2, 4), SourceRange(5, 7)])
        self.assertEqual(diagnostic.station, "台北\n\n\t東")
        self.assertEqual(diagnostic.details["overlap_minutes"], 2)
        self.assertEqual(diagnostic.details["overlap_start"], "08:03")
        self.assertEqual(diagnostic.details["overlap_end"], "08:05")
        self.assertEqual(diagnostic.details["records"][1]["source"],
                         {"line_start": 5, "line_end": 7})

    def test_whitespace_only_physical_line_inside_quotes_is_preserved(self):
        from validator import read_timetable
        self.write(HEADER + '101,"台北\n \t\n東",1,08:00,08:05\n')
        result = read_timetable(self.path)
        self.assertEqual(result.stops[0].station, "台北\n \t\n東")
        self.assertEqual(result.stops[0].source, SourceRange(2, 4))

    def test_multiline_error_reports_start_and_end_not_only_end(self):
        report = self.report(HEADER + '101,"台北\n東",1,invalid,08:05\n')
        error = report.diagnostics[0]
        self.assertEqual(error.code, "INPUT_INVALID_TIME")
        self.assertEqual(error.sources, [SourceRange(2, 3)])
        self.assertEqual(error.field, "Arrival")
        self.assertIn("Sources: lines 2-3", format_report(report))

    def test_crlf_and_cr_physical_line_numbers(self):
        for newline in ("\r\n", "\r"):
            with self.subTest(newline=newline):
                report = self.report((HEADER + '\n101,"台北\n東",1,bad,08:05\n').replace("\n", newline))
                self.assertEqual(report.diagnostics[0].sources, [SourceRange(3, 4)])

    def test_empty_physical_lines_are_ignored(self):
        report = self.report("\n" + HEADER + "\n" + ROW + "\n")
        self.assertEqual(report.summary["records_read"], 1)
        self.assertEqual(report.exit_code, 0)

    def test_spaces_and_tabs_only_physical_lines_are_ignored(self):
        report = self.report(" \t\n" + HEADER + "   \n\t\n" + ROW)
        self.assertEqual(report.summary["records_read"], 1)
        self.assertEqual(report.exit_code, 0)

    def test_delimiter_only_record_is_not_ignored(self):
        report = self.report(HEADER + ",,,,\n")
        self.assertEqual(report.summary["records_read"], 1)
        self.assertEqual(report.summary["records_error"], 1)
        self.assertEqual([d.field for d in report.diagnostics if d.code == "INPUT_EMPTY_FIELD"],
                         ["Train", "Station", "Platform", "Arrival", "Departure"])
        self.assertEqual(report.exit_code, 2)

    def test_quoted_empty_and_whitespace_records_are_not_ignored(self):
        for row in ('""\n', '" \t "\n', '"","","","",""\n', '"\n"\n'):
            with self.subTest(row=row):
                report = self.report(HEADER + row)
                self.assertEqual(report.summary["records_read"], 1)
                self.assertEqual(report.summary["records_error"], 1)
                self.assertEqual(report.exit_code, 2)

    def test_duplicate_after_trimming_lists_first_and_repeated_sources(self):
        report = self.report(HEADER + ROW + " 101 , Taipei , 1 , 08:00 , 08:05 \n" + ROW)
        duplicates = [d for d in report.diagnostics if d.code == "INPUT_DUPLICATE_ROW"]
        self.assertEqual([d.sources for d in duplicates],
                         [[SourceRange(2, 2), SourceRange(3, 3)],
                          [SourceRange(2, 2), SourceRange(4, 4)]])
        self.assertEqual(duplicates[0].details["records"][1]["values"]["Train"], " 101 ")
        self.assertEqual(report.summary["records_valid"], 1)
        self.assertEqual(report.summary["records_duplicate"], 2)
        self.assertEqual(report.summary["records_error"], 2)
        self.assertIsNone(report.summary["conflicts"])
        self.assertFalse(report.validation_complete)
        self.assertEqual(report.exit_code, 2)

    def test_duplicate_invalid_records_count_once_each_in_error_rows(self):
        report = self.report(HEADER + "101,Taipei,1,bad,08:05\n" * 2)
        self.assertEqual(report.summary["records_read"], 2)
        self.assertEqual(report.summary["records_error"], 2)
        self.assertEqual(report.summary["records_duplicate"], 1)
        self.assertEqual(report.summary["input_errors"], 3)

    def test_multiple_recoverable_errors_collect_and_skip_overlap_algorithm(self):
        text = (HEADER + ROW + "103,Taipei,1,08:03,08:08\n"
                + "104,Taipei,1\n" + ", ,1,bad,25:00\n" + ROW)
        with patch("validator.find_conflicts", side_effect=AssertionError("must not run")):
            report = self.report(text)
        self.assertTrue(report.parsing_complete)
        self.assertFalse(report.validation_complete)
        self.assertEqual(report.summary, {
            "records_read": 5, "records_valid": 2, "records_error": 3,
            "records_duplicate": 1, "total_records": 5, "input_errors": 6,
            "conflicts": None, "warnings": 0,
        })
        self.assertEqual({d.sources[-1].line_start for d in report.diagnostics}, {4, 5, 6})
        self.assertNotIn("No conflicts found.", format_report(report))
        self.assertNotIn("No platform overlaps found", format_report(report))

    def test_broken_quotes_stop_parsing_and_do_not_invent_total(self):
        text = HEADER + ROW + '103,Taipei,1,08:03,08:08\n104,"broken\n' + ROW + ROW
        report = self.report(text)
        self.assertFalse(report.parsing_complete)
        self.assertFalse(report.validation_complete)
        self.assertEqual(report.summary["records_read"], 2)
        self.assertIsNone(report.summary["total_records"])
        self.assertIsNone(report.summary["conflicts"])
        self.assertEqual(report.diagnostics[-1].code, "INPUT_CSV_SYNTAX")
        self.assertEqual(report.diagnostics[-1].sources, [SourceRange(4, 6)])
        self.assertEqual(report.exit_code, 2)
        self.assertFalse(report.checks_executed[0]["complete"])

    def test_bad_characters_after_closing_quote_are_fatal(self):
        report = self.report(HEADER + '101,"Taipei"x,1,08:00,08:05\n' + ROW)
        self.assertEqual(report.diagnostics[0].code, "INPUT_CSV_SYNTAX")
        self.assertEqual(report.diagnostics[0].sources, [SourceRange(2, 2)])
        self.assertEqual(report.summary["records_read"], 0)
        self.assertIsNone(report.summary["total_records"])

    def test_strict_headers_reject_missing_duplicate_extra_and_wrong_case(self):
        for header in ("Train,Station,Platform,Arrival\n",
                       "Train,Station,Platform,Arrival,Arrival\n",
                       HEADER.rstrip() + ",Note\n", HEADER.replace("Train", "train")):
            with self.subTest(header=header):
                report = self.report(header + ROW)
                self.assertEqual(report.diagnostics[0].code, "INPUT_INVALID_HEADER")
                self.assertEqual(report.diagnostics[0].sources, [SourceRange(1, 1)])
                self.assertFalse(report.parsing_complete)
                self.assertIsNone(report.summary["total_records"])
                self.assertEqual(report.exit_code, 2)

    def test_header_only_warns_but_is_complete(self):
        report = self.report(HEADER)
        self.assertEqual(report.exit_code, 0)
        self.assertTrue(report.validation_complete)
        self.assertEqual(report.summary["total_records"], 0)
        self.assertEqual(report.summary["warnings"], 1)
        self.assertEqual(report.diagnostics[0].code, "TIMETABLE_EMPTY")
        self.assertIn("沒有可供檢查的停站資料", format_report(report))
        self.assertNotIn("No platform overlaps found", format_report(report))

    def test_require_data_changes_exit_to_two_and_skips_overlap(self):
        report = self.report(HEADER, require_data=True)
        self.assertEqual(report.exit_code, 2)
        self.assertEqual([d.code for d in report.diagnostics], ["TIMETABLE_EMPTY", "INPUT_DATA_REQUIRED"])
        self.assertFalse(report.validation_complete)
        self.assertIsNone(report.summary["conflicts"])

    def test_empty_file_is_missing_header_error_not_clean_empty_timetable(self):
        report = self.report("")
        self.assertEqual(report.diagnostics[0].code, "INPUT_MISSING_HEADER")
        self.assertEqual(report.exit_code, 2)

    def test_identifiers_are_not_fuzzily_merged_or_converted(self):
        rows = ["101,台北,1,08:00,08:05", "102,臺北,1,08:00,08:05",
                "103,Taipei,1,08:00,08:05", "104,台北,01,08:00,08:05",
                "001,台北,2,08:00,08:05", "1,台北,2,08:00,08:05"]
        report = self.report(HEADER + "\n".join(rows))
        self.assertEqual(report.summary["conflicts"], 1)
        self.assertEqual(set(report.diagnostics[0].trains), {"001", "1"})

    def test_same_train_itinerary_remains_out_of_scope(self):
        report = self.report(HEADER + ROW + "101,Banqiao,1,08:01,08:06\n")
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.summary["conflicts"], 0)

    def test_json_schema_and_original_data_are_not_display_escaped(self):
        station = "台北\n[FORGED]\t\x1b[31m\u2028\u202e東"
        data = io.StringIO(newline="")
        writer = csv.writer(data)
        writer.writerow(["Train", "Station", "Platform", "Arrival", "Departure"])
        writer.writerow([" 001 ", station, "01", "08:00", "08:05"])
        writer.writerow(["103", station, "01", "08:03", "08:08"])
        report = self.report(data.getvalue())
        payload = json.loads(json.dumps(report.to_dict()))
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(payload["tool_version"], __version__)
        diagnostic = payload["diagnostics"][0]
        self.assertEqual(set(diagnostic),
                         {"code", "severity", "message", "source_file", "sources",
                          "trains", "station", "platform", "field", "details"})
        self.assertEqual(diagnostic["station"], station)
        self.assertEqual(diagnostic["details"]["records"][0]["values"]["Train"], " 001 ")
        rendered = format_report(report)
        self.assertIn("台北\\n[FORGED]\\t", rendered)
        self.assertNotIn("\n[FORGED]", rendered)
        for control in ("\x1b", "\t", "\u2028", "\u202e"):
            self.assertNotIn(control, rendered)

    def test_safe_text_escapes_controls_and_backslashes_preserves_chinese(self):
        self.assertEqual(safe_text("台北\\n\n\r\t\x00\x7f\x85\u2029"),
                         "台北\\\\n\\n\\r\\t\\u0000\\u007f\\u0085\\u2029")

    def test_text_and_json_conclusions_and_exit_codes_match(self):
        for content, required, expected in (
            (HEADER + ROW, False, 0),
            (HEADER + ROW + "103,Taipei,1,08:03,08:08\n", False, 1),
            (HEADER + ROW + ROW, False, 2),
            (HEADER + "101,Taipei,1,bad,08:05\n", False, 2),
            (HEADER, False, 0), (HEADER, True, 2),
            (HEADER + '101,"broken\n', False, 2),
        ):
            with self.subTest(content=content, required=required):
                self.write(content)
                flags = ["--require-data"] if required else []
                text = self.cli(self.path, *flags)
                machine = self.cli(self.path, "--format", "json", *flags)
                payload = json.loads(machine.stdout)
                self.assertEqual(text.returncode, expected)
                self.assertEqual(machine.returncode, expected)
                self.assertEqual(machine.stderr, "")
                combined = text.stdout + text.stderr
                self.assertIn("validation_complete=" + str(payload["validation_complete"]).lower(), combined)
                for diagnostic in payload["diagnostics"]:
                    self.assertIn("[" + diagnostic["code"] + "]", combined)
                if expected == 2:
                    self.assertNotIn("No platform overlaps found", combined)

    def test_json_command_line_errors_are_one_document_without_stderr(self):
        for args in (("--format", "json"), ("--format=json", "--unknown"),
                     (str(self.path), "--format", "json", "--require-data=bad"),
                     (str(self.path), "--format", "json", "--unknown\n[FORGED]")):
            with self.subTest(args=args):
                result = self.cli(*args)
                payload = json.loads(result.stdout)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, "")
                self.assertFalse(payload["validation_complete"])
                self.assertEqual(payload["diagnostics"][0]["code"], "CLI_ARGUMENT_ERROR")
                self.assertIsNone(payload["summary"]["total_records"])

    def test_text_command_line_error_cannot_inject_heading(self):
        result = self.cli(self.path, "--unknown\n[FORGED]\x1b[0m")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("\n[FORGED]", result.stderr)
        self.assertNotIn("\x1b", result.stderr)

    def test_invalid_format_is_argument_error(self):
        result = self.cli(self.path, "--format", "html")
        self.assertEqual(result.returncode, 2)
        self.assertIn("[CLI_ARGUMENT_ERROR]", result.stderr)

    def test_json_missing_file_and_encoding_errors(self):
        result = self.cli(self.path, "--format=json")
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload["diagnostics"][0]["code"], "INPUT_READ_ERROR")
        self.assertEqual(payload["diagnostics"][0]["sources"], [])
        self.path.write_bytes(b"\xff\xfe")
        result = self.cli(self.path, "--format=json")
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload["diagnostics"][0]["code"], "INPUT_ENCODING")
        self.assertIsNone(payload["summary"]["total_records"])

    def test_cli_version_matches_report(self):
        result = self.cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), __version__)


class IndependentOracleTests(unittest.TestCase):
    def test_seeded_pairwise_oracle_including_same_train_and_resource_ids(self):
        rng = random.Random(72941)
        for case in range(100):
            stops = []
            for _ in range(rng.randrange(1, 50)):
                start = rng.randrange(1439)
                stops.append(Stop(str(rng.randrange(8)), rng.choice(["台北", "臺北", "Taipei"]),
                                  rng.choice(["1", "01"]), start, rng.randrange(start + 1, 1440)))
            # Independent O(n^2) reference: no calls into the tested rule.
            expected = []
            for index, first in enumerate(stops):
                for second in stops[index + 1:]:
                    if (first.station != second.station or first.platform != second.platform
                            or first.train == second.train):
                        continue
                    start, end = max(first.arrival, second.arrival), min(first.departure, second.departure)
                    if start < end:
                        expected.append((frozenset((id(first), id(second))), start, end))
            rng.shuffle(stops)
            actual = [(frozenset((id(c.first), id(c.second))), c.start, c.end) for c in find_conflicts(stops)]
            with self.subTest(case=case):
                self.assertCountEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
