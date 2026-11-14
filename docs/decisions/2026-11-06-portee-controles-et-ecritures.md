# Portée des contrôles et des écritures privées

`benchab controle regles` distingue les règles évaluées et celles hors
portée. U24 nécessite une preuve mécanique. U36 nécessite le contexte du
projet et les références protégées. Sans ces éléments, elles restent
NON VÉRIFIÉES dans `hors_portee`, avec un motif, et ne modifient pas le
code de sortie.

Un contrôle exécutable auquel il manque une donnée reste NON VÉRIFIÉ et
renvoie un code non nul. Cela couvre U25 en l’absence de G-code ou de
plafond. Le code 0 atteste la réussite dans la portée décrite par le rapport.
Il ne valide pas physiquement la pièce.

Un rapport ou un export d’essai peut rester jetable dans un temporaire
système. La partie publique du checkout garde son périmètre même si le
checkout a été cloné sous un temporaire. Les liens internes sont refusés.

Une préparation figée, une preuve, une archive durable, une sauvegarde ou
un vote reste sous la racine de son atelier privé. Les chemins durables
enregistrés dans les données ou manifestes obéissent à la même racine.
Un atelier de test temporaire peut créer des références internes à cet
atelier. Une référence durable vers un temporaire extérieur est refusée.
