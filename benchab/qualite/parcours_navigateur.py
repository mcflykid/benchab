"""Parcours réels Chromium des pages autonomes. Aucun fichier de projet écrit.

Exécution explicite : Python avec Playwright, --page HTML (répétable) --sortie DOSSIER.
Préconditions : page générée, navigateur installé ; aucun téléchargement automatique.
Chaque commande a un budget fixé : sélectionner ≤ 25 clics de sondage sur une
géométrie inconnue ; isoler, afficher, couper et recadrer : une interaction chacun.
Le sondage appartient au test, pas au parcours ergonomique de la future V1.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from benchab.qualite.reception import dossier


def capturer_vues(page, chemin, sortie):
    """Capture cinq angles et deux coupes d'une page locale (pixels, mm implicites).

    Entrées : page Chromium déjà ouverte sur le HTML autonome, son chemin et un
    dossier privé existant. Écrit sept PNG nommés et un manifeste avec SHA-256.
    Renvoie PASS et les références ; aucune donnée de projet ni réglage n'est modifié.
    """
    chemin = Path(chemin).resolve(strict=True)
    sortie = Path(sortie).resolve(strict=True)
    if chemin.is_relative_to(sortie):
        raise ValueError("La page source ne peut pas être dans le dossier des captures")
    page.goto(chemin.as_uri(), wait_until="networkidle")
    page.wait_for_function("window.visionneuse?.pieces.every(p => p.mesh)", timeout=30000)
    canvas = page.locator("#vue canvas")
    if not canvas.bounding_box():
        raise ValueError("Canvas de visualisation absent ou sans dimensions")
    prefixe = chemin.stem + "-" + hashlib.sha256(str(chemin).encode()).hexdigest()[:8]
    vues = (
        ("iso", None),
        ("face", None),
        ("profil", None),
        ("dessus", None),
        ("dessous", None),
        ("coupe-x", "x"),
        ("coupe-z", "z"),
    )
    captures = []
    for nom, axe in vues:
        angle = "iso" if nom.startswith("coupe-") else nom
        etat = page.evaluate("([vue, axe]) => visionneuse.preparerCapture(vue, axe)", [angle, axe])
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        cible = sortie / f"{prefixe}-{nom}.png"
        if cible.exists():
            raise ValueError(f"Destination de capture déjà présente : {cible.name}")
        contenu = canvas.screenshot(path=str(cible))
        if not contenu.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"Capture PNG invalide : {nom}")
        captures.append(
            {
                "nom": nom,
                "axe_coupe": axe,
                "etat": etat,
                "fichier": cible.name,
                "sha256": hashlib.sha256(contenu).hexdigest(),
            }
        )
    manifeste = {
        "page": chemin.name,
        "statut": "PASS",
        "captures": captures,
        "navigateurs": {"execution": "Chromium", "safari": "NON EXÉCUTÉ"},
        "limite": (
            "Captures d'observation du viewer ; aucune validation physique ni comparaison de fidélité "
            "n'est établie."
        ),
    }
    cible_manifeste = sortie / f"{prefixe}-captures.json"
    if cible_manifeste.exists():
        raise ValueError(f"Manifeste déjà présent : {cible_manifeste.name}")
    cible_manifeste.write_text(json.dumps(manifeste, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifeste


def parcourir(page, chemin, sortie):
    """Teste les interactions d’une fixture locale (pixels, s) ; renvoie ses mesures ou lève sur FAIL,
    sans commande machine."""
    erreurs = []
    page.on("pageerror", lambda error: erreurs.append(str(error)))
    t = time.monotonic()
    page.goto(chemin.resolve().as_uri(), wait_until="networkidle")
    page.wait_for_function("window.visionneuse?.pieces.every(p => p.mesh)", timeout=30000)
    chargement = time.monotonic() - t
    canvas = page.locator("#vue canvas")
    boite = canvas.bounding_box()
    assert boite and boite["width"] > 100 and boite["height"] > 100
    page.mouse.move(0, 0)
    avant = canvas.screenshot()
    x, y = boite["x"] + boite["width"] / 2, boite["y"] + boite["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 80, y + 40, steps=12)
    page.mouse.up()
    page.mouse.move(0, 0)  # comparer le rendu sans surbrillance de survol
    page.wait_for_timeout(300)  # amortissement OrbitControls, pas un chargement réseau
    assert canvas.screenshot() != avant, "La rotation ne change pas le rendu"
    page.locator("#recadrer").click()
    page.wait_for_timeout(300)
    trouve = False
    for fy in (0.5, 0.4, 0.6, 0.3, 0.7):
        for fx in (0.5, 0.4, 0.6, 0.3, 0.7):
            canvas.click(position={"x": boite["width"] * fx, "y": boite["height"] * fy})
            if page.locator("#isoler").is_enabled():
                trouve = True
                break
        if trouve:
            break
    assert trouve, "Aucune sélection réelle dans le canvas"
    page.locator("#isoler").click()
    assert page.evaluate("visionneuse.pieces.filter(p => p.mesh.visible).length") == 1
    page.locator("#tout").click()
    page.locator("#coupe").select_option("2")
    assert page.locator("#blocCoupe").is_visible()
    coupe_avant = canvas.screenshot()
    page.locator("#inverse").check()
    page.wait_for_timeout(100)
    assert canvas.screenshot() != coupe_avant, "La coupe ne change pas le rendu"
    page.locator("#coupe").select_option("")
    page.locator("#recadrer").focus()
    page.keyboard.press("Enter")
    with page.expect_download() as dl:
        page.locator("#image").click()
    image = sortie / (chemin.stem + "-export.png")
    dl.value.save_as(image)
    assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    page.screenshot(path=str(sortie / (chemin.stem + "-ecran.png")))
    assert not erreurs, erreurs
    return {
        "page": chemin.name,
        "statut": "PASS",
        "chargement_s": chargement,
        "octets_html": chemin.stat().st_size,
        "erreurs_javascript": erreurs,
        "parcours": [
            "chargement",
            "rotation avec rendu",
            "sélection canvas",
            "isolement",
            "tout afficher",
            "coupe avec rendu",
            "recadrage clavier",
            "export PNG",
        ],
        "limites": [
            "Chromium ; Safari séparé",
            "Trackpad physique non testé",
            "Contrastes et réception V1 non couverts par ce socle",
        ],
    }


def main():
    """Lit les arguments de réception ; mesure pixels et secondes, lève une erreur sur FAIL, sans
    impression ni réglage machine."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--page", type=Path, action="append", required=True)
    p.add_argument("--sortie", type=Path, required=True)
    p.add_argument("--vues-multi-angle", action="store_true", help="Ajoute cinq angles et deux coupes du viewer local")
    a = p.parse_args()
    a.sortie = dossier(a.sortie)
    resultats = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for chemin in a.page:
                page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
                try:
                    resultat = parcourir(page, chemin, a.sortie)
                    if a.vues_multi_angle:
                        resultat["captures_multi_angle"] = capturer_vues(page, chemin, a.sortie)
                    resultats.append(resultat)
                except Exception as exc:
                    page.screenshot(path=str(a.sortie / (chemin.stem + "-echec.png")))
                    resultats.append({"page": chemin.name, "statut": "FAIL", "erreur": str(exc)})
                finally:
                    page.close()
        finally:
            browser.close()
    (a.sortie / "resultats.json").write_text(
        json.dumps(resultats, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(resultats, ensure_ascii=False, indent=2))
    return int(any(r["statut"] != "PASS" for r in resultats))


if __name__ == "__main__":
    raise SystemExit(main())
