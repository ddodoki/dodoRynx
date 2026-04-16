//tools\gps_map\gps_map_script.js.tpl

window._I18N = $i18n_json;

var currentFp = '';
var points = ${points_json};
var routeVisible = ${route_visible};

var _popup = null;
var _popupPt = null;
var _bootSettled = false;

var _selectedClusterKey = '';
var _selectedClusterMembers = [];
var _prevCz = -1;

var REP_OVERRIDES = ${rep_overrides_json};
var PIN_THUMBS_ON = ${pin_thumbs_on};
var PIN_ZOOM_THRESH = ${pin_zoom_thresh};
var PIN_SINGLES_ON = ${pin_singles_on};
var PIN_CLUSTERS_ON = ${pin_clusters_on};

var thumbMarkers = [];
var _selectedPinFp = '';

var GPX = ${gpx_json};
var GPX_VISIBLE = ${gpx_visible};
var GPX_HAS_ELEVATION = ${gpx_has_elevation};
var ELEV_VISIBLE = ${elevation_visible};

var selectedGpxIndex = -1;
var gpxSelectedMarker = null;
var gpxStartMarker = null;
var gpxEndMarker = null;

var segmentStartIdx = -1;
var segmentEndIdx = -1;
var segmentHoverIdx = -1;

var elevationDragging = false;
var elevationDragLastIdx = -1;

var _speedHeatmapVisible = false;
var _arrowsVisible       = false;
var _stopMarkersVisible  = false;
var _stopMarkerList      = []; 

var _playbackTimer  = null;
var _playbackIndex  = -1;
var _playbackPoints = [];     

var TIME_OFFSET_MODE = 'AUTO';
var TIME_OFFSET_HOURS = 0;
var AUTO_TIME_OFFSET_HOURS = null;
var _GEO_UTC_OFFSET = ${geo_utc_offset_hours};

var IS_QT_MODE = '${toolbar_mode}' === 'qt';
var _sensorRangeCache = {}; 
var map = new maplibregl.Map({
    container: 'map',
    style: {
        version: 8,
        glyphs: 'http://127.0.0.1:${port}/glyphs/{fontstack}/{range}.pbf',
        sources: {
            raster: {
                type: 'raster',
                tiles: ['http://127.0.0.1:${port}/tiles/{z}/{x}/{y}.webp'],
                tileSize: ${tile_size},
                scheme: '${scheme}',
                minzoom: ${min_zoom},
                maxzoom: ${max_zoom}
            }
        },
        layers: [{
            id: 'raster-layer',
            type: 'raster',
            source: 'raster',
            paint: { 'raster-fade-duration': '${toolbar_mode}' === 'qt' ? 0 : 200 }
        }]
    },
    center: [${center_lon}, ${center_lat}],
    zoom: ${zoom},
    minZoom: ${min_zoom},
    maxZoom: ${max_zoom},
    attributionControl: false,
    preserveDrawingBuffer: true,
    fadeDuration: 0,                  
    antialias: false,             
});

// ── 센서 설정 ────────────────────────────────────────────────
var _s = window._I18N.sensor;
var SENSOR_CONFIG = {
  speed: {
    key: 'speed_mps', label: _s.speed_label, unit: _s.speed_unit,
    color: '#5cb8ff',
    valid:   function(v) { return v > 0 && v < 100; },
    display: function(v) { return (v * 3.6).toFixed(1) + ' ' + _s.speed_unit; }
  },
  heart_rate: {
    key: 'heart_rate_bpm', label: _s.heartrate_label, unit: _s.heartrate_unit,
    color: '#ff668a',
    valid:   function(v) { return v > 20 && v < 250; },
    display: function(v) { return Math.round(v) + ' ' + _s.heartrate_unit; }
  },
  cadence: {
    key: 'cadence_spm', label: _s.cadence_label, unit: _s.cadence_unit,
    color: '#a98bff',
    valid:   function(v) { return v > 0 && v < 300; },
    display: function(v) { return Math.round(v) + ' ' + _s.cadence_unit; }
  },
  temperature: {
    key: 'temperature_c', label: _s.temperature_label, unit: _s.temperature_unit,
    color: '#ffb454',
    valid:   function(v) { return v > -60 && v < 80; },
    display: function(v) { return v.toFixed(1) + _s.temperature_unit; }
  }
};
var SENSOR_ORDER = ['speed', 'heart_rate', 'cadence', 'temperature'];
var SENSOR_STATE = { speed: true, heart_rate: true, cadence: false, temperature: false };
var GPX_HAS_SENSORS = ${gpx_has_sensors};

