"""Types et accès uniques aux réglages ; aucune sémantique déduite de la taille d'une liste."""

import copy
import json
from functools import lru_cache
from importlib.resources import files
from zipfile import ZipFile

_ABSENT = object()


@lru_cache(maxsize=1)
def _schema():
    return json.loads(files("benchab").joinpath("reference/reglages-types.json").read_text())


def definition(cle, *, valeur=_ABSENT, portee="reglage"):
    """Décrit une clé et sa portée XML ; sans unité, inconnu reste NON VÉRIFIÉ et non transformable.

    Une métadonnée d'origine n'est connue que dans sa portée, jamais comme option projet implicite.
    """
    if portee != "reglage":
        structure = _schema().get("definitions_structurelles", {}).get(portee, {})
        if cle in structure:
            return copy.deepcopy(structure[cle])
    resultat = copy.deepcopy(_schema()["definitions"].get(cle, {"type": "inconnu", "longueur": None}))
    if valeur is not _ABSENT and type(valeur).__name__ in resultat.get("formes_non_verifiees", []):
        resultat.update(type="inconnu", longueur=None, forme_non_verifiee=True)
    return resultat


def profil_machine(nom):
    """Résout l'héritage fabricant et renvoie les vecteurs dans leurs unités natives, sans modifier les profils."""
    profils = _schema()["profils_machine"]
    vus = set()

    def resoudre(n):
        if n not in profils or n in vus:
            return {}
        vus.add(n)
        p = profils[n]
        parent = resoudre(p["inherits"]) if p.get("inherits") else {}
        return {**parent, **copy.deepcopy(p["modes"])}

    return resoudre(nom)


def nombre_filaments_metadata(donnees, *, consommateur=None, extrudeurs=()):
    """Résout N des métadonnées pour un lecteur déclaré ; renvoie int ou None/NON VÉRIFIÉ, sans créer de profil.

    Le lecteur CLI compte les profils ; la préparation BENCHAB peut exiger davantage de profils
    pour les affectations déclarées. PresetBundle compte la palette ; sans lecteur,
    des comptes différents restent ambigus. Entrées/sortie sans unité ; aucune liste n'est modifiée.
    """
    profils = donnees.get("filament_settings_id")
    couleurs = donnees.get("filament_colour")
    nombre_profils = len(profils) if isinstance(profils, list) and profils else None
    nombre_couleurs = len(couleurs) if isinstance(couleurs, list) and couleurs else None
    if consommateur == "cli":
        declares = donnees.get("nombre_filaments_declares", 0)
        if type(declares) is not int or declares < 0:
            raise ValueError("Nombre de filaments déclaré : entier positif ou zéro exigé")
        return max([nombre_profils or 0, declares, *extrudeurs, 1])
    if consommateur == "preset_bundle":
        return nombre_couleurs
    return nombre_profils if nombre_profils == nombre_couleurs else None


def longueur_attendue(cle, contexte):
    """Renvoie une longueur certaine ou None/NON VÉRIFIÉ ; ne crée aucune règle de diffusion implicite."""
    contexte = {**contexte, **getattr(contexte, "contexte", {})}
    d = definition(cle, valeur=contexte.get(cle, _ABSENT))
    regle = d.get("longueur")
    if regle == "profil_machine_resolu":
        valeurs = profil_machine(contexte.get("printer_settings_id", "")).get(cle)
        return len(valeurs) if isinstance(valeurs, list) else None
    if regle == "actifs_declares":
        if cle == "filament_ids":
            return nombre_filaments_metadata(contexte, consommateur=contexte.get("consommateur_reglages"))
        return contexte.get("nombre_filaments_declares", len(contexte.get("filament_settings_id", []))) or None
    if regle == "filaments_plus_machine_procede":
        nombre = nombre_filaments_metadata(contexte, consommateur=contexte.get("consommateur_reglages"))
        return nombre + 2 if nombre else None
    if regle == "plaques_declarees":
        return contexte.get("nombre_plaques_declares")
    if d["type"] == "liste_filament":
        # Les anciennes archives stockent parfois des entrées inutilisées. Leur
        # correspondance exige une déclaration, pas un broadcast supposé (docs/reference/slicer-anycubic.md).
        correspondance = contexte.get("correspondances_filaments", {}).get(cle)
        return len(correspondance) if correspondance is not None else None
    return None


