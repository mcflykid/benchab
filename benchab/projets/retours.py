"""Déclarations physiques privées et références choisies explicitement.

Les observations humaines ne constituent pas une certification du modèle. Aucune
préparation n'est réécrite, aucun fichier supprimé, aucun envoi déduit d'une date.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from benchab.projets.envois import lire_fiche
from benchab.projets.preparations import enregistrer as inscrire
from benchab.projets.preparations import valider_nom
from benchab.projets.protections import protections
from benchab.qualite.ecritures import _sans_lien, ecrire_json_neuf, sortie_durable
from benchab.qualite.empreintes import sha256_fichier

RESULTATS = {"reussie", "reussie_avec_reserves", "defaut", "echec", "arretee"}


def _projet(base, projet):
    chemin = Path(projet)
    chemin = _sans_lien(chemin if chemin.is_absolute() else base.prive / chemin)
    if chemin.parent != (base.prive / "projets").resolve() or not chemin.is_dir():
        raise ValueError("Projet privé direct sous projets/ requis.")
    return chemin


def _document(projet, relatif):
    relatif = str(relatif)
    if Path(relatif).is_absolute() or ".." in Path(relatif).parts:
        raise ValueError("Chemin relatif de projet requis.")
    chemin = _sans_lien(projet / relatif)
    if not chemin.is_relative_to(projet) or not chemin.is_file():
        raise ValueError("Document de projet absent : " + relatif)
    return {"chemin": relatif, "sha256": sha256_fichier(chemin)}


def _pieces(pieces):
    resultat = []
    for item in pieces:
        if not isinstance(item, dict) or set(item) - {"piece", "version", "plaques"}:
            raise ValueError("Pièce/version/plaques attendues.")
        if any(not isinstance(item.get(k), str) or not item[k].strip() for k in ("piece", "version")):
            raise ValueError("Pièce et version explicites requises.")
        plaques = item.get("plaques", [])
        if not isinstance(plaques, list) or any(not isinstance(p, str) or not p.strip() for p in plaques):
            raise ValueError("Plaques invalides.")
        resultat.append({**item, "plaques": plaques})
    if len({(p["piece"], p["version"]) for p in resultat}) != len(resultat):
        raise ValueError("Pièce/version dupliquée.")
    return resultat


def etat_projet(base, projet):
    projet = _projet(base, projet)
    ident = projet.relative_to(base.prive.resolve()).as_posix()
    ligne = base.connexion.execute("SELECT donnees_json FROM projets WHERE id=?", (ident,)).fetchone()
    donnees = json.loads(ligne[0]) if ligne else {}
    if "suivi" not in donnees:
        qualite = projet / "qualite.json"
        donnees["suivi"] = json.loads(qualite.read_text()).get("suivi", {}) if qualite.exists() else {}
    return donnees["suivi"]


def confirmer(
    base,
    projet,
    resultat,
    preuve,
    fichiers_reference=(),
    preparation=None,
    remplacer=(),
    *,
    pieces=(),
    ensemble_complet=False,
    preuves=(),
    envois=(),
):
    """Enregistre un retour humain et sa portée, sans unité ; renvoie fait et conservation simulée.

    Ne transforme jamais un résultat partiel en verdict global, ne modifie aucun fichier figé ni réglage.
    Sans préparation, conserve une capture privée neuve et son SHA-256 comme source contrôlable.
    """
    projet = _projet(base, projet)
    if resultat not in RESULTATS or not isinstance(preuve, str) or not preuve.strip():
        raise ValueError("Résultat physique et preuve explicites requis.")
    if fichiers_reference and resultat != "reussie":
        raise ValueError("Un échec ne peut devenir une référence réussie.")
    if type(ensemble_complet) is not bool:
        raise ValueError("Portée explicite attendue.")
    elements = _pieces(pieces)
    fichiers = [_document(projet, p) for p in fichiers_reference]
    documents = [_document(projet, p) for p in preuves]
    if fichiers:
        for relatif, empreinte in protections(projet).items():
            if empreinte and _document(projet, relatif)["sha256"] != empreinte:
                raise ValueError("Référence protégée altérée : " + relatif)
    ident = projet.relative_to(base.prive.resolve()).as_posix()
    reference, candidats = None, []
    if preparation:
        valider_nom(preparation)
        reference = inscrire(base, projet / "preparations" / preparation / "PREPARATION.json")
        if reference["projet_id"] != ident:
            raise ValueError("Projet explicite de la préparation différent de sa position privée.")
        fiche = lire_fiche(base, projet, preparation)
        candidats = ([fiche["envoi"]] if fiche.get("envoi") else []) + fiche.get("envois_supplementaires", [])
    identifies = []
    for relatif in envois:
        correspondants = [e for e in candidats if e["fichier"] == str(relatif)]
        if len(correspondants) != 1:
            raise ValueError("Envoi non identifiable : " + str(relatif))
        e = correspondants[0]
        fichier = _sans_lien(base.prive / e["fichier"])
        if not fichier.is_relative_to(base.prive.resolve()) or sha256_fichier(fichier) != e["sha256"]:
            raise ValueError("Envoi absent ou altéré.")
        identifies.append({"chemin": e["fichier"], "sha256": e["sha256"]})
    date = datetime.now(timezone.utc).isoformat()
    evenement = {
        "id": str(uuid.uuid4()),
        "type": "retour_physique",
        "date": date,
        "resultat": resultat,
        "preuve": preuve,
        "preparation": preparation,
        "pieces": elements,
        "portee": "ENSEMBLE" if ensemble_complet else "PARTIELLE" if elements else "NON_IDENTIFIEE",
        "envois_identifies": identifies,
        "envois_candidats_preparation": [{"chemin": e["fichier"], "sha256": e["sha256"]} for e in candidats],
        "preuve_statut": "DECLARATION_HUMAINE",
        "documents": documents,
        "limites": ([] if elements or ensemble_complet else ["Pièces et versions non identifiées"])
        + ([] if identifies else ["Fichier envoyé non identifiable"]),
    }
    with base.transaction() as connexion:
        connexion.execute("BEGIN IMMEDIATE")
        suivi = etat_projet(base, projet)
        anciennes = {r["id"]: r for r in suivi.get("references_imprimees", [])}
        if remplacer and (not fichiers or resultat != "reussie" or any(i not in anciennes for i in remplacer)):
            raise ValueError("Remplacement explicite invalide : références et réussite requises.")
        if not any(e.get("type") == "retour_physique" for e in suivi.get("evenements", [])):
            evenement["etat_physique_precedent"] = suivi.get("physique", {"resultat": "inconnu"})
        suivi.setdefault("evenements", []).append(evenement)
        suivi["physique"] = {
            "imprime": True,
            "resultat": resultat if ensemble_complet else "inconnu",
            "preuve": preuve,
            "portee": evenement["portee"],
            "dernier_retour": evenement["id"],
        }
        if fichiers:
            nouvelle = "retour-" + evenement["id"]
            for ancien in remplacer:
                suivi.setdefault("references_historiques", []).append(
                    {**anciennes[ancien], "remplacee_par": nouvelle, "preuve_remplacement": preuve}
                )
            suivi["references_imprimees"] = [r for r in anciennes.values() if r["id"] not in remplacer]
            suivi["references_imprimees"].append(
                {
                    "id": nouvelle,
                    "preuve": preuve,
                    "preparation": preparation,
                    "fichiers": fichiers,
                    "retour": evenement["id"],
                    "pieces": elements,
                }
            )
        else:
            suivi.setdefault("references_imprimees", [])
        # IMPRIME signifie qu'un ensemble a réellement été imprimé, pas qu'il
        # a réussi ; le défaut reste explicite (docs/guides/retour-impression.md).
        ensemble_imprime = ensemble_complet and not suivi.get("travail_en_cours", {}).get("fichiers")
        if ensemble_imprime:
            suivi["statut"] = "IMPRIME"
        ligne = connexion.execute("SELECT donnees_json,etat FROM projets WHERE id=?", (ident,)).fetchone()
        donnees = json.loads(ligne[0]) if ligne else {}
        donnees["suivi"] = suivi
        serialise = json.dumps(donnees, ensure_ascii=False, allow_nan=False)
        connexion.execute(
            "INSERT INTO projets(id,nom,origine,donnees_json,etat) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET donnees_json=excluded.donnees_json,etat=excluded.etat",
            (
                ident,
                projet.name,
                ident,
                serialise,
                "IMPRIME" if ensemble_imprime else ligne["etat"] if ligne else "inconnu",
            ),
        )
        contenu = json.dumps(evenement, ensure_ascii=False, allow_nan=False)
        if reference:
            verdict = {"defaut": "echec", "arretee": "interrompue", "reussie_avec_reserves": "inconnu"}.get(
                resultat, resultat
            )
            connexion.execute(
                "INSERT INTO impressions(id,preparation_id,date,resultat,donnees_json) VALUES(?,?,?,?,?)",
                (evenement["id"], reference["preparation_id"], date, verdict, contenu),
            )
            connexion.execute(
                "INSERT INTO retours(id,impression_id,date,texte,source,donnees_json) VALUES(?,?,?,?,?,?)",
                (evenement["id"], evenement["id"], date, preuve, "declaration_humaine", contenu),
            )
        else:
            # Le type de preuve n'est pas un chemin : conserver la déclaration exacte
            # permet de vérifier sa source sans inventer d'envoi (docs/guides/retour-impression.md).
            capture = sortie_durable(
                base.prive / "donnees/sources/retours" / (evenement["id"] + ".json"), prive=base.prive
            )
            ecrire_json_neuf(capture, evenement)
            source = capture.relative_to(base.prive.resolve()).as_posix()
            connaissance = {
                **evenement,
                "source_originale": "declaration_humaine",
                "sources": [{"chemin": source, "sha256": sha256_fichier(capture)}],
            }
            connexion.execute(
                "INSERT INTO connaissances(id,texte,categorie,source,date_source,public,donnees_json) "
                "VALUES(?,?,'observation',?,?,0,?)",
                (evenement["id"], preuve, source, date, json.dumps(connaissance, ensure_ascii=False, allow_nan=False)),
            )
    from benchab.projets.conservation import simuler

    try:
        conservation = simuler(base, projet)
    except (OSError, ValueError) as erreur:
        # Le fait humain est déjà enregistré ; une simulation indisponible ne
        # doit pas pousser à le saisir deux fois (docs/guides/retour-impression.md).
        conservation = {
            "statut": "FAIL",
            "simulation": True,
            "suppression_possible": False,
            "erreur": str(erreur),
            "retour_deja_enregistre": True,
        }
    return {"retour": evenement, "conservation": conservation}
