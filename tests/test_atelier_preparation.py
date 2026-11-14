"""Témoins publics de l’atelier et de ses mesures en millimètres."""

import trimesh

from benchab.visualisation import carte_risques
from benchab.visualisation.atelier_preparation import construire


def test_assise_et_surplomb_dans_le_repere_pose():
    """La même boîte perd son assise quand elle est réellement au-dessus du plan."""
    mesh = trimesh.creation.box(extents=[10, 8, 2])
    mesh.apply_translation([0, 0, 1])
    sol = carte_risques.mesurer(mesh, "boîte", "témoin", "a" * 64)
    assert sol["calques"]["assise"]["aire_mm2"] == 80
    assert sol["calques"]["assise"]["marge_centre_mm"] == 4
    assert sol["faces_detectees"] == 0
    mesh.apply_translation([0, 0, 2])
    suspendue = carte_risques.mesurer(mesh, "boîte", "témoin", "a" * 64)
    assert suspendue["calques"]["assise"]["aire_mm2"] == 0
    assert suspendue["faces_detectees"] == 2
    assert suspendue["calques"]["surplomb"]["surface_surplomb_mm2"] == 80


def test_surface_et_assise_stables_apres_subdivision():
    """Les surfaces et la marge restent stables malgré l’augmentation du nombre de faces."""
    import pytest

    mesh = trimesh.creation.box(extents=[10, 8, 2])
    mesh.apply_translation([0, 0, 1])
    mesures = [
        carte_risques.mesurer(m, "boîte", "témoin", "a" * 64)["calques"]
        for m in (mesh, mesh.subdivide().subdivide())
    ]
    for cle, mesure in (("assise", "aire_mm2"), ("assise", "marge_centre_mm"),
                        ("surplomb", "surface_surplomb_mm2")):
        assert mesures[1][cle][mesure] == pytest.approx(mesures[0][cle][mesure])


def test_orientation_indicative_ne_deplace_pas_la_piece():
    """La mesure sur une autre face laisse les coordonnées de la source intactes."""
    mesh = trimesh.creation.box(extents=[4, 8, 5])
    mesh.apply_translation([0, 0, 2.5])
    sommets = mesh.vertices.copy()
    alternative = carte_risques.orientation_indicative(mesh)
    assert alternative["assise_alternative_mm2"] == 40
    assert (mesh.vertices == sommets).all()


def test_bibliotheque_assemblage_pose_et_jeu_nominal():
    """Le couvercle garde le jeu par face et chaque modèle est un solide posé."""
    import pytest

    from benchab.conception.bibliotheque_atelier import generer

    boite = generer("boite", 20, 0.3)
    couvercle = boite["Couvercle"]
    assert boite["Boîte"].extents.tolist() == [20, 20, 10]
    insert = couvercle.vertices[couvercle.vertices[:, 2] > 2]
    assert max(insert[:, 0]) - min(insert[:, 0]) == pytest.approx(16.2, abs=1e-5)
    for modele in ("boite", "clip", "temoin"):
        assert all(m.is_volume and m.bounds[0, 2] == 0 for m in generer(modele, 20, 0.3).values())


def test_source_sans_profil_et_epaisseur_localisee(tmp_path):
    """Un STL fin reste sans profil ; la mesure est exprimée dans le repère effectivement lu."""
    source = tmp_path / "fine.stl"
    mesh = trimesh.creation.box(extents=[10, 8, 0.6])
    mesh.apply_translation([0, 0, 0.3])
    mesh.export(source)
    original = source.read_bytes()
    vue = construire(source, unites="mm")
    assert vue.atelier["reglages"] == []
    assert vue.atelier["propositions"]
    assert any(a["type"] == "epaisseur_locale" for a in vue.diagnostics[0]["anomalies"])
    assert source.read_bytes() == original