def verifier(donnees, contexte=None):
    """Contrôle en lecture les types et cardinalités ; renvoie PASS/WARN/FAIL/NON VÉRIFIÉ sans réécrire.

    Pour un vecteur machine court non vide, expose les valeurs effectives par indice selon Orca
    `ConfigOptionVector.get_at` (Config.hpp:434-440) ; cela ne certifie pas le consommateur G-code
    Anycubic ni la version du binaire (docs/reference/reglages-et-integrite.md).
    """
    ctx = {**donnees, **getattr(donnees, "contexte", {}), **(contexte or {})}
    resultat = []
    for cle, valeur in donnees.items():
        d = definition(cle, valeur=valeur)
        attendu = longueur_attendue(cle, ctx)
        statut = "PASS"
        if d["type"] == "inconnu":
            statut = "NON VÉRIFIÉ"
        elif d["type"] == "chaine" and not isinstance(valeur, str):
            statut = "FAIL"
        elif d["type"] == "valeur_simple" and isinstance(valeur, (list, dict)):
            statut = "FAIL"
        elif d["type"].startswith("liste_"):
            if not isinstance(valeur, list):
                statut = "FAIL"
            elif not valeur and (d["type"] == "liste_mode_machine" or attendu is not None and attendu > 0):
                # Un vecteur machine vide échoue à l'assert get_at ; une liste vide ne remplit
                # aucune cardinalité positive déclarée (docs/reference/reglages-et-integrite.md).
                statut = "FAIL"
            elif attendu is None:
                statut = "NON VÉRIFIÉ"
            elif d["type"] == "liste_mode_machine" and len(valeur) != attendu:
                # get_at(i) replie les indices hors vecteur sur l’élément zéro ; l’archive reste
                # non canonique et toute écriture doit exiger la longueur exacte (docs de cardinalités).
                statut = "WARN"
            elif len(valeur) != attendu:
                # Les listes d'identifiants/héritages ont d'autres consommateurs, parfois indexés
                # directement ; sans preuve de leur repli effectif, l'écart reste NON VÉRIFIÉ.
                statut = "NON VÉRIFIÉ"
        ligne = {
            "cle": cle,
            "type": d["type"],
            "longueur_attendue": attendu,
            "longueur_observee": len(valeur) if isinstance(valeur, list) else None,
            "statut": statut,
        }
        if d["type"] == "liste_mode_machine" and statut == "WARN" and attendu is not None:
            ligne["detail"] = "Vecteur non canonique ; repli du slicer sur la première valeur via get_at."
            ligne["valeurs_effectives_par_indice"] = [
                {
                    "indice": indice,
                    "valeur": valeur[indice if indice < len(valeur) else 0],
                    "indice_source": indice if indice < len(valeur) else 0,
                }
                for indice in range(attendu)
            ]
            if len(valeur) > attendu:
                ligne["elements_supplementaires"] = valeur[attendu:]
        resultat.append(ligne)
    return resultat


def _mode_origine_non_verifie(cle, donnees, valeur):
    origine = donnees.original.get(cle, _ABSENT)
    if origine is _ABSENT or origine == valeur or definition(cle)["type"] != "liste_mode_machine":
        return False
    attendu = longueur_attendue(cle, {**donnees.original, **donnees.contexte})
    return attendu is None or not isinstance(origine, list) or len(origine) != attendu


