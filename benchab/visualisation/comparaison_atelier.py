"""Compare au trancheur local le 3MF d’origine et jusqu’à deux variantes."""

import json
import shutil
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile

from benchab import reglages
from benchab.archives_3mf import Archive3MF
from benchab.controles import bilan_depots, debit_gcode
from benchab.fabrication import anycubic, optimisation
from benchab.fabrication.trancher import trancher_fichier
from benchab.gcode.debit import verdict
from benchab.gcode.lecture import plaques_gcode_archive
from benchab.machine.configuration import charger
from benchab.qualite.empreintes import sha256_fichier

_CLES = {"Paroi intérieure": "inner_wall_speed", "Remplissage": "sparse_infill_speed"}
INTENTIONS = ("resistance", "silencieux", "remplissage", "finition", "rapide", "supports", "supports-plateau",
              "haut-debit")
REGLAGES_SUPPORTS = frozenset({"enable_support", "support_type", "support_on_build_plate_only"})
MODES = ("reference", "vitesses", "qualite-supports", "orientation", "couches-variables", "diagnostic-debit",
         *INTENTIONS)


def variante_intention(params, surcharges, mode, *, profil_debit=None):
    """Propose des écarts bornés en mm/s, mm, couches ou %, sans modifier le profil source."""
    def lire(cle):
        """Lit un réglage natif typé et laisse les valeurs absentes indéterminées."""
        try:
            return reglages.valeur_brute(params, cle)
        except ValueError:
            return None

    if surcharges or lire("spiral_mode") != "0":
        return []
    if mode == "supports":
        return _variantes_supports(lire)
    if mode == "supports-plateau":
        # Un support peut partir de la pièce et rester coincé dans un creux (#319).
        if (lire("enable_support") == "1" and lire("support_on_build_plate_only") == "0"
                and lire("support_type") in ("tree(auto)", "normal(auto)")):
            return [("Supports depuis le plateau uniquement", {"support_on_build_plate_only": "1"})]
        return []
    if mode == "haut-debit":
        if not profil_debit:
            return []
        def profil(cle):
            try:
                return reglages.valeur_brute(profil_debit, cle)
            except ValueError:
                return None

        plafonds = lire("filament_max_volumetric_speed")
        if not isinstance(plafonds, list) or len(plafonds) != 1:
            return []
        ancien = debit_gcode.positive(plafonds[0])
        cle = "filament_max_volumetric_speed"
        plafond = profil(cle)
        if not isinstance(plafond, list) or len(plafond) != 1:
            return []
        nouveau = debit_gcode.positive(plafond[0])
        if ancien is None or nouveau is None or nouveau <= ancien or any(
            lire(cle + suffixe) is not None for suffixe in ("_BRASS", "_HS")
        ):
            return []
        # Comparer le même filament et la même extrusion. Un profil d’une autre matière
        # ne constitue ni un plafond ni une autorisation de changer les températures.
        cles = ("filament_settings_id", "filament_flow_ratio", "nozzle_temperature",
                "nozzle_temperature_initial_layer")
        valeurs = {k: lire(k) for k in cles}
        if any(not isinstance(v, list) or len(v) != 1 or v != profil(k) for k, v in valeurs.items()):
            return []
        suffixe = {"brass": "_BRASS", "hardened_steel": "_HS"}.get(lire("nozzle_type"))
        if suffixe and any(
            (specifique := lire(k + suffixe)) is not None and specifique != valeurs[k] for k in cles[2:]
        ):
            return []
        if lire("printer_settings_id") not in reglages.imprimantes_compatibles(profil_debit):
            return []
        return [("Plafond du profil haut débit déclaré", {cle: plafond})]
    if mode == "rapide":
        from benchab.visualisation.atelier_preparation import _vitesse

        # U21 : partir du profil embarqué et de ses limites XY, sans hausser le débit filament.
        axes = [lire(f"machine_max_speed_{axe}") for axe in "xy"]
        if any(not isinstance(a, list) or not a for a in axes):
            return []
        limites = [debit_gcode.positive(v) for a in axes for v in a]
        lignes, _ = _vitesse(params, surcharges)
        if not lignes or any(v is None for v in limites):
            return []
        changements = {}
        acceleration = False
        for ligne in lignes:
            if "borne_mm_s" not in ligne:
                continue
            cible = int(min(*limites, ligne["borne_mm_s"]) * 100) / 100
            if cible <= 0:
                return []
            if cible != ligne["avant_mm_s"]:
                changements[_CLES[ligne["titre"]]] = f"{cible:g}"
                acceleration |= cible > ligne["avant_mm_s"]
        return [("Vitesse intérieure bornée par le profil et le débit déclaré", changements)] if acceleration else []
    if mode == "remplissage":
        densite = lire("sparse_infill_density")
        if isinstance(densite, str) and densite.endswith("%"):
            valeur = debit_gcode.positive(densite[:-1])
            if valeur is not None and 5 <= valeur <= 40:
                return [("Densité de remplissage accrue", {"sparse_infill_density": f"{valeur + 5:g}%"})]
    if mode == "finition":
        from benchab.fabrication.strategies_procede import proposer_strategies

        couche = debit_gcode.positive(lire("layer_height"))
        buses = lire("nozzle_diameter")
        if couche and isinstance(buses, list) and len(buses) == 1 and debit_gcode.positive(buses[0]):
            epaisseur = lire("top_shell_thickness")
            couches = lire("top_shell_layers")
            if epaisseur is not None and couches is not None:
                try:
                    proposition, = proposer_strategies({
                        "layer_height": couche, "nozzle_diameter": buses[0],
                        "top_shell_thickness": epaisseur, "top_shell_layers": couches,
                    }, selection=("dessus",))
                except ValueError:
                    return []
                changements = {
                    k: (f"{float(v):.8g}" if k == "top_shell_thickness" else v)
                    for k, v in proposition["reglages"].items()
                    if float(v) != float(lire(k))
                }
                if changements:
                    return [("Couverture des dessus renforcée", changements)]
    if mode == "resistance":
        variantes = []
        parois = lire("wall_loops")
        if isinstance(parois, str) and parois in {"1", "2", "3", "4", "5"}:
            variantes.append(("Une paroi supplémentaire", {"wall_loops": str(int(parois) + 1)}))
        coques = lire("top_shell_layers")
        if isinstance(coques, str) and coques in {"1", "2", "3", "4", "5", "6", "7"}:
            variantes.append(("Une couche supérieure pleine en plus", {"top_shell_layers": str(int(coques) + 1)}))
        return variantes
    if mode == "silencieux":
        vitesses = {}
        for cle in ("outer_wall_speed", "inner_wall_speed", "sparse_infill_speed"):
            vitesse = debit_gcode.positive(lire(cle))
            if vitesse and 1 <= vitesse <= 1000:
                vitesses[cle] = f"{vitesse * 0.6:.2f}".rstrip("0").rstrip(".")
        if len(vitesses) == 3:
            return [("Vitesses réduites de 40 % sur trois trajets", vitesses)]
    return []