map.addControl(new maplibregl.NavigationControl({
    visualizePitch: false,
    showCompass: false
}), 'top-right');

map.addControl(new maplibregl.ScaleControl({
    maxWidth: 120,
    unit: 'metric'
}), 'bottom-left');

map.addControl(new maplibregl.AttributionControl({
    compact: false,    
    customAttribution: [
        '<a href="https://github.com/ddodoki/dodoRynx"     target="_blank">dodoRynx</a>',
        '<a href="https://github.com/protomaps/basemaps" target="_blank">Protomaps</a>',
        '<a href="https://github.com/maplibre/maplibre-gl-js" target="_blank">MapLibre GL JS</a>',
        '© <a href="https://openstreetmap.org" target="_blank">OpenStreetMap</a> contributors'
    ].join(' · ')
}), 'bottom-right');

function hasGpx() {
    return !!(GPX && GPX.points && GPX.points.length);
}

function hasGpxElevation() {
    return !!(hasGpx() && GPX_HAS_ELEVATION);
}

function signalReady() {
    if (_bootSettled) return;
    _bootSettled = true;
    document.title = 'gpsmap:ready';
    setTimeout(function() {
        if (document.title === 'gpsmap:ready') {
            document.title = 'dodoRynx';
        }
    }, 300);
}

function signalError(msg) {
    if (_bootSettled) return;
    _bootSettled = true;
    document.title = 'gpsmap:error:' + String(msg || 'unknown').slice(0, 80);
    setTimeout(function() {
        document.title = 'dodoRynx';
    }, 300);
}

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;'); 
}

function _num(v, dflt) {
    var n = Number(v);
    return Number.isFinite(n) ? n : dflt;
}

function _pickNum(obj, keys, dflt) {
    if (!obj) return dflt;
    for (var i = 0; i < keys.length; i++) {
        var v = Number(obj[keys[i]]);
        if (Number.isFinite(v)) return v;
    }
    return dflt;
}

function _parseTimeMs(s) {
    if (!s) return null;
    var t = Date.parse(s);
    return Number.isFinite(t) ? t : null;
}

function _fmtDuration(sec) {
  if (!Number.isFinite(sec) || sec < 0) return '-';
  sec = Math.round(sec);
  var h = Math.floor(sec / 3600);
  var m = Math.floor((sec % 3600) / 60);
  var s = sec % 60;
  if (h > 0) {
    var base = h + 'h ' + String(m).padStart(2, '0') + 'm';
    return s > 0 ? base + ' ' + String(s).padStart(2, '0') + 's' : base;
  }
  if (m > 0) return m + 'm ' + String(s).padStart(2, '0') + 's';
  return s + 's';
}

function _fmtDistKm(m) {
    return (Math.max(0, _num(m, 0)) / 1000).toFixed(2) + ' km';
}

function _fmtEle(m) {
    var v = _num(m, null);
    return Number.isFinite(v) ? v.toFixed(1) + ' m' : '-';
}

