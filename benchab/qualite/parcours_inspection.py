"""Réception navigateur des mesures, risques, couches et paquet synthétique."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from benchab.qualite.reception import dossier, url_locale


def parcourir(url, sortie):
    """Teste les interactions d’une fixture locale (pixels, s) ; renvoie ses mesures ou lève sur FAIL,
    sans commande machine."""
    url = url_locale(url)
    sortie = dossier(sortie)
    result = {"url": url}
    erreurs = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda e: erreurs.append(str(e)))
            if url.startswith("file:"):
                page.route("http**/*", lambda route: route.abort())
            page.goto(url, wait_until="networkidle")
            page.wait_for_function("window.visionneuse?.pieces.every(p=>p.mesh)")
            page.locator("#filtreRisques").select_option("bord_ouvert")
            page.locator("#anomalies button[data-type=bord_ouvert]").first.click()
            assert "Bord ouvert" in page.locator("#diagnostic").inner_text()
            page.locator("#blocMesures summary").click()
            texte = page.locator("#mesuresSource").inner_text()
            assert "Volume du maillage : non vérifié" in texte and "3 bords ouverts" in texte
            page.locator("#blocComparaison summary").click()
            page.locator("#compareA").select_option("BaseA")
            page.locator("#compareB").select_option("BaseB")
            page.locator("#comparerGeometries").click()
            assert "Écart de volume : 2 mm³" in page.locator("#comparaison").inner_text()
            page.screenshot(path=str(sortie / "inspection-source.png"))
            page.locator("#ouvrirCouches").click()
            frame = page.frame_locator("#couchesFrame")
            frame.locator("#plus").click()
            assert frame.locator("body").evaluate("()=>window.apercu.couche") == 1
            assert frame.locator("body").evaluate(
                (
                    "async()=>{for(let i=0;i<100;i++){if(window.apercu3d)return true;await new Prom"
                    "ise(r=>setTimeout(r,50));}return false;}"
                )
            )
            frame.locator("#o3d").click()
            frame.locator("#vue3d canvas").wait_for()
            assert frame.locator("#info3d").is_visible()
            page.screenshot(path=str(sortie / "couches-3d.png"))
            page.locator("#fermerCouches").click()
            assert page.locator("#panneauCouches").is_hidden()
            assert page.locator("#couchesFrame").get_attribute("src") == "about:blank"
            assert page.locator("#couchesFrame").get_attribute("srcdoc") is None
            assert not erreurs and not page.evaluate("window.__erreursVisionneuse"), erreurs
            result.update(
                statut="PASS",
                mesures_source=True,
                comparaison_volume=True,
                bord_localise=True,
                couches_2d_3d=True,
                fermeture_et_liberation_document=True,
                erreurs_javascript=erreurs,
            )
        except Exception as exc:
            result.update(statut="FAIL", erreur=str(exc), erreurs_javascript=erreurs)
            raise
        finally:
            browser.close()
            (sortie / "resultat.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--sortie", type=Path, required=True)
    a = p.parse_args()
    a.url = url_locale(a.url)
    print(json.dumps(parcourir(a.url, a.sortie), ensure_ascii=False))
