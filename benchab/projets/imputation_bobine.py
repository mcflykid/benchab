"""Applique une pesée confirmée au stock de l’atelier et en conserve la trace."""

import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from benchab.chemins import sans_lien
from benchab.donnees.base import Base
from benchab.projets.plan_consommation import (
    _SHA256,
    _estimer_retour,
    _json_objet,
    _lier_pesee,
    _lire_fiche_bornee,
    _lire_retour,
)

_CHAMPS_CONFIRMATION = {"schema", "pesee_sha256", "confirmee", "date", "auteur", "observation"}


def _confirmation(prive, chemin, empreinte, sha_pesee):
    """Relit une confirmation humaine privée, datée et liée aux octets de la pesée."""
    if not isinstance(empreinte, str) or _SHA256.fullmatch(empreinte) is None:
        raise ValueError("SHA-256 de la confirmation requis.")
    chemin = sans_lien(Path(chemin).expanduser().absolute(), racine=prive, strict=True)
    brut = _lire_fiche_bornee(chemin, limite=64 * 1024)
    if hashlib.sha256(brut).hexdigest() != empreinte:
        raise ValueError("Empreinte de la confirmation différente.")
    contenu = _json_objet(brut.decode("utf-8"))
    if (
        set(contenu) != _CHAMPS_CONFIRMATION
        or type(contenu["schema"]) is not int or contenu["schema"] != 1
        or contenu["pesee_sha256"] != sha_pesee or contenu["confirmee"] is not True
    ):
        raise ValueError("Une confirmation explicite liée à la pesée est requise.")
    for cle in ("auteur", "observation"):
        valeur = contenu[cle]
        if not isinstance(valeur, str) or not valeur.strip() or len(valeur) > 2048 or not valeur.isprintable():
            raise ValueError("Auteur et observation explicites requis pour la confirmation.")
    try:
        date = datetime.fromisoformat(contenu["date"])
    except (ValueError, TypeError) as erreur:
        raise ValueError("Date ISO de confirmation invalide.") from erreur
    if date.tzinfo is None or date > datetime.now(timezone.utc):
        raise ValueError("Date de confirmation avec fuseau et non future requise.")
    return contenu, chemin.relative_to(prive).as_posix()


def _refuser_chevauchement(connexion, bobine, avant, apres):
    """Refuse deux pesées qui couvrent une même baisse en g pour la même bobine."""
    lignes = connexion.execute(
        "SELECT donnees_json FROM decisions WHERE json_extract(donnees_json,'$.type')='imputation_bobine' "
        "AND json_extract(donnees_json,'$.bobine_id')=?",
        (bobine,),
    )
    for (brut,) in lignes:
        trace = _json_objet(brut)
        if min(avant, trace["pesee_avant_g"]) > max(apres, trace["pesee_apres_g"]):
            raise ValueError("Pesées qui se chevauchent pour cette bobine.")