function _parseNaiveDateTimeMs(s) {
    if (!s) return null;
    var m = String(s).match(/(\d{4})[-/](\d{2})[-/](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
    if (!m) return null;
    return Date.UTC(
        Number(m[1]), Number(m[2]) - 1, Number(m[3]),
        Number(m[4]), Number(m[5]), Number(m[6] || 0)
    );
}

function _firstPhotoDateTakenMs() {
    for (var i = 0; i < points.length; i++) {
        if (points[i] && points[i].date_taken) {
            var ms = _parseNaiveDateTimeMs(points[i].date_taken);
            if (ms !== null) return ms;
        }
    }
    return null;
}

function _firstGpxTimeMs() {
    if (!hasGpx() || !GPX.points || !GPX.points.length) return null;
    for (var i = 0; i < GPX.points.length; i++) {
        var t = GPX.points[i] ? GPX.points[i].time : null;
        if (!t) continue;
        var ms = Date.parse(t);
        if (Number.isFinite(ms)) return ms;
    }
    return null;
}

function _calcAutoTimeOffsetHours() {
    var photoMs = _firstPhotoDateTakenMs();
    var gpxMs   = _firstGpxTimeMs();
    if (photoMs === null || gpxMs === null) return 0;

    var rawHours = (photoMs - gpxMs) / 3600000;
    var offset = Math.round(rawHours * 4) / 4

    return Math.max(-12, Math.min(14, offset));
}

function _effectiveTimeOffsetHours() {
    if (TIME_OFFSET_MODE === 'AUTO') {
        if (AUTO_TIME_OFFSET_HOURS === null) {
            if (_GEO_UTC_OFFSET !== null && Number.isFinite(Number(_GEO_UTC_OFFSET))) {
                AUTO_TIME_OFFSET_HOURS = Number(_GEO_UTC_OFFSET);
            } else {
                AUTO_TIME_OFFSET_HOURS = _calcAutoTimeOffsetHours();
            }
        }
        return AUTO_TIME_OFFSET_HOURS;
    }
    return Number(TIME_OFFSET_HOURS || 0);
}

function _fmtUtcOffset(hours) {
    var h = Number(hours || 0);
    var sign = h >= 0 ? '+' : '-';
    var absH = Math.abs(h);
    var wholeH = Math.floor(absH);
    var mins = Math.round((absH - wholeH) * 60); 
    return 'UTC' + sign
        + String(wholeH).padStart(2, '0')
        + ':' + String(mins).padStart(2, '0');
}

function _fmtDateTime(s) {
    if (!s) return '';

    var ms = Date.parse(s);
    if (!Number.isFinite(ms)) {
        return String(s).split('.')[0].replace('T', ' ').replace('Z', '');
    }

    var shifted = new Date(ms + _effectiveTimeOffsetHours() * 3600000);

    function pad(n) { return String(n).padStart(2, '0'); }
    return shifted.getUTCFullYear() + '-'
        + pad(shifted.getUTCMonth() + 1) + '-'
        + pad(shifted.getUTCDate()) + ' '
        + pad(shifted.getUTCHours()) + ':'
        + pad(shifted.getUTCMinutes()) + ':'
        + pad(shifted.getUTCSeconds());
}

window.setTimeOffsetMode = function(mode, hours) {
    TIME_OFFSET_MODE = (mode === 'AUTO') ? 'AUTO' : 'MANUAL';
    if (TIME_OFFSET_MODE === 'MANUAL') {
        TIME_OFFSET_HOURS = Number(hours || 0);
    }
    AUTO_TIME_OFFSET_HOURS = null;
    drawElevation();
};

function findPointByFilepath(fp) {
    return points.find(function(p) { return p.filepath === fp; }) || null;
}

function clearPopup() {
    if (_popup) {
        _popup.remove();
        _popup = null;
    }
    _popupPt = null;
}

var _imul = (typeof Math.imul === 'function')
  ? Math.imul
  : function(a, b) {
      var ah = (a >>> 16) & 0xffff;
      var al = a & 0xffff;
      var bh = (b >>> 16) & 0xffff;
      var bl = b & 0xffff;
      return ((al * bl) + (((ah * bl + al * bh) << 16) >>> 0)) | 0;
    };

function showPhotoPopup(pt, lngLat, clusterCnt) {
    if (!pt) return;
    clearPopup();
    _popupPt = pt;

    var cnt = (clusterCnt && clusterCnt > 1) ? clusterCnt : 1;
    var i18n = window._I18N.popup;

    // ── 썸네일 래퍼 ──────────────────────────────────────────
    var imgWrap = '<div style="position:relative;width:64px;height:64px;flex-shrink:0;">';
    imgWrap += pt.thumb_url
        ? '<img class="pp-thumb" src="' + pt.thumb_url + '" style="width:64px;height:64px;">'
        : '<div class="pp-thumb-placeholder"></div>';
    if (cnt > 1) {
        imgWrap += '<div style="position:absolute;top:2px;left:2px;'
            + 'background:rgba(0,0,0,.75);color:#fff;font-size:10px;'
            + 'padding:1px 6px;border-radius:8px;font-weight:bold;">'
            + cnt + '</div>';   
    }
    imgWrap += '</div>';

    // ── 버튼 레이블 ──────────────────────────────────────────
    var navLabel = cnt > 1
        ? i18n.btn_open_rep   
        : i18n.btn_open;

    // ── 팝업 본문 ────────────────────────────────────────────
    var html =
        '<div class="pp">'
        + imgWrap
        + '<div class="pp-body">'
        + '<b>' + esc(pt.filename || '') + '</b>'
        + (cnt > 1
            ? '<span class="meta" style="color:#7cc;font-weight:bold;">'
              + '📍 ' + cnt + ' ' + i18n.cluster_meta   
              + '</span>'
            : '')
        + (pt.date_taken ? '<span class="meta">' + esc(pt.date_taken) + '</span>' : '')
        + (pt.model      ? '<span class="meta">' + esc(pt.model)      + '</span>' : '')
        + '<span class="meta">'
          + Number(pt.lat).toFixed(5) + ', ' + Number(pt.lon).toFixed(5)
          + '</span>'
        + '<div class="pp-actions">'
        + '<button class="ppbtn ppbtn-nav" onclick="window._selPhoto()">'
          + navLabel
          + '</button>'
        + '<button class="ppbtn ppbtn-ext" onclick="window._extMap()">' 
          + i18n.btn_ext_map                   
          + '</button>'                             
        + '</div></div></div>';

    _popup = new maplibregl.Popup({ maxWidth:'300px', offset:12 })
        .setLngLat(lngLat)
        .setHTML(html)
        .addTo(map);
}

function _updateStyle() {
    if (!map.getLayer('photos-circle')) return;
    var fp = currentFp;
    map.setPaintProperty('photos-circle', 'circle-radius',
        ['case', ['==', ['get','filepath'], fp], 10, 7]);
    map.setPaintProperty('photos-circle', 'circle-color',
        ['case', ['==', ['get','filepath'], fp], '#ff6b35', '#4a9eff']);
    map.setPaintProperty('photos-circle', 'circle-opacity',
        ['case', ['==', ['get','filepath'], fp], 1.0, 0.85]);
    map.setPaintProperty('photos-circle', 'circle-stroke-width',
        ['case', ['==', ['get','filepath'], fp], 2.5, 1.5]);
}

function _setCircleVisibility(v) {
    if (!map.getLayer('photos-circle')) return;
    map.setLayoutProperty('photos-circle', 'visibility', v ? 'visible' : 'none');
}

function _setCircleFilterFiles(filepaths) {
    if (!map.getLayer('photos-circle')) return;
    if (!filepaths || !filepaths.length) {
        map.setFilter('photos-circle', ['==', ['get', 'filepath'], '__no_match__']);
        return;
    }
    map.setFilter('photos-circle', ['match', ['get', 'filepath'], filepaths, true, false]);
}

function _syncCircleLayer(singleFilepaths) {
    if (!map.getLayer('photos-circle')) return;
    if (!singleFilepaths || !singleFilepaths.length) {
        _setCircleVisibility(false);
        return;
    }
    _setCircleVisibility(true);
    _setCircleFilterFiles(singleFilepaths);
}

window.highlightCurrent = function(fp) {
    currentFp = fp || '';
    _updateStyle();
    renderThumbs();
};

window.goToPhoto = function(lat, lon) {
    map.flyTo({
        center:[lon, lat],
        zoom:Math.max(map.getZoom(), Math.min(13, ${max_zoom})),
        speed:1.2,
        curve:1.4,
        essential:true
    });
};

window.setRouteVisible = function(v) {
    routeVisible = !!v;
    if (map.getLayer('route-line')) {
        map.setLayoutProperty('route-line', 'visibility', routeVisible ? 'visible' : 'none');
    }
};

window._selPhoto = function() {
    if (!_popupPt) return;
    var fp = _popupPt.filepath;
    clearPopup();
    _selectedPinFp = '';
    document.title = 'PHOTO:' + encodeURIComponent(fp);
    renderThumbs();
};

window._extMap = function() {
    if (!_popupPt) return;
    document.title = 'EXTMAP:' + _popupPt.lat + ':' + _popupPt.lon;
};

function thumbCellSize(z) {
    var CELL_PX = PIN_THUMBS_ON ? 64 : 46;
    var pxPerDeg = 256 * Math.pow(2, z) / 360;
    var geoDeg = CELL_PX / pxPerDeg;

    if (z >= 17) return Math.max(0.00008, geoDeg);
    if (z >= 13) return Math.max(0.00035, geoDeg);
    if (z >= 9)  return Math.max(0.0015, geoDeg);
    return Math.max(0.003, geoDeg);
}

function clusterKey(pt, z) {
    var cz = Math.round(Math.min(z, 17));
    var c = thumbCellSize(cz);
    return 'z' + cz + ':' + Math.floor(pt.lat / c) + ':' + Math.floor(pt.lon / c);
}

function clearThumbs() {
    thumbMarkers.forEach(function(m) { m.remove(); });
    thumbMarkers = [];
}

// stableHash — 반드시 숫자 반환
function stableHash(str) {
  var s = String(str || '');
  var h = 2166136261 >>> 0;
  for (var i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = _imul(h, 16777619) >>> 0;
  }
  return h >>> 0; 
}

function pickRep(key, members) {
    if (!members || !members.length) return null;

    var fp = key ? REP_OVERRIDES[key] : null;
    if (fp) {
        var found = members.find(function(m) { return m.filepath === fp; });
        if (found) return found;
    }

    var seed = key || members.map(function(m) { return m.filepath; }).join('|');
    return members[stableHash(seed) % members.length];
}

function buildThumbCard(rep, cnt, isCurrent, isSelected) {
    var borderColor = isCurrent ? '#ff6b35'
                    : isSelected ? '#7cc'
                    : 'rgba(255,255,255,.85)';

    var card = document.createElement('div');
    card.style.cssText =
        'width:46px;height:46px;border-radius:6px;overflow:hidden;'
        + 'background:#202020;display:block;position:relative;'
        + 'border:2px solid ' + borderColor + ';'
        + (IS_QT_MODE ? '' : 'box-shadow:0 3px 12px rgba(0,0,0,.50);') 
        + 'pointer-events:auto;user-select:none;';

    if (rep.thumb_url) {
        var img = document.createElement('img');
        img.crossOrigin = 'anonymous';
        img.src = rep.thumb_url;
        img.draggable = false;
        img.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;';
        card.appendChild(img);
    } else {
        var ph = document.createElement('div');
        ph.style.cssText =
            'width:100%;height:100%;display:flex;align-items:center;'
            + 'justify-content:center;background:#2b2b2b;color:#8a8a8a;font-size:10px;';
        ph.textContent = window._I18N.popup.no_img;
        card.appendChild(ph);
    }

    if (cnt > 1) {
        var badge = document.createElement('div');
        badge.textContent = cnt;
        badge.style.cssText =
            'position:absolute;right:2px;bottom:2px;min-width:16px;height:16px;'
            + 'padding:0 4px;border-radius:8px;background:rgba(0,0,0,.72);'
            + 'color:#fff;font-size:10px;line-height:16px;text-align:center;';
        card.appendChild(badge);
    }

    return card;
}

function emitClusterSelection(key, members) {
    if (!key || !members || !members.length) return;
    document.title = 'CLUSTERSEL:' + encodeURIComponent(key) + ':'
        + encodeURIComponent(members.map(function(m) { return m.filepath; }).join('|'));
}

function buildPinThumbMarker(rep, cnt, isCurrent, isSelected) {
    var dotColor = isCurrent ? '#ff6b35'
                 : isSelected ? '#7cc'
                 : '#4a9eff';

    var wrap = document.createElement('div');
    wrap.style.cssText =
        'display:flex;align-items:center;gap:4px;'
        + 'pointer-events:auto;user-select:none;';

    var dot = document.createElement('div');
    dot.style.cssText =
        'width:14px;height:14px;flex-shrink:0;border-radius:50%;'
        + 'background:' + dotColor + ';'
        + 'border:2px solid #fff;'
        + (IS_QT_MODE ? '' : 'box-shadow:0 1px 6px rgba(0,0,0,.55);');
    wrap.appendChild(dot);

    var card = buildThumbCard(rep, cnt, isCurrent, isSelected);
    wrap.appendChild(card);

    return wrap;
}

function addThumbMarker(rep, members, key) {
    if (!rep || !members || !members.length) return;

    var cnt = members.length;
    var hasCurrent = members.some(function(m) { return m.filepath === currentFp; });
    var isSelected = members.some(function(m) { return m.filepath === _selectedPinFp; });

    if (PIN_THUMBS_ON) {
        var el = buildPinThumbMarker(rep, cnt, hasCurrent, isSelected);

        if (cnt > 1 && key) {
            el.oncontextmenu = function(ev) {
                ev.preventDefault();
                ev.stopPropagation();
                emitClusterSelection(key, members);
                return false;
            };
        }

        el.onclick = function(ev) {
            ev.preventDefault();
            ev.stopPropagation();

            if (cnt === 1) {
                _selectedPinFp = rep.filepath;
                _selectedClusterKey = '';
                _selectedClusterMembers = [];

                showPhotoPopup(rep, [rep.lon, rep.lat], 1);
                document.title = 'PINSEL:' + encodeURIComponent(rep.filepath);
                renderThumbs();
            } else {
                _selectedPinFp = rep.filepath;
                _selectedClusterKey = key || '';
                _selectedClusterMembers = members;

                showPhotoPopup(rep, [rep.lon, rep.lat], cnt);

                document.title = 'CLUSTERSHOW:'
                    + encodeURIComponent(key || '')
                    + ':' + encodeURIComponent(rep.filepath)
                    + ':' + encodeURIComponent(
                        members.map(function(m) { return m.filepath; }).join('|')
                    );

                renderThumbs();
            }
        };

        thumbMarkers.push(
            new maplibregl.Marker({
                element: el,
                anchor: 'left',
                offset: [-7, 0]
            }).setLngLat([rep.lon, rep.lat]).addTo(map)
        );
        return;
    }

    if (cnt <= 1) return;

    var wrap = document.createElement('div');
    var size = cnt >= 20 ? 44 : cnt >= 5 ? 36 : 28;
    var bg = cnt >= 20 ? '#2a60a8' : cnt >= 5 ? '#3a80d0' : '#4a9eff';

    wrap.style.cssText =
        'width:' + size + 'px;height:' + size + 'px;border-radius:50%;'
        + 'display:flex;align-items:center;justify-content:center;'
        + 'background:' + bg + ';border:2px solid '
        + (hasCurrent ? '#ff6b35' : 'rgba(255,255,255,0.7)') + ';'
        + (IS_QT_MODE ? '' : 'box-shadow:0 2px 8px rgba(0,0,0,0.5);')  
        + 'color:#fff;font-size:11px;font-weight:bold;'
        + 'font-family:-apple-system,Segoe UI,sans-serif;';
    wrap.textContent = cnt;

    wrap.onclick = function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        map.easeTo({
            center: [rep.lon, rep.lat],
            zoom: Math.min(map.getZoom() + 2, ${max_zoom}),
            duration: 400
        });
    };

    wrap.oncontextmenu = function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        emitClusterSelection(key, members);
        return false;
    };

    thumbMarkers.push(
        new maplibregl.Marker({
            element: wrap,
            anchor: 'center'
        }).setLngLat([rep.lon, rep.lat]).addTo(map)
    );
}

