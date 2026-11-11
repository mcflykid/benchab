"""Cohérence du registre natif et de son export ; lecture seule, aucun contournement."""

import hashlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path

from benchab.donnees.base import (
    APPLICATION_ID,
    TABLES,
    _signature_export,
)
from benchab.qualite.ecritures import _sans_lien


def _verifier_vues(root, connexion):
    """Compare les vues attendues depuis SQLite au manifeste/fichiers ; lecture seule, erreurs FAIL uniquement."""
    from benchab.donnees import vues

    rendu = vues._rendre(connexion)
    attendu = vues._manifeste(rendu)
    manifeste_path = _sans_lien(root / "vues/manifest.json")
    vues_path = _sans_lien(root / "vues")
    readme_path = _sans_lien(root / "README.md")
    marqueurs_readme = False
    try:
        if readme_path.is_file():
            texte_readme = readme_path.read_text(encoding="utf-8")
            marqueurs_readme = vues.DEBUT in texte_readme or vues.FIN in texte_readme
        # Sa présence déclare la génération, même si les fichiers ont disparu
        # (docs/reference/donnees.md) ; Base ne crée pas ce dossier.
        deja_genere = (
            marqueurs_readme
            or vues_path.exists()
            or any(_sans_lien(root / chemin).exists() for chemin in rendu["fichiers"])
        )
        if not manifeste_path.is_file():
            return ["Manifeste des vues absent après génération"] if deja_genere else []
        manifeste = json.loads(manifeste_path.read_text(encoding="utf-8"))
        if manifeste != attendu:
            return ["Manifeste des vues divergent de SQLite"]
        erreurs = []
        for chemin, sha_attendu in attendu["fichiers"].items():
            fichier = _sans_lien(root / chemin)
            if not fichier.is_file():
                erreurs.append("Vue générée absente : " + chemin)
                continue
            with fichier.open("rb") as flux:
                sha_reel = hashlib.file_digest(flux, "sha256").hexdigest()
            if sha_reel != sha_attendu:
                erreurs.append("Vue générée périmée ou altérée : " + chemin)
        etats = _sans_lien(root / "vues/etats")
        if etats.is_dir():
            # Les fichiers d'état orphelins ne sont pas rendus depuis SQLite (docs/reference/donnees.md).
            chemins_attendus = set(attendu["fichiers"])
            for fichier in etats.rglob("*.md"):
                chemin = _sans_lien(fichier).relative_to(root).as_posix()
                if chemin not in chemins_attendus:
                    erreurs.append("Vue générée inattendue ou périmée : " + chemin)
        if not readme_path.is_file():
            erreurs.append("Section générée absente : README.md")
        else:
            texte = readme_path.read_text(encoding="utf-8")
            if texte.count(vues.DEBUT) != 1 or texte.count(vues.FIN) != 1:
                erreurs.append("Marqueurs de section générée absents ou ambigus : README.md")
            else:
                debut = texte.index(vues.DEBUT) + len(vues.DEBUT)
                fin = texte.index(vues.FIN)
                bloc = texte[debut:fin]
                section_attendue = "\n" + rendu["sections"]["README.md"] + "\n"
                if bloc != section_attendue:
                    erreurs.append("Section générée périmée ou altérée : README.md")
        return erreurs
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as exc:
        return ["Vues non vérifiées : " + type(exc).__name__]


def _verifier_projets(root, connexion):
    """Relève les erreurs des contrats et fichiers privés ; aucune modification ni validation physique."""
    from benchab.qualite.publication import relatif
    from benchab.qualite.publication import verifier as verifier_projet

    erreurs = []
    for ligne in connexion.execute("SELECT id,nom,donnees_json FROM projets ORDER BY id"):
        donnees = json.loads(ligne["donnees_json"])
        ident = Path(ligne["id"])
        if len(ident.parts) == 2 and ident.parts[0] == "projets":
            dossier = relatif(root, ident)
        else:
            nom = ligne["nom"]
            if Path(nom).name != nom:
                erreurs.append("Chemin de projet invalide dans le registre")
                continue
            dossier = relatif(root, Path("projets") / nom)
        suivi = donnees.get("suivi", {})
        actif = (
            suivi.get("travail_en_cours", {}).get("fichiers")
            or suivi.get("references_imprimees")
            or suivi.get("references_historiques")
            or suivi.get("preparation_courante")
        )
        if dossier.is_dir():
            rapport = verifier_projet(dossier)
            erreurs.extend(f"{ligne['id']} : {message}" for message in rapport["erreurs"])
        elif actif:
            erreurs.append(f"{ligne['id']} : dossier des fichiers déclarés absent")
    return erreurs


