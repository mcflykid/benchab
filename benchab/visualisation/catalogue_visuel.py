"""Vue de consultation des projets ; aucun registre ni fichier de projet écrit."""

import hashlib
import html
import json
import math
import re
import sqlite3
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote
from zipfile import BadZipFile

from benchab.archives_3mf import Archive3MF
from benchab.chemins import atelier_prive, sans_lien
from benchab.gcode.lecture import plaques_gcode_archive
from benchab.qualite.empreintes import sha256_fichier

GENERATION = threading.Lock()


def racine_privee():
    """Racine de lecture locale : BENCHAB_PRIVE, puis le privé du dépôt."""
    return atelier_prive()


def _projets_registre(racine):
    """Noms canoniques en lecture seule ; None signifie fixture historique FS."""
    racine = atelier_prive(racine)
    base = Path(racine) / "benchab.sqlite"
    if not base.is_file():
        return None
    uri = base.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        return [row[0] for row in db.execute("SELECT nom FROM projets ORDER BY nom")]


def interne(projet, rel):
    """Résout un chemin de projet sans traversée ni lien, sans unité ni écriture."""
    projet = sans_lien(projet)
    p = projet / rel
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise ValueError("Chemin de projet invalide")
    return sans_lien(p, racine=projet)


def projet_courant(racine, nom):
    """Charge les métadonnées d'un projet privé; statut/document inchangés, liens refusés."""
    racine = atelier_prive(racine)
    if not re.fullmatch("[a-z0-9]+(?:-[a-z0-9]+)*", nom):
        raise ValueError("Projet invalide")
    p = Path(racine) / "projets" / nom
    if p.is_symlink():
        raise ValueError("Projet absent")
    if (Path(racine) / "benchab.sqlite").is_file():
        from benchab.qualite.publication import charger

        q, _ = charger(p)
    else:
        if not (p / "qualite.json").is_file():
            raise ValueError("Projet absent")
        q = json.loads(interne(p, "qualite.json").read_text())
    if not isinstance(q, dict) or not isinstance(q.get("suivi", {}), dict):
        raise ValueError("Métadonnées de projet malformées : objets JSON attendus")
    suivi = q.get("suivi", {})
    for key in ("titre", "statut", "preparation_courante"):
        if key in suivi and (not isinstance(suivi[key], str)):
            raise ValueError("Champ de projet textuel invalide : " + key)
    return (p, q)


def catalogue(racine):
    """Liste les projets lisibles de l'atelier; aucun fichier ni registre n'est modifié."""
    racine = atelier_prive(racine)
    out = []
    racine = Path(racine)
    noms = _projets_registre(racine)
    entrees = (racine / "projets").glob("*") if noms is None else (racine / "projets" / n for n in noms)
    for p in sorted(entrees):
        if not p.is_dir() or p.is_symlink() or (noms is None and not (p / "qualite.json").is_file()):
            continue
        try:
            p, q = projet_courant(racine, p.name)
            suivi = q.get("suivi", {})
            disponible = interne(p, q.get("manifeste", "candidats/assemblage.json")).is_file()
            from benchab.organisation.synthese_atelier import statut_clair

            out.append(
                {
                    "statut_lisible": statut_clair(suivi),
                    "nom": p.name,
                    "titre": suivi.get("titre", p.name),
                    "etat": suivi.get("statut", "NON_VERIFIE"),
                    "preparation": suivi.get("preparation_courante", "Non renseignée"),
                    "disponible": disponible,
                    "couches": interne(p, "preuves/visualisation/apercu-gcode.html").is_file()
                    or source_couches(p, q) is not None,
                }
            )
        except (ValueError, OSError, TypeError):
            out.append(
                {
                    "nom": p.name,
                    "titre": p.name,
                    "etat": "Métadonnées illisibles",
                    "preparation": "Non renseignée",
                    "disponible": False,
                }
            )
    return out


