<?php
/**
 * Plugin Name: Climatologie mensuelle Météo-France — Tableaux
 * Plugin URI: https://github.com/alertesmeteo-hub/climato
 * Description: Tableau de climatologie mensuelle (relevés jour par jour et statistiques du mois) par station officielle Météo-France, pour la France métropolitaine.
 * Version: 1.0.0
 * Author: Alertes Météo Hub
 * Requires at least: 5.8
 * Requires PHP: 7.4
 * License: GPL-2.0-or-later
 */

if (!defined('ABSPATH')) {
    exit;
}

define('CLIMATO_VERSION', '1.0.0');
define('CLIMATO_RELEASE_DATE', '27/08/2026');
define('CLIMATO_OPTION_BASE_URL', 'climato_national_data_base_url');
define(
    'CLIMATO_DEFAULT_BASE_URL',
    'https://raw.githubusercontent.com/alertesmeteo-hub/climato/data'
);

add_action('wp_enqueue_scripts', 'climato_register_assets');
add_action('admin_init', 'climato_register_settings');
add_action('admin_menu', 'climato_add_settings_page');
add_shortcode('climato_meteo', 'climato_render_shortcode');
add_filter('plugin_action_links_' . plugin_basename(__FILE__), 'climato_plugin_action_links');

function climato_plugin_action_links($links) {
    $settings_link = sprintf(
        '<a href="%s">%s</a>',
        esc_url(admin_url('options-general.php?page=climato-meteofrance')),
        esc_html__('Réglages', 'climato-meteofrance-france')
    );
    array_unshift($links, $settings_link);

    $help_link = sprintf(
        '<a href="%s">%s</a>',
        esc_url(admin_url('options-general.php?page=climato-meteofrance')),
        esc_html__('Shortcodes / Aide', 'climato-meteofrance-france')
    );
    array_unshift($links, $help_link);

    return $links;
}

function climato_register_assets() {
    wp_register_style(
        'climato-meteo',
        plugin_dir_url(__FILE__) . 'assets/climato-meteo.css',
        array(),
        CLIMATO_VERSION
    );
    wp_register_script(
        'climato-meteo',
        plugin_dir_url(__FILE__) . 'assets/climato-meteo.js',
        array(),
        CLIMATO_VERSION,
        true
    );
}

function climato_register_settings() {
    register_setting(
        'climato_settings',
        CLIMATO_OPTION_BASE_URL,
        array(
            'type' => 'string',
            'sanitize_callback' => 'esc_url_raw',
            'default' => CLIMATO_DEFAULT_BASE_URL,
        )
    );

    add_settings_section(
        'climato_main_section',
        'Source des données nationales',
        '__return_false',
        'climato-meteofrance'
    );

    add_settings_field(
        'climato_data_base_url_field',
        'Adresse du dossier de données',
        'climato_render_url_field',
        'climato-meteofrance',
        'climato_main_section'
    );
}

function climato_render_url_field() {
    $value = get_option(CLIMATO_OPTION_BASE_URL, CLIMATO_DEFAULT_BASE_URL);
    printf(
        '<input type="url" class="regular-text code" name="%1$s" value="%2$s" autocomplete="off">',
        esc_attr(CLIMATO_OPTION_BASE_URL),
        esc_attr($value)
    );
    echo '<p class="description">Conservez l’adresse proposée : elle pointe vers la branche nationale « data » du dépôt.</p>';
}

function climato_add_settings_page() {
    add_options_page(
        'Climatologie mensuelle Météo-France',
        'Climato Météo-France',
        'manage_options',
        'climato-meteofrance',
        'climato_render_settings_page'
    );
}

function climato_render_settings_page() {
    if (!current_user_can('manage_options')) {
        return;
    }
    ?>
    <div class="wrap">
        <h1>Climatologie mensuelle Météo-France</h1>
        <form action="options.php" method="post">
            <?php
            settings_fields('climato_settings');
            do_settings_sections('climato-meteofrance');
            submit_button();
            ?>
        </form>
        <p><strong>Version du module : <?php echo esc_html(CLIMATO_VERSION); ?> (<?php echo esc_html(CLIMATO_RELEASE_DATE); ?>)</strong></p>
        <h2>Shortcode unique</h2>
        <p><code>[climato_meteo]</code> : tableau du mois en cours pour la station par défaut (Paris-Montsouris).</p>
        <p><code>[climato_meteo departement="28" station="28198001"]</code> : ouvre directement sur une station précise.</p>
        <p><code>[climato_meteo departement="06" annee="2025" mois="8"]</code> : ouvre sur un département, une année et un mois précis.</p>
        <p>Le visiteur peut ensuite changer de département, de station, de mois et d’année depuis le tableau.</p>
        <h2>Source des données</h2>
        <p>Météo-France, jeu de données publiques « Données climatologiques de base - quotidiennes » (data.gouv.fr, Licence Ouverte / Etalab 2.0). Seule la période la plus récente (glissante sur environ deux ans) est republiée par ce module.</p>
    </div>
    <?php
}

