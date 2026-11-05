"""Orchestre les contrôles complets dans un ordre stable et conserve un rapport privé."""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import sys
import time
import uuid
from pathlib import Path

from benchab import reglages as schema_reglages
from benchab.connaissances import memoire
from benchab.controles import reimpression
from benchab.donnees.base import Base
from benchab.fabrication.chaine import executer as executer_chaine
from benchab.projets.nouveau import creer
from benchab.qualite import coherence, edition, publication, references
from benchab.qualite.statuts import code_statut

ORDRE = ("ruff", "coherence", "references_docs_savoir_publication", "reimpression", "pytest")
STATUTS = {"PASS", "WARN", "FAIL", "NON VÉRIFIÉ", "NON APPLICABLE"}


def _sans_lien(path: Path) -> Path:
    """Valide le chemin du rapport via la garde commune ; FAIL sur lien interne, aucune écriture."""
    from benchab.chemins import sans_lien

    return sans_lien(path)


def _mesures() -> dict:
    """Capture les compteurs CPU (s) et RSS haut (KiB) du processus et de ses enfants."""
    self_usage = resource.getrusage(resource.RUSAGE_SELF)
    child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    diviseur_rss = 1024 if sys.platform == "darwin" else 1
    return {
        "cpu_s": self_usage.ru_utime + self_usage.ru_stime + child_usage.ru_utime + child_usage.ru_stime,
        "rss_max_kib_processus": int(self_usage.ru_maxrss / diviseur_rss),
        "rss_max_kib_enfants_cumule": int(child_usage.ru_maxrss / diviseur_rss),
        "charge": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
    }


def _lancer(nom: str, commande: list[str], racine: Path, journal: Path, *, delai=1800) -> dict:
    """Lance une étape locale bornée (s), CPU s/RSS KiB ; PASS/FAIL, aucun shell ni processus tiers touché."""
    from benchab.processus import executer as executer_processus

    debut = _mesures()
    # Le runner doit arrêter également les enfants de pytest en cas de délai,
    # et garder les logs entiers (docs/reference/controles.md).
    resultat = executer_processus(commande, cwd=racine, diagnostics=journal.parent / "diagnostics",
                                   delai=delai, conserver_capture=True, fusion_stderr=True)
    diagnostic = json.loads(Path(resultat.diagnostic).read_text())
    shutil.copyfile(Path(resultat.diagnostic).parent / "stdout.log", journal)
    if resultat.code_controle:
        with journal.open("a", encoding="utf-8") as flux:
            flux.write("\n" + resultat.stderr)
    fin = _mesures()
    rss = diagnostic.get("rss_groupe_pic_observe_octets")
    return {
        "statut": "PASS" if resultat.ok else "FAIL",
        "code": resultat.code_controle,
        "code_processus": resultat.returncode,
        "commande": commande,
        "duree_s": resultat.duree_secondes,
        "cpu_s": max(0.0, fin["cpu_s"] - debut["cpu_s"]),
        "rss_max_kib_etape": rss / 1024 if rss is not None else None,
        "limite_rss": diagnostic["limites_rss"],
        "rss_max_kib_processus": fin["rss_max_kib_processus"],
        "rss_max_kib_enfants_cumule": fin["rss_max_kib_enfants_cumule"],
        "charge_avant": debut["charge"],
        "charge_apres": fin["charge"],
        "journal": str(journal),
        "diagnostic_processus": resultat.diagnostic,
        "delai_secondes": delai,
    }


def _statut_rapport(rapport: dict) -> str:
    """Agrège les statuts sans transformer NON VÉRIFIÉ en PASS."""
    statuts = [etape["statut"] for etape in rapport["etapes"]]
    if "FAIL" in statuts:
        return "FAIL"
    if "NON VÉRIFIÉ" in statuts:
        return "NON VÉRIFIÉ"
    if "WARN" in statuts:
        return "WARN"
    return "PASS"


def _references_docs_savoir_publication(racine: Path, prive: Path) -> dict:
    """Vérifie références, connaissances, documentation et frontière publique sans accès réseau."""
    erreurs = []
    bilan_refs = references.verify(racine, historical=False, anchors=True)
    if not bilan_refs.get("ok"):
        erreurs.extend(bilan_refs.get("errors", ["Références non vérifiées"]))
    rapport_savoir = memoire.controler(prive)
    erreurs_savoir = rapport_savoir["erreurs"]
    erreurs.extend(erreurs_savoir)

    def hors_ligne(_url):
        raise OSError("Vérification HTTP désactivée par le contrôle complet")

    # La doc publique doit être vérifiable sans réseau; les URL externes restent WARN.
    # docs/reference/controles.md distingue cette preuve documentaire des validations physiques.
    erreurs_edition = edition.verifier(racine, lecteur_url=hors_ligne, prive=prive)
    erreurs.extend(erreurs_edition)
    externes_seuls = bool(erreurs_edition) and all("lien HTTP inaccessible" in e for e in erreurs_edition)
    statut = "WARN" if externes_seuls and bilan_refs.get("ok") and not erreurs_savoir else "FAIL" if erreurs else "PASS"
    if statut != "FAIL" and rapport_savoir["statut"] == "NON VÉRIFIÉ":
        statut = "NON VÉRIFIÉ"
    return {"statut": statut, "erreurs": erreurs, "savoir": rapport_savoir, "validation_physique": False}