def source_couches(projet, q):
    """Retrouve le G-code validé, conserve la graphie du projet ; aucune écriture ni analyse physique."""
    from benchab.chemins import sans_lien

    racine = sans_lien(projet, strict=True)

    def declare(chemin):
        # L'appelant peut employer /var alors que le contrôle a validé /private/var ;
        # la source reste identique (docs/reference/chemins-et-atelier.md).
        return Path(projet) / chemin.relative_to(racine) if chemin.is_relative_to(racine) else chemin

    candidats = ["candidats/tranche.3mf"]
    references = q.get("suivi", {}).get("references_imprimees", [])
    if not isinstance(references, list) or any((not isinstance(ref, dict) for ref in references)):
        raise ValueError("Références de projet malformées")
    for ref in references:
        files = ref.get("fichiers", [])
        if not isinstance(files, list) or any((not isinstance(f, dict) for f in files)):
            raise ValueError("Fichiers de référence malformés")
        candidats += [f["chemin"] for f in files if f.get("chemin")]
    candidats += [str(p.relative_to(projet)) for p in (projet / "reference").rglob("*.gcode.3mf")]
    for rel in dict.fromkeys(candidats):
        from benchab.qualite.publication import chemin_projet

        f = chemin_projet(Path(projet).parent.parent, projet, rel)
        if not f.is_file():
            continue
        if f.suffix.lower() == ".gcode":
            return declare(f)
        if f.suffix.lower() != ".3mf":
            continue
        try:
            with Archive3MF(f) as archive:
                if plaques_gcode_archive(archive):
                    return declare(f)
        except (BadZipFile, ValueError):
            # Comme un ZIP illisible, un candidat ambigu est écarté ; la référence est nommée dans l'aperçu.
            continue
    return None


def couches(racine, nom, ressources_partagees=False):
    """Rend l'aperçu des couches en octets HTML ; conserve sources et preuves du projet."""
    projet, q = projet_courant(racine, nom)
    ancien = interne(projet, "preuves/visualisation/apercu-gcode.html")

    def signature(path):
        st = path.stat()
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

    if ancien.is_file():
        avant = signature(ancien)
        empreinte = sha256_fichier(ancien)
        body = ancien.read_text()
        if signature(ancien) != avant:
            raise ValueError("Aperçu modifié pendant sa lecture")
        provenance = (
            "Aperçu enregistré : "
            + str(ancien.relative_to(projet))
            + " ; SHA-256 : "
            + empreinte
            + " ; correspondance avec la préparation courante non revérifiée."
        )
    else:
        f = source_couches(projet, q)
        if f is None:
            raise ValueError("Aucun G-code ni aperçu conservé disponible")
        if f.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("G-code volumineux : utiliser le visualiseur spécialisé")
        from benchab.visualisation.apercu_gcode import page

        avant = signature(f)
        empreinte = sha256_fichier(f)
        with GENERATION, tempfile.TemporaryDirectory(prefix="couches-atelier-") as tmp:
            out = Path(tmp) / "couches.html"
            # Appliquer le budget de la consultation dans le même contexte que le rendu (#122).
            page(f, out, titre="Couches de " + nom, taille_max_archive=256 * 1024 * 1024)
            body = out.read_text()
        if signature(f) != avant:
            raise ValueError("Source G-code modifiée pendant sa lecture")
        provenance = "Source : " + str(f.relative_to(projet)) + " ; SHA-256 : " + empreinte
    bandeau = (
        (
            '<div style="position:fixed;bottom:4px;left:8px;z-index:20;max-wid'
            "th:65%;background:#14161c;color:#fff;font:12px system-ui;padding:"
            '6px">'
        )
        + html.escape(provenance)
        + "</div>"
    )
    from benchab.visualisation.hors_ligne import autonome, partagee

    body = body.replace("</body>", bandeau + "</body>")
    return (partagee(body) if ressources_partagees else autonome(body)).encode()