class Reglages(dict):
    """Dictionnaire de réglages en unités natives ; toute écriture à cardinalité connue exige la longueur exacte.

    Les lectures préservent les listes et signalent WARN/NON VÉRIFIÉ selon le consommateur ;
    aucune liste n'est complétée.
    """

    def __init__(self, donnees=(), *, contexte=None):
        super().__init__(copy.deepcopy(dict(donnees)))
        self.original = copy.deepcopy(donnees.original if isinstance(donnees, Reglages) else dict(donnees))
        self.contexte = dict(contexte if contexte is not None else getattr(donnees, "contexte", {}))

    def __setitem__(self, cle, valeur):
        d = definition(cle, valeur=dict.get(self, cle, valeur))
        cible = definition(cle, valeur=valeur)
        if (d["type"] == "inconnu" or cible["type"] == "inconnu") and (
            cle not in self or dict.__getitem__(self, cle) != valeur
        ):
            raise ValueError("Réglage de type inconnu NON VÉRIFIÉ : transformation interdite : " + cle)
        if _mode_origine_non_verifie(cle, self, valeur):
            raise ValueError("Vecteur machine d'origine NON VÉRIFIÉ : transformation interdite : " + cle)
        attendu = longueur_attendue(cle, {**self, **self.contexte})
        if d["type"].startswith("liste_"):
            if not isinstance(valeur, list) or attendu is not None and len(valeur) != attendu:
                raise ValueError("Cardinalité de réglage invalide : " + cle)
            precedent = dict.get(self, cle)
            if attendu is None and isinstance(precedent, list) and precedent != valeur:
                raise ValueError("Correspondance NON VÉRIFIÉE : réindexation interdite : " + cle)
        dict.__setitem__(self, cle, copy.deepcopy(valeur))

    def update(self, autre=(), **valeurs):
        """Affecte des valeurs explicites dans leurs unités natives ;
        aucune normalisation silencieuse, lève sur FAIL."""
        nouveaux = {**dict(autre), **valeurs}
        essai = self.copy()
        for cle, valeur in nouveaux.items():
            essai[cle] = valeur
        dict.clear(self)
        dict.update(self, essai)

    def copy(self):
        """Renvoie une copie typée indépendante, sans unité ni statut ;
        conserve toutes les valeurs inconnues intactes."""
        resultat = Reglages(self, contexte=self.contexte)
        resultat.original = copy.deepcopy(self.original)
        return resultat

    def __delitem__(self, cle):
        if definition(cle, valeur=self[cle])["type"] == "inconnu":
            raise ValueError("Réglage de type inconnu NON VÉRIFIÉ : suppression interdite : " + cle)
        dict.__delitem__(self, cle)

    def pop(self, cle, *defaut):
        """Retire une clé connue ; retourne sa valeur en unités natives, refuse toute suppression inconnue."""
        if cle not in self:
            if defaut:
                return defaut[0]
            raise KeyError(cle)
        valeur = self[cle]
        del self[cle]
        return valeur

    def setdefault(self, cle, defaut=None):
        """Ajoute un défaut explicitement typé en unités natives ; refuse les clés inconnues, sans conversion."""
        if cle not in self:
            self[cle] = defaut
        return self[cle]

    def clear(self):
        """Refuse la suppression globale des réglages ; aucun réglage d'impression effacé implicitement."""
        raise ValueError("Suppression globale des réglages interdite")

    def popitem(self):
        """Refuse la suppression implicite d'une clé ; aucun réglage choisi arbitrairement."""
        raise ValueError("Suppression implicite des réglages interdite")

    def __ior__(self, autre):
        self.update(autre)
        return self

    def __deepcopy__(self, memo):
        resultat = Reglages(copy.deepcopy(dict(self), memo), contexte=copy.deepcopy(self.contexte, memo))
        resultat.original = copy.deepcopy(self.original, memo)
        memo[id(self)] = resultat
        return resultat


def decoder(texte, *, contexte=None):
    """Décode un objet JSON de réglages en unités natives ; renvoie Reglages, sans conversion ni réglage inventé."""

    def paires(lignes):
        resultat = {}
        for cle, valeur in lignes:
            if cle in resultat:
                raise ValueError("Clé de réglage JSON dupliquée : " + cle)
            resultat[cle] = valeur
        return resultat

    def constante(_valeur):
        raise ValueError("Constante JSON non finie interdite dans les réglages")

    donnees = json.loads(texte, object_pairs_hook=paires, parse_constant=constante)
    if not isinstance(donnees, dict):
        raise ValueError("Objet JSON de réglages requis")
    return Reglages(donnees, contexte=contexte)


def encoder(donnees, **options):
    """Encode des réglages ; toute cardinalité connue non exacte produit FAIL à l'écriture.

    Les listes non canoniques restent lisibles et intactes, mais leur sérialisation est refusée ;
    aucune version/provenance n'est inventée (docs/reference/reglages-et-integrite.md).
    """
    if isinstance(donnees, Reglages):
        # Une liste JSON reste mutable : contrôler également les mutations
        # imbriquées avant sérialisation (docs/reference/integrite-3mf.md).
        for cle in donnees.original.keys() | donnees.keys():
            origine = donnees.original.get(cle, _ABSENT)
            cible = donnees.get(cle, _ABSENT)
            inconnu = (
                definition(cle, valeur=origine)["type"] == "inconnu"
                or definition(cle, valeur=cible)["type"] == "inconnu"
            )
            if inconnu and (cle not in donnees or cle not in donnees.original or donnees[cle] != donnees.original[cle]):
                raise ValueError("Réglage inconnu modifié : " + cle)
            if cle in donnees and _mode_origine_non_verifie(cle, donnees, donnees[cle]):
                raise ValueError("Vecteur machine d'origine NON VÉRIFIÉ : mutation imbriquée interdite : " + cle)
            if (
                cle in donnees and cle in donnees.original
                and donnees[cle] != donnees.original[cle]
                and definition(cle)["type"] == "liste_mode_machine"
            ):
                attendu = longueur_attendue(cle, donnees)
                if not isinstance(donnees[cle], list) or attendu is None or len(donnees[cle]) != attendu:
                    # Une lecture historique NON VÉRIFIÉE ne doit pas autoriser une réduction
                    # par mutation imbriquée (docs/reference/reglages-et-integrite.md, défaut M203).
                    raise ValueError("Cardinalité machine modifiée par mutation imbriquée : " + cle)
            if (
                cle in donnees.original
                and cle in donnees
                and definition(cle)["type"].startswith("liste_")
                and longueur_attendue(cle, {**donnees, **donnees.contexte}) is None
                and donnees[cle] != donnees.original[cle]
            ):
                raise ValueError("Correspondance NON VÉRIFIÉE : liste modifiée : " + cle)
    controles = verifier(donnees, getattr(donnees, "contexte", None))
    cardinalites = {
        cle: longueur_attendue(cle, {**donnees, **getattr(donnees, "contexte", {})})
        for cle, valeur in donnees.items()
        if definition(cle, valeur=valeur)["type"].startswith("liste_")
    }
    if any(
        cardinalites[ligne["cle"]] is not None
        and ligne["longueur_observee"] != cardinalites[ligne["cle"]]
        for ligne in controles
        if ligne["cle"] in cardinalites
    ) or any(ligne["statut"] == "FAIL" for ligne in controles):
        raise ValueError("Réglages de cardinalité invalide : fichier non livré")
    return json.dumps(dict(donnees), **options)


