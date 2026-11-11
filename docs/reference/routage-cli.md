# Délégation des commandes CLI

`benchab.cli` centralise les appels de modules homogènes avec `_deleguer` :
séparation du `--` initial, conversion du résultat en code entier et reprise du
code ou du message de `SystemExit`. Les espaces `connaissances`, `conception`,
`imports`, `visualisation`, `machine` et `references` sont décrits dans
`ROUTES_NAMESPACE` avec leurs préfixes, alias et options `--prive`/racine.
Les contrôles directs gardent leurs noms de commande et modules dans
`CONTROLES_DIRECTS`; seuls les contrôles qui l’exigent reçoivent aussi la racine
privée.

Les voies qui construisent un rapport, manipulent plusieurs sources, réécrivent
des arguments ou contrôlent explicitement l’atelier restent dans `executer` :
contrôles `routine`, `continuite-paroi`, `complet`, `envoi`, `publication`,
fabrication avec préparation/tranchage/envoi, opérations de données, votes,
séances, installation et réparations de vues. Elles gardent leurs options
typées, leurs textes de refus et leurs codes propres. La comparaison d’arbres
utilise la table `code_statut`, avec son contrat PASS → 0 et FAIL → 2.

Cette factorisation ne modifie ni les options publiques, ni l’aide déléguée, ni
le choix de l’atelier, ni le schéma de mesure. Les tests CLI vérifient les
arguments, modules, options, codes et messages transmis.
