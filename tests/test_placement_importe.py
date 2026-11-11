"""Contrat d’un placement déclaré à partir d’une source 3MF, sans G-code."""

import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from benchab.cli import main as main_benchab
from benchab.controles.placement import valider as valider_placement
from benchab.imports3d.lecture_3mf import lire as lire_3mf
from benchab.imports3d.placement import generer
from benchab.qualite.empreintes import sha256_fichier

NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _modele(path, items, *, composant=False, unite="millimeter"):
    """Construit une source 3MF synthétique avec des objets et instances identifiables."""
    racine = ET.Element(f"{{{NS}}}model", unit=unite)
    ressources = ET.SubElement(racine, f"{{{NS}}}resources")
    if composant:
        objet = ET.SubElement(ressources, f"{{{NS}}}object", id="20", type="model")
        composants = ET.SubElement(objet, f"{{{NS}}}components")
        ET.SubElement(
            composants, f"{{{NS}}}component", objectid="1",
            **{f"{{{PROD}}}path": "/3D/Objects/piece.model",
               "transform": "1 0 0 0 1 0 0 0 1 4 5 0"},
        )
    else:
        for ident in sorted({item["objectid"] for item in items}):
            objet = ET.SubElement(ressources, f"{{{NS}}}object", id=ident, type="model", name=f"Objet {ident}")
            _maillage(objet)
    build = ET.SubElement(racine, f"{{{NS}}}build")
    for item in items:
        ET.SubElement(build, f"{{{NS}}}item", **item)
    return ET.tostring(racine, encoding="utf-8")


def _maillage(objet):
    mesh = ET.SubElement(objet, f"{{{NS}}}mesh")
    sommets, faces = ET.SubElement(mesh, f"{{{NS}}}vertices"), ET.SubElement(mesh, f"{{{NS}}}triangles")
    for x, y, z in ((0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
                    (0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10)):
        ET.SubElement(sommets, f"{{{NS}}}vertex", x=str(x), y=str(y), z=str(z))
    for a, b, c in ((0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
                    (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)):
        ET.SubElement(faces, f"{{{NS}}}triangle", v1=str(a), v2=str(b), v3=str(c))


def _source(path, *, items=None, composant=False, unite="millimeter"):
    """Écrit les seuls membres géométriques nécessaires à la lecture 3MF."""
    items = items or [{"objectid": "1", "transform": "1 0 0 0 1 0 0 0 1 5 6 0"}]
    with ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", _modele(path, items, composant=composant, unite=unite))
        archive.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="{PKG_REL}"><Relationship Id="r0" Type="{REL}" '
            'Target="/3D/3dmodel.model"/></Relationships>',
        )
        if composant:
            racine = ET.Element(f"{{{NS}}}model", unit=unite)
            ressources = ET.SubElement(racine, f"{{{NS}}}resources")
            _maillage(ET.SubElement(ressources, f"{{{NS}}}object", id="1", type="model"))
            archive.writestr("3D/Objects/piece.model", ET.tostring(racine, encoding="utf-8"))
            archive.writestr(
                "3D/_rels/3dmodel.model.rels",
                f'<Relationships xmlns="{PKG_REL}"><Relationship Id="r1" Type="{REL}" '
                'Target="/3D/Objects/piece.model"/></Relationships>',
            )
    return path


def _declaration(source, objets, plaques=None):
    return {
        "schema": 1,
        "source_sha256": sha256_fichier(source),
        "repere_xy": "plateau",
        "plates": plaques or [{"index": 1, "name": "Plateau explicite"}],
        "objects": objets,
    }


def test_placement_3mf_est_valide_et_trace_la_transformation_xy(tmp_path):
    source = _source(tmp_path / "piece.3mf")
    avant = source.read_bytes()
    declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Pièce témoin"}])

    rapport = generer(source, declaration)

    assert rapport["statut"] == "PASS"
    placement = valider_placement(rapport["placement"])
    assert placement["source_sha256"] == sha256_fichier(source)
    assert placement["declaration_sha256"]
    assert placement["objects"][0]["xy"] == [5.0, 6.0]
    assert placement["objects"][0]["size"] == [10.0, 10.0, 10.0]
    trace = placement["objects"][0]["transformations"][0]["etapes"][0]
    assert trace["build_item"] == 1 and trace["objet_id"] == "1"
    assert trace["translation_mm"] == [5.0, 6.0, 0.0]
    assert source.read_bytes() == avant


def test_identites_et_traces_sont_optionnelles_pour_les_lecteurs_generiques(tmp_path):
    source = _source(tmp_path / "scene.3mf")

    scene_generique = lire_3mf(source)
    scene_tracee = lire_3mf(source, identites=True)

    assert "identites_import" not in scene_generique.metadata
    assert len(scene_tracee.metadata["identites_import"]) == 1


def test_placement_qualifie_un_3mf_genere_par_le_producteur_benchab(tmp_path):
    """Vérifie le StartPart et la relation .model.rels émis par le producteur, sans trancheur."""
    import trimesh

    from benchab.fabrication.anycubic import ecrire_3mf

    source = tmp_path / "producteur-benchab.3mf"
    maillage = trimesh.creation.box((10, 10, 10))
    maillage.apply_translation((5, 5, 5))
    plaque = [{"name": "Plaque A", "items": [{
        "name": "Pièce", "mesh": maillage, "x": 3, "y": 4, "extruder": 1,
    }]}]
    ecrire_3mf(plaque, {"filament_colour": ["#000000"], "top_shell_layers": "5"}, source)
    declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Pièce"}], [
        {"index": 1, "name": "Plaque A"},
    ])

    rapport = generer(source, declaration)

    assert rapport["statut"] == "PASS", rapport["raisons"]
    trace = rapport["placement"]["objects"][0]["transformations"][0]["etapes"]
    assert trace[0]["modele"] == "3D/3dmodel.model"
    assert trace[1]["modele_cible"] == "3D/Objects/pieces.model"


