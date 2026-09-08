#!/usr/bin/env python3
"""Check exclusive platform resources in a same-day CSV timetable."""

import argparse
import csv
from dataclasses import asdict, dataclass, field as dc_field
import json
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence


__version__ = "0.2.0"  # Source version; this is not a PyPI release.
SCHEMA_VERSION = "1.0"
FIELDS = ("Train", "Station", "Platform", "Arrival", "Departure")


class TimetableError(ValueError):
    """A timetable does not conform to the supported CSV format."""


@dataclass(frozen=True)
class SourceRange:
    line_start: int
    line_end: int


@dataclass(frozen=True)
class Stop:
    train: str
    station: str
    platform: str
    arrival: int
    departure: int
    source_file: Optional[str] = dc_field(default=None, compare=False)
    source: Optional[SourceRange] = dc_field(default=None, compare=False)
    raw_fields: Dict[str, str] = dc_field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Conflict:
    first: Stop
    second: Stop
    start: int
    end: int


@dataclass
class Diagnostic:
    code: str
    severity: str
    message: str
    source_file: Optional[str]
    sources: List[SourceRange] = dc_field(default_factory=list)
    trains: List[str] = dc_field(default_factory=list)
    station: Optional[str] = None
    platform: Optional[str] = None
    field: Optional[str] = None
    details: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class InputResult:
    source_file: str
    stops: List[Stop] = dc_field(default_factory=list)
    diagnostics: List[Diagnostic] = dc_field(default_factory=list)
    parsing_complete: bool = False
    header_valid: bool = False
    records_read: int = 0
    records_error: int = 0
    records_duplicate: int = 0


@dataclass
class Report:
    source_file: Optional[str]
    validation_complete: bool
    parsing_complete: bool
    summary: Dict[str, Any]
    checks_executed: List[Dict[str, Any]]
    checks_skipped: List[Dict[str, str]]
    diagnostics: List[Diagnostic]
    schema_version: str = SCHEMA_VERSION
    tool_version: str = __version__

    @property
    def exit_code(self) -> int:
        if not self.validation_complete:
            return 2
        return 1 if self.summary["conflicts"] else 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_time(value: str) -> int:
    """Convert a 24-hour HH:MM value into minutes since midnight."""
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise TimetableError(f"invalid time {value!r}; expected HH:MM (00:00-23:59)")
    hours, minutes = map(int, value.split(":"))
    return hours * 60 + minutes


def format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class RecordLines:
    """Retain exactly the physical lines consumed by the next csv.reader call.

    Blank lines are classified AFTER parsing, never removed from inside quotes.
    line_num is the physical line count, not a logical record number.
    """

    def __init__(self, stream):
        self.stream = stream
        self.line_num = 0
        self.lines = []

    def __iter__(self):
        return self

    def __next__(self):
        line = next(self.stream)
        self.line_num += 1
        self.lines.append(line)
        return line


def record_details(source: SourceRange, raw_fields: Dict[str, str]) -> Dict[str, Any]:
    return {"source": asdict(source), "values": raw_fields}


def validate_row(row: List[str], header: List[str], source: SourceRange,
                 result: InputResult, seen: Dict) -> None:
    result.records_read += 1
    if len(row) != len(FIELDS):
        result.records_error += 1
        result.diagnostics.append(Diagnostic(
            "INPUT_COLUMN_COUNT", "error", f"expected 5 values, got {len(row)}",
            result.source_file, [source],
            details={"expected": 5, "actual": len(row), "values": row},
        ))
        return

    raw = dict(zip(header, row))
    values = {key: value.strip() for key, value in raw.items()}
    record = record_details(source, raw)
    key = tuple(values[name] for name in FIELDS)
    problems = []

    def problem(code, message, input_field=None, details=None, sources=None):
        problems.append(Diagnostic(
            code, "error", message, result.source_file,
            sources if sources is not None else [source],
            [values["Train"]], values["Station"], values["Platform"], input_field,
            details if details is not None else {"records": [record]},
        ))

    if key in seen:
        original_source, original_record = seen[key]
        result.records_duplicate += 1
        problem("INPUT_DUPLICATE_ROW", "duplicate stop record after trimming field whitespace",
                details={"records": [original_record, record]},
                sources=[original_source, source])
    else:
        seen[key] = (source, record)

    times = {}
    for name in FIELDS:
        if not values[name]:
            problem("INPUT_EMPTY_FIELD", f"{name} must not be empty", name)
        elif name in ("Arrival", "Departure"):
            try:
                times[name] = parse_time(values[name])
            except TimetableError as error:
                problem("INPUT_INVALID_TIME", str(error), name)

    if len(times) == 2 and times["Departure"] <= times["Arrival"]:
        problem("INPUT_INVALID_INTERVAL",
                "Departure must be later than Arrival on the same day; "
                "overnight and zero-duration stops are not supported", "Departure")

    if problems:
        result.records_error += 1
        result.diagnostics.extend(problems)
    else:
        result.stops.append(Stop(
            values["Train"], values["Station"], values["Platform"],
            times["Arrival"], times["Departure"], result.source_file, source, raw,
        ))


