"""Recherche structurée des observations physiques ; les champs inconnus restent inconnus."""

import json


def impressions(base, *, resultat=None, matiere=None, ponts=False):
    """Ne déduit ni réussite, ni matière, ni pont à partir d'un texte libre.

    Les métadonnées recherchables appartiennent à l'impression observée. Les
    réglages seuls d'un projet ou d'une autre préparation ne sont pas hérités.
    """
    if resultat is not None and resultat not in {"reussie", "echec", "interrompue", "inconnu"}:
        raise ValueError("Résultat d'impression inconnu.")
    sql = (
        "SELECT i.*, p.projet_id, p.nom AS preparation, j.nom AS projet "
        "FROM impressions i JOIN preparations p ON p.id=i.preparation_id "
        "JOIN projets j ON j.id=p.projet_id"
    )
    lignes = [dict(row) for row in base.connexion.execute(sql + " ORDER BY i.date,i.id")]
    vus = {row["id"] for row in lignes}
    # Un retour sans préparation reste une observation du projet ; aucun Pnn
    # fictif n'est créé pour satisfaire la relation SQL des impressions préparées.
    for projet in base.connexion.execute("SELECT id,nom,donnees_json FROM projets ORDER BY id"):
        donnees_projet = json.loads(projet["donnees_json"])
        if not isinstance(donnees_projet, dict):
            continue
        historique = donnees_projet.get("retours_physiques", [])
        retours = list(historique) if isinstance(historique, list) else []
        suivi = donnees_projet.get("suivi", {})
        evenements = suivi.get("evenements", []) if isinstance(suivi, dict) else []
        retours += [e for e in (evenements if isinstance(evenements, list) else [])
                    if isinstance(e, dict) and e.get("type") == "retour_physique"]
        for retour in retours:
            if (not isinstance(retour, dict) or not isinstance(retour.get("id"), str)
                    or not retour["id"] or retour["id"] in vus):
                continue
            if not (retour.get("preuve") or retour.get("source")):
                continue
            verdict = {"defaut": "echec", "arretee": "interrompue"}.get(retour.get("resultat"), retour.get("resultat"))
            if verdict not in {"reussie", "echec", "interrompue", "inconnu"}:
                continue
            vus.add(retour["id"])
            lignes.append({
                "id": retour["id"], "preparation_id": None, "preparation": retour.get("preparation"),
                "date": retour.get("date"), "resultat": verdict, "projet_id": projet["id"], "projet": projet["nom"],
                "donnees_json": json.dumps({**retour, "origine": "retour_projet_sans_preparation"}),
            })
    reponses = []
    for ligne in lignes:
        row = dict(ligne)
        donnees = json.loads(row.pop("donnees_json"))
        if not isinstance(donnees, dict):
            continue
        if resultat is not None and row["resultat"] != resultat:
            continue
        filaments = donnees.get("filaments", [])
        if matiere is not None and (not isinstance(filaments, list) or not any(
            isinstance(f, dict) and f.get("matiere") == matiere
            and f.get("confirmation_bobine_physique") is not False
            and f.get("nature") != "déclaration de profil/filament" for f in filaments
        )):
            continue
        preuve_ponts = donnees.get("ponts", {})
        if ponts and not (
            isinstance(preuve_ponts, dict)
            and preuve_ponts.get("present") is True
            and isinstance(preuve_ponts.get("source"), str)
            and preuve_ponts["source"].strip()
        ):
            continue
        reponses.append({**row, "donnees": donnees})
    return sorted(reponses, key=lambda row: (row["date"] or "", row["id"]))