def test_composant_interne_transforme_est_trace_sans_a_platir_la_source(tmp_path):
    source = _source(
        tmp_path / "assemblage.3mf",
        items=[{"objectid": "20", "transform": "1 0 0 0 1 0 0 0 1 1 2 0"}],
        composant=True,
    )
    declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Assemblage"}])

    rapport = generer(source, declaration)

    assert rapport["statut"] == "PASS"
    objet = rapport["placement"]["objects"][0]
    assert objet["xy"] == [5.0, 7.0]
    etapes = objet["transformations"][0]["etapes"]
    assert len(etapes) == 2
    assert etapes[1]["modele_cible"] == "3D/Objects/piece.model"


def test_multi_plaque_exige_une_affectation_explicite_et_valide_les_liens(tmp_path):
    source = _source(
        tmp_path / "multi.3mf",
        items=[
            {"objectid": "1", "transform": "1 0 0 0 1 0 0 0 1 2 3 0"},
            {"objectid": "2", "transform": "1 0 0 0 1 0 0 0 1 30 40 0"},
        ],
    )
    declaration = _declaration(
        source,
        [
            {"build_item": 1, "plate": 1, "name": "Gauche"},
            {"build_item": 2, "plate": 2, "name": "Droite"},
        ],
        plaques=[{"index": 1, "name": "Plaque A"}, {"index": 2, "name": "Plaque B"}],
    )

    rapport = generer(source, declaration)

    assert rapport["statut"] == "PASS"
    placement = valider_placement(rapport["placement"])
    assert [plaque["name"] for plaque in placement["plates"]] == ["Plaque A", "Plaque B"]
    assert [objet["plate"] for objet in placement["objects"]] == [1, 2]


def test_liaison_absente_donne_un_placement_indetermine_sans_origine_inventee(tmp_path):
    source = _source(tmp_path / "incomplet.3mf")
    declaration = _declaration(source, [{"build_item": 9, "plate": 1, "name": "Inconnue"}])

    rapport = generer(source, declaration)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["placement"] is None
    assert "indisponible" in rapport["raisons"][0]


