"""Contrats historiques des retours, adaptés à la base privée canonique."""

import json

import pytest

from benchab.donnees.base import Base
from benchab.fabrication.retour import main
from benchab.projets.protections import protections
from benchab.projets.retours import confirmer, etat_projet


@pytest.fixture
def atelier(tmp_path):
    base = Base(tmp_path / "prive")
    projet = base.prive / "projets/fixture"
    projet.mkdir()
    yield base, projet
    base.fermer()


def test_retour_sans_preparation_garde_une_source_verifiable(atelier):
    """Un retour humain reste contrôlable ; une capture altérée donne FAIL, sans unité."""
    from benchab.connaissances.memoire import controler

    base, projet = atelier
    retour = confirmer(base, projet, "reussie", "Observation de la fixture")
    rapport = controler(base.prive)
    assert rapport["erreurs"] == []
    assert rapport["statut"] == "PASS"
    ligne = base.connexion.execute("SELECT * FROM connaissances").fetchone()
    source = base.prive / ligne["source"]
    assert json.loads(source.read_text()) == retour["retour"]
    assert ligne["texte"] == "Observation de la fixture"
    assert base.connexion.execute("SELECT count(*) FROM impressions").fetchone()[0] == 0
    source.write_text("{}\n")
    assert controler(base.prive)["statut"] == "FAIL"


def test_reference_reussie_conservee_apres_defaut_et_refus_atomique(atelier):
    base, projet = atelier
    fichier = projet / "v1.stl"
    fichier.write_bytes(b"original")
    resultat = confirmer(base, projet, "reussie", "Constat humain", ["v1.stl"])
    assert resultat["conservation"]["simulation"] is True
    confirmer(base, projet, "defaut", "Défaut constaté")
    etat = etat_projet(base, projet)
    assert len(etat["references_imprimees"]) == 1
    assert len(etat["evenements"]) == 2
    assert etat["physique"]["resultat"] == "inconnu"
    avant = base.connexion.execute("SELECT donnees_json FROM projets").fetchone()[0]
    with pytest.raises(ValueError, match="référence réussie"):
        confirmer(base, projet, "arretee", "Arrêt humain", ["v1.stl"])
    assert base.connexion.execute("SELECT donnees_json FROM projets").fetchone()[0] == avant
    assert fichier.read_bytes() == b"original"
    assert "v1.stl" in protections(projet)
    assert base.connexion.execute("SELECT count(*) FROM impressions").fetchone()[0] == 0


def test_remplacement_cible_garde_historique_et_autre_piece(atelier):
    base, projet = atelier
    for nom in ("a-v1.stl", "a-v2.stl", "b.stl"):
        (projet / nom).write_bytes(nom.encode())
    a = confirmer(base, projet, "reussie", "A validé", ["a-v1.stl"])
    ident = a["conservation"]["references_imprimees"][0]["id"]
    confirmer(base, projet, "reussie", "B validé", ["b.stl"])
    resultat = confirmer(base, projet, "reussie", "A remplacé explicitement", ["a-v2.stl"], remplacer=[ident])
    etat = etat_projet(base, projet)
    assert [r["id"] for r in etat["references_historiques"]] == [ident]
    assert len(etat["references_imprimees"]) == 2
    assert {f["chemin"] for r in etat["references_imprimees"] for f in r["fichiers"]} == {"a-v2.stl", "b.stl"}
    assert set(protections(projet)) == {"a-v1.stl", "a-v2.stl", "b.stl"}
    assert (projet / "a-v1.stl").exists()
    assert resultat["conservation"]["intermediaires"] == []


def test_reussite_sans_selection_ne_choisit_aucune_reference(atelier):
    base, projet = atelier
    confirmer(base, projet, "reussie", "Impression réussie", ensemble_complet=True)
    etat = etat_projet(base, projet)
    assert etat["references_imprimees"] == []
    assert etat["physique"]["resultat"] == "reussie"