function renderThumbs() {
    clearThumbs();
    if (!points.length || !map.getBounds) {
        _setCircleVisibility(false);  
        return;
    }

    var z = map.getZoom();
    var b = map.getBounds();
    var vis = points.filter(function(p) {
        return b.contains({ lng: p.lon, lat: p.lat });
    });

    if (!vis.length) {
        _setCircleVisibility(false); 
        return;
    }

    var groups = {};
    vis.forEach(function(p) {
        var k = clusterKey(p, z);
        if (!groups[k]) groups[k] = [];
        groups[k].push(p);
    });

    var keys = Object.keys(groups);

    if (PIN_THUMBS_ON) {
        _setCircleVisibility(false); 
        keys.forEach(function(k) {
            var m = groups[k];
            if (m.length === 1 && !PIN_SINGLES_ON)  return;
            if (m.length  >  1 && !PIN_CLUSTERS_ON) return;
            addThumbMarker(pickRep(k, m), m, k);
        });
        return;
    }

    var singles = [];
    keys.forEach(function(k) {
        var m = groups[k];
        if (m.length === 1) {
            if (PIN_SINGLES_ON) singles.push(m[0].filepath);
        } else {
            if (PIN_CLUSTERS_ON) addThumbMarker(pickRep(k, m), m, k);
        }
    });

    if (singles.length) {
        _setCircleVisibility(true); 
        _setCircleFilterFiles(singles);
        _setCircleVisibility(false);  
    }
}

