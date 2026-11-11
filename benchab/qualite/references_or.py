"""Références privées fixes : empreintes, réglages et commandes réellement conservées."""

import hashlib
import json
from pathlib import Path

from benchab.archives_3mf import Archive3MF
from benchab.controles.commandes_machine import controler as commandes_machine
from benchab.gcode.lecture import empreinte_3mf, plaques_gcode_archive
from benchab.integrite_3mf import instantane
from benchab.qualite.ecritures import _sans_lien

MAX_LIGNE_GCODE_OCTETS = 1024 * 1024


def _commandes_archive(archive):
    """Empreinte les commandes G-code par flux et ligne bornés après contrôle des index ambigus."""
    plaques = plaques_gcode_archive(archive)
    noms_plaques = {nom for _, nom in plaques}
    commandes = {}
    # Conserver l'ordre ZIP historique tout en validant les index via la table commune des plaques.
    for nom in archive.noms():
        if nom not in noms_plaques:
            continue
        digest = hashlib.sha256()
        nombre = 0
        with archive.ouvrir_flux(nom) as flux:
            while ligne := flux.readline(MAX_LIGNE_GCODE_OCTETS + 1):
                if len(ligne) > MAX_LIGNE_GCODE_OCTETS:
                    raise ValueError(f"ligne G-code dépasse le budget de {MAX_LIGNE_GCODE_OCTETS} octets")
                code = ligne.split(b";", 1)[0].strip()
                if code:
                    digest.update(code + b"\n")
                    nombre += 1
        commandes[nom] = {"sha256": digest.hexdigest(), "nombre": nombre}
    return commandes


def relever(chemin):
    """Relit un 3MF ;
    renvoie SHA, réglages en unités natives et commandes sans commentaires, avec lignes limitées à 1 Mio.
    Les lectures secondaires restent liées au même fichier ; aucune écriture ni slicer.
    """
    chemin = _sans_lien(Path(chemin).absolute())
    with Archive3MF(chemin) as archive:
        commandes = _commandes_archive(archive)
        # Les consommateurs gardent leurs propres règles et cache ; le contexte d'origine épingle l'identité commune.
        archive.verifier_source(chemin)
        digest = empreinte_3mf(chemin)
        archive.verifier_source(chemin)
        reglages = {cle: valeur for cle, valeur in instantane(chemin).items() if not cle.startswith("structure/")}
        archive.verifier_source(chemin)
        digest_apres_reglages = empreinte_3mf(chemin)
        archive.verifier_source(chemin)
        if digest_apres_reglages != digest:
            raise ValueError("Archive modifiée pendant la lecture des réglages de référence")
        analyse = None
        if commandes:
            archive.verifier_source(chemin)
            analyse = commandes_machine(chemin)
            archive.verifier_source(chemin)
            digest_apres_analyse = empreinte_3mf(chemin)
            archive.verifier_source(chemin)
            if digest_apres_analyse != digest:
                raise ValueError("Archive modifiée pendant l'analyse des commandes de référence")
    return {
        "sha256": digest,
        "reglages": reglages,
        "commandes": commandes,
        "analyse_demarrage": analyse,
    }


def verifier(prive, *, reference=None):
    """Compare le manifeste fixe aux fichiers privés ;
    PASS/FAIL/NON VÉRIFIÉ, aucune création ni réécriture de référence.
    """
    prive = _sans_lien(Path(prive).absolute()).resolve()
    manifeste = prive / "references-or/manifest.json"
    if not manifeste.is_file():
        return {"statut": "NON VÉRIFIÉ", "execute": False, "raison": "Références privées absentes"}
    donnees = json.loads(_sans_lien(manifeste).read_text())
    if reference is not None and reference not in {e["id"] for e in donnees["references"]}:
        raise ValueError("Référence fixe absente du manifeste")
    resultats = []
    for entree in donnees["references"]:
        if reference is not None and entree["id"] != reference:
            continue
        fichier = _sans_lien(prive / entree["fichier"]).resolve()
        if not fichier.is_relative_to(prive):
            raise ValueError("Référence hors du dossier privé")
        actuel = relever(fichier)
        ecarts = [cle for cle in ("sha256", "reglages", "commandes", "analyse_demarrage") if actuel[cle] != entree[cle]]
        qualification = entree.get("qualification_machine")
        supplement = None
        if qualification is not None:
            if not isinstance(qualification, dict) or not isinstance(qualification.get("fichier"), str):
                raise ValueError("Qualification machine de référence mal formée")
            chemin = _sans_lien(prive / qualification["fichier"]).resolve()
            if not chemin.is_relative_to(prive):
                raise ValueError("Qualification machine hors du dossier privé")
            supplement = relever(chemin)
            ecarts.extend(
                "qualification_machine." + cle
                for cle in ("sha256", "reglages", "commandes", "analyse_demarrage")
                if supplement[cle] != qualification.get(cle)
            )
        # Un original purement géométrique reste tel quel. Le calcul daté ajouté porte son propre profil explicite
        # et ne devient jamais une impression historique réussie (docs/reference/reglages-et-integrite.md).
        machines = ([actuel] if actuel["commandes"] else []) + ([supplement] if supplement is not None else [])
        statuts_machine = [
            m["analyse_demarrage"]["statut"] if m["analyse_demarrage"] else "NON VÉRIFIÉ"
            for m in machines
        ]
        statut_machine = (
            "FAIL" if "FAIL" in statuts_machine else "PASS"
            if statuts_machine and all(s == "PASS" for s in statuts_machine) else "NON VÉRIFIÉ"
        )
        resultats.append(
            {
                "id": entree["id"],
                "statut": "FAIL" if ecarts else "PASS",
                "ecarts": ecarts,
                "gcode_present": any(bool(m["commandes"]) for m in machines),
                "gcode_original_present": bool(actuel["commandes"]),
                "qualification_machine": qualification.get("fichier") if qualification else None,
                "statut_commandes": statut_machine,
            }
        )
    # Sans G-code, une référence de géométrie ne prouve aucune commande machine
    # (docs/reference/reglages-et-integrite.md).
    manquantes = [r["id"] for r in resultats if not r["gcode_present"]]
    statut = (
        "FAIL"
        if any(r["statut"] == "FAIL" or r["statut_commandes"] == "FAIL" for r in resultats)
        else "NON VÉRIFIÉ"
        if manquantes or not resultats or any(r["statut_commandes"] != "PASS" for r in resultats)
        else "PASS"
    )
    return {
        "statut": statut,
        "execute": True,
        "references": resultats,
        "references_sans_gcode": manquantes,
        "validation_physique": False,
    }
