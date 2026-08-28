=== Climatologie mensuelle Météo-France ===
Contributors: alertesmeteo
Tags: meteo, climatologie, meteo-france, station, tableau, avada
Requires at least: 5.8
Requires PHP: 7.4
Stable tag: 1.0.0
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Tableau de climatologie mensuelle par station officielle Météo-France : relevés jour par jour et statistiques du mois.

== Description ==

Le shortcode [climato_meteo] affiche dans un seul module :

* le relevé quotidien du mois choisi pour une station Météo-France (Tmax, Tmin, précipitations 24h, ensoleillement) ;
* une ligne de moyennes/totaux du mois ;
* les statistiques du mois (jours de chaleur, forte/très forte chaleur, nuit tropicale, gelée, forte/très forte gelée, sans dégel, jours de pluie) ;
* un sélecteur de département, de station, de mois et d'année, avec navigation mois précédent/suivant.

Les données proviennent des fichiers publics Météo-France « Données climatologiques de base - quotidiennes » (data.gouv.fr, Licence Ouverte / Etalab 2.0). Seule la période la plus récente (glissante sur environ deux ans) est republiée par ce module — v1 volontairement « light », sans historique long ni comparaison aux normales 1991-2020.

== Installation ==

1. Téléversez le ZIP dans Extensions > Ajouter une extension.
2. Activez Climatologie mensuelle Météo-France.
3. Vérifiez l'URL dans Réglages > Climato Météo-France.
4. Insérez [climato_meteo] dans un bloc Avada.

Exemple : [climato_meteo departement="28" station="28198001"]

== Changelog ==

= 1.0.0 =
* Première version : pipeline GitHub Actions Météo-France (data.gouv.fr) jusqu'à publication sur la branche data.
* Tableau quotidien du mois, statistiques du mois, sélection département/station/mois/année.
