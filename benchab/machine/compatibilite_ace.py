"""Compatibilité documentaire de l'acheminement déclaré avant envoi."""

GUIDE_ACE_2 = "https://wiki.anycubic.com/en/fdm-3d-printer/ace-2-pro/warm-tips"
GUIDE_ACE = "https://wiki.anycubic.com/en/fdm-3d-printer/kobra-3-combo/ace-pro-notes"
FICHE_95A = "https://store.anycubic.com/products/tpu-filament?variant=44896641548477"
FICHE_68D = "https://store.anycubic.com/products/tpu-68d?variant=47608800510114"

# Couples exacts relus à la source primaire ; portée et date dans
# docs/reference/machine-kobra-s1.md. Ne pas étendre par famille de matériau.
_PRODUITS = {
    ("ACE Pro", "Anycubic TPU 95A"): ("FAIL", GUIDE_ACE),
    ("ACE 2 Pro", "Anycubic TPU 95A"): ("FAIL", FICHE_95A),
    ("ACE 2 Pro", "Anycubic TPU 68D"): ("PASS", FICHE_68D),
}


def evaluer_alimentation(declaration, emplacement, type_3mf):
    """Compare le routage déclaré et le type 3MF typé ; emplacement ACE entier de 1 à 4.

    Renvoie acheminement et bobine sous forme (PASS, WARN, FAIL ou NON VÉRIFIÉ,
    détail, source). Aucune vérification physique ni modification de réglage.
    """
    inconnu = ("NON VÉRIFIÉ", "déclaration ou source exacte insuffisante", "déclaration d'alimentation")
    resultat = {"acheminement": inconnu, "bobine": inconnu}
    if not isinstance(declaration, dict):
        return resultat
    chemin = declaration.get("chemin")
    generation = declaration.get("generation_ace")
    produit = declaration.get("produit")
    slot_valide = type(emplacement) is int and 1 <= emplacement <= 4
    if chemin == "arriere":
        if slot_valide:
            resultat["acheminement"] = ("FAIL", "entrée arrière et emplacement ACE déclarés ensemble", "déclaration")
        elif emplacement is None:
            resultat["acheminement"] = (
                "NON VÉRIFIÉ", "entrée arrière déclarée ; aucune compatibilité ACE déduite", "déclaration"
            )
        return resultat
    if chemin == "sechage":
        resultat["acheminement"] = (
            "NON VÉRIFIÉ", "le séchage déclaré ne prouve pas l'acheminement pour imprimer", "déclaration"
        )
        return resultat
    if chemin != "ace" or not slot_valide or generation not in ("ACE Pro", "ACE 2 Pro"):
        return resultat
    if declaration.get("bobine") == "carton":
        resultat["bobine"] = (
            "WARN", "bobine carton déclarée : risque de glissement ou de débris selon le fabricant",
            GUIDE_ACE if generation == "ACE Pro" else GUIDE_ACE_2,
        )
    if isinstance(produit, str) and (couple := _PRODUITS.get((generation, produit))) is not None:
        if type_3mf != "TPU":
            resultat["acheminement"] = (
                "NON VÉRIFIÉ", "le type TPU du produit déclaré n'est pas confirmé par le 3MF", "déclaration et 3MF"
            )
            return resultat
        statut, source = couple
        detail = (
            "produit donné compatible pour l'acheminement par ce modèle d'ACE"
            if statut == "PASS"
            else "produit donné incompatible pour l'acheminement par ce modèle d'ACE"
        )
        resultat["acheminement"] = (statut, f"{produit} / {generation} : {detail} ; preuve documentaire", source)
    return resultat
