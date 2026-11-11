"""Fixtures de remplacement 3MF direct, sans profil ni archive d'atelier."""

import hashlib
import io
import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np
import pytest
import trimesh

from benchab import cli
from benchab.archives_3mf import Archive3MF
from benchab.fabrication import remplacement_maillage
from benchab.fabrication.remplacement_maillage import remplacer
from benchab.imports3d.lecture_3mf import lire
from benchab.imports3d.maillage_indexe import sauvegarder

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_MODEL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
MEMBRE = "3D/3dmodel.model"


def _objet(parent, identifiant, mesh, facteur=1.0):
    """Ajoute un volume triangulé synthétique en millimètres."""
    objet = ET.SubElement(parent, f"{{{CORE}}}object", id=str(identifiant), type="model")
    geometrique = ET.SubElement(objet, f"{{{CORE}}}mesh")
    sommets = ET.SubElement(geometrique, f"{{{CORE}}}vertices")
    triangles = ET.SubElement(geometrique, f"{{{CORE}}}triangles")
    for sommet in mesh.vertices:
        ET.SubElement(
            sommets, f"{{{CORE}}}vertex",
            **dict(zip(("x", "y", "z"), (format(float(x / facteur), ".9g") for x in sommet), strict=True)),
        )
    for face in mesh.faces:
        ET.SubElement(
            triangles, f"{{{CORE}}}triangle",
            **dict(zip(("v1", "v2", "v3"), map(str, face), strict=True)),
        )
    return objet


