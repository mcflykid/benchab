"""Réception V1 : interactions, états 3D, rendu, contrastes et export.

Budgets fixés dans le journal du lot 1 ; le test ne modifie aucun projet.
--url accepte une page autonome ou l'accueil HTTP avec --projet NOM.
"""

import argparse
import json
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

from benchab.qualite.reception import dossier, url_locale

CONTRASTES = r"""() => {
  const cv=document.createElement('canvas');
cv.width=cv.height=1;
const ctx=cv.getContext('2d',{
willReadFrequently:true}
);
  const rgb=s=>{
ctx.clearRect(0,0,1,1);
ctx.fillStyle=s;
ctx.fillRect(0,0,1,1);
return [...ctx.getImageData(0,0,1,1).data]}
;
  const L=c=>c.slice(0,3).map(v=>{
v/=255;
return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}
).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
  const bg=e=>{
let out=[255,255,255];
const chain=[];
for(let p=e;
p;
p=p.parentElement)chain.unshift(p);
for(const p of chain){
const c=rgb(getComputedStyle(p).backgroundColor),a=c[3]/255;
out=out.map((v,i)=>c[i]*a+v*(1-a));
}
return out}
;
  return [...document.querySelectorAll('button,summary,legend,.note,h1,#contexte,a')]
.filter(e=>e.getClientRects().length && !e.disabled).map(e=>{
    const c=getComputedStyle(e),a=L(rgb(c.color)),b=L(bg(e));
    return {element:e.id||e.textContent.slice(0,40),ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
  });
}"""