def accueil(racine):
    """Renvoie le HTML UTF-8 du catalogue de l’atelier en octets, sans modifier les projets."""
    lignes = []
    for p in catalogue(racine):
        action = (
            '<a href="/inspecter?projet=' + quote(p["nom"]) + '">Inspecter</a>'
            if p["disponible"]
            else "<span>Scène indisponible : aucun manifeste de géométrie</span>"
        )
        if p.get("couches"):
            action += '<br><a href="/couches?projet=' + quote(p["nom"]) + '">Consulter les couches</a>'
        lignes.append(
            '<tr><th scope="row">'
            + html.escape(p["titre"])
            + "</th><td>"
            + html.escape(p["etat"])
            + "</td><td>"
            + html.escape(p["preparation"])
            + "</td><td>"
            + action
            + "</td></tr>"
        )
    pages = []
    for f in sorted(Path(racine).glob("projets/*/preuves/visualisation/*.html")):
        rel = f.relative_to(racine).as_posix()
        pages.append(
            '<li><a href="/'
            + html.escape(rel)
            + '">'
            + html.escape(f.relative_to(racine).parts[1] + " — " + f.stem)
            + "</a></li>"
        )
    historique = "<details><summary>Visionneuses 3D enregistrées</summary><ul>" + "".join(pages) + "</ul></details>"
    return (
        (
            '<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="'
            'viewport" content="width=device-width,initial-scale=1">\n<title>At'
            "elier Impression 3D</title><style>\nbody{margin:0;background:#f3f5"
            "f8;color:#263448;font:16px/1.5 -apple-system,system-ui,sans-serif"
            "}\nmain{max-width:1100px;margin:48px auto;padding:0 24px}h1{font-s"
            "ize:32px;margin-bottom:8px}\np{max-width:75ch;color:#53647a}table{"
            "width:100%;border-collapse:collapse;background:white;text-align:l"
            "eft}\nth,td{padding:18px 16px;border-bottom:1px solid #d7dfe8}th{f"
            "ont-weight:600}a{color:#17607d}\na:focus-visible{outline:3px solid"
            " #17607d;outline-offset:4px}caption{text-align:left;margin:24px 0"
            " 10px}\n.table{overflow:auto}td span{display:block;max-width:30ch;"
            "font-size:14px}\n</style><main><h1>Atelier Impression 3D</h1><p>Ch"
            "oisissez un projet pour inspecter ses pièces. Les références rest"
            "ent intactes.\nLes états décrivent le projet ; ils ne valident pas"
            ' toutes les pièces affichées.</p><p><a href="/atelier">Ouvrir l’a'
            "telier : pièces, préparations, preuves et connaissances</a></p><d"
            'iv class="table"><table><caption>Projets du Mac</caption>\n<thead>'
            "<tr><th>Projet</th><th>État du projet</th><th>Préparation courant"
            "e</th><th>Scène actuelle</th></tr></thead><tbody>"
        )
        + "".join(lignes)
        + "</tbody></table></div>"
        + historique
        + "</main></html>"
    ).encode()


def scene(racine, nom, ressources_partagees=False, tranche=None, placement=None):
    """Renvoie la scène HTML et ses aperçus facultatifs en octets.

    La génération est verrouillée et ne lance aucune fabrication.
    """
    with GENERATION:
        return _scene(racine, nom, ressources_partagees, tranche, placement)