function clearPopupAndDeselect() {
    clearPopup();

    var hadCluster = !!_selectedClusterKey;
    var hadPin = !!_selectedPinFp;

    _selectedPinFp = '';
    _selectedClusterKey = '';
    _selectedClusterMembers = [];

    if (hadCluster) {
        document.title = 'CLUSTERCLEAR';
    } else if (hadPin) {
        renderThumbs();
    }
}

function hasGpxPanelData() {
  if (!hasGpx()) return false;
  if (GPX_HAS_ELEVATION) return true;
  if (!GPX_HAS_SENSORS)  return false;
  var avail = (GPX.sensors && GPX.sensors.available) || {};
  return !!(avail.speed || avail.heart_rate || avail.cadence || avail.temperature);
}

function _availableSensors() {
  var avail = (GPX.sensors && GPX.sensors.available) || {};
  return {
    speed:       !!avail.speed,
    heart_rate:  !!avail.heart_rate,
    cadence:     !!avail.cadence,
    temperature: !!avail.temperature
  };
}

// 포인트에서 센서값 추출 + 유효성 검사
function _sensorValue(pt, sensorKey) {
  if (!pt) return null;
  var cfg = SENSOR_CONFIG[sensorKey];
  if (!cfg) return null;
  var raw = pt[cfg.key];
  if (raw === null || raw === undefined) return null;
  var v = Number(raw);
  if (!Number.isFinite(v)) return null;
  if (!cfg.valid(v)) return null;  
  return v;
}

