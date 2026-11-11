"""Contrat de conception et diagnostic local. Ne génère aucune image et ne contacte aucun service."""

import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def lire_conception(projet):
    """Lit la fiche de conception du projet, ou retourne None si elle est absente."""
    fichier = Path(projet) / "CONCEPTION.json"
    if not fichier.exists():
        return None
    data = json.loads(fichier.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("reference"), dict):
        raise ValueError("CONCEPTION.json : objet et reference requis")
    if data.get("reference", {}).get("mode") not in ("aucune", "fournie", "generee"):
        raise ValueError("reference.mode attendu : aucune, fournie ou generee")
    if data.get("type_forme") not in ("technique", "organique", "mixte"):
        raise ValueError("type_forme attendu : technique, organique ou mixte")
    dimensions = data.get("dimensions_mm", {})
    if not isinstance(dimensions, dict):
        raise ValueError("dimensions_mm : objet de dimensions nommé requis")
    for nom, valeur in dimensions.items():
        if isinstance(valeur, bool) or not isinstance(valeur, (int, float)) or not math.isfinite(valeur) or valeur <= 0:
            raise ValueError(f"dimension {nom} : nombre fini positif en mm attendu")
    return data


def sha(path):
    """Retourne le SHA-256 hexadécimal des octets du fichier."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def module_references():
    """Charge le module public des références visuelles à la demande."""
    from benchab.references_visuelles import references

    return references


def revue_concept_possible(rapport):
    """Indique si toutes les vues autorisent une revue du concept malgré des perspectives non vérifiées."""
    vues = rapport.get("vues", {})
    return (
        rapport.get("liaison_modele") == "PASS"
        and bool(vues)
        and rapport.get("couverture_candidats", {}).get("statut") == "PASS"
        and all(
            v.get("statut") == "PASS"
            or (v.get("statut") == "NON VÉRIFIÉ" and v.get("projection") == "concept" and bool(v.get("rendu_sha256")))
            for v in vues.values()
        )
    )


def couvrir_candidats(projet, resultat):
    """Même ensemble de STL que celui transmis à preparation.py par le Makefile."""
    attendus = {str(p.resolve()) for p in (Path(projet) / "candidats").glob("*.stl")}
    rendus = set(resultat.get("sources_modele", {}))
    resultat["couverture_candidats"] = {
        "attendus": sorted(attendus),
        "rendus": sorted(rendus),
        "statut": "PASS" if attendus and attendus == rendus else "FAIL",
    }
    if not attendus:
        if resultat["statut"] != "FAIL":
            resultat.update(statut="NON VÉRIFIÉ", raison="Aucun STL candidat à comparer.")
    elif attendus != rendus:
        resultat.update(statut="FAIL", raison="Les rendus ne couvrent pas exactement les STL candidats à préparer.")
    return resultat


def retenir(projet, rapport, auteur, motif, concept=False):
    """Construit la revue visuelle liée aux empreintes après vérification des candidats. L’appelant la publie."""
    projet = Path(projet).resolve()
    rapport = Path(rapport).resolve()
    contrat = projet / "REFERENCES-VISUELLES.json"
    lire_conception(projet)
    if not auteur or not auteur.strip() or not motif or not motif.strip():
        raise ValueError("Auteur et motif de revue requis ; examiner les images avant de retenir.")
    resultat = couvrir_candidats(projet, module_references().audit_contrat(contrat, rapport))
    if resultat["statut"] != "PASS" and not (
        concept and resultat["statut"] == "NON VÉRIFIÉ" and revue_concept_possible(resultat)
    ):
        raise ValueError(
            "Évaluation absente, périmée, en échec ou incomplète : " + json.dumps(resultat, ensure_ascii=False)
        )
    return {
        "schema": 1,
        "rapport": os.path.relpath(rapport, projet),
        "rapport_sha256": sha(rapport),
        "contrat_sha256": sha(contrat),
        "conception_sha256": sha(projet / "CONCEPTION.json"),
        "besoin_sha256": sha(projet / "BESOIN.md") if (projet / "BESOIN.md").exists() else None,
        "revue_concept": concept,
        "auteur": auteur,
        "motif": motif,
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "limite": "Revue documentée du concept, aucune validation métrique de perspective ni validation physique.",
    }


def proposer(data):
    """Propose un parcours de conception et de contrôle à partir du type de forme déclaré."""
    forme = data["type_forme"]
    return {
        "technique": "CAO paramétrique",
        "organique": "Blender",
        "mixte": "CAO pour interfaces cotées, Blender pour enveloppe",
    }[forme]


def diagnostic():
    """Inventorie les outils et la cible déclarée sans modifier le profil du slicer."""
    from benchab.machine.configuration import charger

    slicer = charger()["slicer"]
    executables = {
        "blender": shutil.which("blender"),
        "make": shutil.which("make"),
        "python_atelier": sys.executable,
        "slicer": slicer["executable"],
    }
    return {
        "interpreteur": sys.executable,
        "moteur_tranchage": {**slicer, "version_reelle_verifiee": False},
        "executables": {
            nom: {"chemin": chemin, "present": bool(chemin and Path(chemin).is_file() and os.access(chemin, os.X_OK))}
            for nom, chemin in executables.items()
        },
        "modules": {nom: importlib.util.find_spec(nom) is not None for nom in ("PIL", "numpy", "trimesh", "build123d")},
        "limite": "Présence seulement : versions, connexion MCP, CUDA/Metal et fonctionnement non testés.",
    }


def verifier(projet):
    """Vérifie la revue et ses empreintes. Retourne PASS, FAIL, NON VÉRIFIÉ, NON APPLICABLE ou REVU."""
    projet = Path(projet).resolve()
    data = lire_conception(projet)
    contrat = projet / "REFERENCES-VISUELLES.json"
    if data is None and not contrat.exists():
        return {"statut": "NON APPLICABLE", "raison": "Aucun contrat visuel déclaré."}
    actif = data is not None and data["reference"]["mode"] != "aucune"
    if actif and not contrat.exists():
        return {
            "statut": "NON VÉRIFIÉ",
            "raison": "Référence demandée : figer REFERENCES-VISUELLES.json avant validation.",
        }
    if not actif and not contrat.exists():
        return {
            "statut": "NON APPLICABLE",
            "raison": "Projet déclaré sans référence visuelle.",
            "parcours": proposer(data),
        }
    pointeur = projet / "REVUE-VISUELLE.json"
    if not pointeur.exists():
        return {"statut": "NON VÉRIFIÉ", "raison": "Retenir une évaluation courante dans REVUE-VISUELLE.json."}
    revue = json.loads(pointeur.read_text())
    rapport = projet / revue["rapport"]
    besoin = projet / "BESOIN.md"
    if (
        sha(rapport) != revue["rapport_sha256"]
        or sha(contrat) != revue["contrat_sha256"]
        or sha(projet / "CONCEPTION.json") != revue["conception_sha256"]
        or (sha(besoin) if besoin.exists() else None) != revue.get("besoin_sha256")
    ):
        return {"statut": "FAIL", "raison": "Revue périmée : besoin, conception, référence ou rapport modifié."}
    resultat = couvrir_candidats(projet, module_references().audit_contrat(contrat, rapport))
    if (
        resultat["statut"] == "NON VÉRIFIÉ"
        and revue.get("revue_concept") is True
        and revue.get("auteur", "").strip()
        and revue.get("motif", "").strip()
        and revue_concept_possible(resultat)
    ):
        resultat["statut"] = "REVU"
        resultat["revue"] = {"auteur": revue["auteur"], "motif": revue["motif"], "date_utc": revue["date_utc"]}
        resultat["limite_revue"] = "Concept examiné ; perspective non validée métriquement, aucune preuve physique."
    return resultat


def brief(projet):
    """Prépare la synthèse de conception à partir du besoin et des données locales du projet."""
    projet = Path(projet).resolve()
    data = lire_conception(projet)
    if data is None or not data.get("objectif", "").strip():
        raise ValueError("Renseigner objectif dans CONCEPTION.json avant de préparer une référence.")
    besoin = projet / "BESOIN.md"
    instructions = [
        "Créer une proposition visuelle de conception, pas une preuve de fabrication.",
        f"Objectif : {data['objectif']}",
        "Dimensions imposées en mm : " + json.dumps(data.get("dimensions_mm", {}), ensure_ascii=False),
        "Contraintes : " + json.dumps(data.get("contraintes", []), ensure_ascii=False),
        "Style : " + data.get("style", ""),
        "Présenter un objet isolé, fond simple ; distinguer le volume des ombres et des textures.",
        "Toute profondeur ou interface non cotée reste une hypothèse à expliciter.",
        "Une image perspective est une référence de concept ; ne pas la déclarer orthographique.",
        "Une fois choisie, conserver cette référence et sa provenance ; "
        "ne pas la régénérer pour masquer les écarts du modèle.",
    ]
    return {
        "schema": 1,
        "parcours": proposer(data),
        "demande_image": "\n".join(instructions),
        "conception": data,
        "conception_sha256": hashlib.sha256((projet / "CONCEPTION.json").read_bytes()).hexdigest(),
        "besoin_sha256": hashlib.sha256(besoin.read_bytes()).hexdigest() if besoin.exists() else None,
        "execution": (
            "Aucune génération effectuée. L’assistant utilise un outil autorisé et archive son résultat "
            "avant de figer la référence."
        ),
    }


def main(argv=None):
    """Exécute l’action de conception demandée. Renvoie 0 en cas de succès, 1 si non vérifié ou 2 sur refus."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("diagnostic", "brief", "verifier", "retenir"))
    parser.add_argument("--projet", type=Path, default=Path.cwd())
    parser.add_argument("--sortie", type=Path)
    parser.add_argument("--rapport", type=Path)
    parser.add_argument("--auteur")
    parser.add_argument("--motif")
    parser.add_argument("--revue-concept", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "retenir":
            if not args.rapport or not args.sortie:
                raise ValueError("retenir exige --rapport et --sortie (REVUE-VISUELLE.json).")
            resultat = retenir(args.projet, args.rapport, args.auteur, args.motif, args.revue_concept)
        else:
            resultat = (
                diagnostic()
                if args.action == "diagnostic"
                else brief(args.projet)
                if args.action == "brief"
                else verifier(args.projet)
            )
        texte = json.dumps(resultat, ensure_ascii=False, indent=2) + "\n"
        if args.sortie:
            args.sortie.parent.mkdir(parents=True, exist_ok=True)
            with args.sortie.open("x") as f:
                f.write(texte)
        print(texte, end="")
        statut = resultat.get("statut")
        return 0 if statut in (None, "PASS", "REVU", "NON APPLICABLE") else 1 if statut == "NON VÉRIFIÉ" else 2
    except (ValueError, OSError, ImportError, KeyError, TypeError) as e:
        print(json.dumps({"statut": "FAIL", "erreur": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
