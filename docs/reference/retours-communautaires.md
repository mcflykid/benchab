# Retours communautaires

Le [registre public versionné](../../benchab/reference/retours-communautaires.json)
conserve deux signalements relus dans le dépôt d’Anycubic Slicer Next :
[l’affectation de débit entre slots, issue #9](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/issues/9)
et [l’association de filament personnalisé, issue #4](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/issues/4).
Les observations sont paraphrasées. Les pièces jointes ne sont pas recopiées.

L’issue #9 a été ouverte le 18 septembre 2026 ; elle décrit un effet rapporté
entre le premier slot et les autres dans Anycubic Slicer Next 2.0.0.3 sous
Windows 11 sur Kobra S1 Combo. Un autre commentaire rapporte un comportement
similaire. L’issue #4 a été ouverte le 20 août 2026 ; son auteur rapporte un
changement d’affectation avec profils de filament personnalisés à partir de
1.3.9.4, et compare son scénario à 1.3.9.3, sur Kobra S1 Combo avec ACE Pro et
Windows 11. Les deux entrées ont été relues le 11 novembre 2026.

Ces entrées sont des **hypothèses externes** contextualisées, pas des faits
reproduits par BENCHAB. Elles ne donnent aucun réglage à recopier, n’adoptent
aucune valeur automatiquement et ne modifient pas le verdict U25. Le registre
indique les conditions déclarées, l’observation résumée et les limites de la
source pour chaque signalement.

La comparabilité des conditions s’examine avant de départager les sources.
Une fois les cas comparables, l’ordre documentaire est : mesure locale,
observation locale, information fabricant, hypothèse externe. Cet ordre est un
repère d’examen ; il ne prouve pas à lui seul une causalité ni une validité
physique. `benchab.connaissances.retours_communautaires.priorite_documentaire`
encode ces quatre rangs et ne renvoie aucun rang lorsque la comparabilité n’a
pas été établie.

La vérification locale du format d’export des profils utilisateur a porté sur
16 paires fichier/INFO et a recensé les clés documentaires `base_id`,
`setting_id`, `sync_info`, `updated_time` et `user_id`. Cette observation de
format est conservée dans les preuves privées ; ce registre n’importe ni
n’exporte aucun profil et ne publie aucune valeur de ces clés.
