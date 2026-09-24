<?php
/**
 * Plugin Name: Climatologie mensuelle Météo-France — Tableaux
 * Plugin URI: https://github.com/alertesmeteo-hub/climato
 * Description: Tableau de climatologie mensuelle (relevés jour par jour et statistiques du mois) par station officielle Météo-France, pour la France métropolitaine — historique complet depuis l'ouverture de chaque station.
 * Version: 1.8.0
 * Author: Alertes Météo Hub
 * Requires at least: 5.8
 * Requires PHP: 7.4
 * License: GPL-2.0-or-later
 */

if (!defined('ABSPATH')) {
    exit;
}

define('CLIMATO_VERSION', '1.8.0');
define('CLIMATO_RELEASE_DATE', '24/09/2026');
define('CLIMATO_OPTION_BASE_URL', 'climato_national_data_base_url');
define(
    'CLIMATO_DEFAULT_BASE_URL',
    'https://raw.githubusercontent.com/alertesmeteo-hub/climato/data'
);

// Auto-guérison du pipeline Climato : si index.json est resté bloqué trop
// longtemps (cron GitHub Actions peu fiable), chaque chargement de la page
// relance côté serveur un nouveau run via workflow_dispatch. Le jeton
// GitHub reste EXCLUSIVEMENT côté serveur — à définir dans wp-config.php :
//   define('CLIMATO_GITHUB_TOKEN', 'github_pat_xxx...');
// Jeton « fine-grained », limité au dépôt alertesmeteo-hub/climato,
// permission « Actions » en Read and write.
define('CLIMATO_GITHUB_REPO', 'alertesmeteo-hub/climato');
define('CLIMATO_GITHUB_DATA_BRANCH', 'data');
define('CLIMATO_GITHUB_WORKFLOW_BRANCH', 'main');
define('CLIMATO_GITHUB_WORKFLOW_FILE', 'update-climato.yml');
// Un seul run quotidien : seuil largement au-dessus de 24 h.
define('CLIMATO_STALE_THRESHOLD_MIN', 26 * 60);

add_action('wp_enqueue_scripts', 'climato_register_assets');
add_action('admin_init', 'climato_register_settings');
add_action('admin_menu', 'climato_add_settings_page');
add_shortcode('climato_meteo', 'climato_render_shortcode');
add_filter('plugin_action_links_' . plugin_basename(__FILE__), 'climato_plugin_action_links');
add_action('wp_ajax_climato_autoheal', 'climato_handle_autoheal');
add_action('wp_ajax_nopriv_climato_autoheal', 'climato_handle_autoheal');

function climato_handle_autoheal() {
    if (!defined('CLIMATO_GITHUB_TOKEN') || !CLIMATO_GITHUB_TOKEN) {
        wp_send_json_success(array('configured' => false));
    }

    if (get_transient('climato_autoheal_lock')) {
        wp_send_json_success(array('skipped' => true));
    }
    set_transient('climato_autoheal_lock', 1, 5 * MINUTE_IN_SECONDS);

    $generated_at = climato_fetch_generated_at();
    if (null === $generated_at) {
        wp_send_json_success(array('configured' => true, 'checked' => false));
    }

    $age_minutes = (time() - $generated_at) / 60;
    if ($age_minutes <= CLIMATO_STALE_THRESHOLD_MIN) {
        wp_send_json_success(array('configured' => true, 'stale' => false, 'age_minutes' => round($age_minutes)));
    }

    if (get_transient('climato_autoheal_cooldown')) {
        wp_send_json_success(array('configured' => true, 'stale' => true, 'triggered' => false, 'cooldown' => true));
    }
    set_transient('climato_autoheal_cooldown', 1, 30 * MINUTE_IN_SECONDS);

    $triggered = climato_trigger_workflow();
    wp_send_json_success(array('configured' => true, 'stale' => true, 'triggered' => $triggered));
}

function climato_fetch_generated_at() {
    $url = 'https://api.github.com/repos/' . CLIMATO_GITHUB_REPO . '/contents/index.json'
        . '?ref=' . rawurlencode(CLIMATO_GITHUB_DATA_BRANCH);
    $response = wp_remote_get($url, array(
        'headers' => array(
            'Accept'     => 'application/vnd.github.raw',
            'User-Agent' => 'climato-meteofrance-france-autoheal',
        ),
        'timeout' => 8,
    ));
    if (is_wp_error($response) || 200 !== wp_remote_retrieve_response_code($response)) {
        return null;
    }
    $data = json_decode(wp_remote_retrieve_body($response), true);
    if (empty($data['generated_at'])) {
        return null;
    }
    $timestamp = strtotime($data['generated_at']);
    return $timestamp ? $timestamp : null;
}