def test_erreur_de_mesure_conserve_la_geometrie(tmp_path, monkeypatch):
    """Une mesure indisponible laisse l’atelier et la pièce consultables."""
    source = tmp_path / "temoin.stl"
    trimesh.creation.box().export(source)

    def indisponible(*args, **kwargs):
        raise ValueError("Calcul indisponible sur le témoin")

    monkeypatch.setattr(carte_risques, "mesurer", indisponible)
    vue = construire(source, unites="mm")
    assert all(c["statut"] == "NON VÉRIFIÉ" for c in vue.diagnostics[0]["calques"].values())
    assert vue.atelier["propositions"] == []


def test_annulation_interrompt_avant_la_mesure(tmp_path, monkeypatch):
    """L’annulation côté serveur empêche la carte d’être calculée ou rendue."""
    import pytest

    source = tmp_path / "temoin.stl"
    trimesh.creation.box().export(source)

    def impossible(*args, **kwargs):
        pytest.fail("La mesure ne doit pas démarrer après l’annulation.")

    monkeypatch.setattr(carte_risques, "mesurer", impossible)
    with pytest.raises(ValueError, match="Analyse annulée"):
        construire(source, unites="mm", annule=lambda: True)


def test_annulation_au_milieu_des_rayons():
    """La demande d’arrêt interrompt la mesure avant le verdict final."""
    import pytest

    appels = 0

    def annule():
        nonlocal appels
        appels += 1
        return appels == 6

    mesh = trimesh.creation.box(extents=[4, 4, 4])
    with pytest.raises(carte_risques.AnalyseAnnulee):
        carte_risques.mesurer(mesh, "témoin", "source", "a" * 64, annule=annule)


def test_bibliotheque_archive_deux_versions_liees(tmp_path):
    """Un changement de cote crée une preuve distincte, reliée à l’ancienne version."""
    import json

    from benchab.conception.registre_variantes import verifier
    from benchab.visualisation.atelier_preparation import enregistrer_bibliotheque

    racine = tmp_path / "prive"
    racine.mkdir()
    avant = enregistrer_bibliotheque(racine, "boite", 20, 0.3)
    apres = enregistrer_bibliotheque(racine, "boite", 22, 0.4, avant["id"])
    base = racine / "conceptions/bibliotheque"
    registre = json.loads((base / apres["id"] / "registre.json").read_text())
    assert verifier(base, registre)["explorations"] == [avant["id"], apres["id"]]
    assert registre[1]["parent"] == avant["id"]
    assert (base / avant["id"] / "piece.stl").read_bytes() != (base / apres["id"] / "piece.stl").read_bytes()


