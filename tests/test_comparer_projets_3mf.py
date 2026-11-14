"""Comparaison préparatoire de palettes entre archives synthétiques."""

import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from benchab.cli import main as cli_main
from benchab.controles.comparer_projets_3mf import comparer


def _archive(path, settings):
    """Écrit uniquement les réglages fictifs utiles au contrat de comparaison."""
    with ZipFile(path, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(settings))


def _archive_peinte(
    path, *, dialect="paint_color", couleurs=("1", "2"), triangles=((0, 1, 2), (0, 2, 3)),
    x1="1", composant=False, relation_ambigue=False, objet_supplementaire=False,
    type_relation=None, cible_relation="/3D/3dmodel.model", mode_relation=None,
    qname_relation=False, proprietes_objet=None, groupes_materiau=(), proprietes_facettes=None, transform=None,
):
    """Écrit un modèle OPC synthétique direct, avec deux facettes et un dialecte explicite."""
    from benchab.imports3d.lecture_3mf import NS, NS_REL, REL_MODELE

    modele = ET.Element(NS + "model", unit="millimeter")
    ressources = ET.SubElement(modele, NS + "resources")
    for identifiant, bases in groupes_materiau:
        groupe = ET.SubElement(ressources, NS + "basematerials", id=identifiant)
        for nom, couleur in bases:
            ET.SubElement(groupe, NS + "base", name=nom, displaycolor=couleur)
    objet = ET.SubElement(ressources, NS + "object", id="1", type="model")
    for cle, valeur in (proprietes_objet or {}).items():
        objet.set(cle, valeur)
    if composant:
        ET.SubElement(objet, NS + "components")
    maillage = ET.SubElement(objet, NS + "mesh")
    sommets = ET.SubElement(maillage, NS + "vertices")
    for x, y, z in (("0", "0", "0"), (x1, "0", "0"), ("1", "1", "0"), ("0", "1", "0")):
        ET.SubElement(sommets, NS + "vertex", x=x, y=y, z=z)
    faces = ET.SubElement(maillage, NS + "triangles")
    for index, face in enumerate(triangles):
        attributs = dict(zip(("v1", "v2", "v3"), map(str, face), strict=True))
        if couleurs[index] is not None:
            nom = (
                "paint_color" if dialect == "paint_color"
                else "{http://schemas.slic3r.org/3mf/2017/06}mmu_segmentation"
            )
            attributs[nom] = couleurs[index]
        if proprietes_facettes is not None:
            attributs.update(proprietes_facettes[index])
        ET.SubElement(faces, NS + "triangle", **attributs)
    if objet_supplementaire:
        ET.SubElement(ressources, NS + "object", id="2", type="model")
    construction = ET.SubElement(modele, NS + "build")
    item = ET.SubElement(construction, NS + "item", objectid="1")
    if transform is not None:
        item.set("transform", transform)
    relations = ET.Element(NS_REL + "Relationships")
    attributs_relation = {"Id": "rModel", "Type": type_relation or REL_MODELE, "Target": cible_relation}
    if mode_relation is not None:
        attributs_relation["TargetMode"] = mode_relation
    qname = "{urn:autre}Relationship" if qname_relation else NS_REL + "Relationship"
    ET.SubElement(relations, qname, attributs_relation)
    if relation_ambigue:
        ET.SubElement(
            relations,
            NS_REL + "Relationship",
            Id="rAlias",
            Type=REL_MODELE,
            Target="/3D/3dmodel.model",
        )
    with ZipFile(path, "w") as archive:
        archive.writestr("_rels/.rels", ET.tostring(relations))
        archive.writestr("3D/3dmodel.model", ET.tostring(modele))
        archive.writestr("Metadata/project_settings.config", json.dumps({
            "filament_settings_id": ["Profil A", "Profil B"],
            "filament_colour": ["#112233", "#AABBCC"],
        }))


