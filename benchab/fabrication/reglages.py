"""
Normalisation de métadonnées 3MF sans lancement du slicer.
"""

from benchab import reglages as schema_reglages


def normaliser_heritages(reglages):
    """
    Contrat procédé + N filaments + machine (longueurs sans unité).

    Renvoie une copie et les changements déclarés ; refuse les parents nommés,
    ne modifie aucun réglage d'impression ni profil installé.

    """
    reglages = schema_reglages.Reglages(
        reglages, contexte={**getattr(reglages, "contexte", {}), "consommateur_reglages": "cli"}
    )
    groupe = reglages.get("inherits_group")
    if groupe is None:
        return reglages, []
    filaments = reglages.get("filament_settings_id")
    if (
        not isinstance(groupe, list)
        or not isinstance(filaments, list)
        or not filaments
        or any(not isinstance(x, str) or not x for x in filaments)
    ):
        raise ValueError("Héritages : liste et noms de filaments explicites requis.")
    attendu = schema_reglages.longueur_attendue("inherits_group", reglages)
    if len(groupe) == attendu:
        return reglages, []
    if any(x != "" for x in groupe):
        raise ValueError("Héritages incohérents avec parents nommés : aucune conversion automatique.")
    resultat = reglages.copy()
    resultat["inherits_group"] = [""] * attendu
    return resultat, [
        {
            "reglage": "inherits_group",
            "avant": groupe,
            "apres": resultat["inherits_group"],
            "raison": (
                "Contrat procédé + N filaments + machine ; évite une lecture hors du tableau des noms dans la CLI."
            ),
            "source_url": (
                "https://github.com/ANYCUBIC-3D/AnycubicSlicerNext"
                "/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/OrcaSlicer.cpp#L1446-L1475"
            ),
        }
    ]
