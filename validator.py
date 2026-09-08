#!/usr/bin/env python3
"""Validate same-day CSV railway timetables using only the standard library."""

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Iterable, List, Optional, Sequence


FIELDS = ("Train", "Station", "Platform", "Arrival", "Departure")


class TimetableError(ValueError):
    """A timetable does not conform to the supported CSV format."""


@dataclass(frozen=True)
class Stop:
    train: str
    station: str
    platform: str
    arrival: int
    departure: int


@dataclass(frozen=True)
class Conflict:
    first: Stop
    second: Stop
    start: int
    end: int


def parse_time(value: str) -> int:
    """Convert a 24-hour HH:MM value into minutes since midnight."""
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise TimetableError(f"invalid time {value!r}; expected HH:MM (00:00-23:59)")
    hours, minutes = map(int, value.split(":"))
    return hours * 60 + minutes


def format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def load_timetable(path: Path) -> List[Stop]:
    """Read UTF-8 CSV (optional BOM), reporting invalid input with line numbers."""
    stops = []
    with open(path, encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        try:
            header = next(reader, None)
            if header is None:
                raise TimetableError("CSV is empty; expected header: " + ",".join(FIELDS))
            header = [field.strip() for field in header]
            if len(header) != len(FIELDS) or set(header) != set(FIELDS):
                raise TimetableError("CSV must contain each column exactly once: " + ",".join(FIELDS))
            for row in reader:
                if not row:
                    continue
                line = reader.line_num
                if len(row) != len(FIELDS):
                    raise TimetableError(f"line {line}: expected {len(FIELDS)} values, got {len(row)}")
                values = dict(zip(header, (value.strip() for value in row)))
                for field, value in values.items():
                    if not value:
                        raise TimetableError(f"line {line}: {field} must not be empty")
                try:
                    arrival = parse_time(values["Arrival"])
                    departure = parse_time(values["Departure"])
                except TimetableError as error:
                    raise TimetableError(f"line {line}: {error}") from error
                if departure <= arrival:
                    raise TimetableError(
                        f"line {line}: Departure must be later than Arrival on the same day; "
                        "overnight and zero-duration stops are not supported"
                    )
                stops.append(Stop(values["Train"], values["Station"], values["Platform"], arrival, departure))
        except csv.Error as error:
            raise TimetableError(f"line {reader.line_num}: invalid CSV: {error}") from error
    return stops


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


def format_conflict(conflict: Conflict) -> str:
    return (
        "[CONFLICT]\n"
        f"Station: {conflict.first.station}\n"
        f"Platform: {conflict.first.platform}\n"
        f"Train {conflict.first.train} overlaps with Train {conflict.second.train}\n"
        f"Overlap: {format_time(conflict.start)} - {format_time(conflict.end)}"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path, help="path to a UTF-8 CSV timetable")
    args = parser.parse_args(argv)
    try:
        conflicts = find_conflicts(load_timetable(args.csv_file))
    except (OSError, UnicodeError, TimetableError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 2
    if conflicts:
        print("\n\n".join(format_conflict(conflict) for conflict in conflicts))
        return 1
    print("No conflicts found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