def variantes_procede(params, surcharges):
    """Propose des couches fixes plus fines et des supports automatiques, en mm et valeurs natives."""
    def lire(cle):
        """Retourne la valeur native typée d’une clé, si elle est établie."""
        try:
            return reglages.valeur_brute(params, cle)
        except ValueError:
            return None

    if surcharges or lire("spiral_mode") != "0":
        return []
    variantes = []
    couche = debit_gcode.positive(lire("layer_height"))
    buses = lire("nozzle_diameter")
    adaptive = lire("adaptive_layer_height")
    if couche and isinstance(buses, list) and len(buses) == 1 and debit_gcode.positive(buses[0]) == 0.4 and (
        "adaptive_layer_height" not in params or adaptive == "0"
    ):
        nouvelle = next((h for h in (0.20, 0.16, 0.12) if h < couche), None)
        if nouvelle is not None:
            variantes.append(("Couches plus fines", {"layer_height": str(nouvelle)}))
    return (variantes + _variantes_supports(lire))[:2]


def _variantes_supports(lire):
    supports, type_support = lire("enable_support"), lire("support_type")
    if supports not in ("0", "1") or type_support not in ("normal(auto)", "tree(auto)"):
        return []
    configurations = []
    cible = "tree(auto)" if type_support == "normal(auto)" else "normal(auto)"
    changements = {"support_type": cible}
    if supports == "0":
        configurations.append(("Supports automatiques (type de la référence)", {"enable_support": "1"}))
        changements["enable_support"] = "1"
    configurations.append(("Supports arborescents" if cible.startswith("tree") else "Supports classiques", changements))
    if supports == "1":
        configurations.append(("Sans support", {"enable_support": "0"}))
    return configurations


