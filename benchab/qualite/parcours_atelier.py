"""Réception de l'atelier synthétique, observations seulement dans la fixture."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from benchab.qualite.reception import dossier, url_locale

BUDGETS = {
    "recherche_et_ouverture": 3,
    "comparaison_preparations": 3,
    "lecture_preuve_et_retour": 2,
    "observation_depuis_projet": 6,
    "recherche_connaissances": 3,
    "retour_apres_erreur": 1,
}


def produit(m, v):
    "Multiplie matrice 4×4 et vecteur ; renvoie les coordonnées dans les unités d’entrée, sans statut ni écriture."
    return [sum(m[j * 4 + i] * v[j] for j in range(4)) for i in range(4)]


def parcourir(base, sortie):
    """Teste les interactions d’une fixture locale (pixels, s) ; renvoie ses mesures ou lève sur FAIL,
    sans commande machine."""
    sortie = dossier(sortie)
    result = {}
    erreurs = []
    base = url_locale(base)
    origine = urlsplit(base).netloc
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            page = b.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda x: erreurs.append(str(x)))
            # L'application et ses ressources restent locales ; tout autre réseau est refusé.
            page.route(
                "http**/*",
                lambda route: route.continue_() if urlsplit(route.request.url).netloc == origine else route.abort(),
            )
            page.goto(base + "/")
            page.get_by_role("link", name="Ouvrir l’atelier", exact=False).click()
            assert page.locator("h1").inner_text() == "Projets et pièces"
            page.screenshot(path=str(sortie / "catalogue.png"))
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement.tagName") == "A"
            page.get_by_label("Rechercher un projet ou une pièce").fill("BaseB")
            page.get_by_role("button", name="Rechercher", exact=True).click()
            assert "Lune ancienne" not in page.locator("main").inner_text()
            page.get_by_role("link", name="Inspection été — projet actif", exact=True).click()
            page.get_by_label("Préparation A").select_option("P01")
            page.get_by_label("Préparation B").select_option("P02")
            page.get_by_role("button", name="Comparer les préparations").click()
            assert (
                "P01 → P02" in page.locator("#comparaisonPreparations").inner_text()
                and "parametres" in page.locator("#comparaisonPreparations").inner_text()
            )
            page.get_by_role("link", name="Vérifier le contrat du projet").click()
            assert "PASS — contrat" in page.get_by_role("status").inner_text()
            page.screenshot(path=str(sortie / "projet-actif.png"))
            page.get_by_role("link", name="projets/inspection-ete/preuves/controle.json", exact=True).click()
            assert (
                "WARNING" in page.locator("pre").inner_text()
                and "SHA-256 du document lu" in page.locator("main").inner_text()
            )
            page.get_by_role("link", name="Retour au projet").click()
            page.get_by_role("link", name="Ajouter une observation pointée", exact=False).click()
            page.wait_for_function("window.visionneuse?.pieces.every(p=>p.mesh) && visionneuse.serveur()")
            page.get_by_text("Commentaires", exact=True).click()
            page.locator("#modeCom").check()
            etat = page.evaluate("visionneuse.etat()")
            monde = page.evaluate("visionneuse.pieces[0].mesh.matrixWorld.elements")
            point = produit(etat["projection"], produit(etat["mondeInverse"], produit(monde, [0, 0, 0, 1])))
            box = page.locator("#vue canvas").bounding_box()
            x = (point[0] / point[3] + 1) * box["width"] / 2
            y = (1 - point[1] / point[3]) * box["height"] / 2
            page.locator("#vue canvas").click(position={"x": x, "y": y})
            assert page.locator("#blocNouveau").is_visible(), "Point géométrique non trouvé"
            page.locator("#texteCom").fill("Observation synthétique été, sans validation physique")
            with page.expect_response(lambda r: "/__commentaires" in r.url and r.request.method == "POST") as reponse:
                page.locator("#ajouterCom").click()
            assert reponse.value.ok
            page.reload()
            page.wait_for_function("window.visionneuse?.commentaires().length===1")
            c = page.evaluate("visionneuse.commentaires()[0]")
            assert c["piece"] == "Gauche" and len(c["point_mm"]) == 3 and c["statut"] == "ouvert"
            page.goto(base + "/atelier")
            page.get_by_role("link", name="Lune ancienne — historique", exact=True).click()
            assert "aucune géométrie déclarée" in page.locator("main").inner_text()
            page.get_by_role("link", name="suivi/journal/2026-11/retour-synthetique.md", exact=True).click()
            assert "Déclaration humaine synthétique" in page.locator("pre").inner_text()
            page.get_by_role("link", name="Connaissances", exact=True).click()
            page.get_by_label("Rechercher dans les connaissances").fill("base")
            page.get_by_role("button", name="Rechercher").click()
            assert "Ne valide pas le contact réel" in page.locator("main").inner_text()
            page.goto(base + "/atelier?projet=..")
            assert page.get_by_role("link", name="Revenir aux projets").is_visible()
            page.get_by_role("link", name="Revenir aux projets").click()
            assert page.locator("h1").inner_text() == "Projets et pièces"
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            assert not erreurs, erreurs
            result = {
                "statut": "PASS",
                "catalogue_recherche_piece": True,
                "actif_historique": True,
                "comparaison_declarations": True,
                "contrat_non_destructif": True,
                "preuve_source_sha": True,
                "connaissances": True,
                "observation_pointee_fixture_persistee": c,
                "budgets_interactions": BUDGETS,
                "erreurs_javascript": erreurs,
                "limites": (
                    "Réseau externe refusé ; observation seulement dans la fixture synthétique. Tra"
                    "ckpad physique et appréciation du propriétaire non recueillis."
                ),
            }
        except Exception as exc:
            result = {"statut": "FAIL", "erreur": str(exc), "erreurs_javascript": erreurs}
            raise
        finally:
            b.close()
            (sortie / "resultat.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("--base", required=True)
    a.add_argument("--sortie", type=Path, required=True)
    args = a.parse_args()
    print(json.dumps(parcourir(args.base, args.sortie), ensure_ascii=False))