def _verifier_reimpressions(prive: Path) -> dict:
    """Contrôle cycle et verrou de chaque projet ; PASS/WARN/FAIL, aucun verrou absent réputé vérifié."""
    projets = prive / "projets"
    if not projets.is_dir():
        return {"statut": "NON VÉRIFIÉ", "erreurs": ["Dossiers de projets privés absents"]}
    rapports = []
    for projet in sorted(projets.iterdir()):
        if projet.is_symlink() or not projet.is_dir() or projet.name.startswith("."):
            continue
        verrou = reimpression.verifier(projet)
        # Le contrôle historique de réimpression vérifie aussi le cycle, même sans verrou déclaré
        # (docs/reference/controles.md). L'absence de verrou conserve son statut propre.
        cycle = publication.verifier(projet)
        rapports.append({"projet": projet.name, "verrou": verrou, "cycle": cycle})
    erreurs = [e for r in rapports for c in (r["verrou"], r["cycle"]) for e in c.get("erreurs", [])]
    statuts = {c["statut"] for r in rapports for c in (r["verrou"], r["cycle"])}
    statut = (
        "FAIL" if erreurs or "FAIL" in statuts else "NON VÉRIFIÉ"
        if not rapports or "NON VÉRIFIÉ" in statuts else "WARN" if "WARN" in statuts else "PASS"
    )
    return {"statut": statut, "projets": rapports, "erreurs": erreurs, "validation_physique": False}


def _valeur_filament(reglages, cle: str, index: int):
    definition = schema_reglages.definition(cle, valeur=reglages.get(cle))
    if definition["type"] != "liste_filament":
        raise ValueError(f"Profil NON VÉRIFIÉ : {cle} n'est pas typé liste_filament")
    valeurs = schema_reglages.valeur_brute(reglages, cle)
    if not isinstance(valeurs, list) or index >= len(valeurs):
        raise ValueError(f"Index filament absent dans {cle}")
    return valeurs[index]


def _nombre_reglage(valeur, cle: str):
    """Convertit le texte numérique embarqué en nombre de même valeur, sans unité ni réglage modifié."""
    if isinstance(valeur, bool):
        raise ValueError(f"Valeur numérique invalide dans le profil explicite : {cle}")
    if isinstance(valeur, (int, float)):
        return valeur
    try:
        nombre = float(valeur)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Valeur numérique NON VÉRIFIÉE dans le profil explicite : {cle}") from exc
    return int(nombre) if nombre.is_integer() else nombre


