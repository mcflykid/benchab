"""Témoins synthétiques du contrôle de conservation des projets privés."""

import hashlib
import json

from benchab.donnees.base import Base
from benchab.qualite.publication import verifier


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _projet(tmp_path, *, retour=True, preparation=True):
    base = Base(tmp_path / "prive")
    projet = base.prive / "projets" / "demo"
    (projet / "reference").mkdir(parents=True)
    (projet / "sources").mkdir()
    (projet / "reference" / "modele.stl").write_bytes(b"modele")
    (projet / "sources" / "construire.py").write_bytes(b"source")
    suivi = {
        "schema": 1,
        "nom": "demo",
        "titre": "Projet synthétique",
        "type": "conception",
        "statut": "EN_CONCEPTION",
        "physique": {"imprime": False, "resultat": "inconnu"},
        "references_imprimees": [
            {
                "id": "ref-1",
                "preuve": "preuve utilisateur",
                "fichiers": [{"chemin": "reference/modele.stl", "sha256": _sha(projet / "reference" / "modele.stl")}],
            }
        ],
        "references_historiques": [],
        "travail_en_cours": {"fichiers": ["sources/construire.py"]},
        "sources_necessaires": ["sources/construire.py"],
        "evenements": [],
    }
    if preparation:
        pnn = projet / "preparations" / "P01"
        pnn.mkdir(parents=True)
        (pnn / "PREPARATION.json").write_text(json.dumps({"id": "P01", "sources": {}}))
        suivi["preparation_courante"] = "P01"
    if retour:
        retour_path = projet / "retours" / "retour.md"
        retour_path.parent.mkdir()
        retour_path.write_text("Observation utilisateur")
        suivi["evenements"] = [
            {
                "type": "retour_physique",
                "documents": [{"chemin": "retours/retour.md", "sha256": _sha(retour_path)}],
                "envois_identifies": [],
            }
        ]
    donnees = {"suivi": suivi}
    base.ecrire(
        "INSERT INTO projets(id,nom,etat,donnees_json) VALUES(?,?,?,?)",
        ("projets/demo", "demo", "EN_CONCEPTION", json.dumps(donnees)),
    )
    return base, projet


def test_verifier_signale_fichiers_actifs_absents(tmp_path):
    """Les références et le travail courant absents ne passent pas."""
    base, projet = _projet(tmp_path)
    (projet / "reference" / "modele.stl").unlink()
    (projet / "sources" / "construire.py").unlink()

    rapport = verifier(projet)

    assert rapport["statut"] == "FAIL"
    assert any("référence active absent" in erreur for erreur in rapport["erreurs"])
    assert any("travail courant absent" in erreur for erreur in rapport["erreurs"])
    assert rapport["validation_physique"] is False
    base.fermer()


def test_verifier_refuse_sha_altere(tmp_path):
    """Un contenu qui diffère de sa référence SHA est FAIL."""
    base, projet = _projet(tmp_path)
    (projet / "reference" / "modele.stl").write_bytes("altéré".encode())

    rapport = verifier(projet)

    assert rapport["statut"] == "FAIL"
    assert any("SHA déclaré" in erreur for erreur in rapport["erreurs"])
    base.fermer()


def test_verifier_preparation_courante_absente(tmp_path):
    """Une préparation courante déclarée doit avoir sa fiche Pnn."""
    base, projet = _projet(tmp_path)
    (projet / "preparations" / "P01" / "PREPARATION.json").unlink()

    rapport = verifier(projet)

    assert rapport["statut"] == "FAIL"
    assert any("préparation courante absente" in erreur for erreur in rapport["erreurs"])
    base.fermer()


def test_verifier_document_retour_manquant(tmp_path):
    """Un document humain identifié par un retour ne peut pas disparaître."""
    base, projet = _projet(tmp_path)
    (projet / "retours" / "retour.md").unlink()

    rapport = verifier(projet)

    assert rapport["statut"] == "FAIL"
    assert any("document de retour absent" in erreur for erreur in rapport["erreurs"])
    base.fermer()


def test_verifier_propre_reste_non_physique(tmp_path):
    """Un projet cohérent peut passer sans transformer les preuves numériques en validation physique."""
    base, projet = _projet(tmp_path)

    rapport = verifier(projet)

    assert rapport["statut"] == "WARN"
    assert rapport["validation_physique"] is False
    assert not rapport["erreurs"]
    base.fermer()


def test_source_historique_absente_reste_un_avertissement(tmp_path):
    """Une source Pnn historique manquante est signalée sans réécrire sa fiche figée."""
    base, projet = _projet(tmp_path)
    fiche = projet / "preparations" / "P01" / "PREPARATION.json"
    preparation = json.loads(fiche.read_text())
    preparation["sources"] = {"projets/ancien/src.py": "0" * 64}
    fiche.write_text(json.dumps(preparation))

    rapport = verifier(projet)

    assert rapport["statut"] == "WARN"
    assert not rapport["erreurs"]
    assert any("source historique P01 indisponible" in avertissement for avertissement in rapport["avertissements"])
    assert json.loads(fiche.read_text())["sources"]["projets/ancien/src.py"] == "0" * 64
    base.fermer()