def variante_locale(prive, sha, params):
    """Lit des plafonds en mm/s rattachés à l’empreinte exacte du 3MF ; aucun profil modifié."""
    if prive is None:
        return None
    chemin = Path(prive) / "config" / "variantes-comparateur.json"
    if not chemin.is_file():
        return None
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    if not isinstance(donnees, dict):
        raise ValueError("Configuration des variantes locales invalide.")
    variante = donnees.get(sha)
    if variante is None:
        return None
    if not isinstance(variante, dict) or not variante or set(variante) - set(
        (*optimisation.INTERIEURS, *optimisation.EXTERIEURS)
    ):
        raise ValueError("Réglage hors des vitesses du comparateur.")
    for cle, valeur in variante.items():
        try:
            plafond = Decimal(valeur) if isinstance(valeur, str) else Decimal("NaN")
            avant = Decimal(reglages.valeur_brute(params, cle))
            if not plafond.is_finite() or not avant.is_finite() or not 0 < plafond < avant:
                raise ValueError("Plafond local invalide pour " + cle)
        except (InvalidOperation, TypeError) as erreur:
            raise ValueError("Vitesse non numérique pour " + cle) from erreur
    return variante


def _mesures(archive):
    """Agrège les estimations par plaque en secondes et grammes, avec verdict U25 issu du G-code."""
    plaques = anycubic.resultats(archive)
    with Archive3MF(archive) as fichier:
        resume = fichier.xml("Metadata/slice_info.config")
        masse_filaments = []
        for plaque in resume.findall("plate"):
            index = int(plaque.find("metadata[@key='index']").get("value"))
            filaments = []
            for filament in plaque.findall("filament"):
                try:
                    masse = float(filament.get("used_g", ""))
                except ValueError:
                    continue
                if masse == 0 or debit_gcode.positive(masse) is not None:
                    filaments.append({"filament": filament.get("id"), "grammes": masse})
            masse_filaments.append({"plaque": index, "filaments": filaments})
    controle = debit_gcode.analyser(archive)
    debit_plaques = []
    for plaque in controle.get("plaques", []):
        outils = [outil for objet in plaque["objets"] for outil in objet["outils"]]
        pics = [outil["debit_max"] for outil in outils if outil["debit_max"] is not None]
        debit_plaques.append({
            "plaque": plaque["plaque"],
            "pic_mm3_s": max(pics, default=None),
            "depassement_etabli": any(
                verdict(outil["debit_max"], outil["plafond_mm3_s"]) == "FAIL" for outil in outils
            ),
            "plafonds_mm3_s": sorted({
                outil["plafond_mm3_s"] for outil in outils if outil["plafond_mm3_s"] is not None
            }),
        })
    mesures = {
        "plaques": sorted(p["index"] for p in plaques),
        "estimations_plaques": [
            {"plaque": p["index"], "secondes": p["secondes"], "grammes": p["grammes"]}
            for p in sorted(plaques, key=lambda p: p["index"])
        ],
        "secondes": sum(p["secondes"] for p in plaques),
        "grammes": sum(p["grammes"] for p in plaques),
        "debit": controle["statut"],
        "debit_plaques": debit_plaques,
        "estimation_par_filament": masse_filaments,
        "plaques_avec_supports": [
            p["plaque"] for p in controle.get("plaques", []) if p["segments_depot_support"]
        ],
        "trajets_supports_mm": round(sum(p["trajets_supports_mm"] for p in controle.get("plaques", [])), 3),
        "supports_verifies": bool(controle.get("plaques")) and all(
            p["lecture_depots_supports_complete"] for p in controle["plaques"]
        ),
    }
    try:
        bilan = bilan_depots.analyser(archive)
    except (ValueError, RuntimeError) as erreur:
        mesures["depots_par_filament"] = {"statut": "NON VÉRIFIÉ", "raison": str(erreur), "lignes": []}
    else:
        masses = defaultdict(list)
        for groupe in bilan["groupes"]:
            masses[groupe["plaque"], groupe["filament"]].append(groupe["matiere_g"])
        mesures["depots_par_filament"] = {
            "statut": bilan["statut"],
            "lignes": [
                {"plaque": plaque, "filament": filament,
                 "grammes": round(sum(valeurs), 6) if all(v is not None for v in valeurs) else None}
                for (plaque, filament), valeurs in sorted(masses.items(), key=lambda ligne: (
                    ligne[0][0], ligne[0][1] or 0,
                ))
            ],
        }
    return mesures