def imputer(base, retour_id, preuve_pesee, sha_pesee, confirmation, sha_confirmation):
    """Déduit les grammes confirmés et écrit la trace dans la même transaction.

    Les deux fichiers privés et leurs SHA sont requis. L’imputation concerne
    une impression, une plaque et un outil ; un rejeu exact conserve le débit
    initial et un conflit lève ValueError. Le résultat porte sur le stock
    déclaré. La machine, les réglages et les preuves source restent intacts.
    """
    if not isinstance(base, Base) or base.connexion.in_transaction:
        raise ValueError("Atelier BENCHAB hors transaction requis pour l’imputation.")
    with base.transaction() as connexion:
        # Réserver la transaction avant le contrôle et le débit de stock (#77).
        connexion.execute("BEGIN IMMEDIATE")
        plan = _estimer_retour(base.prive, connexion, retour_id)
        pesee = _lier_pesee(base.prive, connexion, plan, preuve_pesee, sha_pesee, exiger_depot=True)
        if pesee["statut"] != "DÉCLARATION LIÉE":
            raise ValueError(pesee["raison"])
        attestation, chemin = _confirmation(base.prive, confirmation, sha_confirmation, sha_pesee)
        fichier_pesee = sans_lien(Path(preuve_pesee).expanduser().absolute(), racine=base.prive, strict=True)
        fichier_confirmation = sans_lien(base.prive / chemin, racine=base.prive, strict=True)
        for fichier, attendu in ((fichier_pesee, sha_pesee), (fichier_confirmation, sha_confirmation)):
            if hashlib.sha256(_lire_fiche_bornee(fichier, limite=64 * 1024)).hexdigest() != attendu:
                raise ValueError("Une preuve a changé pendant le contrôle de l’imputation.")
        ligne, _, _ = _lire_retour(connexion, retour_id)
        projet, impression = ligne["projet_id"], ligne["impression_id"]
        identite = json.dumps([impression, pesee["plaque"], pesee["index_gcode"]], separators=(",", ":"))
        decision_id = "imputation-bobine:" + hashlib.sha256(identite.encode()).hexdigest()
        ancienne = connexion.execute("SELECT donnees_json FROM decisions WHERE id=?", (decision_id,)).fetchone()
        if ancienne is not None:
            trace = _json_objet(ancienne[0])
            if (
                trace.get("type") != "imputation_bobine" or trace.get("pesee_sha256") != sha_pesee
                or trace.get("confirmation_sha256") != sha_confirmation
            ):
                raise ValueError("Une imputation différente existe pour cette impression, plaque et outil.")
            return {**trace, "deja_imputee": True}

        avant, apres = pesee["masse_avant_g"], pesee["masse_apres_g"]
        quantite = Decimal(str(avant)) - Decimal(str(apres))
        if quantite <= 0:
            raise ValueError("Une baisse de masse strictement positive est requise.")
        bobine = pesee["bobine_id"]
        _refuser_chevauchement(connexion, bobine, avant, apres)
        stock = connexion.execute("SELECT masse_restante_g FROM bobines WHERE id=?", (bobine,)).fetchone()[0]
        if type(stock) not in (float, int) or not math.isfinite(stock) or Decimal(str(stock)) < quantite:
            raise ValueError("Stock de la bobine inconnu ou insuffisant.")
        reste = float(Decimal(str(stock)) - quantite)
        if reste >= stock:
            raise ValueError("Baisse de masse trop petite pour la précision du stock enregistré.")
        trace = {
            "schema": 1, "type": "imputation_bobine", "decision_id": decision_id, "statut": "IMPUTATION ENREGISTRÉE",
            "retour_id": retour_id, "impression_id": impression, "bobine_id": bobine,
            "plaque": pesee["plaque"], "index_gcode": pesee["index_gcode"],
            "membre_gcode": pesee["membre_gcode"], "gcode_sha256": pesee["gcode_sha256"],
            "envoi_sha256": plan["envoi"]["sha256"], "emplacement_declare": pesee["emplacement_declare"],
            "preuve_pesee": {"fichier": fichier_pesee.relative_to(base.prive).as_posix(), "sha256": sha_pesee},
            "pesee_sha256": sha_pesee, "confirmation_sha256": sha_confirmation, "confirmation": attestation,
            "pesee_avant_g": avant, "pesee_apres_g": apres, "consommation_declaree_g": float(quantite),
            "estimation_slicer_g": pesee["estimation_slicer_g"],
            "stock_avant_g": stock, "stock_apres_g": reste, "validation_physique": False,
        }
        connexion.execute("UPDATE bobines SET masse_restante_g=? WHERE id=?", (reste, bobine))
        connexion.execute(
            "INSERT INTO decisions(id,projet_id,date,texte,source,donnees_json) VALUES(?,?,?,?,?,?)",
            (decision_id, projet, attestation["date"], "Imputation de stock par pesée confirmée.",
             chemin, json.dumps(trace, ensure_ascii=False, allow_nan=False)),
        )
        return {**trace, "deja_imputee": False}