def test_reference_historique_protegee_alteree_reste_fail(tmp_path):
    """Une ancienne référence reste protégée ; FAIL sur altération, jamais un simple avertissement historique."""
    base, projet = _projet(tmp_path)
    try:
        row = base.connexion.execute("SELECT donnees_json FROM projets").fetchone()
        donnees = json.loads(row[0])
        suivi = donnees["suivi"]
        suivi["references_historiques"] = suivi.pop("references_imprimees")
        suivi["references_imprimees"] = []
        base.ecrire("UPDATE projets SET donnees_json=?", (json.dumps(donnees),))
        (projet / "reference/modele.stl").write_bytes(b"mutation interdite")
        rapport = verifier(projet)
        assert rapport["statut"] == "FAIL"
        assert any("SHA déclaré" in e for e in rapport["erreurs"])
    finally:
        base.fermer()


def _resumer_preparation(base, projet):
    from benchab.projets.contrat_reprise import empreinte_origine

    donnees = json.loads(base.connexion.execute("SELECT donnees_json FROM projets").fetchone()[0])
    origine = json.loads(json.dumps(donnees))
    fiche = projet / "preparations/P01/PREPARATION.json"
    preparation = json.loads(fiche.read_text())
    spec = {"fichier": "preparations/P01/tranche.3mf", "sha256": "a" * 64}
    preparation["tranche_3mf"] = spec
    fiche.write_text(json.dumps(preparation))
    declarations = [{"source_originale": "sources/construire.py", "decision": "CONSERVÉ_ACTIF"}]
    declarations += [{"source_originale": "reference/modele.stl", "decision": "CONSERVÉ_ACTIF"}]
    declarations += [{"source_originale": spec["fichier"], "decision": "RÉSUMÉ_HISTORIQUE",
                      "raison": "Préparation ancienne résumée par décision de reprise"}]
    donnees["originebrute"] = {"qualite": origine}
    donnees["migration"] = {
        "contrat_natif": {
            "schema": 1,
            "origine_qualite_sha256": empreinte_origine(origine),
            "declarations": declarations,
            "fichiers_actifs": [{"chemin": "sources/construire.py", "gel": False, "sha256": None}],
            "preparations_historiques": {
                "P01": {
                    "sha256_original": _sha(fiche),
                    "statut": "historique résumé ; réimpression non établie",
                    "artefacts": [
                        {"source_originale": spec["fichier"], "sha256": spec["sha256"], "decision": "RÉSUMÉ_HISTORIQUE"}
                    ],
                }
            },
        }
    }
    base.ecrire("UPDATE projets SET donnees_json=?", (json.dumps(donnees),))
    return donnees


def test_resume_fige_ne_prouve_pas_reimpression_et_ne_cache_pas_fichier_actif(tmp_path):
    """Le tri explicite autorise un résumé WARN ; un fichier actif absent reste FAIL, sans recréer l'ancien 3MF."""
    base, projet = _projet(tmp_path)
    try:
        _resumer_preparation(base, projet)
        rapport = verifier(projet)
        assert not rapport["erreurs"]
        assert rapport["statut"] == "WARN"
        assert any("réimpression" in a for a in rapport["avertissements"])
        (projet / "sources/construire.py").unlink()
        rapport = verifier(projet)
        assert rapport["statut"] == "FAIL"
        assert any("contrat natif actif absent" in e for e in rapport["erreurs"])
    finally:
        base.fermer()


def test_resume_refuse_fiche_figee_alteree_et_reference_physique_ecartee(tmp_path):
    """Le résumé ne permet ni de réécrire Pnn ni de déclasser une référence physique ; FAIL dans chaque cas."""
    base, projet = _projet(tmp_path)
    try:
        donnees = _resumer_preparation(base, projet)
        fiche = projet / "preparations/P01/PREPARATION.json"
        original = fiche.read_bytes()
        fiche.write_bytes(original + b"\n")
        assert any("fiche résumée altérée" in e for e in verifier(projet)["erreurs"])
        fiche.write_bytes(original)
        d = donnees["migration"]["contrat_natif"]["declarations"][1]
        d.update(decision="ÉCARTÉ_LOT5", raison="preuve intermédiaire supposée")
        base.ecrire("UPDATE projets SET donnees_json=?", (json.dumps(donnees),))
        assert any("référence physique écartée" in e for e in verifier(projet)["erreurs"])
    finally:
        base.fermer()
