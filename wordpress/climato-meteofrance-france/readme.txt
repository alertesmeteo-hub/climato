=== Climatologie mensuelle Météo-France ===
Contributors: alertesmeteo
Tags: meteo, climatologie, meteo-france, station, tableau, avada
Requires at least: 5.8
Requires PHP: 7.4
Stable tag: 1.2.0
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Tableau de climatologie mensuelle par station officielle Météo-France : relevés jour par jour et statistiques du mois, sur tout l'historique publié par Météo-France.

== Description ==

Le shortcode [climato_meteo] affiche dans un seul module :

* le relevé quotidien du mois choisi pour une station Météo-France (Tmax, Tmin, précipitations 24h, ensoleillement) ;
* une ligne de moyennes/totaux du mois ;
* les statistiques du mois (jours de chaleur, forte/très forte chaleur, nuit tropicale, gelée, forte/très forte gelée, sans dégel, jours de pluie) ;
* un sélecteur de département, de station, de mois et d'année couvrant tout l'historique de la station (certaines stations parisiennes remontent à 1816), avec navigation mois précédent/suivant.

Les données proviennent des fichiers publics Météo-France « Données climatologiques de base - quotidiennes » (data.gouv.fr, Licence Ouverte / Etalab 2.0) : tout l'historique publié par Météo-France pour chaque station, pas seulement les années récentes. Chaque année n'est téléchargée par le visiteur que lorsqu'il la consulte, pour rester rapide malgré l'historique complet. Comparaison aux normales 1991-2020 pas encore incluse.

== Installation ==

1. Téléversez le ZIP dans Extensions > Ajouter une extension.
2. Activez Climatologie mensuelle Météo-France.
3. Vérifiez l'URL dans Réglages > Climato Météo-France.
4. Insérez [climato_meteo] dans un bloc Avada.

Exemple : [climato_meteo departement="28" station="28198001"]

== Changelog ==

= 1.2.0 =
* Les fichiers de données volumineux (séries annuelles par station, catalogue des stations) sont désormais publiés compressés gzip pour rester sous le seuil d'alerte de taille de dépôt GitHub. Décompression native côté navigateur, aucune dépendance ajoutée. Nécessite un navigateur récent (Chrome/Edge, Firefox ou Safari à jour).

= 1.1.0 =
* Historique complet : toutes les périodes publiées par Météo-France sont désormais récupérées (jusqu'à 1816 pour certaines stations parisiennes), pas seulement les ~2 dernières années.
* Chaque station est publiée en un fichier JSON par année pour que le site ne télécharge que l'année consultée.
* Correction : le changement de département ne réinitialisait pas correctement le mois/année affiché.

= 1.0.0 =
* Première version : pipeline GitHub Actions Météo-France (data.gouv.fr) jusqu'à publication sur la branche data.
* Tableau quotidien du mois, statistiques du mois, sélection département/station/mois/année.
