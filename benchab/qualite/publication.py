"""Provenance des géométries publiques : aucun contrôle de ce type n'existait encore."""

import hashlib
import json
import os
import re
from pathlib import Path

from benchab.projets.protections import protections

EXTENSIONS_3D = {
    ".stl",
    ".3mf",
    ".step",
    ".stp",
    ".glb",
    ".gltf",
    ".gcode",
    ".gco",
    ".gc",
    ".obj",
    ".ply",
    ".off",
    ".amf",
    ".blend",
    ".fbx",
    ".dae",
    ".3ds",
    ".3dm",
    ".brep",
    ".iges",
    ".igs",
    ".wrl",
    ".vrml",
    ".usd",
    ".usdc",
    ".usdz",
}
REPERTOIRES_FIXTURES = ("tests/fixtures/", "benchab/reference/temoins/", "benchab/reference/fixtures/")
FAMILLES = {"temoin-S", "temoin-M", "temoin-L", "fixture-test"}


def empreinte(fichier):
    with fichier.open("rb") as flux:
        return hashlib.file_digest(flux, "sha256").hexdigest()


def controler_modeles(racine, exclus, *, racines_privees=()):
    """Contrôle les modèles publics et les fixtures déclarées ; erreurs FAIL, aucune donnée privée lue."""
    racine = Path(racine).resolve()
    manifeste = racine / "benchab/reference/fixtures-atelier.json"
    erreurs = []
    try:
        autorises = json.loads(manifeste.read_text())["fichiers"] if manifeste.exists() else {}
    except (OSError, ValueError, KeyError):
        return ["Manifeste des fixtures générées absent de structure valide"]
    for dossier, sous_dossiers, fichiers in os.walk(racine, followlinks=False):
        sous_dossiers[:] = [
            n for n in sous_dossiers
            if n not in exclus and not n.endswith(".egg-info") and Path(dossier) / n not in racines_privees
        ]
        for nom in fichiers:
            fichier = Path(dossier) / nom
            if fichier.suffix.lower() not in EXTENSIONS_3D and not nom.lower().endswith((".gcode.gz", ".gco.gz")):
                continue
            relatif = fichier.relative_to(racine).as_posix()
            entree = autorises.get(relatif, {})
            if not relatif.startswith(REPERTOIRES_FIXTURES) or entree.get("famille") not in FAMILLES:
                erreurs.append(f"{relatif} : fichier 3D public interdit, tous les projets restent privés")
                continue
            if entree.get("origine") != "atelier" or entree.get("projet_id"):
                erreurs.append(f"{relatif} : provenance d'atelier indépendante des projets non établie")
                continue
            generateur_relatif = Path(entree.get("generateur", ""))
            if generateur_relatif.is_absolute() or ".." in generateur_relatif.parts:
                erreurs.append(f"{relatif} : chemin du générateur non publiable")
                continue
            generateur = racine / generateur_relatif
            if not generateur.is_file() or generateur.is_symlink() or fichier.is_symlink():
                erreurs.append(f"{relatif} : générateur ou fixture invalide")
                continue
            if not generateur.resolve().is_relative_to(racine) or "prive" in generateur_relatif.parts:
                erreurs.append(f"{relatif} : générateur hors de la partie publique")
                continue
            if empreinte(fichier) != entree.get("sha256") or empreinte(generateur) != entree.get("generateur_sha256"):
                erreurs.append(f"{relatif} : empreinte du modèle ou du générateur divergente")
    return erreurs


ETATS = {
    "ATTENTE_HUMAINE",
    "ABANDONNE",
    "EN_CONCEPTION",
    "IMPRIME",
    "PRET_NUMERIQUEMENT",
    "SUSPENDU",
    "EN_PREPARATION",
    "HISTORIQUE",
    "TERMINE",
}
RESULTATS = {"inconnu", "defaut", "echec", "reussie_avec_reserves", "reussie", "arretee"}


def relatif(base, value):
    """Chemin contractuel interne, sans lien ni traversée, même via un parent."""
    if not isinstance(value, (str, Path)):
        raise ValueError("chemin de projet invalide")
    p = Path(value)
    if p.is_absolute() or ".." in p.parts or not p.parts or str(p) == ".":
        raise ValueError("chemin de projet invalide")
    candidate = Path(base) / p
    if any(x.is_symlink() for x in (candidate, *candidate.parents) if x == Path(base) or x.is_relative_to(Path(base))):
        raise ValueError("chemin symbolique refusé")
    return candidate


