"""Synthetic tests only. Temporary files are created next to this test folder."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_data", REPO / "scripts/check_data.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class CheckDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "work"
        self.root.mkdir()

    def run_file(self, text, kind="csv", filename="input.txt", encoding="utf-8"):
        path = self.root / filename
        path.write_text(text, encoding=encoding, newline="")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = checker.main([kind, filename, "--root", str(self.root)])
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
        return code, json.loads(output.getvalue())

    def test_normal_csv(self):
        code, report = self.run_file("id,value\nA,0\nB,1\n")
        self.assertEqual(code, 0)
        self.assertEqual(report["rows"], 2)
        self.assertEqual(report["missing_by_column"], [0, 0])

    def test_csv_missing_duplicates_and_width(self):
        code, report = self.run_file((REPO / "examples/sample.csv").read_text())
        self.assertEqual(code, 1)
        self.assertEqual(report["rows"], 4)
        self.assertEqual(report["missing_by_column"], [0, 1, 1])
        self.assertEqual(report["duplicate_rows"], 1)
        self.assertEqual(report["bad_width_data_rows_first_10"], [4])

    def test_blank_extra_and_whitespace_cells(self):
        code, report = self.run_file("a,b\n   ,x\n1,2,3\n\n")
        self.assertEqual(code, 1)
        self.assertEqual(report["rows"], 3)
        self.assertEqual(report["missing_by_column"], [2, 1])
        self.assertEqual(report["bad_width_count"], 2)

    def test_empty_and_header_only_csv(self):
        for text in ("", "id,value\n", "\n"):
            with self.subTest(text=text):
                self.assertEqual(self.run_file(text)[0], 1)

    def test_header_problems(self):
        code, report = self.run_file("id,id, \nA,B,C\n")
        self.assertEqual(code, 1)
        self.assertEqual(report["header_issues"]["duplicate_names"], 1)
        self.assertEqual(report["header_issues"]["empty_names"], 1)

    def test_quoted_csv_newline_and_comma(self):
        code, report = self.run_file('id,note\nA,"one,two\nthree"\n')
        self.assertEqual(code, 0)
        self.assertEqual(report["rows"], 1)

    def test_malformed_csv(self):
        self.assertEqual(self.run_file('id,note\nA,"unclosed\n')[0], 1)

    def test_bom_and_unicode(self):
        self.assertEqual(self.run_file("id,label\nA,가상값\n", encoding="utf-8-sig")[0], 0)
        self.assertEqual(self.run_file('{"label":"가상값"}', "json", encoding="utf-8-sig")[0], 0)

    def test_normal_json(self):
        text = (REPO / "examples/sample.json").read_text()
        self.assertEqual(self.run_file(text, "json")[0], 0)
        self.assertEqual(self.run_file("null", "json")[0], 0)

    def test_invalid_json(self):
        for text in ("", '{"a":1,}', "{} {}", '{"a":NaN}', "Infinity", "-Infinity"):
            with self.subTest(text=text):
                self.assertEqual(self.run_file(text, "json")[0], 1)

    def test_json_error_location(self):
        code, report = self.run_file('{\n"a":\n}', "json")
        self.assertEqual(code, 1)
        self.assertEqual(report["line"], 3)
        self.assertEqual(report["column"], 1)

    def test_wrong_encoding(self):
        self.assertEqual(self.run_file("가상값", encoding="utf-16")[0], 2)

    def test_outside_root(self):
        outside = self.base / "outside.json"
        outside.write_text("{}")
        for name in ("../outside.json", str(outside)):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "inside"):
                checker.read_input(str(self.root), name)

    def test_network_paths(self):
        for value in ("//server/share/data.json", "\\\\server\\share\\data.json", "\\\\?\\C:\\file"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Network"):
                checker.read_input(str(self.root), value)
            with self.subTest(root=value), self.assertRaisesRegex(ValueError, "Network"):
                checker.read_input(value, "input.json")

    def test_directory_and_missing_file(self):
        with self.assertRaisesRegex(ValueError, "regular"):
            checker.read_input(str(self.root), ".")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(checker.main(["json", "missing.json", "--root", str(self.root)]), 2)

    def test_file_size_limit(self):
        path = self.root / "large.json"
        path.write_bytes(b" " * (checker.MAX_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "10 MiB"):
            checker.read_input(str(self.root), path.name)

    def test_symlink_outside_root(self):
        outside = self.base / "outside.json"
        outside.write_text("{}")
        link = self.root / "link.json"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("OS did not allow creating a test symlink")
        with self.assertRaisesRegex(ValueError, "inside"):
            checker.read_input(str(self.root), link.name)

    def test_hard_link(self):
        outside = self.base / "outside.json"
        outside.write_text("{}")
        link = self.root / "linked.json"
        try:
            os.link(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("OS did not allow creating a test hard link")
        with self.assertRaisesRegex(ValueError, "Hard-linked"):
            checker.read_input(str(self.root), link.name)


if __name__ == "__main__":
    unittest.main()