def read_timetable(path: Path) -> InputResult:
    """Collect recoverable row errors; stop at untrustworthy record boundaries."""
    result = InputResult(str(path))
    seen = {}
    tracker = None
    start = 1
    try:
        with open(path, encoding="utf-8-sig", newline="") as stream:
            tracker = RecordLines(stream)
            reader = csv.reader(tracker, strict=True)
            header = None
            while True:
                start = tracker.line_num + 1
                tracker.lines = []
                try:
                    row = next(reader)
                except StopIteration:
                    result.parsing_complete = True
                    break
                source = SourceRange(start, tracker.line_num)
                # Only raw, unquoted physical whitespace lines can be ignored.
                if len(tracker.lines) == 1 and not tracker.lines[0].strip(" \t\r\n"):
                    continue
                if header is None:
                    header = [value.strip() for value in row]
                    if len(header) != len(FIELDS) or set(header) != set(FIELDS):
                        result.diagnostics.append(Diagnostic(
                            "INPUT_INVALID_HEADER", "error",
                            "CSV must contain each column exactly once: " + ",".join(FIELDS),
                            result.source_file, [source],
                            details={"expected": list(FIELDS), "actual": row},
                        ))
                        break
                    result.header_valid = True
                else:
                    validate_row(row, header, source, result, seen)
            if header is None:
                result.diagnostics.append(Diagnostic(
                    "INPUT_MISSING_HEADER", "error", "CSV is empty; expected header: " + ",".join(FIELDS),
                    result.source_file, details={"expected": list(FIELDS)},
                ))
    except csv.Error as error:
        result.diagnostics.append(Diagnostic(
            "INPUT_CSV_SYNTAX", "error", f"invalid CSV: {error}; parsing stopped",
            result.source_file, [SourceRange(start, tracker.line_num)],
            details={"record_boundary_reliable": False},
        ))
    except UnicodeError as error:
        # TextIO may decode ahead. Do not invent an offending physical line.
        result.diagnostics.append(Diagnostic(
            "INPUT_ENCODING", "error", "file must be UTF-8; parsing stopped",
            result.source_file, details={"reason": str(error), "location_known": False},
        ))
    except OSError as error:
        result.diagnostics.append(Diagnostic(
            "INPUT_READ_ERROR", "error", "could not read the input file",
            result.source_file, details={"reason": str(error), "location_known": False},
        ))
    return result


def load_timetable(path: Path) -> List[Stop]:
    """Compatibility API: collect errors, then raise instead of returning partial data."""
    result = read_timetable(path)
    if result.diagnostics:
        messages = []
        for diagnostic in result.diagnostics:
            location = ""
            if diagnostic.sources:
                source = diagnostic.sources[-1]
                location = (f"line {source.line_start}: " if source.line_start == source.line_end
                            else f"lines {source.line_start}-{source.line_end}: ")
            messages.append(location + diagnostic.message)
        raise TimetableError("\n".join(messages))
    return result.stops


def find_conflicts(stops: Iterable[Stop]) -> List[Conflict]:
    """Find every overlapping pair of distinct trains, including nested intervals.

    Occupancy is [arrival, departure), so touching intervals do not conflict.
    Station, platform, and train identifiers are case-sensitive strings.
    """
    groups = {}
    for stop in stops:
        groups.setdefault((stop.station, stop.platform), []).append(stop)

    conflicts = []
    for key in sorted(groups):
        active = []
        for current in sorted(groups[key], key=lambda stop: (stop.arrival, stop.departure, stop.train)):
            active = [previous for previous in active if previous.departure > current.arrival]
            for previous in active:
                if previous.train != current.train:
                    conflicts.append(Conflict(
                        previous, current, current.arrival,
                        min(previous.departure, current.departure),
                    ))
            active.append(current)
    return conflicts