def bilan_tranchage_existant(source, sha_attendu):
    """Relit les estimations (s, g) et le débit (mm³/s) du G-code déjà présent."""
    mesures = _mesures(source)
    if sha256_fichier(source) != sha_attendu:
        raise ValueError("Le 3MF a changé pendant la lecture de son G-code.")
    return {"source_sha256": sha_attendu, "reference": mesures, "variantes": [],
            "tranchage_existant": True}


def _empreintes_commandes(tranche):
    """Hache les commandes par plaque, hors commentaires et espaces en bord de ligne."""
    with Archive3MF(tranche) as archive:
        resultats = {}
        for plaque, membre in plaques_gcode_archive(archive):
            empreinte = sha256()
            with archive.ouvrir_flux(membre) as flux:
                for ligne in flux:
                    commande = ligne.split(b";", 1)[0].strip()
                    if commande:
                        empreinte.update(commande + b"\n")
            resultats[plaque] = empreinte.hexdigest()
        return resultats


def _ecarts(reference, variante):
    """Soustrait la référence en s et g ou refuse un ensemble de plaques différent."""
    if reference["plaques"] != variante["plaques"]:
        raise ValueError("Plaques différentes après tranchage : comparaison refusée.")
    return {
        "secondes": round(variante["secondes"] - reference["secondes"], 3),
        "grammes": round(variante["grammes"] - reference["grammes"], 3),
    }


