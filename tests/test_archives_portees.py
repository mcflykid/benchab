"""Régressions d'inventaire, données synthétiques et aucune extraction."""

import hashlib
import io
import os
import stat
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from benchab.qualite import archives as inventory


def zip_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        for name, data in entries:
            archive.writestr(name, data)
    return stream.getvalue()


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "source"
        self.root.mkdir()

    def run_inventory(self):
        return inventory.inventory(self.root)

    def test_swap_source_to_external_symlink_before_open_never_reads_target(self):
        source = self.root / "source"
        source.write_bytes(b"local")
        outside = self.base / "outside"
        outside.write_bytes(b"EXTERNAL_SYNTHETIC")
        original = os.open

        def swap(path, *args, **kwargs):
            if Path(path) == source:
                source.rename(self.base / "original")
                source.symlink_to(outside)
            return original(path, *args, **kwargs)

        with patch.object(inventory.os, "open", side_effect=swap):
            report = self.run_inventory()
        digest = hashlib.sha256(b"EXTERNAL_SYNTHETIC").hexdigest()
        self.assertFalse(any(e.get("sha256") == digest for e in report["entries"]))
        self.assertTrue(report["inventory_errors"])

    def test_archive_uses_already_open_descriptor_after_path_replacement(self):
        source = self.root / "source.zip"
        source.write_bytes(zip_bytes([("local", b"LOCAL")]))
        outside = self.base / "outside.zip"
        outside.write_bytes(zip_bytes([("external", b"EXTERNAL_SYNTHETIC")]))
        original = inventory.inspect_archive

        def swap(stream, *args, **kwargs):
            source.rename(self.base / "original.zip")
            source.symlink_to(outside)
            return original(stream, *args, **kwargs)

        with patch.object(inventory, "inspect_archive", side_effect=swap):
            report = self.run_inventory()
        members = [e["path"] for e in report["entries"] if "!/" in e["path"]]
        self.assertEqual(members, ["source.zip!/local"])
        self.assertTrue(next(e for e in report["entries"] if e["path"] == "source.zip")["errors"])

    def test_magic_detected_swu_and_nested_extensionless_zip_are_inspected(self):
        nested = zip_bytes([("version", b"2.7.2.7")])
        raw = zip_bytes([("inner-no-extension", nested)])
        source = self.root / "firmware.swu"
        source.write_bytes(raw)
        report = self.run_inventory()
        entries = {entry["path"]: entry for entry in report["entries"]}
        inner = entries["firmware.swu!/inner-no-extension!/version"]
        self.assertEqual(inner["sha256"], hashlib.sha256(b"2.7.2.7").hexdigest())
        self.assertEqual(inner["type"], "archive_file")
        self.assertEqual(source.read_bytes(), raw)
        self.assertEqual(list(self.root.iterdir()), [source])

    def test_zip_link_target_is_hashed_without_being_followed(self):
        outside = self.base / "outside"
        outside.write_bytes(b"PRIVATE_SYNTHETIC_NOT_TO_BE_READ")
        name = zipfile.ZipInfo("link")
        name.create_system = 3
        name.external_attr = (stat.S_IFLNK | 0o777) << 16
        target = b"../outside"
        (self.root / "links.zip").write_bytes(zip_bytes([(name, target)]))
        entries = self.run_inventory()["entries"]
        link = next(entry for entry in entries if entry["path"] == "links.zip!/link")
        self.assertEqual(link["type"], "archive_link")
        self.assertEqual(link["target"], "../outside")
        self.assertEqual(link["sha256"], hashlib.sha256(target).hexdigest())
        self.assertEqual(link["sha256_scope"], "link_text")
        self.assertNotIn(hashlib.sha256(outside.read_bytes()).hexdigest(), [entry["sha256"] for entry in entries])

    def test_zip_special_members_are_not_regular_files(self):
        entries = []
        for name, mode in [("fifo", stat.S_IFIFO), ("socket", stat.S_IFSOCK), ("device", stat.S_IFCHR)]:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (mode | 0o600) << 16
            entries.append((info, b""))
        (self.root / "special.zip").write_bytes(zip_bytes(entries))
        members = [entry for entry in self.run_inventory()["entries"] if "!/" in entry["path"]]
        self.assertEqual([entry["type"] for entry in members], ["archive_special"] * 3)
        self.assertTrue(all(entry["sha256"] is None for entry in members))

    def test_unsafe_archive_paths_are_visible_but_never_extracted(self):
        names = ["../outside", "/absolute", "C:/outside", "a//b", "a\\b", "a\x7fb"]
        for name in names:
            self.assertFalse(inventory.safe_member(name), name)
        source = self.root / "paths.zip"
        source.write_bytes(zip_bytes([(name, b"x") for name in names]))
        members = [entry for entry in self.run_inventory()["entries"] if "!/" in entry["path"]]
        self.assertEqual(len(members), len(names))
        self.assertTrue(all(entry["errors"] and not entry["safe_path"] for entry in members))
        self.assertEqual(list(self.root.iterdir()), [source])

    def test_root_and_ancestor_symlinks_are_refused(self):
        (self.root / "file").write_bytes(b"content")
        alias = self.base / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        child = self.root / "child"
        child.mkdir()
        for path in (alias, alias / "child"):
            with self.subTest(path=str(path)), self.assertRaises(ValueError):
                inventory.inventory(path)

    def test_hidden_files_empty_directories_and_external_links_are_inventoried(self):
        (self.root / ".hidden").write_bytes(b"content")
        (self.root / "copy").write_bytes(b"content")
        (self.root / "empty").mkdir()
        (self.root / "external").symlink_to("../outside-not-created", target_is_directory=True)
        report = self.run_inventory()
        entries = {entry["path"]: entry for entry in report["entries"]}
        self.assertEqual(set(entries), {".hidden", "copy", "empty", "external"})
        self.assertEqual(entries["empty"]["type"], "directory")
        self.assertTrue(entries["external"]["external_target"])
        self.assertEqual(entries["external"]["sha256_scope"], "link_text")
        digest = hashlib.sha256(b"content").hexdigest()
        self.assertEqual(set(report["identical_groups"][digest]), {".hidden", "copy"})
        self.assertEqual(report["empty_directories"], ["empty"])
        self.assertEqual(report["zone_sizes"]["."], {"fichiers": 2, "octets": 14})
        self.assertEqual(report["octets_fichiers_physiques"], 14)

    def test_empty_archive_directory_and_nested_zone_sizes(self):
        zone = self.root / "projet"
        zone.mkdir()
        (zone / "data.bin").write_bytes(b"abc")
        (self.root / "vide").mkdir()
        archive = zipfile.ZipInfo("empty/")
        archive.external_attr = (stat.S_IFDIR | 0o755) << 16
        (self.root / "archive.zip").write_bytes(zip_bytes([(archive, b"")]))
        report = self.run_inventory()
        self.assertEqual(report["empty_directories"], ["archive.zip!/empty", "vide"])
        self.assertEqual(report["zone_sizes"]["projet"], {"fichiers": 1, "octets": 3})
        self.assertEqual(report["octets_fichiers_physiques"], (self.root / "archive.zip").stat().st_size + 3)

    def test_duplicate_member_paths_keep_every_occurrence(self):
        (self.root / "duplicates.zip").write_bytes(zip_bytes([("same", b"a"), ("same", b"b")]))
        members = [entry for entry in self.run_inventory()["entries"] if entry["type"] == "archive_file"]
        self.assertEqual(len(members), 2)
        self.assertEqual([entry["member_index"] for entry in members], [1, 2])
        self.assertNotEqual(members[0]["sha256"], members[1]["sha256"])
        self.assertIn("nom de membre dupliqué", members[1]["errors"])

    def test_unreadable_archive_is_explicit(self):
        (self.root / "broken.zip").write_bytes(b"not a ZIP")
        report = self.run_inventory()
        self.assertEqual(report["counts"]["file"], 1)
        errors = [entry for entry in report["entries"] if entry["type"] == "archive_error"]
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0]["errors"])
        self.assertEqual(errors[0]["analysis"], "unreadable")

    def test_member_and_depth_budgets_are_explicit(self):
        nested = zip_bytes([("entry", b"abc")])
        (self.root / "bounds.zip").write_bytes(zip_bytes([("large", b"x" * 1000), ("inner.zip", nested)]))
        with patch.object(inventory, "MAX_MEMBER", 512), patch.object(inventory, "MAX_DEPTH", 0):
            entries = {entry["path"]: entry for entry in self.run_inventory()["entries"]}
        self.assertIsNone(entries["bounds.zip!/large"]["sha256"])
        self.assertTrue(entries["bounds.zip!/large"]["errors"])
        self.assertEqual(entries["bounds.zip!/large"]["inspection"], "incomplete")
        self.assertTrue(entries["bounds.zip!/inner.zip"]["sha256"])
        self.assertIn("archive imbriquée: profondeur limite", entries["bounds.zip!/inner.zip"]["errors"])
        self.assertEqual(entries["bounds.zip!/inner.zip"]["inspection"], "incomplete")
        self.assertNotIn("bounds.zip!/inner.zip!/entry", entries)

    def test_tar_members_and_links_are_listed_without_extraction(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            file = tarfile.TarInfo("payload")
            file.size = 3
            archive.addfile(file, io.BytesIO(b"abc"))
            link = tarfile.TarInfo("alias")
            link.type = tarfile.SYMTYPE
            link.linkname = "../outside"
            archive.addfile(link)
        source = self.root / "payload.tar.gz"
        source.write_bytes(stream.getvalue())
        entries = {entry["path"]: entry for entry in self.run_inventory()["entries"]}
        self.assertEqual(entries["payload.tar.gz!/payload"]["sha256"], hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(entries["payload.tar.gz!/alias"]["type"], "archive_link")
        self.assertEqual(entries["payload.tar.gz!/alias"]["target"], "../outside")
        self.assertEqual(list(self.root.iterdir()), [source])


if __name__ == "__main__":
    unittest.main()


def test_nettoyage_simule_ne_retient_que_finder_et_doublon_exact(tmp_path):
    """Liste des octets théoriques sans suppression ; un backup unique n'est jamais présenté comme libérable."""
    from benchab.qualite.archives import inventory

    (tmp_path / ".DS_Store").write_bytes(b"finder")
    (tmp_path / "reference.txt").write_bytes(b"copie exacte")
    (tmp_path / ".avant-tranchage-a").write_bytes(b"copie exacte")
    (tmp_path / ".avant-tranchage-unique").write_bytes(b"unique")
    (tmp_path / ".avant-tranchage-b").write_bytes(b"deux backups")
    (tmp_path / ".avant-tranchage-c").write_bytes(b"deux backups")
    avant = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    rapport = inventory(tmp_path, archives=False)["nettoyage_simule"]
    noms = {p["chemin"] for p in rapport["candidats"]}
    assert noms == {".DS_Store", ".avant-tranchage-a", ".avant-tranchage-c"}
    assert rapport["simulation"] and not rapport["suppression_possible_par_cet_outil"]
    assert rapport["gain_reel_octets"] == 0
    assert all(p["accord_requis"] for p in rapport["candidats"])
    assert sum(g["gain_theorique_maximal_octets"] for g in rapport["groupes_doublons_physiques"]) == 24
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == avant