def overlap_diagnostic(conflict: Conflict) -> Diagnostic:
    first, second = conflict.first, conflict.second
    return Diagnostic(
        "PLATFORM_OVERLAP", "error", "distinct trains overlap on this exclusive platform resource",
        first.source_file, [first.source, second.source],
        [first.train, second.train], first.station, first.platform,
        details={
            "overlap_start": format_time(conflict.start),
            "overlap_end": format_time(conflict.end),
            "overlap_minutes": conflict.end - conflict.start,
            "records": [record_details(stop.source, stop.raw_fields) for stop in (first, second)],
        },
    )


def validate_timetable(path: Path, require_data: bool = False) -> Report:
    result = read_timetable(path)
    diagnostics = list(result.diagnostics)
    if result.parsing_complete and result.header_valid and not result.stops:
        diagnostics.append(Diagnostic(
            "TIMETABLE_EMPTY", "warning", "沒有可供檢查的停站資料",
            result.source_file,
        ))
    if require_data and not result.stops:
        diagnostics.append(Diagnostic(
            "INPUT_DATA_REQUIRED", "error", "at least one valid stop is required",
            result.source_file, details={"require_data": True},
        ))

    input_errors = sum(d.severity == "error" for d in diagnostics)
    complete = result.parsing_complete and not input_errors
    executed = [{"code": "CSV_INPUT", "complete": result.parsing_complete}]
    skipped = []
    if result.header_valid:
        executed.append({"code": "DUPLICATE_ROWS", "complete": result.parsing_complete})
    else:
        skipped.append({"code": "DUPLICATE_ROWS", "reason": "header not valid"})
    conflicts = None
    if complete:
        overlaps = find_conflicts(result.stops)
        conflicts = len(overlaps)
        diagnostics.extend(overlap_diagnostic(c) for c in overlaps)
        executed.append({"code": "PLATFORM_OVERLAP", "complete": True})
    else:
        skipped.append({"code": "PLATFORM_OVERLAP", "reason": "input invalid or incomplete"})
    return Report(
        result.source_file, complete, result.parsing_complete,
        {
            "records_read": result.records_read,
            "records_valid": len(result.stops),
            "records_error": result.records_error,
            "records_duplicate": result.records_duplicate,
            "total_records": result.records_read if result.parsing_complete else None,
            "input_errors": input_errors,
            "conflicts": conflicts,
            "warnings": sum(d.severity == "warning" for d in diagnostics),
        },
        executed, skipped, diagnostics,
    )


