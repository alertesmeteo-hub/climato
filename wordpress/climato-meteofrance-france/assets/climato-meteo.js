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

        var departements = {};
        var stationsByDept = {};
        var stationsByCode = {};
        var currentStationMeta = null;
        var yearCache = {};
        var yearRequestToken = 0;
        var currentYm = { year: 0, month: 0 };
        var minYm = null;
        var maxYm = null;

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

        function populateStationSelect(deptCode, selectCode) {
            var list = (stationsByDept[deptCode] || []).slice().sort(function (a, b) {
                return a.nom.localeCompare(b.nom, "fr");
            });
            elStation.innerHTML = "";
            list.forEach(function (station) {
                var opt = document.createElement("option");
                opt.value = station.num_poste;
                opt.textContent = station.nom;
                elStation.appendChild(opt);
            });
            if (selectCode && stationsByDept[deptCode].some(function (s) { return s.num_poste === selectCode; })) {
                elStation.value = selectCode;
            } else if (list.length) {
                elStation.value = list[0].num_poste;
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
        }

        function goToYm(target) {
            target = clamp(target, minYm, maxYm);
            currentYm = target;
            var year = target.year;
            var token = ++yearRequestToken;
            ensureYearLoaded(year).then(function () {
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

        elDept.addEventListener("change", onDepartementChange);
        elStation.addEventListener("change", onStationChange);
        elMonth.addEventListener("change", onMonthOrYearChange);
        elYear.addEventListener("change", onMonthOrYearChange);
        elPrev.addEventListener("click", function () { shiftMonth(-1); });
        elNext.addEventListener("click", function () { shiftMonth(1); });

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
