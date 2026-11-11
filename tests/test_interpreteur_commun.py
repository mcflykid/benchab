"""Régressions de l’interprétation commune et des budgets de caches concurrents, sans machine."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from zipfile import ZipFile

from benchab.controles.gcode_interpreter import Interpreter
from benchab.fabrication.comparer_remplissages import trajets_remplissage
from benchab.gcode import lecture
from benchab.gcode.continuite import controler


def test_cache_membre_concurrent_ne_compte_qu_une_fois_les_octets(tmp_path, monkeypatch):
    """Deux lectures réelles simultanées gardent un compte exact d’octets, sans doubler la réservation."""
    lecture.vider_cache_lecture()
    archive = tmp_path / "source.3mf"
    membre, contenu = "Metadata/plate_1.gcode", b"G1 X1\n"
    with ZipFile(archive, "w") as z:
        z.writestr(membre, contenu)
    barriere = Barrier(2)
    lire = ZipFile.read

    def lecture_synchronisee(z, *args, **kwargs):
        valeur = lire(z, *args, **kwargs)
        barriere.wait(timeout=5)
        return valeur

    monkeypatch.setattr(ZipFile, "read", lecture_synchronisee)
    with ThreadPoolExecutor(max_workers=2) as executant:
        resultats = list(executant.map(lambda _: lecture.lire_membre_3mf(archive, membre), range(2)))
    assert resultats == [contenu, contenu]
    assert lecture._CACHE_OCTETS == len(contenu)
    lecture.vider_cache_lecture()


def test_cache_analyse_concurrent_ne_compte_qu_une_fois_le_resultat(tmp_path):
    """Deux analyses simultanées rendent le même résultat avec une seule réservation en octets."""
    lecture.vider_cache_lecture()

    archive = tmp_path / "source.3mf"
    membre = "Metadata/plate_1.gcode"
    with ZipFile(archive, "w") as z:
        z.writestr(membre, "G1 X1\nG1 X2\n")
    barriere = Barrier(2)

    def analyser(lignes):
        nombre = sum(1 for _ in lignes)
        barriere.wait(timeout=5)
        return {"lignes": nombre}

    with ThreadPoolExecutor(max_workers=2) as executant:
        resultats = list(executant.map(
            lambda _: lecture.analyser_membre_gcode_3mf(archive, membre, {}, analyser), range(2),
        ))
    assert resultats == [{"lignes": 2}, {"lignes": 2}]
    assert lecture._ANALYSE_OCTETS == len(b'{"lignes":2}')
    lecture.vider_cache_lecture()


def test_flux_reutilise_le_contexte_3mf_sans_confondre_la_source(tmp_path, monkeypatch):
    """Deux plaques partagent l'archive ouverte, y compris avec une entrée de cache disponible."""
    import pytest

    from benchab.archives_3mf import Archive3MF

    f = tmp_path / "tranche.3mf"
    autre = tmp_path / "autre.3mf"
    p1, p2 = "Metadata/plate_1.gcode", "Metadata/plate_2.gcode"
    for chemin in (f, autre):
        with ZipFile(chemin, "w") as z:
            z.writestr(p1, "G90\n")
            z.writestr(p2, "M83\n")
    lecture.vider_cache_lecture()
    assert lecture.lire_membre_3mf(f, p1) == b"G90\n"

    def reouverture_interdite(*args, **kwargs):
        pytest.fail("le contexte 3MF ouvert doit être réutilisé")

    with Archive3MF(f) as archive:
        monkeypatch.setattr(lecture, "Archive3MF", reouverture_interdite)
        assert list(lecture.iter_lignes_gcode_3mf(f, p1, archive=archive)) == ["G90\n"]
        assert list(lecture.iter_lignes_gcode_3mf(f, p2, archive=archive)) == ["M83\n"]
        with pytest.raises(ValueError, match="contexte 3MF"):
            list(lecture.iter_lignes_gcode_3mf(autre, p1, archive=archive))
    lecture.vider_cache_lecture()


def test_interpreteur_reconnait_alias_lineaire_et_borne_ses_diagnostics():
    """G01 est linéaire ; le budget de messages borne la mémoire et signale la perte de détail."""
    lecteur = Interpreter(error_limit=3)
    lecteur.feed_line("M83")
    mouvement = lecteur.feed_line("G01 X1 E1 F600")
    assert mouvement["end"] == (1.0, 0.0, 0.0)
    assert mouvement["deposited_length"] == 1.0
    for numero in range(20):
        lecteur.feed_line(f"M{10000 + numero}")
    assert lecteur.errors_truncated is True
    assert len(lecteur.errors) <= 4


def test_continuite_reutilise_outils_et_refus_du_lecteur_commun(tmp_path):
    """T4 reste une identité numérique ; une commande sans modèle rend la couverture NON VÉRIFIÉE."""
    source = tmp_path / "source.gcode"
    contenu = "M83\nT4\n;TYPE:Outer wall\nG0 Z2\nG1 X1 E1 F600\n"
    source.write_text(contenu)
    assert controler(source)["statut"] == "PASS"
    source.write_text(contenu + "M999\n")
    assert controler(source)["statut"] == "NON VÉRIFIÉ"


def test_trajets_remplissage_equivalents_en_absolu_et_relatif():
    """Deux encodages du même trajet donnent 40 mm de déplacement, une rétraction et deux îlots."""
    relatif = "M83\n;TYPE:Sparse infill\nG0 X0 Y0\nG1 X10 E1\nG1 E-1\nG0 X30\nG1 E1\nG1 X40 E1\nG0 X60\n"
    absolu = "M82\nG92 E10\n;TYPE:Sparse infill\nG0 X0 Y0\nG1 X10 E11\nG1 E10\nG0 X30\nG1 E11\nG1 X40 E12\nG0 X60\n"
    for contenu in (absolu, relatif):
        resultat = trajets_remplissage(contenu)
        assert resultat["retractions"] == 1
        assert resultat["deplacements"] == 2
        assert resultat["longueur_deplacements_mm"] == 40
        assert resultat["longs_sans_retraction"] == 1
        assert resultat["ilots"] == 2
        assert resultat["couverture_complete"] is True


def test_trajets_remplissage_arc_non_mesurable_reste_explicite():
    """Un arc sans modèle ne produit pas une fausse couverture complète."""
    resultat = trajets_remplissage("M83\n;TYPE:Sparse infill\nG2 X10 Y0 I5 E1\n")
    assert resultat["statut"] == "NON VÉRIFIÉ"
    assert resultat["erreurs"]


def test_remboursement_exact_retraction_ne_devient_pas_trajet_sans_retraction():
    """Un déplacement XY de 100 mm remboursant la dette reste un trajet avec rétraction."""
    resultat = trajets_remplissage("M83\n;TYPE:Sparse infill\nG1 E-1\nG1 X100 E1\n")
    assert resultat["deplacements"] == 1
    assert resultat["longueur_deplacements_mm"] == 100
    assert resultat["longs_sans_retraction"] == 0
    assert resultat["ilots"] == 0


def test_deplacement_vertical_ne_gonfle_pas_les_mesures_xy_remplissage():
    """Les Z-hop seuls ne comptent pas dans les déplacements et longueurs XY historiques."""
    resultat = trajets_remplissage("M83\n;TYPE:Sparse infill\nG0 Z30\nG0 Z0\nG1 X10 E1\n")
    assert resultat["deplacements"] == 0
    assert resultat["longueur_deplacements_mm"] == 0
    assert resultat["longs_sans_retraction"] == 0
