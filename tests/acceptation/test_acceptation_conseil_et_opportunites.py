"""Grille du conseil et règles sur des données entièrement générées pour le test."""

import hashlib
import json

from fixtures_votes import ajouter_critique_synthetique, creer_atelier_vote

from benchab.cli import main
from benchab.controles.opportunite_decoupe import main as controler_opportunite_decoupe
from benchab.donnees.base import Base
from benchab.vote.conseil import produire


def test_rejeu_du_conseil_est_deterministe_et_conserve_toutes_les_voix(tmp_path):
    atelier = creer_atelier_vote(tmp_path)
    try:
        a = produire(atelier["base"], atelier["dossier"])
        b = produire(atelier["base"], atelier["dossier"])
        assert a["votes"] == b["votes"]
        assert a["empreinte_vote"] == b["empreinte_vote"]
        assert len(a["votes"]) == 7
        assert all(a["votes"][role]["origine"] == "contrôles déterministes" for role in ("A2", "A3", "A4", "A6"))
        assert a["statut"] == "APPROUVÉ"
        assert a["action_autorisee_par_vote"] is False
    finally:
        atelier["fermer"]()


def test_verification_indispensable_absente_bloque_meme_avec_trois_avis_ia_pour(tmp_path, capsys):
    atelier = creer_atelier_vote(tmp_path)
    try:
        donnees = atelier["donnees"]
        donnees["controles"][0]["fichier"] = "rapport-inexistant.json"
        ajouter_critique_synthetique(atelier["base"], atelier["dossier"], donnees)
        atelier["dossier"].write_text(json.dumps(donnees))
        assert main(["--prive", str(atelier["base"].prive), "vote", str(atelier["dossier"])]) == 0
        resultat = json.loads(capsys.readouterr().out)
        assert resultat["statut"].startswith("BLOQUÉ")
        assert all(resultat["votes"][role]["vote"] == "abstention" for role in ("A2", "A3", "A4", "A6"))
        assert all(resultat["votes"][role]["vote"] == "pour" for role in ("A1", "A5", "A7"))
        assert resultat["verifications_manquantes"]
        assert atelier["base"].connexion.execute("SELECT code FROM mesures").fetchone()[0] == 0
    finally:
        atelier["fermer"]()


def test_opportunite_signale_variante_obligatoire_sans_lancer_de_generation(tmp_path):
    base = Base(tmp_path / "prive")
    try:
        source = base.prive / "projets/synthetique/candidats/piece.stl"
        source.parent.mkdir(parents=True)
        source.write_bytes("maillage synthétique non interprété".encode("utf-8"))
        sha_piece = hashlib.sha256(source.read_bytes()).hexdigest()
        rapports = {}
        for famille, declencheurs in (
            ("forme", {"retrecissement_naturel": False, "volumes_fusionnes": False}),
            (
                "imprimabilite",
                {
                    "supports": True,
                    "surplombs": True,
                    "couture_visible_ou_fonctionnelle": True,
                    "depasse_plateau": False,
                },
            ),
        ):
            path = base.prive / f"rapports/{famille}.json"
            path.write_text(
                json.dumps({"statut": "PASS", "_rapport": {"source_sha256": sha_piece}, "declencheurs": declencheurs})
            )
            rapports[famille] = {
                "fichier": path.relative_to(base.prive).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        demande = base.prive / "demandes/opportunite.json"
        demande.parent.mkdir()
        demande.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "piece": {
                        "fichier": source.relative_to(base.prive).as_posix(),
                        "sha256": sha_piece,
                    },
                    "rapports": rapports,
                }
            )
        )
        empreinte_source = hashlib.sha256(source.read_bytes()).hexdigest()

        code = controler_opportunite_decoupe([str(demande)], base=base)

        resultat = json.loads((base.prive / "rapports/opportunite-decoupe.json").read_text())
        assert code == resultat["code"] == 2
        assert resultat["statut"] == "FAIL"
        assert {declencheur["id"] for declencheur in resultat["declencheurs"]} == {
            "supports",
            "surplombs",
            "couture_visible_ou_fonctionnelle",
        }
        assert resultat["choix_automatique"] is False
        assert resultat["decoupe_generee"] is False
        assert hashlib.sha256(source.read_bytes()).hexdigest() == empreinte_source
    finally:
        base.fermer()