def _verifier_empreintes_sources(root, connexion):
    """Contrôle les fichiers/SHA privés ; renvoie erreurs FAIL, sans réparer ni certifier la fidélité passée."""
    from benchab.connaissances import provenance
    from benchab.connaissances.sqlite import lire_connexion

    erreurs, manquants = [], []
    references = []
    for ligne in connexion.execute("SELECT id,donnees_json FROM filaments ORDER BY id"):
        donnees = json.loads(ligne["donnees_json"])
        for source in donnees.get("sources_profils", []):
            if not isinstance(source, dict) or not isinstance(source.get("chemin"), str):
                erreurs.append(f"{ligne['id']} : source de profil mal formée")
                continue
            references.append((ligne["id"], source["chemin"], source.get("sha256")))
        references.extend((ligne["id"], chemin, sha)
                          for chemin, sha in donnees.get("preuves_empreintes", {}).items())
    references.extend((ligne["id"], ligne["source"], ligne["empreinte"])
                      for ligne in connexion.execute("SELECT id,source,empreinte FROM profils ORDER BY id"))
    for ident, chemin, sha in references:
        try:
            relatif = Path(chemin)
            if not chemin or relatif.is_absolute() or ".." in relatif.parts:
                raise ValueError("Chemin de source hors racine privée")
            if not re.fullmatch(r"[a-f0-9]{64}", str(sha)):
                raise ValueError("Empreinte SHA-256 absente ou invalide")
            fichier = _sans_lien(root / relatif)
            if not fichier.is_file():
                # Ce préfixe est l'espace explicite d'import gelé, pas un accès au
                # profil installé d'origine (docs/reference/donnees.md).
                fichier = _sans_lien(root / "archive/impression-3d" / relatif)
            if not fichier.is_file():
                manquants.append((ident, chemin, sha))
                continue
            with fichier.open("rb") as flux:
                reel = hashlib.file_digest(flux, "sha256").hexdigest()
            if reel != sha:
                erreurs.append(f"{ident} : empreinte de source différente : {chemin}")
        except (OSError, ValueError, TypeError) as exc:
            erreurs.append(f"{ident} : source non vérifiée : {chemin} : {exc}")
    if manquants:
        conversion = provenance.controler_contrats(lire_connexion(connexion), root)
        erreurs.extend(conversion["erreurs"])
        couverts = {(chemin, sha) for chemin, _, sha in conversion["sources_converties"]}
        for ident, chemin, sha in manquants:
            # Une conversion vérifiée atteste les extraits structurés ; elle ne
            # prétend jamais vérifier l'original absent (docs/explications/preuves.md).
            if (chemin, sha) not in couverts or conversion["statut"] != "PASS":
                erreurs.append(f"{ident} : source ou preuve absente sans conversion vérifiée : {chemin}")
    return erreurs


def _table_fiche(texte):
    """Normalise l'ordre des lignes de paramètres, sans unité ; renvoie des lignes, sans modifier de fichier."""
    lignes = texte.strip("\n").splitlines()
    positions = [i for i, ligne in enumerate(lignes) if ligne.startswith("| `")]
    valeurs = sorted(lignes[i] for i in positions)
    for index, valeur in zip(positions, valeurs, strict=True):
        lignes[index] = valeur
    return lignes