def safe_text(value: Any) -> str:
    """Escape line/control/format characters for display only; retain normal CJK."""
    output = []
    escapes = {"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for character in str(value):
        if character in escapes:
            output.append(escapes[character])
        elif unicodedata.category(character).startswith("C") or character in ("\u2028", "\u2029"):
            number = ord(character)
            output.append(f"\\u{number:04x}" if number <= 0xffff else f"\\U{number:08x}")
        else:
            output.append(character)
    return "".join(output)


def format_conflict(conflict: Conflict) -> str:
    """Legacy plain conflict block; the CLI uses the unified diagnostic report."""
    return (
        "[CONFLICT]\n"
        f"Station: {safe_text(conflict.first.station)}\n"
        f"Platform: {safe_text(conflict.first.platform)}\n"
        f"Train {safe_text(conflict.first.train)} overlaps with Train {safe_text(conflict.second.train)}\n"
        f"Overlap: {format_time(conflict.start)} - {format_time(conflict.end)}"
    )


def format_report(report: Report) -> str:
    blocks = []
    for diagnostic in report.diagnostics:
        lines = [f"[{diagnostic.code}] {diagnostic.severity}",
                 f"Message: {safe_text(diagnostic.message)}"]
        if diagnostic.source_file is not None:
            lines.append(f"File: {safe_text(diagnostic.source_file)}")
        if diagnostic.sources:
            lines.append("Sources: " + ", ".join(
                f"line {s.line_start}" if s.line_start == s.line_end
                else f"lines {s.line_start}-{s.line_end}" for s in diagnostic.sources
            ))
        for label, value in (("Station", diagnostic.station), ("Platform", diagnostic.platform),
                             ("Field", diagnostic.field)):
            if value is not None:
                lines.append(f"{label}: {safe_text(value)}")
        if diagnostic.trains:
            lines.append("Trains: " + ", ".join(safe_text(t) for t in diagnostic.trains))
        if diagnostic.code == "PLATFORM_OVERLAP":
            details = diagnostic.details
            lines.append(f"Overlap: {details['overlap_start']} - {details['overlap_end']} "
                         f"({details['overlap_minutes']} minutes)")
        elif diagnostic.details:
            # JSON string encoding plus display escaping prevents terminal injection.
            lines.append("Details: " + safe_text(json.dumps(diagnostic.details, ensure_ascii=False)))
        blocks.append("\n".join(lines))

    summary = report.summary
    if not report.validation_complete:
        conclusion = "[ERROR] Input invalid or incomplete; platform overlap check was not performed."
    elif not summary["records_valid"]:
        conclusion = "沒有可供檢查的停站資料。"
    elif summary["conflicts"]:
        conclusion = f"Found {summary['conflicts']} platform overlap(s) in the provided data under enabled rules."
    else:
        conclusion = "No platform overlaps found in the provided data under enabled rules."
    status = ("validation_complete=" + str(report.validation_complete).lower()
              + "; parsing_complete=" + str(report.parsing_complete).lower())
    counts = "Summary: " + ", ".join(
        f"{key}={'unknown/not checked' if value is None else value}" for key, value in summary.items()
    )
    checks = "Checks executed: " + ", ".join(
        f"{c['code']} (complete={str(c['complete']).lower()})" for c in report.checks_executed
    )
    skipped = "Checks skipped: " + (", ".join(
        f"{c['code']} ({c['reason']})" for c in report.checks_skipped
    ) or "none")
    scope = ("Scope: enabled rules and provided data only; no train itinerary, vehicle, "
             "signalling or real-world operational safety assessment.")
    return "\n\n".join([conclusion, *blocks, status, counts, checks, skipped, scope])


class CommandLineError(ValueError):
    pass


class ReportArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise CommandLineError(message)


def wants_json(argv: Sequence[str]) -> bool:
    """Recover the selected output format even when other arguments are invalid."""
    selected = "text"
    for index, value in enumerate(argv):
        if value == "--":
            break
        if value.startswith("--format="):
            selected = value.split("=", 1)[1]
        elif value == "--format" and index + 1 < len(argv):
            selected = argv[index + 1]
    return selected == "json"


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = ReportArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("csv_file", type=Path, help="path to a UTF-8 CSV timetable")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="report format")
    parser.add_argument("--require-data", action="store_true", help="require at least one valid stop")
    parser.add_argument("--version", action="version", version=__version__)
    try:
        args = parser.parse_args(argv)
    except CommandLineError as error:
        report = Report(None, False, False,
                        {"records_read": 0, "records_valid": 0, "records_error": 0,
                         "records_duplicate": 0, "total_records": None,
                         "input_errors": 1, "conflicts": None, "warnings": 0}, [],
                        [{"code": code, "reason": "invalid command line"}
                         for code in ("CSV_INPUT", "DUPLICATE_ROWS", "PLATFORM_OVERLAP")],
                        [Diagnostic("CLI_ARGUMENT_ERROR", "error", str(error), None)])
        output_format = "json" if wants_json(argv) else "text"
        if output_format == "text":
            # Usage is trusted; untrusted argument values go through safe_text below.
            print(parser.format_usage(), file=sys.stderr, end="")
    else:
        report = validate_timetable(args.csv_file, args.require_data)
        output_format = args.format
    if output_format == "json":
        print(json.dumps(report.to_dict(), ensure_ascii=True, indent=2))
    else:
        print(format_report(report), file=sys.stderr if report.exit_code == 2 else sys.stdout)
    return report.exit_code


if __name__ == "__main__":
    # UTF-8 reports also work when redirected on Windows. Escaping is display-only.
    for output_stream in (sys.stdout, sys.stderr):
        output_stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main())