def test_preparation_figee_et_envoi_seulement_candidat(atelier):
    from benchab.projets.envois import enregistrer

    base, projet = atelier
    dossier = projet / "preparations/P01"
    dossier.mkdir(parents=True)
    fiche = dossier / "PREPARATION.json"
    fiche.write_text(json.dumps({"id": "P01", "gcode": {"1": {"commandes_sha256": "inconnue"}}, "envoi": None}))
    source = base.prive / "export.gcode"
    source.write_text("G1 X1 E1\n")
    enregistrer(base, projet, "P01", source)
    avant = fiche.read_bytes()
    retour = confirmer(
        base,
        projet,
        "defaut",
        "Observation humaine",
        preparation="P01",
        pieces=[{"piece": "fixture", "version": "v1", "plaques": ["1"]}],
    )["retour"]
    assert retour["envois_identifies"] == []
    assert len(retour["envois_candidats_preparation"]) == 1
    assert retour["portee"] == "PARTIELLE"
    assert fiche.read_bytes() == avant
    assert base.connexion.execute("SELECT resultat FROM impressions").fetchone()[0] == "echec"
    assert base.connexion.execute("SELECT validation_physique FROM retours").fetchone()[0] == 0


def test_cli_resultat_explicite_sans_preparation(atelier):
    base, projet = atelier
    assert main(["--projet", str(projet), "--resultat", "arretee", "--preuve-retour", "Arrêt humain"], base=base) == 0
    assert etat_projet(base, projet)["evenements"][0]["resultat"] == "arretee"
    assert main(["--projet", str(projet), "--resultat", "reussie"], base=base) == 2


def test_chemin_reference_exterieur_refuse_sans_fait(atelier):
    base, projet = atelier
    with pytest.raises(ValueError, match="relatif"):
        confirmer(base, projet, "reussie", "Preuve humaine", ["../autre.stl"])
    assert base.connexion.execute("SELECT count(*) FROM projets").fetchone()[0] == 0


def test_reference_deja_protegee_alteree_interdit_nouvelle_reference(atelier):
    base, projet = atelier
    ancien, nouveau = projet / "ancien.stl", projet / "nouveau.stl"
    ancien.write_bytes(b"original")
    nouveau.write_bytes(b"nouveau")
    confirmer(base, projet, "reussie", "Observation humaine", [ancien.name])
    ancien.write_bytes(b"modifie")
    with pytest.raises(ValueError, match="protégée altérée"):
        confirmer(base, projet, "reussie", "Observation humaine", [nouveau.name])
    assert len(etat_projet(base, projet)["evenements"]) == 1


@pytest.mark.parametrize(
    "ensemble,travail,attendu",
    [(True, False, "IMPRIME"), (False, False, "EN_PREPARATION"), (True, True, "EN_PREPARATION")],
)
def test_transition_etat_seulement_apres_retour_global_sans_travail(atelier, ensemble, travail, attendu):
    """Le retour global clôt la préparation sans effacer un défaut.

    Un retour partiel ou un travail actif reste en préparation ; aucune réussite déduite.
    """
    base, projet = atelier
    courant = ["actif.stl"] if travail else []
    if travail:
        (projet / "actif.stl").write_bytes(b"fixture")
    donnees = {"suivi": {"statut": "EN_PREPARATION", "travail_en_cours": {"fichiers": courant}}}
    base.ecrire(
        "INSERT INTO projets(id,nom,etat,donnees_json) VALUES(?,?,?,?)",
        ("projets/fixture", "fixture", "EN_PREPARATION", json.dumps(donnees)),
    )
    confirmer(base, projet, "defaut", "Défaut observé par une personne", ensemble_complet=ensemble)
    row = base.connexion.execute("SELECT etat,donnees_json FROM projets").fetchone()
    assert row["etat"] == attendu
    suivi = json.loads(row["donnees_json"])["suivi"]
    assert suivi["statut"] == attendu
    assert suivi["physique"]["resultat"] == ("defaut" if ensemble else "inconnu")


def test_cli_conserve_une_duree_observee_sans_collecte_machine(atelier):
    """La déclaration en secondes est conservée dans la preuve du retour."""
    base, projet = atelier
    args = ["--projet", str(projet), "--resultat", "arretee", "--preuve-retour", "Chronomètre",
            "--duree-observee-s", "123.5"]
    assert main(args, base=base) == 0
    retour, = etat_projet(base, projet)["evenements"]
    assert retour["duree_observee_s"] == 123.5
    assert retour["resultat"] == "arretee"
    assert main([*args[:-1], "nan"], base=base) == 2
    assert etat_projet(base, projet)["evenements"] == [retour]


@pytest.mark.parametrize("duree", [0, -1, True, "123", 10**1000])
def test_retour_refuse_une_duree_non_positive_ou_non_numerique(atelier, duree):
    """Une durée invalide ne produit aucune déclaration persistée."""
    base, projet = atelier
    with pytest.raises(ValueError, match="Durée observée"):
        confirmer(base, projet, "reussie", "Chronomètre", duree_observee_s=duree)
    assert not etat_projet(base, projet).get("evenements")