def charger(projet):
    projet = Path(projet)
    if (projet.parent / ".transaction-vues.json").exists():
        raise ValueError("catalogue interrompu : reprise transactionnelle requise")
    p = projet / "qualite.json"
    if not p.is_file() and not (projet.parent.parent / "benchab.sqlite").is_file():
        raise ValueError("qualite.json absent : projet incomplet")
    d = donnees_projet(projet) if (projet.parent.parent / "benchab.sqlite").is_file() else json.loads(p.read_text())
    if not isinstance(d, dict):
        raise ValueError("qualite.json doit être un objet")
    s = d.get("suivi")
    if not isinstance(s, dict) or s.get("schema") != 1:
        raise ValueError("qualite.json/suivi absent ou invalide")
    if (
        not isinstance(s.get("statut"), str)
        or s["statut"] not in ETATS
        or s.get("type") not in ("conception", "historique")
    ):
        raise ValueError("état ou type de projet invalide")
    for key in ("titre", "nom"):
        if key in s and not isinstance(s[key], str):
            raise ValueError("qualite.json/suivi/" + key + " doit être un texte")
    for key in ("physique", "travail_en_cours", "publication"):
        if key in s and not isinstance(s[key], dict):
            raise ValueError("qualite.json/suivi/" + key + " doit être un objet")
    if "reproductibilite" in s and not isinstance(s["reproductibilite"], (dict, str)):
        raise ValueError("qualite.json/suivi/reproductibilite doit être un objet ou un texte historique")
    for key in ("references_imprimees", "references_historiques", "evenements", "envois_declares"):
        values = s.get(key, [])
        if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
            raise ValueError("qualite.json/suivi/" + key + " doit être une liste d’objets")
    for key in ("preuves_utiles", "sources_necessaires"):
        values = s.get(key, [])
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError("qualite.json/suivi/" + key + " doit être une liste de chemins")
    if s.get("id") is not None:
        from uuid import UUID

        if not isinstance(s["id"], str) or str(UUID(s["id"])) != s["id"]:
            raise ValueError("identifiant de projet invalide")
    physique = s.get("physique", {})
    if (
        (physique.get("imprime") is not None and type(physique.get("imprime")) is not bool)
        or not isinstance(physique.get("resultat"), str)
        or physique["resultat"] not in RESULTATS
    ):
        raise ValueError("état physique invalide")
    if physique.get("imprime") is not True and physique.get("resultat") != "inconnu":
        raise ValueError("un contrôle numérique ne prouve pas une impression")
    if physique.get("imprime") is True and not physique.get("preuve"):
        raise ValueError("impression déclarée sans preuve utilisateur")
    if not isinstance(s.get("references_imprimees", []), list):
        raise ValueError("références imprimées invalides")
    for reference in s.get("references_imprimees", []) + s.get("references_historiques", []):
        if not isinstance(reference, dict) or not reference.get("preuve"):
            raise ValueError("référence imprimée sans preuve")
        files = reference.get("fichiers", [])
        if not isinstance(files, list) or any(not isinstance(item, dict) or "chemin" not in item for item in files):
            raise ValueError("fichiers de référence invalides")
        for item in files:
            relatif(projet, item["chemin"])
    files = s.get("travail_en_cours", {}).get("fichiers", [])
    if not isinstance(files, list):
        raise ValueError("fichiers de travail invalides")
    for item in files:
        relatif(projet, item)
    return d, s


def donnees_projet(projet):
    """Vue du registre canonique, ouverte strictement en lecture seule."""
    import sqlite3

    from benchab.qualite.ecritures import _sans_lien

    projet = _sans_lien(Path(projet).absolute())
    fichier = _sans_lien(projet.parent.parent / "benchab.sqlite")
    connexion = sqlite3.connect(fichier.as_uri() + "?mode=ro", uri=True)
    try:
        ligne = connexion.execute("SELECT donnees_json FROM projets WHERE nom=?", (projet.name,)).fetchone()
        if ligne is None:
            raise ValueError("Projet absent du registre canonique")
        canonique = json.loads(ligne[0])
        historique = canonique.get("originebrute", {}).get("qualite", {})
        if not isinstance(historique, dict):
            raise ValueError("Fiche historique invalide")
        return {**historique, **canonique}
    finally:
        connexion.close()


