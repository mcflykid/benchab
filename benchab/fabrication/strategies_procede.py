"""Audite des profils et prépare des études expérimentales privées de procédé."""

import argparse
import copy
import hashlib
import json
import math
import re
import shutil
import zipfile
from pathlib import Path

from benchab import reglages as schema_reglages
from benchab.archives_3mf import Archive3MF
from benchab.donnees.base import Base
from benchab.fabrication import optimisation_entrees, tranchage_copies
from benchab.fabrication.optimisation import _fichier_prive, _lire_maitre, _publier_json, lire_3mf, positif
from benchab.machine import profil_maitre as maitre_api
from benchab.qualite.empreintes import sha256_fichier

STRATEGIES = ("adaptivecubic", "combinaison", "dessus", "supports")
IDENTITE = {"name", "type", "base_id", "version", "inherits", "print_settings_id"}


def sha(chemin: str | Path) -> str:
    return sha256_fichier(chemin)


def _dossier_sortie(base: Base, chemin: str | Path) -> Path:
    candidat = Path(chemin).expanduser()
    if not candidat.is_absolute():
        candidat = base.prive / candidat
    if any(element.is_symlink() for element in (candidat, *candidat.parents)):
        raise ValueError("Lien symbolique dans la sortie d’étude refusé.")
    if not candidat.resolve(strict=False).is_relative_to(base.prive.resolve()):
        raise ValueError("La sortie d’étude doit rester dans base.prive.")
    if any(re.fullmatch(r"P\d+", partie) for partie in candidat.parts):
        raise ValueError("Une étude ne peut pas être publiée dans une préparation figée Pnn.")
    if candidat.exists():
        raise FileExistsError("Le dossier d’étude doit être neuf.")
    return candidat


def _json_objet(chemin: str | Path) -> dict:
    donnees = json.loads(Path(chemin).read_text(encoding="utf-8"))
    if not isinstance(donnees, dict):
        raise ValueError("Objet JSON requis.")
    return donnees


def _nombre_fini(valeur, nom: str) -> float:
    if isinstance(valeur, bool):
        raise ValueError(f"{nom} : booléen interdit.")
    try:
        resultat = float(valeur)
    except (TypeError, ValueError, OverflowError) as erreur:
        raise ValueError(f"{nom} : valeur numérique requise.") from erreur
    if not math.isfinite(resultat):
        raise ValueError(f"{nom} : nombre fini requis.")
    return resultat


def _maitre(base: Base, configuration: dict, maitre=None):
    return _lire_maitre(base, configuration, maitre)