def test_appuis_multiplaque_ne_cachent_pas_un_defaut_local(tmp_path):
    """Deux G-codes en repère local gardent leurs mesures sans fausse superposition."""
    from zipfile import ZipFile

    from benchab.visualisation.comparaison_atelier import _appuis_selection

    chemin = tmp_path / "planches.3mf"
    debut = "G90\nM83\n;LAYER_CHANGE\n;Z:0.2\n;TYPE:Inner wall\n;WIDTH:0.4\nG1 X0 Y0 Z0.2\nG1 X4 Y0 E0.2\n"
    suite = ";LAYER_CHANGE\n;Z:0.4\n;TYPE:Inner wall\n;WIDTH:0.4\nG1 X0 Y{y} Z0.4\nG1 X4 Y{y} E0.2\n"
    with ZipFile(chemin, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", debut + suite.format(y=0))
        archive.writestr("Metadata/plate_2.gcode", debut + suite.format(y=5))
    rapport = _appuis_selection(chemin, [1, 2])
    assert rapport["statut"] == "PASS"
    assert [p["plaque"] for p in rapport["par_plaque"]] == [1, 2]
    assert rapport["par_plaque"][0]["parcours_sans_appui"] == 0
    assert rapport["par_plaque"][1]["parcours_sans_appui"] > 0
    assert rapport["superposition"] is None
    assert _appuis_selection(chemin, [1, 3])["statut"] == "NON VÉRIFIÉ"


def test_comparaison_depuis_la_reference_exacte(tmp_path, monkeypatch):
    """Les écarts sont signés, et une plaque manquante invalide l’autre variante."""
    import hashlib
    from zipfile import ZipFile

    from benchab.visualisation import comparaison_atelier as module

    source = tmp_path / "origine.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
    original = source.read_bytes()
    sha = hashlib.sha256(original).hexdigest()
    binaire = tmp_path / "moteur"
    binaire.write_bytes(b"moteur temoin")
    monkeypatch.setattr(module, "charger", lambda: {"slicer": {"executable": binaire}})
    deltas = []

    def cloner(entree, sortie, modifs):
        assert entree.read_bytes() == original
        deltas.append(modifs["reglages"]["global"])
        sortie.write_bytes(b"variante")
        return {}, {"statut": "PASS", "differences": []}

    mesures = iter([
        {"plaques": [1, 2], "secondes": 310, "grammes": 11, "debit": "PASS"},
        {"plaques": [1, 2], "secondes": 290, "grammes": 11.5, "debit": "PASS"},
        {"plaques": [1], "secondes": 100, "grammes": 4, "debit": "PASS"},
    ])

    def trancher(entree, dossier, cible, **_):
        assert entree.parent == dossier
        assert entree.read_bytes() == (original if cible.startswith("reference") else b"variante")
        return next(mesures), None

    monkeypatch.setattr(module.optimisation, "clone", cloner)
    monkeypatch.setattr(module, "trancher_fichier", trancher)
    monkeypatch.setattr(module, "_mesures", lambda x: x)
    monkeypatch.setattr(module, "_empreintes_commandes", lambda _: {1: "commandes"})
    resultat = module.comparer(
        source, sha, [{"titre": "Remplissage", "avant_mm_s": 350, "apres_mm_s": 249.09}], marge=True,
    )
    assert deltas == [{"sparse_infill_speed": "249.09"}, {"sparse_infill_speed": "224.18"}]
    valide, invalide = resultat["variantes"]
    assert valide["ecarts"] == {"secondes": -20, "grammes": 0.5}
    assert valide["commandes_identiques"]
    assert "Plaques différentes" in invalide["erreur"] and "ecarts" not in invalide
    assert source.read_bytes() == original


def test_variante_locale_liee_a_la_reference(tmp_path):
    """Seule la référence épinglée reçoit les plafonds privés, en mm/s."""
    import json

    import pytest

    from benchab.visualisation.comparaison_atelier import variante_locale

    config = tmp_path / "config"
    config.mkdir()
    variante = {"gap_infill_speed": "105", "internal_solid_infill_speed": "135"}
    (config / "variantes-comparateur.json").write_text(json.dumps({"a" * 64: variante}), encoding="utf-8")
    reference = {"gap_infill_speed": "422.5", "internal_solid_infill_speed": "422.5"}
    assert variante_locale(tmp_path, "a" * 64, reference) == variante
    assert variante_locale(tmp_path, "b" * 64, reference) is None
    with pytest.raises(ValueError, match="Plafond local invalide"):
        variante_locale(tmp_path, "a" * 64, {**reference, "gap_infill_speed": "100"})


def test_variantes_procede_conservent_les_autres_reglages():
    """Deux variantes séparées sur une référence simple ; le vase garde ses contraintes."""
    from benchab.visualisation.comparaison_atelier import variantes_procede

    params = {
        "layer_height": "0.2", "nozzle_diameter": ["0.4"], "spiral_mode": "0",
        "enable_support": "0", "support_type": "tree(auto)",
    }
    assert [reglages for _, reglages in variantes_procede(params, False)] == [
        {"layer_height": "0.16"}, {"enable_support": "1"},
    ]
    assert variantes_procede({**params, "spiral_mode": "1"}, False) == []
    assert variantes_procede(params, True) == []


def test_deux_maillages_du_meme_membre_externe_restant_lies_a_leurs_plaques(tmp_path):
    """Un même membre 3MF contient deux ID, deux géométries et deux plaques."""
    from xml.etree import ElementTree as ET
    from zipfile import ZipFile

    from benchab.archives_3mf import Archive3MF
    from benchab.fabrication import optimisation
    from benchab.imports3d.lecture_3mf import NS, NS_REL, PROD, REL_MODELE
    from benchab.visualisation.atelier_preparation import _choisir_pose, _lire_3mf
    from benchab.visualisation.orientation_atelier import copier_pose

    source = tmp_path / "externe.3mf"
    modele = ET.Element(NS + "model", unit="millimeter")
    ressources = ET.SubElement(modele, NS + "resources")
    build = ET.SubElement(modele, NS + "build")
    externe = ET.Element(NS + "model", unit="millimeter")
    feuilles = ET.SubElement(externe, NS + "resources")
    config = ET.Element("config")
    for i, mesh in enumerate((trimesh.creation.box(extents=[2, 3, 4]),
                              trimesh.creation.icosphere(subdivisions=1, radius=5)), 1):
        objet = ET.SubElement(ressources, NS + "object", id=str(i), type="model")
        composants = ET.SubElement(objet, NS + "components")
        ET.SubElement(composants, NS + "component", {PROD + "path": "/3D/feuilles.model", "objectid": str(i)})
        ET.SubElement(build, NS + "item", objectid=str(i), transform=f"1 0 0 0 1 0 0 0 1 {40 + 300*(i-1)} 20 5")
        feuille = ET.SubElement(feuilles, NS + "object", id=str(i), type="model")
        geometrie = ET.SubElement(feuille, NS + "mesh")
        sommets = ET.SubElement(geometrie, NS + "vertices")
        for sommet in mesh.vertices:
            ET.SubElement(sommets, NS + "vertex", dict(zip("xyz", map(str, sommet), strict=True)))
        triangles = ET.SubElement(geometrie, NS + "triangles")
        for face in mesh.faces:
            attributs = dict(zip(("v1", "v2", "v3"), map(str, face), strict=True))
            ET.SubElement(triangles, NS + "triangle", {**attributs, "paint_color": str(4 * i)})
        description = ET.SubElement(config, "object", id=str(i))
        ET.SubElement(description, "metadata", key="name", value=f"Objet {i}")
        plaque = ET.SubElement(config, "plate")
        ET.SubElement(plaque, "metadata", key="plater_id", value=str(i))
        instance = ET.SubElement(plaque, "model_instance")
        ET.SubElement(instance, "metadata", key="object_id", value=str(i))
    def relations(cible):
        racine = ET.Element(NS_REL + "Relationships")
        ET.SubElement(racine, NS_REL + "Relationship", Id="r1", Type=REL_MODELE, Target=cible)
        return ET.tostring(racine)

    with ZipFile(source, "w") as archive:
        archive.writestr("_rels/.rels", relations("/3D/3dmodel.model"))
        archive.writestr("3D/_rels/3dmodel.model.rels", relations("/3D/feuilles.model"))
        archive.writestr("3D/3dmodel.model", ET.tostring(modele))
        archive.writestr("3D/feuilles.model", ET.tostring(externe))
        archive.writestr("Metadata/model_settings.config", ET.tostring(config))
        archive.writestr("Metadata/project_settings.config", '{"inner_wall_speed":"100"}')
    objets, _, _, _ = _lire_3mf(source)
    assert [(plaque, ident["modele_maillage"], ident["objet_id_maillage"]) for _, _, plaque, ident in objets] == [
        (1, "3D/feuilles.model", "1"), (2, "3D/feuilles.model", "2"),
    ]
    assert [len(mesh.faces) for _, mesh, _, _ in objets] == [12, 80]
    assert [max(mesh.extents) for _, mesh, _, _ in objets] == [4, 10]
    # Le sérialiseur du témoin émet des préfixes ; l’atelier doit les préserver.
    cible = tmp_path / "tourne.3mf"
    arguments, options = _choisir_pose(objets, False, "Objet 2 · 2")
    preuve = copier_pose(source, cible, *arguments, **options)
    assert preuve["plaque"] == 2 and preuve["instance"] == 2
    relus = _lire_3mf(cible)[0]
    assert (objets[0][1].vertices == relus[0][1].vertices).all()
    assert [o[2] for o in relus] == [1, 2]
    with Archive3MF(source) as avant, Archive3MF(cible) as apres:
        assert [m for m in avant.noms() if avant.lire(m) != apres.lire(m)] == [preuve["membre"]]
    export = tmp_path / "vitesses.3mf"
    _, integrite = optimisation.clone(source, export, {"reglages": {"global": {"inner_wall_speed": "90"}}})
    assert integrite["statut"] == "PASS"
    with Archive3MF(source) as avant, Archive3MF(export) as apres:
        assert [m for m in avant.noms() if avant.lire(m) != apres.lire(m)] == ["Metadata/project_settings.config"]
    assert [o[2] for o in _lire_3mf(export)[0]] == [1, 2]

    local = tmp_path / "par-objet.3mf"
    _, preuve_locale = optimisation.clone(source, local, {
        "reglages": {"objets": [{"id": "2", "reglages": {"inner_wall_speed": "80"}}]},
    })
    assert [d["chemin"] for d in preuve_locale["differences"]] == ["objet/Objet 2/inner_wall_speed"]
    with Archive3MF(local) as fichier:
        objets_relus = fichier.xml("Metadata/model_settings.config")
        assert objets_relus.find("object[@id='1']/metadata[@key='inner_wall_speed']") is None
        assert objets_relus.find("object[@id='2']/metadata[@key='inner_wall_speed']").get("value") == "80"


def test_plafond_compare_les_reglages_de_reference():
    """Le plafond volumique réduit le remplissage et conserve une paroi déjà sous la limite."""
    from benchab.visualisation.atelier_preparation import _vitesse

    params = {
        "filament_settings_id": ["PLA témoin"],
        "filament_max_volumetric_speed": ["20.28"],
        "layer_height": "0.2",
        "inner_wall_speed": "169",
        "inner_wall_line_width": "0.45",
        "sparse_infill_speed": "456.3",
        "sparse_infill_line_width": "0.45",
    }
    vitesses, _ = _vitesse(params, False)
    paroi, remplissage = vitesses
    assert (paroi["avant_mm_s"], paroi["apres_mm_s"]) == (169, 169)
    assert (remplissage["avant_mm_s"], remplissage["apres_mm_s"]) == (456.3, 249.09)
    assert remplissage["apres_mm_s"] * remplissage["section_mm2"] <= 20.28
    assert _vitesse(params | {"spiral_mode": "1"}, False)[0] == []
    assert _vitesse(params, True)[0] == []


def test_rapide_respecte_le_filament_et_les_limites_xy():
    """Un trajet peut accélérer pendant que l’autre est ramené à la limite (mm/s)."""
    from benchab.visualisation.comparaison_atelier import variante_intention

    params = {
        "spiral_mode": "0", "layer_height": "0.2",
        "filament_settings_id": ["PLA témoin"], "filament_max_volumetric_speed": ["20.28"],
        "inner_wall_speed": "169", "sparse_infill_speed": "350",
        "inner_wall_line_width": "0.45", "sparse_infill_line_width": "0.45",
        "machine_max_speed_x": ["600", "200"], "machine_max_speed_y": ["600", "220"],
        "outer_wall_speed": "70", "support_top_z_distance": "0.2",
    }
    _, changements = variante_intention(params, False, "rapide")[0]
    assert changements == {"inner_wall_speed": "200", "sparse_infill_speed": "200"}
    for interdit in ({"filament_settings_id": []}, {"spiral_mode": "1"}):
        assert variante_intention(params | interdit, False, "rapide") == []
    assert variante_intention(params, True, "rapide") == []


def test_comparaison_supports_preserve_les_ecarts_z():
    """Classique, arborescent et sans support s’obtiennent sans réécrire l’interface en mm."""
    from benchab.visualisation.comparaison_atelier import variante_intention

    params = {"spiral_mode": "0", "enable_support": "0", "support_type": "normal(auto)",
              "support_top_z_distance": "0.2", "support_bottom_z_distance": "0.2"}
    assert [m for _, m in variante_intention(params, False, "supports")] == [
        {"enable_support": "1"}, {"support_type": "tree(auto)", "enable_support": "1"},
    ]
    assert [m for _, m in variante_intention(params | {"enable_support": "1"}, False, "supports")] == [
        {"support_type": "tree(auto)"}, {"enable_support": "0"},
    ]


def test_haut_debit_exige_un_profil_de_meme_filament():
    """Seul un profil compatible peut relever le plafond déclaré en mm³/s."""
    from benchab.visualisation.comparaison_atelier import variante_intention

    debit = "filament_max_volumetric_speed"
    source = {
        "printer_settings_id": "Machine témoin", "spiral_mode": "0", debit: ["16"],
        "filament_settings_id": ["PLA témoin"], "filament_flow_ratio": ["0.96"],
        "nozzle_temperature": ["205"], "nozzle_temperature_initial_layer": ["215"],
    }
    profil = source | {debit: ["18"], "compatible_printers": ["Machine témoin"]}
    assert variante_intention(source, False, "haut-debit", profil_debit=profil)[0][1] == {debit: ["18"]}
    for autres in ({debit: ["14"]}, {"filament_settings_id": ["Autre PLA"]}, {"nozzle_temperature": ["220"]},
                   {"compatible_printers": ["Autre machine"]}):
        assert variante_intention(source, False, "haut-debit", profil_debit=profil | autres) == []


def test_constat_confirme_par_preparation_sans_modifier_la_source(tmp_path, monkeypatch):
    """Seule la préparation exacte peut recevoir un constat, échappé dans l’historique privé."""
    import pytest

    from benchab.qualite.empreintes import sha256_fichier
    from benchab.visualisation import atelier_preparation
    from benchab.visualisation.atelier_local import observations_atelier_html

    projet = tmp_path / "projets" / "temoin"
    projet.mkdir(parents=True)
    source = projet / "source.3mf"
    source.write_text("source témoin", encoding="utf-8")
    sha = sha256_fichier(source)
    monkeypatch.setattr(atelier_preparation, "_reference_projet", lambda *_: (source, sha, "P01"))
    monkeypatch.setattr(atelier_preparation, "_lire_3mf", lambda _: (
        [("Boîte", trimesh.creation.box(), 1, {})], {"filament_settings_id": ["PLA témoin"]}, [], False,
    ))
    arguments = ("Boîte · 1", "decollement", None, None, "Coin <soulevé>")
    with pytest.raises(ValueError, match="préparation a changé"):
        atelier_preparation.enregistrer_observation(tmp_path, "temoin", "P01", "0" * 64, *arguments)
    assert not (projet / "preuves").exists()
    constat = atelier_preparation.enregistrer_observation(tmp_path, "temoin", "P01", sha, *arguments)
    assert constat["plaque"] == 1 and constat["causalite"] == "NON VÉRIFIÉ"
    assert constat["profils_filament_declares"] == ["PLA témoin"]
    assert sha256_fichier(source) == sha
    historique = observations_atelier_html(projet)
    assert f'observations-{constat["id"]}' in historique
    assert "Coin &lt;soulevé&gt;" in historique
