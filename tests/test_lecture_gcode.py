from zipfile import ZipFile

import pytest

from benchab.gcode.lecture import (
    analyser_membre_gcode_3mf,
    empreinte_3mf,
    lire_membre_3mf,
    lire_texte_gcode_3mf,
    vider_cache_lecture,
)


def test_lecture_cache_par_empreinte_du_3mf(tmp_path):
    paquet = tmp_path / "tranche.3mf"
    with ZipFile(paquet, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "G1 X1\r\n")

    sha_initial = empreinte_3mf(paquet)
    assert lire_membre_3mf(paquet, "Metadata/plate_1.gcode") == b"G1 X1\r\n"
    assert lire_texte_gcode_3mf(paquet, "Metadata/plate_1.gcode") == "G1 X1\r\n"
    assert empreinte_3mf(paquet) == sha_initial

    vider_cache_lecture()


def test_cache_invalide_automatiquement_si_le_3mf_change(tmp_path):
    paquet = tmp_path / "tranche.3mf"
    membre = "Metadata/plate_1.gcode"
    for commande in ("G1 X1", "G1 X2"):
        with ZipFile(paquet, "w") as archive:
            archive.writestr(membre, commande)
        assert lire_texte_gcode_3mf(paquet, membre) == commande

    vider_cache_lecture()


def test_cache_ne_contourne_pas_la_garde_du_chemin_source(tmp_path):
    """La même empreinte ne rend pas admissible un chemin lié."""
    paquet = tmp_path / "tranche.3mf"
    membre = "Metadata/plate_1.gcode"
    with ZipFile(paquet, "w") as archive:
        archive.writestr(membre, "G1 X1\n")
    assert lire_membre_3mf(paquet, membre) == b"G1 X1\n"
    lien = tmp_path / "alias.3mf"
    lien.symlink_to(paquet)
    with pytest.raises(ValueError, match="Lien symbolique"):
        lire_membre_3mf(lien, membre)


def test_analyseur_gcode_reutilise_les_resultats_par_options(tmp_path):
    paquet = tmp_path / "tranche.3mf"
    membre = "Metadata/plate_1.gcode"
    with ZipFile(paquet, "w") as archive:
        archive.writestr(membre, "G1 X1\nG1 X3\n")
    appels = []

    def analyser(lignes):
        appels.append(1)
        return {"mouvements": sum(ligne.startswith("G1") for ligne in lignes)}

    premier = analyser_membre_gcode_3mf(paquet, membre, {"seuil": 1}, analyser)
    premier["mouvements"] = 0
    second = analyser_membre_gcode_3mf(paquet, membre, {"seuil": 1}, analyser)
    autre_option = analyser_membre_gcode_3mf(paquet, membre, {"seuil": 2}, analyser)

    assert second == {"mouvements": 2}
    assert autre_option == {"mouvements": 2}
    assert len(appels) == 2
    vider_cache_lecture()


def test_analyse_refuse_si_le_3mf_change_pendant_le_parse(tmp_path):
    paquet = tmp_path / "tranche.3mf"
    membre = "Metadata/plate_1.gcode"
    with ZipFile(paquet, "w") as archive:
        archive.writestr(membre, "G1 X1\n")

    def analyser(lignes):
        list(lignes)
        with ZipFile(paquet, "w") as archive:
            archive.writestr(membre, "G1 X2\n")
        return {"statut": "PASS"}

    with pytest.raises(ValueError, match="modifié pendant"):
        analyser_membre_gcode_3mf(paquet, membre, {}, analyser)

    vider_cache_lecture()


@pytest.mark.parametrize("cache_chaud", [False, True])
def test_contexte_refuse_le_remplacement_du_chemin_avant_le_sha(tmp_path, monkeypatch, cache_chaud):
    """Un changement après la garde refuse le résultat et laisse le cache exact."""
    from benchab.archives_3mf import Archive3MF
    from benchab.gcode import lecture

    ancien = tmp_path / "lecture.3mf"
    nouveau = tmp_path / "remplacement.3mf"
    membre = "Metadata/plate_1.gcode"
    for chemin, gcode in ((ancien, "G1 X11"), (nouveau, "G1 X22")):
        with ZipFile(chemin, "w") as archive:
            archive.writestr(membre, gcode)

    vider_cache_lecture()
    if cache_chaud:
        assert lire_membre_3mf(nouveau, membre) == b"G1 X22"

    def empreinte_apres_remplacement(chemin):
        if nouveau.exists():
            nouveau.replace(ancien)
        return empreinte_3mf(chemin)

    refus = []
    with monkeypatch.context() as patch:
        try:
            with Archive3MF(ancien) as archive:
                patch.setattr(lecture, "empreinte_3mf", empreinte_apres_remplacement)
                try:
                    lire_membre_3mf(ancien, membre, archive=archive)
                except ValueError as erreur:
                    refus.append(str(erreur))
        except ValueError as erreur:
            # Le remplacement peut aussi changer le ctime de l’inode déjà ouvert.
            assert "modifiée pendant" in str(erreur)
    assert refus and "contexte" in refus[0]
    assert lire_membre_3mf(ancien, membre) == b"G1 X22"
    vider_cache_lecture()