def test_placement_refuse_plusieurs_relations_de_modele_principal(tmp_path):
    source = _source(tmp_path / "ambigu.3mf")
    # Deux cibles internes candidates : l’ordre du ZIP ne peut choisir le repère.
    temporaire = tmp_path / "ambigu-relations.3mf"
    with ZipFile(source) as entree, ZipFile(temporaire, "w") as sortie:
        for info in entree.infolist():
            if info.filename != "_rels/.rels":
                sortie.writestr(info, entree.read(info.filename))
        sortie.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="{PKG_REL}"><Relationship Id="r1" Type="{REL}" '
            'Target="/3D/3dmodel.model"/><Relationship Id="r2" '
            f'Type="{REL}" Target="/Autre.model"/></Relationships>',
        )
        sortie.writestr("Autre.model", _modele(source, [{"objectid": "1"}]))
    source = temporaire
    declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Objet"}])

    rapport = generer(source, declaration)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["placement"] is None
    assert "startpart" in rapport["raisons"][0].lower()


def test_placement_refuse_modele_principal_sans_startpart_opc(tmp_path):
    source = _source(tmp_path / "sans-startpart.3mf")
    temporaire = tmp_path / "sans-startpart-reconstruit.3mf"
    with ZipFile(source) as entree, ZipFile(temporaire, "w") as sortie:
        for info in entree.infolist():
            if info.filename != "_rels/.rels":
                sortie.writestr(info, entree.read(info.filename))
    declaration = _declaration(temporaire, [{"build_item": 1, "plate": 1, "name": "Objet"}])

    rapport = generer(temporaire, declaration)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["placement"] is None
    assert "StartPart" in rapport["raisons"][0]
    assert lire_3mf(temporaire).extents.tolist() == [10.0, 10.0, 10.0]


def test_placement_refuse_p_path_sans_relation_opc_du_modele_source(tmp_path):
    source = _source(
        tmp_path / "sans-relation-part.3mf",
        items=[{"objectid": "20", "transform": "1 0 0 0 1 0 0 0 1 1 2 0"}],
        composant=True,
    )
    temporaire = tmp_path / "sans-relation-part-reconstruit.3mf"
    with ZipFile(source) as entree, ZipFile(temporaire, "w") as sortie:
        for info in entree.infolist():
            if info.filename != "3D/_rels/3dmodel.model.rels":
                sortie.writestr(info, entree.read(info.filename))
    declaration = _declaration(temporaire, [{"build_item": 1, "plate": 1, "name": "Assemblage"}])

    rapport = generer(temporaire, declaration)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["placement"] is None
    assert "relation opc" in rapport["raisons"][0].lower()
    assert lire_3mf(temporaire).extents.tolist() == [10.0, 10.0, 10.0]


def test_placement_refuse_p_path_inconnu_ou_multiple(tmp_path):
    for suffix, attributs in (
        ("inconnu", {"path": "/3D/Objects/piece.model"}),
        ("multiple", {f"{{{PROD}}}path": "/3D/Objects/piece.model",
                      "{urn:extension-test}path": "/3D/Objects/autre.model"}),
    ):
        source = _source(
            tmp_path / f"{suffix}.3mf",
            items=[{"objectid": "20", "transform": "1 0 0 0 1 0 0 0 1 1 2 0"}],
            composant=True,
        )
        temporaire = tmp_path / f"{suffix}-reconstruit.3mf"
        with ZipFile(source) as entree, ZipFile(temporaire, "w") as sortie:
            for info in entree.infolist():
                contenu = entree.read(info.filename)
                if info.filename == "3D/3dmodel.model":
                    racine = ET.fromstring(contenu)
                    composant = racine.find(f".//{{{NS}}}component")
                    for cle, valeur in attributs.items():
                        composant.set(cle, valeur)
                    contenu = ET.tostring(racine, encoding="utf-8")
                sortie.writestr(info, contenu)
        declaration = _declaration(temporaire, [{"build_item": 1, "plate": 1, "name": "Assemblage"}])

        rapport = generer(temporaire, declaration)

        assert rapport["statut"] == "NON VÉRIFIÉ"
        assert rapport["placement"] is None
        assert "p:path" in rapport["raisons"][0]