def chemin_projet(root, projet, valeur):
    """Résout aussi un ancien nom de dossier, uniquement avec une copie importée prouvée."""
    root, projet = Path(root).resolve(), Path(projet).resolve()
    valeur = Path(valeur)
    base = root if valeur.parts[:1] == ("projets",) else projet
    cible = relatif(base, valeur)
    if cible.is_relative_to(projet):
        return cible
    if (root / "benchab.sqlite").is_file() and len(valeur.parts) > 2 and valeur.parts[0] == "projets":
        copie = relatif(projet, Path(*valeur.parts[2:]))
        donnees = donnees_projet(projet)
        for preuve in donnees.get("fichiers_copies", []):
            if preuve.get("destination") == copie.relative_to(root).as_posix():
                if copie.is_file() and empreinte(copie) == preuve.get("sha256"):
                    return copie
        # Les préparations figées ont leur registre propre ; leur JSON reste intact.
        import sqlite3

        attendus = set()

        def relever(objet):
            if isinstance(objet, dict):
                if objet.get("fichier") == valeur.as_posix() and isinstance(objet.get("sha256"), str):
                    attendus.add(objet["sha256"])
                entree = objet.get(valeur.as_posix())
                if isinstance(entree, str):
                    attendus.add(entree)
                elif isinstance(entree, dict) and isinstance(entree.get("sha256"), str):
                    attendus.add(entree["sha256"])
                for v in objet.values():
                    relever(v)
            elif isinstance(objet, list):
                for v in objet:
                    relever(v)

        db = sqlite3.connect((root / "benchab.sqlite").as_uri() + "?mode=ro", uri=True)
        try:
            for ligne in db.execute(
                "SELECT preparations.donnees_json FROM preparations JOIN projets "
                "ON projets.id=preparations.projet_id WHERE projets.nom=?",
                (projet.name,),
            ):
                relever(json.loads(ligne[0]))
        finally:
            db.close()
        if attendus and copie.is_file() and empreinte(copie) in attendus:
            return copie
        raise ValueError("Ancien chemin de projet sans copie importée conforme au SHA")
    raise ValueError("Source hors du projet sélectionné")


