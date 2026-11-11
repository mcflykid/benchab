# Graphe de code et choix des tests (#34)

Le graphe local reste un outil d’explication dans `tests-impact`. Il n’est pas
un second système de sélection. Les modifications de code gardent la suite
complète ; seuls les changements limités à des tests connus sont ciblés.
Aucun service, outil externe ou envoi de données n’est nécessaire (U64).

## Comparaison sur cinq changements

Sur une candidate de 1.4.0, le graphe statique a été calculé pour cinq fichiers
représentatifs de changements réels. Dans chaque cas le plan conservateur
retenait les 213 fichiers de test et tous les tests utiles identifiés en revue.

| Changement | Dépendants statiques qui sont des tests | Tests utiles absents de la vue statique |
| --- | ---: | --- |
| #36 : import et attribution | 8 | Aucun dans la revue |
| #80 : dictionnaire des réglages | 142 | Aucun dans la revue |
| #123 : interpréteur G-code | 95 | Aucun dans la revue |
| #133 : diagnostic de finition | 1 | Contrat des arcs et de G92 |
| #66 : serveur de la visionneuse | 10 | Aucun dans la revue |

L’absence d’arête dans le quatrième cas n’est pas une preuve d’indépendance :
ce test couvre le contrat de l’interpréteur utilisé par la finition. Réduire
la suite aux dépendants aurait écarté ce contrôle utile.

Sur le même checkout et les cinq sources dans un seul lot, mutualiser la
construction du graphe a ramené les lectures de texte de **2 455 à 491** et
le volume de **22 576 800 à 4 515 360 octets**. Le plan et les rapports de
dépendance sont identiques. Ce comptage inclut le Python, les liens locaux
et les déclarations analysées ; il ne prétend pas couvrir les imports
dynamiques ou les comportements d’exécution.
