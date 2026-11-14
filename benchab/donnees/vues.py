"""Vues Markdown déterministes issues de SQLite ; aucun projet dans les vues publiques."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from benchab import __version__
from benchab.qualite.ecritures import _sans_lien

DEBUT = "<!-- BENCHAB:BASE:DEBUT -->"
FIN = "<!-- BENCHAB:BASE:FIN -->"


def _texte(valeur):
    texte = str(valeur).replace("\n", " ").replace("\r", " ")
    for caractere in ("\\", "`", "*", "_", "[", "]", "<", ">", "|", "#"):
        texte = texte.replace(caractere, "\\" + caractere)
    return texte


def _ecrire(chemin, texte):
    chemin = _sans_lien(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    fd, nom = tempfile.mkstemp(prefix=".vue-", dir=chemin.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as flux:
            flux.write(texte)
        Path(nom).replace(chemin)
    finally:
        Path(nom).unlink(missing_ok=True)


def _section(chemin, corps, titre):
    chemin = _sans_lien(chemin)
    texte = chemin.read_text(encoding="utf-8") if chemin.exists() else titre + "\n\n"
    if texte.count(DEBUT) != texte.count(FIN) or texte.count(DEBUT) > 1:
        raise ValueError("Marqueurs de vue générée invalides : " + str(chemin))
    bloc = DEBUT + "\n" + corps.rstrip() + "\n" + FIN
    if DEBUT in texte:
        avant, reste = texte.split(DEBUT)
        _, apres = reste.split(FIN)
        texte = avant + bloc + apres
    else:
        texte = texte.rstrip() + "\n\n" + bloc + "\n"
    _ecrire(chemin, texte)


def _rendre(connexion):
    """Rend en mémoire les vues depuis SQLite ; renvoie textes exacts, sans écriture ni modification des données."""
    lignes = list(connexion.execute("SELECT * FROM projets ORDER BY id"))
    index = ["# Projets privés", "", "Vue générée depuis la base ; tous les projets restent privés.", ""]
    fichiers = {}
    for ligne in lignes:
        projet = dict(ligne)
        nom_fichier = hashlib.sha256(projet["id"].encode()).hexdigest()[:24] + ".md"
        chemin_etat = "vues/etats/" + nom_fichier
        if chemin_etat in fichiers:
            raise ValueError("Collision de chemins dans les vues générées.")
        donnees = json.loads(projet["donnees_json"])
        suivi = donnees.get("suivi", {}) if isinstance(donnees, dict) else {}
        physique = suivi.get("physique", {})
        preparations = list(
            connexion.execute(
                "SELECT nom,date,empreinte FROM preparations WHERE projet_id=? ORDER BY nom", (projet["id"],)
            )
        )
        resumes = donnees.get("migration", {}).get("contrat_natif", {}).get("preparations_historiques", {})
        etat = [
            "# " + _texte(projet["nom"]),
            "",
            "État : " + _texte(projet["etat"]),
            "",
            "Résultat physique global déclaré : " + _texte(physique.get("resultat", "inconnu")),
            "",
            "Portée : " + _texte(physique.get("portee", "non précisée")),
            "",
            "## Préparations",
            "",
        ]
        etat += [
            f"- {_texte(p['nom'])} — {_texte(p['date'] or 'date inconnue')} — "
            f"SHA : {_texte(p['empreinte'] or 'inconnu')}"
            + (" — historique résumé ; réimpression non établie" if p["nom"] in resumes else "")
            for p in preparations
        ]
        fichiers[chemin_etat] = "\n".join(etat) + "\n"
        index.append(f"- [{_texte(projet['nom'])}](etats/{nom_fichier}) — {_texte(projet['etat'])}")
    fichiers["vues/INDEX.md"] = "\n".join(index) + "\n"
    versions = list(connexion.execute("SELECT * FROM versions ORDER BY date,numero,id"))
    changelog = ["# Changements", "", "Généré depuis la table versions.", ""]
    for version in versions:
        changelog += [
            "## " + _texte(version["numero"]),
            "",
            _texte(version["date"] or "date inconnue"),
            "",
            _texte(version["changement"]),
            "",
        ]
    fichiers["CHANGELOG.md"] = "\n".join(changelog) + "\n"
    sections = {"README.md": f"Projets : {len(lignes)}. Voir [l'index privé](vues/INDEX.md)."}
    return {"fichiers": fichiers, "sections": sections, "projets": len(lignes), "versions": len(versions)}


def _fiches_filaments(connexion):
    """Rend les fiches modernes privées ; unités citées, aucune archive ni écriture."""
    from benchab.connaissances.bobine import texte_fiche

    fichiers = {}

    for ligne in connexion.execute("SELECT id,nom,donnees_json FROM filaments ORDER BY id"):
        donnees = {**json.loads(ligne["donnees_json"]), "id": ligne["id"], "nom": ligne["nom"]}
        # Les fiches modernes sont des vues ; aucune archive historique n'est
        # régénérée (docs/decisions/2026-11-05-controles-et-representations.md).
        if donnees.get("fiche_contrat") != "recherche-sources-v1":
            continue
        relatif = Path(donnees["fiche"])
        if (relatif.is_absolute() or ".." in relatif.parts or relatif.suffix != ".md"
                or not relatif.as_posix().startswith("docs/reference/filaments/")):
            raise ValueError("Chemin de fiche filament hors contrat : " + str(relatif))
        fichiers[relatif.as_posix()] = texte_fiche(donnees)
    return fichiers


def _manifeste(rendu):
    """Déclare les empreintes des vues et sections générées ; les zones éditoriales libres restent hors empreinte."""
    return {
        "schema": 1,
        "fichiers": {
            chemin: hashlib.sha256(texte.encode("utf-8")).hexdigest()
            for chemin, texte in sorted(rendu["fichiers"].items())
        },
        "sections": {
            chemin: hashlib.sha256(texte.encode("utf-8")).hexdigest()
            for chemin, texte in sorted(rendu["sections"].items())
        },
    }


def generer(base, *, racine_publique=None):
    """Régénère les vues privées et le marqueur README public ; compte sans unité, aucune archive ni réglage adopté.

    La version publique suit le paquet, pas la base privée ; CHANGELOG public manuel préservé.
    Les fiches modernes reprennent SQLite ; les fiches historiques ne sont jamais réécrites.
    """
    from benchab.qualite.ecritures import verrou_publication

    # Le verrou commun empêche une régénération de remplacer la vue en réparation (#119).
    with verrou_publication(base.prive / "cache", "vues.lock"):
        return _generer_sous_verrou(base, racine_publique=racine_publique)


def _generer_sous_verrou(base, *, racine_publique=None):
    """Écrit les vues après acquisition du verrou commun ; conserve SQLite et les fichiers éditoriaux hors vues."""
    rendu = _rendre(base.connexion)
    fiches = _fiches_filaments(base.connexion)
    for chemin, texte in {**rendu["fichiers"], **fiches}.items():
        _ecrire(base.prive / chemin, texte)
    _section(base.prive / "README.md", rendu["sections"]["README.md"], "# Atelier privé")
    # Le manifeste conserve les chemins attendus afin de détecter un index manquant
    # (docs/reference/donnees.md).
    _ecrire(base.prive / "vues/manifest.json", json.dumps(_manifeste(rendu), ensure_ascii=False, sort_keys=True) + "\n")
    if racine_publique is not None:
        racine = _sans_lien(Path(racine_publique))
        if racine == base.prive.resolve() or racine.is_relative_to(base.prive.resolve()):
            raise ValueError("La racine publique doit être distincte de l'espace privé.")
        # SQLite reste privée ; le README suit le paquet, pas les numéros de versions locaux.
        # Le CHANGELOG public reste éditorial (docs/reference/donnees.md).
        corps = (
            f"Version du paquet : {__version__}. Les versions publiées sont référencées "
            "dans le CHANGELOG et les tags Git (vX.Y.Z)."
        )
        _section(racine / "README.md", corps, "# BENCHAB")
    return {"projets_prives": rendu["projets"], "versions": rendu["versions"], "projets_publics": 0}
