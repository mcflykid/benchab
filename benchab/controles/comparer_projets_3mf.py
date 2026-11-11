"""Compare les réglages reconnus de deux projets 3MF, en lecture seule."""

from pathlib import Path

from benchab import reglages
from benchab.archives_3mf import Archive3MF

_CONFIG = "Metadata/project_settings.config"


def _lire(chemin):
    """Lit les réglages projet et le SHA exact dans un seul contexte 3MF."""
    with Archive3MF(chemin) as archive:
        empreinte = archive.sha256()
        if _CONFIG in archive.noms():
            donnees = archive.reglages(_CONFIG)
        else:
            donnees = None
        archive.verifier_source(chemin)
    return (dict(donnees) if donnees is not None else None, empreinte)


def _type_qualifie(cle, valeur):
    """Vérifie la forme native d’une valeur sans normaliser le dictionnaire."""
    definition = reglages.definition(cle, valeur=valeur)
    formes = definition.get("formes_certifiees", definition.get("formes", ()))
    return definition.get("type") not in (None, "inconnu") and type(valeur).__name__ in formes


def _comparer_reglages(source, candidat):
    """Compare les clés projet typées exactement et liste les absences/inconnus."""
    if source is None or candidat is None:
        return {
            "statut": "NON VÉRIFIÉ", "identiques": [], "differences": [], "absences": [],
            "non_verifies": [{
                "cle": _CONFIG, "statut": "NON VÉRIFIÉ",
                "raison": "Le membre de réglages projet manque d’une archive.",
                "present_source": source is not None, "present_candidat": candidat is not None,
            }],
        }
    identiques, differences, absences, inconnus = [], [], [], []
    qualifications = []
    for donnees in (source, candidat):
        contexte = reglages.contexte_filaments(donnees)
        qualifications.append({
            ligne["cle"]: ligne["statut"]
            for ligne in reglages.verifier(donnees, contexte)
        })
    qualification_source, qualification_candidat = qualifications
    for cle in sorted(set(source) | set(candidat)):
        present_source, present_candidat = cle in source, cle in candidat
        entree = {
            "cle": cle,
            "type": reglages.definition(cle).get("type", "inconnu"),
            "valeur_source": source.get(cle),
            "valeur_candidat": candidat.get(cle),
        }
        if not present_source or not present_candidat:
            absences.append({
                **entree,
                "present_source": present_source,
                "present_candidat": present_candidat,
                "statut": "DIFFÉRENT",
            })
        elif (
            not _type_qualifie(cle, source[cle])
            or not _type_qualifie(cle, candidat[cle])
            or type(source[cle]) is not type(candidat[cle])
            or qualification_source.get(cle) != "PASS"
            or qualification_candidat.get(cle) != "PASS"
        ):
            inconnus.append({
                **entree, "statut": "NON VÉRIFIÉ",
                "raison": "Type, forme ou cardinalité non qualifié par le dictionnaire.",
            })
        elif source[cle] != candidat[cle]:
            differences.append({**entree, "statut": "DIFFÉRENT"})
        else:
            identiques.append({**entree, "statut": "IDENTIQUE"})
    statut = "NON VÉRIFIÉ" if inconnus else "WARN" if differences or absences else "PASS"
    return {
        "statut": statut, "identiques": identiques, "differences": differences,
        "absences": absences, "non_verifies": inconnus,
    }


