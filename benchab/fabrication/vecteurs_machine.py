"""Prépare un complément explicite des vecteurs machine d’un projet 3MF."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from benchab import integrite_3mf, reglages
from benchab.archives_3mf import Archive3MF
from benchab.fabrication.tracer_3mf import _est_signee
from benchab.qualite.ecritures import PublicationNeuve, destination_neuve

CONFIG = "Metadata/project_settings.config"


def canoniser(source, sortie=None, *, prive=None):
    """Renvoie le plan des compléments dans les unités natives des réglages.

    PASS signifie que les replis et le profil fabricant concordent. En mode
    écriture, seule la configuration projet change et son différentiel est
    vérifié avant publication. Une archive ambiguë reste NON VÉRIFIÉE.
    """
    source = Path(source)
    with Archive3MF(source) as entree:
        configuration = entree.reglages()
        plan = reglages.plan_vecteurs_machine(configuration)
        rapport = {
            "statut": "NON VÉRIFIÉ" if any(ligne["apres"] is None for ligne in plan) else "PASS",
            "source_sha256": entree.sha256(), "vecteurs": plan, "sortie": None,
        }
        if sortie is None or rapport["statut"] != "PASS" or not plan:
            return rapport
        sortie = destination_neuve(sortie, prive=prive)
        if sortie.suffix.casefold() != ".3mf":
            raise ValueError("Sortie .3mf requise.")
        noms = entree.noms()
        relations = [n for n in noms if n.casefold().endswith(".rels")]
        if _est_signee(noms) or any(
            "digital-signature" in relation.get("Type", "").casefold()
            for nom in relations for relation in entree.xml(nom)
        ):
            raise ValueError("Copie de projet signé refusée.")
        if any(n.casefold().endswith((".gcode", ".gcode.gz", ".bgcode")) for n in noms):
            raise ValueError("Projet avant tranchage requis.")
        if any(reglages.definition(ligne["cle"]).get("lecture_seule") for ligne in plan):
            raise ValueError("Vecteur machine en lecture seule.")
        # Le plan qualifie chaque différence avant la sérialisation typée.
        valeurs = dict(configuration)
        permis = []
        for ligne in plan:
            cle = ligne["cle"]
            valeurs[cle] = ligne["apres"]
            permis.append(integrite_3mf.autorisation("projet/" + cle, ligne["avant"], ligne["apres"]))
        contenu = reglages.encoder(reglages.Reglages(valeurs), ensure_ascii=False, allow_nan=False).encode("utf-8")
        empreintes = {CONFIG: hashlib.sha256(contenu).digest()}
        with PublicationNeuve([source], [sortie]) as publication:
            temporaire = publication.temporaire(sortie)
            with ZipFile(temporaire, "w") as copie:
                copie.comment = entree.commentaire
                for nom in noms:
                    with copie.open(entree.information(nom), "w") as destination:
                        if nom == CONFIG:
                            destination.write(contenu)
                        else:
                            with entree.ouvrir_flux(nom) as original:
                                digest = hashlib.sha256()
                                for bloc in iter(lambda: original.read(1024 * 1024), b""):
                                    digest.update(bloc)
                                    destination.write(bloc)
                                empreintes[nom] = digest.digest()
            with Archive3MF(temporaire) as relecture:
                if relecture.noms() != noms or relecture.commentaire != entree.commentaire:
                    raise ValueError("Index ou commentaire ZIP modifié.")
                for nom in noms:
                    with relecture.ouvrir_flux(nom) as flux:
                        obtenu = hashlib.file_digest(flux, "sha256").digest()
                    if empreintes[nom] != obtenu:
                        raise ValueError("Membre ZIP modifié hors plan : " + nom)
            integrite_3mf.verifier(source, temporaire, permis=permis)
            entree.verifier_source(source)
            publication.publier()
    rapport["sortie"] = str(sortie)
    return rapport


def main(argv=None, *, base):
    """Affiche le plan des vecteurs ou publie une copie 3MF neuve si le plan passe."""
    parser = argparse.ArgumentParser(description="Qualifie le complément des vecteurs machine d’un projet 3MF.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--sortie", type=Path, help="Copie 3MF privée neuve. Sans cette option, lecture seule.")
    args = parser.parse_args(argv)
    sortie = base.prive / args.sortie if args.sortie is not None else None
    rapport = canoniser(args.source, sortie, prive=base.prive)
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return 0 if rapport["statut"] == "PASS" else 2