function climato_base_url() {
    $url = get_option(CLIMATO_OPTION_BASE_URL, CLIMATO_DEFAULT_BASE_URL);
    return untrailingslashit(apply_filters('climato_national_data_base_url', $url));
}

function climato_department_code($value) {
    $code = trim((string) $value);
    return preg_match('/^\d{2}$/', $code) ? $code : '75';
}

function climato_station_code($value) {
    $code = trim((string) $value);
    return preg_match('/^\d{8}$/', $code) ? $code : '';
}

function climato_year_value($value) {
    $year = absint($value);
    return ($year >= 1950 && $year <= 2100) ? $year : 0;
}

function climato_month_value($value) {
    $month = absint($value);
    return ($month >= 1 && $month <= 12) ? $month : 0;
}

function climato_unique_identifier() {
    if (function_exists('wp_unique_id')) {
        return wp_unique_id('climato-');
    }
    return 'climato-' . wp_rand(1000, 999999);
}

function climato_render_shortcode($atts) {
    $atts = shortcode_atts(
        array(
            'departement' => '75',
            'station' => '75114001',
            'annee' => '',
            'mois' => '',
            'titre' => 'Climatologie mensuelle',
        ),
        $atts,
        'climato_meteo'
    );

    $department = climato_department_code($atts['departement']);
    $station = climato_station_code($atts['station']);
    $year = climato_year_value($atts['annee']);
    $month = climato_month_value($atts['mois']);
    $title = trim(sanitize_text_field($atts['titre']));
    if ($title === '') {
        $title = 'Climatologie mensuelle';
    }
    $app_id = climato_unique_identifier();

    wp_enqueue_style('climato-meteo');
    wp_enqueue_script('climato-meteo');

    ob_start();
    ?>
    <section
        id="<?php echo esc_attr($app_id); ?>"
        class="clm-card"
        data-clm-app
        data-base-url="<?php echo esc_url(climato_base_url()); ?>"
        data-departement="<?php echo esc_attr($department); ?>"
        data-station="<?php echo esc_attr($station); ?>"
        data-annee="<?php echo esc_attr($year ?: ''); ?>"
        data-mois="<?php echo esc_attr($month ?: ''); ?>"
        data-module-version="<?php echo esc_attr(CLIMATO_VERSION); ?>"
    >
        <header class="clm-header">
            <div>
                <p class="clm-kicker">STATIONS OFFICIELLES MÉTÉO-FRANCE</p>
                <h2><?php echo esc_html($title); ?></h2>
                <p class="clm-meta" data-clm-station-meta>Chargement des stations…</p>
            </div>
            <div class="clm-badge"><span>CLIMATO</span><strong>Quotidien</strong></div>
        </header>

        <div class="clm-toolbar">
            <label class="clm-field">
                <span>Département</span>
                <select data-clm-select-departement></select>
            </label>
            <label class="clm-field clm-field-grow">
                <span>Station</span>
                <select data-clm-select-station></select>
            </label>
            <label class="clm-field">
                <span>Mois</span>
                <select data-clm-select-mois></select>
            </label>
            <label class="clm-field">
                <span>Année</span>
                <select data-clm-select-annee></select>
            </label>
            <div class="clm-field clm-field-nav">
                <span>&nbsp;</span>
                <div class="clm-nav-buttons">
                    <button type="button" data-clm-prev aria-label="Mois précédent">&laquo;</button>
                    <button type="button" data-clm-next aria-label="Mois suivant">&raquo;</button>
                </div>
            </div>
        </div>

        <div class="clm-status" data-clm-status hidden></div>

        <div class="clm-table-wrap">
            <table class="clm-table" data-clm-table>
                <thead>
                    <tr>
                        <th scope="col">Jour</th>
                        <th scope="col">Température max.</th>
                        <th scope="col">Température min.</th>
                        <th scope="col">Précipitations 24h</th>
                        <th scope="col">Ensoleillement</th>
                    </tr>
                </thead>
                <tbody data-clm-table-body>
                    <tr><td colspan="5" class="clm-empty">Chargement…</td></tr>
                </tbody>
                <tfoot data-clm-table-foot></tfoot>
            </table>
        </div>
        <p class="clm-legend">— Donnée manquante</p>

        <div class="clm-stats">
            <h3>Statistiques du mois</h3>
            <ul data-clm-stats-list class="clm-stats-list"></ul>
        </div>

        <footer class="clm-footer">
            <span>
                Données publiques :
                <a href="https://www.data.gouv.fr/datasets/donnees-climatologiques-de-base-quotidiennes" target="_blank" rel="noopener noreferrer">Météo-France — Climatologie de base quotidienne</a>
                • <a href="https://www.alertes-meteo.com/" target="_blank" rel="noopener noreferrer">www.alertes-meteo.com</a>
            </span>
            <span class="clm-plugin-version">Module Climato v<?php echo esc_html(CLIMATO_VERSION); ?> (<?php echo esc_html(CLIMATO_RELEASE_DATE); ?>)</span>
        </footer>
    </section>
    <?php
    return ob_get_clean();
}
