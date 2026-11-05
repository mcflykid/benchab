"""Confronte passivement le Python local à uv.lock, sans lancer ce Python ni installer de paquet."""

import email.parser
import hashlib
import json
import os
import re
import tomllib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def _nom(nom):
    return re.sub(r"[-_.]+", "-", nom).lower()


def _paires_uniques(paires):
    valeurs = {}
    for cle, valeur in paires:
        if cle in valeurs:
            raise ValueError("Clé de provenance JSON dupliquée")
        valeurs[cle] = valeur
    return valeurs


def controler(interpreteur, verrou, paquets):
    """Vérifie versions et révisions des paquets demandés (sans unité).

    Renvoie PASS, FAIL si version/révision connue différente, ou NON VÉRIFIÉ si
    provenance absente/ambiguë. Ne lance aucun interpréteur, import, installateur ou réseau.
    """
    interpreteur = Path(interpreteur).expanduser().absolute()
    verrou = Path(verrou)
    resultat = {
        "statut": "NON VÉRIFIÉ",
        "execution_runtime": False,
        "validation_fonctionnelle": False,
        "paquets": [],
        "verrou_sha256": None,
    }
    try:
        contenu = verrou.read_bytes()
        resultat["verrou_sha256"] = hashlib.sha256(contenu).hexdigest()
        contrat = tomllib.loads(contenu.decode())
        if not interpreteur.is_file() or not os.access(interpreteur, os.X_OK):
            return {**resultat, "raison": "Interpréteur local absent ou non exécutable"}
        # Garder le préfixe lexical du venv : son Python est normalement un lien
        # vers le runtime uv partagé (docs/guides/installation.md).
        prefixe = interpreteur.parent.parent
        sites = [p for p in prefixe.glob("lib/python*/site-packages") if p.is_dir()]
        if (prefixe / "Lib/site-packages").is_dir():
            sites.append(prefixe / "Lib/site-packages")
        if len(sites) != 1:
            return {**resultat, "raison": "Site-packages absent ou plusieurs ABI : provenance ambiguë"}
        attendus = {}
        for paquet in contrat["package"]:
            attendus.setdefault(_nom(paquet["name"]), []).append(paquet)
        observes = {}
        for metadata in sites[0].glob("*.dist-info/METADATA"):
            if metadata.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("METADATA dépasse le budget de lecture de 4 Mio")
            entetes = email.parser.Parser().parsestr(metadata.read_text())
            nom, version = entetes.get("Name"), entetes.get("Version")
            if (
                not nom
                or not version
                or len(entetes.get_all("Name", [])) != 1
                or len(entetes.get_all("Version", [])) != 1
            ):
                raise ValueError("METADATA sans nom/version unique")
            observes.setdefault(_nom(nom), []).append((version, metadata.parent))
        for nom in dict.fromkeys(_nom(p) for p in paquets):
            ligne = {"paquet": nom, "statut": "NON VÉRIFIÉ"}
            resultat["paquets"].append(ligne)
            candidats, distributions = attendus.get(nom, []), observes.get(nom, [])
            if len(candidats) != 1 or len(distributions) != 1:
                ligne["raison"] = "Paquet absent, doublonné ou plusieurs versions verrouillées : correspondance ambiguë"
                continue
            attendu = candidats[0]
            version, dossier = distributions[0]
            ligne.update(version_attendue=attendu["version"], version_observee=version)
            ligne["statut"] = "FAIL" if version != attendu["version"] else "NON VÉRIFIÉ"
            provenance = None
            direct = dossier / "direct_url.json"
            if direct.exists():
                if direct.stat().st_size > 1024 * 1024:
                    raise ValueError("direct_url.json dépasse le budget de lecture de 1 Mio")
                provenance = json.loads(direct.read_text(), object_pairs_hook=_paires_uniques)
                if not isinstance(provenance, dict) or not isinstance(provenance.get("vcs_info", {}), dict):
                    ligne["raison"] = "Métadonnées direct_url.json malformées"
                    continue
            source = attendu.get("source", {})
            revision = urlsplit(source["git"]).fragment if "git" in source else None
            if revision:
                # PEP610 exige URL, VCS et commit : un SHA seul n'identifie pas le paquet attendu
                # (docs/guides/installation.md, diagnostic passif).
                depot = urlsplit(source["git"])
                url = (provenance or {}).get("url")
                vcs = (provenance or {}).get("vcs_info", {})
                if not isinstance(url, str) or vcs.get("vcs") != "git":
                    ligne["raison"] = "Origine Git incomplète : URL ou type VCS absent"
                    continue
                origine = urlsplit(url)
                if not origine.scheme or not origine.netloc or not origine.path:
                    ligne["raison"] = "URL Git non certifiable"
                    continue
                sous_dossier = parse_qs(depot.query).get("subdirectory", [""])[0]
                if (origine.scheme, origine.netloc, origine.path.rstrip("/")) != (
                    depot.scheme,
                    depot.netloc,
                    depot.path.rstrip("/"),
                ) or (provenance or {}).get("subdirectory", "") != sous_dossier:
                    ligne.update(statut="FAIL", raison="Origine Git ou sous-dossier différent du verrou")
                    continue
                observee = vcs.get("commit_id")
                if not observee:
                    ligne["raison"] = "Révision Git installée absente : provenance NON VÉRIFIÉE"
                    continue
                if not isinstance(observee, str) or not re.fullmatch("(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", observee):
                    ligne["raison"] = "Révision installée malformée"
                    continue
                ligne.update(revision_attendue=revision, revision_observee=observee)
                ligne["statut"] = "PASS" if version == attendu["version"] and observee == revision else "FAIL"
            elif "registry" in source and provenance is None:
                ligne["statut"] = "PASS" if version == attendu["version"] else "FAIL"
            else:
                ligne["raison"] = "Source locale ou provenance non certifiée par ce contrôle passif"
        resultat["statut"] = (
            "FAIL"
            if any(p["statut"] == "FAIL" for p in resultat["paquets"])
            else "PASS"
            if resultat["paquets"] and all(p["statut"] == "PASS" for p in resultat["paquets"])
            else "NON VÉRIFIÉ"
        )
    except (OSError, ValueError, KeyError, TypeError) as erreur:
        resultat.update(
            statut="FAIL" if any(p["statut"] == "FAIL" for p in resultat["paquets"]) else "NON VÉRIFIÉ",
            raison=f"{type(erreur).__name__}: {erreur}",
        )
    return resultat
