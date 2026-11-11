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
    entrees_supplementaires=(), pax_headers=None, pax_headers_manifeste=None,
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
            if membre == nom and pax_headers:
                info.pax_headers = pax_headers
            if membre == "BENCHAB-MANIFEST.json" and pax_headers_manifeste:
                info.pax_headers = pax_headers_manifeste
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


def test_taille_entete_pax_est_bornee_avant_lecture_du_corps(tmp_path, monkeypatch):
    """Un entête PAX surdimensionné est refusé sur sa taille déclarée, sans corps alloué."""
    import tarfile

    from benchab.donnees import reprise_source

    archive = tmp_path / "pax-volumineux.tar"
    info = tarfile.TarInfo("pax")
    info.type = tarfile.XHDTYPE
    info.size = 5
    archive.write_bytes(info.tobuf() + b"court")
    monkeypatch.setattr(reprise_source, "_MAX_TAILLE_EXTENSION", 4, raising=False)
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()
    prive = tmp_path / "prive"
    (prive / "rapports").mkdir(parents=True)

    with pytest.raises(ValueError, match="entête TAR dépasse le budget"):
        _appel(prive, archive, sha_archive, "0" * 64, prive / "rapports" / "sortie")

    assert list((prive / "rapports").iterdir()) == []


def test_nombre_entetes_extension_est_borne_independamment_des_membres(tmp_path, monkeypatch):
    """Les en-têtes d’extension PAX comptent dans un plafond même sans membre retourné."""
    import tarfile

    from benchab.donnees import reprise_source

    archive = tmp_path / "pax-nombreux.tar"
    with tarfile.open(archive, "w") as tar:
        for _ in range(2):
            info = tarfile.TarInfo("pax")
            info.type = tarfile.XHDTYPE
            info.size = 0
            tar.addfile(info)
        info = tarfile.TarInfo("fin")
        info.size = 0
        tar.addfile(info)
    monkeypatch.setattr(reprise_source, "_MAX_ENTETES_EXTENSION", 1)
    with pytest.raises(ValueError, match="entêtes d’extension"):
        with tarfile.open(archive, "r:", tarinfo=reprise_source._TarInfoBorne) as tar:
            reprise_source._indexer(tar)


def test_metadata_pax_valide_du_producteur_reste_prise_en_charge(tmp_path):
    """Les chemins longs émis en PAX restent compatibles avec les archives BENCHAB."""
    prive = tmp_path / "prive"
    (prive / "rapports").mkdir(parents=True)
    archive = tmp_path / "sauvegarde-pax.tar"
    membre = "projets/fixture/sources/" + ("nom" * 35) + ".stl"
    contenu = b"source pax\n"
    sha_source = _archive(archive, contenu=contenu, nom=membre)
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()

    resultat = _appel(prive, archive, sha_archive, sha_source, prive / "rapports" / "copie", membre)

    assert resultat["statut"] == "PASS"
    assert (prive / "rapports" / "copie" / "source" / Path(membre).name).read_bytes() == contenu


def test_metadata_pax_sparse_refusees_avant_parseur_sparse(tmp_path):
    """Les extensions PAX GNU sparse sont refusées avant l’analyse des segments."""
    import io
    import tarfile

    from benchab.donnees import reprise_source

    corps = b""
    donnees = b"GNU.sparse.size=1\n"
    longueur = len(donnees) + 3
    while True:
        enregistrement = str(longueur).encode() + b" " + donnees
        if len(enregistrement) == longueur:
            corps = enregistrement
            break
        longueur = len(enregistrement)
    archive = tmp_path / "sparse.tar"
    with tarfile.open(archive, "w") as tar:
        extension = tarfile.TarInfo("pax")
        extension.type = tarfile.XHDTYPE
        extension.size = len(corps)
        tar.addfile(extension, io.BytesIO(corps))
        membre = tarfile.TarInfo("sparse.bin")
        membre.size = 0
        tar.addfile(membre)

    with pytest.raises(ValueError, match="TAR sparse"):
        with tarfile.open(archive, "r:", tarinfo=reprise_source._TarInfoBorne):
            pytest.fail("Les métadonnées sparse doivent être refusées à l’ouverture")


def test_membre_gnu_sparse_refuse_avant_lecture_des_blocs_etendus(tmp_path):
    """Un indicateur sparse GNU est refusé sans parcourir ses blocs d’extension."""
    import tarfile

    from benchab.donnees import reprise_source

    archive = tmp_path / "gnu-sparse.tar"
    info = tarfile.TarInfo("sparse")
    info.type = tarfile.GNUTYPE_SPARSE
    info.size = 0
    archive.write_bytes(info.tobuf())

    with pytest.raises(ValueError, match="TAR sparse"):
        with tarfile.open(archive, "r:", tarinfo=reprise_source._TarInfoBorne):
            pytest.fail("Le type sparse doit être refusé avant son traitement")


def test_corps_pax_tronque_refuse_sans_sortie(tmp_path):
    """Un corps d’extension tronqué produit un refus et ne publie aucun dossier."""
    import tarfile

    archive = tmp_path / "pax-tronque.tar"
    info = tarfile.TarInfo("pax")
    info.type = tarfile.XHDTYPE
    info.size = 20
    archive.write_bytes(info.tobuf() + b"court")
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()
    prive = tmp_path / "prive"
    rapports = prive / "rapports"
    rapports.mkdir(parents=True)

    with pytest.raises(ValueError, match="Archive tar illisible ou incomplète"):
        _appel(prive, archive, sha_archive, "0" * 64, rapports / "sortie")

    assert list(rapports.iterdir()) == []


