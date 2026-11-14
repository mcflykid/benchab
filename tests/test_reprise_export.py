"""Prépare une copie d’export sans modifier l’atelier canonique."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from benchab.donnees.base import Base


@pytest.fixture
def atelier(tmp_path):
    """Atelier jetable contenant un export non vide, avant retrait de la table."""
    racine = tmp_path / "prive"
    base = Base(racine)
    try:
        base.ecrire(
            "INSERT INTO projets(id,nom,etat,donnees_json) VALUES (?,?,?,?)",
            ("fixture", "Copie épreuve", "actif", "{}"),
        )
    finally:
        base.fermer()
    cible = racine / "export/projets.jsonl"
    octets = cible.read_bytes()
    cible.unlink()
    return racine, cible, octets


def test_preparation_restitue_les_octets_sans_appliquer(tmp_path, atelier):
    """SQLite et le manifeste prouvent les mêmes lignes que l’export original."""
    from benchab.qualite.reprise_export import preparer

    prive, cible, attendu = atelier
    avant = (prive / "benchab.sqlite").read_bytes()
    manifeste = (prive / "export/manifest.json").read_bytes()
    sortie = tmp_path / "reprise"
    rapport = preparer(prive, "projets", sortie)
    assert rapport["statut"] == "PASS" and rapport["applique"] is False
    assert rapport["statut_preuve_fichier"] == "CANDIDAT"
    assert rapport["verification_post_publication"] == "PASS"
    assert "PASS" in rapport["verification"]
    assert rapport["preuve_sha256"] == hashlib.sha256((sortie / "preuve.json").read_bytes()).hexdigest()
    assert rapport["table"] == "projets" and rapport["lignes"] == 1
    assert (sortie / "projets.jsonl").read_bytes() == attendu
    preuve = json.loads((sortie / "preuve.json").read_bytes())
    assert preuve["statut"] == "CANDIDAT"
    assert preuve["sha256_attendu"] == rapport["sha256"]
    assert "revérification" in preuve["verification"].lower()
    assert not cible.exists()
    assert (prive / "benchab.sqlite").read_bytes() == avant
    assert (prive / "export/manifest.json").read_bytes() == manifeste
    assert not (prive / "benchab.sqlite-journal").exists()
    with pytest.raises(FileExistsError):
        preparer(prive, "projets", sortie)


def test_verifier_reprise_export_distingue_cible_et_copie(tmp_path, atelier, capsys):
    """La relecture passive ne crée aucun fichier et distingue une cible modifiée."""
    from benchab.cli import main
    from benchab.qualite.reprise_export import preparer

    prive, cible, attendu = atelier
    sortie = tmp_path / "reprise"
    preuve = preparer(prive, "projets", sortie)
    cmd = ["--prive", str(prive), "controle", "verifier-reprise-export", "--preuve",
           str(sortie / "preuve.json"), "--sha256-preuve", preuve["preuve_sha256"]]
    for etat, contenu in (("ABSENTE", None), ("IDENTIQUE", attendu), ("DIVERGENTE", b"alteree")):
        if contenu is not None:
            cible.write_bytes(contenu)
        avant = {p: p.read_bytes() for p in prive.rglob("*") if p.is_file()}
        assert main(cmd) == 0
        rapport = json.loads(capsys.readouterr().out)
        assert rapport["cible"]["statut"] == etat
        assert rapport["applique"] is False
        assert rapport["compatibilite_sqlite_et_manifeste"] == "NON VÉRIFIÉ"
        assert {p: p.read_bytes() for p in prive.rglob("*") if p.is_file()} == avant
    (sortie / "projets.jsonl").write_bytes(b"copie alteree")
    assert main(cmd) == 2
    assert "octets du JSONL" in capsys.readouterr().err
    assert cible.read_bytes() == b"alteree"


def test_refus_si_manifeste_et_sqlite_divergent(tmp_path, atelier):
    """Une preuve attendue mais différente ne produit aucune copie publiable."""
    from benchab.qualite.reprise_export import preparer

    prive, cible, _ = atelier
    manifeste = prive / "export/manifest.json"
    data = json.loads(manifeste.read_bytes())
    data["sha256"]["projets"] = "0" * 64
    manifeste.write_text(json.dumps(data))
    sortie = tmp_path / "reprise"
    with pytest.raises(ValueError, match="empreinte"):
        preparer(prive, "projets", sortie)
    assert not sortie.exists() and not cible.exists()


def test_refus_si_cible_presente_ou_sortie_dans_export(tmp_path, atelier):
    """Le dossier de preuve ne peut pas servir à restaurer en place."""
    from benchab.qualite.reprise_export import preparer

    prive, cible, _ = atelier
    sortie = tmp_path / "reprise"
    with pytest.raises(ValueError, match="hors.*export"):
        preparer(prive, "projets", cible.parent / "reprise")
    lien = tmp_path / "copie"
    lien.write_bytes(b"fichier a conserver")
    cible.symlink_to(lien)
    with pytest.raises(ValueError, match="absente"):
        preparer(prive, "projets", sortie)
    assert not sortie.exists() and cible.is_symlink()
    assert lien.read_bytes() == b"fichier a conserver"


def test_refus_wal_sans_creation_de_sidecar(tmp_path, atelier):
    """Une base en WAL reste hors du contrat de préparation en lecture seule."""
    from benchab.qualite.reprise_export import preparer

    prive, cible, _ = atelier
    with sqlite3.connect(prive / "benchab.sqlite") as cx:
        assert cx.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    cx.close()
    avant = {p.name for p in prive.iterdir()}
    with pytest.raises(ValueError, match="journal"):
        preparer(prive, "projets", tmp_path / "reprise")
    assert {p.name for p in prive.iterdir()} == avant
    assert not cible.exists() and not (tmp_path / "reprise").exists()


def test_cli_prepare_sans_ouvrir_base_ni_enregistrer_mesure(tmp_path, atelier, capsys, monkeypatch):
    """L’entrée CLI produit seulement la preuve et conserve le registre et ses mesures."""
    from benchab import cli

    def interdire_base(*_args, **_kwargs):
        raise AssertionError("La reprise préparée ne doit pas ouvrir Base().")

    monkeypatch.setattr(cli, "Base", interdire_base)
    prive, cible, attendu = atelier
    avant = (prive / "benchab.sqlite").read_bytes()
    sortie = tmp_path / "reprise"
    arguments = [
        "--prive",
        str(prive),
        "controle",
        "preparer-reprise-export",
        "--table",
        "projets",
        "--sortie",
        str(sortie),
    ]
    assert cli.main(arguments) == 0
    retour_cli = json.loads(capsys.readouterr().out)
    preuve = json.loads((sortie / "preuve.json").read_bytes())
    assert retour_cli["statut"] == "PASS"
    assert preuve["statut"] == "CANDIDAT"
    assert retour_cli["preuve_sha256"] == hashlib.sha256((sortie / "preuve.json").read_bytes()).hexdigest()
    assert (sortie / "projets.jsonl").read_bytes() == attendu
    assert (prive / "benchab.sqlite").read_bytes() == avant and not cible.exists()
    assert cli.main(arguments) == 2


@pytest.mark.parametrize("membre", ["projets.jsonl", "preuve.json"])
def test_course_sur_membre_publie_refuse_sans_second_renommer(tmp_path, atelier, monkeypatch, membre):
    """Une mutation après le SHA prévu reste à la destination et refuse PASS sans second déplacement."""
    import benchab.qualite.reprise_export as reprise

    prive, _cible, attendu = atelier
    sortie = tmp_path / "reprise"
    renommer = reprise.renommer_exclusif
    appels_renommage = []

    def modifier_au_renommage(source, destination, **kwargs):
        appels_renommage.append((Path(source), Path(destination)))
        if Path(destination) == sortie and Path(source) != sortie:
            (Path(source) / membre).write_bytes(b"modification concurrente\n")
        return renommer(source, destination, **kwargs)

    monkeypatch.setattr(reprise, "renommer_exclusif", modifier_au_renommage)
    with pytest.raises(ValueError, match="vérification|modifié|diffèrent"):
        reprise.preparer(prive, "projets", sortie)

    assert sortie.is_dir()
    assert (sortie / membre).read_bytes() == b"modification concurrente\n"
    assert (sortie / "projets.jsonl").read_bytes() == (
        b"modification concurrente\n" if membre == "projets.jsonl" else attendu
    )
    assert len(appels_renommage) == 1
    assert not list(tmp_path.glob(".reprise.quarantaine-*"))


def test_destination_remplacee_apres_renommage_reste_intacte(tmp_path, atelier, monkeypatch):
    """Une cible qui ne désigne plus le stage est refusée sans toucher au tiers."""
    import benchab.qualite.reprise_export as reprise

    prive, _cible, _attendu = atelier
    sortie = tmp_path / "reprise"
    renommer = reprise.renommer_exclusif
    appels = []

    def substituer_apres_renommage(source, destination, **kwargs):
        appels.append((Path(source), Path(destination)))
        if Path(destination) == sortie:
            renommer(source, destination, **kwargs)
            sortie.rename(tmp_path / "stage-deplace")
            sortie.mkdir()
            (sortie / "projets.jsonl").write_bytes(b"dossier tiers\n")
            return
        return renommer(source, destination, **kwargs)

    monkeypatch.setattr(reprise, "renommer_exclusif", substituer_apres_renommage)
    with pytest.raises(ValueError, match="ne désigne plus le dossier préparé"):
        reprise.preparer(prive, "projets", sortie)

    assert (sortie / "projets.jsonl").read_bytes() == b"dossier tiers\n"
    assert (tmp_path / "stage-deplace" / "projets.jsonl").is_file()
    assert len(appels) == 1
    assert not list(tmp_path.glob(".reprise.quarantaine-*"))


def test_preuve_modifiee_avant_premier_hachage_est_refusee(tmp_path, atelier, monkeypatch):
    """Le SHA attendu vient des octets prévus, jamais du fichier déjà altéré."""
    from contextlib import contextmanager

    from benchab.qualite import reprise_export as reprise

    prive, cible, _ = atelier
    sortie = tmp_path / "reprise"
    ouvrir = reprise._ouvrir_dossier_epingle
    premier = True

    @contextmanager
    def ouverture_alteree(dossier):
        nonlocal premier
        if premier:
            premier = False
            (dossier / "preuve.json").write_bytes(b'{"statut":"PASS","faux":true}\n')
        with ouvrir(dossier) as fd:
            yield fd

    monkeypatch.setattr(reprise, "_ouvrir_dossier_epingle", ouverture_alteree)
    with pytest.raises(ValueError, match="SHA|empreinte"):
        reprise.preparer(prive, "projets", sortie)
    assert not sortie.exists() and not cible.exists()


def _candidat(prive, tmp_path):
    """Construit une preuve privée CANDIDAT dans le temporaire du test."""
    from benchab.qualite.reprise_export import preparer

    sortie = tmp_path / "candidat"
    preparer(prive, "projets", sortie)
    preuve = sortie / "preuve.json"
    sha = hashlib.sha256(preuve.read_bytes()).hexdigest()
    return sortie, preuve, sha


def test_appliquer_copie_uniquement_table_absente_et_garde_candidat(tmp_path, atelier):
    """Appliquer publie une copie exclusive, garde la preuve et laisse SQLite identique."""
    from benchab.qualite.reprise_export import appliquer

    prive, cible, attendu = atelier
    candidat, preuve, sha_preuve = _candidat(prive, tmp_path)
    base_avant = (prive / "benchab.sqlite").read_bytes()
    candidat_avant = {p.name: p.read_bytes() for p in candidat.iterdir()}

    rapport = appliquer(prive, preuve, sha_preuve)

    assert rapport["statut"] == "PASS" and rapport["applique"] is True
    assert cible.read_bytes() == attendu
    assert {p.name: p.read_bytes() for p in candidat.iterdir()} == candidat_avant
    assert (prive / "benchab.sqlite").read_bytes() == base_avant
    assert not list((prive / "export").glob(".reprise-*"))
    with pytest.raises(ValueError, match="absente"):
        appliquer(prive, preuve, sha_preuve)


def test_appliquer_refuse_sha_preuve_ou_copie_falsifie(tmp_path, atelier):
    """Ni le JSON candidat ni son SHA déclaré ne peuvent être substitués."""
    from benchab.qualite.reprise_export import appliquer

    prive, cible, _ = atelier
    _candidat(prive, tmp_path)
    preuve = tmp_path / "candidat/preuve.json"
    with pytest.raises(ValueError, match="SHA.*preuve"):
        appliquer(prive, preuve, "0" * 64)
    preuve_data = json.loads(preuve.read_bytes())
    preuve_data["sha256_attendu"] = "0" * 64
    preuve.write_text(json.dumps(preuve_data))
    sha = hashlib.sha256(preuve.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="(?i)preuve|empreinte"):
        appliquer(prive, preuve, sha)
    assert not cible.exists()


def test_appliquer_refuse_si_manifeste_ou_base_a_change(tmp_path, atelier):
    """La preuve n’est applicable que contre le manifeste et le snapshot identiques."""
    from benchab.qualite.reprise_export import appliquer

    prive, cible, _ = atelier
    _candidat(prive, tmp_path)
    preuve = tmp_path / "candidat/preuve.json"
    sha = hashlib.sha256(preuve.read_bytes()).hexdigest()
    manifeste = prive / "export/manifest.json"
    manifeste.write_bytes(manifeste.read_bytes() + b" ")
    with pytest.raises(ValueError, match="manifeste"):
        appliquer(prive, preuve, sha)
    assert not cible.exists()


def test_appliquer_refuse_si_snapshot_sqlite_a_change(tmp_path, atelier):
    """Une mutation SQLite postérieure au candidat ne passe pas via son ancien manifeste."""
    from benchab.qualite.reprise_export import appliquer

    prive, cible, _ = atelier
    _candidat(prive, tmp_path)
    preuve = tmp_path / "candidat/preuve.json"
    sha = hashlib.sha256(preuve.read_bytes()).hexdigest()
    with sqlite3.connect(prive / "benchab.sqlite") as connexion:
        connexion.execute("UPDATE projets SET nom=? WHERE id=?", ("modifié", "fixture"))
        connexion.commit()
    with pytest.raises(ValueError, match="manifeste|SQLite|sérialisation"):
        appliquer(prive, preuve, sha)
    assert not cible.exists()


def test_appliquer_refuse_arrivee_concurrente_sans_ecraser(tmp_path, atelier, monkeypatch):
    """Un fichier arrivé avant le renommage exclusif reste intact."""
    import benchab.qualite.reprise_export as reprise

    prive, cible, _ = atelier
    _candidat(prive, tmp_path)
    preuve = tmp_path / "candidat/preuve.json"
    sha = hashlib.sha256(preuve.read_bytes()).hexdigest()
    publier = reprise._publier_stage_exclusif

    def arriver_avant(source, destination, export_fd):
        import os

        cible_fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=export_fd)
        os.write(cible_fd, b"tiers\n")
        os.close(cible_fd)
        return publier(source, destination, export_fd)

    monkeypatch.setattr(reprise, "_publier_stage_exclusif", arriver_avant)
    with pytest.raises((FileExistsError, ValueError)):
        reprise.appliquer(prive, preuve, sha)
    assert cible.read_bytes() == b"tiers\n"
    assert list((prive / "export").glob(".reprise-*"))


def test_appliquer_course_postpublication_conserve_la_cible(tmp_path, atelier, monkeypatch):
    """Une altération après publication échoue sans supprimer ni déplacer le fichier."""
    import benchab.qualite.reprise_export as reprise

    prive, cible, _ = atelier
    _candidat(prive, tmp_path)
    preuve = tmp_path / "candidat/preuve.json"
    sha = hashlib.sha256(preuve.read_bytes()).hexdigest()
    publier = reprise._publier_stage_exclusif

    def modifier_apres_renommage(source, destination, export_fd):
        publier(source, destination, export_fd)
        cible.write_bytes("altération concurrente\n".encode())

    monkeypatch.setattr(reprise, "_publier_stage_exclusif", modifier_apres_renommage)
    with pytest.raises(ValueError, match="(?i)cible.*créée.*laissée"):
        reprise.appliquer(prive, preuve, sha)
    assert cible.read_bytes() == "altération concurrente\n".encode()


def test_sha_fd_refuse_croissance_au_dela_du_budget(tmp_path, monkeypatch):
    """Le dernier contrôle de SHA ne lit jamais davantage que budget + 1 octets."""
    import benchab.qualite.reprise_export as reprise

    fichier = tmp_path / "candidat.jsonl"
    fichier.write_bytes(b"1234567890")
    fd = fichier.open("rb")
    demandes = []
    pread = reprise.os.pread

    def borner(descripteur, taille, position):
        demandes.append(taille)
        return pread(descripteur, taille, position)

    monkeypatch.setattr(reprise.os, "pread", borner)
    try:
        with pytest.raises(ValueError, match="budget"):
            reprise._hachage_fd_borne(fd.fileno(), 4)
    finally:
        fd.close()
    assert demandes and max(demandes) <= 5


def test_ecriture_stage_refuse_ecriture_nulle(monkeypatch):
    """Une écriture système nulle termine en refus sans boucle infinie."""
    import benchab.qualite.reprise_export as reprise

    monkeypatch.setattr(reprise.os, "write", lambda *_a, **_k: 0)
    with pytest.raises(OSError, match="nulle"):
        reprise._ecrire_tout(123, b"candidat")


def test_appliquer_verifie_chaine_preuve_apres_renommage(tmp_path, atelier, monkeypatch):
    """Le remplacement du parent de preuve après publication refuse le PASS sans retirer la cible."""
    import benchab.qualite.reprise_export as reprise

    prive, cible, attendu = atelier
    candidat, preuve, sha = _candidat(prive, tmp_path)
    publier = reprise._publier_stage_exclusif

    def remplacer_parent(source, destination, export_fd):
        publier(source, destination, export_fd)
        ancien = candidat.with_name("candidat-ancien")
        candidat.rename(ancien)
        candidat.mkdir()
        (candidat / "tiers.json").write_text("tiers")

    monkeypatch.setattr(reprise, "_publier_stage_exclusif", remplacer_parent)
    with pytest.raises(ValueError, match="(?i)cible.*créée.*laissée"):
        reprise.appliquer(prive, preuve, sha)
    assert cible.read_bytes() == attendu
    assert (candidat / "tiers.json").read_text() == "tiers"


def test_appliquer_verifie_la_cible_en_dernier(tmp_path, atelier, monkeypatch):
    """Une altération déclenchée à la relecture du candidat est détectée ensuite sur la cible épinglée."""
    import benchab.qualite.reprise_export as reprise

    prive, cible, _ = atelier
    _candidat(prive, tmp_path)
    preuve = tmp_path / "candidat/preuve.json"
    sha = hashlib.sha256(preuve.read_bytes()).hexdigest()
    hacher = reprise._hachage_fd_borne

    def altérer_apres_publication(fd, budget):
        resultat = hacher(fd, budget)
        if cible.exists():
            cible.write_bytes(b"changement apres verification source\n")
        return resultat

    monkeypatch.setattr(reprise, "_hachage_fd_borne", altérer_apres_publication)
    with pytest.raises(ValueError, match="(?i)cible.*créée.*laissée"):
        reprise.appliquer(prive, preuve, sha)
    assert cible.read_bytes() == b"changement apres verification source\n"


def test_appliquer_refuse_dossier_export_remplace_pendant_derniere_relecture(tmp_path, atelier, monkeypatch):
    """Les octets exacts d’un ancien dirfd ne valident pas un chemin canonique remplacé."""
    import os

    import benchab.qualite.reprise_export as reprise

    prive, cible, attendu = atelier
    _, preuve, sha = _candidat(prive, tmp_path)
    hacher = reprise._hachage_fd_borne
    ancien = prive / "export-ancien"

    def deplacer_apres_relecture(fd, budget):
        resultat = hacher(fd, budget)
        if cible.exists() and not ancien.exists() and os.path.samestat(cible.lstat(), os.fstat(fd)):
            cible.parent.rename(ancien)
            cible.parent.mkdir()
            cible.write_bytes(b"tiers")
        return resultat

    monkeypatch.setattr(reprise, "_hachage_fd_borne", deplacer_apres_relecture)
    with pytest.raises(ValueError, match="(?i)cible.*créée.*laissée"):
        reprise.appliquer(prive, preuve, sha)
    assert (ancien / cible.name).read_bytes() == attendu
    assert cible.read_bytes() == b"tiers"


def test_cli_appliquer_est_diagnostic_sans_base_ni_mesure(tmp_path, atelier, capsys, monkeypatch):
    """La commande explicite n’ouvre pas Base() et ne journalise pas une mesure."""
    from benchab import cli

    prive, cible, attendu = atelier
    candidat, preuve, sha = _candidat(prive, tmp_path)
    monkeypatch.setattr(cli, "Base", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Base()")))
    arguments = ["--prive", str(prive), "controle", "appliquer-reprise-export",
                 "--preuve", str(preuve), "--sha256-preuve", sha]
    assert cli.main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["statut"] == "PASS"
    assert cible.read_bytes() == attendu
    assert (candidat / "preuve.json").is_file()


def test_cli_refus_postpublication_annonce_cible_conservee(tmp_path, atelier, capsys, monkeypatch):
    """La CLI renvoie 2 et indique que la cible créée n’a pas été supprimée."""
    import benchab.qualite.reprise_export as reprise
    from benchab import cli

    prive, cible, _ = atelier
    _candidat(prive, tmp_path)
    preuve = tmp_path / "candidat/preuve.json"
    sha = hashlib.sha256(preuve.read_bytes()).hexdigest()
    publier = reprise._publier_stage_exclusif

    def altérer(source, destination, fd_export):
        publier(source, destination, fd_export)
        cible.write_bytes(b"tiers concurrent\n")

    monkeypatch.setattr(reprise, "_publier_stage_exclusif", altérer)
    monkeypatch.setattr(cli, "Base", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Base()")))
    code = cli.main([
        "--prive", str(prive), "controle", "appliquer-reprise-export",
        "--preuve", str(preuve), "--sha256-preuve", sha,
    ])
    assert code == 2
    assert "créée et est laissée en place" in capsys.readouterr().err
    assert cible.read_bytes() == b"tiers concurrent\n"
