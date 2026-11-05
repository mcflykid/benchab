"""Sauvegarde/restauration sur fixtures ; aucun accès aux données de l'atelier."""

import hashlib
import io
import json
import os
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchab.donnees import sauvegarde as S
from benchab.donnees.base import Base
from benchab.donnees.sauvegarde import MANIFEST_NAME, creer, restaurer


class SauvegardeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.racine = Path(self.tmp.name).resolve()
        self.base = Base(self.racine / "prive")
        self.addCleanup(self.base.fermer)
        self.source = self.base.prive / "projets" / "fixture"
        self.source.mkdir()

    def _archive(self, path, membres, *, manifeste=True):
        inventaire = []
        with tarfile.open(path, "w") as tar:
            for nom, genre, payload in membres:
                item = tarfile.TarInfo(nom)
                if genre == "file":
                    item.size = len(payload)
                    item.mode = 0o600
                    tar.addfile(item, io.BytesIO(payload))
                    inventaire.append(
                        {
                            "path": nom,
                            "type": "file",
                            "size": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "mode": 0o600,
                        }
                    )
                elif genre == "directory":
                    item.type = tarfile.DIRTYPE
                    tar.addfile(item)
                    inventaire.append({"path": nom, "type": "directory", "mode": 0o700})
                else:
                    item.type = tarfile.SYMTYPE if genre == "symlink" else tarfile.LNKTYPE
                    item.linkname = payload
                    tar.addfile(item)
                    inventaire.append({"path": nom, "type": genre, "target": payload})
            if manifeste:
                data = json.dumps({"schema": 1, "source": "fixture", "files": inventaire}).encode()
                manifest_item = tarfile.TarInfo(MANIFEST_NAME)
                manifest_item.size = len(data)
                tar.addfile(manifest_item, io.BytesIO(data))
        return path

    def test_explicit_fixture_round_trip_streamed_sha_and_canonical_sqlite_row(self):
        (self.source / "note.txt").write_text("donnée synthétique", encoding="utf-8")
        self.base.ecrire(
            "INSERT INTO projets (id, nom, origine, etat, donnees_json) VALUES (?, ?, ?, ?, ?)",
            ("fixture-projet", "Projet fixture", "tests", "actif", "{}"),
        )
        archive = self.racine / "fixture.tar"
        resultat = creer(self.base, self.base.prive, archive)
        self.assertGreater(resultat["files"], 1)
        cible = self.base.prive / "restauration-neuve"
        resultat = restaurer(archive, cible, base=self.base)
        self.assertTrue(resultat["verified"])
        self.assertEqual((cible / "projets/fixture/note.txt").read_text(encoding="utf-8"), "donnée synthétique")
        connexion = sqlite3.connect(cible / "benchab.sqlite")
        try:
            self.assertEqual(connexion.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertEqual(
                connexion.execute("SELECT nom FROM projets WHERE id = ?", ("fixture-projet",)).fetchone(),
                ("Projet fixture",),
            )
        finally:
            connexion.close()

    def test_sqlite_snapshot_omits_sidecars(self):
        tenue = sqlite3.connect(self.base.prive / "benchab.sqlite")
        tenue.execute("PRAGMA journal_mode = WAL")
        tenue.execute(
            "INSERT INTO projets (id, nom, origine, etat, donnees_json) VALUES (?, ?, ?, ?, ?)",
            ("wal-fixture", "WAL fixture", "tests", "actif", "{}"),
        )
        tenue.commit()
        self.base.fermer()
        archive = self.racine / "database-fixture.tar"
        try:
            self.assertTrue((self.base.prive / "benchab.sqlite-wal").exists())
            creer(self.base, self.base.prive, archive)
            with tarfile.open(archive, "r:") as tar:
                noms = tar.getnames()
                self.assertIn("benchab.sqlite", noms)
                self.assertFalse(any(nom.endswith(("-wal", "-shm", "-journal")) for nom in noms))
        finally:
            tenue.close()

    def test_internal_symlink_and_hardlink_to_regular_file_are_restored(self):
        archive = self._archive(
            self.racine / "links.tar",
            [
                ("root/f", "file", b"ok"),
                ("root/l", "symlink", "f"),
                ("root/h", "hardlink", "root/f"),
            ],
        )
        cible = self.base.prive / "restore-links"
        restaurer(archive, cible, base=self.base)
        self.assertEqual((cible / "root/f").read_bytes(), b"ok")
        self.assertTrue((cible / "root/l").is_symlink())
        self.assertEqual((cible / "root/l").read_bytes(), b"ok")
        self.assertEqual((cible / "root/f").stat().st_ino, (cible / "root/h").stat().st_ino)

    def test_source_seven_dangerous_archive_cases_refuse_before_destination(self):
        cases = [
            [("safe", "file", b"ok"), ("../sortie", "file", b"no")],
            [("escape", "symlink", ".."), ("escape/sortie", "file", b"no")],
            [("escape", "symlink", "/tmp"), ("escape/sortie", "file", b"no")],
            [("a", "hardlink", "../sortie")],
            [("a", "symlink", "b"), ("b", "symlink", "a")],
            [("a", "file", b"one"), ("a", "file", b"two")],
            [("a", "symlink", "b"), ("a/file", "file", b"no"), ("b", "file", b"ok")],
        ]
        for index, membres in enumerate(cases):
            archive = self.racine / f"unsafe-{index}.tar"
            self._archive(archive, membres)
            dest = self.base.prive / f"refuse-{index}"
            with self.subTest(membres=membres), self.assertRaises(ValueError):
                restaurer(archive, dest, base=self.base)
            self.assertFalse(dest.exists())

    def test_existing_destination_and_linked_parent_refuse_before_writing(self):
        archive = self._archive(self.racine / "fixture.tar", [("root/f", "file", b"ok")])
        dest = self.base.prive / "existing"
        dest.mkdir()
        marker = dest / "user.txt"
        marker.write_text("conserver")
        with self.assertRaises(FileExistsError):
            restaurer(archive, dest, base=self.base)
        self.assertEqual(marker.read_text(), "conserver")
        outside = self.racine / "outside"
        outside.mkdir()
        alias = self.base.prive / "linked-parent"
        alias.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            restaurer(archive, alias / "restore", base=self.base)
        self.assertEqual(list(outside.iterdir()), [])

    def test_corrupt_hash_and_copy_failure_never_publish_partial_destination(self):
        archive = self._archive(self.racine / "fixture.tar", [("root/f", "file", b"ok")])
        corrupt = self.racine / "corrupt.tar"
        with tarfile.open(archive, "r:") as source, tarfile.open(corrupt, "w") as target:
            for item in source.getmembers():
                data = source.extractfile(item).read() if item.isfile() else None
                if item.name == "root/f":
                    data = b"no"
                if data is not None:
                    item.size = len(data)
                    target.addfile(item, io.BytesIO(data))
                else:
                    target.addfile(item)
        dest = self.base.prive / "corrupt-restore"
        with self.assertRaises(ValueError):
            restaurer(corrupt, dest, base=self.base)
        self.assertFalse(dest.exists())

        dest = self.base.prive / "failed-restore"
        with patch.object(S, "_copier_membre", side_effect=OSError("fixture I/O failure")):
            with self.assertRaises(OSError):
                restaurer(archive, dest, base=self.base)
        self.assertFalse(dest.exists())
        self.assertFalse(list(self.base.prive.glob(".benchab-restauration-*")))

    def test_budgets_reject_synthetic_small_limits_without_large_fixtures(self):
        (self.source / "one.bin").write_bytes(b"12345")
        archive = self.racine / "too-large.tar"
        with patch.object(S, "MAX_MEMBER", 4), self.assertRaises(ValueError):
            creer(self.base, self.source, archive)
        self.assertFalse(archive.exists())
        (self.source / "one.bin").write_bytes(b"12")
        (self.source / "two.bin").write_bytes(b"34")
        with patch.object(S, "MAX_MEMBER", 10), patch.object(S, "MAX_TOTAL", 3), self.assertRaises(ValueError):
            creer(self.base, self.source, archive)
        self.assertFalse(archive.exists())

    def test_source_change_during_copy_is_detected_and_archive_not_published(self):
        source_file = self.source / "mutable.txt"
        source_file.write_bytes(b"before")
        original = S._copier_flux

        def mutate_after_copy(*args, **kwargs):
            result = original(*args, **kwargs)
            source_file.write_bytes(b"changed")
            return result

        archive = self.racine / "mutated.tar"
        with patch.object(S, "_copier_flux", side_effect=mutate_after_copy), self.assertRaises(ValueError):
            creer(self.base, self.source, archive)
        self.assertFalse(archive.exists())

    def test_external_source_and_out_of_scope_destination_are_refused(self):
        with self.assertRaises(ValueError):
            creer(self.base, self.racine, self.racine / "external.tar")
        alias = self.base.prive / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            creer(self.base, alias, self.racine / "alias.tar")
        with self.assertRaises(ValueError):
            creer(self.base, self.source, Path.home() / "public.tar")

    def test_sqlite_wal_dans_un_sous_dossier_ne_perd_pas_ses_lignes(self):
        connexion = sqlite3.connect(self.source / "benchab.sqlite")
        self.addCleanup(connexion.close)
        connexion.execute("CREATE TABLE t (valeur INTEGER)")
        connexion.commit()
        connexion.execute("PRAGMA journal_mode=WAL")
        connexion.execute("INSERT INTO t VALUES (42)")
        connexion.commit()
        archive = self.racine / "sous-dossier.tar"
        creer(self.base, self.source, archive)
        dest = self.base.prive / "sous-dossier-restaure"
        restaurer(archive, dest, base=self.base)
        with sqlite3.connect(dest / "benchab.sqlite") as copie:
            self.assertEqual(copie.execute("SELECT valeur FROM t").fetchall(), [(42,)])

    def test_un_dossier_illisible_ne_devient_pas_une_sauvegarde_vide_validee(self):
        refus = self.source / "illisible"
        refus.mkdir()
        (refus / "important.txt").write_text("à conserver")
        original = os.scandir

        def scandir(path):
            if isinstance(path, (str, bytes, os.PathLike)) and Path(path) == refus:
                raise PermissionError("fixture : parcours refusé")
            return original(path)

        archive = self.racine / "illisible.tar"
        with patch.object(os, "scandir", side_effect=scandir), self.assertRaises(PermissionError):
            creer(self.base, self.source, archive)
        self.assertFalse(archive.exists())

    def test_nom_posix_avec_deux_points_cree_et_restaure(self):
        nom = "capture 12:30.txt"
        (self.source / nom).write_text("contenu conservé")
        archive = self.racine / "nom-posix.tar"
        creer(self.base, self.source, archive)
        dest = self.base.prive / "nom-posix-restaure"
        restaurer(archive, dest, base=self.base)
        self.assertEqual((dest / nom).read_text(), "contenu conservé")


if __name__ == "__main__":
    unittest.main()


def test_espace_insuffisant_refuse_avant_archive_et_source_intacte(tmp_path, monkeypatch):
    """Moins de 31 Gio : FAIL avant copie, aucun fichier livré ni source modifiée."""
    from types import SimpleNamespace

    base = Base(tmp_path / "prive")
    try:
        source = base.prive / "fixture"
        source.mkdir()
        (source / "note").write_bytes(b"abc")
        cible = tmp_path / "copie.tar"
        monkeypatch.setattr(S.shutil, "disk_usage", lambda _: SimpleNamespace(free=S.SEUIL_DEPART - 1))
        import pytest

        with pytest.raises(ValueError, match="Espace insuffisant"):
            creer(base, source, cible)
        assert not cible.exists() and (source / "note").read_bytes() == b"abc"
    finally:
        base.fermer()


def test_baisse_espace_pendant_copie_ne_livre_pas_archive(tmp_path, monkeypatch):
    """Passage sous 10 Gio après précontrôle : FAIL, aucune archive partielle livrée, source intacte."""
    from types import SimpleNamespace

    import pytest

    base = Base(tmp_path / "prive")
    try:
        source = base.prive / "fixture"
        source.mkdir()
        (source / "note").write_bytes(b"abc")
        cible = tmp_path / "copie.tar"
        valeurs = iter([S.SEUIL_DEPART + 1, S.SEUIL_DEPART + 1, S.PLANCHER_LIBRE - 1])
        monkeypatch.setattr(S.shutil, "disk_usage", lambda _: SimpleNamespace(free=next(valeurs)))
        with pytest.raises(ValueError, match="Espace insuffisant"):
            creer(base, source, cible)
        assert not cible.exists() and not list(tmp_path.glob(".benchab-backup-*.tmp"))
        assert (source / "note").read_bytes() == b"abc"
    finally:
        base.fermer()


def test_comparaison_arbres_releve_modification_absence_et_mode(tmp_path):
    """Compare octets/SHA, présence et droits ; PASS puis FAIL détaillé, aucune réparation ni source changée."""
    base = Base(tmp_path / "prive")
    try:
        a, b = base.prive / "a", base.prive / "b"
        a.mkdir()
        b.mkdir()
        for dossier in (a, b):
            (dossier / "note").write_bytes(b"abc")
            (dossier / "absent").write_bytes(b"a")
            (dossier / "droits").write_bytes(b"b")
        assert S.comparer_arbres(base, a, b)["statut"] == "PASS"
        (b / "note").write_bytes(b"def")
        (b / "absent").unlink()
        (b / "droits").chmod(0o700)
        rapport = S.comparer_arbres(base, a, b)
        assert rapport["statut"] == "FAIL"
        assert {e["chemin"] for e in rapport["ecarts"]} == {"note", "absent", "droits"}
        assert (a / "note").read_bytes() == b"abc"
        assert (b / "note").read_bytes() == b"def"
    finally:
        base.fermer()


def test_restauration_sans_espace_ne_publie_pas_dossier(tmp_path, monkeypatch):
    """Une archive valide n'autorise pas une restauration sans espace ; FAIL, archive conservée, cible absente."""
    from types import SimpleNamespace

    import pytest

    base = Base(tmp_path / "prive")
    try:
        source = base.prive / "fixture"
        source.mkdir()
        (source / "note").write_bytes(b"abc")
        archive = tmp_path / "archive.tar"
        creer(base, source, archive)
        avant = archive.read_bytes()
        cible = base.prive / "restauree"
        monkeypatch.setattr(S.shutil, "disk_usage", lambda _: SimpleNamespace(free=S.SEUIL_DEPART - 1))
        with pytest.raises(ValueError, match="Espace insuffisant"):
            restaurer(archive, cible, base=base)
        assert not cible.exists() and archive.read_bytes() == avant
    finally:
        base.fermer()