def _temoin_niveau(
    niveau: str,
    prive: Path,
    profil: Path,
    index_filament: int,
    declaration: Path,
    sortie: Path,
) -> dict:
    """Crée un témoin privé depuis le gabarit natif et l'enchaîne jusqu'à l'envoi, sans validation physique."""
    if niveau not in {"S", "M", "L"} or index_filament < 1:
        raise ValueError("Niveau S/M/L et index filament (base 1) requis")
    profil, declaration = _sans_lien(profil), _sans_lien(declaration)
    if not profil.is_file() or not declaration.is_file():
        raise ValueError("Profil canonique explicite et fixture d'envoi exigés")
    if not profil.is_relative_to(prive) or not declaration.is_relative_to(prive):
        raise ValueError("Profil et fixture doivent rester dans la racine privée explicite")
    reglages = schema_reglages.lire_3mf(profil)
    i = index_filament - 1
    identifiant = _valeur_filament(reglages, "filament_ids", i)
    if not isinstance(identifiant, str) or not identifiant or "temporaire" in identifiant.lower():
        raise ValueError("Identifiant filament permanent absent du profil explicite")
    # Le 3MF stocke souvent ces scalaires numériques comme texte; les gabarits attendent les mêmes nombres typés.
    # docs/reference/slicer-anycubic.md impose de garder les valeurs embarquées inchangées.
    values = {
        "type": _valeur_filament(reglages, "filament_type", i),
        "color": _valeur_filament(reglages, "filament_colour", i),
        "id": identifiant,
        "settings_id": _valeur_filament(reglages, "filament_settings_id", i),
        "temp": _nombre_reglage(_valeur_filament(reglages, "nozzle_temperature", i), "nozzle_temperature"),
        "temp_1re": _nombre_reglage(
            _valeur_filament(reglages, "nozzle_temperature_initial_layer", i),
            "nozzle_temperature_initial_layer",
        ),
        "plateau_c": _nombre_reglage(_valeur_filament(reglages, "textured_plate_temp", i), "textured_plate_temp"),
        "debit_max": _nombre_reglage(
            _valeur_filament(reglages, "filament_max_volumetric_speed", i), "filament_max_volumetric_speed"
        ),
    }
    fixture = json.loads(declaration.read_text(encoding="utf-8"))
    declared = fixture.get("filaments", {}).get("1")
    cles_declarees = ("type", "color", "id", "settings_id", "temp", "temp_1re", "plateau_c", "debit_max")
    if not isinstance(declared, dict) or any(declared.get(cle) != values[cle] for cle in cles_declarees):
        raise ValueError("Fixture d'envoi absente ou non concordante avec l'index filament explicite")
    sortie = _sans_lien(sortie)
    sortie.mkdir(parents=True, exist_ok=False)
    atelier = sortie / "prive"
    base = Base(atelier)
    try:
        projet = creer(atelier, f"temoin-{niveau.lower()}", niveau=niveau, base=base)
        copie_profil = projet / "profil-canonique.3mf"
        shutil.copyfile(profil, copie_profil)
        parametres = projet / "src/parametres.py"
        contenu = parametres.read_text(encoding="utf-8")
        if "FILAMENTS = {}" not in contenu or "PROFIL_REFERENCE = None" not in contenu:
            raise ValueError("Gabarit témoin différent : marqueurs de référence absents")
        contenu = contenu.replace("FILAMENTS = {}", "FILAMENTS = {'1': " + repr(values) + "}")
        contenu = contenu.replace("PROFIL_REFERENCE = None", "PROFIL_REFERENCE = " + repr(str(copie_profil)))
        if niveau in {"M", "L"}:
            contenu += "\nP.update(rayon_pion=4.0, jeu_radial=0.6)\n"
            contenu += (
                "\nPLAQUES[0]['items'].append("
                "{'piece':'Pion','copies':1,'extruder':1,'reglages':{'top_shell_layers':8}})\n"
            )
            (projet / "src/modele.py").write_text(
                '"""Témoin numérique en mm ; renvoie boîte percée et pion, aucune tenue physique validée."""\n'
                "from build123d import Box, Cylinder, Axis, fillet\n\n"
                "def pieces(p):\n"
                '    """Construit les solides en mm ; aucun réglage ni statut physique modifié."""\n'
                "    bloc = Box(p['longueur'], p['largeur'], p['epaisseur'])\n"
                "    bloc = fillet(bloc.edges().filter_by(Axis.Z), 1.0)\n"
                "    trou = Cylinder(p['rayon_pion'] + p['jeu_radial'], p['epaisseur'])\n"
                "    return {'Piece': bloc - trou, 'Pion': Cylinder(p['rayon_pion'], p['epaisseur'])}\n",
                encoding="utf-8",
            )
            interface = {
                "schema": 1,
                "interfaces": [
                    {
                        "id": "IF-1",
                        "type": "coulissement",
                        "piece_a": "Piece",
                        "piece_b": "Pion",
                        "cote_nominale": 8.0,
                        "jeu_radial": 0.6,
                        "justification": "Témoin numérique non mesuré physiquement.",
                        "mesure": {
                            "trou": {"piece": "Piece", "xy": [20, 10], "z": 2},
                            "arbre": {"piece": "Pion", "xy": [4, 4], "z": 2},
                        },
                    }
                ],
            }
            (projet / "interfaces.json").write_text(json.dumps(interface, ensure_ascii=False, indent=2) + "\n")
        parametres.write_text(contenu, encoding="utf-8")
        shutil.copyfile(declaration, projet / "DECLARATION-ENVOI.json")
        rapport = executer_chaine(projet, atelier)
        rapport["niveau"] = niveau
        rapport["validation_physique"] = False
        return rapport
    finally:
        base.fermer()