def test_placement_refuse_unite_differente_sur_cible_p_path(tmp_path):
    source = _source(
        tmp_path / "unite-mixte.3mf",
        items=[{"objectid": "20", "transform": "1 0 0 0 1 0 0 0 1 1 2 0"}],
        composant=True,
    )
    temporaire = tmp_path / "unite-mixte-reconstruit.3mf"
    with ZipFile(source) as entree, ZipFile(temporaire, "w") as sortie:
        for info in entree.infolist():
            contenu = entree.read(info.filename)
            if info.filename == "3D/Objects/piece.model":
                racine = ET.fromstring(contenu)
                racine.set("unit", "inch")
                contenu = ET.tostring(racine, encoding="utf-8")
            sortie.writestr(info, contenu)
    declaration = _declaration(temporaire, [{"build_item": 1, "plate": 1, "name": "Assemblage"}])

    rapport = generer(temporaire, declaration)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["placement"] is None
    assert "Unités mixtes" in rapport["raisons"][0]


def test_placement_refuse_indicateur_printable_malforme(tmp_path):
    source = _source(tmp_path / "printable.3mf")
    temporaire = tmp_path / "printable-reconstruit.3mf"
    with ZipFile(source) as entree, ZipFile(temporaire, "w") as sortie:
        for info in entree.infolist():
            contenu = entree.read(info.filename)
            if info.filename == "3D/3dmodel.model":
                racine = ET.fromstring(contenu)
                racine.find(f"{{{NS}}}build/{{{NS}}}item").set("printable", "yes")
                contenu = ET.tostring(racine, encoding="utf-8")
            sortie.writestr(info, contenu)
    declaration = _declaration(temporaire, [{"build_item": 1, "plate": 1, "name": "Objet"}])

    rapport = generer(temporaire, declaration)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["placement"] is None
    assert "printable" in rapport["raisons"][0]


def test_placement_refuse_path_non_qualifie_sur_build_item(tmp_path):
    source = _source(tmp_path / "item-path.3mf")
    temporaire = tmp_path / "item-path-reconstruit.3mf"
    with ZipFile(source) as entree, ZipFile(temporaire, "w") as sortie:
        for info in entree.infolist():
            contenu = entree.read(info.filename)
            if info.filename == "3D/3dmodel.model":
                racine = ET.fromstring(contenu)
                racine.find(f"{{{NS}}}build/{{{NS}}}item").set("path", "/3D/3dmodel.model")
                contenu = ET.tostring(racine, encoding="utf-8")
            sortie.writestr(info, contenu)
    declaration = _declaration(temporaire, [{"build_item": 1, "plate": 1, "name": "Objet"}])

    rapport = generer(temporaire, declaration)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["placement"] is None
    assert "p:path" in rapport["raisons"][0]


def test_placement_refuse_mode_opc_inconnu_et_replis_de_casse_ambigus(tmp_path):
    """Le placement exige une relation racine OPC au mode interne défini."""
    for cas in ("mode-opc", "mode-externe"):
        source = _source(tmp_path / f"{cas}.3mf")
        temporaire = tmp_path / f"{cas}-reconstruit.3mf"
        mode = "Inconnu" if cas == "mode-opc" else "External"
        with ZipFile(source) as entree, ZipFile(temporaire, "w") as sortie:
            for info in entree.infolist():
                if info.filename != "_rels/.rels":
                    sortie.writestr(info, entree.read(info.filename))
            sortie.writestr(
                "_rels/.rels",
                f'<Relationships xmlns="{PKG_REL}"><Relationship Id="r" Type="{REL}" '
                f'Target="/3D/3dmodel.model" TargetMode="{mode}"/></Relationships>',
            )
        source = temporaire
        declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Objet"}])
        rapport = generer(source, declaration)
        assert rapport["placement"] is None
        assert "opc" in rapport["raisons"][0].lower()
        assert lire_3mf(source).extents.tolist() == [10.0, 10.0, 10.0]


def test_lecteur_generique_ne_suit_pas_le_path_nu_d_un_build_item(tmp_path):
    """L’extension hors namespace ne change pas le chargement historique."""
    source = _source(tmp_path / "build-path-nu.3mf", items=[{"objectid": "1", "path": "/3D/alt.model"}])
    with ZipFile(source, "a") as archive:
        archive.writestr("3D/alt.model", _modele(source, [{"objectid": "1"}], unite="inch"))
    assert lire_3mf(source).extents.tolist() == [10.0, 10.0, 10.0]
    declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Objet"}])
    assert generer(source, declaration)["placement"] is None