def contexte_etude(base: Base, maitre=None) -> dict:
    """Empreintes SHA-256 du profil et des modules de l’étude ; aucune préparation modifiée."""
    profil, limites = _maitre(base, {}, maitre)
    modules = [
        Path(__file__),
        Path(maitre_api.__file__),
        Path(__import__("benchab.fabrication.comparer_remplissages", fromlist=["main"]).__file__),
        Path(__import__("benchab.fabrication.optimisation", fromlist=["proposer"]).__file__),
        Path(optimisation_entrees.__file__),
        Path(tranchage_copies.__file__),
        Path(__import__("benchab.fabrication.anycubic", fromlist=["trancher"]).__file__),
    ]
    return {
        "maitre_id": profil.get("profil_id", profil.get("id")),
        "maitre_sha256": hashlib.sha256(
            json.dumps(
                {k: v for k, v in profil.items() if not k.startswith("_")}, sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest(),
        "limites_sha256": hashlib.sha256(json.dumps(limites, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "code_sha256": {path.name: sha(path) for path in modules},
    }


def auditer_profil(
    base: Base,
    chemin: str | Path,
    parents: dict,
    atelier: dict | None = None,
) -> dict:
    """Lit les réglages typés et parents explicites (unités natives) ; rapport d'effets, sans adoption ni écriture."""
    fichier = _fichier_prive(base, chemin)
    brut = schema_reglages.Reglages(_json_objet(fichier))
    if brut.get("type") not in ("print", "process") or not isinstance(brut.get("name"), str) or not brut["name"]:
        raise ValueError("Profil de procédé nommé requis.")
    if not isinstance(parents, dict):
        raise ValueError("Parents explicites nom -> JSON ou chemin requis.")
    sources = {brut["name"]: {**copy.deepcopy(brut), "type": "process"}}
    for nom, parent in parents.items():
        if isinstance(parent, dict):
            sources[nom] = copy.deepcopy(parent)
        elif isinstance(parent, str):
            sources[nom] = _json_objet(_fichier_prive(base, parent))
        else:
            raise ValueError("Parent de preset invalide : " + str(nom))
    resolution = maitre_api.resoudre_preset(brut["name"], "process", sources)
    valeurs = resolution["valeurs"]
    risques = []

    def noter(cle, effet, risque, conjoint, verification):
        risques.append(
            {
                "parametre": cle,
                "valeur": valeurs.get(cle),
                "effet": effet,
                "risque": risque,
                "parametres_conjoints": conjoint,
                "verification": verification,
            }
        )

    couche = positif(valeurs["layer_height"], "hauteur de couche") if valeurs.get("layer_height") is not None else None
    couches_dessus = (
        positif(valeurs["top_shell_layers"], "couches supérieures", nul=True)
        if valeurs.get("top_shell_layers") is not None
        else None
    )
    epaisseur = (
        positif(valeurs["top_shell_thickness"], "épaisseur supérieure", nul=True)
        if valeurs.get("top_shell_thickness") is not None
        else None
    )
    if couches_dessus is not None and not couches_dessus.is_integer():
        raise ValueError("Nombre entier de couches supérieures requis.")
    minimum = max(couche * couches_dessus, epaisseur) if None not in (couche, couches_dessus, epaisseur) else None
    if valeurs.get("bridge_flow") is not None and positif(valeurs["bridge_flow"], "débit ponts") > 1:
        noter(
            "bridge_flow",
            "rapport de débit de pont augmenté",
            "sur-extrusion ou pont plus lourd ; aucune adoption automatique",
            ["bridge_speed", "refroidissement", "largeur de pont", "débit filament", "température"],
            "contrôler les ponts tranchés et confirmer l’aspect physique",
        )
    if (
        valeurs.get("initial_layer_speed") is not None
        and positif(valeurs["initial_layer_speed"], "vitesse initiale") > 40
    ):
        noter(
            "initial_layer_speed",
            "réduction possible du temps initial",
            "adhésion non prouvée pour la pièce et le plateau",
            ["initial_layer_infill_speed", "hauteur et largeur initiales", "bordure", "température plateau"],
            "contrôler l’appui, les îlots et le G-code de première couche",
        )
    if valeurs.get("support_object_xy_distance") is not None:
        noter(
            "support_object_xy_distance",
            "éloignement du support du décor",
            "certaines régions peuvent être insuffisamment soutenues",
            ["distances Z", "interface", "surplombs"],
            "revoir les zones soutenues ; le retrait physique reste inconnu",
        )
    if (
        valeurs.get("xy_contour_compensation") is not None
        and _nombre_fini(valeurs["xy_contour_compensation"], "compensation XY") != 0
    ):
        noter(
            "xy_contour_compensation",
            "décalage dimensionnel des contours",
            "jeux et interfaces modifiés ; ne pas transférer sans mesures",
            ["compensation XY des trous", "dimensions", "jeu de chaque interface"],
            "mesurer chaque interface et comparer la géométrie",
        )
    if str(valeurs.get("infill_combination", "0")) == "1":
        noter(
            "infill_combination",
            "regroupement de couches de remplissage par le slicer",
            "hauteur et débit locaux plus élevés selon les couches et la buse",
            ["hauteur de couche", "diamètre de buse", "largeur", "débit maximum", "dessus"],
            "relire les hauteurs et débits réellement émis",
        )
    if valeurs.get("sparse_infill_pattern") == "adaptivecubic":
        noter(
            "sparse_infill_pattern",
            "densité interne adaptée aux surfaces",
            "résistance et appui des dessus non établis par la durée",
            ["densité", "dessus", "efforts", "parois"],
            "comparer même pièce, matière et couche ; audit mécanique distinct",
        )
    differences = {}
    if atelier is not None:
        if not isinstance(atelier, dict):
            raise ValueError("Référence atelier résolue requise.")
        differences = {
            cle: {"externe": valeurs.get(cle), "atelier": atelier.get(cle)}
            for cle in sorted(set(valeurs) | set(atelier))
            if cle not in IDENTITE and valeurs.get(cle) != atelier.get(cle)
        }
    return {
        "schema": 1,
        "statut": "NON VÉRIFIÉ" if resolution["manquants"] else "WARN",
        "source": {"fichier": str(fichier), "sha256": sha(fichier), "type_original": brut["type"]},
        "normalisation": "type print converti en process en mémoire pour analyse seulement",
        "resolution": resolution,
        "dessus": {
            "couches_seules_mm": couche * couches_dessus if None not in (couche, couches_dessus) else None,
            "minimum_epaisseur_herite_mm": epaisseur,
            "minimum_nominal_mm": minimum,
            "limite": "nombre effectif de couches et fermeture à contrôler après tranchage",
        },
        "differences": differences,
        "risques": risques,
        "adoption_automatique": False,
        "validation_physique": "NON VÉRIFIÉ",
    }


def auditer_machine(base: Base, chemin: str | Path, maitre=None) -> dict:
    fichier = _fichier_prive(base, chemin)
    brut = _json_objet(fichier)
    if brut.get("type") not in ("printer", "machine"):
        raise ValueError("Profil machine requis.")
    profil, limites = _maitre(base, {}, maitre)
    alertes = []
    if brut.get("printer_model") not in ("Anycubic Kobra S1", "Anycubic Kobra S1 Combo"):
        alertes.append("Modèle différent ou absent du maître.")
    if brut.get("nozzle_type") != limites.get("buse_matiere"):
        alertes.append("Matière de buse différente du maître.")
    if positif(brut.get("nozzle_diameter"), "diamètre de buse") != float(limites["buse_mm"]):
        alertes.append("Diamètre de buse différent du maître.")
    commandes_inconnues = []
    for cle, texte in brut.items():
        if "gcode" not in cle or not isinstance(texte, str):
            continue
        for numero, ligne in enumerate(texte.replace("\\n", "\n").splitlines(), 1):
            mots = ligne.split(";", 1)[0].strip().split()
            if mots and mots[0] == "C":
                commandes_inconnues.append(
                    {"parametre": cle, "ligne": numero, "opcode": "C", "statut": "NON PRIS EN CHARGE"}
                )
    if commandes_inconnues:
        alertes.append("Commande C inconnue ; aucune conversion automatique.")
    modes = {}
    for cle, valeur in brut.items():
        if not cle.startswith("machine_max_"):
            continue
        valeurs = valeur if isinstance(valeur, list) else str(valeur).split(",")
        zero_permis = "jerk" in cle or "junction" in cle
        nombres = [positif(item, cle, nul=zero_permis) for item in valeurs]
        modes[cle] = nombres
    if not str(brut.get("change_filament_gcode", "")):
        alertes.append("G-code de changement filament vide ; compatibilité ACE non établie.")
    statut = "FAIL" if commandes_inconnues or alertes else "NON VÉRIFIÉ"
    return {
        "schema": 1,
        "source": {"fichier": str(fichier), "sha256": sha(fichier)},
        "nom": brut.get("name"),
        "valeurs_modes": modes,
        "mode_choisi": None,
        "alertes": alertes,
        "commandes_non_couvertes": commandes_inconnues,
        "statut": statut,
        "adoption_automatique": False,
        "maitre_id": profil.get("profil_id", profil.get("id")),
        "limites_maitre_reference": limites,
        "limite": "tableaux conservés ; aucune capacité physique déduite de valeurs de profil",
    }


def proposer_strategies(st: dict, selection=("adaptivecubic", "combinaison"), usage="non_declare") -> list[dict]:
    if (
        not isinstance(selection, (tuple, list))
        or not 1 <= len(selection) <= 3
        or len(set(selection)) != len(selection)
        or set(selection) - set(STRATEGIES)
    ):
        raise ValueError("Choisir une à trois stratégies distinctes connues.")
    if usage not in ("decoratif", "fonctionnel", "non_declare"):
        raise ValueError("Usage de pièce invalide.")
    couche = positif(st.get("layer_height"), "hauteur de couche")
    buse = positif(st.get("nozzle_diameter"), "diamètre de buse")
    if couche > buse:
        raise ValueError("Hauteur de couche supérieure au diamètre de buse.")
    variantes = []
    for nom in selection:
        if nom == "adaptivecubic":
            reglages = {"sparse_infill_pattern": "adaptivecubic"}
            justification = "Comparer le motif seul ; densité, matière, parois et dessus inchangés."
        elif nom == "combinaison":
            actif = str(st.get("infill_combination", "0")) == "1"
            reglages = {"infill_combination": "0" if actif else "1"}
            justification = "Comparer combinaison activée/désactivée ; contrôler la hauteur locale émise."
        elif nom == "dessus":
            epaisseur = positif(st.get("top_shell_thickness", 0), "épaisseur supérieure", nul=True)
            couches = positif(st.get("top_shell_layers", 0), "couches supérieures", nul=True)
            reglages = {
                "top_shell_thickness": str(max(epaisseur, 0.72, 6 * couche)),
                "top_shell_layers": str(max(int(couches), 6)),
            }
            justification = "Augmenter la couverture des dessus ; aucun amincissement proposé."
        else:
            if str(st.get("enable_support", "0")) != "1":
                raise ValueError("Stratégie supports impossible sans supports activés dans la référence.")
            distance = positif(st.get("support_object_xy_distance"), "distance XY support", nul=True)
            reglages = {"support_object_xy_distance": str(distance + 0.1)}
            justification = "Éloigner légèrement le support ; revoir les surplombs et garder les interfaces Z."
        variantes.append(
            {
                "nom": nom,
                "reglages": reglages,
                "acceptable": False,
                "justification": justification + " Expérimental ; acceptation mécanique et finition non établies.",
            }
        )
    return variantes


def preparer_etude(
    base: Base,
    base_3mf: str | Path,
    modele: str | Path,
    sortie: str | Path,
    selection=("adaptivecubic", "combinaison"),
    usage="non_declare",
    maitre=None,
) -> dict:
    source = _fichier_prive(base, base_3mf)
    fichier_modele = _fichier_prive(base, modele)
    if fichier_modele.suffix.lower() != ".stl":
        raise ValueError("Un STL avec unité millimètre explicite est requis.")
    sortie = _dossier_sortie(base, sortie)
    configuration = {"machine": {}}
    profil, limites = _maitre(base, configuration, maitre)
    reglages, _xml, _geometrie = lire_3mf(source)
    if reglages.get("printer_model") not in ("Anycubic Kobra S1", "Anycubic Kobra S1 Combo"):
        raise ValueError("Une référence Kobra S1 est requise.")
    buse = positif(reglages.get("nozzle_diameter"), "diamètre buse")
    if reglages.get("nozzle_type") != limites.get("buse_matiere") or buse != float(limites["buse_mm"]):
        raise ValueError("Configuration de buse incompatible avec le profil maître.")
    import trimesh

    maillage = trimesh.load_mesh(fichier_modele)
    if not isinstance(maillage, trimesh.Trimesh) or maillage.is_empty:
        raise ValueError("Maillage STL vide ou invalide.")
    if not all(math.isfinite(float(valeur)) for valeur in maillage.vertices.flat):
        raise ValueError("Coordonnées du STL non finies.")
    volume = limites.get("volume_mm")
    if (
        not isinstance(volume, list)
        or len(volume) != 3
        or any(
            float(extent) <= 0 or float(extent) > float(volume[index]) for index, extent in enumerate(maillage.extents)
        )
    ):
        raise ValueError("Dimensions STL hors du volume de profil maître.")
    variantes = proposer_strategies(reglages, selection, usage)
    empreintes = {source: sha(source), fichier_modele: sha(fichier_modele)}
    sortie.mkdir(parents=True, exist_ok=False)
    copie_3mf, copie_modele = sortie / "reference.3mf", sortie / "modele.stl"
    shutil.copy2(source, copie_3mf)
    shutil.copy2(fichier_modele, copie_modele)
    if any(
        sha(copie) != empreintes[original] for original, copie in ((source, copie_3mf), (fichier_modele, copie_modele))
    ):
        raise ValueError("Source modifiée pendant la copie ; étude refusée.")
    configuration_etude = {
        "base_3mf": "reference.3mf",
        "sortie": "comparaison",
        "pieces": [
            {
                "nom": "Piece",
                "stl": "modele.stl",
                "extruder": 1,
                "usage": usage,
                "reference": {},
                "variantes": variantes,
            }
        ],
    }
    _publier_json(sortie / "etude.json", configuration_etude)
    manifeste = {
        "schema": 1,
        "statut": "EXPERIMENTAL",
        "unites": "mm",
        "sources": {
            chemin.relative_to(base.prive.resolve()).as_posix(): empreinte for chemin, empreinte in empreintes.items()
        },
        "contexte": contexte_etude(base, profil),
        "copie_3mf_sha256": sha(copie_3mf),
        "modele_sha256": sha(copie_modele),
        "etude_sha256": sha(sortie / "etude.json"),
        "strategie": list(selection),
        "usage": usage,
        "invariants": [
            "même modèle et orientation",
            "même matière et température",
            "même couche",
            "seuls réglages déclarés changent",
        ],
        "limite": "Comparaison numérique ; aucune résistance ni finition physique établie.",
    }
    _publier_json(sortie / "MANIFESTE.json", manifeste)
    return {"etude": str(sortie / "etude.json"), "manifeste": manifeste}


def executer_etude(base: Base, etude: str | Path, maitre=None) -> dict:
    """Exécute executer_etude avec les entrées déclarées et renvoie le résultat du contrôle. Réglages
    en unités natives (mm, s, °C, %) lus via le dictionnaire typé ; aucun réglage inconnu
    transformé."""
    fichier = _fichier_prive(base, etude)
    dossier = fichier.parent
    manifeste_path = dossier / "MANIFESTE.json"
    donnees, manifeste = _json_objet(fichier), _json_objet(manifeste_path)
    if fichier.name != "etude.json" or manifeste.get("contexte") != contexte_etude(base, maitre):
        raise ValueError("Étude périmée ou chemin d’étude invalide.")
    if sha(fichier) != manifeste.get("etude_sha256"):
        raise ValueError("Fiche d’étude altérée.")
    for nom, champ in (("reference.3mf", "copie_3mf_sha256"), ("modele.stl", "modele_sha256")):
        cible = dossier / nom
        if cible.is_symlink() or not cible.is_file() or sha(cible) != manifeste.get(champ):
            raise ValueError("Preuve d’étude absente ou altérée : " + nom)
    sortie_rel = donnees.get("sortie")
    if not isinstance(sortie_rel, str) or Path(sortie_rel).is_absolute() or ".." in Path(sortie_rel).parts:
        raise ValueError("Chemin de sortie forgé ou hors de l’étude.")
    sortie = dossier / sortie_rel
    if sortie.exists() or sortie.is_symlink():
        raise ValueError("Sortie d’étude déjà utilisée.")
    # Le SHA du manifeste ne rend pas les membres ambigus ni les réglages illimités valides (#122).
    with Archive3MF(dossier / "reference.3mf") as archive:
        reglages = archive.reglages()
    pieces = donnees.get("pieces")
    if not isinstance(pieces, list) or len(pieces) != 1:
        raise ValueError("L’étude doit contenir une pièce unique.")
    piece = pieces[0]
    if piece.get("nom") != "Piece" or piece.get("reference") != {} or piece.get("extruder") != 1:
        raise ValueError("Déclaration de pièce altérée.")
    variantes = proposer_strategies(reglages, manifeste.get("strategie"), piece.get("usage"))
    if piece.get("variantes") != variantes:
        raise ValueError("Les stratégies de l’étude ne correspondent plus au manifeste.")
    journal = dossier / "comparateur.log"
    if journal.exists() or journal.is_symlink():
        raise ValueError("Journal de comparateur déjà présent ou symbolique.")
    from benchab.fabrication.comparer_remplissages import main as comparer

    try:
        resultat = comparer(fichier, prive=base.prive)
    except BaseException as erreur:
        with journal.open("x", encoding="utf-8") as flux:
            flux.write(f"Comparateur interrompu : {type(erreur).__name__}: {erreur}\n")
        raise
    with journal.open("x", encoding="utf-8") as flux:
        flux.write(json.dumps(resultat, ensure_ascii=False, allow_nan=False) + "\n")
    return {
        "statut": "WARN",
        "validation_physique": "NON VÉRIFIÉ",
        "resultat": resultat,
        "rapport": str(sortie),
        "adoption_automatique": False,
    }


def main(argv: list[str] | None = None, *, base: Base | None = None) -> int:
    """Compare les procédés privés (mm, s, g) ; statuts conservés, aucune commande machine ni base créée."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prive", type=Path)
    commandes = parser.add_subparsers(dest="commande", required=True)
    audit = commandes.add_parser("auditer-profil")
    audit.add_argument("profil", type=Path)
    audit.add_argument("--parents", type=Path, required=True)
    audit.add_argument("--atelier", type=Path)
    machine = commandes.add_parser("auditer-machine")
    machine.add_argument("profil", type=Path)
    preparer = commandes.add_parser("preparer")
    preparer.add_argument("--base-3mf", type=Path, required=True)
    preparer.add_argument("--modele", type=Path, required=True)
    preparer.add_argument("--sortie", type=Path, required=True)
    preparer.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=["adaptivecubic", "combinaison"])
    preparer.add_argument("--usage", choices=("decoratif", "fonctionnel", "non_declare"), default="non_declare")
    preparer.add_argument("--trancher", action="store_true")
    execution = commandes.add_parser("executer")
    execution.add_argument("etude", type=Path)
    args = parser.parse_args(argv)
    instance = base
    fermer = False
    if instance is None:
        if args.prive is None:
            parser.error("--prive requis hors appel depuis le CLI BENCHAB.")
        instance = Base(args.prive, creer=False)
        fermer = True
    try:
        if args.commande == "auditer-profil":
            resultat = auditer_profil(
                instance,
                args.profil,
                _json_objet(_fichier_prive(instance, args.parents)),
                _json_objet(_fichier_prive(instance, args.atelier)) if args.atelier else None,
            )
        elif args.commande == "auditer-machine":
            resultat = auditer_machine(instance, args.profil)
        elif args.commande == "preparer":
            resultat = preparer_etude(instance, args.base_3mf, args.modele, args.sortie, args.strategies, args.usage)
            if args.trancher:
                resultat["comparaison"] = executer_etude(instance, resultat["etude"])
        else:
            resultat = executer_etude(instance, args.etude)
        print(json.dumps(resultat, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, zipfile.BadZipFile) as erreur:
        parser.error(str(erreur))
    finally:
        if fermer:
            instance.fermer()
    return 2
