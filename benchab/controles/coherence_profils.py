"""Avertissements sourcés sur les réglages du 3MF et les profils fournis."""

_REGLES = (
    ("machine", "printer_model", "modèle d'imprimante"),
    ("machine", "nozzle_type", "matière de buse du profil"),
    ("process", "wall_sequence", "ordre des parois"),
    ("process", "sparse_infill_pattern", "motif clairsemé"),
    ("filament", "filament_type", "matière du filament"),
)


def evaluer(comparaisons, contexte_filaments, empreinte_3mf):
    """Résume les comparaisons typées (chaînes, indices de slots dès 1, SHA-256 hexadécimal).

    Retourne PASS, WARN ou NON VÉRIFIÉ et les preuves par règle. Une différence
    sourcée reste WARN même si d'autres clés manquent. Aucun réglage n'est écrit,
    aucune conformité du filament ou de la buse réellement montés n'est déduite.
    """
    index = {
        (ligne["profil"]["categorie"], ligne["cle"], ligne.get("slot")): ligne
        for ligne in comparaisons if ligne["portee"] == "projet"
    }
    nombre_filaments = contexte_filaments["nombre_filaments_declares"]
    resultat = []
    for categorie, cle, libelle in _REGLES:
        slots = range(1, nombre_filaments + 1) if categorie == "filament" and nombre_filaments else (None,)
        for slot in slots:
            comparaison = index.get((categorie, cle, slot))
            ligne = {
                "cle": cle, "controle": libelle, "famille": categorie, "slot": slot,
                "statut": "NON VÉRIFIÉ", "detail": "Valeur ou source typée absente ou ambiguë.",
                "source_3mf": {"sha256": empreinte_3mf, "chemin": f"projet/{cle}"},
                "source_profil": None,
            }
            if comparaison is not None:
                profil = comparaison["profil"]
                valeurs = (comparaison["valeur_3mf"], comparaison["valeur_profil"])
                if profil["sha256"] and all(isinstance(v, str) and v.strip() for v in valeurs):
                    identique = comparaison["statut"] == "IDENTIQUE"
                    detail = "Identique au profil fourni." if identique else "Écart au profil fourni, à expliquer."
                    ligne.update(
                        statut="PASS" if identique else "WARN",
                        detail=detail,
                        valeur_3mf=valeurs[0],
                        valeur_profil=valeurs[1],
                        source_profil=profil,
                    )
            resultat.append(ligne)
    statuts = {ligne["statut"] for ligne in resultat}
    return {
        "statut": "WARN" if "WARN" in statuts else "NON VÉRIFIÉ" if "NON VÉRIFIÉ" in statuts else "PASS",
        "resultats": resultat,
        "portee": "Cohérence documentaire avec les profils fournis ; aucune validation physique.",
    }