def _verifier_fiches_filaments(root, connexion):
    """Compare les fiches privées à SQLite ; erreurs FAIL, sans réécrire une archive ni adopter de calibration."""
    from benchab.connaissances.bobine import reference_fiche, texte_fiche

    erreurs = []
    for ligne in connexion.execute("SELECT id,nom,donnees_json FROM filaments ORDER BY id"):
        donnees = {**json.loads(ligne["donnees_json"]), "id": ligne["id"], "nom": ligne["nom"]}
        if "fiche" not in donnees and "fiche_contrat" not in donnees:
            continue
        try:
            relatif = Path(donnees["fiche"])
            if (relatif.is_absolute() or ".." in relatif.parts or relatif.suffix != ".md"
                    or not relatif.as_posix().startswith("docs/reference/filaments/")):
                raise ValueError("chemin de fiche hors contrat")
            fiche = _sans_lien(root / relatif)
            contrat = donnees.get("fiche_contrat")
            if contrat is None and not fiche.is_file():
                fiche = _sans_lien(root / "archive/impression-3d" / relatif)
            if not fiche.is_file() or fiche.stat().st_size > 2 * 1024**2:
                raise ValueError("fiche absente ou au-delà du budget de 2 Mio")
            texte = fiche.read_text(encoding="utf-8")
            if contrat == "recherche-sources-v1":
                conforme = _table_fiche(texte) == _table_fiche(texte_fiche(donnees))
            elif contrat is None:
                debut = "<!-- AUTO:" + donnees["id"] + ":DEBUT -->"
                fin = "<!-- AUTO:" + donnees["id"] + ":FIN -->"
                if texte.count(debut) != 1 or texte.count(fin) != 1:
                    raise ValueError("bloc de référence historique absent ou dupliqué")
                if texte.index(debut) >= texte.index(fin):
                    raise ValueError("marqueurs de référence historique dans un ordre invalide")
                bloc = texte.split(debut, 1)[1].split(fin, 1)[0]
                # Les archives gardent leur ordre d'origine ; le tri de comparaison
                # ne change aucun fichier (docs/reference/donnees.md, fiches filament).
                conforme = _table_fiche(bloc) == _table_fiche(reference_fiche(donnees))
            else:
                raise ValueError("contrat de fiche NON VÉRIFIÉ")
            if not conforme:
                raise ValueError("contenu périmé ou différent des données canoniques")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as erreur:
            erreurs.append(f"{ligne['id']} : fiche filament non conforme : {erreur}")
    return erreurs


def _cles_uniques(paires):
    objet = {}
    for cle, valeur in paires:
        if cle in objet:
            raise ValueError("Clé d’export dupliquée")
        objet[cle] = valeur
    return objet


def _ouvrir_chaine_dossiers(chemin):
    """Ouvre chaque dossier du chemin sans lien et conserve les descripteurs."""
    chemin = Path(chemin)
    if not chemin.is_absolute():
        raise ValueError("Chemin privé absolu requis")
    drapeaux = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    descripteurs = [os.open(os.sep, drapeaux)]
    noms = []
    try:
        for nom in chemin.parts[1:]:
            descripteurs.append(os.open(nom, drapeaux, dir_fd=descripteurs[-1]))
            noms.append(nom)
        _verifier_chaine_dossiers(descripteurs, noms)
        return descripteurs, noms
    except BaseException:
        for descripteur in reversed(descripteurs):
            os.close(descripteur)
        raise