def recevoir(page, url, projet, sortie):
    """Teste les interactions d’une fixture locale (pixels, s) ; renvoie ses mesures ou lève sur FAIL,
    sans commande machine.
    """
    erreurs = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    t = time.monotonic()
    page.goto(url, wait_until="networkidle")
    if projet:
        page.locator('a[href="/inspecter?projet=' + projet + '"]').click()
    page.wait_for_function("window.visionneuse?.pieces.every(p=>p.mesh)", timeout=60000)
    chargement = time.monotonic() - t
    print("Chargement", round(chargement, 3), flush=True)

    def etat():
        """Lit l’état UI de la fixture, sans unité physique ni écriture."""
        return page.evaluate("visionneuse.etat()")

    initial = etat()
    canvas = page.locator("#vue canvas")
    r = canvas.bounding_box()
    assert r and r["width"] > 100 and r["height"] > 100
    page.mouse.move(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
    page.mouse.down()
    page.mouse.move(r["x"] + r["width"] / 2 + 70, r["y"] + r["height"] / 2 + 25, steps=10)
    page.mouse.up()
    page.wait_for_timeout(250)
    assert etat()["camera"] != initial["camera"], "Rotation sans déplacement caméra"
    avant_zoom = etat()["camera"]
    page.mouse.wheel(0, -160)
    page.wait_for_timeout(250)
    assert etat()["camera"] != avant_zoom, "Zoom molette sans effet"
    avant_pan = etat()["cible"]
    page.mouse.down(button="right")
    page.mouse.move(r["x"] + r["width"] / 2 + 100, r["y"] + r["height"] / 2 + 60, steps=10)
    page.mouse.up(button="right")
    assert etat()["cible"] != avant_pan, "Déplacement sans effet"
    print("Gestes souris terminés", flush=True)
    page.locator("#initiale").click()
    page.wait_for_timeout(250)
    assert max(abs(a - b) for a, b in zip(initial["camera"], etat()["camera"])) < 0.01
    # Le chemin Tab réel part du document ; 30 transitions au plus.
    page.locator("body").click(position={"x": 2, "y": 2})
    for _ in range(30):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.parentElement?.id==='listePieces'"):
            break
    else:
        raise AssertionError("Liste de pièces inaccessible par Tab")
    page.keyboard.press("Enter")
    assert etat()["selection"]
    camera_selection = etat()["camera"]
    assert page.locator("#listePieces button[aria-pressed=true]").count() == 1
    print("Sélection clavier terminée", flush=True)
    page.locator("#isoler").click()
    assert page.evaluate("visionneuse.pieces.filter(p=>p.mesh.visible).length") == 1
    assert etat()["camera"] == camera_selection, "Isolement déplace la caméra"
    page.locator("#masquer").click()
    assert page.evaluate("visionneuse.pieces.filter(p=>p.mesh.visible).length") == 0
    page.locator("#tout").click()
    assert page.evaluate("visionneuse.pieces.some(p=>p.mesh.visible)")
    page.locator("#coupe").select_option("2")
    assert etat()["coupes"] == 1
    print("Coupe active", flush=True)
    avant_coupe = canvas.screenshot()
    page.locator("#inverse").check()
    page.wait_for_timeout(150)
    assert canvas.screenshot() != avant_coupe, "Coupe sans changement du rendu"
    page.locator("#coupe").select_option("")
    assert etat()["coupes"] == 0
    page.locator("#recadrer").click()
    page.locator("#vue").focus()
    c = etat()["camera"]
    page.keyboard.press("ArrowLeft")
    assert etat()["camera"] != c
    page.keyboard.press("+")
    page.keyboard.press("r")
    assert etat()["selection"] is None
    plaques = page.locator("#plaques button")
    if plaques.count() > 1:
        plaques.nth(1).click()
        assert plaques.nth(1).get_attribute("aria-pressed") == "true"
        assert page.locator("#resumePlaque").is_visible()
        page.screenshot(path=str(sortie / "plaque.png"))
        page.locator("#initiale").click()
    else:
        assert "Plaques indisponibles" in page.locator("#fidelite").inner_text()
    with page.expect_download() as download:
        page.locator("#image").click()
    png = sortie / "export.png"
    download.value.save_as(png)
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    print("Export terminé", flush=True)
    contrastes = {}
    for theme in ("light", "dark"):
        page.emulate_media(color_scheme=theme)
        mesures = page.evaluate(CONTRASTES)
        contrastes[theme] = mesures
        assert all(x["ratio"] >= 4.5 for x in mesures), [x for x in mesures if x["ratio"] < 4.5]
    page.emulate_media(color_scheme="light")
    page.screenshot(path=str(sortie / "ecran.png"))
    page.set_viewport_size({"width": 430, "height": 900})
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), "Débordement horizontal"
    assert page.locator("#vue canvas").bounding_box()["width"] > 100
    assert not erreurs and not page.evaluate("window.__erreursVisionneuse"), erreurs
    return {
        "statut": "PASS",
        "url": url,
        "projet": projet,
        "chargement_s": chargement,
        "contrastes": contrastes,
        "erreurs_javascript": erreurs,
        "parcours": [
            "ouverture",
            "sélection clavier",
            "rotation",
            "zoom",
            "déplacement",
            "isolement",
            "masquage",
            "réaffichage",
            "coupe avec rendu",
            "recadrage",
            "vue initiale",
            "navigation clavier 3D",
            "plaques ou raison",
            "PNG",
            "fenêtre étroite",
        ],
        "limite": "Gestes injectés ; aucun essai avec un trackpad physique ni appréciation ergonomique humaine.",
    }


def main():
    """Qualifie la fixture HTML locale, pixels/s ; code0/PASS ou1/FAIL, sans accès machine ni source modifiée."""
    """Lit les arguments de réception ; mesure pixels et secondes, lève une erreur sur FAIL, sans
    impression ni réglage machine."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--projet")
    p.add_argument("--sortie", type=Path, required=True)
    a = p.parse_args()
    a.url = url_locale(a.url)
    a.sortie = dossier(a.sortie)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        if a.url.startswith("file:"):
            page.route("http**/*", lambda route: route.abort())
        try:
            result = recevoir(page, a.url, a.projet, a.sortie)
        except Exception as exc:
            page.screenshot(path=str(a.sortie / "echec.png"))
            result = {
                "statut": "FAIL",
                "erreur": type(exc).__name__ + ": " + str(exc),
                "diagnostic": traceback.format_exc(),
            }
        finally:
            browser.close()
    (a.sortie / "resultat.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "contrastes"}, ensure_ascii=False, indent=2))
    return int(result["statut"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
