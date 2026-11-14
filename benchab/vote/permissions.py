"""Classification et limites du vote ; aucune action ni autorisation exécutée."""

import json
import re
import unicodedata
from importlib.resources import files

G = json.loads(files("benchab").joinpath("reference/organisation/grille.json").read_text())


def norm(s):
    """Retourne le texte en minuscules sans marques diacritiques pour la recherche par mot."""
    return "".join((c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn"))


def contient(t, mot):
    """Mot ou expression entier, pluriel en s/x accepté (« cle » ne trouve pas « cycle »)."""
    return re.search("(?<![a-z0-9])" + re.escape(norm(mot).strip()) + "(?:s|x)?(?![a-z0-9])", t) is not None


def classer(texte, mode=None, cfg=None):
    """Classe le texte en catégorie A, B ou C selon la grille et le mode déclarés.

    Renvoie le niveau, les spécialistes et les indices retenus. Ne donne
    aucune autorisation et n’exécute aucune action.
    """
    t = norm(texte)
    C = G["classification"]
    mode = mode or (cfg or {}).get("mode") or G["mode_par_defaut"]
    trouve = {k: [m for m in C[k] if contient(t, m)] for k in ("C", "B", "automatisable")}
    cat = "C" if trouve["C"] else "B" if trouve["B"] else "A"
    spec = []
    for mot, ids in G["activation_specialistes"].items():
        if mot.startswith("_"):
            continue
        if contient(t, mot):
            spec += [i for i in ids if i not in spec]
    maximum = G["modes"][mode]["specialistes_max"]
    if cat in ("B", "C"):
        requis = "P3.compatibilite"
        autres = [i for i in spec if i != requis]
        spec = autres[: maximum - 1] + [requis]
    else:
        spec = spec[:maximum]
    if cat == "A":
        niveau = 0 if trouve["automatisable"] else 1
        spec = [] if niveau == 0 else spec[:1]
    elif cat == "B":
        niveau = 3 if mode == "APPROFONDI" else 2
    else:
        niveau = 3
    conseil = G["categories"][cat]["conseil"]
    return {
        "categorie": cat,
        "libelle": G["categories"][cat]["libelle"],
        "niveau": niveau,
        "niveau_libelle": G["niveaux"][str(niveau)],
        "mode": mode,
        "specialistes": spec,
        "conseil": "vote des 7 administrateurs" if conseil else "non",
        "accord_utilisateur": G["categories"][cat].get("accord_utilisateur", False),
        "indices": trouve,
        "automatisations": [
            a for a in (cfg or {}).get("automatisations", []) if any((contient(t, m) for m in a.get("mots", [])))
        ],
    }


ACTIONS_COURANTES = frozenset(("documentation", "classement", "verification", "lecture", "correction_reversible"))

ACTIONS_PROTEGEES = frozenset(
    (
        "suppression_definitive",
        "impression",
        "chauffe",
        "mouvement",
        "calibration",
        "modification_piece_imprimee",
        "modification_piece_protegee",
        "modification_profil_installe",
        "depense",
        "publication",
        "exposition_donnees",
        "git_init",
        "git_commit",
        "git_push",
    )
)

INDICES_PROTEGES = (
    "supprimer",
    "suppression",
    "effacer",
    "delete",
    "unlink",
    "rmtree",
    "rm",
    "imprimer",
    "impression",
    "print",
    "chauffer",
    "chauffe",
    "prechauffer",
    "deplacer",
    "deplacement",
    "bouger",
    "mouvement",
    "move",
    "chauffer",
    "chauffage",
    "heat",
    "calibrer",
    "calibration",
    "etalonnage",
    "calibrate",
    "homing",
    "piece imprimee",
    "deja imprime",
    "deja imprimee",
    "piece protegee",
    "piece figee",
    "profil installe",
    "profils installes",
    "profil systeme",
    "depense",
    "depenser",
    "achat",
    "acheter",
    "payer",
    "paiement",
    "publication",
    "publier",
    "exposer",
    "exposition",
    "mettre en vente",
    "commercialiser",
    "git init",
    "git commit",
    "git push",
    "git reset",
    "git clean",
    "git merge",
    "git rebase",
)


def permissions(data):
    """Déduction conservatrice pour l'affichage du vote, jamais une autorisation d'exécuter.

    Le type connu est nécessaire pour approuver ; les descriptions et le risque peuvent
    toujours renforcer la restriction. Un champ déclaratif ne peut jamais la lever.
    """
    motifs = []
    types = []
    categorie = "A"

    def examiner(action):
        nonlocal categorie
        if not isinstance(action, dict):
            motifs.append("action non structurée")
            return
        textes = []
        for champ in ("objet", "recommandation", "action", "cible", "commande"):
            valeur = action.get(champ, "")
            if not isinstance(valeur, str):
                motifs.append(champ + " non interprétable")
            else:
                textes.append(valeur)
        brut = action.get("type_action")
        typ = norm(brut).strip().replace("-", "_").replace(" ", "_") if isinstance(brut, str) else None
        if typ:
            types.append(typ)
            textes.append(typ.replace("_", " "))
        if typ in ACTIONS_PROTEGEES:
            motifs.append("type protégé : " + typ)
        enfants = action.get("actions")
        if "actions" in action and (not isinstance(enfants, list) or not enfants):
            motifs.append("liste d’actions absente ou non interprétable")
        if typ not in ACTIONS_COURANTES and typ not in ACTIONS_PROTEGEES:
            if "type_action" in action or not isinstance(enfants, list) or (not enfants):
                motifs.append("type d’action inconnu ou absent")
        texte = norm(" ".join(textes))
        for indice in INDICES_PROTEGES:
            if contient(texte, indice):
                motifs.append("action protégée détectée : " + indice)
        detectee = classer(texte)["categorie"]
        retenue = detectee
        for champ in ("categorie", "categorie_risque"):
            if champ not in action:
                continue
            declaree = action[champ]
            if not isinstance(declaree, str) or declaree not in G["categories"]:
                motifs.append("catégorie de risque inconnue : " + champ)
            else:
                retenue = max(retenue, declaree)
        categorie = max(categorie, retenue)
        if G["categories"][retenue].get("accord_utilisateur"):
            motifs.append("catégorie de risque exigeant l’accord utilisateur : " + retenue)
        if action.get("autorisation_requise"):
            motifs.append("autorisation explicitement requise")
        if action.get("commande"):
            motifs.append("commande à soumettre à l’utilisateur (exécution non vérifiée)")
        if isinstance(enfants, list):
            for enfant in enfants:
                examiner(enfant)

    examiner(data)
    return {
        "autorisation_requise": bool(motifs),
        "motifs": list(dict.fromkeys(motifs)),
        "categorie_risque": categorie,
        "types_action": list(dict.fromkeys(types)),
    }
