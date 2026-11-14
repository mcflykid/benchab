import hashlib
import json
from pathlib import Path

import benchab.controles.opportunite_decoupe as opportunite_decoupe
from benchab.controles.opportunite_decoupe import AXES, evaluer, main
from benchab.donnees.base import Base


def _ecrire_json(racine: Path, relatif: str, donnees) -> dict:
    path = racine / relatif
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(donnees), encoding="utf-8")
    return {"fichier": relatif, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _sha256_fichier(path: Path) -> str:
    empreinte = hashlib.sha256()
    with path.open("rb") as fichier:
        for bloc in iter(lambda: fichier.read(1024 * 1024), b""):
            empreinte.update(bloc)
    return empreinte.hexdigest()


def _indicateurs(racine: Path, *, forme=None, imprimabilite=None) -> dict:
    forme = forme or {"retrecissement_naturel": False, "volumes_fusionnes": False}
    imprimabilite = imprimabilite or {
        "supports": False,
        "surplombs": False,
        "couture_visible_ou_fonctionnelle": False,
        "depasse_plateau": False,
    }
    piece = _ecrire_json(racine, "candidats/piece-synthetique.stl", "maillage factice")
    sha_piece = piece["sha256"]
    return {
        "schema": 1,
        "piece": piece,
        "rapports": {
            "forme": _ecrire_json(
                racine,
                "rapports/forme.json",
                {"statut": "PASS", "_rapport": {"source_sha256": sha_piece}, "declencheurs": forme},
            ),
            "imprimabilite": _ecrire_json(
                racine,
                "rapports/imprimabilite.json",
                {
                    "statut": "PASS",
                    "_rapport": {"source_sha256": sha_piece},
                    "declencheurs": imprimabilite,
                },
            ),
        },
    }


def _axes():
    valeurs = {axe: "Évaluation qualitative synthétique." for axe in AXES if axe != "estimation_reussite"}
    valeurs["estimation_reussite"] = {
        "evaluation": "qualitative : incertaine",
        "justification": "Aucune probabilité numérique mesurée dans ce scénario.",
        "mesuree": False,
    }
    return valeurs


def _comparaison(racine: Path, sha_source: str):
    pieces = [
        _ecrire_json(racine, f"variantes/pied-{index}.stl", f"pièce découpée synthétique {index}") for index in (1, 2)
    ]
    return {
        "monobloc": _axes(),
        "variantes_decoupees": [
            {
                "id": "variante-pieds-separes",
                "source_sha256": sha_source,
                "pieces": pieces,
                "axes": _axes(),
            }
        ],
    }


def test_indicateurs_synthetiques_absents_restent_non_verifies(tmp_path):
    rapport = evaluer(tmp_path, {"schema": 1, "piece": {}, "rapports": {}})

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["declencheurs"] == []
    assert rapport["manquants"]
    assert rapport["decoupe_generee"] is False


def test_rapports_complets_sans_declencheur_ne_demandent_pas_de_variante(tmp_path):
    donnees = _indicateurs(tmp_path)

    rapport = evaluer(tmp_path, donnees)

    assert rapport["statut"] == "PASS"
    assert rapport["declencheurs"] == []
    assert rapport["choix_final_humain_requis"] is False
    assert rapport["choix_automatique"] is False


def test_declencheur_sourcé_sans_comparaison_echoue_et_ne_genere_pas_de_coupe(tmp_path):
    donnees = _indicateurs(tmp_path, forme={"retrecissement_naturel": True, "volumes_fusionnes": False})

    rapport = evaluer(tmp_path, donnees)

    assert rapport["statut"] == "FAIL"
    assert rapport["declencheurs"] == [
        {"famille": "forme", "id": "retrecissement_naturel", "rapport": "rapports/forme.json"}
    ]
    assert any("comparaison complète" in manque for manque in rapport["manquants"])
    assert rapport["decoupe_generee"] is False
    assert rapport["choix_automatique"] is False


def test_declencheur_compare_monobloc_et_variante_sur_les_sept_axes(tmp_path):
    donnees = _indicateurs(
        tmp_path,
        imprimabilite={
            "supports": True,
            "surplombs": False,
            "couture_visible_ou_fonctionnelle": True,
            "depasse_plateau": False,
        },
    )
    donnees["comparaison"] = _comparaison(tmp_path, donnees["piece"]["sha256"])

    rapport = evaluer(tmp_path, donnees)

    assert rapport["statut"] == "PASS"
    assert rapport["comparaison"]["complete"] is True
    assert rapport["comparaison"]["axes_requis"] == list(AXES)
    assert rapport["choix_final_humain_requis"] is True
    assert rapport["choix_automatique"] is False
    assert rapport["decoupe_generee"] is False
    assert rapport["comparaison"]["variantes_decoupees"][0]["axes"] == _axes()


def test_comparaison_incomplete_ou_estimation_mesuree_ne_passe_pas(tmp_path):
    donnees = _indicateurs(tmp_path, forme={"retrecissement_naturel": True, "volumes_fusionnes": False})
    comparaison = _comparaison(tmp_path, donnees["piece"]["sha256"])
    comparaison["variantes_decoupees"][0]["axes"]["couture"] = ""
    donnees["comparaison"] = comparaison

    rapport = evaluer(tmp_path, donnees)

    assert rapport["statut"] == "FAIL"
    assert rapport["comparaison"]["complete"] is False

    comparaison = _comparaison(tmp_path, donnees["piece"]["sha256"])
    comparaison["variantes_decoupees"][0]["axes"]["estimation_reussite"]["mesuree"] = True
    donnees["comparaison"] = comparaison
    assert evaluer(tmp_path, donnees)["statut"] == "FAIL"


def test_empreinte_perimee_ne_peut_pas_declencher_un_pass(tmp_path):
    donnees = _indicateurs(tmp_path)
    (tmp_path / "rapports/forme.json").write_text('{"statut":"PASS","declencheurs":{}}', encoding="utf-8")

    rapport = evaluer(tmp_path, donnees)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert any("empreinte périmée" in manque for manque in rapport["manquants"])


def test_main_lit_et_ecrit_dans_base_prive(tmp_path):
    base = Base(tmp_path / "prive")
    try:
        demande = base.prive / "demandes/opportunite.json"
        demande.parent.mkdir()
        demande.write_text(json.dumps(_indicateurs(base.prive)), encoding="utf-8")

        code = main([str(demande)], base=base)

        assert code == 0
        rapport = base.prive / "rapports/opportunite-decoupe.json"
        assert json.loads(rapport.read_text())["statut"] == "PASS"
    finally:
        base.fermer()


def test_main_refuse_un_json_sans_provenance_sans_le_modifier(tmp_path, capsys):
    """Une destination étrangère donne un refus lisible et reste intacte."""
    demande = tmp_path / "demande.json"
    demande.write_text(json.dumps(_indicateurs(tmp_path)), encoding="utf-8")
    cible = tmp_path / "rapport.json"
    original = b'{"note":"a conserver"}'
    cible.write_bytes(original)

    assert main([str(demande), "--sortie", str(cible)], prive=tmp_path) == 2
    sortie = capsys.readouterr()
    assert "Destination existante non reconnue" in sortie.err
    assert sortie.out == ""
    assert cible.read_bytes() == original


def test_resume_public_conserve_les_sources_du_diagnostic(capsys, atelier_initialise):
    """Le rapport privé contient les sources et comparaisons derrière le résumé."""
    from benchab.cli import main as cli

    prive = atelier_initialise
    demande = prive / "demande.json"
    demande.write_text(json.dumps(_indicateurs(prive)), encoding="utf-8")
    cible = prive / "rapport opportunité.json"
    args = ["--prive", str(prive), "controle", "opportunite-decoupe", str(demande)]
    assert cli([*args, "--resume", "--sortie", str(cible)]) == 0
    resume = json.loads(capsys.readouterr().out)
    rapport = json.loads(cible.read_bytes())
    assert resume["statut"] == rapport["statut"] == "PASS"
    assert resume["code"] == rapport["code"] == 0
    assert resume["rapport"] == str(cible)
    assert resume["nombre_declencheurs"] == len(rapport["declencheurs"])
    assert rapport["rapports_sources"] and "rapports_sources" not in resume
    assert resume["choix_final_humain_requis"] == rapport["choix_final_humain_requis"]


def test_rapport_declencheur_doit_etre_lie_a_la_piece_courante(tmp_path):
    donnees = _indicateurs(tmp_path, forme={"retrecissement_naturel": True, "volumes_fusionnes": False})
    rapport = tmp_path / donnees["rapports"]["forme"]["fichier"]
    donnees_rapport = json.loads(rapport.read_text(encoding="utf-8"))
    donnees_rapport["_rapport"]["source_sha256"] = "0" * 64
    rapport.write_text(json.dumps(donnees_rapport), encoding="utf-8")
    donnees["rapports"]["forme"]["sha256"] = hashlib.sha256(rapport.read_bytes()).hexdigest()

    resultat = evaluer(tmp_path, donnees)

    assert resultat["statut"] == "NON VÉRIFIÉ"
    assert not any(item["id"] == "retrecissement_naturel" for item in resultat["declencheurs"])
    assert any("non lié à l'empreinte" in manque for manque in resultat["manquants"])


def test_maillage_superieur_a_16_mio_est_empreinte_sans_chargement_complet(tmp_path):
    chemin = tmp_path / "candidats/maillage-large.stl"
    chemin.parent.mkdir(parents=True)
    with chemin.open("wb") as fichier:
        fichier.truncate(17 * 1024 * 1024)
    donnees = _indicateurs(tmp_path)
    donnees["piece"] = {
        "fichier": "candidats/maillage-large.stl",
        "sha256": _sha256_fichier(chemin),
    }
    for spec in donnees["rapports"].values():
        rapport = tmp_path / spec["fichier"]
        contenu = json.loads(rapport.read_text(encoding="utf-8"))
        contenu["_rapport"]["source_sha256"] = donnees["piece"]["sha256"]
        rapport.write_text(json.dumps(contenu), encoding="utf-8")
        spec["sha256"] = hashlib.sha256(rapport.read_bytes()).hexdigest()

    assert evaluer(tmp_path, donnees)["statut"] == "PASS"


def test_variante_sans_fichiers_decoupes_ne_passe_pas(tmp_path):
    donnees = _indicateurs(tmp_path, forme={"retrecissement_naturel": True, "volumes_fusionnes": False})
    donnees["comparaison"] = {
        "monobloc": _axes(),
        "variantes_decoupees": [{"id": "variante-fantome", "axes": _axes()}],
    }

    resultat = evaluer(tmp_path, donnees)

    assert resultat["statut"] == "FAIL"
    assert resultat["comparaison"]["complete"] is False
    assert any("au moins deux pièces découpées" in manque for manque in resultat["manquants"])


def test_mutation_rapport_pendant_evaluation_empeche_publication(tmp_path, monkeypatch, capsys):
    base = Base(tmp_path / "prive")
    try:
        donnees = _indicateurs(base.prive)
        demande = base.prive / "demandes/opportunite.json"
        demande.parent.mkdir()
        demande.write_text(json.dumps(donnees), encoding="utf-8")
        original = opportunite_decoupe.evaluer

        def evaluer_puis_muter(racine, manifeste):
            resultat = original(racine, manifeste)
            rapport = base.prive / donnees["rapports"]["forme"]["fichier"]
            rapport.write_text("rapport modifié après contrôle", encoding="utf-8")
            return resultat

        monkeypatch.setattr(opportunite_decoupe, "evaluer", evaluer_puis_muter)

        code = main([str(demande)], base=base)

        assert code == 2
        assert "Entrée modifiée pendant le contrôle" in capsys.readouterr().err
        assert not (base.prive / "rapports/opportunite-decoupe.json").exists()
    finally:
        base.fermer()