def _scene(racine, nom, ressources_partagees=False, tranche=None, placement=None):
    p, q = projet_courant(racine, nom)
    from benchab.qualite.publication import chemin_projet

    fichier = chemin_projet(Path(racine), p, q.get("manifeste", "candidats/assemblage.json"))
    raw = fichier.read_bytes()
    man = json.loads(raw)
    if not isinstance(man, dict):
        raise ValueError("Manifeste de géométrie : objet JSON attendu")
    models = man.get("models") or man.get("pieces", {})
    if not isinstance(models, dict):
        raise ValueError("Modèles du manifeste malformés")
    parts = man.get("parts") or [{"name": n, "model": n} for n in models]
    if not isinstance(parts, list) or any((not isinstance(part, dict) for part in parts)):
        raise ValueError("Modèles ou pièces du manifeste malformés")
    if not models or not parts:
        raise ValueError("Aucune géométrie déclarée dans ce manifeste")
    import numpy as np

    from benchab.visualisation.visionneuse import Visionneuse

    suivi = q.get("suivi", {})
    source = {
        "projet": nom,
        "version": "Candidats actuels",
        "inspection_ephemere": True,
        "etat_projet": suivi.get("statut", "NON_VERIFIE"),
        "preparation_courante": suivi.get("preparation_courante", "Non renseignée"),
        "manifeste_sha256": hashlib.sha256(raw).hexdigest(),
        "limite": "Préparation courante donnée à titre de contexte ; cette scène ne certifie pas sa correspondance.",
    }
    source["paquet_url"] = "/paquet-inspection?projet=" + quote(nom)
    v = Visionneuse(suivi.get("titre", nom), note="Inspection numérique ; aucune validation physique.", source=source)
    dossier = q.get("stl_assemblage", "candidats")
    from benchab.visualisation.anomalies_geometriques import charger_diagnostic

    diagnostics = {}
    for n in models:
        if not re.fullmatch("[A-Za-z][A-Za-z0-9_]*", n):
            raise ValueError("Identifiant de modèle invalide")
        fichier_modele = interne(p, str(Path(dossier) / (n + ".stl")))
        mesh, diagnostics[n] = charger_diagnostic(fichier_modele, n, str(fichier_modele.relative_to(p)))
        couleur = models[n].get("couleur") if isinstance(models[n], dict) else None
        if couleur is not None and (not re.fullmatch("#[0-9a-fA-F]{6}", str(couleur))):
            raise ValueError("Couleur de modèle hexadécimale requise")
        v.modele(n, mesh.copy().process(validate=False), **{"couleur": couleur} if couleur else {})
    independantes = len(parts) > 1 and (
        not any((p.get("matrix") is not None or p.get("matrice") is not None for p in parts))
    )
    if independantes:
        source["disposition"] = (
            "Pièces indépendantes réparties pour inspection ; aucune position d’assemblage attestée."
        )
        cols = math.ceil(math.sqrt(len(parts)))
        largeur = max((m["maillage"].extents[0] for m in v.modeles.values())) + 30
        profondeur = max((m["maillage"].extents[1] for m in v.modeles.values())) + 30
    v.diagnostics = []
    for index, part in enumerate(parts):
        matrix = part.get("matrix", part.get("matrice"))
        if matrix is not None:
            matrix = np.asarray(matrix, float).reshape(4, 4)
            if not np.isfinite(matrix).all():
                raise ValueError("Transformation non finie")
        if independantes:
            lo = v.modeles[part["model"]]["maillage"].bounds[0]
            matrix = np.eye(4)
            matrix[:3, 3] = [index % cols * largeur - lo[0], index // cols * profondeur - lo[1], -lo[2]]
        v.piece(part["name"], part["model"], matrice=matrix)
        d = dict(diagnostics[part["model"]])
        d["piece"] = part["name"]
        d["anomalies"] = [{**a, "piece": part["name"], "id": a["id"] + ":" + part["name"]} for a in d["anomalies"]]
        v.diagnostics.append(d)
    apercu = interne(p, "preuves/visualisation/apercu-gcode.html")
    if apercu.is_file() or source_couches(p, q):
        source["couches_url"] = "/couches?projet=" + quote(nom)
    placement = placement if placement is not None else interne(p, "preuves/placement.json")
    if isinstance(placement, dict) or placement.is_file():
        if tranche is not None:
            if (
                not isinstance(tranche, dict)
                or not isinstance(tranche.get("fichier"), str)
                or (not re.fullmatch("[a-f0-9]{64}", str(tranche.get("sha256", ""))))
            ):
                raise ValueError("Tranchage déclaré sans preuve SHA-256")
            rel = tranche["fichier"]
            if rel.startswith("projets/" + nom + "/"):
                rel = rel[len("projets/" + nom + "/") :]
            source_tranche = interne(p, rel)
            from benchab.organisation.synthese_atelier import _lecture

            with tempfile.TemporaryDirectory(prefix="tranche-scene-") as frozen:
                copie = Path(frozen) / "tranche.3mf"
                digest = hashlib.sha256()
                with _lecture(source_tranche, 256 * 1024 * 1024) as stream, copie.open("xb") as dest:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                        dest.write(chunk)
                if digest.hexdigest() != tranche["sha256"]:
                    raise ValueError("Tranchage modifié")
                from benchab.visualisation.visionneuse import MAX_SUPPORT_GCODE_OCTETS

                with Archive3MF(copie, taille_max_totale=256 * 1024 * 1024) as archive:
                    for _, membre in plaques_gcode_archive(archive):
                        if archive.taille_membre(membre) > MAX_SUPPORT_GCODE_OCTETS:
                            raise ValueError("G-code volumineux")
                    source["tranche_sha256"] = tranche["sha256"]
                    v.vue_impression(
                        placement, interne(p, "candidats"), copie, interne(p, "preuves/tranchage.json"),
                        allegement=0.2, archive_tranche=archive,
                    )
        else:
            v.vue_impression(
                placement, interne(p, "candidats"), None, interne(p, "preuves/tranchage.json"), allegement=0.2
            )
    with tempfile.TemporaryDirectory(prefix="inspection-atelier-") as tmp:
        out = Path(tmp) / "scene.html"
        v.ecrire(out, hors_ligne=True)
        if ressources_partagees:
            from benchab.visualisation.hors_ligne import partagee

            return partagee(out.read_text()).encode()
        return out.read_bytes()