function climato_trigger_workflow() {
    $url = 'https://api.github.com/repos/' . CLIMATO_GITHUB_REPO . '/actions/workflows/'
        . rawurlencode(CLIMATO_GITHUB_WORKFLOW_FILE) . '/dispatches';
    $response = wp_remote_post($url, array(
        'headers' => array(
            'Accept'        => 'application/vnd.github+json',
            'Authorization' => 'Bearer ' . CLIMATO_GITHUB_TOKEN,
            'Content-Type'  => 'application/json',
            'User-Agent'    => 'climato-meteofrance-france-autoheal',
        ),
        'body'    => wp_json_encode(array('ref' => CLIMATO_GITHUB_WORKFLOW_BRANCH)),
        'timeout' => 8,
    ));
    if (is_wp_error($response)) {
        return false;
    }
    $code = wp_remote_retrieve_response_code($response);
    return $code >= 200 && $code < 300;
}

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
    wp_localize_script('climato-meteo', 'CLIMATO_AUTOHEAL', array(
        'url' => admin_url('admin-ajax.php?action=climato_autoheal'),
    ));
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
        <p>Le visiteur peut ensuite changer de département, de station, de mois et d’année depuis le tableau — jusqu’à l’ouverture de la station (certaines stations parisiennes remontent à 1816).</p>
        <p>Les stations fermées depuis longtemps sont masquées par défaut (case « Afficher aussi les stations fermées » pour les retrouver). Quand Météo-France publie une fiche climatologique pour la station, les normales 1991-2020 et les records sont disponibles via « Normales 1991-2020 et records » et « Comparer avec les normales ».</p>
        <h2>Source des données</h2>
        <p>Météo-France, jeux de données publiques « Données climatologiques de base - quotidiennes » et « Fiches climatologiques » (data.gouv.fr, Licence Ouverte / Etalab 2.0), historique complet publié par Météo-France pour chaque station. Chaque année n’est téléchargée par le visiteur que lorsqu’il la consulte, sous forme compressée (décompression native dans le navigateur — nécessite un navigateur récent : Chrome/Edge, Firefox ou Safari à jour).</p>
        <h2>Auto-guérison du pipeline</h2>
        <p>
            Statut : <strong><?php echo (defined('CLIMATO_GITHUB_TOKEN') && CLIMATO_GITHUB_TOKEN) ? '✅ Configurée' : '⚠️ Non configurée'; ?></strong>
        </p>
        <p>
            Si <code>index.json</code> reste bloqué plus de <?php echo esc_html((int) round(CLIMATO_STALE_THRESHOLD_MIN / 60)); ?> heures,
            chaque chargement de cette page relance automatiquement le pipeline sur GitHub. Pour l'activer, ajouter dans
            <code>wp-config.php</code> :
        </p>
        <p><code>define('CLIMATO_GITHUB_TOKEN', 'github_pat_xxx...');</code></p>
        <p>
            Jeton « fine-grained » GitHub, limité au dépôt <code>alertesmeteo-hub/climato</code>, permission
            « Actions : Read and write » uniquement. Il n'est jamais transmis au navigateur.
        </p>
    </div>
    <?php
}

/** Relevés horaires archivés par le VPS (compléments récents + détail d'une journée). */
function climato_obs_url() {
    return untrailingslashit(apply_filters('climato_obs_url', 'https://dicton-du-jour.alertes-meteo.com/donnees/observations'));
}

/** API du VPS : relevés horaires à la demande (API climatologique Météo-France) pour les jours hors archive. */
function climato_obs_api_url() {
    return untrailingslashit(apply_filters('climato_obs_api_url', 'https://dicton-du-jour.alertes-meteo.com/api/v1/obs'));
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
        data-obs-url="<?php echo esc_url(climato_obs_url()); ?>"
        data-obs-api-url="<?php echo esc_url(climato_obs_api_url()); ?>"
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

        <div class="clm-links">
            <label class="clm-checkbox">
                <input type="checkbox" data-clm-toggle-closed>
                <span>Afficher aussi les stations fermées</span>
            </label>
            <button type="button" class="clm-link-button" data-clm-normales-toggle aria-expanded="false">
                [ Normales 1991-2020 et records ]
            </button>
            <label class="clm-checkbox">
                <input type="checkbox" data-clm-compare-toggle>
                <span>[ Comparer avec les normales ]</span>
            </label>
        </div>

        <div class="clm-normales-panel" data-clm-normales-panel hidden></div>

        <div class="clm-status" data-clm-status hidden></div>

        <div class="clm-graphs" data-clm-graphs hidden></div>

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
        <p class="clm-legend">— Donnée manquante ou non mesurée par cette station · <span class="clm-prov-legend">Valeur en italique</span> : relevé horaire provisoire (en attendant la publication quotidienne officielle) · Cliquez sur un jour pour le détail heure par heure.</p>
        <div class="clm-detail" data-clm-detail hidden></div>

        <div class="clm-compare-block" data-clm-compare-block hidden></div>

        <div class="clm-stats">
            <h3>Statistiques du mois</h3>
            <ul data-clm-stats-list class="clm-stats-list"></ul>
        </div>

        <div class="clm-precisions">
            <h3>Comment sont calculées ces valeurs ?</h3>
            <ul>
                <li><strong>Température maximale</strong> : la valeur la plus haute mesurée entre 6 h TU et 6 h TU le lendemain (jour J à J+1).</li>
                <li><strong>Température minimale</strong> : la valeur la plus basse mesurée entre 18 h TU la veille et 18 h TU du jour (jour J-1 à J).</li>
                <li><strong>Précipitations</strong> : quantité de pluie, neige ou grêle (en mm d'eau) tombée entre 6 h TU et 6 h TU le lendemain.</li>
                <li><strong>Enneigement</strong> : épaisseur de la couche de neige relevée à 6 h TU.</li>
                <li><strong>Valeurs en italique</strong> : provisoires, calculées à partir des relevés horaires sur la journée UTC (0 h à 24 h TU) en attendant la publication officielle de Météo-France ; elles peuvent différer légèrement de la valeur officielle.</li>
            </ul>
            <p><strong>TU = Temps Universel</strong>, l'heure de référence des relevés météo. Pour passer à l'heure de France :</p>
            <ul>
                <li>0 h TU = 1 h en hiver, 2 h en été ;</li>
                <li>6 h TU = 7 h en hiver, 8 h en été ;</li>
                <li>18 h TU = 19 h en hiver, 20 h en été.</li>
            </ul>
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