def _source(
    path, *, partage=False, gcode=False, doublon_build=False, relation_ambigue=False, path_nu=False,
    unite="millimeter", metadata_apres_mesh=False, commentaire=False, instruction=False,
    extension_namespace=False, slice_config=False, startpart=True, requiredextensions=False,
    core_extension=False, materiau=False,
):
    """Construit un 3MF minimal à deux objets imprimables avec un StartPart OPC."""
    facteur = {"millimeter": 1.0, "centimeter": 10.0}[unite]
    racine = ET.Element(f"{{{CORE}}}model", unit=unite)
    ressources = ET.SubElement(racine, f"{{{CORE}}}resources")
    cube = trimesh.creation.box((10, 12, 14))
    cible = _objet(ressources, "1", cube, facteur)
    if metadata_apres_mesh:
        cible[0].tail = "\n    "
        meta = ET.SubElement(cible, f"{{{CORE}}}metadata", name="note")
        meta.text = "conservée après le mesh"
    _objet(ressources, "2", trimesh.creation.box((4, 6, 8)), facteur)
    if partage:
        assemblage = ET.SubElement(ressources, f"{{{CORE}}}object", id="3", type="model")
        composants = ET.SubElement(assemblage, f"{{{CORE}}}components")
        ET.SubElement(composants, f"{{{CORE}}}component", objectid="1")
    construction = ET.SubElement(racine, f"{{{CORE}}}build")
    ET.SubElement(
        construction, f"{{{CORE}}}item", objectid="1",
        transform="1 0 0 0 1 0 0 0 1 " + " ".join(str(v / facteur) for v in (11, 13, 17)),
    )
    if doublon_build:
        ET.SubElement(construction, f"{{{CORE}}}item", objectid="1")
    if path_nu:
        construction[-1].set("path", "/3D/3dmodel.model")
    if extension_namespace:
        racine.set("{urn:fixture-extension}custom", "not-qualified")
    if requiredextensions:
        racine.set("requiredextensions", "m")
    if core_extension:
        ET.SubElement(ressources, f"{{{CORE}}}basematerials", id="7")
    if materiau:
        triangle = racine.find(f".//{{{CORE}}}triangle")
        triangle.set("pid", "7")
        triangle.set("p1", "0")
    ET.SubElement(construction, f"{{{CORE}}}item", objectid="2")
    ET.register_namespace("", CORE)
    modele = ET.tostring(racine, encoding="utf-8", xml_declaration=True)
    if commentaire:
        modele = modele.replace(b"<model ", b"<!-- must not be dropped -->\n<model ")
    if instruction:
        modele = modele.replace(b"?>", b"?><?fixture processing?>", 1)
    with ZipFile(path, "w") as archive:
        archive.comment = b"3mf fixture comment"
        archive.writestr("[Content_Types].xml", b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        relations = (
            f'<Relationships xmlns="{REL}"><Relationship Id="r1" Type="{REL_MODEL}" '
            f'Target="/{MEMBRE}"/>'
            + (f'<Relationship Id="r2" Type="{REL_MODEL}" Target="/{MEMBRE}"/>' if relation_ambigue else "")
            + '</Relationships>'
        )
        if startpart:
            archive.writestr("_rels/.rels", relations.encode())
        archive.writestr(MEMBRE, modele)
        archive.writestr("Metadata/notes.txt", b"fixture")
        if gcode:
            archive.writestr("Metadata/plate_1.gcode", b"; sliced witness\n")
        if slice_config:
            archive.writestr("Metadata/slice_info.config", b"{}")
    return cube


@pytest.mark.parametrize("unite", ["millimeter", "centimeter"])
def test_remplacement_unique_preserve_instances_membres_et_source(tmp_path, unite):
    """L'objet demandé change en repère local et tous les autres contenus restent prouvés."""
    source, candidat, sortie = (tmp_path / nom for nom in ("source.3mf", "candidat.npz", "sortie.3mf"))
    original = _source(source, unite=unite)
    nouveau = trimesh.creation.icosphere(subdivisions=1, radius=5)
    nouveau.apply_translation([1.25, -2.0, 0.5])
    sauvegarder(candidat, nouveau, prive=tmp_path)
    sha_source = hashlib.sha256(source.read_bytes()).hexdigest()
    autres_avant = {"[Content_Types].xml": None, "_rels/.rels": None, "Metadata/notes.txt": None}
    with Archive3MF(source) as archive:
        for nom in autres_avant:
            autres_avant[nom] = hashlib.sha256(archive.lire(nom)).hexdigest()

    rapport = remplacer(
        source, candidat, sortie, objet_id="1", source_sha256=sha_source,
        repere="local_objet_mm", decalage_mm=(2.0, -1.0, 0.0), prive=tmp_path,
    )

    assert rapport["statut"] == "PASS"
    assert rapport["source_sha256"] == sha_source
    assert hashlib.sha256(source.read_bytes()).hexdigest() == sha_source
    scene = lire(sortie, identites=True)
    assert len(scene.geometry) == 2
    cible = next(
        mesh for mesh, identite in zip(scene.geometry.values(), scene.metadata["identites_import"], strict=True)
        if identite["objet_id_racine"] == "1"
    )
    attendu = nouveau.copy()
    attendu.apply_translation([2.0, -1.0, 0.0])
    attendu.apply_translation([11.0, 13.0, 17.0])
    assert np.allclose(cible.bounds, attendu.bounds)
    with ZipFile(source) as archive_source:
        source_modele_sha = hashlib.sha256(archive_source.read(MEMBRE)).hexdigest()
    with Archive3MF(sortie) as archive:
        assert archive.commentaire == b"3mf fixture comment"
        archive.verifier()
        assert hashlib.sha256(archive.lire(MEMBRE)).hexdigest() != source_modele_sha
        assert {nom: hashlib.sha256(archive.lire(nom)).hexdigest() for nom in autres_avant} == autres_avant
    assert rapport["topologie_avant"]["faces"] == len(original.faces)
    assert rapport["topologie_apres"]["faces"] == len(nouveau.faces)


def test_remplacement_preserve_la_position_xml_du_maillage(tmp_path):
    """Le maillage remplacé garde son rang parmi les enfants de l'objet."""
    source, candidat, sortie = (tmp_path / nom for nom in ("source.3mf", "candidat.npz", "sortie.3mf"))
    _source(source, metadata_apres_mesh=True)
    sauvegarder(candidat, trimesh.creation.box((3, 4, 5)), prive=tmp_path)

    remplacer(
        source, candidat, sortie, objet_id="1", source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        repere="local_objet_mm", decalage_mm=(0, 0, 0), prive=tmp_path,
    )

    with Archive3MF(sortie) as archive:
        racine = archive.xml(MEMBRE)
    objet = next(o for o in racine.findall(f"{{{CORE}}}resources/{{{CORE}}}object") if o.get("id") == "1")
    assert [enfant.tag.rsplit("}", 1)[-1] for enfant in objet] == ["mesh", "metadata"]
    assert objet[0].tail == "\n    "


@pytest.mark.parametrize("options", [
    {"commentaire": True}, {"instruction": True}, {"extension_namespace": True},
    {"slice_config": True}, {"startpart": False},
])
def test_refuse_xml_et_archive_tranchee_non_qualifies_sans_sortie(tmp_path, options):
    """Les commentaires/PI/extensions, traces slicer et StartPart absent ne sont pas normalisés."""
    source, candidat, sortie = (tmp_path / nom for nom in ("source.3mf", "candidat.npz", "sortie.3mf"))
    _source(source, **options)
    sauvegarder(candidat, trimesh.creation.box((2, 2, 2)), prive=tmp_path)

    with pytest.raises(ValueError):
        remplacer(
            source, candidat, sortie, objet_id="1", source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            repere="local_objet_mm", decalage_mm=(0, 0, 0), prive=tmp_path,
        )

    assert not sortie.exists()


@pytest.mark.parametrize("options", [
    {"requiredextensions": True}, {"core_extension": True}, {"materiau": True},
])
def test_refuse_extensions_et_proprietes_core_non_qualifiees(tmp_path, options):
    """Les extensions Core et propriétés de matériau ne peuvent pas survivre au remplacement par oubli."""
    source, candidat, sortie = (tmp_path / nom for nom in ("source.3mf", "candidat.npz", "sortie.3mf"))
    _source(source, **options)
    sauvegarder(candidat, trimesh.creation.box((2, 2, 2)), prive=tmp_path)

    with pytest.raises(ValueError):
        remplacer(
            source, candidat, sortie, objet_id="1", source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            repere="local_objet_mm", decalage_mm=(0, 0, 0), prive=tmp_path,
        )

    assert not sortie.exists()


def test_budgets_xml_et_faces_refuses_avant_ecriture(tmp_path, monkeypatch):
    """Les plafonds de mémoire du remplacement sont explicites et testés sur de petites fixtures."""
    source, candidat, sortie = (tmp_path / nom for nom in ("source.3mf", "candidat.npz", "sortie.3mf"))
    _source(source)
    sauvegarder(candidat, trimesh.creation.box((2, 2, 2)), prive=tmp_path)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(remplacement_maillage, "_MAX_XML", 128)
    with pytest.raises(ValueError, match="XML"):
        remplacer(
            source, candidat, sortie, objet_id="1", source_sha256=sha,
            repere="local_objet_mm", decalage_mm=(0, 0, 0), prive=tmp_path,
        )
    assert not sortie.exists()

    monkeypatch.setattr(remplacement_maillage, "_MAX_XML", 64 * 1024 * 1024)
    monkeypatch.setattr(remplacement_maillage, "_MAX_FACES", 10, raising=False)
    monkeypatch.setattr(
        remplacement_maillage, "_topologie",
        lambda *_: pytest.fail("Le plafond du candidat doit précéder les métriques"),
    )
    with pytest.raises(ValueError, match="Budget"):
        remplacer(
            source, candidat, sortie, objet_id="1", source_sha256=sha,
            repere="local_objet_mm", decalage_mm=(0, 0, 0), prive=tmp_path,
        )
    assert not sortie.exists()


def test_empreinte_non_calculee_d_un_objet_non_cible_refuse(tmp_path, monkeypatch):
    """Une paire de valeurs None ne peut pas faire passer la comparaison d'un autre objet."""
    source, candidat, sortie = (tmp_path / nom for nom in ("source.3mf", "candidat.npz", "sortie.3mf"))
    _source(source)
    sauvegarder(candidat, trimesh.creation.box((2, 2, 2)), prive=tmp_path)
    empreinte_originale = remplacement_maillage.empreinte_geometrie

    def empreinte_bornee(mesh, max_faces=3_000_000):
        if mesh.bounds[0, 0] < -0.5:
            return {"sha256_triangles": None, "statut": "NON_CALCULEE_BUDGET"}
        return empreinte_originale(mesh, max_faces)

    monkeypatch.setattr(remplacement_maillage, "empreinte_geometrie", empreinte_bornee)
    with pytest.raises(ValueError, match="[Ee]mpreinte géométrique non calculée"):
        remplacer(
            source, candidat, sortie, objet_id="1", source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            repere="local_objet_mm", decalage_mm=(0, 0, 0), prive=tmp_path,
        )
    assert not sortie.exists()


@pytest.mark.parametrize("partage,gcode", [(True, False), (False, True)])
def test_refuse_objet_partage_ou_gcode_embarque_sans_sortie(tmp_path, partage, gcode):
    """Les références multiples et la sortie tranchée embarquée ne sont pas réécrites."""
    source, candidat, sortie = (tmp_path / nom for nom in ("source.3mf", "candidat.npz", "sortie.3mf"))
    _source(source, partage=partage, gcode=gcode)
    sauvegarder(candidat, trimesh.creation.box((2, 2, 2)), prive=tmp_path)
    with pytest.raises(ValueError):
        remplacer(
            source, candidat, sortie, objet_id="1", source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            repere="local_objet_mm", decalage_mm=(0, 0, 0), prive=tmp_path,
        )
    assert not sortie.exists()


@pytest.mark.parametrize("options", [
    {"doublon_build": True}, {"relation_ambigue": True}, {"path_nu": True},
])
def test_refuse_references_build_ou_opc_ambigues(tmp_path, options):
    """Une cible répétée ou une résolution OPC/path non qualifiée ne peut être remplacée."""
    source, candidat, sortie = (tmp_path / nom for nom in ("source.3mf", "candidat.npz", "sortie.3mf"))
    _source(source, **options)
    sauvegarder(candidat, trimesh.creation.box((2, 2, 2)), prive=tmp_path)
    with pytest.raises(ValueError):
        remplacer(
            source, candidat, sortie, objet_id="1", source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            repere="local_objet_mm", decalage_mm=(0, 0, 0), prive=tmp_path,
        )
    assert not sortie.exists()


def test_refuse_sha_ou_repere_absent_et_destination_existante(tmp_path):
    """La déclaration doit lier le 3MF et le repère explicite avant toute publication."""
    source, candidat, sortie = (tmp_path / nom for nom in ("source.3mf", "candidat.npz", "sortie.3mf"))
    _source(source)
    sauvegarder(candidat, trimesh.creation.box((2, 2, 2)), prive=tmp_path)
    commun = {"objet_id": "1", "repere": "local_objet_mm", "decalage_mm": (0, 0, 0), "prive": tmp_path}
    with pytest.raises(ValueError):
        remplacer(source, candidat, sortie, source_sha256="0" * 64, **commun)
    with pytest.raises(ValueError):
        remplacer(
            source, candidat, sortie, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            **{**commun, "repere": "plateau_mm"},
        )
    sortie.write_bytes("preuve déjà présente".encode())
    with pytest.raises(FileExistsError):
        remplacer(
            source, candidat, sortie, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), **commun,
        )


