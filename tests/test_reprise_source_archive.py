"""Préparation passive d’une source privée depuis une sauvegarde tar."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

MEMBRE = "projets/fixture/sources/original.stl"


def _archive(
    path: Path, *, contenu=b"STL synthetique\n", contenu_manifeste=None, manifeste_brut=None, nom=MEMBRE,
    entrees_supplementaires=(),
):
    """Construit une sauvegarde tar petite et reproductible pour les tests."""
    sha = hashlib.sha256(contenu if contenu_manifeste is None else contenu_manifeste).hexdigest()
    files = [
        {"path": parent, "type": "directory", "mode": 0o700}
        for parent in ("projets", "projets/fixture", "projets/fixture/sources")
    ]
    files.append({"path": nom, "type": "file", "size": len(contenu), "sha256": sha})
    manifeste = {"schema": 1, "files": files}
    with tarfile.open(path, "w") as tar:
        for parent in ("projets", "projets/fixture", "projets/fixture/sources"):
            info = tarfile.TarInfo(parent)
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        for membre, donnees in (
            (nom, contenu),
            *entrees_supplementaires,
            ("BENCHAB-MANIFEST.json", manifeste_brut or json.dumps(manifeste).encode("utf-8")),
        ):
            info = tarfile.TarInfo(membre)
            info.size = len(donnees)
            tar.addfile(info, io.BytesIO(donnees))
    return sha


def _appel(prive, archive, sha_archive, sha_source, sortie, membre=MEMBRE):
    """Appelle l’API avec les empreintes calculées sur les fichiers de fixture."""
    from benchab.donnees.reprise_source import preparer

    return preparer(prive, archive, sha_archive, membre, sha_source, sortie)


def test_copie_un_original_et_sa_preuve_sans_appliquer(tmp_path):
    """La sortie PASS est une copie exacte sous rapports, jamais l’original."""
    prive = tmp_path / "prive"
    rapports = prive / "rapports"
    rapports.mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    contenu = b"original de fixture\x00\n"
    sha_source = _archive(archive, contenu=contenu)
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()
    sortie = rapports / "reprise-source"

    resultat = _appel(prive, archive, sha_archive, sha_source, sortie)

    copie = sortie / "source" / Path(MEMBRE).name
    preuve = json.loads((sortie / "preuve.json").read_bytes())
    assert resultat["statut"] == "PASS" and resultat["applique"] is False
    assert resultat["statut_preuve_fichier"] == "CANDIDAT"
    assert resultat["archive_sha256"] == sha_archive
    assert resultat["sha256_source"] == sha_source
    assert copie.read_bytes() == contenu
    assert preuve["statut"] == "CANDIDAT" and preuve["applique"] is False
    assert preuve["membre"] == MEMBRE and preuve["sha256_source"] == sha_source
    assert preuve["copie"] == "source/original.stl"
    assert not (prive / "projets" / "fixture" / "sources" / "original.stl").exists()
    assert not (prive / "benchab.sqlite").exists()
    assert sorted(p.name for p in rapports.iterdir()) == ["reprise-source"]


@pytest.mark.parametrize(
    "motif", ["membre_absent", "source_alteree", "sha_source_faux", "sha_archive_faux"]
)
def test_refuse_preuve_ou_source_absente_ou_alteree_sans_sortie(tmp_path, motif):
    """Une incohérence entre archive, manifeste et SHA déclarés ne publie rien."""
    prive = tmp_path / "prive"
    rapports = prive / "rapports"
    rapports.mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    contenu = b"source alteree dans le tar" if motif == "source_alteree" else b"STL synthetique\n"
    sha_source = _archive(archive, contenu=contenu,
                          contenu_manifeste=b"STL synthetique\n" if motif == "source_alteree" else None)
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()
    membre = MEMBRE
    if motif == "membre_absent":
        membre = "projets/fixture/sources/introuvable.stl"
    elif motif == "sha_source_faux":
        sha_source = "0" * 64
    else:
        sha_archive = "0" * 64
    sortie = rapports / "reprise-source"

    with pytest.raises((OSError, ValueError)):
        _appel(prive, archive, sha_archive, sha_source, sortie, membre)

    assert not sortie.exists()
    assert list(rapports.iterdir()) == []


def test_collision_de_sortie_preserve_le_dossier_existant(tmp_path):
    """Une destination déjà présente est refusée sans modifier ses octets."""
    prive = tmp_path / "prive"
    rapports = prive / "rapports"
    rapports.mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    sha_source = _archive(archive)
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()
    sortie = rapports / "reprise-source"
    sortie.mkdir()
    garde = sortie / "garde.txt"
    garde.write_bytes(b"conserver")

    with pytest.raises((OSError, ValueError)):
        _appel(prive, archive, sha_archive, sha_source, sortie)

    assert garde.read_bytes() == b"conserver"
    assert sorted(p.name for p in sortie.iterdir()) == ["garde.txt"]


def test_collision_apparue_au_renommage_est_refusee_sans_ecrasement(tmp_path, monkeypatch):
    """Une sortie apparue après la validation reste intacte face au renommage exclusif."""
    import os

    from benchab.donnees import reprise_source

    prive = tmp_path / "prive"
    rapports = prive / "rapports"
    rapports.mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    sha_source = _archive(archive)
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()
    sortie = rapports / "reprise-source"
    renommer = reprise_source.renommer_exclusif

    def creer_collision(stage, cible, *, src_dir_fd, dst_dir_fd):
        os.mkdir(cible, dir_fd=dst_dir_fd)
        fd = os.open(f"{cible}/garde.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dst_dir_fd)
        with os.fdopen(fd, "wb") as flux:
            flux.write(b"tiers")
        return renommer(stage, cible, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(reprise_source, "renommer_exclusif", creer_collision)
    with pytest.raises(FileExistsError):
        _appel(prive, archive, sha_archive, sha_source, sortie)

    assert (sortie / "garde.txt").read_bytes() == b"tiers"
    assert sorted(p.name for p in sortie.iterdir()) == ["garde.txt"]


def test_refuse_une_sortie_hors_rapports(tmp_path):
    """L’API limite la publication durable au dossier rapports choisi."""
    prive = tmp_path / "prive"
    (prive / "rapports").mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    sha_source = _archive(archive)
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="rapports"):
        _appel(prive, archive, sha_archive, sha_source, prive / "reprise-source")

    assert not (prive / "reprise-source").exists()


def test_refuse_un_membre_non_canonique_sans_sortie(tmp_path):
    """Un chemin source absolu ou traversant n’atteint jamais le tar."""
    prive = tmp_path / "prive"
    rapports = prive / "rapports"
    rapports.mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    sha_source = _archive(archive)
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="Chemin archive dangereux"):
        _appel(prive, archive, sha_archive, sha_source, rapports / "refus", "projets/../source.stl")

    assert list(rapports.iterdir()) == []


def test_refuse_un_manifeste_json_ambigu_sans_sortie(tmp_path):
    """Les clés de manifeste dupliquées restent refusées avant toute publication."""
    prive = tmp_path / "prive"
    rapports = prive / "rapports"
    rapports.mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    sha_source = _archive(
        archive,
        manifeste_brut=b'{"schema":1,"schema":1,"files":[]}',
    )
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="ambigu"):
        _appel(prive, archive, sha_archive, sha_source, rapports / "refus")

    assert list(rapports.iterdir()) == []


def test_refuse_un_tar_lien_et_une_entree_speciale(tmp_path):
    """Le lecteur refuse un chemin TAR symbolique au lieu de suivre sa cible."""
    from benchab.donnees.reprise_source import preparer

    prive = tmp_path / "prive"
    rapports = prive / "rapports"
    rapports.mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    sha_source = _archive(archive)
    lien = tmp_path / "lien.tar"
    lien.symlink_to(archive)
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="Lien symbolique"):
        preparer(prive, lien, sha_archive, MEMBRE, sha_source, rapports / "lien")
    assert list(rapports.iterdir()) == []


def test_budgets_tar_refusent_avant_de_lire_un_nombre_non_borne_de_membres(tmp_path, monkeypatch):
    """L’indexeur s’arrête au plafond configuré, avant d’accumuler tous les membres."""
    import tarfile

    from benchab.donnees import reprise_source

    archive = tmp_path / "beaucoup.tar"
    with tarfile.open(archive, "w") as tar:
        for nom in ("un", "deux", "trois"):
            info = tarfile.TarInfo(nom)
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
    monkeypatch.setattr(reprise_source, "_MAX_ENTREES", 2)
    with tarfile.open(archive, "r:") as tar:
        with pytest.raises(ValueError, match="budget de 100 000 entrées"):
            reprise_source._indexer(tar)


def test_budget_du_membre_est_verifie_avant_extraction(tmp_path, monkeypatch):
    """La taille d’un fichier TAR est refusée dès l’index, sans ouvrir son flux."""
    import io
    import tarfile

    from benchab.donnees import reprise_source

    archive = tmp_path / "membre.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("grand.bin")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"1234"))
    monkeypatch.setattr(reprise_source, "MAX_MEMBER", 3)
    with tarfile.open(archive, "r:") as tar:
        with pytest.raises(ValueError, match="4 Gio"):
            reprise_source._indexer(tar)


def test_cli_prepare_depuis_tar_ne_ouvre_pas_base(tmp_path, capsys, monkeypatch):
    """La route CLI prépare sous rapports sans ouvrir de Base ni enregistrer de mesure."""
    from benchab import cli

    prive = tmp_path / "prive"
    rapports = prive / "rapports"
    rapports.mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    sha_source = _archive(archive)
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(cli, "Base", lambda *_args, **_kwargs: pytest.fail("Base ne doit pas être ouverte"))
    sortie = rapports / "reprise"
    code = cli.main([
        "--prive", str(prive), "controle", "preparer-reprise-source",
        "--archive", str(archive), "--sha256-archive", sha_archive,
        "--membre", MEMBRE, "--sha256-source", sha_source, "--sortie", str(sortie),
    ])
    resultat = json.loads(capsys.readouterr().out)

    assert code == 0 and resultat["statut"] == "PASS"
    assert (sortie / "source" / "original.stl").is_file()
    assert (sortie / "preuve.json").is_file()
    assert not (prive / "benchab.sqlite").exists()

    assert cli.main([
        "--prive", str(prive), "controle", "preparer-reprise-source", "--archive", str(archive),
    ]) == 2
    capsys.readouterr()
    assert not (prive / "benchab.sqlite").exists()