// GPX가 교체될 때 캐시 무효화 — removeGpxLayers() 내부 또는 _reloadGpx() 직후 호출
function _clearSensorRangeCache() {
  _sensorRangeCache = {};
}

function _sensorRange(sensorKey) {
  if (!hasGpx()) return null;

  // ── 캐시 히트 ──────────────────────────────────────────
  if (_sensorRangeCache.hasOwnProperty(sensorKey)) {
    return _sensorRangeCache[sensorKey];
  }

  // ── 캐시 미스: 1회만 계산 ──────────────────────────────
  var vals = GPX.points.map(function(p) {
    return _sensorValue(p, sensorKey);
  }).filter(function(v) { return v !== null; });

  if (vals.length < 2) {
    _sensorRangeCache[sensorKey] = null;
    return null;
  }
  var mn = Math.min.apply(null, vals);
  var mx = Math.max.apply(null, vals);
  if (mx === mn) { mn = mn - 1; mx = mx + 1; }

  var result = { min: mn, max: mx, range: mx - mn };
  _sensorRangeCache[sensorKey] = result;
  return result;
}

function _sensorAvg(key) {
  if (!hasGpx()) return null;
  var vals = GPX.points.map(function(p) {
    return _sensorValue(p, key);
  }).filter(function(v) { return v !== null; });
  if (!vals.length) return null;
  return vals.reduce(function(a, b) { return a + b; }, 0) / vals.length;
}