def test_sortie_neuve_dans_une_preparation_figee_refusee(tmp_path):
    """Même un nouveau fichier ne peut pas être ajouté à une préparation figée."""
    source, candidat = tmp_path / "source.3mf", tmp_path / "candidat.npz"
    _source(source)
    sauvegarder(candidat, trimesh.creation.box((2, 3, 4)), prive=tmp_path)
    projet = tmp_path / "projets/fixture"
    figee = projet / "preparations/P01"
    figee.mkdir(parents=True)
    (projet / "qualite.json").write_text("{}")
    (figee / "PREPARATION.json").write_text("{}")
    sortie = figee / "resultat.3mf"
    avant = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="Préparation figée"):
        remplacer(
            source, candidat, sortie, objet_id="1", source_sha256=avant,
            repere="local_objet_mm", decalage_mm=(0, 0, 0), prive=tmp_path,
        )
    assert list(figee.iterdir()) == [figee / "PREPARATION.json"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == avant


def test_commande_cli_reste_hors_sqlite_et_publie_neuf(tmp_path, monkeypatch, capsys):
    """Le parcours CLI ne crée ni n'ouvre l'atelier SQLite et livre seulement une copie privée neuve."""
    source, candidat = tmp_path / "source.3mf", tmp_path / "candidat.npz"
    _source(source)
    sauvegarder(candidat, trimesh.creation.box((2, 3, 4)), prive=tmp_path)
    sortie = tmp_path / "prive" / "resultat.3mf"
    (tmp_path / "prive").mkdir()

    def base_interdite(*args, **kwargs):
        raise AssertionError("La commande ne doit pas ouvrir SQLite")

    monkeypatch.setattr(cli, "Base", base_interdite)
    code = cli.main([
        "--prive", str(tmp_path / "prive"), "fabrication", "remplacer-maillage", str(source), str(candidat),
        "--sortie", str(sortie), "--objet-id", "1", "--source-sha256", hashlib.sha256(source.read_bytes()).hexdigest(),
        "--repere", "local_objet_mm", "--decalage-mm", "0", "0", "0",
    ])
    rapport = json.loads(capsys.readouterr().out)
    assert code == 0
    assert rapport["statut"] == "PASS"
    assert sortie.is_file()


def test_membre_non_cible_altere_pendant_copie_refuse_la_publication(tmp_path, monkeypatch):
    """Une altération d'un autre membre est détectée avant que la sortie soit livrée."""
    source, candidat, sortie = (tmp_path / nom for nom in ("source.3mf", "candidat.npz", "sortie.3mf"))
    _source(source)
    sauvegarder(candidat, trimesh.creation.box((2, 2, 2)), prive=tmp_path)
    avant = hashlib.sha256(source.read_bytes()).hexdigest()
    zip_original = remplacement_maillage.ZipFile

    class _FluxAlterant:
        """Tamponne un membre non ciblé et en modifie les octets à la fermeture."""

        def __init__(self, flux):
            self.flux = flux
            self.tampon = io.BytesIO()

        def __enter__(self):
            return self

        def write(self, donnees):
            return self.tampon.write(donnees)

        def __exit__(self, *exc):
            with self.flux as sortie_zip:
                sortie_zip.write(self.tampon.getvalue() + b"alteration")

    class _ZipAlterant(zip_original):
        """Injecte une altération uniquement pendant la copie de la fixture."""

        def open(self, nom, mode="r", *args, **kwargs):
            flux = super().open(nom, mode, *args, **kwargs)
            if mode == "w" and getattr(nom, "filename", nom) == "Metadata/notes.txt":
                return _FluxAlterant(flux)
            return flux

    monkeypatch.setattr(remplacement_maillage, "ZipFile", _ZipAlterant)
    with pytest.raises(ValueError, match="Membre non ciblé altéré"):
        remplacer(
            source, candidat, sortie, objet_id="1", source_sha256=avant,
            repere="local_objet_mm", decalage_mm=(0, 0, 0), prive=tmp_path,
        )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == avant
    assert not sortie.exists()


def _source_assemblage(path, *, partage=False, imbrique=False, build_feuille=False, metadonnees=False):
    """Crée deux feuilles Core dans un StartPart, avec rotations et translations en cm."""
    racine = ET.Element(f"{{{CORE}}}model", unit="centimeter")
    ressources = ET.SubElement(racine, f"{{{CORE}}}resources")
    _objet(ressources, "1", trimesh.creation.box((10, 12, 14)), 10)
    _objet(ressources, "2", trimesh.creation.box((4, 6, 8)), 10)
    assemblage = ET.SubElement(ressources, f"{{{CORE}}}object", id="3", type="model")
    composants = ET.SubElement(assemblage, f"{{{CORE}}}components")
    premier = "1"
    if imbrique:
        autre = ET.SubElement(ressources, f"{{{CORE}}}object", id="4", type="model")
        niveau = ET.SubElement(autre, f"{{{CORE}}}components")
        ET.SubElement(niveau, f"{{{CORE}}}component", objectid="1")
        premier = "4"
    ET.SubElement(composants, f"{{{CORE}}}component", objectid=premier, transform="1 0 0 0 1 0 0 0 1 .5 .2 .3")
    ET.SubElement(composants, f"{{{CORE}}}component", objectid="2", transform="1 0 0 0 1 0 0 0 1 2 0 0")
    if partage:
        ET.SubElement(composants, f"{{{CORE}}}component", objectid="1")
    build = ET.SubElement(racine, f"{{{CORE}}}build")
    ET.SubElement(build, f"{{{CORE}}}item", objectid="3", transform="0 1 0 -1 0 0 0 0 1 1 2 3")
    if build_feuille:
        ET.SubElement(build, f"{{{CORE}}}item", objectid="1")
    ET.register_namespace("", CORE)
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("_rels/.rels", f'<Relationships xmlns="{REL}"><Relationship Id="r1" '
                         f'Type="{REL_MODEL}" Target="/{MEMBRE}"/></Relationships>')
        archive.writestr(MEMBRE, ET.tostring(racine, encoding="utf-8", xml_declaration=True))
        if metadonnees:
            archive.writestr("Metadata/model_settings.config", "<config/>")


@pytest.mark.parametrize("via_cli", [False, True])
def test_feuille_unique_preserve_autre_volume_et_transforms(tmp_path, capsys, via_cli):
    """La feuille déclarée change, l’autre volume et les matrices restent identiques."""
    source, candidat, sortie = (tmp_path / n for n in ("source.3mf", "volume.npz", "sortie.3mf"))
    _source_assemblage(source)
    sauvegarder(candidat, trimesh.creation.box((2, 4, 6)), prive=tmp_path)
    sha_avant = hashlib.sha256(source.read_bytes()).hexdigest()
    if via_cli:
        assert cli.main([
            "--prive", str(tmp_path), "fabrication", "remplacer-maillage", str(source), str(candidat),
            "--sortie", str(sortie), "--objet-id", "3", "--volume-id", "1", "--source-sha256", sha_avant,
            "--repere", "local_objet_mm", "--decalage-mm", "1", "2", "3",
        ]) == 0
        rapport = json.loads(capsys.readouterr().out)
    else:
        rapport = remplacer(source, candidat, sortie, objet_id="3", volume_id="1", source_sha256=sha_avant,
                            repere="local_objet_mm", decalage_mm=(1, 2, 3), prive=tmp_path)
    assert rapport["volume_id"] == "1"
    assert rapport["indice_composant"] == rapport["indice_build"] == 1
    assert rapport["statut"] == "PASS"
    scene_avant, scene_apres = [lire(p, identites=True) for p in (source, sortie)]
    assert scene_avant.metadata["identites_import"] == scene_apres.metadata["identites_import"]
    volumes = [
        {i["objet_id_maillage"]: m for i, m in zip(s.metadata["identites_import"], s.geometry.values(), strict=True)}
        for s in (scene_avant, scene_apres)
    ]
    assert volumes[1]["1"].bounds.mean(axis=0).tolist() == pytest.approx([6, 26, 36])
    assert volumes[1]["1"].volume == pytest.approx(48)
    assert np.array_equal(volumes[0]["2"].vertices, volumes[1]["2"].vertices)
    assert np.array_equal(volumes[0]["2"].faces, volumes[1]["2"].faces)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == sha_avant


@pytest.mark.parametrize("options", [
    {"partage": True}, {"imbrique": True}, {"build_feuille": True}, {"metadonnees": True},
])
def test_feuille_ambigue_ou_imbriquee_refusee(tmp_path, options):
    """Un composant partagé, imbriqué ou également construit n’est jamais modifié."""
    source, candidat, sortie = (tmp_path / n for n in ("source.3mf", "volume.npz", "sortie.3mf"))
    _source_assemblage(source, **options)
    sauvegarder(candidat, trimesh.creation.box((2, 4, 6)), prive=tmp_path)
    avant = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        remplacer(source, candidat, sortie, objet_id="3", volume_id="1", source_sha256=avant,
                   repere="local_objet_mm", decalage_mm=(0, 0, 0), prive=tmp_path)
    assert not sortie.exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == avant


def test_deplacement_non_autorise_du_build_refuse_la_copie(tmp_path, monkeypatch):
    """Même avec un volume cible valide, une matrice d’instance modifiée empêche la publication."""
    source, candidat, sortie = (tmp_path / n for n in ("source.3mf", "volume.npz", "sortie.3mf"))
    _source_assemblage(source)
    sauvegarder(candidat, trimesh.creation.box((2, 4, 6)), prive=tmp_path)
    avant = hashlib.sha256(source.read_bytes()).hexdigest()
    serialiser = ET.tostring

    def deplacer(element, *args, **kwargs):
        brut = serialiser(element, *args, **kwargs)
        if kwargs.get("xml_declaration"):
            initial = b'transform="0 1 0 -1 0 0 0 0 1 1 2 3"'
            assert initial in brut
            return brut.replace(initial, b'transform="0 1 0 -1 0 0 0 0 1 9 2 3"')
        return brut

    monkeypatch.setattr(ET, "tostring", deplacer)
    with pytest.raises(ValueError, match="hors du maillage cible"):
        remplacer(source, candidat, sortie, objet_id="3", volume_id="1", source_sha256=avant,
                   repere="local_objet_mm", decalage_mm=(0, 0, 0), prive=tmp_path)
    assert not sortie.exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == avant
