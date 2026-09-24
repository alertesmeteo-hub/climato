(function () {
    "use strict";

    var MONTH_NAMES = [
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre"
    ];
    var WEEKDAY_ABBR = ["Dim.", "Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam."];

    var STAT_DEFINITIONS = [
        { key: "jours_chaleur", label: "Jours de chaleur (Tmax >= 25°C)", field: "tx", op: ">=", threshold: 25 },
        { key: "jours_forte_chaleur", label: "Jours de forte chaleur (Tmax >= 30°C)", field: "tx", op: ">=", threshold: 30 },
        { key: "jours_tres_forte_chaleur", label: "Jours de très forte chaleur (Tmax >= 35°C)", field: "tx", op: ">=", threshold: 35 },
        { key: "jours_nuit_tropicale", label: "Jours avec nuit tropicale (Tmin >= 20°C)", field: "tn", op: ">=", threshold: 20 },
        { key: "jours_gelee", label: "Jours avec gelée (Tmin <= 0°C)", field: "tn", op: "<=", threshold: 0 },
        { key: "jours_forte_gelee", label: "Jours avec forte gelée (Tmin <= -5°C)", field: "tn", op: "<=", threshold: -5 },
        { key: "jours_tres_forte_gelee", label: "Jours avec très forte gelée (Tmin <= -10°C)", field: "tn", op: "<=", threshold: -10 },
        { key: "jours_sans_degel", label: "Jours sans dégel (Tmax <= 0°C)", field: "tx", op: "<=", threshold: 0 },
        { key: "jours_pluie", label: "Jours avec pluie (RR >= 1mm)", field: "rr", op: ">=", threshold: 1 }
    ];

    function ready(fn) {
        if (document.readyState !== "loading") {
            fn();
        } else {
            document.addEventListener("DOMContentLoaded", fn);
        }
    }

    function fetchJson(url) {
        return fetch(url, { credentials: "omit" }).then(function (response) {
            if (!response.ok) {
                throw new Error("HTTP " + response.status + " sur " + url);
            }
            return response.json();
        });
    }

    // Les gros fichiers (catalogue des stations, séries annuelles) sont
    // publiés compressés gzip (.json.gz) pour rester sous la limite de
    // taille de dépôt GitHub. On décompresse avec l'API native
    // DecompressionStream — aucune bibliothèque JS ajoutée.
    function fetchJsonGz(url) {
        if (typeof DecompressionStream === "undefined") {
            return Promise.reject(new Error(
                "Ce navigateur ne prend pas en charge la décompression native (DecompressionStream) " +
                "nécessaire pour afficher ce module. Merci de le mettre à jour."
            ));
        }
        return fetch(url, { credentials: "omit" }).then(function (response) {
            if (!response.ok) {
                throw new Error("HTTP " + response.status + " sur " + url);
            }
            var decompressed = response.body.pipeThrough(new DecompressionStream("gzip"));
            return new Response(decompressed).text();
        }).then(function (text) {
            return JSON.parse(text);
        });
    }

    function fmtValue(value, suffix) {
        if (value === null || value === undefined || isNaN(value)) {
            return "—";
        }
        var rounded = Math.round(value * 10) / 10;
        var text = (Math.round(rounded) === rounded) ? String(Math.round(rounded)) : rounded.toFixed(1);
        return text + suffix;
    }

    function pad2(value) {
        return value < 10 ? "0" + value : String(value);
    }

    function compareYm(a, b) {
        if (a.year !== b.year) {
            return a.year - b.year;
        }
        return a.month - b.month;
    }

    function dateToYm(dateStr) {
        var parts = dateStr.split("-");
        return { year: parseInt(parts[0], 10), month: parseInt(parts[1], 10) };
    }

    function daysInMonth(year, month) {
        return new Date(Date.UTC(year, month, 0)).getUTCDate();
    }

    function clamp(target, min, max) {
        if (min && compareYm(target, min) < 0) { return min; }
        if (max && compareYm(target, max) > 0) { return max; }
        return target;
    }

    function initApp(root) {
        var baseUrl = root.getAttribute("data-base-url");
        var obsUrl = (root.getAttribute("data-obs-url") || "").replace(/\/$/, "");
        var initialDepartement = root.getAttribute("data-departement") || "";
        var initialStation = root.getAttribute("data-station") || "";
        var initialYear = parseInt(root.getAttribute("data-annee"), 10) || 0;
        var initialMonth = parseInt(root.getAttribute("data-mois"), 10) || 0;

        var elDept = root.querySelector("[data-clm-select-departement]");
        var elStation = root.querySelector("[data-clm-select-station]");
        var elMonth = root.querySelector("[data-clm-select-mois]");
        var elYear = root.querySelector("[data-clm-select-annee]");
        var elPrev = root.querySelector("[data-clm-prev]");
        var elNext = root.querySelector("[data-clm-next]");
        var elMeta = root.querySelector("[data-clm-station-meta]");
        var elStatus = root.querySelector("[data-clm-status]");
        var elTableBody = root.querySelector("[data-clm-table-body]");
        var elTableFoot = root.querySelector("[data-clm-table-foot]");
        var elStatsList = root.querySelector("[data-clm-stats-list]");
        var elShowClosed = root.querySelector("[data-clm-toggle-closed]");
        var elNormalesToggle = root.querySelector("[data-clm-normales-toggle]");
        var elNormalesPanel = root.querySelector("[data-clm-normales-panel]");
        var elCompareToggle = root.querySelector("[data-clm-compare-toggle]");
        var elCompareBlock = root.querySelector("[data-clm-compare-block]");
        var elDetail = root.querySelector("[data-clm-detail]");
        var recentDays = {};
        var detailToken = 0;

        var departements = {};
        var stationsByDept = {};
        var stationsByCode = {};
        var currentStationMeta = null;
        var yearCache = {};
        var normalesCache = {};
        var yearRequestToken = 0;
        var currentYm = { year: 0, month: 0 };
        var minYm = null;
        var maxYm = null;
        var showClosedStations = false;
        var showNormalesPanel = false;
        var compareWithNormales = false;
        var catalogLastDate = "";

        function stationIsActive(station) {
            if (typeof station.active === "boolean") {
                return station.active;
            }
            // Compatibilité avec les catalogues <= 1.2.0, qui ne publiaient
            // pas encore le champ `active`. Une station est considérée active
            // si son dernier relevé date de moins de 730 jours par rapport au
            // relevé le plus récent du catalogue.
            if (!station.last_date || !catalogLastDate) {
                return true;
            }
            var last = new Date(station.last_date + "T00:00:00Z");
            var reference = new Date(catalogLastDate + "T00:00:00Z");
            return (reference.getTime() - last.getTime()) <= 730 * 86400000;
        }

        function showStatus(message) {
            if (!message) {
                elStatus.hidden = true;
                elStatus.textContent = "";
            } else {
                elStatus.hidden = false;
                elStatus.textContent = message;
            }
        }

        function populateMonthSelect() {
            elMonth.innerHTML = "";
            for (var m = 1; m <= 12; m++) {
                var opt = document.createElement("option");
                opt.value = String(m);
                opt.textContent = MONTH_NAMES[m - 1];
                elMonth.appendChild(opt);
            }
        }

        function populateYearSelect(fromYear, toYear) {
            elYear.innerHTML = "";
            for (var y = fromYear; y <= toYear; y++) {
                var opt = document.createElement("option");
                opt.value = String(y);
                opt.textContent = String(y);
                elYear.appendChild(opt);
            }
        }

        function populateDepartementSelect() {
            var codes = Object.keys(departements).sort();
            elDept.innerHTML = "";
            codes.forEach(function (code) {
                if (!stationsByDept[code] || !stationsByDept[code].length) {
                    return;
                }
                var opt = document.createElement("option");
                opt.value = code;
                opt.textContent = departements[code] + " (" + code + ")";
                elDept.appendChild(opt);
            });
        }

        function makeStationOption(station) {
            var opt = document.createElement("option");
            opt.value = station.num_poste;
            opt.textContent = station.nom;
            return opt;
        }

        // Les stations fermées depuis longtemps restent dans le catalogue
        // (pour l'historique) mais sont masquées par défaut — sauf si la
        // station demandée en fait justement partie, ou si le département
        // n'a plus aucune station active.
        function populateStationSelect(deptCode, selectCode) {
            var all = (stationsByDept[deptCode] || []).slice().sort(function (a, b) {
                return a.nom.localeCompare(b.nom, "fr");
            });
            var active = all.filter(stationIsActive);

            var mustShowClosed = !active.length;
            if (selectCode) {
                var target = all.filter(function (s) { return s.num_poste === selectCode; })[0];
                if (target && !stationIsActive(target)) {
                    mustShowClosed = true;
                }
            }
            if (mustShowClosed && !showClosedStations) {
                showClosedStations = true;
                if (elShowClosed) { elShowClosed.checked = true; }
            }

            elStation.innerHTML = "";
            if (showClosedStations) {
                var closed = all.filter(function (s) { return !stationIsActive(s); });
                if (active.length) {
                    var groupActive = document.createElement("optgroup");
                    groupActive.label = "Stations actives";
                    active.forEach(function (s) { groupActive.appendChild(makeStationOption(s)); });
                    elStation.appendChild(groupActive);
                }
                if (closed.length) {
                    var groupClosed = document.createElement("optgroup");
                    groupClosed.label = "Stations fermées";
                    closed.forEach(function (s) {
                        var opt = makeStationOption(s);
                        opt.textContent += " (jusqu'en " + s.last_date.slice(0, 4) + ")";
                        groupClosed.appendChild(opt);
                    });
                    elStation.appendChild(groupClosed);
                }
            } else {
                active.forEach(function (s) { elStation.appendChild(makeStationOption(s)); });
            }

            var pool = showClosedStations ? all : active;
            if (selectCode && pool.some(function (s) { return s.num_poste === selectCode; })) {
                elStation.value = selectCode;
            } else if (pool.length) {
                elStation.value = pool[0].num_poste;
            }
        }

        // Compléments récents : jours pas encore publiés dans la climatologie quotidienne,
        // calculés par le VPS à partir des relevés horaires Météo-France (fichier recent/<poste>.json).
        function ensureRecentLoaded(numPoste) {
            if (!obsUrl) { return Promise.resolve({}); }
            return fetchJson(obsUrl + "/recent/" + numPoste + ".json").then(function (data) {
                var byDate = {};
                (data.days || []).forEach(function (d) { byDate[d.date] = d; });
                return byDate;
            }).catch(function () { return {}; });
        }

        function lastRecentDate() {
            var dates = Object.keys(recentDays).sort();
            return dates.length ? dates[dates.length - 1] : "";
        }

        var TITRE_MANQUANT = "Donnée manquante : non publiée par Météo-France pour ce jour, ou grandeur non mesurée par cette station";

        // Cellule : valeur officielle, à défaut valeur provisoire issue des relevés horaires, à défaut « — ».
        function cellHtml(official, provisional, suffix, recentDay) {
            if (official !== null && official !== undefined) {
                return "<td>" + fmtValue(official, suffix) + "</td>";
            }
            if (provisional !== null && provisional !== undefined) {
                var partiel = recentDay && recentDay.n < 24 ? " — jour incomplet (" + recentDay.n + " relevés sur 24)" : "";
                return '<td class="clm-prov" title="Valeur provisoire calculée à partir des relevés horaires' + partiel + '">' + fmtValue(provisional, suffix) + "</td>";
            }
            return '<td class="clm-na" title="' + TITRE_MANQUANT + '">—</td>';
        }

        function renderEmptyTable(message) {
            elTableBody.innerHTML = '<tr><td colspan="5" class="clm-empty">' + message + "</td></tr>";
            elTableFoot.innerHTML = "";
            elStatsList.innerHTML = "";
        }

        // Un fichier annuel par station (au lieu d'un unique fichier avec tout
        // l'historique) : certaines stations remontent à 1816, un seul mois
        // affiché n'a besoin de télécharger que l'année concernée.
        function ensureYearLoaded(year) {
            if (Object.prototype.hasOwnProperty.call(yearCache, year)) {
                return Promise.resolve(yearCache[year]);
            }
            if (!currentStationMeta || currentStationMeta.years.indexOf(year) === -1) {
                yearCache[year] = {};
                return Promise.resolve(yearCache[year]);
            }
            showStatus("Chargement de l'année " + year + "…");
            var url = baseUrl + "/stations/" + currentStationMeta.num_poste + "/" + year + ".json.gz";
            return fetchJsonGz(url).then(function (data) {
                var byDate = {};
                (data.days || []).forEach(function (day) {
                    byDate[day.date] = day;
                });
                yearCache[year] = byDate;
                return byDate;
            }).catch(function (error) {
                yearCache[year] = {};
                showStatus("Erreur de chargement de l'année " + year + " : " + error.message);
                return yearCache[year];
            });
        }

        // Toutes les stations n'ont pas de fiche climatologique (normales
        // 1991-2020 et records) — seules les stations de référence en
        // publient une. On ne tente même pas la requête sinon.
        function ensureNormalesLoaded(numPoste) {
            if (Object.prototype.hasOwnProperty.call(normalesCache, numPoste)) {
                return Promise.resolve(normalesCache[numPoste]);
            }
            var meta = stationsByCode[numPoste];
            if (!meta || meta.has_normales === false) {
                normalesCache[numPoste] = null;
                return Promise.resolve(null);
            }
            return fetchJson(baseUrl + "/stations/" + numPoste + "/normales.json").then(function (data) {
                normalesCache[numPoste] = data;
                return data;
            }).catch(function () {
                normalesCache[numPoste] = null;
                return null;
            });
        }

        function fmtRecordDate(dayYear, monthIndex) {
            if (!dayYear) { return ""; }
            var parts = dayYear.split("-");
            if (parts.length !== 2) { return ""; }
            var day = parseInt(parts[0], 10);
            if (!day || !parts[1]) { return ""; }
            return day + " " + MONTH_NAMES[monthIndex] + " " + parts[1];
        }

        function renderNormalesPanel() {
            if (!elNormalesPanel) { return; }
            if (!currentStationMeta) { return; }
            var data = normalesCache[currentStationMeta.num_poste];
            if (!data) {
                elNormalesPanel.innerHTML = '<p class="clm-empty">Normales et records non disponibles pour cette station.</p>';
                return;
            }
            var rows = data.months.map(function (m) {
                var txRecord = fmtValue(m.tx_record, " °C");
                var txDate = fmtRecordDate(m.tx_record_date, m.mois - 1);
                var tnRecord = fmtValue(m.tn_record, " °C");
                var tnDate = fmtRecordDate(m.tn_record_date, m.mois - 1);
                return "<tr><td>" + MONTH_NAMES[m.mois - 1] + "</td>" +
                    "<td>" + fmtValue(m.tx_moy, " °C") + "</td>" +
                    "<td>" + fmtValue(m.tn_moy, " °C") + "</td>" +
                    "<td>" + txRecord + (txDate ? " <small>(" + txDate + ")</small>" : "") + "</td>" +
                    "<td>" + tnRecord + (tnDate ? " <small>(" + tnDate + ")</small>" : "") + "</td>" +
                    "<td>" + fmtValue(m.rr_moy, " mm") + "</td>" +
                    "<td>" + fmtValue(m.insol_moy, " h") + "</td></tr>";
            }).join("");
            elNormalesPanel.innerHTML =
                '<p class="clm-normales-periode">Normales ' + data.periode_normales + ', records sur toute la période de mesure.</p>' +
                '<div class="clm-table-wrap"><table class="clm-table clm-normales-table"><thead><tr>' +
                "<th>Mois</th><th>Tmax moy.</th><th>Tmin moy.</th><th>Record Tmax</th><th>Record Tmin</th><th>Pluie moy.</th><th>Ensoleil. moy.</th>" +
                "</tr></thead><tbody>" + rows + "</tbody></table></div>";
        }

        function renderCompareBlock(sums, counts) {
            if (!elCompareBlock) { return; }
            if (!compareWithNormales) {
                elCompareBlock.hidden = true;
                return;
            }
            elCompareBlock.hidden = false;
            var numPoste = currentStationMeta.num_poste;
            var normales = normalesCache[numPoste];
            if (!normales) {
                elCompareBlock.innerHTML = '<p class="clm-empty">Normales non disponibles pour cette station.</p>';
                return;
            }
            var monthData = normales.months[currentYm.month - 1];
            var actualTx = counts.tx ? sums.tx / counts.tx : null;
            var actualTn = counts.tn ? sums.tn / counts.tn : null;
            var actualRr = counts.rr ? sums.rr : null;
            var actualInsol = counts.insol ? sums.insol : null;

            function deltaRow(label, actual, normal, suffix) {
                var deltaText = "";
                if (actual !== null && normal !== null && normal !== undefined) {
                    var delta = Math.round((actual - normal) * 10) / 10;
                    deltaText = ", écart : " + (delta > 0 ? "+" : "") + fmtValue(delta, suffix);
                }
                return "<li><span class=\"clm-stat-label\">" + label + "</span>" +
                    "<span class=\"clm-stat-value\">" + fmtValue(actual, suffix) +
                    " <small>(normale : " + fmtValue(normal, suffix) + deltaText + ")</small></span></li>";
            }

            elCompareBlock.innerHTML =
                "<h4>Comparaison à la normale " + normales.periode_normales + " (" + MONTH_NAMES[currentYm.month - 1] + ")</h4>" +
                '<ul class="clm-stats-list">' +
                deltaRow("Température max. moyenne", actualTx, monthData.tx_moy, " °C") +
                deltaRow("Température min. moyenne", actualTn, monthData.tn_moy, " °C") +
                deltaRow("Précipitations", actualRr, monthData.rr_moy, " mm") +
                deltaRow("Ensoleillement", actualInsol, monthData.insol_moy, " h") +
                "</ul>";
        }

        function renderMonth() {
            if (!currentStationMeta) {
                return;
            }
            elMonth.value = String(currentYm.month);
            elYear.value = String(currentYm.year);
            elPrev.disabled = minYm ? compareYm(currentYm, minYm) <= 0 : false;
            elNext.disabled = maxYm ? compareYm(currentYm, maxYm) >= 0 : false;

            var currentDaysByDate = yearCache[currentYm.year] || {};
            var total = daysInMonth(currentYm.year, currentYm.month);
            var rows = [];
            var sums = { tx: 0, tn: 0, rr: 0, insol: 0 };
            var counts = { tx: 0, tn: 0, rr: 0, insol: 0 };
            var statCounts = {};
            STAT_DEFINITIONS.forEach(function (def) { statCounts[def.key] = 0; });
            var anyData = false;

            for (var d = 1; d <= total; d++) {
                var dateStr = currentYm.year + "-" + pad2(currentYm.month) + "-" + pad2(d);
                var day = currentDaysByDate[dateStr];
                var weekday = WEEKDAY_ABBR[new Date(Date.UTC(currentYm.year, currentYm.month - 1, d)).getUTCDay()];

                var rec = recentDays[dateStr] || null;
                var txO = day ? day.tx : null;
                var tnO = day ? day.tn : null;
                var rrO = day ? day.rr : null;
                var insolO = day ? day.insol_h : null;
                var tx = (txO !== null && txO !== undefined) ? txO : (rec ? rec.tx : null);
                var tn = (tnO !== null && tnO !== undefined) ? tnO : (rec ? rec.tn : null);
                var rr = (rrO !== null && rrO !== undefined) ? rrO : (rec ? rec.rr : null);
                var insol = (insolO !== null && insolO !== undefined) ? insolO : (rec ? rec.insol_h : null);

                if (day || rec) {
                    anyData = true;
                }
                if (tx !== null && tx !== undefined) { sums.tx += tx; counts.tx++; }
                if (tn !== null && tn !== undefined) { sums.tn += tn; counts.tn++; }
                if (rr !== null && rr !== undefined) { sums.rr += rr; counts.rr++; }
                if (insol !== null && insol !== undefined) { sums.insol += insol; counts.insol++; }

                STAT_DEFINITIONS.forEach(function (def) {
                    var value = def.field === "tx" ? tx : (def.field === "tn" ? tn : rr);
                    if (value === null || value === undefined) {
                        return;
                    }
                    if (def.op === ">=" ? value >= def.threshold : value <= def.threshold) {
                        statCounts[def.key]++;
                    }
                });

                rows.push(
                    '<tr class="clm-clic" data-date="' + dateStr + '" tabindex="0" title="Cliquez pour le détail heure par heure"><td>' + weekday + " " + d + "</td>" +
                    cellHtml(txO, rec ? rec.tx : null, " °C", rec) +
                    cellHtml(tnO, rec ? rec.tn : null, " °C", rec) +
                    cellHtml(rrO, rec ? rec.rr : null, " mm", rec) +
                    cellHtml(insolO, rec ? rec.insol_h : null, " h", rec) + "</tr>"
                );
            }

            elTableBody.innerHTML = rows.join("");
            elTableFoot.innerHTML =
                "<tr class=\"clm-summary-row\"><td>Moyenne / total</td>" +
                "<td>" + fmtValue(counts.tx ? sums.tx / counts.tx : null, " °C") + "</td>" +
                "<td>" + fmtValue(counts.tn ? sums.tn / counts.tn : null, " °C") + "</td>" +
                "<td>" + fmtValue(counts.rr ? sums.rr : null, " mm") + "</td>" +
                "<td>" + fmtValue(counts.insol ? sums.insol : null, " h") + "</td></tr>";

            elStatsList.innerHTML = STAT_DEFINITIONS.map(function (def) {
                return "<li><span class=\"clm-stat-label\">" + def.label + "</span>" +
                    "<span class=\"clm-stat-value\">" + statCounts[def.key] + "</span></li>";
            }).join("");

            showStatus(anyData ? "" : "Aucune donnée disponible pour ce mois.");
            renderCompareBlock(sums, counts);
        }

        var POINTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"];

        // Flèche dans le sens où souffle le vent (dd = direction d'où il vient, en degrés) : vent du nord (0°) → flèche vers le bas.
        function ventHtml(dir, vitesse) {
            var v = (vitesse === null || vitesse === undefined) ? "—" : vitesse + " km/h";
            if (vitesse === 0) { return "calme"; }
            if (dir === null || dir === undefined) { return v; }
            var pt = POINTS[Math.round(dir / 22.5) % 16];
            return '<span class="clm-vent" title="Vent de ' + pt + " (" + dir + '°)"><span class="clm-vent-fleche" style="transform:rotate(' + dir + 'deg)">↓</span> ' + pt + "</span> " + v;
        }

        function heureParis(iso) {
            return new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
        }

        function dateLongue(dateStr) {
            var p = dateStr.split("-");
            var d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
            return WEEKDAY_ABBR[d.getUTCDay()] + " " + (+p[2]) + " " + MONTH_NAMES[+p[1] - 1] + " " + p[0];
        }

        // Courbe SVG de la température (ligne) et de la pluie horaire (barres).
        function detailChart(rows, pas) {
            var w = 640, h = 190, l = 36, r = 30, t = 12, b = 26;
            var temps = rows.map(function (x) { return x[1]; }).filter(function (v) { return v !== null; });
            if (!temps.length) { return ""; }
            var tmin = Math.floor(Math.min.apply(null, temps)) - 1, tmax = Math.ceil(Math.max.apply(null, temps)) + 1;
            var rmax = Math.max(pas === 6 ? 0.5 : 2, Math.max.apply(null, rows.map(function (x) { return x[7] || 0; })));
            var n = rows.length;
            function X(i) { return l + (n > 1 ? i * (w - l - r) / (n - 1) : 0); }
            function Y(v) { return t + (tmax - v) * (h - t - b) / (tmax - tmin); }
            var bars = rows.map(function (x, i) {
                if (!x[7]) { return ""; }
                var bh = x[7] / rmax * (h - t - b) * 0.6;
                var bw = Math.max(2, Math.min(10, (w - l - r) / n * 0.8));
                return '<rect x="' + (X(i) - bw / 2) + '" y="' + (h - b - bh) + '" width="' + bw + '" height="' + bh + '" fill="#60a5fa" opacity=".7"><title>' + x[7] + " mm</title></rect>";
            }).join("");
            var pts = rows.map(function (x, i) { return x[1] === null ? null : X(i).toFixed(1) + "," + Y(x[1]).toFixed(1); }).filter(Boolean).join(" ");
            var ticks = rows.map(function (x, i) {
                var hm = heureParis(x[0]);
                return (hm.slice(3) === "00" && parseInt(hm.slice(0, 2), 10) % 3 === 0) ? '<text x="' + X(i) + '" y="' + (h - 8) + '" font-size="10" text-anchor="middle" fill="#64748b">' + hm.slice(0, 2) + " h</text>" : "";
            }).join("");
            return '<svg viewBox="0 0 ' + w + " " + h + '" class="clm-detail-chart" role="img" aria-label="Température et précipitations heure par heure">' +
                '<text x="4" y="' + (t + 8) + '" font-size="10" fill="#dc2626">' + tmax + "°</text>" +
                '<text x="4" y="' + (h - b) + '" font-size="10" fill="#dc2626">' + tmin + "°</text>" +
                bars + '<polyline points="' + pts + '" fill="none" stroke="#dc2626" stroke-width="2"/>' + ticks + "</svg>";
        }

        function showDetail(dateStr) {
            if (!currentStationMeta || !obsUrl) { return; }
            var token = ++detailToken;
            var poste = currentStationMeta.num_poste;
            elDetail.hidden = false;
            elDetail.innerHTML = '<p class="clm-detail-msg">Chargement du détail du ' + dateLongue(dateStr) + "…</p>";
            var dep = poste.slice(0, 2);
            var pick = function (url) {
                return fetchJson(url).then(function (d) {
                    var r = d[poste];
                    if (!r || !r.length) { throw new Error("vide"); }
                    return r;
                });
            };
            // Pas de 6 minutes quand la station en dispose (30 derniers jours), sinon relevés horaires.
            pick(obsUrl + "/jours6m/" + dateStr + "/" + dep + ".json").then(function (r) { return { rows: r, pas: 6 }; }, function () {
                return pick(obsUrl + "/jours/" + dateStr + "/" + dep + ".json").then(function (r) { return { rows: r, pas: 60 }; });
            }).then(function (res) {
                if (token !== detailToken) { return; }
                var rows = res.rows;
                var f = function (v, s) { return v === null || v === undefined ? "—" : v + s; };
                var body = rows.map(function (x) {
                    return "<tr><td>" + heureParis(x[0]) + "</td><td>" + f(x[1], " °C") + "</td><td>" + f(x[2], " °C") + "</td><td>" + f(x[3], " %") + "</td><td>" +
                        ventHtml(x[4], x[5]) + "</td><td>" + f(x[6], " km/h") + "</td><td>" + f(x[7], " mm") + "</td><td>" +
                        f(x[8], " hPa") + "</td><td>" + f(x[9], " km") + "</td><td>" + (x[10] === null || x[10] === undefined ? "—" : x[10] + " min") + "</td></tr>";
                }).join("");
                elDetail.innerHTML = '<div class="clm-detail-head"><h3>' + dateLongue(dateStr) + " — " + currentStationMeta.nom + '</h3><button type="button" class="clm-detail-close" data-clm-detail-close>Fermer ✕</button></div>' +
                    detailChart(rows, res.pas) +
                    '<div class="clm-table-wrap"><table class="clm-table clm-detail-table"><thead><tr><th>Heure</th><th>Temp.</th><th>Rosée</th><th>Humidité</th><th>Vent</th><th>Rafale</th><th>' + (res.pas === 6 ? "Pluie 6 min" : "Pluie 1 h") + '</th><th>Pression</th><th>Visib.</th><th>Soleil</th></tr></thead><tbody>' + body + "</tbody></table></div>" +
                    '<p class="clm-legend">' + (res.pas === 6 ? "Relevés toutes les 6 minutes" : "Relevés horaires") + ' Météo-France, heure de Paris. Journée UTC (00 h–24 h UTC) : commence à 02 h ou 01 h heure locale.</p>';
                elDetail.scrollIntoView({ behavior: "smooth", block: "nearest" });
            }).catch(function () {
                if (token !== detailToken) { return; }
                elDetail.innerHTML = '<div class="clm-detail-head"><h3>' + dateLongue(dateStr) + '</h3><button type="button" class="clm-detail-close" data-clm-detail-close>Fermer ✕</button></div>' +
                    '<p class="clm-detail-msg">Le détail heure par heure n\'est pas disponible pour ce jour : l\'archive horaire a démarré le 24/09/2026 et conserve les 120 derniers jours. Les valeurs quotidiennes du tableau restent celles de Météo-France.</p>';
            });
        }

        elTableBody.addEventListener("click", function (ev) {
            var tr = ev.target.closest ? ev.target.closest("tr[data-date]") : null;
            if (tr) { showDetail(tr.getAttribute("data-date")); }
        });
        elTableBody.addEventListener("keydown", function (ev) {
            if (ev.key !== "Enter" && ev.key !== " ") { return; }
            var tr = ev.target.closest ? ev.target.closest("tr[data-date]") : null;
            if (tr) { ev.preventDefault(); showDetail(tr.getAttribute("data-date")); }
        });
        elDetail.addEventListener("click", function (ev) {
            if (ev.target && ev.target.hasAttribute("data-clm-detail-close")) { elDetail.hidden = true; elDetail.innerHTML = ""; }
        });

        function goToYm(target) {
            target = clamp(target, minYm, maxYm);
            currentYm = target;
            var year = target.year;
            var token = ++yearRequestToken;
            var tasks = [ensureYearLoaded(year)];
            if (currentStationMeta && !currentStationMeta.recentRequested) {
                currentStationMeta.recentRequested = true;
                tasks.push(ensureRecentLoaded(currentStationMeta.num_poste).then(function (r) {
                    recentDays = r;
                    var last = lastRecentDate();
                    if (last && last > currentStationMeta.last_date) {
                        maxYm = dateToYm(last);
                        populateYearSelect(minYm.year, maxYm.year);
                        elMeta.textContent = elMeta.textContent.replace(/ au \d{4}-\d{2}-\d{2}.*$/, " au " + currentStationMeta.last_date + " (complété jusqu'au " + last + " par les relevés horaires)");
                    }
                }));
            }
            if (compareWithNormales && currentStationMeta) {
                tasks.push(ensureNormalesLoaded(currentStationMeta.num_poste));
            }
            Promise.all(tasks).then(function () {
                if (token !== yearRequestToken) {
                    return; // une navigation plus récente a pris le dessus
                }
                renderMonth();
            });
        }

        function loadStation(numPoste, preferredYm) {
            var meta = stationsByCode[numPoste];
            if (!meta) {
                renderEmptyTable("Station introuvable.");
                return;
            }
            currentStationMeta = meta;
            yearCache = {};
            recentDays = {};
            meta.recentRequested = false;
            elDetail.hidden = true;
            elDetail.innerHTML = "";
            minYm = dateToYm(meta.first_date);
            maxYm = dateToYm(meta.last_date);
            populateYearSelect(minYm.year, maxYm.year);

            elMeta.textContent = meta.nom + " (" + meta.departement + ") — altitude " +
                (meta.alti !== null && meta.alti !== undefined ? Math.round(meta.alti) + " m" : "inconnue") +
                " — données du " + meta.first_date + " au " + meta.last_date;

            var target = preferredYm;
            if (!target || compareYm(target, minYm) < 0 || compareYm(target, maxYm) > 0) {
                target = maxYm;
            }
            renderEmptyTable("Chargement…");
            goToYm(target);

            if (showNormalesPanel) {
                ensureNormalesLoaded(meta.num_poste).then(renderNormalesPanel);
            }
        }

        function onDepartementChange() {
            var code = elDept.value;
            populateStationSelect(code, null);
            var preferred = currentYm.year ? currentYm : null;
            loadStation(elStation.value, preferred);
        }

        function onStationChange() {
            var preferred = currentYm.year ? currentYm : null;
            loadStation(elStation.value, preferred);
        }

        function onMonthOrYearChange() {
            var year = parseInt(elYear.value, 10);
            var month = parseInt(elMonth.value, 10);
            if (!year || !month) {
                return;
            }
            goToYm({ year: year, month: month });
        }

        function shiftMonth(delta) {
            var year = currentYm.year;
            var month = currentYm.month + delta;
            if (month < 1) { month = 12; year -= 1; }
            if (month > 12) { month = 1; year += 1; }
            goToYm({ year: year, month: month });
        }

        function onToggleClosedChange() {
            showClosedStations = elShowClosed.checked;
            var previous = elStation.value;
            populateStationSelect(elDept.value, previous);
            if (elStation.value !== previous) {
                var preferred = currentYm.year ? currentYm : null;
                loadStation(elStation.value, preferred);
            }
        }

        function onNormalesToggleClick() {
            showNormalesPanel = !showNormalesPanel;
            elNormalesPanel.hidden = !showNormalesPanel;
            elNormalesToggle.setAttribute("aria-expanded", showNormalesPanel ? "true" : "false");
            if (showNormalesPanel && currentStationMeta) {
                elNormalesPanel.innerHTML = '<p class="clm-empty">Chargement…</p>';
                ensureNormalesLoaded(currentStationMeta.num_poste).then(renderNormalesPanel);
            }
        }

        function onCompareToggleChange() {
            compareWithNormales = elCompareToggle.checked;
            if (compareWithNormales && currentStationMeta) {
                ensureNormalesLoaded(currentStationMeta.num_poste).then(function () {
                    renderMonth();
                });
            } else if (elCompareBlock) {
                elCompareBlock.hidden = true;
            }
        }

        elDept.addEventListener("change", onDepartementChange);
        elStation.addEventListener("change", onStationChange);
        elMonth.addEventListener("change", onMonthOrYearChange);
        elYear.addEventListener("change", onMonthOrYearChange);
        elPrev.addEventListener("click", function () { shiftMonth(-1); });
        elNext.addEventListener("click", function () { shiftMonth(1); });
        if (elShowClosed) { elShowClosed.addEventListener("change", onToggleClosedChange); }
        if (elNormalesToggle) { elNormalesToggle.addEventListener("click", onNormalesToggleClick); }
        if (elCompareToggle) { elCompareToggle.addEventListener("change", onCompareToggleChange); }

        populateMonthSelect();
        showStatus("Chargement des stations…");

        if (window.CLIMATO_AUTOHEAL && window.CLIMATO_AUTOHEAL.url) {
            fetch(window.CLIMATO_AUTOHEAL.url, { method: "POST", cache: "no-store" }).catch(function () {});
        }

        Promise.all([
            fetchJson(baseUrl + "/departements.json"),
            fetchJsonGz(baseUrl + "/stations.json.gz")
        ]).then(function (results) {
            departements = results[0];
            var allStations = results[1].stations;
            allStations.forEach(function (station) {
                if (station.last_date && station.last_date > catalogLastDate) {
                    catalogLastDate = station.last_date;
                }
            });
            allStations.forEach(function (station) {
                stationsByCode[station.num_poste] = station;
                if (!stationsByDept[station.departement]) {
                    stationsByDept[station.departement] = [];
                }
                stationsByDept[station.departement].push(station);
            });

            var startDept = stationsByDept[initialDepartement] ? initialDepartement : Object.keys(stationsByDept).sort()[0];
            populateDepartementSelect();
            elDept.value = startDept;
            populateStationSelect(startDept, initialStation);

            var preferredYm = (initialYear && initialMonth) ? { year: initialYear, month: initialMonth } : null;
            loadStation(elStation.value, preferredYm);
        }).catch(function (error) {
            showStatus("Impossible de charger la liste des stations : " + error.message);
            renderEmptyTable("Données indisponibles.");
        });
    }

    ready(function () {
        var apps = document.querySelectorAll("[data-clm-app]:not([data-clm-initialized])");
        apps.forEach(function (app) {
            app.setAttribute("data-clm-initialized", "1");
            initApp(app);
        });
    });
})();
