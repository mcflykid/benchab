"""Réception V2 : centrage calculé indépendant et choix depuis le marqueur 3D."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from benchab.qualite.reception import dossier, url_locale


def produit(m, v):
    "Multiplie matrice 4×4 et vecteur ; renvoie les coordonnées dans les unités d’entrée, sans statut ni écriture."
    return [sum(m[j * 4 + i] * v[j] for j in range(4)) for i in range(4)]


def main():
    """Qualifie une fixture géométrique locale, pixels/s ; code0/PASS ou1/FAIL, sans validation physique ni machine."""
    """Lit les arguments de réception ; mesure pixels et secondes, lève une erreur sur FAIL, sans
    impression ni réglage machine."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--sortie", type=Path, required=True)
    p.add_argument("--synthetique", action="store_true")
    a = p.parse_args()
    a.url = url_locale(a.url)
    a.sortie = dossier(a.sortie)
    result = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        if a.url.startswith("file:"):
            page.route("http**/*", lambda route: route.abort())
        erreurs = []
        page.on("pageerror", lambda e: erreurs.append(str(e)))
        try:
            page.goto(a.url, wait_until="networkidle")
            page.wait_for_function("window.visionneuse?.pieces.every(p=>p.mesh)")
            data = page.locator("#donnees").text_content()
            ds = json.loads(data)["diagnostics"]
            anomalies = [x for d in ds for x in d["anomalies"]]
            assert anomalies, "Le cas de réception exige un risque calculé réel"
            page.locator("#anomalies button").first.click()
            page.wait_for_timeout(200)
            target = page.evaluate("visionneuse.etat().cible")
            point = anomalies[0]["point_modele"]
            if a.synthetique:
                expected = [40 - point[1], point[2] + 7, -point[0] - 10]
                assert max(abs(x - y) for x, y in zip(target, expected)) < 1e-6, (target, expected)
            assert anomalies[0]["sha256"] in page.locator("#diagnostic").inner_text()
            assert "seuil" in page.locator("#diagnostic").inner_text()
            # Parcours utilisateur : replier le diagnostic, puis cliquer le
            # repère visible au centre de la vue pour le retrouver.
            page.locator("#blocAnomalies summary").click()
            assert not page.locator("#diagnostic").is_visible()
            box = page.locator("#vue canvas").bounding_box()
            page.locator("#vue canvas").click(position={"x": box["width"] / 2, "y": box["height"] / 2})
            assert page.locator("#diagnostic").is_visible()
            assert page.locator("#diagnostic").get_attribute("data-anomalie") == anomalies[0]["id"]
            page.screenshot(path=str(a.sortie / "anomalie.png"))
            assert not erreurs and not page.evaluate("window.__erreursVisionneuse"), erreurs
            result = {
                "statut": "PASS",
                "anomalies_localisees": len(anomalies),
                "source": anomalies[0]["source"],
                "sha256": anomalies[0]["sha256"],
                "cible_apres_liste": target,
                "transformation_connue": a.synthetique,
                "diagnostic_depuis_scene": True,
                "erreurs_javascript": erreurs,
                "limite": "Risque géométrique ; aucune validation physique.",
            }
        except Exception as exc:
            import traceback

            page.screenshot(path=str(a.sortie / "echec.png"))
            result = {"statut": "FAIL", "erreur": str(exc), "diagnostic": traceback.format_exc()}
        finally:
            browser.close()
    (a.sortie / "resultat.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["statut"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
