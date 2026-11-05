"""Archives synthétiques originales ; aucune archive privée ou action matérielle."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import stat
import struct
import tempfile
import unittest
import warnings
import zipfile
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from benchab.controles.paquet_3mf import (
    DEFAULT_LIMITS,
    PROJECT_SETTINGS,
    PacketLimits,
    inspect_packet,
    main,
)


def make_zip(entries: list[tuple[str | zipfile.ZipInfo, bytes]], *, compression: int = zipfile.ZIP_STORED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


class PacketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "synthetic.3mf"

    def write(self, data: bytes) -> Path:
        self.path.write_bytes(data)
        return self.path

    def test_inventory_hashes_crc_and_explicit_project_settings(self) -> None:
        gcode = b"G1 X12 Y5\n"
        settings = b'{"nozzle_diameter":[0.4],"opaque":null}'
        data = make_zip(
            [
                (PROJECT_SETTINGS, settings),
                ("Metadata/plate_1.gcode", gcode),
                ("3D/model.model", b"<model/>"),
                ("Metadata/sequence.json", b"[0,3]"),
            ]
        )
        report = inspect_packet(self.write(data))
        self.assertEqual(report.archive_sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(report.archive_size, len(data))
        self.assertTrue(report.crc_verified)
        self.assertEqual(report.gcode_members, ("Metadata/plate_1.gcode",))
        self.assertEqual(report.json_members, (PROJECT_SETTINGS, "Metadata/sequence.json"))
        self.assertIsNotNone(report.project_settings)
        assert report.project_settings is not None
        self.assertEqual(report.project_settings.keys, ("nozzle_diameter", "opaque"))
        self.assertEqual(report.project_settings.sha256, hashlib.sha256(settings).hexdigest())
        selected = next(member for member in report.members if member.name.endswith(".gcode"))
        self.assertEqual(selected.sha256, hashlib.sha256(gcode).hexdigest())
        self.assertEqual(self.path.read_bytes(), data)
        self.assertEqual(tuple(self.root.iterdir()), (self.path,))
        self.assertFalse(hasattr(report, "ready_to_send"))
        with self.assertRaises(FrozenInstanceError):
            report.crc_verified = False  # type: ignore[misc]

    def test_missing_gcode_or_exact_settings_is_unknown_not_compatibility(self) -> None:
        data = make_zip([("metadata/project_settings.config", b"{}"), ("3D/model.model", b"<model/>")])
        report = inspect_packet(self.write(data))
        self.assertIsNone(report.project_settings)
        self.assertEqual(report.gcode_members, ())
        self.assertTrue(any("absente" in item for item in report.unknowns))
        self.assertTrue(any("Anycubic non établie" in item for item in report.unknowns))

    def test_multiple_gcodes_are_listed_without_selecting_a_plate(self) -> None:
        report = inspect_packet(
            self.write(make_zip([("Metadata/plate_2.gcode", b"T1"), ("Metadata/plate_1.gcode", b"T0")]))
        )
        self.assertEqual(report.gcode_members, ("Metadata/plate_1.gcode", "Metadata/plate_2.gcode"))
        self.assertIsNone(report.project_settings)

    def test_crc_failure_is_refused(self) -> None:
        data = bytearray(make_zip([("plate.gcode", b"G1 X12 Y5")]))
        data[data.index(b"G1 X12 Y5") + 4] ^= 1
        with self.assertRaisesRegex(ValueError, "CRC"):
            inspect_packet(self.write(bytes(data)))

    def test_unsafe_member_paths_are_refused(self) -> None:
        for name in (
            "../escape",
            "/absolute",
            "C:/absolute",
            "folder\\file",
            "./file",
            "a/../b",
            "a//b",
            "a/./b",
            "a\nfile",
            "x" * 513,
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Chemin"):
                inspect_packet(self.write(make_zip([(name, b"x")])))

    def test_duplicate_case_and_unicode_aliases_are_refused(self) -> None:
        for first, second in (
            ("same", "same"),
            ("Metadata/File", "metadata/file"),
            ("é", "e\u0301"),
            ("directory/", "directory"),
        ):
            with self.subTest(names=(first, second)), self.assertRaisesRegex(ValueError, "dupliqués"):
                inspect_packet(self.write(make_zip([(first, b""), (second, b"")])))

    def test_unix_links_and_special_files_are_refused(self) -> None:
        for file_type in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK):
            info = zipfile.ZipInfo("member")
            info.create_system = 3
            info.external_attr = (file_type | 0o700) << 16
            with self.subTest(file_type=file_type), self.assertRaisesRegex(ValueError, "Liens"):
                inspect_packet(self.write(make_zip([(info, b"target")])))
        info = zipfile.ZipInfo("member")
        info.extra = struct.pack("<HH", 0x756E, 0)
        with self.assertRaisesRegex(ValueError, "Métadonnées"):
            inspect_packet(self.write(make_zip([(info, b"target")])))

    def test_empty_directories_are_inventory_members(self) -> None:
        report = inspect_packet(self.write(make_zip([("Metadata/", b""), ("data", b"x")])))
        self.assertTrue(next(member for member in report.members if member.name == "Metadata/").is_directory)
        with self.assertRaisesRegex(ValueError, "répertoire"):
            inspect_packet(self.write(make_zip([("Metadata/", b"x")])))

    def test_encryption_unsupported_compression_and_local_mismatch_are_refused(self) -> None:
        original = make_zip([("file", b"data")])
        for patch in ("encryption", "method", "local"):
            data = bytearray(original)
            local, central = data.index(b"PK\x03\x04"), data.index(b"PK\x01\x02")
            if patch == "encryption":
                struct.pack_into("<H", data, local + 6, 1)
                struct.pack_into("<H", data, central + 8, 1)
            elif patch == "method":
                struct.pack_into("<H", data, local + 8, 99)
                struct.pack_into("<H", data, central + 10, 99)
            else:
                struct.pack_into("<H", data, local + 6, 8)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                inspect_packet(self.write(bytes(data)))

    def test_archive_member_total_count_and_ratio_limits(self) -> None:
        data = make_zip([("one", b"12345"), ("two", b"67890")])
        for limits in (
            replace(DEFAULT_LIMITS, archive_bytes=4),
            replace(DEFAULT_LIMITS, member_bytes=4),
            replace(DEFAULT_LIMITS, total_bytes=9),
            replace(DEFAULT_LIMITS, members=1),
        ):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                inspect_packet(self.write(data), limits=limits)
        bomb = make_zip([("repeated", b"0" * 100_000)], compression=zipfile.ZIP_DEFLATED)
        with self.assertRaisesRegex(ValueError, "ratio"):
            inspect_packet(self.write(bomb))

    def test_invalid_duplicate_nonfinite_and_nonobject_project_json(self) -> None:
        for payload in (
            b"not JSON",
            b'{"same":1,"same":2}',
            b'{"x":NaN}',
            b'{"x":Infinity}',
            b'{"x":1e999}',
            b"[]",
            b'{"x":' + b"1" * 129 + b"}",
            b"\xff",
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                inspect_packet(self.write(make_zip([(PROJECT_SETTINGS, payload)])))
        with self.assertRaisesRegex(ValueError, "JSON"):
            inspect_packet(self.write(make_zip([("other.json", b"{broken")])))

    def test_json_size_depth_and_node_budgets(self) -> None:
        for payload, limits in (
            (b'{"a":1}', replace(DEFAULT_LIMITS, json_bytes=4)),
            (b'{"a":[[1]]}', replace(DEFAULT_LIMITS, json_depth=2)),
            (b'{"a":[1,2,3]}', replace(DEFAULT_LIMITS, json_nodes=3)),
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                inspect_packet(self.write(make_zip([(PROJECT_SETTINGS, payload)])), limits=limits)

    def test_invalid_limits_and_nonlocal_sources(self) -> None:
        for value in (True, 0, -1, float("nan"), float("inf"), 513 * 1024 * 1024):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PacketLimits(archive_bytes=value)  # type: ignore[arg-type]
        target = self.write(make_zip([("file", b"x")]))
        link = self.root / "alias"
        link.symlink_to(target)
        with self.assertRaises(ValueError):
            inspect_packet(link)
        with self.assertRaises(ValueError):
            inspect_packet(self.root)

    def test_truncated_empty_multivolume_and_incoherent_directories(self) -> None:
        original = make_zip([("file", b"x")])
        variants = [b"not a ZIP", original[:-1], original + b"trailing", make_zip([])]
        for field, value in ((4, 1), (8, 2), (10, 2), (12, 2**32 - 1), (16, 1)):
            data = bytearray(original)
            end = data.rfind(b"PK\x05\x06")
            struct.pack_into("<H" if field < 12 else "<L", data, end + field, value)
            variants.append(bytes(data))
        for data in variants:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                inspect_packet(self.write(data))


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source.3mf"
        self.data = make_zip([(PROJECT_SETTINGS, b"{}"), ("Metadata/plate_1.gcode", b"G90")])
        self.source.write_bytes(self.data)

    def invoke(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main([str(self.source), *map(str, arguments)])
        return code, output.getvalue(), errors.getvalue()

    def test_cli_structural_success_is_never_printing_approval(self):
        code, output, errors = self.invoke()
        self.assertEqual(code, 0, errors)
        report = json.loads(output)
        self.assertEqual(report["statut"], "INSPECTION_STRUCTURELLE_TERMINEE")
        self.assertFalse(report["ready_to_send"])
        self.assertFalse(report["physically_validated"])
        self.assertEqual(self.source.read_bytes(), self.data)

    def test_report_published_exclusively_and_source_collision_refused(self):
        report = self.root / "report.json"
        self.assertEqual(self.invoke("--json", report)[0], 0)
        before = report.read_bytes()
        self.assertEqual(json.loads(before)["archive_sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(self.invoke("--json", report)[0], 2)
        self.assertEqual(report.read_bytes(), before)
        self.assertEqual(self.invoke("--json", self.source)[0], 2)
        self.assertEqual(self.source.read_bytes(), self.data)

    def test_ancestor_symlink_and_fifo_refused(self):
        link = self.root / "alias"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            inspect_packet(link / "source.3mf")
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            inspect_packet(fifo)

    def test_large_sparse_archive_refused_before_reading(self):
        with self.source.open("wb") as source:
            source.truncate(65 * 1024**2)
        with self.assertRaisesRegex(ValueError, "bornée"):
            inspect_packet(self.source)

    def test_explicit_budget_extension_and_reduction(self):
        # 300 small members exceed the conservative default without a large fixture.
        self.source.write_bytes(make_zip([(f"item-{i}", b"x") for i in range(300)]))
        self.assertEqual(self.invoke()[0], 2)
        code, output, errors = self.invoke("--membres", 300)
        self.assertEqual(code, 0, errors)
        self.assertEqual(len(json.loads(output)["members"]), 300)
        self.assertEqual(self.invoke("--membres", 299)[0], 2)
        self.assertEqual(self.invoke("--membres", 8193)[0], 2)
        self.assertEqual(self.invoke("--archive-mio", 0)[0], 2)

    def test_refused_archive_does_not_publish_report(self):
        self.source.write_bytes(b"invalid")
        report = self.root / "report.json"
        self.assertEqual(self.invoke("--json", report)[0], 2)
        self.assertFalse(report.exists())


if __name__ == "__main__":
    unittest.main()