def _appuis(tranche, plaque, *, tracer=True):
    """Résume les appuis nominaux des petites plaques en mm, avec couverture explicite."""
    from benchab.controles import appuis_gcode

    with Archive3MF(tranche) as archive:
        membre = dict(plaques_gcode_archive(archive)).get(plaque)
        if membre is None:
            return {"statut": "NON VÉRIFIÉ", "raison": "Plaque absente."}
        if archive.taille_membre(membre) > 8 * 1024 * 1024:
            return {"statut": "NON VÉRIFIÉ", "raison": "G-code décompressé hors budget d’appuis."}
        # Première plaque et outil sans décalage : repère commun avec la vue du 3MF.
        superposition = (
            tracer and plaque == 1 and "Metadata/project_settings.config" in archive.noms()
            and reglages.extrudeur_unique_sans_decalage(archive.reglages())
        )
    try:
        rapport = appuis_gcode.analyser_fichier(
            tranche, plaques=[plaque], arcs_planaires=True, types_cibles=appuis_gcode.PIECE,
            superposition=superposition,
        )
    except (ValueError, RuntimeError) as erreur:
        return {"statut": "NON VÉRIFIÉ", "raison": str(erreur)}
    plaques = rapport["plaques"]
    return {
        "statut": rapport["statut"],
        "parcours_sans_appui": sum(p["sans_appui_nominal"] for p in plaques),
        "portee_libre_max_mm": max(
            (p["portee_libre_max_global_mm"] for p in plaques if p["portee_libre_max_global_mm"] is not None),
            default=None,
        ),
        "plaques": [plaque],
        "superposition": plaques[0].get("superposition") if len(plaques) == 1 and rapport["statut"] == "PASS" else None,
    }


def _appuis_selection(tranche, selection, *, annule=None):
    """Compare les appuis nominaux par plaque (mm) avec un budget commun de 8 Mio."""
    from benchab.visualisation.carte_risques import verifier_annulation

    if len(selection) == 1:
        return _appuis(tranche, selection[0])
    with Archive3MF(tranche) as archive:
        membres = dict(plaques_gcode_archive(archive))
        if not selection or any(plaque not in membres for plaque in selection):
            return {"statut": "NON VÉRIFIÉ", "raison": "Plaque absente."}
        if sum(archive.taille_membre(membres[plaque]) for plaque in selection) > 8 * 1024 * 1024:
            return {"statut": "NON VÉRIFIÉ", "raison": "G-code décompressé hors budget d’appuis."}
    rapports = []
    for plaque in selection:
        verifier_annulation(annule)
        rapport = _appuis(tranche, plaque, tracer=False)
        # Les coordonnées G-code restent locales à chaque plaque ; la vue 3MF les
        # dispose sur un plateau composite dont le recalage n’est pas attesté (#319).
        rapports.append({"plaque": plaque, **{k: v for k, v in rapport.items() if k != "superposition"}})
    resultat = {"plaques": selection, "par_plaque": rapports, "superposition": None}
    if any(p["statut"] != "PASS" for p in rapports):
        return {**resultat, "statut": "NON VÉRIFIÉ"}
    return {
        **resultat, "statut": "PASS",
        "parcours_sans_appui": sum(p["parcours_sans_appui"] for p in rapports),
        "portee_libre_max_mm": max(
            (p["portee_libre_max_mm"] for p in rapports if p["portee_libre_max_mm"] is not None),
            default=None,
        ),
    }