def test_placement_refuse_unite_differente_sur_cible_du_build_item(tmp_path):
    """Le modèle relié à l’instance doit partager l’unité du modèle principal."""
    source = _source(tmp_path / "build-unites-mixtes.3mf", items=[
        {"objectid": "1", f"{{{PROD}}}path": "/3D/alt.model"},
    ])
    temporaire = tmp_path / "build-unites-mixtes-reconstruit.3mf"
    with ZipFile(source) as entree, ZipFile(temporaire, "w") as sortie:
        for info in entree.infolist():
            sortie.writestr(info, entree.read(info.filename))
        sortie.writestr("3D/alt.model", _modele(source, [{"objectid": "1"}], unite="inch"))
        sortie.writestr(
            "3D/_rels/3dmodel.model.rels",
            f'<Relationships xmlns="{PKG_REL}"><Relationship Id="r" Type="{REL}" '
            'Target="/3D/alt.model"/></Relationships>',
        )
    source = temporaire
    declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Objet"}])
    rapport = generer(source, declaration)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["placement"] is None
    assert "Unités mixtes" in rapport["raisons"][0]


def test_cli_publie_le_contrat_placement_sur_pass_et_aucun_faux_contrat_sur_nv(
    atelier_initialise, tmp_path, capsys
):
    source = _source(tmp_path / "piece.3mf")
    declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Pièce"}])
    fichier_declaration = tmp_path / "declaration.json"
    fichier_declaration.write_text(json.dumps(declaration), encoding="utf-8")
    sortie = atelier_initialise / "preuves" / "placement.json"
    sortie.parent.mkdir(parents=True)

    code = main_benchab([
        "--prive", str(atelier_initialise), "imports", "placement",
        str(source), str(fichier_declaration), "--sortie", str(sortie),
    ])

    stdout = json.loads(capsys.readouterr().out)
    placement = json.loads(sortie.read_text(encoding="utf-8"))
    assert code == stdout["code"] == 0
    assert placement["schema"] == 1
    assert placement["objects"][0]["name"] == "Pièce"
    assert stdout["placement"] == placement
    assert stdout["reglages_consultes"] is False

    invalide = _declaration(source, [{"build_item": 9, "plate": 1, "name": "Inconnue"}])
    fichier_declaration.write_text(json.dumps(invalide), encoding="utf-8")
    sortie_nv = atelier_initialise / "preuves" / "placement-nv.json"
    code_nv = main_benchab([
        "--prive", str(atelier_initialise), "imports", "placement",
        str(source), str(fichier_declaration), "--sortie", str(sortie_nv),
    ])
    stdout_nv = json.loads(capsys.readouterr().out)
    assert code_nv == stdout_nv["code"] == 2
    assert stdout_nv["placement"] is None
    assert not sortie_nv.exists()


def test_declaration_perimee_est_non_verifiee_sans_placer(tmp_path):
    source = _source(tmp_path / "modifiee.3mf")
    declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Pièce"}])
    source.write_bytes(source.read_bytes() + b" ")

    rapport = generer(source, declaration)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["placement"] is None
    assert "SHA" in rapport["raisons"][0]


def test_cli_ecrit_le_rapport_neuf_dans_le_prive(atelier_initialise, tmp_path, capsys):
    source = _source(tmp_path / "piece.3mf")
    declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Pièce"}])
    fichier_declaration = tmp_path / "declaration.json"
    fichier_declaration.write_text(json.dumps(declaration), encoding="utf-8")
    sortie = atelier_initialise / "preuves" / "placement.json"
    sortie.parent.mkdir(parents=True)

    code = main_benchab([
        "--prive", str(atelier_initialise), "imports", "placement",
        str(source), str(fichier_declaration), "--sortie", str(sortie),
    ])

    console = json.loads(capsys.readouterr().out)
    placement = json.loads(sortie.read_text(encoding="utf-8"))
    assert code == console["code"] == 0
    assert placement["objects"][0]["source_instance"]["build_item"] == 1
    assert console["placement"] == placement