window.setRepOverrides = function(obj) {
    Object.keys(REP_OVERRIDES).forEach(function(k) {
        delete REP_OVERRIDES[k];
    });
    if (obj) {
        Object.keys(obj).forEach(function(k) {
            REP_OVERRIDES[k] = obj[k];
        });
    }
    renderThumbs();
};

map.on('load', function() {
    points.forEach(function(pt) {
        if (pt.is_current) currentFp = pt.filepath;
    });

    if (IS_QT_MODE) {
        var mapEl2 = document.getElementById('map');
        if (mapEl2) {
            mapEl2.style.isolation    = 'isolate';
        }
    }

    map.addSource('route', {
        type: 'geojson',
        data: {
            type:'Feature',
            geometry:{ type:'LineString', coordinates:${route_json} }
        }
    });
    map.addLayer({
        id: 'route-line',
        type: 'line',
        source: 'route',
        layout: {
            'line-cap':'round', 'line-join':'round',
            'visibility': routeVisible ? 'visible' : 'none'
        },
        paint: {
            'line-color':'#4a9eff', 'line-width':2,
            'line-opacity':0.65, 'line-dasharray':[2,2]
        }
    });

    map.addSource('photos', {
        type: 'geojson',
        data: {
            type: 'FeatureCollection',
            features: points.map(function(pt) {
                return {
                    type: 'Feature',
                    geometry: { type:'Point', coordinates:[pt.lon, pt.lat] },
                    properties: {
                        filepath: pt.filepath, filename: pt.filename,
                        lat: pt.lat, lon: pt.lon,
                        date_taken: pt.date_taken || '',
                        model: pt.model || '',
                        is_current: pt.is_current ? 1 : 0
                    }
                };
            })
        }
    });
    map.addLayer({
        id: 'photos-circle',
        type: 'circle',
        source: 'photos',
        paint: {
            'circle-radius': ['case', ['==', ['get','filepath'], currentFp], 10, 7],
            'circle-color': ['case', ['==', ['get','filepath'], currentFp], '#ff6b35', '#4a9eff'],
            'circle-opacity': ['case', ['==', ['get','filepath'], currentFp], 1.0, 0.85],
            'circle-stroke-color': '#fff',
            'circle-stroke-width': ['case', ['==', ['get','filepath'], currentFp], 2.5, 1.5]
        }
    });

    _prevCz = Math.round(Math.min(map.getZoom(), 17));
    _updateStyle();
    renderThumbs();

    if (window._onMapLoaded) window._onMapLoaded();
});