def _verifier_chaine_dossiers(descripteurs, noms):
    """Confirme que chaque nom de dossier désigne toujours son descripteur ouvert."""
    for parent, nom, enfant in zip(descripteurs, noms, descripteurs[1:]):
        courant = os.stat(nom, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(courant.st_mode) or _signature_export(courant) != _signature_export(os.fstat(enfant)):
            raise ValueError("Dossier remplacé pendant le contrôle")


def _ouvrir_membre_export(dossier_fd, nom):
    """Ouvre un membre régulier depuis le dossier épinglé, sans suivre de lien."""
    courant = os.stat(nom, dir_fd=dossier_fd, follow_symlinks=False)
    if stat.S_ISLNK(courant.st_mode) or not stat.S_ISREG(courant.st_mode):
        raise ValueError("Export lié ou spécial refusé")
    signature = _signature_export(courant)
    fd = os.open(nom, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dossier_fd)
    if _signature_export(os.fstat(fd)) != signature:
        os.close(fd)
        raise ValueError("Export remplacé pendant l’ouverture")
    return fd, signature


def _verifier_membre_export(dossier_fd, nom, signature):
    """Refuse une modification ou un remplacement après lecture du membre."""
    courant = os.stat(nom, dir_fd=dossier_fd, follow_symlinks=False)
    if _signature_export(courant) != signature:
        raise ValueError("Export modifié pendant le contrôle")


def _empreinte_membre_export(dossier_fd, nom):
    """Calcule un SHA en flux borné en mémoire et vérifie le membre épinglé."""
    fd, signature = _ouvrir_membre_export(dossier_fd, nom)
    hachage = hashlib.sha256()
    with os.fdopen(fd, "rb") as flux:
        for bloc in iter(lambda: flux.read(1024 * 1024), b""):
            hachage.update(bloc)
        if _signature_export(os.fstat(flux.fileno())) != signature:
            raise ValueError("Export modifié pendant la lecture")
    _verifier_membre_export(dossier_fd, nom, signature)
    return hachage.hexdigest(), signature


def _manifeste_export(dossier_fd, erreurs, signatures):
    """Lit le manifeste par le descripteur du dossier, dans la borne de 100 Ko."""
    try:
        fd, signature = _ouvrir_membre_export(dossier_fd, "manifest.json")
        with os.fdopen(fd, "rb") as flux:
            donnees = flux.read(100_001)
            if _signature_export(os.fstat(flux.fileno())) != signature:
                raise ValueError("Manifeste changé pendant la lecture")
        _verifier_membre_export(dossier_fd, "manifest.json", signature)
        signatures["manifest.json"] = signature
    except FileNotFoundError:
        erreurs.append("Manifeste d’export absent : export/manifest.json")
        return None
    except (OSError, ValueError) as exc:
        erreurs.append(f"Manifeste d’export illisible ou lié : {type(exc).__name__}")
        return None
    try:
        if len(donnees) > 100_000:
            raise ValueError("Budget manifeste dépassé")
        manifeste = json.loads(donnees, object_pairs_hook=_cles_uniques)
        if not isinstance(manifeste, dict):
            raise ValueError("Objet JSON attendu")
    except (ValueError, RecursionError):
        erreurs.append("Manifeste d’export mal formé : export/manifest.json")
        return None
    return manifeste


def _verifier_exports(root, connexion):
    """Compare chaque export au registre, même si un autre export ne peut pas être lu."""
    erreurs = []
    descripteurs = []
    export_fd = None
    try:
        descripteurs, noms = _ouvrir_chaine_dossiers(root)
        racine_fd = descripteurs[-1]
        entree = os.stat("export", dir_fd=racine_fd, follow_symlinks=False)
        if stat.S_ISLNK(entree.st_mode) or not stat.S_ISDIR(entree.st_mode):
            raise ValueError("Dossier export lié ou non régulier")
        signature_dossier = _signature_export(entree)
        export_fd = os.open(
            "export", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=racine_fd
        )
        if _signature_export(os.fstat(export_fd)) != signature_dossier:
            raise ValueError("Dossier export remplacé pendant l’ouverture")
    except FileNotFoundError:
        erreurs.append("Manifeste d’export absent : export/manifest.json")
        erreurs.extend("Export absent : " + table for table in TABLES)
        if export_fd is not None:
            os.close(export_fd)
        for descripteur in reversed(descripteurs):
            os.close(descripteur)
        return erreurs
    except (OSError, ValueError):
        if export_fd is not None:
            os.close(export_fd)
        for descripteur in reversed(descripteurs):
            os.close(descripteur)
        return ["Dossier d’export lié ou remplacé pendant le contrôle : export/"]
    try:
        return _comparer_exports_epingles(connexion, descripteurs, noms, export_fd, signature_dossier)
    finally:
        os.close(export_fd)
        for descripteur in reversed(descripteurs):
            os.close(descripteur)


def _comparer_exports_epingles(connexion, descripteurs, noms, export_fd, signature_dossier):
    """Compare les exports ouverts et revalide tous leurs noms avant de rendre le rapport."""
    erreurs = []
    signatures = {}
    manifeste = _manifeste_export(export_fd, erreurs, signatures)
    empreintes = None
    if manifeste is not None:
        if manifeste.get("tables") != list(TABLES):
            erreurs.append("Liste des tables exportées divergente")
        schema = manifeste.get("schema")
        if type(schema) is not int or schema != connexion.execute("PRAGMA user_version").fetchone()[0]:
            erreurs.append("Version du schéma exporté divergente")
        empreintes = manifeste.get("sha256")
        if (
            not isinstance(empreintes, dict) or set(empreintes) != set(TABLES)
            or any(not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha) for sha in empreintes.values())
        ):
            erreurs.append("Empreintes du manifeste d’export mal formées")
            empreintes = None
    for table in TABLES:
        nom = table + ".jsonl"
        try:
            reel, signatures[nom] = _empreinte_membre_export(export_fd, nom)
        except FileNotFoundError:
            erreurs.append("Export absent : " + table)
            continue
        except ValueError as exc:
            diagnostic = "lié refusé" if "lié ou spécial" in str(exc) else "illisible"
            erreurs.append(f"Export {diagnostic} : {table} ({type(exc).__name__})")
            continue
        except OSError as exc:
            erreurs.append(f"Export lié ou illisible : {table} ({type(exc).__name__})")
            continue
        attendu = hashlib.sha256()
        for ligne in connexion.execute(f'SELECT * FROM "{table}" ORDER BY id'):
            attendu.update(
                (json.dumps(dict(ligne), ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode()
            )
        if attendu.hexdigest() != reel or (empreintes is not None and reel != empreintes[table]):
            erreurs.append("Export non conforme au registre : " + table)
    try:
        _verifier_chaine_dossiers(descripteurs, noms)
        courant = os.stat("export", dir_fd=descripteurs[-1], follow_symlinks=False)
        if (
            _signature_export(courant) != signature_dossier
            or _signature_export(os.fstat(export_fd)) != signature_dossier
        ):
            raise ValueError("Dossier remplacé")
        for nom, signature in signatures.items():
            _verifier_membre_export(export_fd, nom, signature)
    except (OSError, ValueError):
        erreurs.append("Dossier d’export remplacé pendant le contrôle : export/")
    return erreurs


def verifier(root):
    """Contrôle le registre, ses exports et ses vues depuis le chemin privé donné.

    Sortie sans unité : [] pour PASS ou une liste de diagnostics FAIL. Un
    export absent, illisible ou divergent ne masque pas les autres écarts.
    Aucune donnée n'est régénérée et aucun original n'est modifié.
    """
    root = _sans_lien(Path(root).absolute())
    base = _sans_lien(root / "benchab.sqlite")
    if not base.is_file():
        return ["Registre natif absent : aucune réparation ou validation implicite du catalogue historique"]
    erreurs = []
    connexion = sqlite3.connect(base.as_uri() + "?mode=ro", uri=True)
    connexion.row_factory = sqlite3.Row
    try:
        connexion.execute("BEGIN")
        if connexion.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            erreurs.append("Identité SQLite étrangère")
        if connexion.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            erreurs.append("Intégrité SQLite en échec")
        if connexion.execute("PRAGMA foreign_key_check").fetchall():
            erreurs.append("Relations SQLite invalides")
        erreurs.extend(_verifier_exports(root, connexion))
        erreurs.extend(_verifier_vues(root, connexion))
        # Un registre/export cohérent ne prouve pas la présence des fichiers
        # qu'il désigne (docs/reference/donnees.md).
        erreurs.extend(_verifier_projets(root, connexion))
        erreurs.extend(_verifier_empreintes_sources(root, connexion))
        erreurs.extend(_verifier_fiches_filaments(root, connexion))
    except (OSError, ValueError, sqlite3.Error) as exc:
        erreurs.append("Cohérence non établie : " + type(exc).__name__)
    finally:
        connexion.close()
    return erreurs
