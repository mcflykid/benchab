# Poursuite de la roadmap après 1.2.0

## Objectif et méthode

Poursuivre toutes les issues réalisables, les dépendances de la roadmap et les
défauts découverts jusqu'à leur résolution vérifiée. Les observations physiques
indisponibles restent explicitement à obtenir ; aucun résultat n'est inventé.
Les projets, préparations, profils installés et anciennes connaissances restent
intacts. Les changements passent par PR, revue indépendante et CI verte.

## Premier lot 1.3.0

- #103 : constructeur commun du contexte des listes filament dans le dictionnaire
  typé ; remplacer les constructions locales sans changer les identités, l'ordre
  ni les résidus. Mono, multi, cardinalité absente/ambiguë et suffixes qualifiés.
- #109 : extraire les avertissements du format slice_info préalablement qualifié
  à partir d'une source primaire ; conserver code, message, plaque et origine.
  Les codes inconnus restent visibles et ne prouvent aucune compatibilité physique.
- #111 : preuves structurées de recul/recouvrement, méthode et repère explicites,
  mesures numériques recalculées et contradiction bloquante avant acquittement.
  Une géométrie non mesurable reste NON VÉRIFIÉE ; aucune justification textuelle
  ne neutralise une contradiction mesurée.
- #125 : le booléen de conformité suit le verdict de l'agrégateur d'envoi.
  Un contrôle commun bloquant ne peut pas hériter d'une conformité historique.

Ces tâches utilisent des fichiers distincts ; le raccordement à l'envoi et à la
CLI se fait après intégration. Aucun réglage du slicer n'est lu ni transformé
hors du dictionnaire typé. Les archives passent par le lecteur borné existant.

## Vérification et suite

Pour chaque tâche : test de régression rouge, implémentation minimale, tests
ciblés et annotations françaises ; puis suite complète sur la candidate figée,
témoins S/M/L concernés, revue indépendante et traitement des retours. La version
mineure est publiée après CI verte, avec conservation et sauvegarde vérifiées.

La suite reprend les fondations #122/#123, la provenance #36 et #38, le contrôle
machine #71 et les dépendances slicer #80/#81/#82/#89, puis les parcours et
l'entretien décrits dans ROADMAP.md. Le suivi vivant des 64 issues garde les
critères d'origine et distingue code réalisable, qualification numérique,
recette humaine, données manquantes et validation physique.