def test_chemin_pax_en_contradiction_avec_le_manifeste_refuse(tmp_path):
    """Un nom PAX effectif différent du manifeste n’est pas copié comme la source attendue."""
    prive = tmp_path / "prive"
    rapports = prive / "rapports"
    rapports.mkdir(parents=True)
    archive = tmp_path / "pax-contradictoire.tar"
    sha_source = _archive(
        archive,
        pax_headers={"path": "projets/fixture/sources/autre.stl"},
    )
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="Inventaire et archive différents"):
        _appel(prive, archive, sha_archive, sha_source, rapports / "sortie")

    assert list(rapports.iterdir()) == []


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


@pytest.mark.parametrize("queue", [b"", b"\0" * 512, b"\0" * 1024 + b"a" * 512])
def test_fin_tar_incomplete_ou_non_nulle_refusee(tmp_path, queue):
    """Une fin TAR incomplète ou suivie de données ne produit aucune copie."""
    prive = tmp_path / "prive"
    (prive / "rapports").mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    sha_source = _archive(archive)
    with tarfile.open(archive, "r:") as tar:
        tar.getmembers()
        fin_membres = tar.offset
    archive.write_bytes(archive.read_bytes()[:fin_membres] + queue)
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="Fin du TAR"):
        _appel(prive, archive, sha_archive, sha_source, prive / "rapports" / "sortie")
    assert list((prive / "rapports").iterdir()) == []


def test_budget_cumule_entetes_meme_si_chacune_est_petite(tmp_path, monkeypatch):
    """Le budget cumulé empêche les extensions ordinaires de gonfler l’index."""
    from benchab.donnees import reprise_source

    prive = tmp_path / "prive"
    (prive / "rapports").mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    sha_source = _archive(archive, pax_headers={"mtime": "42.1"}, pax_headers_manifeste={"mtime": "43.2"})
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(reprise_source, "_MAX_OCTETS_EXTENSIONS", 20, raising=False)
    with pytest.raises(ValueError, match="budget cumulé des extensions"):
        _appel(prive, archive, sha_archive, sha_source, prive / "rapports" / "sortie")
    assert list((prive / "rapports").iterdir()) == []


@pytest.mark.parametrize("etat", ["MANQUANT", "IDENTIQUE", "DIVERGENT", "NON VÉRIFIÉ"])
def test_verifier_copie_distingue_l_original_sans_ecrire(tmp_path, etat):
    """Une preuve correcte peut coexister avec un original absent ou différent."""
    from benchab.donnees.reprise_source import verifier_copie

    prive = tmp_path / "prive"
    (prive / "rapports").mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    contenu = b"source connue\n"
    sha_source = _archive(archive, contenu=contenu)
    sortie = prive / "rapports" / "copie"
    preparation = _appel(prive, archive, hashlib.sha256(archive.read_bytes()).hexdigest(), sha_source, sortie)
    original = prive / MEMBRE
    if etat != "MANQUANT":
        original.parent.mkdir(parents=True)
        if etat == "NON VÉRIFIÉ":
            original.symlink_to(archive)
        else:
            original.write_bytes(contenu if etat == "IDENTIQUE" else b"autres octets")
    avant = original.read_bytes() if original.is_file() and not original.is_symlink() else None
    resultat = verifier_copie(prive, sortie / "preuve.json", preparation["preuve_sha256"])
    assert resultat["statut"] == "PASS" and resultat["applique"] is False
    assert resultat["original"]["statut"] == etat
    assert resultat["sha256_copie"] == sha_source
    assert not (prive / "benchab.sqlite").exists()
    if avant is not None:
        assert original.read_bytes() == avant
    elif etat == "MANQUANT":
        assert not original.exists()


@pytest.mark.parametrize("cible", ["preuve", "copie", "copie-liee"])
def test_verifier_copie_refuse_octets_alteres_ou_lien(tmp_path, cible):
    """Une preuve liée ou altérée ne devient pas une source de réparation."""
    from benchab.donnees.reprise_source import verifier_copie

    prive = tmp_path / "prive"
    (prive / "rapports").mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    sha_source = _archive(archive)
    sortie = prive / "rapports" / "copie"
    preparation = _appel(prive, archive, hashlib.sha256(archive.read_bytes()).hexdigest(), sha_source, sortie)
    preuve = sortie / "preuve.json"
    copie = sortie / preparation["copie"]
    if cible == "preuve":
        preuve.write_bytes(preuve.read_bytes() + b"\n")
    elif cible == "copie":
        copie.write_bytes(b"octets alteres")
    else:
        copie.rename(copie.with_suffix(".copie"))
        copie.symlink_to(copie.with_suffix(".copie"))
    with pytest.raises((OSError, ValueError)):
        verifier_copie(prive, preuve, preparation["preuve_sha256"])


def test_cli_verifie_copie_sans_ouvrir_la_base(tmp_path, capsys, monkeypatch):
    """La route passive n’initialise ni atelier ni original absent."""
    from benchab import cli

    prive = tmp_path / "prive"
    (prive / "rapports").mkdir(parents=True)
    archive = tmp_path / "sauvegarde.tar"
    sha_source = _archive(archive)
    sortie = prive / "rapports" / "copie"
    prep = _appel(prive, archive, hashlib.sha256(archive.read_bytes()).hexdigest(), sha_source, sortie)
    monkeypatch.setattr(cli, "Base", lambda *_a, **_k: pytest.fail("Base ne doit pas être ouverte"))
    assert cli.main([
        "--prive", str(prive), "controle", "verifier-reprise-source", "--preuve", str(sortie / "preuve.json"),
        "--sha256-preuve", prep["preuve_sha256"],
    ]) == 0
    resultat = json.loads(capsys.readouterr().out)
    assert resultat["statut"] == "PASS" and resultat["original"]["statut"] == "MANQUANT"
    assert not resultat["archive_relue"] and not (prive / MEMBRE).exists()