// 사진 이벤트 핸들러
map.on('click', 'photos-circle', function(e) {
    if (!e.features || !e.features.length) return;
    e.preventDefault();
    var props = e.features[0].properties;
    var pt = findPointByFilepath(props.filepath);
    if (!pt) return;
    _selectedPinFp = pt.filepath;
    _selectedClusterKey = '';
    _selectedClusterMembers = [];
    showPhotoPopup(pt, e.lngLat, 1);
    document.title = 'PINSEL:' + encodeURIComponent(pt.filepath);
    renderThumbs();
});
map.on('mouseenter', 'photos-circle', function() { map.getCanvas().style.cursor = 'pointer'; });
map.on('mouseleave', 'photos-circle', function() { map.getCanvas().style.cursor = ''; });
map.on('click', function(e) {
    if (!e.defaultPrevented && _popup) { clearPopupAndDeselect(); }
});
map.on('moveend', function() {
    var cz = Math.round(Math.min(map.getZoom(), 17));
    if (cz !== _prevCz) { clearPopupAndDeselect(); _prevCz = cz; }
    renderThumbs();
});
map.on('error', function(e) {
    var msg = (e && (e.error || e).message) || String((e && (e.error || e)) || e);
    signalError(msg);
});

setTimeout(function() { if (!_bootSettled) signalReady(); }, 10000);