def test_palette_comparee_par_identite_et_ordre_sans_affirmer_peinture(tmp_path):
    """Compare des couleurs par slot seulement si les identités et leur ordre concordent."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    slots = ["Filament A", "Filament B"]
    _archive(source, {
        "filament_settings_id": slots,
        "filament_colour": ["#112233", "#AABBCC"],
        "top_shell_layers": "4",
    })
    _archive(candidate, {
        "filament_settings_id": slots,
        "filament_colour": ["#112233", "#AABBCC"],
        "top_shell_layers": "4",
    })

    rapport = comparer(source, candidate)

    assert rapport["palette"]["statut"] == "PASS"
    assert [slot["identite_profil"] for slot in rapport["palette"]["associations_profils"]] == slots
    assert rapport["palette"]["attribution_physique_piece"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["reglages"]["statut"] == "PASS"
    assert rapport["peinture"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_palette_ordre_different_est_signale_et_attribution_reste_non_verifiee(tmp_path):
    """Une même couleur sous des identités réordonnées ne prouve pas la conservation du slot."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {
        "filament_settings_id": ["Filament A", "Filament B"],
        "filament_colour": ["#112233", "#AABBCC"],
    })
    _archive(candidate, {
        "filament_settings_id": ["Filament B", "Filament A"],
        "filament_colour": ["#112233", "#AABBCC"],
    })

    rapport = comparer(source, candidate)

    assert rapport["palette"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["associations_profils"] == []
    assert rapport["palette"]["association_profil_couleur"]["statut"] == "NON VÉRIFIÉ"
    assert {d["cle"] for d in rapport["reglages"]["differences"]} == {"filament_settings_id"}


def test_absence_de_reglage_est_signalee_et_sources_restent_intactes(tmp_path):
    """Une clé absente du candidat est visible et les deux fichiers restent identiques."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"top_shell_layers": "4"})
    _archive(candidate, {})
    octets_source, octets_candidate = source.read_bytes(), candidate.read_bytes()

    rapport = comparer(source, candidate)

    assert any(d["cle"] == "top_shell_layers" for d in rapport["reglages"]["absences"])
    assert len(rapport["sha256"]["source"]) == 64
    assert len(rapport["sha256"]["candidat"]) == 64
    assert source.read_bytes() == octets_source
    assert candidate.read_bytes() == octets_candidate


def test_cli_est_un_diagnostic_json_sans_ouvrir_un_atelier(tmp_path, capsys, monkeypatch):
    """Le contrôle compare deux fixtures sans Base, mesure, sortie fichier ou réglage modifié."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112233"]})
    _archive(candidate, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112233"]})
    monkeypatch.setattr("benchab.cli.Base", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Base")))

    code = cli_main(["controle", "comparer-projets-3mf", str(source), str(candidate)])

    sortie = capsys.readouterr()
    assert code == 2  # Les propriétés Core et la compatibilité restent NON VÉRIFIÉES.
    assert sortie.err == ""
    rapport = json.loads(sortie.out)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["statut"] == "PASS"


def test_palette_signale_couleur_litteralement_differente(tmp_path):
    """Une couleur modifiée au même slot est signalée sans conversion ou normalisation."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112233"]})
    _archive(candidate, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112234"]})

    rapport = comparer(source, candidate)

    assert rapport["palette"]["statut"] == "WARN"
    assert rapport["palette"]["association_profil_couleur"]["statut"] == "PASS"
    assert rapport["palette"]["differences_litterales"][0]["statut"] == "DIFFÉRENT"
    assert rapport["reglages"]["statut"] == "WARN"


def test_profils_repetes_n_interpretent_pas_les_couleurs_comme_slots(tmp_path):
    """Des identifiants de profil répétés ne désignent pas des slots physiques distincts."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    ids = ["Profil partagé", "Profil partagé"]
    _archive(source, {"filament_settings_id": ids, "filament_colour": ["#112233", "#AABBCC"]})
    _archive(candidate, {"filament_settings_id": ids, "filament_colour": ["#112234", "#AABBCC"]})

    rapport = comparer(source, candidate)

    assert rapport["palette"]["association_profil_couleur"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["associations_profils"] == []
    assert rapport["palette"]["differences_litterales"][0]["index_litteral"] == 1
    assert rapport["palette"]["differences_litterales"][0]["statut"] == "DIFFÉRENT"
    assert rapport["palette"]["attribution_physique_piece"]["statut"] == "NON VÉRIFIÉ"


def test_couleur_vide_ou_mal_formee_reste_visible_sans_association(tmp_path):
    """Une chaîne vide ou qui n’est pas un RGB hexadécimal n’est pas un slot coloré qualifié."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"filament_settings_id": ["Profil A"], "filament_colour": [""]})
    _archive(candidate, {"filament_settings_id": ["Profil A"], "filament_colour": ["#GGGGGG"]})

    rapport = comparer(source, candidate)

    assert rapport["palette"]["association_profil_couleur"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["associations_profils"] == []
    assert rapport["palette"]["differences_litterales"] == [{
        "index_litteral": 1, "couleur_source": "", "couleur_candidat": "#GGGGGG",
        "present_source": True, "present_candidat": True, "statut": "DIFFÉRENT",
    }]


def test_couleur_residuelle_est_signalee_sans_devenir_un_slot(tmp_path):
    """Une couleur en trop reste un résidu littéral, jamais un filament actif inventé."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"filament_settings_id": ["Profil A"], "filament_colour": ["#112233", "#AABBCC"]})
    _archive(candidate, {"filament_settings_id": ["Profil A"], "filament_colour": ["#112233"]})

    rapport = comparer(source, candidate)

    assert rapport["palette"]["association_profil_couleur"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["associations_profils"] == []
    assert rapport["palette"]["couleurs_residuelles"]["source"] == [{
        "index_litteral": 2, "valeur": "#AABBCC", "statut": "NON VÉRIFIÉ",
    }]
    assert rapport["palette"]["differences_litterales"][0]["statut"] == "DIFFÉRENT"


def test_absence_commune_de_palette_ne_devient_pas_une_difference(tmp_path):
    """Deux palettes absentes ne sont ni égales ni différentes, et restent NON VÉRIFIÉES."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {})
    _archive(candidate, {})

    rapport = comparer(source, candidate)

    assert rapport["palette"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["differences_litterales"] == []
    assert rapport["palette"]["association_profil_couleur"]["statut"] == "NON VÉRIFIÉ"


def test_peinture_litterale_signale_une_couleur_facette_retiree(tmp_path):
    """Une propriété retirée d’une facette est visible si l’identité et l’ordre restent établis."""
    source, candidate = tmp_path / "source.3mf", tmp_path / "candidate.3mf"
    _archive_peinte(source)
    _archive_peinte(candidate, couleurs=("1", None))

    rapport = comparer(source, candidate)

    peinture = rapport["peinture"]
    assert peinture["extensions_asn_prusa"]["statut"] == "WARN"
    assert peinture["extensions_asn_prusa"]["dialecte"] == "paint_color"
    assert peinture["extensions_asn_prusa"]["differences_litterales"] == [{
        "objet_id": "1", "facette": 2, "attribut": "paint_color",
        "source": "2", "candidat": None, "present_source": True, "present_candidat": False,
        "statut": "DIFFÉRENT",
    }]


def test_peinture_non_comparee_si_ordre_des_facettes_change(tmp_path):
    """La même couleur au même index ne suffit pas lorsque les facettes ont été permutées."""
    source, candidate = tmp_path / "source.3mf", tmp_path / "candidate.3mf"
    _archive_peinte(source)
    _archive_peinte(candidate, triangles=((0, 2, 3), (0, 1, 2)), couleurs=("1", "2"))

    rapport = comparer(source, candidate)

    assert rapport["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"
    assert "ordre" in rapport["peinture"]["extensions_asn_prusa"]["raison"].lower()
    assert rapport["peinture"]["extensions_asn_prusa"]["differences_litterales"] == []


def test_peinture_non_comparee_entre_qnames_de_dialectes_differents(tmp_path):
    """Les valeurs ASN paint_color et Prusa mmu_segmentation ne sont jamais transcodées."""
    source, candidate = tmp_path / "source.3mf", tmp_path / "candidate.3mf"
    _archive_peinte(source, dialect="paint_color")
    _archive_peinte(candidate, dialect="mmu_segmentation")

    rapport = comparer(source, candidate)

    assert rapport["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"
    assert "dialecte" in rapport["peinture"]["extensions_asn_prusa"]["raison"].lower()
    assert rapport["peinture"]["extensions_asn_prusa"]["differences_litterales"] == []


def test_peinture_non_comparee_si_coordonnees_changees(tmp_path):
    """Un maillage qui a bougé n’est pas pris pour la même surface peinte."""
    source, candidate = tmp_path / "source.3mf", tmp_path / "candidate.3mf"
    _archive_peinte(source)
    _archive_peinte(candidate, x1="1.0")

    rapport = comparer(source, candidate)

    assert rapport["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"
    assert "coordonnées" in rapport["peinture"]["extensions_asn_prusa"]["raison"].lower()


def test_peinture_non_verifiee_si_composant_objet_ou_relation_ambigue(tmp_path):
    """Composants, objets multiples et StartPart aliasés ne sont jamais choisis arbitrairement."""
    for nom, options in (
        ("composant", {"composant": True}),
        ("objet", {"objet_supplementaire": True}),
        ("relation", {"relation_ambigue": True}),
    ):
        source, candidate = tmp_path / f"{nom}-source.3mf", tmp_path / f"{nom}-candidate.3mf"
        _archive_peinte(source, **options)
        _archive_peinte(candidate)

        rapport = comparer(source, candidate)

        assert rapport["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"


def test_perte_ou_ajout_integral_de_peinture_est_warn(tmp_path):
    """Un dialecte unique d’un seul côté qualifie la perte ou l’ajout littéral intégral."""
    for nom, couleurs_source, couleurs_candidat, attendu in (
        ("perte", ("1", "2"), (None, None), "peinture absente du candidat"),
        ("ajout", (None, None), ("1", "2"), "peinture absente de la source"),
    ):
        source, candidate = tmp_path / f"{nom}-source.3mf", tmp_path / f"{nom}-candidate.3mf"
        _archive_peinte(source, couleurs=couleurs_source)
        _archive_peinte(candidate, couleurs=couleurs_candidat)

        peinture = comparer(source, candidate)["peinture"]["extensions_asn_prusa"]

        assert peinture["statut"] == "WARN"
        assert peinture["cause"] == attendu
        assert len(peinture["differences_litterales"]) == 2


def test_proprietes_core_vides_ne_sont_pas_signalees_presentes(tmp_path):
    """Les dictionnaires de facette vides ne prouvent aucune propriété Core."""
    source, candidate = tmp_path / "source.3mf", tmp_path / "candidate.3mf"
    _archive_peinte(source)
    _archive_peinte(candidate, proprietes_objet={"pid": "2", "pindex": "0"})

    peinture = comparer(source, candidate)["peinture"]["proprietes_standard_3mf"]

    assert peinture["present_source"] is False
    assert peinture["present_candidat"] is True
    assert peinture["statut"] == "NON VÉRIFIÉ"


def test_relation_opc_non_qualifiee_ou_cible_dangereuse_reste_non_verifiee(tmp_path):
    """La résolution directe refuse QName, type, chemin et mode externe non qualifiés."""
    for nom, options in (
        ("qname", {"qname_relation": True}),
        ("type", {"type_relation": "urn:other/3dmodel"}),
        ("parent", {"cible_relation": "/3D/../3D/3dmodel.model"}),
        ("backslash", {"cible_relation": "3D\\3dmodel.model"}),
        ("uri", {"cible_relation": "https://example.invalid/model"}),
        ("external", {"mode_relation": "External"}),
    ):
        source, candidate = tmp_path / f"{nom}-source.3mf", tmp_path / f"{nom}-candidate.3mf"
        _archive_peinte(source, **options)
        _archive_peinte(candidate)

        assert comparer(source, candidate)["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"


def test_facette_aux_indices_repetes_reste_non_verifiee(tmp_path):
    """Une triangle dégénéré répété n’établit pas une identité de facette qualifiée."""
    source, candidate = tmp_path / "source.3mf", tmp_path / "candidate.3mf"
    _archive_peinte(source, triangles=((0, 0, 2), (0, 2, 3)))
    _archive_peinte(candidate, triangles=((0, 0, 2), (0, 2, 3)))

    assert comparer(source, candidate)["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"


def test_cle_inconnue_reste_non_verifiee_meme_identique(tmp_path):
    """Une clé hors dictionnaire n’est pas présentée comme préservée par égalité brute."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"vendor_painted_faces": ["01", "02"]})
    _archive(candidate, {"vendor_painted_faces": ["01", "02"]})

    rapport = comparer(source, candidate)

    assert rapport["reglages"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["reglages"]["non_verifies"][0]["cle"] == "vendor_painted_faces"
    assert rapport["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"


def test_absence_de_config_projet_reste_non_verifiee(tmp_path):
    """Un 3MF sans réglages projet n’est pas traité comme un projet vide équivalent."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
    _archive(candidate, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112233"]})

    rapport = comparer(source, candidate)

    assert rapport["reglages"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["peinture"]["statut"] == "NON VÉRIFIÉ"


def test_cli_archive_illisible_est_refuse_sans_traceback(tmp_path, capsys):
    """Une archive qui n’est pas un ZIP retourne le code technique 2 sans traceback."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    source.write_bytes(b"not a zip")
    _archive(candidate, {})

    code = cli_main(["controle", "comparer-projets-3mf", str(source), str(candidate)])

    sortie = capsys.readouterr()
    assert code == 2
    assert sortie.out == ""
    assert "comparaison 3MF refusée" in sortie.err
    assert "Traceback" not in sortie.err


def test_cli_resume_conserve_le_rapport_complet_et_les_sources(tmp_path, capsys):
    """Une sortie courte garde le chemin et les écarts sans perdre le rapport détaillé."""
    from benchab import cli

    source, candidat = (tmp_path / f"{nom}.3mf" for nom in ("source", "candidate"))
    _archive(source, {"top_shell_layers": "3", "vendor_inconnu": "A"})
    _archive(candidat, {"top_shell_layers": "5", "vendor_inconnu": "B"})
    avant = (source.read_bytes(), candidat.read_bytes())
    prive = tmp_path / "prive"
    prive.mkdir()
    sortie = prive / "rapport.json"
    commande = ["--prive", str(prive), "controle", "comparer-projets-3mf", str(source), str(candidat)]
    assert cli.main([*commande, "--resume", "--sortie", str(sortie)]) == 2
    resultat = capsys.readouterr()
    assert not resultat.err
    court = json.loads(resultat.out)
    complet = json.loads(sortie.read_bytes())
    assert court["rapport"] == str(sortie)
    assert court["reglages"]["differences"] == len(complet["reglages"]["differences"])
    assert court["reglages"]["non_verifies"] == len(complet["reglages"]["non_verifies"])
    assert court["statut"] == complet["statut"] == "NON VÉRIFIÉ"
    assert "vendor_inconnu" not in resultat.out and "vendor_inconnu" in sortie.read_text()
    assert (source.read_bytes(), candidat.read_bytes()) == avant
    empreinte = sortie.read_bytes()
    assert cli.main([*commande, "--resume", "--sortie", str(sortie)]) == 2
    assert capsys.readouterr().err and sortie.read_bytes() == empreinte


def test_core_compare_materiaux_resolus_malgre_reindexation(tmp_path):
    """Un autre id ou index ne change pas le matériau déclaré par facette."""
    source, candidat = tmp_path / "source.3mf", tmp_path / "candidat.3mf"
    _archive_peinte(
        source, groupes_materiau=(("2", [("Structure", "#112233"), ("Interface", "#abcdef")]),),
        proprietes_objet={"pid": "2", "pindex": "0"},
        proprietes_facettes=({}, {"p1": "1"}),
    )
    _archive_peinte(
        candidat, groupes_materiau=(("7", [("Interface", "#ABCDEFFF"), ("Structure", "#112233FF")]),),
        proprietes_objet={"pid": "7", "pindex": "1"},
        proprietes_facettes=({"p1": "1"}, {"p1": "0", "p2": "0", "p3": "0"}),
    )
    rapport = comparer(source, candidat)
    core = rapport["peinture"]["proprietes_standard_3mf"]
    assert core["statut"] == "PASS"
    assert core["differences"] == [] and core["facettes_comparees"] == 2
    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_core_alerte_si_un_materiau_de_facette_disparait(tmp_path):
    """La perte de l’attribution Core reste visible sans déduire un filament."""
    source, candidat = tmp_path / "source.3mf", tmp_path / "candidat.3mf"
    _archive_peinte(
        source, groupes_materiau=(("2", [("Structure", "#112233"), ("Interface", "#AABBCC")]),),
        proprietes_objet={"pid": "2", "pindex": "0"},
        proprietes_facettes=({}, {"p1": "1"}),
    )
    _archive_peinte(
        candidat, groupes_materiau=(("2", [("Structure", "#112233")]),),
        proprietes_objet={"pid": "2", "pindex": "0"},
    )
    core = comparer(source, candidat)["peinture"]["proprietes_standard_3mf"]
    assert core["statut"] == "WARN"
    assert core["differences"] == [{
        "portee": "facette", "facette": 2,
        "source": {"nom": "Interface", "srgb_affichage": "#AABBCCFF"},
        "candidat": {"nom": "Structure", "srgb_affichage": "#112233FF"},
    }]


@pytest.mark.parametrize(
    ("groupes", "objet", "triangle"),
    [
        ((("1", [("A", "#000000")]),), {"pid": "1", "pindex": "0"}, {}),
        ((("2", [("A", "#000000")]),), {"pid": "2", "pindex": "2"}, {}),
        ((("2", [("A", "#000000"), ("B", "#FFFFFF")]),), {"pid": "2", "pindex": "0"}, {"p1": "0", "p2": "1"}),
        ((("2", [("A", "#000000")]),), {}, {"pid": "2", "p1": "0"}),
    ],
)
def test_core_refuse_alias_indices_gradients_et_defauts_absents(tmp_path, groupes, objet, triangle):
    """Une référence ambiguë ou un gradient interdit ne peut produire un résultat qualifié."""
    source, candidat = tmp_path / "source.3mf", tmp_path / "candidat.3mf"
    _archive_peinte(
        source, groupes_materiau=groupes, proprietes_objet=objet, proprietes_facettes=({}, triangle),
    )
    _archive_peinte(candidat)
    core = comparer(source, candidat)["peinture"]["proprietes_standard_3mf"]
    assert core["statut"] == "NON VÉRIFIÉ" and core["differences"] == []


def test_core_herite_index_objet_avec_groupe_de_facette_explicite(tmp_path):
    """Un pid de facette remplace le groupe et conserve l’index par défaut sans p1."""
    source, candidat = tmp_path / "source.3mf", tmp_path / "candidat.3mf"
    groupes = (("2", [("Structure", "#112233")]), ("3", [("Interface", "#AABBCC")]))
    objet = {"pid": "2", "pindex": "0"}
    _archive_peinte(
        source, groupes_materiau=groupes, proprietes_objet=objet,
        proprietes_facettes=({"pid": "3", "p2": "0", "p3": "0"}, {}),
    )
    _archive_peinte(
        candidat, groupes_materiau=groupes, proprietes_objet=objet,
        proprietes_facettes=({"pid": "3", "p1": "0"}, {}),
    )
    core = comparer(source, candidat)["peinture"]["proprietes_standard_3mf"]
    assert core["statut"] == "PASS" and core["differences"] == []


def test_peinture_et_materiau_sur_deux_placements_translates_identiques(tmp_path):
    """Les différences de déclaration restent visibles à placement direct égal."""
    source, candidat = tmp_path / "source.3mf", tmp_path / "candidat.3mf"
    translation = "1 0 0 0 1 0 0 0 1 10 -2.5 0"
    base = dict(transform=translation, groupes_materiau=(("3", (("PLA", "#123456"),)),))
    _archive_peinte(source, **base, proprietes_objet={"pid": "3", "pindex": "0"})
    _archive_peinte(candidat, **base, couleurs=("1", None))
    rapport = comparer(source, candidat)
    assert rapport["peinture"]["extensions_asn_prusa"]["statut"] == "WARN"
    assert rapport["peinture"]["proprietes_standard_3mf"]["statut"] == "WARN"
    identique = comparer(source, source)
    assert identique["peinture"]["extensions_asn_prusa"]["statut"] == "PASS"
    assert identique["peinture"]["proprietes_standard_3mf"]["statut"] == "PASS"


@pytest.mark.parametrize("matrice", [
    "1 0 0 0 1 0 0 0 1 11 -2.5 0",
    "1 1e-999 0 0 1 0 0 0 1 10 -2.5 0",
    "1 0 0 0 1 0 0 0 1 NaN -2.5 0",
    "1 0 0 0 1 0 0 0 1 1e+99999999999999 -2.5 0",
    "1 0 0 0 1 0 0 0 1 10 -2.5",
    "1 0 0 0 1 0 0 0 1 1000000000.000000000000000000000001 -2.5 0",
    "1\u00a00 0 0 1 0 0 0 1 10 -2.5 0",
])
def test_placement_different_ou_matrice_non_qualifiee(tmp_path, matrice):
    """Un déplacement ou une matrice hors contrat interdit la comparaison."""
    source, candidat = tmp_path / "source.3mf", tmp_path / "candidat.3mf"
    translation = "1 0 0 0 1 0 0 0 1 10 -2.5 0"
    _archive_peinte(source, transform=translation)
    _archive_peinte(candidat, transform=matrice)
    assert comparer(source, candidat)["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"
    if matrice != "1 0 0 0 1 0 0 0 1 11 -2.5 0":
        assert comparer(candidat, candidat)["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"


def _archive_assemblee(
    path, *, couleur="1", translation="1 0 0 0 1 0 0 0 1 2 0 0", variante=None,
    seconde=False, couleur2="2", materiau2="#654321",
):
    """Construit un assemblage Core à une ou deux feuilles dans le même StartPart."""
    from benchab.imports3d.lecture_3mf import NS, NS_REL, REL_MODELE

    racine = ET.Element(NS + "model", unit="millimeter")
    ressources = ET.SubElement(racine, NS + "resources")
    groupe = ET.SubElement(ressources, NS + "basematerials", id="3")
    ET.SubElement(groupe, NS + "base", name="PLA", displaycolor="#123456")
    feuille = ET.SubElement(ressources, NS + "object", id="1", type="model", pid="3", pindex="0")
    mesh = ET.SubElement(feuille, NS + "mesh")
    sommets = ET.SubElement(mesh, NS + "vertices")
    for x, y, z in (("0", "0", "0"), ("1", "0", "0"), ("0", "1", "0"), ("0", "0", "1")):
        ET.SubElement(sommets, NS + "vertex", x=x, y=y, z=z)
    faces = ET.SubElement(mesh, NS + "triangles")
    for i, indices in enumerate(((0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3))):
        face = ET.SubElement(faces, NS + "triangle", dict(zip(("v1", "v2", "v3"), map(str, indices), strict=True)))
        if i == 0 and couleur is not None:
            face.set("paint_color", couleur)
    parent = ET.SubElement(ressources, NS + "object", id="2", type="model")
    composants = ET.SubElement(parent, NS + "components")
    composant = ET.SubElement(composants, NS + "component", objectid="1", transform=translation)
    if seconde:
        ET.SubElement(groupe, NS + "base", name="PLA-B", displaycolor=materiau2)
        autre = ET.fromstring(ET.tostring(feuille))
        autre.set("id", "4")
        autre.set("pindex", "1")
        premiere = autre.find(f".//{NS}triangle")
        premiere.attrib.pop("paint_color", None)
        if couleur2 is not None:
            premiere.set("paint_color", couleur2)
        ressources.insert(list(ressources).index(parent), autre)
        nouveau = ET.SubElement(composants, NS + "component", objectid="4", transform="1 0 0 0 1 0 0 0 1 5 0 0")
        if variante == "ordre-inverse":
            composants.remove(nouveau)
            composants.insert(0, nouveau)
        elif variante == "deux-fois":
            nouveau.set("objectid", "1")
        elif variante == "extension-sommet":
            autre.find(f".//{NS}vertex").set("{urn:fixture}inconnue", "1")
    if variante == "p:path":
        composant.set("{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path", "/3D/objet.model")
    elif variante == "propriete-parent":
        parent.set("pid", "3")
        parent.set("pindex", "0")
    elif variante == "cycle":
        composant.set("objectid", "2")
    elif variante == "membre-repete":
        ET.SubElement(composants, NS + "component", objectid="1")
    elif variante == "ressource-ambigue":
        groupe.set("id", "1")
    build = ET.SubElement(racine, NS + "build")
    ET.SubElement(build, NS + "item", objectid="2", transform="1 0 0 0 1 0 0 0 1 10 0 0")
    relations = ET.Element(NS_REL + "Relationships")
    ET.SubElement(relations, NS_REL + "Relationship", Id="rModel", Type=REL_MODELE, Target="/3D/3dmodel.model")
    ns_types = "{http://schemas.openxmlformats.org/package/2006/content-types}"
    types = ET.Element(ns_types + "Types")
    ET.SubElement(types, ns_types + "Default", Extension="rels",
                  ContentType="application/vnd.openxmlformats-package.relationships+xml")
    ET.SubElement(types, ns_types + "Default", Extension="model",
                  ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml")
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", ET.tostring(types))
        archive.writestr("_rels/.rels", ET.tostring(relations))
        archive.writestr("3D/3dmodel.model", ET.tostring(racine))


def test_assemblage_a_une_feuille_compare_la_peinture_et_le_materiau(tmp_path):
    """La même feuille garde ses matériaux et expose sa peinture perdue."""
    source, candidat = tmp_path / "a.3mf", tmp_path / "b.3mf"
    _archive_assemblee(source)
    _archive_assemblee(candidat, couleur=None)
    avant = source.read_bytes(), candidat.read_bytes()
    resultats = comparer(source, candidat)
    assert resultats["peinture"]["extensions_asn_prusa"]["statut"] == "WARN"
    assert resultats["peinture"]["proprietes_standard_3mf"]["statut"] == "PASS"
    assert resultats["peinture"]["statut"] == "NON VÉRIFIÉ"

    from benchab.imports3d.lecture_3mf import lire

    for archive in (source, candidat):
        scene = lire(archive, identites=True)
        assert scene.bounds.tolist() == [[12.0, 0.0, 0.0], [13.0, 1.0, 1.0]]
        maillage, = scene.geometry.values()
        assert maillage.is_watertight and maillage.volume == pytest.approx(1 / 6)
    assert (source.read_bytes(), candidat.read_bytes()) == avant


@pytest.mark.parametrize("variante", ["p:path", "propriete-parent", "cycle", "membre-repete", "ressource-ambigue"])
def test_assemblage_hors_sous_cas_reste_non_verifie(tmp_path, variante):
    """Une source ambiguë n’est pas qualifiée, même comparée à elle-même."""
    source = tmp_path / "source.3mf"
    _archive_assemblee(source, variante=variante)
    rapport = comparer(source, source)["peinture"]
    assert rapport["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["proprietes_standard_3mf"]["statut"] == "NON VÉRIFIÉ"


def test_translation_differente_de_la_feuille_refuse_la_correspondance(tmp_path):
    """Deux maillages identiques ne suffisent pas si le composant se déplace."""
    source, candidat = tmp_path / "a.3mf", tmp_path / "b.3mf"
    _archive_assemblee(source)
    _archive_assemblee(candidat, translation="1 0 0 0 1 0 0 0 1 3 0 0")
    rapport = comparer(source, candidat)["peinture"]
    assert rapport["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["proprietes_standard_3mf"]["statut"] == "NON VÉRIFIÉ"


def test_assemblage_local_compare_les_deux_feuilles_et_localise_la_difference(tmp_path):
    """Deux feuilles explicites conservent leurs indices de facettes distincts."""
    source, candidat = tmp_path / "a.3mf", tmp_path / "b.3mf"
    _archive_assemblee(source, seconde=True)
    _archive_assemblee(candidat, seconde=True, couleur2=None, materiau2="#101010")
    avant = source.read_bytes(), candidat.read_bytes()
    resultat = comparer(source, candidat)
    peinture = resultat["peinture"]["extensions_asn_prusa"]
    core = resultat["peinture"]["proprietes_standard_3mf"]
    assert resultat["statut"] == "NON VÉRIFIÉ"
    assert peinture["statut"] == "WARN"
    assert [(d["objet_id"], d["facette"]) for d in peinture["differences_litterales"]] == [("4", 1)]
    assert core["statut"] == "WARN" and core["facettes_comparees"] == 8
    assert {d["objet_id"] for d in core["differences"]} == {"4"}
    assert {d["facette"] for d in core["differences"] if d["portee"] == "facette"} == {1, 2, 3, 4}

    from benchab.imports3d.lecture_3mf import lire

    for archive in (source, candidat):
        scene = lire(archive, identites=True)
        assert scene.bounds.tolist() == [[12.0, 0.0, 0.0], [16.0, 1.0, 1.0]]
        assert len(scene.geometry) == 2 and all(m.is_volume for m in scene.geometry.values())
    assert (source.read_bytes(), candidat.read_bytes()) == avant


@pytest.mark.parametrize("variante", ["ordre-inverse", "deux-fois", "extension-sommet"])
def test_assemblage_a_deux_feuilles_refuse_ordre_change_ou_reference_repetee(tmp_path, variante):
    """Des maillages égaux n’autorisent ni réindexation ni doublon."""
    source, candidat = tmp_path / "a.3mf", tmp_path / "b.3mf"
    _archive_assemblee(source, seconde=True)
    _archive_assemblee(candidat, seconde=True, variante=variante)
    rapport = comparer(source, candidat)["peinture"]
    assert rapport["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["proprietes_standard_3mf"]["statut"] == "NON VÉRIFIÉ"


@pytest.fixture
def projets_production(tmp_path):
    """Déplace une feuille synthétique vers la partie liée du même ZIP, comme Orca."""
    from benchab.imports3d.lecture_3mf import NS, NS_REL, PROD, REL_MODELE

    source = tmp_path / "direct.3mf"
    _archive_peinte(source, dialect="prusa")

    def construire(*, couleurs=("1", "2"), cible="/3D/Objects/part.model", relation="interne",
                   translation="0", unite="millimeter", espace=PROD, extension="prod"):
        candidat = tmp_path / "production.3mf"
        with ZipFile(source) as archive:
            membres = {nom: archive.read(nom) for nom in archive.namelist()}
        modele = ET.fromstring(membres["3D/3dmodel.model"])
        ressources = modele.find(NS + "resources")
        feuille = ressources[0]
        for triangle, couleur in zip(feuille.iter(NS + "triangle"), couleurs, strict=True):
            cle, = set(triangle.attrib) - {"v1", "v2", "v3"}
            if couleur is None:
                triangle.attrib.pop(cle)
            else:
                triangle.set(cle, couleur)
        ressources.remove(feuille)
        sous_modele = ET.Element(NS + "model", unit=unite, requiredextensions=extension)
        ET.SubElement(sous_modele, NS + "resources").append(feuille)
        ET.SubElement(sous_modele, NS + "build")
        parent = ET.SubElement(ressources, NS + "object", id="2", type="model")
        composants = ET.SubElement(parent, NS + "components")
        ET.SubElement(composants, NS + "component", {
            espace + "path": cible, "objectid": "1", "transform": f"1 0 0 0 1 0 0 0 1 {translation} 0 0",
        })
        modele.find(NS + "build")[0].set("objectid", "2")
        modele.set("requiredextensions", extension)
        relations = ET.Element(NS_REL + "Relationships")
        if relation != "absente":
            for i in range(2 if relation == "double" else 1):
                ET.SubElement(relations, NS_REL + "Relationship", {
                    "Id": f"r{i}", "Type": REL_MODELE, "Target": cible,
                    "TargetMode": "External" if relation == "externe" else "Internal",
                })

        def xml(element):
            return ET.tostring(element).replace(b"<ns0:model ", b'<ns0:model xmlns:prod="' +
                                               PROD[1:-1].encode() + b'" ', 1)

        membres.update({
            "3D/3dmodel.model": xml(modele),
            "3D/Objects/part.model": xml(sous_modele),
            "3D/_rels/3dmodel.model.rels": ET.tostring(relations),
        })
        with ZipFile(candidat, "w") as archive:
            for nom, contenu in membres.items():
                archive.writestr(nom, contenu)
        return comparer(source, candidat)["peinture"]["extensions_asn_prusa"]

    return construire


@pytest.mark.parametrize(("couleurs", "statut"), [(("1", "2"), "PASS"), ((None, None), "WARN")])
def test_composant_production_compare_la_peinture_des_memes_facettes(projets_production, couleurs, statut):
    """Deux marques sont vérifiées avec la même feuille et la même pose effective."""
    preuve = projets_production(couleurs=couleurs)
    assert preuve["statut"] == statut
    assert len(preuve["differences_litterales"]) == (2 if statut == "WARN" else 0)
    if statut == "WARN":
        assert [ecart["facette"] for ecart in preuve["differences_litterales"]] == [1, 2]
        assert all(not ecart["present_candidat"] for ecart in preuve["differences_litterales"])


@pytest.mark.parametrize("options", [
    {"relation": "absente"}, {"relation": "double"}, {"relation": "externe"},
    {"cible": "/3D/Objects/%2e%2e/part.model"}, {"cible": "/3D/Objects/manquante.model"},
    {"espace": "{urn:autre}"}, {"extension": "inconnue"},
    {"translation": "0.0001"}, {"unite": "inch"},
])
def test_composant_production_ambigu_reste_non_verifie(projets_production, options):
    """Le ZIP, le lien OPC, la feuille et la pose doivent tous être qualifiés."""
    assert projets_production(**options)["statut"] == "NON VÉRIFIÉ"
