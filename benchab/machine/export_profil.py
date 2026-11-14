"""Prépare une paire de fichiers de procédé utilisateur pour import manuel hors ligne."""

import argparse
import json
import re
import time
from pathlib import Path

from benchab import reglages
from benchab.chemins import sans_lien
from benchab.machine.profil_maitre import resoudre_preset
from benchab.qualite.ecritures import PublicationNeuve, sortie_durable
from benchab.qualite.empreintes import sha256_fichier


def exporter(parent, source, nom, dossier, *, prive=None):
    """Publie un JSON et un INFO privés neufs à partir d’un procédé parent et de surcharges typées.

    Les valeurs gardent les unités du slicer. Le résultat contient les fichiers,
    le nombre de différences et le statut NON VÉRIFIÉ de l’import. Les profils
    installés sont en lecture seule et aucune connexion n’est réalisée.
    """
    if not re.fullmatch(r"[\w][\w .()-]{0,89}", nom) or nom != nom.strip() or nom.endswith("."):
        raise ValueError("Nom de profil simple attendu, sans séparateur et sans point final.")
    parent, source = (sans_lien(Path(p), strict=True) for p in (parent, source))
    dossier = sortie_durable(dossier, prive=prive)
    if not dossier.is_dir():
        raise ValueError("Le dossier privé de destination doit exister.")
    profil_parent = reglages.decoder(parent.read_bytes())
    nom_parent = profil_parent.get("name")
    version = profil_parent.get("version")
    if (
        nom_parent != parent.stem or profil_parent.get("type") != "process"
        or profil_parent.get("from") != "system" or not isinstance(version, str) or not version
    ):
        raise ValueError("Parent système de type process, nommé et versionné, requis.")
    if nom.casefold() == nom_parent.casefold():
        raise ValueError("Un profil utilisateur ne peut pas porter le nom du parent.")

    resolution = resoudre_preset(nom_parent, categorie="process", sources=[parent.parent])
    if resolution["manquants"]:
        raise ValueError("Parents introuvables : " + ", ".join(resolution["manquants"]))
    traces = {Path(p["source"]): p["sha256"] for p in resolution["heritages"]}
    if traces[parent] != sha256_fichier(parent) or profil_parent != reglages.decoder(parent.read_bytes()):
        raise ValueError("Le parent a changé pendant sa lecture.")

    contenu = source.read_bytes()
    surcharges = reglages.decoder(contenu)
    for ligne in reglages.verifier(surcharges, contexte=resolution["valeurs"]):
        cle = ligne["cle"]
        definition = reglages.definition(cle, valeur=surcharges[cle])
        if ligne["statut"] != "PASS" or "process" not in definition.get("familles", ()):
            raise ValueError("Surcharge de procédé inconnue, ambiguë ou mal typée : " + cle)
    reservees = {"name", "inherits", "from", "type", "version", "setting_id", "print_settings_id"}
    if reservees.intersection(surcharges):
        raise ValueError("Les métadonnées du profil sont réservées à l’export.")
    valeurs = {
        cle: valeur for cle, valeur in surcharges.items()
        if cle not in resolution["valeurs"] or valeur != reglages.valeur_brute(resolution["valeurs"], cle)
    }
    if not valeurs:
        raise ValueError("Aucune différence typée avec le parent.")

    profil = dossier / (nom + ".json")
    info = dossier / (nom + ".info")
    # Preset.cpp sérialise l’héritage par nom et stocke à part les identifiants de synchronisation.
    donnees = {"name": nom, "inherits": nom_parent, "from": "User", "type": "process", "version": version, **valeurs}
    fiche = f"sync_info = \nuser_id = \nsetting_id = \nbase_id = \nupdated_time = {int(time.time())}\n"
    with PublicationNeuve([source, *traces], [profil, info]) as publication:
        publication.temporaire(profil).write_text(reglages.encoder(donnees, ensure_ascii=False, indent=2) + "\n")
        publication.temporaire(info).write_text(fiche, encoding="utf-8")
        if source.read_bytes() != contenu or any(sha256_fichier(p) != sha for p, sha in traces.items()):
            raise ValueError("Une entrée de l’export a changé avant publication.")
        publication.publier()
    return {
        "statut": "PASS", "import_slicer": "NON VÉRIFIÉ", "parent": nom_parent,
        "nombre_differences": len(valeurs), "profil": str(profil), "info": str(info),
    }


def main(argv=None, *, prive=None):
    """Exporte une paire privée neuve ; chemins sans unité, code 0 ou 2, sans ouvrir le slicer."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--reglages", type=Path, required=True)
    parser.add_argument("--nom", required=True)
    parser.add_argument("--dossier", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        resultat = exporter(args.parent, args.reglages, args.nom, args.dossier, prive=prive)
    except (OSError, ValueError, RuntimeError) as erreur:
        parser.error(str(erreur))
    print(json.dumps(resultat, ensure_ascii=False))
    return 0