def comparer(source, sha_attendu, vitesses, *, marge=False, prive=None, variante=None, procede=None,
             dossier_exports=None, orientation=None, couches=None, profil_debit_sha=None,
             reference_seule=False, annule=None):
    """Tranche la référence exacte et les propositions admissibles, avec estimations en s et g.

    Une variante optionnelle retient 90 % du plafond estimé en mm/s.
    La variante locale reçoit ses propres plafonds. Le verdict U25 est recalculé
    à partir du G-code. Sans sortie conforme du trancheur, aucun écart n’est calculé.
    Le dossier d’export facultatif appartient à la publication locale en cours.
    Le moteur et l’original sont vérifiés par empreinte. Aucune commande machine.
    """
    from benchab.visualisation.atelier_preparation import MAX_SOURCE
    from benchab.visualisation.carte_risques import verifier_annulation

    verifier_annulation(annule)
    source = Path(source)
    if source.stat().st_size > MAX_SOURCE:
        raise ValueError("Source supérieure à 128 Mio.")
    modifications = [v for v in vitesses if "apres_mm_s" in v and v["apres_mm_s"] < v["avant_mm_s"]]
    if procede is not None and not procede:
        raise ValueError("Aucune variante de procédé n’est vérifiable pour ce 3MF.")
    if not reference_seule and procede is None and not any((modifications, variante, orientation, couches)):
        raise ValueError("Cette référence ne présente aucun plafonnement de vitesse à comparer.")
    binaire = Path(charger()["slicer"]["executable"]).resolve(strict=True)
    empreinte_moteur = sha256_fichier(binaire)
    essais = []
    plaque_appuis = orientation[1]["plaque"] if orientation is not None else None
    if orientation is not None:
        essais = [("Pose sur une autre face", {})]
    elif couches is not None:
        essais = [("Hauteurs de couche variables", {})]
    elif procede is not None:
        if len(procede) > 2:
            raise ValueError("Deux variantes de procédé au maximum.")
        essais = procede
    elif modifications:
        coefficients = [("Plafond estimé", 1.0)]
        if marge:
            coefficients.append(("90 % du plafond estimé", 0.9))
        for nom, coefficient in coefficients:
            essais.append((nom, {
                _CLES[v["titre"]]: f"{int(v['apres_mm_s'] * coefficient * 100 + 1e-8) / 100:g}" for v in modifications
            }))
    if procede is None and variante and orientation is None and couches is None:
        essais.append(("Plafonds ciblés", variante))
        # Garde la variante privée et au plus une autre proposition de vitesse.
        if len(essais) > 2:
            essais.pop(0)
    with TemporaryDirectory(prefix="benchab-comparaison-", dir="/private/tmp") as tmp:
        dossier = Path(tmp)
        original = dossier / "reference.3mf"
        with Archive3MF(source) as archive:
            if archive.sha256() != sha_attendu:
                raise ValueError("Le 3MF a changé depuis son ouverture.")
            if any(n.casefold().endswith(".gcode") for n in archive.noms()):
                raise ValueError("Choisissez le 3MF de préparation avant tranchage.")
            shutil.copyfile(source, original)
        if sha256_fichier(original) != sha_attendu:
            raise ValueError("Copie de référence différente de l’original.")
        options_annulation = {"annule": annule} if annule is not None else {}
        tranche, _ = trancher_fichier(
            original, dossier, "reference-tranchee.3mf", prive=prive, timeout_s=120, **options_annulation,
        )
        verifier_annulation(annule)
        reference = _mesures(tranche)
        commandes_reference = _empreintes_commandes(tranche)
        if couches is not None:
            from benchab.fabrication.couches_variables import ecrire_profils, hauteurs_gcode

            reference["hauteurs_gcode"] = hauteurs_gcode(tranche)
        comparaison_supports = any(REGLAGES_SUPPORTS & changements.keys()
                                   for _, changements in essais)
        selection_appuis = [plaque_appuis] if plaque_appuis is not None else (
            reference["plaques"] if comparaison_supports else []
        )
        if selection_appuis:
            reference["appuis"] = _appuis_selection(tranche, selection_appuis, annule=annule)
        variantes = []
        for i, (nom, changements) in enumerate(essais, 1):
            verifier_annulation(annule)
            variante = {"nom": nom, "reglages": changements}
            try:
                if procede is None and any(float(v) <= 0 for v in changements.values()):
                    raise ValueError("Plafond trop faible pour cette variante.")
                fichier = dossier / f"variante-{i}.3mf"
                options = {"comparaison_procede": True} if procede is not None else {}
                if profil_debit_sha is not None:
                    options["comparaison_debit"] = True
                if orientation is not None:
                    from benchab.visualisation.orientation_atelier import copier_pose

                    arguments_pose, options_pose = orientation
                    variante["pose"] = copier_pose(original, fichier, *arguments_pose, **options_pose)
                    controle_clone = {"statut": variante["pose"]["statut"], "differences": []}
                elif couches is not None:
                    from benchab import integrite_3mf

                    ecrire_profils(original, fichier, couches)
                    controle_clone = {
                        "statut": "PASS",
                        "differences": integrite_3mf.difference(
                            integrite_3mf.instantane(original), integrite_3mf.instantane(fichier),
                        ),
                    }
                else:
                    _, controle_clone = optimisation.clone(
                        original, fichier, {"reglages": {"global": changements}}, **options,
                    )
                variante["differences_3mf"] = controle_clone["differences"]
                tranche, preuve_tranchage = trancher_fichier(
                    fichier,
                    dossier,
                    f"variante-{i}-tranchee.3mf",
                    prive=prive,
                    timeout_s=120,
                    attendu=changements,
                    **options_annulation,
                )
                verifier_annulation(annule)
                mesures = _mesures(tranche)
                ecarts = _ecarts(reference, mesures)
                commandes_variante = _empreintes_commandes(tranche)
                variante["commandes_identiques"] = commandes_reference == commandes_variante
                variante["commandes_par_plaque"] = [
                    {"plaque": plaque, "commandes_identiques": commandes_variante.get(plaque) == empreinte}
                    for plaque, empreinte in sorted(commandes_reference.items())
                ]
                if mesures["debit"] == "FAIL":
                    variante["motif_export_refuse"] = "Dépassement du plafond de débit déclaré (U25)."
                if couches is not None:
                    mesures["hauteurs_gcode"] = hauteurs_gcode(tranche)
                    h = mesures["hauteurs_gcode"]
                    fixe = reference["hauteurs_gcode"]
                    mesures["hauteurs_variables_observees"] = h["distinctes"] >= 2 and (
                        h["distinctes"] > fixe["distinctes"] or h["min"] != fixe["min"] or h["max"] != fixe["max"]
                    )
                if selection_appuis:
                    mesures["appuis"] = _appuis_selection(tranche, selection_appuis, annule=annule)
                if orientation is not None:
                    variante["autres_plaques"] = [
                        bilan for bilan in variante["commandes_par_plaque"] if bilan["plaque"] != plaque_appuis
                    ]
                if dossier_exports is not None and mesures["debit"] != "FAIL":
                    nom_export = f"variante-{i}.3mf"
                    empreinte = sha256_fichier(fichier)
                    with (dossier_exports / nom_export).open("xb") as sortie, fichier.open("rb") as entree:
                        shutil.copyfileobj(entree, sortie)
                    if sha256_fichier(dossier_exports / nom_export) != empreinte:
                        raise OSError("Copie d’export différente de la variante contrôlée.")
                    variante["export"] = {
                        "fichier": nom_export, "sha256": empreinte, "statut": "PASS",
                        "integrite_3mf": controle_clone["statut"],
                        "differences_3mf": variante["differences_3mf"],
                        "plaques_relues": mesures["plaques"],
                        "reglages_relus_gcode": changements,
                        "membres_geometrie_sha256": controle_clone.get("geometrie_sha256"),
                        "sortie_moteur_sha256": preuve_tranchage.get("sortie_sha256"),
                        "integrite_sortie_moteur": preuve_tranchage.get(
                            "integrite_apres_restauration_matrices",
                            preuve_tranchage.get("integrite_sortie_moteur", {}),
                        ).get("statut"),
                    }
                variante.update(mesures=mesures, ecarts=ecarts)
            except (ValueError, RuntimeError, BadZipFile) as erreur:
                verifier_annulation(annule)
                variante["erreur"] = str(erreur)
            variantes.append(variante)
    verifier_annulation(annule)
    if sha256_fichier(source) != sha_attendu or sha256_fichier(binaire) != empreinte_moteur:
        raise ValueError("La référence ou le moteur a changé pendant la comparaison.")
    return {
        "source_sha256": sha_attendu,
        "moteur_sha256": empreinte_moteur,
        **({"profil_debit_sha256": profil_debit_sha} if profil_debit_sha is not None else {}),
        "reference": reference,
        "variantes": variantes,
    }
