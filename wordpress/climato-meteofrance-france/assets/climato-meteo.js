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
            var active = all.filter(function (s) { return s.active; });

            var mustShowClosed = !active.length;
            if (selectCode) {
                var target = all.filter(function (s) { return s.num_poste === selectCode; })[0];
                if (target && !target.active) {
                    mustShowClosed = true;
                }
            }
            if (mustShowClosed && !showClosedStations) {
                showClosedStations = true;
                if (elShowClosed) { elShowClosed.checked = true; }
            }

            elStation.innerHTML = "";
            if (showClosedStations) {
                var closed = all.filter(function (s) { return !s.active; });
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
            if (!meta || !meta.has_normales) {
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

                var tx = day ? day.tx : null;
                var tn = day ? day.tn : null;
                var rr = day ? day.rr : null;
                var insol = day ? day.insol_h : null;

                if (day) {
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
                    "<tr><td>" + weekday + " " + d + "</td>" +
                    "<td>" + fmtValue(tx, " °C") + "</td>" +
                    "<td>" + fmtValue(tn, " °C") + "</td>" +
                    "<td>" + fmtValue(rr, " mm") + "</td>" +
                    "<td>" + fmtValue(insol, " h") + "</td></tr>"
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

        function goToYm(target) {
            target = clamp(target, minYm, maxYm);
            currentYm = target;
            var year = target.year;
            var token = ++yearRequestToken;
            var tasks = [ensureYearLoaded(year)];
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

        Promise.all([
            fetchJson(baseUrl + "/departements.json"),
            fetchJsonGz(baseUrl + "/stations.json.gz")
        ]).then(function (results) {
            departements = results[0];
            var allStations = results[1].stations;
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