def verifier(projet):
    """Contrôle les preuves et fichiers déclarés ; rend PASS/WARN/FAIL sans valider le physique.

    Entrée : répertoire privé du projet ; sortie : rapport JSON-compatible, sans unité physique.
    Les SHA connus sont comparés et aucune donnée, préparation ou vue n'est réparée.
    """
    projet = Path(projet).absolute()
    erreurs, avertissements, fichiers_controles = [], [], []
    try:
        from benchab.qualite.ecritures import _sans_lien

        projet = _sans_lien(projet)
        donnees, suivi = charger(projet)
        racine = projet.parent.parent.resolve()

        def verifier_fichier(valeur, attendu=None, *, historique=False, libelle="fichier", sha_requis=False):
            detail = {
                "chemin": valeur,
                "sha256_attendu": attendu,
                "role": libelle,
                "historique": historique,
                "statut": "NON VÉRIFIÉ",
            }
            fichiers_controles.append(detail)
            localisation = str(valeur) if isinstance(valeur, (str, Path)) else "<chemin invalide>"
            try:
                fichier = chemin_projet(racine, projet, valeur)
                detail["resolution"] = fichier.relative_to(racine).as_posix()
                if not fichier.is_file():
                    message = f"{libelle} absent : {localisation}"
                    detail["statut"] = "WARN" if historique else "FAIL"
                    (avertissements if historique else erreurs).append(message)
                    return
                if attendu:
                    if empreinte(fichier) != attendu:
                        message = f"{libelle} différent du SHA déclaré : {localisation}"
                        detail["statut"] = "WARN" if historique else "FAIL"
                        (avertissements if historique else erreurs).append(message)
                    else:
                        detail["statut"] = "PASS"
                else:
                    detail["statut"] = "FAIL" if sha_requis else "NON VÉRIFIÉ"
                    (erreurs if sha_requis else avertissements).append(
                        f"{libelle} sans SHA déclaré : {localisation} ; vérification partielle"
                    )
            except (OSError, ValueError, TypeError) as exc:
                detail["statut"] = "WARN" if historique else "FAIL"
                if historique:
                    avertissements.append(
                        f"{libelle} indisponible ou non prouvée : {localisation} ; {type(exc).__name__}"
                    )
                else:
                    erreurs.append(f"{libelle} invalide : {localisation} ; {type(exc).__name__}")

        from benchab.projets.contrat_reprise import controler as controler_contrat

        resumes = controler_contrat(donnees, projet, verifier_fichier)
        if resumes:
            avertissements.append("Préparations historiques résumées : réimpression NON VÉRIFIÉE")

        # Les protections sont l'inventaire natif des fichiers gelés (docs/reference/donnees.md).
        for relatif_protege, sha_attendu in protections(projet).items():
            verifier_fichier(relatif_protege, sha_attendu, libelle="fichier protégé")

        for cle, historique in (("references_imprimees", False), ("references_historiques", True)):
            for reference in suivi.get(cle, []):
                for item in reference.get("fichiers", []):
                    verifier_fichier(
                        item.get("chemin"),
                        item.get("sha256"),
                        sha_requis=True,
                        libelle="référence historique" if historique else "référence active",
                    )

        for cle, libelle in (("travail_en_cours", "travail courant"),):
            for valeur in suivi.get(cle, {}).get("fichiers", []):
                verifier_fichier(valeur, libelle=libelle)
        for valeur in suivi.get("sources_necessaires", []):
            verifier_fichier(valeur, libelle="source nécessaire")

        courant = suivi.get("preparation_courante")
        if courant is not None:
            if not isinstance(courant, str) or re.fullmatch(r"P[0-9]{2,}", courant) is None:
                erreurs.append("identifiant de préparation courante invalide")
            else:
                fiche_courante = projet / "preparations" / courant / "PREPARATION.json"
                if not fiche_courante.is_file() or fiche_courante.is_symlink():
                    erreurs.append("préparation courante absente")

        preparations = projet / "preparations"
        if preparations.exists():
            if preparations.is_symlink():
                erreurs.append("répertoire des préparations lié")
            else:
                for dossier in preparations.glob("P[0-9]*"):
                    if re.fullmatch(r"P[0-9]{2,}", dossier.name) is None:
                        continue
                    if dossier.is_symlink():
                        erreurs.append(f"préparation liée : {dossier.name}")
                        continue
                    if not dossier.is_dir():
                        continue
                    fiche = dossier / "PREPARATION.json"
                    try:
                        if not fiche.is_file() or fiche.is_symlink():
                            erreurs.append(f"préparation incomplète : {dossier.name}")
                            continue
                        preparation = json.loads(fiche.read_text(encoding="utf-8"))
                        if not isinstance(preparation, dict) or preparation.get("id") != dossier.name:
                            erreurs.append(f"identifiant de préparation incohérent : {dossier.name}")
                            continue
                        for valeur, sha_attendu in preparation.get("sources", {}).items():
                            verifier_fichier(
                                valeur, sha_attendu, historique=True, libelle=f"source historique {dossier.name}"
                            )
                        for cle in ("plaques_3mf", "tranche_3mf"):
                            spec = preparation.get(cle)
                            if spec:
                                if not isinstance(spec, dict) or not spec.get("fichier"):
                                    erreurs.append(f"preuve {cle} invalide : {dossier.name}")
                                else:
                                    resume = resumes.get(dossier.name)
                                    decisions = (
                                        []
                                        if resume is None
                                        else [
                                            a
                                            for a in resume.get("artefacts", [])
                                            if a.get("source_originale") == spec["fichier"]
                                            and a.get("sha256") == spec.get("sha256")
                                        ]
                                    )
                                    if resume is not None and not decisions:
                                        erreurs.append(f"Résumé sans décision exacte pour {cle} {dossier.name}")
                                    elif decisions and all(d.get("decision") != "CONSERVÉ_ACTIF" for d in decisions):
                                        avertissements.append(
                                            f"{cle} {dossier.name} : historique résumé, réimpression non établie"
                                        )
                                    else:
                                        verifier_fichier(
                                            spec["fichier"],
                                            spec.get("sha256"),
                                            libelle=f"preuve {cle} {dossier.name}",
                                            sha_requis=True,
                                        )
                    except (OSError, ValueError, TypeError, AttributeError):
                        erreurs.append(f"fiche de préparation illisible : {dossier.name}")

        envoi = suivi.get("envoi_reference")
        if envoi:
            verifier_fichier(envoi.get("chemin"), envoi.get("sha256"), libelle="envoi de référence", sha_requis=True)

        for evenement in suivi.get("evenements", []):
            if evenement.get("type") not in ("retour_physique", "IMPRESSION_PHYSIQUE"):
                continue
            for item in evenement.get("documents", []) + evenement.get("envois_identifies", []):
                if not isinstance(item, dict):
                    erreurs.append("document de retour invalide")
                    continue
                verifier_fichier(item.get("chemin"), item.get("sha256"), libelle="document de retour", sha_requis=True)

        if not protections(projet):
            avertissements.append("aucune protection déclarée ; vérification de conservation limitée")
        reproductibilite = suivi.get("reproductibilite", {})
        if isinstance(reproductibilite, dict):
            avertissements.extend(str(limite) for limite in reproductibilite.get("limites", []))
        elif isinstance(reproductibilite, str) and reproductibilite:
            avertissements.append("reproductibilité au format historique : détails non vérifiés")
    except Exception as exc:
        erreurs.append(f"vérification impossible : {type(exc).__name__} : {exc}")

    statut = "FAIL" if erreurs else "WARN" if avertissements else "PASS"
    return {
        "projet": projet.name,
        "statut": statut,
        "erreurs": erreurs,
        "avertissements": avertissements,
        "fichiers_controles": fichiers_controles,
        "validation_physique": False,
    }