def executer(
    racine,
    *,
    prive,
    temoins=False,
    profil_canonique=None,
    index_filament=None,
    declaration_envoi=None,
    executeur=None,
):
    """Orchestre les contrôles séquentiels ; chemins privés en entrée, durées s/RSS KiB en sortie.

    Statuts : PASS, WARN, FAIL ou NON VÉRIFIÉ ; invariants : aucun profil implicite,
    aucun accès Cloud/imprimante et aucune validation physique déduite.

    `executeur` permet de remplacer une étape dans un test déterministe. Aucun
    réglage d'impression n'est modifié dans les profils ; voir docs/reference/slicer-anycubic.md.
    """
    racine, prive = _sans_lien(Path(racine)), _sans_lien(Path(prive))
    if not racine.is_dir() or not prive.is_dir():
        raise ValueError("Racine du dépôt et racine privée existantes requises")
    etapes = list(ORDRE)
    if temoins:
        if profil_canonique is None or index_filament is None or declaration_envoi is None:
            raise ValueError("Témoins S/M/L : profil privé, index filament et fixture d'envoi explicites requis")
        etapes.extend(("temoin-S", "temoin-M", "temoin-L"))
    sortie = _sans_lien(prive / "qualite/complet" / uuid.uuid4().hex)
    sortie.mkdir(parents=True, exist_ok=False)
    sortie.chmod(0o700)
    (sortie / "journaux").mkdir()
    rapport = {
        "schema": 1,
        "statut": "EN COURS",
        "validation_physique": False,
        "etapes": [],
        "rapport": str(sortie / "rapport.json"),
    }

    def action(nom, journal):
        if executeur is not None:
            return executeur(nom)
        if nom == "ruff":
            binaire = Path(sys.executable).with_name("ruff")
            return _lancer(nom, [str(binaire), "check", "."], racine, journal)
        if nom == "coherence":
            erreurs = coherence.verifier(prive)
            return {"statut": "FAIL" if erreurs else "PASS", "erreurs": erreurs, "validation_physique": False}
        if nom == "references_docs_savoir_publication":
            return _references_docs_savoir_publication(racine, prive)
        if nom == "reimpression":
            return _verifier_reimpressions(prive)
        if nom == "pytest":
            return _lancer(nom, [sys.executable, "-B", "-m", "pytest"], racine, journal)
        niveau = nom.removeprefix("temoin-")
        return _temoin_niveau(
            niveau,
            prive,
            Path(profil_canonique),
            int(index_filament),
            Path(declaration_envoi),
            sortie / "temoins" / niveau,
        )

    for nom in etapes:
        journal = sortie / "journaux" / f"{nom}.log"
        debut = _mesures()
        t0 = time.perf_counter()
        try:
            resultat = action(nom, journal)
            if not isinstance(resultat, dict) or resultat.get("statut") not in STATUTS:
                resultat = {"statut": "NON VÉRIFIÉ", "erreurs": ["Résultat d'étape absent ou statut inconnu"]}
        except Exception as exc:  # Le contrôle global conserve tous les résultats, comme check-all (contrôles.md).
            resultat = {"statut": "FAIL", "erreurs": [f"{type(exc).__name__}: {exc}"]}
            journal.write_text(json.dumps(resultat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        fin = _mesures()
        resultat = {
            **resultat,
            "etape": nom,
            "duree_s": time.perf_counter() - t0,
            "cpu_s": max(0.0, fin["cpu_s"] - debut["cpu_s"]),
            "rss_max_kib_processus": fin["rss_max_kib_processus"],
            "rss_max_kib_enfants_cumule": fin["rss_max_kib_enfants_cumule"],
            "rss_max_kib_etape": resultat.get("rss_max_kib_etape"),
            "charge_avant": debut["charge"],
            "charge_apres": fin["charge"],
            "journal": str(journal),
        }
        rapport["etapes"].append(resultat)
        if not journal.exists():
            journal.write_text(json.dumps(resultat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rapport["statut"] = _statut_rapport(rapport)
        rapport["code"] = code_statut(rapport["statut"])
        Path(rapport["rapport"]).write_text(json.dumps(rapport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rapport["statut"] = _statut_rapport(rapport)
    return rapport


def main(argv=None):
    """Point d'entrée de contrôle ; chemins privés explicites, durées s/RSS KiB et codes PASS/WARN/FAIL/NON VÉRIFIÉ."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--racine", type=Path, default=Path.cwd())
    parser.add_argument("--prive", type=Path, required=True)
    parser.add_argument("--temoins", action="store_true")
    parser.add_argument("--profil-canonique", type=Path)
    parser.add_argument("--index-filament", type=int)
    parser.add_argument("--declaration-envoi", type=Path)
    args = parser.parse_args(argv)
    rapport = executer(
        args.racine,
        prive=args.prive,
        temoins=args.temoins,
        profil_canonique=args.profil_canonique,
        index_filament=args.index_filament,
        declaration_envoi=args.declaration_envoi,
    )
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return rapport["code"]


if __name__ == "__main__":
    raise SystemExit(main())
