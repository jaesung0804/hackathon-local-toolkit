"""Read one explicitly selected local CSV/JSON; print a report, never edit input."""

import argparse
import csv
import io
import json
from pathlib import Path
import stat
import sys


MAX_BYTES = 10 * 1024 * 1024


def reject_network_path(value):
    if str(value).replace("\\", "/").startswith("//"):
        raise ValueError("Network and Windows device paths are not supported.")


def read_input(root_arg, file_arg):
    reject_network_path(root_arg)
    reject_network_path(file_arg)
    root = Path(root_arg).resolve(strict=True)
    reject_network_path(root)
    if not root.is_dir():
        raise ValueError("--root must be a local directory.")
    candidate = Path(file_arg)
    if not candidate.is_absolute():
        candidate = root / candidate
    path = candidate.resolve(strict=True)
    reject_network_path(path)
    if not path.is_relative_to(root):
        raise ValueError("Input must stay inside --root, including symlink targets.")
    if any(":" in part for part in path.relative_to(root).parts):
        raise ValueError("Alternate data streams are not supported.")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Input must be a regular file.")
    if info.st_nlink > 1:
        raise ValueError("Hard-linked inputs are not supported.")
    if info.st_size > MAX_BYTES:
        raise ValueError("Input exceeds the 10 MiB limit.")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Input exceeds the 10 MiB limit.")
    return path, raw.decode("utf-8-sig")


def check_csv(text):
    # The size cap also bounds one field; the stdlib's smaller default varies.
    previous_limit = csv.field_size_limit(MAX_BYTES)
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(reader, None)
        if header is None:
            return {"ok": False, "empty_file": True, "rows": 0}
        names = [name.strip() for name in header]
        missing = [0] * len(header)
        seen = set()
        rows = duplicate_rows = bad_width_count = 0
        bad_width_samples = []
        for row in reader:
            rows += 1
            if len(row) != len(header):
                bad_width_count += 1
                if len(bad_width_samples) < 10:
                    bad_width_samples.append(rows)
            for index in range(len(header)):
                if index >= len(row) or not row[index].strip():
                    missing[index] += 1
            key = tuple(row)
            if key in seen:
                duplicate_rows += 1
            seen.add(key)
        header_issues = {
            "empty_names": sum(not name for name in names),
            "duplicate_names": len(names) - len(set(names)),
            "no_columns": not header,
        }
        ok = not (rows == 0 or any(header_issues.values()) or
                  any(missing) or duplicate_rows or bad_width_count)
        return {
            "ok": ok, "rows": rows, "columns": len(header),
            "header_only": rows == 0, "header_issues": header_issues,
            "missing_by_column": missing,
            "duplicate_rows": duplicate_rows,
            "bad_width_count": bad_width_count,
            "bad_width_data_rows_first_10": bad_width_samples,
        }
    finally:
        csv.field_size_limit(previous_limit)


def reject_constant(_value):
    raise ValueError("NaN and Infinity are not valid JSON constants.")


def check_json(text):
    try:
        json.loads(text, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": "Invalid JSON syntax.",
                "line": exc.lineno, "column": exc.colno}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "syntax": "valid"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("csv", "json"))
    parser.add_argument("file", help="File path relative to --root, or inside it")
    parser.add_argument("--root", required=True, help="Explicit local work folder")
    args = parser.parse_args(argv)
    try:
        _path, text = read_input(args.root, args.file)
        report = check_csv(text) if args.kind == "csv" else check_json(text)
        report["format"] = args.kind
    except csv.Error:
        report = {"ok": False, "error": "Malformed CSV quoting or field structure."}
    except UnicodeError:
        report = {"ok": False, "error": "Input must be UTF-8 (BOM is supported)."}
        print(json.dumps(report))
        return 2
    except (OSError, ValueError, RuntimeError, RecursionError) as exc:
        # OS errors may include full paths; do not expose them in the report.
        message = str(exc) if isinstance(exc, ValueError) else "Cannot read or parse input."
        print(json.dumps({"ok": False, "error": message}))
        return 2
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