def valeur_brute(donnees, cle):
    """Lit une valeur connue en unités natives ; lève NON VÉRIFIÉ si son type est inconnu, sans conversion."""
    valeur = donnees.get(cle)
    if cle not in donnees or definition(cle, valeur=valeur)["type"] == "inconnu":
        raise ValueError("Réglage NON VÉRIFIÉ : lecture typée impossible : " + cle)
    return copy.deepcopy(valeur)


def valeur_unique(donnees, cle):
    """Lit une valeur scalaire ou un filament unique en unités natives ; refuse listes ambiguës et modes machine."""
    valeur = donnees.get(cle)
    d = definition(cle, valeur=valeur)
    if d["type"] in ("inconnu", "liste_mode_machine", "liste_plaque", "liste_objet"):
        raise ValueError("Réglage sans valeur unique vérifiée : " + cle)
    if isinstance(valeur, list):
        if len(valeur) != 1:
            raise ValueError("Correspondance NON VÉRIFIÉE : valeur unique requise : " + cle)
        return valeur[0]
    return valeur


def lire_3mf(chemin):
    """Lit le projet d'un 3MF (unités natives), retourne Reglages ; aucune écriture ni ouverture du slicer."""
    with ZipFile(chemin) as archive:
        if archive.getinfo("Metadata/project_settings.config").file_size > 8 * 1024 * 1024:
            raise ValueError("Configuration 3MF trop volumineuse")
        return decoder(archive.read("Metadata/project_settings.config"))


def valeur_indice(donnees, cle, indice, *, lecteur):
    """Lit un élément vectoriel en unités natives ; None/NON VÉRIFIÉ si type, indice ou lecteur sont ambigus.

    `get_at` replie un indice trop grand sur le premier élément ; `index_exact` ne replie pas.
    Le choix du lecteur doit être explicite, sans modification des réglages (Config.hpp:434-440).
    """
    if definition(cle)["type"] not in {"liste_mode_machine", "liste_filament", "liste_objet", "liste_plaque"}:
        return None
    valeurs = donnees.get(cle)
    if not isinstance(valeurs, list) or not valeurs or type(indice) is not int or indice < 0:
        return None
    if lecteur == "get_at":
        position = indice if indice < len(valeurs) else 0
    elif lecteur == "index_exact":
        if indice >= len(valeurs):
            return None
        position = indice
    else:
        return None
    try:
        return float(valeurs[position])
    except (TypeError, ValueError):
        return None


def valeur_mode(donnees, cle, mode="standard"):
    """Lit le mode machine demandé (mm/s, mm/s² ou mm/s de jerk) ; valeur None reste NON VÉRIFIÉE.

    Un indice hors vecteur replie sur le premier élément comme Orca `ConfigOptionVector.get_at`
    (Config.hpp:434-440). Le consommateur G-code Anycubic documenté a un repli différent ; le lien
    au binaire installé reste NON VÉRIFIÉ et aucun profil n'est modifié.
    """
    if definition(cle)["type"] != "liste_mode_machine":
        return None
    indice = {"standard": 0, "silencieux": 1, "sport": 2}.get(mode)
    return None if indice is None else valeur_indice(donnees, cle, indice, lecteur="get_at")