def _palette(source, candidat):
    """Compare les couleurs par slot seulement lorsque les identités ordonnées concordent."""
    resultats = {"statut": "NON VÉRIFIÉ", "slots": [], "attributions": {"statut": "NON VÉRIFIÉ"}}
    if source is None or candidat is None:
        resultats["raison"] = "Réglages projet manquants."
        return resultats
    identites_source = source.get("filament_settings_id")
    identites_candidat = candidat.get("filament_settings_id")
    if (
        not isinstance(identites_source, list)
        or not identites_source
        or not all(isinstance(x, str) and x.strip() for x in identites_source)
        or not _type_qualifie("filament_settings_id", identites_source)
        or not _type_qualifie("filament_settings_id", identites_candidat)
        or identites_source != identites_candidat
    ):
        resultats["raison"] = "Identités filament absentes, invalides ou d’ordre différent."
        return resultats
    couleurs_source = source.get("filament_colour")
    couleurs_candidat = candidat.get("filament_colour")
    if (
        not isinstance(couleurs_source, list)
        or not isinstance(couleurs_candidat, list)
        or len(couleurs_source) != len(identites_source)
        or len(couleurs_candidat) != len(identites_source)
        or not _type_qualifie("filament_colour", couleurs_source)
        or not _type_qualifie("filament_colour", couleurs_candidat)
    ):
        resultats["raison"] = "La palette n’est pas appariée exactement aux identités ordonnées."
        return resultats
    slots = []
    for index, identite in enumerate(identites_source):
        couleur_source = couleurs_source[index]
        couleur_candidat = couleurs_candidat[index]
        valide = isinstance(couleur_source, str) and isinstance(couleur_candidat, str)
        slots.append({
            "index": index + 1,
            "identite": identite,
            "couleur_source": couleur_source,
            "couleur_candidat": couleur_candidat,
            "statut": ("IDENTIQUE" if couleur_source == couleur_candidat else "DIFFÉRENT")
            if valide else "NON VÉRIFIÉ",
        })
    if any(slot["statut"] == "NON VÉRIFIÉ" for slot in slots):
        statut = "NON VÉRIFIÉ"
    elif any(slot["statut"] == "DIFFÉRENT" for slot in slots):
        statut = "WARN"
    else:
        statut = "PASS"
    resultats.update(
        statut=statut,
        slots=slots,
        attributions={
            "statut": "PASS",
            "raison": "Couleurs comparées par index uniquement sous identités exactes et ordonnées.",
        },
    )
    return resultats


def comparer(source: str | Path, candidat: str | Path) -> dict:
    """Compare deux 3MF sans les modifier ; valeurs sans conversion, empreintes SHA-256 exactes.

    Les réglages projet reconnus sont identiques, différents ou NON VÉRIFIÉS.
    La peinture par facette reste NON VÉRIFIÉE, faute d’identité et d’ordre de maillage
    comparés dans cette première étape.
    """
    source = Path(source).expanduser()
    candidat = Path(candidat).expanduser()
    etat_source, sha_source = _lire(source)
    etat_candidat, sha_candidat = _lire(candidat)
    reglages_comparaison = _comparer_reglages(etat_source, etat_candidat)
    palette = _palette(etat_source, etat_candidat)
    return {
        "schema": 1,
        "statut": "NON VÉRIFIÉ",  # La peinture par facette n’est pas qualifiée dans ce lot.
        "source": str(source),
        "candidat": str(candidat),
        "sha256": {
            "source": sha_source,
            "candidat": sha_candidat,
        },
        "reglages": reglages_comparaison,
        "palette": palette,
        "peinture": {
            "statut": "NON VÉRIFIÉ",
            "proprietes_standard_3mf": "NON VÉRIFIÉ",
            "extensions_asn_prusa": "NON VÉRIFIÉ",
            "raison": (
                "Aucune équivalence n’est déduite entre matériaux standard 3MF et peinture par facette "
                "spécifique aux slicers. Identité et ordre des facettes non établis."
            ),
        },
        "limites": [
            "Comparaison en lecture seule des seuls réglages projet décodés par le dictionnaire typé.",
            "Les valeurs sont comparées dans leur forme native, sans normalisation ni réindexation.",
            "La peinture de facettes standard ou spécifique au slicer reste NON VÉRIFIÉE.",
            "Aucune conversion, réécriture, compatibilité de slicer ou validation physique n’est établie.",
        ],
    }
