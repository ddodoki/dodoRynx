// tools\gps_map\gps_map_elevation.js.tpl

function _activeSensorsWithRange() {
    var avail = _availableSensors();
    return SENSOR_ORDER.filter(function(k) {
        return SENSOR_STATE[k] && avail[k] && !!_sensorRange(k);
    });
}

function _sensorPadR() {
    var n = _activeSensorsWithRange().length;
    return n > 0 ? (10 + n * 46) : 18;
}

function _drawSensorYAxes(ctx, padL, padT, plotW, plotH) {
    var keys = _activeSensorsWithRange();
    keys.forEach(function(key, i) {
        var cfg = SENSOR_CONFIG[key];
        var sr  = _sensorRange(key);
        var x   = padL + plotW + 10 + i * 46;

        ctx.save();

        // 세로 기준선
        ctx.beginPath();
        ctx.moveTo(x, padT);
        ctx.lineTo(x, padT + plotH);
        ctx.strokeStyle = cfg.color + '44';
        ctx.lineWidth = 1;
        ctx.stroke();

        // 단위 레이블
        ctx.font = 'bold 8px -apple-system, Segoe UI, sans-serif';
        ctx.fillStyle = cfg.color;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'bottom';
        ctx.fillText(cfg.unit, x + 5, padT - 3);

        // 5단계 눈금
        ctx.font = '9px -apple-system, Segoe UI, sans-serif';
        for (var j = 0; j <= 4; j++) {
            var ratio = j / 4;
            var yPos  = padT + ratio * plotH;
            var val   = sr.max - ratio * (sr.max - sr.min);

            var label;
            if      (key === 'speed')       label = (val * 3.6).toFixed(0);
            else if (key === 'heart_rate')  label = Math.round(val).toString();
            else if (key === 'cadence')     label = Math.round(val).toString();
            else                            label = val.toFixed(1);

            // 틱
            ctx.beginPath();
            ctx.moveTo(x, yPos);
            ctx.lineTo(x + 4, yPos);
            ctx.strokeStyle = cfg.color + '66';
            ctx.lineWidth = 1;
            ctx.stroke();

            ctx.fillStyle = cfg.color;
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(label, x + 6, yPos);
        }

        ctx.restore();
    });
}

function _setElevationSub(text) {
  var el  = document.getElementById('elevationSub');
  var sep = document.getElementById('elevationHeadSep');
  if (!el) return;
  el.textContent = text || window._I18N.chart.elevation_sub_empty;

  if (sep) {
    if (text) {
      sep.classList.remove('hidden-when-empty');
    } else {
      sep.classList.add('hidden-when-empty');
    }
  }
}

//   — 선택 지점의 활성 센서값을 chip으로 표시
function _setElevationHeadRight(stats, selectedPt) {
  var el = document.getElementById('elevationHeadRight');
  if (!el) return;
  if (!stats) { el.innerHTML = ''; return; }

  var chips = '';

  chips += '<span class="elevation-chip">' + _fmtDistKm(stats.distanceM) + '</span>';
  if (stats.totalTimeSec !== null && Number.isFinite(stats.totalTimeSec))
    chips += '<span class="elevation-chip">' + _fmtDuration(stats.totalTimeSec) + '</span>';
  if (Number.isFinite(stats.ascentM) && stats.ascentM > 0)
    chips += '<span class="elevation-chip elev-chip-up">▲ ' + _fmtEle(stats.ascentM) + '</span>';
  if (Number.isFinite(stats.descentM) && stats.descentM > 0)
    chips += '<span class="elevation-chip elev-chip-dn">▼ ' + _fmtEle(stats.descentM) + '</span>';

  var offsetHours = _effectiveTimeOffsetHours();
  var isAuto = (TIME_OFFSET_MODE === 'AUTO');
  var utcLabel = _fmtUtcOffset(offsetHours);
  chips += isAuto
    ? '<span class="elevation-chip elevation-chip--auto" title="' + window._I18N.chart.utc_auto_tooltip + '">' + utcLabel + '</span>'
    : '<span class="elevation-chip elevation-chip--manual">' + utcLabel + '</span>';

  var avail = _availableSensors();
  ['speed', 'heart_rate', 'cadence'].forEach(function(key) {
    if (!avail[key]) return;
    var avg = _sensorAvg(key);
    if (avg === null) return;
    var cfg = SENSOR_CONFIG[key];
    chips += '<span class="elevation-chip" style="color:' + cfg.color
           + ';border-color:' + cfg.color + '66;background:' + cfg.color + '1a;"'
           + ' title="' + cfg.label + ' (' + (window._I18N.chart.avg_label || 'avg') + ')">'
           + '~ ' + cfg.display(avg)
           + '</span>';
  });

  el.innerHTML = chips;
}

function _buildGpxFlag(text, cls) {
    var wrap = document.createElement('div');
    wrap.className = 'gpx-flag-wrap ' + cls;
    wrap.style.zIndex = '140';

    var badge = document.createElement('div');
    badge.className = 'gpx-flag-badge';
    badge.textContent = text;

    var stem = document.createElement('div');
    stem.className = 'gpx-flag-stem';
    stem.style.height = PIN_THUMBS_ON ? '38px' : '18px';

    var dot = document.createElement('div');
    dot.className = 'gpx-flag-dot';

    wrap.appendChild(badge);
    wrap.appendChild(stem);
    wrap.appendChild(dot);
    return wrap;
}

function buildGpxPointCollection() {
    if (!hasGpx()) {
        return { type:'FeatureCollection', features:[] };
    }
    return {
        type:'FeatureCollection',
        features: GPX.points.map(function(p) {
            return {
                type:'Feature',
                geometry:{ type:'Point', coordinates:[p.lon, p.lat] },
                properties:{
                    idx: p.idx,
                    lat: p.lat,
                    lon: p.lon,
                    ele: p.ele,
                    time: p.time || '',
                    dist_m: p.dist_m || 0
                }
            };
        })
    };
}

function setStartEndVisible(v) {
    [gpxStartMarker, gpxEndMarker].forEach(function(m) {
        if (!m || !m.getElement()) return;
        m.getElement().style.display = v ? '' : 'none';
    });
}

function ensureGpxSelectedMarker() {
    if (gpxSelectedMarker) return gpxSelectedMarker;
    var el = document.createElement('div');
    el.className = 'gpx-selected-dot';
    gpxSelectedMarker = new maplibregl.Marker({
        element: el,
        anchor: 'center'
    });
    return gpxSelectedMarker;
}

function updateGpxSelectedMarkerVisibility() {
    if (!gpxSelectedMarker || !gpxSelectedMarker.getElement()) return;
    gpxSelectedMarker.getElement().style.display = GPX_VISIBLE ? '' : 'none';
}

function buildStartEndMarkers() {
    if (!hasGpx()) return;

    if (gpxStartMarker) gpxStartMarker.remove();
    if (gpxEndMarker) gpxEndMarker.remove();

    gpxStartMarker = new maplibregl.Marker({
        element: _buildGpxFlag(window._I18N.chart.start, 'start'),
        anchor: 'bottom'
    }).setLngLat([GPX.start.lon, GPX.start.lat]).addTo(map);

    gpxEndMarker = new maplibregl.Marker({
        element: _buildGpxFlag(window._I18N.chart.end,   'end'),
        anchor: 'bottom'
    }).setLngLat([GPX.end.lon, GPX.end.lat]).addTo(map);

    setStartEndVisible(GPX_VISIBLE);
}

function addGpxLayers() {
    if (!hasGpx()) return;
    var before = map.getLayer('photos-circle') ? 'photos-circle' : undefined;

    map.addSource('gpx-route', {
        type: 'geojson',
        data: {
            type:'Feature',
            geometry:{ type:'LineString', coordinates: GPX.route || [] }
        }
    });

    map.addLayer({
        id: 'gpx-route-casing',
        type: 'line',
        source: 'gpx-route',
        layout: {
            'line-cap':'round',
            'line-join':'round',
            'visibility': GPX_VISIBLE ? 'visible' : 'none'
        },   
        paint: {
            'line-color':'#151b22',
            'line-width':8,
            'line-opacity':0.92
        }
    },before);

    map.addLayer({
        id: 'gpx-route-line',
        type: 'line',
        source: 'gpx-route',
        layout: {
            'line-cap':'round',
            'line-join':'round',
            'visibility': GPX_VISIBLE ? 'visible' : 'none'
        },
        paint: {
            'line-color':'#ff9a2f',
            'line-width':4.25,
            'line-opacity':0.98
        }
    },before);

    map.addLayer({
        id: 'gpx-route-hit',
        type: 'line',
        source: 'gpx-route',
        layout: {
            'line-cap':'round',
            'line-join':'round',
            'visibility': GPX_VISIBLE ? 'visible' : 'none'
        },
        paint: {
            'line-color':'#ffffff',
            'line-width':18,
            'line-opacity':0.001
        }
    },before);

    map.addSource('gpx-points', {
        type:'geojson',
        data: buildGpxPointCollection()
    });

    map.addLayer({
        id: 'gpx-points-hit',
        type:'circle',
        source:'gpx-points',
        layout:{ 'visibility': GPX_VISIBLE ? 'visible' : 'none' },
        paint:{
            'circle-radius': 8,
            'circle-color': '#ffffff',
            'circle-opacity': 0.001,
            'circle-stroke-width': 0
        }
    },before);

    buildStartEndMarkers();
}

function findNearestGpxIndexByLngLat(lngLat) {
    if (!hasGpx()) return -1;
    var targetPx = map.project([lngLat.lng, lngLat.lat]);
    var bestIdx = -1;
    var bestDist = Infinity;

    GPX.points.forEach(function(p, idx) {
        var px = map.project([p.lon, p.lat]);
        var dx = px.x - targetPx.x;
        var dy = px.y - targetPx.y;
        var d2 = dx * dx + dy * dy;
        if (d2 < bestDist) {
            bestDist = d2;
            bestIdx = idx;
        }
    });

    return bestIdx;
}

function setGpxVisible(v) {
    GPX_VISIBLE = !!v;
    ['gpx-route-casing','gpx-route-line','gpx-route-hit','gpx-points-hit'].forEach(function(id) {
        if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', GPX_VISIBLE ? 'visible' : 'none');
    });
    if (GPX_VISIBLE && _speedHeatmapVisible) {
        if (map.getLayer('gpx-route-casing'))
            map.setLayoutProperty('gpx-route-casing', 'visibility', 'none');
        if (map.getLayer('gpx-route-line'))
            map.setLayoutProperty('gpx-route-line', 'visibility', 'none');
    }
    if (map.getLayer('gpx-speed-line'))
        map.setLayoutProperty('gpx-speed-line', 'visibility',
            (GPX_VISIBLE && _speedHeatmapVisible) ? 'visible' : 'none');

    _arrowMarkerList.forEach(function(m) {  
        if (m.getElement())
            m.getElement().style.display = (GPX_VISIBLE && _arrowsVisible) ? '' : 'none';
    });

    _stopMarkerList.forEach(function(m) {
        if (m.getElement())
            m.getElement().style.display = (GPX_VISIBLE && _stopMarkersVisible) ? '' : 'none';
    });

    setStartEndVisible(GPX_VISIBLE);
    updateGpxSelectedMarkerVisibility();
}

function setElevationVisible(v) {
  ELEV_VISIBLE = !!v && hasGpxPanelData(); 

  var dock = document.getElementById('elevationDock');
  if (!dock) return;
  dock.classList.toggle('hidden', !ELEV_VISIBLE);

  if (ELEV_VISIBLE) {
    _buildSensorDock(); 
  } else {
    var bar = document.getElementById('elevationSensorBar');
    if (bar) bar.classList.add('hidden');
  }

  syncAttributionWithElevation();

  if (ELEV_VISIBLE) {
    requestAnimationFrame(function() {
      requestAnimationFrame(drawElevation);
    });
  }
}

function _gpxStats() {
    if (!GPX || !GPX.points || !GPX.points.length) {
        return {
            distanceM: 0, totalTimeSec: null, restTimeSec: null, movingTimeSec: null,
            ascentM: 0, descentM: 0, minPt: null, maxPt: null, minEle: null, maxEle: null
        };
    }

    var pts = GPX.points;
    var distanceM = _pickNum(GPX, ['distance_m', 'total_distance_m'], _num(pts[pts.length - 1].dist_m, 0));

    var totalTimeSec = _pickNum(GPX, ['total_time_sec', 'elapsed_time_sec'], null);
    if (!Number.isFinite(totalTimeSec)) {
        var firstT = null, lastT = null;
        for (var i = 0; i < pts.length; i++) {
            var ms = _parseTimeMs(pts[i].time);
            if (ms !== null) { firstT = ms; break; }
        }
        for (var j = pts.length - 1; j >= 0; j--) {
            var ms2 = _parseTimeMs(pts[j].time);
            if (ms2 !== null) { lastT = ms2; break; }
        }
        if (firstT !== null && lastT !== null && lastT >= firstT) {
            totalTimeSec = (lastT - firstT) / 1000.0;
        } else {
            totalTimeSec = null;
        }
    }

    var restTimeSec = _pickNum(GPX, ['rest_time_sec', 'stopped_time_sec', 'pause_time_sec'], null);
    var movingTimeSec = _pickNum(GPX, ['moving_time_sec', 'walking_time_sec', 'active_time_sec'], null);

    if (!Number.isFinite(movingTimeSec) && Number.isFinite(totalTimeSec) && Number.isFinite(restTimeSec)) {
        movingTimeSec = Math.max(0, totalTimeSec - restTimeSec);
    }
    if (!Number.isFinite(restTimeSec) && Number.isFinite(totalTimeSec) && Number.isFinite(movingTimeSec)) {
        restTimeSec = Math.max(0, totalTimeSec - movingTimeSec);
    }

    var ascentM = _pickNum(GPX, ['ascent_m', 'elevation_gain_m', 'uphill_m', 'total_up_m'], null);
    var descentM = _pickNum(GPX, ['descent_m', 'elevation_loss_m', 'downhill_m', 'total_down_m'], null);

    var prevEle = null;
    var calcUp = 0;
    var calcDown = 0;
    var minPt = null;
    var maxPt = null;

    pts.forEach(function(p) {
        if (p.ele === null || p.ele === undefined || !Number.isFinite(Number(p.ele))) return;
        var ele = Number(p.ele);

        if (!minPt || ele < Number(minPt.ele)) minPt = p;
        if (!maxPt || ele > Number(maxPt.ele)) maxPt = p;

        if (prevEle !== null) {
            var diff = ele - prevEle;
            if (diff > 0) calcUp += diff;
            else calcDown += Math.abs(diff);
        }
        prevEle = ele;
    });

    if (!Number.isFinite(ascentM)) ascentM = calcUp;
    if (!Number.isFinite(descentM)) descentM = calcDown;

    return {
        distanceM: distanceM,
        totalTimeSec: Number.isFinite(totalTimeSec) ? totalTimeSec : null,
        restTimeSec: Number.isFinite(restTimeSec) ? restTimeSec : null,
        movingTimeSec: Number.isFinite(movingTimeSec) ? movingTimeSec : null,
        ascentM: ascentM,
        descentM: descentM,
        minPt: minPt,
        maxPt: maxPt,
        minEle: minPt ? Number(minPt.ele) : null,
        maxEle: maxPt ? Number(maxPt.ele) : null
    };
}

function getElevationLayout(width, height, stats, pts) {
    var padL = 52, padT = 12, padB = 34;
    var padR = _sensorPadR();
    var plotW = Math.max(1, width - padL - padR);
    var plotH = Math.max(1, height - padT - padB);
    var minEle = _num(stats.minEle, 0);
    var maxEle = _num(stats.maxEle, 0);
    var rangeEle = Math.max(1, maxEle - minEle);
    var totalDist = Math.max(1, _num(stats.distanceM, pts[pts.length - 1].dist_m || 1));

    return {
        padL: padL,
        padR: padR,
        padT: padT,
        padB: padB,
        plotW: plotW,
        plotH: plotH,
        minEle: minEle,
        maxEle: maxEle,
        rangeEle: rangeEle,
        totalDist: totalDist
    };
}

function _pickIndexFromCanvasX(canvas, localX) {
  if (!hasGpx()) return -1;

  var rect   = canvas.getBoundingClientRect();
  var stats  = _gpxStats();
  var totalDist = Math.max(1, stats.distanceM ||
    _num(GPX.points[GPX.points.length - 1].dist_m, 1));

  var validPts = GPX.points.filter(function(p) {
    return Number.isFinite(Number(p.dist_m));
  });
  if (validPts.length < 2) return -1;

  var layout = getElevationLayout(rect.width, rect.height, stats, validPts);
  var padL   = layout.padL;
  var plotW  = layout.plotW;

  var px    = Math.max(padL, Math.min(localX, padL + plotW));
  var ratio = (px - padL) / Math.max(1, plotW);

  if (ratio <= 0.02) return 0;
  if (ratio >= 0.98) return GPX.points.length - 1;

  var targetDist = ratio * totalDist;
  var bestIdx = 0, bestGap = Infinity;
  GPX.points.forEach(function(p, idx) {
    var gap = Math.abs(Number(p.dist_m || 0) - targetDist);
    if (gap < bestGap) { bestGap = gap; bestIdx = idx; }
  });
  return bestIdx;
}

function _selectGpxFromCanvasClientX(canvas, clientX) {
    var rect = canvas.getBoundingClientRect();
    var idx = _pickIndexFromCanvasX(canvas, clientX - rect.left);
    if (idx < 0) return;

    if (idx === elevationDragLastIdx) return;
    elevationDragLastIdx = idx;

    selectGpxIndex(idx, false);

    var pt = GPX.points[idx];
    if (pt && map.getBounds && !map.getBounds().contains({ lng: pt.lon, lat: pt.lat })) {
        map.panTo([pt.lon, pt.lat], { duration: 0 });
    }
}

function _setSegmentCardHtml(html, visible) {
    var el = document.getElementById('elevationSegmentCard');
    if (!el) return;
    el.innerHTML = html || '';
    el.classList.toggle('hidden', !visible);
}

function clearSegmentSelection() {
    segmentStartIdx = -1;
    segmentEndIdx = -1;
    _setSegmentCardHtml('', false);
    drawElevation();
}

function _segmentStats(aIdx, bIdx) {
    if (!hasGpx()) return null;
    if (aIdx < 0 || bIdx < 0) return null;

    var s = Math.min(aIdx, bIdx);
    var e = Math.max(aIdx, bIdx);
    if (s === e) return null;

    var pts = GPX.points;
    var a = pts[s];
    var b = pts[e];

    var distM = Math.max(0, Number(b.dist_m || 0) - Number(a.dist_m || 0));

    var timeA = _parseTimeMs(a.time);
    var timeB = _parseTimeMs(b.time);
    var durationSec = (timeA !== null && timeB !== null && timeB >= timeA)
        ? (timeB - timeA) / 1000.0
        : null;

    var up = 0;
    var down = 0;
    for (var i = s + 1; i <= e; i++) {
        var prevEle = Number(pts[i - 1].ele);
        var curEle = Number(pts[i].ele);
        if (!Number.isFinite(prevEle) || !Number.isFinite(curEle)) continue;
        var diff = curEle - prevEle;
        if (diff > 0) up += diff;
        else down += Math.abs(diff);
    }

    var eleA = Number(a.ele);
    var eleB = Number(b.ele);
    var gradePct = distM > 0 && Number.isFinite(eleA) && Number.isFinite(eleB)
        ? ((eleB - eleA) / distM) * 100.0
        : null;

    var sensorStats = {};
    var avail = _availableSensors();
    SENSOR_ORDER.forEach(function(key) {
        if (!SENSOR_STATE[key] || !avail[key]) return;
        var vals = [];
        for (var si = s; si <= e; si++) {
            var v = _sensorValue(pts[si], key);
            if (v !== null) vals.push(v);
        }
        if (!vals.length) return;
        var mn = Math.min.apply(null, vals);
        var mx = Math.max.apply(null, vals);
        var avg = vals.reduce(function(a, b) { return a + b; }, 0) / vals.length;
        sensorStats[key] = { min: mn, max: mx, avg: avg };
    });

    return {
        startIdx: s,
        endIdx: e,
        startPt: a,
        endPt: b,
        distanceM: distM,
        durationSec: durationSec,
        ascentM: up,
        descentM: down,
        avgGradePct: gradePct,
        sensorStats: sensorStats
    };
}

function updateSegmentCard() {
    var stats = _segmentStats(segmentStartIdx, segmentEndIdx);
    if (!stats) {
        _setSegmentCardHtml('', false);
        return;
    }

    var _C = window._I18N.chart;

    // 기본 구간 정보
    var html = ''
        + '<span class="elevation-segment-title">' + _C.segment_title + '</span>'
        + '<span class="elevation-chip">' + _fmtDistKm(stats.distanceM) + '</span>'
        + '<span class="elevation-chip">' + _C.segment_duration + ' ' + _fmtDuration(stats.durationSec) + '</span>'
        + '<span class="elevation-chip">UP ' + _fmtEle(stats.ascentM) + '</span>'
        + '<span class="elevation-chip">DOWN ' + _fmtEle(stats.descentM) + '</span>'
        + '<span class="elevation-chip">' + _C.segment_grade + ' '
        + (Number.isFinite(stats.avgGradePct) ? stats.avgGradePct.toFixed(1) + '%' : '-')
        + '</span>';

    // 활성 센서 통계 행
    SENSOR_ORDER.forEach(function(key) {
        var ss = stats.sensorStats[key];
        if (!ss) return;
        var cfg = SENSOR_CONFIG[key];
        html += '<span class="elevation-segment-sensor-row"'
              + ' style="border-color:' + cfg.color + '44;background:' + cfg.color + '0d;">'
              + '<span class="elevation-segment-sensor-label" style="color:' + cfg.color + '">'
              + cfg.label + '</span>'
              + '<span class="elevation-segment-sensor-stat">' + _C.stat_min + '&nbsp;'
              + '<b>' + cfg.display(ss.min) + '</b></span>'
              + '<span class="elevation-segment-sensor-stat">' + _C.stat_avg + '&nbsp;'
              + '<b>' + cfg.display(ss.avg) + '</b></span>'
              + '<span class="elevation-segment-sensor-stat">' + _C.stat_max + '&nbsp;'
              + '<b>' + cfg.display(ss.max) + '</b></span>'
              + '</span>';
    });

    _setSegmentCardHtml(html, true);
}

function setSegmentPoint(idx) {
    if (!hasGpx()) return;
    if (idx < 0 || idx >= GPX.points.length) return;

    if (segmentStartIdx < 0 || (segmentStartIdx >= 0 && segmentEndIdx >= 0)) {
        segmentStartIdx = idx;
        segmentEndIdx = -1;
    } else {
        segmentEndIdx = idx;
    }

    updateSegmentCard();
    drawElevation();
}

window.clearElevationSegment = clearSegmentSelection;

function _drawTag(ctx, x, y, text, fill, stroke, fg) {
    ctx.save();
    ctx.font = '11px -apple-system, Segoe UI, sans-serif';
    var padX = 7;
    var h = 22;
    var w = Math.ceil(ctx.measureText(text).width) + padX * 2;
    var rx = Math.round(x - w / 2);
    var ry = Math.round(y - h / 2);

    ctx.beginPath();
    ctx.roundRect(rx, ry, w, h, 8);
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = stroke;
    ctx.stroke();

    ctx.fillStyle = fg;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x, y + 0.5);
    ctx.restore();
}

function _drawAxisBadge(ctx, x, y, text, kind) {
    var fill = kind === 'start'
        ? 'rgba(38,191,103,.96)'
        : 'rgba(236,104,104,.96)';
    var stroke = kind === 'start'
        ? 'rgba(255,255,255,.26)'
        : 'rgba(255,255,255,.26)';
    var fg = '#ffffff';

    ctx.save();
    ctx.font = '700 10px -apple-system, Segoe UI, sans-serif';
    var padX = 8;
    var h = 20;
    var w = Math.ceil(ctx.measureText(text).width) + padX * 2;
    var rx = Math.round(x - w / 2);
    var ry = Math.round(y - h / 2);

    ctx.beginPath();
    ctx.roundRect(rx, ry, w, h, 10);
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = stroke;
    ctx.stroke();

    ctx.fillStyle = fg;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x, y + 0.5);
    ctx.restore();
}

function _updateElevationInfo(selectedPt, stats) {
  _setElevationHeadRight(stats, selectedPt);

  if (!stats) { _setElevationSub(null); return; }

  if (selectedPt) {
    var parts = [_fmtDistKm(selectedPt.dist_m || 0)];

    if (selectedPt.ele != null && Number.isFinite(Number(selectedPt.ele)))
      parts.push(_fmtEle(selectedPt.ele));

    if (selectedPt.time)
      parts.push(_fmtDateTime(selectedPt.time));

    var avail = _availableSensors();
    SENSOR_ORDER.forEach(function(key) {
      if (!SENSOR_STATE[key]) return;
      if (!avail[key]) return;
      var v = _sensorValue(selectedPt, key);
      if (v === null) return;
      var cfg = SENSOR_CONFIG[key];
      parts.push(cfg.display(v));
    });

    _setElevationSub(parts.join(' · '));
  } else {
    _setElevationSub(null);
  }
}

function resizeElevationCanvas() {
    var canvas = document.getElementById('elevationCanvas');
    if (!canvas) return null;

    var rect = canvas.getBoundingClientRect();
    var dpr  = Math.max(1, window.devicePixelRatio || 1);
    if (!rect.width || !rect.height) return null;

    var newW = Math.floor(rect.width  * dpr);
    var newH = Math.floor(rect.height * dpr);

    if (canvas.width !== newW || canvas.height !== newH) {
        canvas.width  = newW;
        canvas.height = newH;
    }

    var ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { canvas: canvas, ctx: ctx, width: rect.width, height: rect.height };
}

function drawElevation() {
  var panel = document.getElementById('elevationPanel');
  if (!panel) return;
  if (!ELEV_VISIBLE || !hasGpxPanelData()) return;

  var pack = resizeElevationCanvas();
  if (!pack) {
    var stats = _gpxStats();
    var pt = selectedGpxIndex >= 0 ? GPX.points[selectedGpxIndex] : null;
    _updateElevationInfo(pt, stats);
    requestAnimationFrame(drawElevation);
    return;
  }

  var ctx    = pack.ctx;
  var width  = pack.width;
  var height = pack.height;
  ctx.clearRect(0, 0, width, height);

  var stats = _gpxStats();

  // 고도 필터 포인트
  var elePts = GPX.points.filter(function(p) {
    return p.ele !== null && p.ele !== undefined && Number.isFinite(Number(p.ele));
  });

  if (elePts.length < 2) {
    _drawSensorOnlyMode(ctx, width, height, stats);
    _drawSelectedLine(ctx, width, height, stats);
    _updateElevationInfo(selectedGpxIndex >= 0 ? GPX.points[selectedGpxIndex] : null, stats);
    return;
  }
  var layout   = getElevationLayout(width, height, stats, elePts);
  var padL = layout.padL, padR = layout.padR, padT = layout.padT, padB = layout.padB;
  var plotW = layout.plotW, plotH = layout.plotH;
  var totalDist = layout.totalDist;

  function xOfDist(distM)  { return padL + (_num(distM, 0) / totalDist) * plotW; }
  function yOfEle(ele)     {
    var t = (_num(ele, layout.minEle) - layout.minEle) / layout.rangeEle;
    return padT + (1 - t) * plotH;
  }

  if (_speedHeatmapVisible) {
    _drawSpeedLegend(ctx, padL, padT, plotW, plotH);
  }

  ctx.save();
  ctx.font = '11px -apple-system, Segoe UI, sans-serif';

  for (var i = 0; i <= 4; i++) {
    var ratio = i / 4;
    var y     = padT + ratio * plotH;
    var eleVal = layout.maxEle - ratio * layout.rangeEle;
    ctx.beginPath();
    ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y);
    ctx.strokeStyle = i === 4 ? 'rgba(255,255,255,.14)' : 'rgba(255,255,255,.08)';
    ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = '#93a0aa';
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    ctx.fillText(eleVal.toFixed(0) + ' m', padL - 8, y);
  }

  // X축 눈금
  var axisStep = _axisStepMeters(totalDist);
  _drawAxisBadge(ctx, padL+22,       padT+plotH+18, window._I18N.chart.start, 'start');
  _drawAxisBadge(ctx, padL+plotW-22, padT+plotH+18, window._I18N.chart.end,   'end');
  ctx.fillStyle = '#9aa8b3'; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  for (var dt = axisStep; dt < totalDist; dt += axisStep) {
    var tx = padL + (dt / totalDist) * plotW;
    ctx.beginPath(); ctx.moveTo(tx, padT + plotH); ctx.lineTo(tx, padT + plotH + 5);
    ctx.strokeStyle = 'rgba(255,255,255,.16)'; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillText((dt / 1000).toFixed(dt < 10000 ? 1 : 0) + ' km', tx, padT + plotH + 8);
  }

  ctx.beginPath();
  elePts.forEach(function(p, idx) {
    var x = xOfDist(p.dist_m), y = yOfEle(p.ele);
    if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = '#ff9a2f'; ctx.lineWidth = 2.4; ctx.stroke();
  ctx.lineTo(xOfDist(elePts[elePts.length - 1].dist_m), padT + plotH);
  ctx.lineTo(xOfDist(elePts[0].dist_m), padT + plotH);
  ctx.closePath(); ctx.fillStyle = 'rgba(255,154,47,.16)'; ctx.fill();

  // 센서 오버레이 (오류 #10, #11 수정 포함)
  _drawSensorOverlays(ctx, padL, padT, plotW, plotH, totalDist);
  _drawSensorYAxes(ctx, padL, padT, plotW, plotH);
  _drawSegmentHighlight(ctx, padL, padT, plotW, plotH, xOfDist, yOfEle);
  _drawMinMaxTags(ctx, stats, xOfDist, yOfEle, padT, plotH);
  _drawSelectedPoint(ctx, stats, xOfDist, yOfEle, padT, plotH, totalDist);

  ctx.restore();

  var selPt = selectedGpxIndex >= 0 ? GPX.points[selectedGpxIndex] : null;
  _updateElevationInfo(selPt, stats);
}

function _drawSpeedLegend(ctx, padL, padT, plotW, plotH) {
    var w = Math.min(160, plotW * 0.35);
    var h = 10;
    var x = padL + plotW - w;
    var y = padT + 4;

    // 그라디언트 바
    var grad = ctx.createLinearGradient(x, 0, x + w, 0);
    grad.addColorStop(0,    '#4a9eff');
    grad.addColorStop(0.25, '#26c8d0');
    grad.addColorStop(0.5,  '#26bf67');
    grad.addColorStop(0.75, '#ffb454');
    grad.addColorStop(1,    '#ff3c3c');
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, 3);
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,.25)';
    ctx.lineWidth = 1;
    ctx.stroke();

    // 레이블
    ctx.fillStyle = '#9aa8b3';
    ctx.font = '9px -apple-system, Segoe UI, sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillText(window._I18N.chart.speed_slow, x,     y + h + 3);
    ctx.textAlign = 'right';
    ctx.fillText(window._I18N.chart.speed_fast, x + w, y + h + 3);
    ctx.restore();
}

function syncAttributionWithElevation() {
    var ctrl  = document.querySelector('.maplibregl-ctrl-bottom-right');
    var scale = document.querySelector('.maplibregl-ctrl-bottom-left');  
    var dock  = document.getElementById('elevationDock');
    if (!ctrl || !dock) return;

    if (!ELEV_VISIBLE || dock.classList.contains('hidden')) {
        // 고도 패널 닫힘 → 원위치
        ctrl.classList.remove('elev-attr-floating');
        ctrl.style.right = ctrl.style.bottom = ctrl.style.top = ctrl.style.zIndex = '';

        if (scale) {                              
            scale.style.bottom = scale.style.zIndex = '';
        }
        return;
    }

    var dockH = dock.offsetHeight || 0;
    var offset = (14 + dockH + 8) + 'px';

    // 저작권 (우하단)
    ctrl.classList.add('elev-attr-floating');
    ctrl.style.right  = '18px';
    ctrl.style.top    = 'auto';
    ctrl.style.bottom = offset;
    ctrl.style.zIndex = '31';

    // 축척 (좌하단) — 동일한 높이로 올림
    if (scale) {
        scale.style.bottom  = offset;
        scale.style.zIndex  = '31';
    }
}

function _drawSensorOverlays(ctx, padL, padT, plotW, plotH, totalDist) {
  SENSOR_ORDER.forEach(function(key) {
    if (!SENSOR_STATE[key]) return;
    var avail = _availableSensors();
    if (!avail[key]) return;

    var cfg   = SENSOR_CONFIG[key];
    var sr    = _sensorRange(key);
    if (!sr) return;

    function yOfSensor(v) {
      var t = (v - sr.min) / sr.range;
      return padT + (1 - t) * plotH;
    }

    ctx.save();
    ctx.strokeStyle  = cfg.color;
    ctx.lineWidth    = 1.8;
    ctx.globalAlpha  = 0.72;
    ctx.beginPath();

    var inPath = false; 

    GPX.points.forEach(function(p) {
      var v = _sensorValue(p, key);
      var x = padL + (_num(p.dist_m, 0) / totalDist) * plotW;

      if (v === null) {
        inPath = false; 
        return;
      }
      var y = yOfSensor(v);
      if (!inPath) {
        ctx.moveTo(x, y); 
        inPath = true;
      } else {
        ctx.lineTo(x, y);
      }
    });

    ctx.stroke();
    ctx.restore();
  });
}

// 센서-only 모드 (고도 없음) 캔버스 그리기
function _drawSensorOnlyMode(ctx, width, height, stats) {
  var layout = getElevationLayout(width, height, stats, GPX.points);
  var padL = layout.padL, padT = layout.padT, plotW = layout.plotW, plotH = layout.plotH;
  var totalDist = layout.totalDist;

  ctx.save();
  ctx.font = '11px -apple-system, Segoe UI, sans-serif';

  // 격자선만 (Y레이블 없음 — 다중 센서 스케일 혼재)
  for (var i = 0; i <= 4; i++) {
    var y = padT + (i / 4) * plotH;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y);
    ctx.strokeStyle = 'rgba(255,255,255,.08)'; ctx.lineWidth = 1; ctx.stroke();
  }

  _drawAxisBadge(ctx, padL + 22,         padT + plotH + 18, window._I18N.chart.start, 'start');
  _drawAxisBadge(ctx, padL + plotW - 22, padT + plotH + 18, window._I18N.chart.end,   'end');

  // 센서 선 (동일한 정규화 로직 사용)
  _drawSensorOverlays(ctx, padL, padT, plotW, plotH, totalDist);
  _drawSensorYAxes(ctx, padL, padT, plotW, plotH);

  ctx.restore();
}

function bindElevationCanvas() {
    var canvas = document.getElementById('elevationCanvas');
    if (!canvas) return;

    var dragMode = '';
    var dragMoved = false;

    var _elevHoverRaf = false;
    function _scheduleDrawElev() {
        if (_elevHoverRaf) return;
        _elevHoverRaf = true;
        requestAnimationFrame(function() {
            _elevHoverRaf = false;
            drawElevation();
        });
    }
    function updateHover(ev) {
        var rect   = canvas.getBoundingClientRect();
        var inside = (ev.clientX >= rect.left && ev.clientX <= rect.right &&
                      ev.clientY >= rect.top  && ev.clientY <= rect.bottom);

        if (!inside && !elevationDragging) {
            if (segmentHoverIdx !== -1) {
                segmentHoverIdx = -1;
                _scheduleDrawElev();
            }
            return;
        }

        var newIdx = _pickIndexFromCanvasX(canvas, ev.clientX - rect.left);
        if (newIdx !== segmentHoverIdx) {
            segmentHoverIdx = newIdx;
            _scheduleDrawElev();
        }
    }
    function updateSelectedPoint(clientX) {
        var rect = canvas.getBoundingClientRect();
        var idx = _pickIndexFromCanvasX(canvas, clientX - rect.left);
        if (idx < 0) return;
        if (idx === elevationDragLastIdx) return;
        elevationDragLastIdx = idx;

        selectGpxIndex(idx, false);
        if (!elevationDragging) {
            var pt = GPX.points[idx];
            if (pt && map.getBounds && !map.getBounds().contains({ lng: pt.lon, lat: pt.lat })) {
                map.easeTo({ center: [pt.lon, pt.lat], duration: 200 });
            }
        }
    }

    function updateSegmentPoint(clientX) {
        var rect = canvas.getBoundingClientRect();
        var idx = _pickIndexFromCanvasX(canvas, clientX - rect.left);
        if (idx < 0) return;
        if (idx === elevationDragLastIdx) return;
        elevationDragLastIdx = idx;

        if (segmentStartIdx < 0 || (segmentStartIdx >= 0 && segmentEndIdx >= 0 && !elevationDragging)) {
            segmentStartIdx = idx;
            segmentEndIdx = -1;
        } else if (segmentStartIdx >= 0 && segmentEndIdx < 0) {
            segmentEndIdx = idx;
        } else {
            segmentEndIdx = idx;
        }

        updateSegmentCard();
        drawElevation();
    }

    canvas.addEventListener('mousedown', function(ev) {
        if (ev.button !== 0) return;

        ev.preventDefault();
        elevationDragging = true;
        elevationDragLastIdx = -1;
        dragMoved = false;

        if (ev.shiftKey) {
            dragMode = 'segment';
            var rect = canvas.getBoundingClientRect();
            var idx = _pickIndexFromCanvasX(canvas, ev.clientX - rect.left);
            if (idx >= 0) {
                if (segmentStartIdx < 0 || (segmentStartIdx >= 0 && segmentEndIdx >= 0)) {
                    segmentStartIdx = idx;
                    segmentEndIdx = -1;
                } else {
                    segmentEndIdx = idx;
                }
                elevationDragLastIdx = idx;
                updateSegmentCard();
                drawElevation();
            }
        } else {
            dragMode = 'select';
            updateSelectedPoint(ev.clientX);
        }

        updateHover(ev);
    });

    window.addEventListener('mousemove', function(ev) {
        if (elevationDragging) {
            dragMoved = true;
            if (dragMode === 'segment') {
                updateSegmentPoint(ev.clientX);
            } else if (dragMode === 'select') {
                updateSelectedPoint(ev.clientX);
            }
        }
        updateHover(ev);
    });

    window.addEventListener('mouseup', function(ev) {
        if (!elevationDragging) return;

        elevationDragging = false;
        elevationDragLastIdx = -1;
        var endDragMode = dragMode;
        dragMode = '';

        if (endDragMode === 'select' && ev && typeof ev.clientX === 'number') {
            updateSelectedPoint(ev.clientX);
        }
    });

    canvas.addEventListener('mouseleave', function() {
        if (!elevationDragging) {
            segmentHoverIdx = -1;
            drawElevation();
        }
    });

    canvas.addEventListener('click', function(ev) {
        if (dragMoved) {
            dragMoved = false;
            return;
        }

        var rect = canvas.getBoundingClientRect();
        var idx = _pickIndexFromCanvasX(canvas, ev.clientX - rect.left);
        if (idx < 0) return;

        if (ev.shiftKey) {
            setSegmentPoint(idx);
        } else {
            selectGpxIndex(idx, true);
        }
    });

    canvas.addEventListener('contextmenu', function(ev) {
        ev.preventDefault();
        var rect = canvas.getBoundingClientRect();
        var idx = _pickIndexFromCanvasX(canvas, ev.clientX - rect.left);
        if (idx < 0) return false;
        setSegmentPoint(idx);
        return false;
    });

    canvas.addEventListener('dblclick', function(ev) {
        ev.preventDefault();
        clearSegmentSelection();
    });
}

function selectGpxIndex(idx, centerMap) {
  if (!hasGpx()) return;
  if (idx < 0 || idx >= GPX.points.length) return;
  selectedGpxIndex = idx;
  var pt = GPX.points[idx];
  ensureGpxSelectedMarker().setLngLat([pt.lon, pt.lat]).addTo(map);
  if (gpxSelectedMarker && gpxSelectedMarker.getElement())
    gpxSelectedMarker.getElement().style.zIndex = 150;
  updateGpxSelectedMarkerVisibility();
  if (centerMap) map.easeTo({ center: [pt.lon, pt.lat], duration: 260, essential: true });

  // Sub 텍스트 및 캔버스 업데이트
  var stats = _gpxStats();
  _updateElevationInfo(pt, stats);
  requestAnimationFrame(drawElevation);
  updateSegmentCard();

  if (!window.isPlaybackActive || !window.isPlaybackActive()) {
    document.title = 'GPXSEL:' + String(idx);
  }
}

// GPX 레이어·소스·마커 완전 제거 (재로드 전 호출)
function removeGpxLayers() {
    ['gpx-route-casing', 'gpx-route-line', 'gpx-route-hit', 'gpx-points-hit',
     'gpx-speed-line']   
        .forEach(function(id) {
            if (map.getLayer(id)) map.removeLayer(id);
        });

    ['gpx-route', 'gpx-points',
     'gpx-speed']  
        .forEach(function(id) {
            if (map.getSource(id)) map.removeSource(id);
        });

    if (gpxStartMarker)    { gpxStartMarker.remove();    gpxStartMarker = null; }
    if (gpxEndMarker)      { gpxEndMarker.remove();      gpxEndMarker = null; }
    if (gpxSelectedMarker) { gpxSelectedMarker.remove(); gpxSelectedMarker = null; }
    _clearArrowMarkers();  
    _clearStopMarkers();
    _clearSensorRangeCache();
    window.stopPhotoPlayback();
    selectedGpxIndex = segmentStartIdx = segmentEndIdx = segmentHoverIdx = -1;
}

function _buildSensorDock() {
  var bar = document.getElementById('elevationSensorBar');
  if (!bar) return;

  var avail = _availableSensors();
  var anyAvail = SENSOR_ORDER.some(function(k) { return avail[k]; });

  if (!anyAvail) {
    bar.classList.add('hidden');
    return;
  }

  var html = '';
  SENSOR_ORDER.forEach(function(key) {
    if (!avail[key]) return;
    var cfg  = SENSOR_CONFIG[key];
    var chk  = SENSOR_STATE[key] ? 'checked' : '';
    var act  = SENSOR_STATE[key] ? ' active'  : '';
    html += '<label class="sensor-chip sensor-chip--' + key + act + '" data-sensor="' + key + '">'
          + '<input type="checkbox" ' + chk + ' data-sensor="' + key + '">'
          + cfg.label
          + ' <span class="sensor-unit">' + cfg.unit + '</span>'
          + '</label>';
  });
  bar.innerHTML = html;
  bar.classList.remove('hidden');

  bar.querySelectorAll('input[type=checkbox]').forEach(function(cb) {
    cb.addEventListener('change', function() {
      var key = this.getAttribute('data-sensor');
      SENSOR_STATE[key] = this.checked;
      var chip = this.closest('.sensor-chip');
      if (chip) chip.classList.toggle('active', this.checked);
      drawElevation();
    });
  });
}

function _drawSelectedLine(ctx, width, height, stats) {
  if (selectedGpxIndex < 0 || !hasGpx()) return;

  var pt = GPX.points[selectedGpxIndex];
  if (!pt || !Number.isFinite(Number(pt.dist_m))) return;

  var layout   = getElevationLayout(width, height, stats, GPX.points);
  var padL     = layout.padL;
  var padT     = layout.padT;
  var plotW    = layout.plotW;
  var plotH    = layout.plotH;
  var totalDist = layout.totalDist;

  var x = padL + (_num(pt.dist_m, 0) / totalDist) * plotW;
  x = Math.max(padL, Math.min(padL + plotW, x));

  ctx.save();
  ctx.beginPath();
  ctx.moveTo(x, padT);
  ctx.lineTo(x, padT + plotH);
  ctx.strokeStyle = 'rgba(255,154,47,.70)';
  ctx.lineWidth   = 1.5;
  ctx.setLineDash([4, 3]);
  ctx.stroke();
  ctx.setLineDash([]);

  // 상단 원형 핸들
  ctx.beginPath();
  ctx.arc(x, padT + plotH / 2, 5, 0, Math.PI * 2);
  ctx.fillStyle   = '#ff9a2f';
  ctx.strokeStyle = '#fff';
  ctx.lineWidth   = 2;
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

// ── Window API (공개) ────────────────────────────────────────
window.setGpxVisible      = setGpxVisible;
window.setElevationVisible = setElevationVisible;
window.selectGpxIndex     = selectGpxIndex;

window.setPinThumbsEnabled = function(v) {
    PIN_THUMBS_ON = !!v;
    buildStartEndMarkers();
    renderThumbs();
    drawElevation();
};

window.setPinSinglesEnabled = function(v) {
    PIN_SINGLES_ON = !!v;
    if (!PIN_SINGLES_ON && _selectedPinFp && !_selectedClusterKey) {
        clearPopupAndDeselect();
    }
    renderThumbs();
};

window.setPinClustersEnabled = function(v) {
    PIN_CLUSTERS_ON = !!v;
    // 숨길 때 클러스터가 선택 중이면 해제
    if (!PIN_CLUSTERS_ON && _selectedClusterKey) {
        clearPopupAndDeselect();
    }
    renderThumbs();
};

// ─────────────────────────────────────────────────────────────
// X축 눈금 간격 계산 (원본 drawElevation 내부 로컬 함수에서 추출)
// ─────────────────────────────────────────────────────────────
function _axisStepMeters(distM) {
    if (distM <  5000)  return   500;
    if (distM < 10000)  return  1000;
    if (distM < 30000)  return  2000;
    if (distM < 80000)  return  5000;
    return 10000;
}

// ─────────────────────────────────────────────────────────────
// 구간 선택 하이라이트 + A/B 핀 (원본 drawElevation 인라인 코드 추출)
// xOfDist, yOfEle: drawElevation 내부 클로저를 파라미터로 전달
// ─────────────────────────────────────────────────────────────
function _drawSegmentHighlight(ctx, padL, padT, plotW, plotH, xOfDist, yOfEle) {
    // 구간 음영
    if (segmentStartIdx >= 0 && segmentEndIdx >= 0 &&
        segmentStartIdx < GPX.points.length && segmentEndIdx < GPX.points.length) {
        var ss = Math.min(segmentStartIdx, segmentEndIdx);
        var ee = Math.max(segmentStartIdx, segmentEndIdx);
        var sx0 = xOfDist(GPX.points[ss].dist_m);
        var ex0 = xOfDist(GPX.points[ee].dist_m);
        ctx.fillStyle = 'rgba(255,214,102,.10)';
        ctx.fillRect(sx0, padT, Math.max(2, ex0 - sx0), plotH);
    }

    // 호버 수직선
    if (segmentHoverIdx >= 0 && segmentHoverIdx < GPX.points.length) {
        var hx = xOfDist(GPX.points[segmentHoverIdx].dist_m);
        ctx.beginPath();
        ctx.moveTo(hx, padT); ctx.lineTo(hx, padT + plotH);
        ctx.strokeStyle = 'rgba(255,255,255,.16)';
        ctx.lineWidth = 1; ctx.stroke();
    }

    // A 핀 (segmentStartIdx)
    if (segmentStartIdx >= 0 && segmentStartIdx < GPX.points.length) {
        var sp  = GPX.points[segmentStartIdx];
        var spx = xOfDist(sp.dist_m);
        var spy = (sp.ele !== null && sp.ele !== undefined && Number.isFinite(Number(sp.ele)))
                    ? yOfEle(sp.ele)
                    : padT + plotH / 2;
        ctx.beginPath(); ctx.moveTo(spx, padT); ctx.lineTo(spx, padT + plotH);
        ctx.strokeStyle = 'rgba(255,214,102,.80)'; ctx.lineWidth = 1.5; ctx.stroke();
        ctx.beginPath(); ctx.arc(spx, spy, 5, 0, Math.PI * 2);
        ctx.fillStyle = '#ffd666'; ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = '#ffffff'; ctx.stroke();
        _drawTag(ctx, spx, Math.max(14, spy - 18), 'A',
            'rgba(86,66,25,.95)', 'rgba(255,214,102,.45)', '#ffffff');
    }

    // B 핀 (segmentEndIdx)
    if (segmentEndIdx >= 0 && segmentEndIdx < GPX.points.length) {
        var ep  = GPX.points[segmentEndIdx];
        var epx = xOfDist(ep.dist_m);
        var epy = (ep.ele !== null && ep.ele !== undefined && Number.isFinite(Number(ep.ele)))
                    ? yOfEle(ep.ele)
                    : padT + plotH / 2;
        ctx.beginPath(); ctx.moveTo(epx, padT); ctx.lineTo(epx, padT + plotH);
        ctx.strokeStyle = 'rgba(124,214,255,.80)'; ctx.lineWidth = 1.5; ctx.stroke();
        ctx.beginPath(); ctx.arc(epx, epy, 5, 0, Math.PI * 2);
        ctx.fillStyle = '#7cd6ff'; ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = '#ffffff'; ctx.stroke();
        _drawTag(ctx, epx, Math.max(14, epy - 18), 'B',
            'rgba(25,57,74,.95)', 'rgba(124,214,255,.45)', '#ffffff');
    }
}

// ─────────────────────────────────────────────────────────────
// MAX / MIN 고도 태그 (원본 drawElevation 인라인 코드 추출)
// ─────────────────────────────────────────────────────────────
function _drawMinMaxTags(ctx, stats, xOfDist, yOfEle, padT, plotH) {
    if (stats.maxPt) {
        var mx = xOfDist(stats.maxPt.dist_m);
        var my = yOfEle(stats.maxPt.ele);
        ctx.beginPath(); ctx.arc(mx, my, 4.5, 0, Math.PI * 2);
        ctx.fillStyle = '#5cd6ff'; ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = '#ffffff'; ctx.stroke();
        _drawTag(ctx, mx, Math.max(14, my - 18),
            'MAX ' + _fmtEle(stats.maxPt.ele),
            'rgba(34,46,58,.72)', 'rgba(92,214,255,.32)', '#dff8ff');
    }
    if (stats.minPt) {
        var nx = xOfDist(stats.minPt.dist_m);
        var ny = yOfEle(stats.minPt.ele);
        ctx.beginPath(); ctx.arc(nx, ny, 4.5, 0, Math.PI * 2);
        ctx.fillStyle = '#7effa9'; ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = '#ffffff'; ctx.stroke();
        _drawTag(ctx, nx, Math.min(padT + plotH - 4, ny + 18), 
            'MIN ' + _fmtEle(stats.minPt.ele),
            'rgba(28,52,36,.72)', 'rgba(126,255,169,.28)', '#e8fff1');
    }
}

// ── 속도 히트맵 ────────────────────────────────────────────────
function _buildSpeedSegments() {
    if (!hasGpx() || GPX.points.length < 2) return [];

    function _calcSpd(i) {
        var raw = GPX.points[i].speed_mps;
        if (raw !== null && raw !== undefined) {
            var v = Number(raw);
            if (Number.isFinite(v) && v >= 0) return v;
        }
        var p0 = GPX.points[i - 1], p1 = GPX.points[i];
        var dD = Number(p1.dist_m || 0) - Number(p0.dist_m || 0);
        var t0 = _parseTimeMs(p0.time), t1 = _parseTimeMs(p1.time);
        if (t0 === null || t1 === null || t1 <= t0 || dD < 0) return null;
        return dD / ((t1 - t0) / 1000.0);
    }

    var rawSpeeds = [];
    for (var i = 1; i < GPX.points.length; i++) {
        var s = _calcSpd(i);
        if (s !== null) rawSpeeds.push(s);
    }
    if (!rawSpeeds.length) return [];

    rawSpeeds.sort(function(a, b) { return a - b; });
    var p5  = rawSpeeds[Math.floor(rawSpeeds.length * 0.05)];
    var p95 = rawSpeeds[Math.floor(rawSpeeds.length * 0.95)];
    var range = Math.max(0.01, p95 - p5);

    var features = [];
    for (var i = 1; i < GPX.points.length; i++) {
        var spd = _calcSpd(i);
        if (spd === null) continue;
        var norm = Math.max(0, Math.min(1, (spd - p5) / range + 0.5));
        features.push({
            type: 'Feature',
            geometry: { type: 'LineString',
                        coordinates: [[GPX.points[i-1].lon, GPX.points[i-1].lat],
                                      [GPX.points[i].lon,   GPX.points[i].lat]] },
            properties: { n: norm }
        });
    }
    return features;
}

function addSpeedHeatmapLayer() {
    if (map.getSource('gpx-speed')) return;
    var feats = _buildSpeedSegments();  
    if (!feats || !feats.length) return;
    var before = map.getLayer('gpx-route-hit') ? 'gpx-route-hit' : undefined;
    map.addSource('gpx-speed', {
        type: 'geojson',
        tolerance: 0,  
        data: { type: 'FeatureCollection', features: feats }
    });
    map.addLayer({
        id: 'gpx-speed-line',
        type: 'line',
        source: 'gpx-speed',
        layout: {
            'line-cap': 'round',
            'line-join': 'round',
            'visibility': (GPX_VISIBLE && _speedHeatmapVisible) ? 'visible' : 'none'
        },
        paint: {
            'line-width': 4.5,
            'line-opacity': 0.93,
            'line-color': [
                'interpolate', ['linear'], ['get', 'n'],
                0,    '#4a9eff',
                0.25, '#26c8d0',
                0.5,  '#26bf67',
                0.75, '#ffb454',
                1,    '#ff3c3c'
            ]
        }
    }, before);
}

window.setSpeedHeatmapVisible = function(v) {
    _speedHeatmapVisible = !!v;

    if (map.getLayer('gpx-route-casing'))
        map.setLayoutProperty('gpx-route-casing', 'visibility',
            (GPX_VISIBLE && !_speedHeatmapVisible) ? 'visible' : 'none');
    if (map.getLayer('gpx-route-line'))
        map.setLayoutProperty('gpx-route-line', 'visibility',
            (GPX_VISIBLE && !_speedHeatmapVisible) ? 'visible' : 'none');

    if (!map.getSource('gpx-speed')) {
        if (_speedHeatmapVisible) addSpeedHeatmapLayer();
        return;
    }
    map.setLayoutProperty('gpx-speed-line', 'visibility',
        (GPX_VISIBLE && _speedHeatmapVisible) ? 'visible' : 'none');
};

// ── 방향 화살표 ────────────────────────────────────────────────
function _bearing(p0, p1) {
    var r = Math.PI / 180;
    var dLon = (p1.lon - p0.lon) * r;
    var la1 = p0.lat * r, la2 = p1.lat * r;
    var y = Math.sin(dLon) * Math.cos(la2);
    var x = Math.cos(la1) * Math.sin(la2) - Math.sin(la1) * Math.cos(la2) * Math.cos(dLon);
    return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

function _buildArrowFeatures(intervalM) {
    if (!hasGpx() || GPX.points.length < 2) return [];
    intervalM = intervalM || 600;
    var feats = [];
    var next = intervalM;

    for (var i = 1; i < GPX.points.length; i++) {
        var dist = Number(GPX.points[i].dist_m || 0);
        if (dist >= next) {
            feats.push({
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [GPX.points[i].lon, GPX.points[i].lat] },
                properties: { b: _bearing(GPX.points[i - 1], GPX.points[i]) }
            });
            next += intervalM;
        }
    }
    return feats;
}

var _arrowMarkerList = [];

function _clearArrowMarkers() {
    _arrowMarkerList.forEach(function(m) { m.remove(); });
    _arrowMarkerList = [];
}

function addArrowLayer() {
    if (_arrowMarkerList.length) return;

    var totalDist = Number((GPX.points[GPX.points.length - 1] || {}).dist_m || 0);
    var intervalM = Math.max(500, Math.ceil(totalDist / 15));

    var feats = _buildArrowFeatures(intervalM);
    if (!feats || !feats.length) return;

    feats.forEach(function(f) {
        var outer = document.createElement('div');
        outer.style.cssText = 'width:20px;height:24px;pointer-events:none;';

        var inner = document.createElement('div');
        inner.style.cssText =
            'width:20px;height:24px;display:flex;align-items:center;' +
            'justify-content:center;' +
            'transform:rotate(' + f.properties.b + 'deg);' +
            'transform-origin:center center;' +
            'filter:drop-shadow(0 0 3px rgba(0,0,0,0.8));';

        inner.innerHTML =
            '<svg width="14" height="20" viewBox="0 0 14 20" fill="none" xmlns="http://www.w3.org/2000/svg">' +
            '<path d="M7 0 L14 18 L7 13 L0 18 Z" fill="#ff9a2f" stroke="rgba(0,0,0,0.55)" stroke-width="1.2" stroke-linejoin="round"/>' +
            '</svg>';

        outer.appendChild(inner);

        _arrowMarkerList.push(
            new maplibregl.Marker({ element: outer, anchor: 'center' })
                .setLngLat(f.geometry.coordinates)
                .addTo(map)
        );
    });
}

window.setArrowsVisible = function(v) {
    _arrowsVisible = !!v;
    if (_arrowsVisible && GPX_VISIBLE) {
        addArrowLayer();
        _arrowMarkerList.forEach(function(m) {
            if (m.getElement()) m.getElement().style.display = '';
        });
    } else {
        _arrowMarkerList.forEach(function(m) {
            if (m.getElement()) m.getElement().style.display = 'none';
        });
    }
};

// ── 정지 감지 ──────────────────────────────────────────────────
function _detectStops(minSec, maxMps) {
    if (!hasGpx() || GPX.points.length < 2) return [];
    minSec = minSec || 60;
    maxMps = maxMps || 0.8;

    function _calcSpd(i) {
        if (i <= 0) return null;
        var raw = GPX.points[i].speed_mps;
        if (raw !== null && raw !== undefined) {
            var v = Number(raw);
            if (Number.isFinite(v) && v >= 0) return v;
        }
        var p0 = GPX.points[i - 1], p1 = GPX.points[i];
        var dD = Number(p1.dist_m || 0) - Number(p0.dist_m || 0);
        var t0 = _parseTimeMs(p0.time), t1 = _parseTimeMs(p1.time);
        if (t0 === null || t1 === null || t1 <= t0 || dD < 0) return null;
        return dD / ((t1 - t0) / 1000.0);
    }

    var stops = [], inStop = false, stopStart = -1;

    function _flush(endIdx) {
        var ps = GPX.points[stopStart], pe = GPX.points[endIdx];
        var ts = _parseTimeMs(ps.time), te = _parseTimeMs(pe.time);
        var dur = (ts !== null && te !== null && te > ts) ? (te - ts) / 1000 : 0;
        if (dur < minSec) return;
        var lats = [], lons = [];
        for (var j = stopStart; j <= endIdx; j++) {
            lats.push(GPX.points[j].lat);
            lons.push(GPX.points[j].lon);
        }
        stops.push({
            lat:         lats.reduce(function(a,b){return a+b;},0) / lats.length,
            lon:         lons.reduce(function(a,b){return a+b;},0) / lons.length,
            durationSec: dur
        });
    }

    for (var i = 0; i < GPX.points.length; i++) {
        var spd   = _calcSpd(i);
        var still = (spd !== null && spd <= maxMps); 
        if (still && !inStop)       { inStop = true; stopStart = i; }
        else if (!still && inStop)  { _flush(i - 1); inStop = false; }
    }
    if (inStop) _flush(GPX.points.length - 1);
    return stops;
}

function _buildStopEl(stop) {
    var dur = stop.durationSec;
    var mins = Math.floor(dur / 60);
    var label = dur >= 3600
        ? Math.floor(dur / 3600) + 'h' + String(Math.floor((dur % 3600) / 60)).padStart(2,'0') + 'm'
        : mins + 'm';

    var size = Math.min(52, Math.max(32, 26 + Math.floor(dur / 90)));

    var el = document.createElement('div');
    el.style.cssText =
        'width:' + size + 'px;' +
        'height:' + size + 'px;' +
        'border-radius:50%;' +
        'background:rgba(30,40,60,0.88);' +        
        'border:2px solid rgba(140,200,255,0.90);' + 
        'display:flex;align-items:center;justify-content:center;' +
        'color:#c8e8ff;' +                       
        'font-size:9px;font-weight:bold;text-align:center;line-height:1.2;' +
        'box-shadow:0 2px 8px rgba(0,0,0,.65);' +
        'pointer-events:none;';
    el.textContent = label;
    return el;
}

function _clearStopMarkers() {
    _stopMarkerList.forEach(function(m) { m.remove(); });
    _stopMarkerList = [];
}

function _addStopMarkers() {
    _clearStopMarkers();
    _detectStops(60, 0.8).forEach(function(s) {
        var marker = new maplibregl.Marker({ element: _buildStopEl(s), anchor: 'center' })
            .setLngLat([s.lon, s.lat])
            .addTo(map);
        _stopMarkerList.push(marker);
    });
}

window.setStopMarkersVisible = function(v) {
    _stopMarkersVisible = !!v;
    if (_stopMarkersVisible && GPX_VISIBLE) _addStopMarkers();
    else _clearStopMarkers();
};

// ── 사진 시퀀스 재생 ───────────────────────────────────────────
function _buildPlaybackPoints() {
    return points
        .filter(function(p) { return !!p.date_taken; })
        .sort(function(a, b) {
            return (_parseNaiveDateTimeMs(a.date_taken) || 0)
                 - (_parseNaiveDateTimeMs(b.date_taken) || 0);
        });
}

function _playbackStep() {
    if (!_playbackPoints.length) { window.stopPhotoPlayback(); return; }
    _playbackIndex = (_playbackIndex + 1) % _playbackPoints.length;
    var pt = _playbackPoints[_playbackIndex];

    // 지도 강조 + 이동
    currentFp = pt.filepath;
    _updateStyle();
    renderThumbs();
    map.easeTo({ center: [pt.lon, pt.lat],
        zoom: Math.max(map.getZoom(), 13), duration: 350, essential: true });

    // GPX 포인트 동기화 (5분 이내 매칭)
    if (hasGpx() && pt.date_taken) {
        var photoMs = _parseNaiveDateTimeMs(pt.date_taken);
        if (photoMs !== null) {
            var adj = photoMs - _effectiveTimeOffsetHours() * 3600000;
            var bestIdx = -1, bestDiff = Infinity;
            GPX.points.forEach(function(gp, idx) {
                var t = _parseTimeMs(gp.time);
                if (t === null) return;
                var d = Math.abs(t - adj);
                if (d < bestDiff) { bestDiff = d; bestIdx = idx; }
            });
            if (bestIdx >= 0 && bestDiff < 300000) selectGpxIndex(bestIdx, false);
        }
    }
    document.title = 'PHOTO:' + encodeURIComponent(pt.filepath);
}

window.startPhotoPlayback = function(intervalMs) {
    if (_playbackTimer) clearInterval(_playbackTimer);
    _playbackPoints = _buildPlaybackPoints();
    if (!_playbackPoints.length) return;
    _playbackIndex = -1;
    _playbackStep();
    _playbackTimer = setInterval(_playbackStep, intervalMs || 2500);
};

window.stopPhotoPlayback = function() {
    var wasActive = !!_playbackTimer;  
    if (_playbackTimer) { clearInterval(_playbackTimer); _playbackTimer = null; }
    _playbackIndex = -1;
    if (wasActive) document.title = 'ACTION:PLAYBACK_STOP'; 
};

window.isPlaybackActive = function() { return _playbackTimer !== null; };

// ─────────────────────────────────────────────────────────────
// 선택 포인트 수직선 + 원형 핸들 (원본 drawElevation 인라인 코드 추출)
// ─────────────────────────────────────────────────────────────
function _drawSelectedPoint(ctx, stats, xOfDist, yOfEle, padT, plotH, totalDist) {
    if (selectedGpxIndex < 0 || selectedGpxIndex >= GPX.points.length) return;

    var pt  = GPX.points[selectedGpxIndex];
    var sx  = xOfDist(pt.dist_m);
    var hasEle = (pt.ele !== null && pt.ele !== undefined && Number.isFinite(Number(pt.ele)));

    // 수직선
    ctx.beginPath();
    ctx.moveTo(sx, padT); ctx.lineTo(sx, padT + plotH);
    ctx.strokeStyle = 'rgba(255,154,47,.60)';
    ctx.lineWidth = 1; ctx.stroke();

    // 원형 핸들 (고도 있을 때만 y 좌표 정확히, 없으면 중앙)
    var sy = hasEle ? yOfEle(Number(pt.ele)) : padT + plotH / 2;
    ctx.beginPath(); ctx.arc(sx, sy, 5, 0, Math.PI * 2);
    ctx.fillStyle   = '#ff9a2f'; ctx.fill();
    ctx.lineWidth   = 2;
    ctx.strokeStyle = '#ffffff'; ctx.stroke();
}

window.fitAll = function() {
    var coords = [];
    points.forEach(function(p) { coords.push([Number(p.lon), Number(p.lat)]); });
    if (hasGpx() && GPX.route && GPX.route.length) {
        GPX.route.forEach(function(c) { coords.push([Number(c[0]), Number(c[1])]); });
    }
    if (!coords.length) return;
    var lons = coords.map(function(c) { return c[0]; });
    var lats = coords.map(function(c) { return c[1]; });
    var w = Math.min.apply(null, lons), e = Math.max.apply(null, lons);
    var s = Math.min.apply(null, lats), n = Math.max.apply(null, lats);
    if (w === e && s === n) {
        map.easeTo({ center:[w,s], zoom:Math.min(14, ${max_zoom}), duration:500 });
    } else {
        map.fitBounds([[w,s],[e,n]], {
            padding: {
            top: 52, right: 52,
            bottom: (ELEV_VISIBLE && hasGpxPanelData()) ? 270 : 52,
            left: 52
            },
            maxZoom: ${max_zoom}, duration: 500
        });
    }
};
function _enrichGpxSpeed() {
    if (!hasGpx()) return;
    var pts = GPX.points;

    for (var i = 1; i < pts.length; i++) {
        if (pts[i].speed_mps !== null && pts[i].speed_mps !== undefined
                && Number.isFinite(Number(pts[i].speed_mps))) continue;

        var dD = Number(pts[i].dist_m || 0) - Number(pts[i-1].dist_m || 0);
        var t0 = _parseTimeMs(pts[i-1].time);
        var t1 = _parseTimeMs(pts[i].time);

        if (t0 !== null && t1 !== null && t1 > t0 && dD >= 0) {
            pts[i].speed_mps = dD / ((t1 - t0) / 1000.0);
        } else {
            pts[i].speed_mps = null;
        }
    }
    if (pts[0].speed_mps === null || pts[0].speed_mps === undefined ||
        !Number.isFinite(Number(pts[0].speed_mps))) {

    var fallbackSpd = (pts.length > 1 &&
                        pts[1].speed_mps !== null &&
                        pts[1].speed_mps !== undefined &&
                        Number.isFinite(Number(pts[1].speed_mps)))
        ? Number(pts[1].speed_mps)
        : 0;
    pts[0].speed_mps = fallbackSpd;
    }

    if (!GPX.sensors) GPX.sensors = {};
    if (!GPX.sensors.available) GPX.sensors.available = {};
    var hasSpeed = pts.some(function(p) {
        return p.speed_mps !== null && p.speed_mps !== undefined
            && Number.isFinite(p.speed_mps);
    });
    GPX.sensors.available.speed = hasSpeed;
    if (hasSpeed) GPX_HAS_SENSORS = true; 
}

window._reloadGpx = function(data) {
  if (!data || !data.points || !data.points.length) {
    console.warn('[GpsMap] _reloadGpx: 빈 GPX 데이터');
    return;
  }

  removeGpxLayers();

  GPX = data;
  _enrichGpxSpeed();
  GPX_HAS_ELEVATION = !!(data && data.has_elevation);
  GPX_HAS_SENSORS = !!(data && data.sensors &&
    Object.values(data.sensors.available || {}).some(Boolean));
  GPX_VISIBLE = true;
  AUTO_TIME_OFFSET_HOURS = null;

  addGpxLayers();

  if (hasGpxPanelData()) {
    ELEV_VISIBLE = true;
    setElevationVisible(true);  
    _buildSensorDock();
  } else {
    ELEV_VISIBLE = false;
    setElevationVisible(false);
  }

  if (GPX.bounds) {
    var b = GPX.bounds;
    map.fitBounds([[b.west, b.south], [b.east, b.north]], {
      padding: {
        top: 52, right: 52,
        bottom: (ELEV_VISIBLE && hasGpxPanelData()) ? 270 : 52,
        left: 52
      },
      maxZoom: map.getMaxZoom(),
      duration: 500
    });
  }

  if (window._onGpxLoaded) window._onGpxLoaded(data);
};

// ── GPX 레이어 이벤트 핸들러 ─────────────────────────────────
map.on('click', 'gpx-points-hit', function(e) {
    e.preventDefault();
    if (!e.features || !e.features.length) return;
    selectGpxIndex(Number(e.features[0].properties.idx), false);
});
map.on('click', 'gpx-route-hit', function(e) {
    e.preventDefault();
    if (!e.lngLat) return;
    var idx = findNearestGpxIndexByLngLat(e.lngLat);
    if (idx >= 0) selectGpxIndex(idx, false);
});
map.on('mouseenter', 'gpx-points-hit', function() { map.getCanvas().style.cursor = 'pointer'; });
map.on('mouseleave', 'gpx-points-hit', function() { map.getCanvas().style.cursor = ''; });
map.on('mouseenter', 'gpx-route-hit', function() { map.getCanvas().style.cursor = 'pointer'; });
map.on('mouseleave', 'gpx-route-hit', function() { map.getCanvas().style.cursor = ''; });

// ── resize 핸들러 (elevation 함수 사용) ───────────────────────
window.addEventListener('resize', function() {
    drawElevation();
    buildStartEndMarkers();
    syncAttributionWithElevation();
});

// ── Canvas 바인딩 ────────────────────────────────────────────
bindElevationCanvas();

window._onMapLoaded = function() {
  addGpxLayers();

  if (hasGpx()) {
    _enrichGpxSpeed();
    setGpxVisible(GPX_VISIBLE);
    if (hasGpxPanelData()) {
        setElevationVisible(ELEV_VISIBLE);
        _buildSensorDock(); 
    }
    selectGpxIndex(0, false);
  }

  syncAttributionWithElevation();
  window.fitAll();
  signalReady();
};

// ── 캡처 ─────────────────────────────────────────────────────
function _loadHtml2Canvas(cb) {
    if (window.html2canvas) { cb(true); return; }
    var s = document.createElement('script');
    s.src = 'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js';
    s.onload  = function() { cb(true); };
    s.onerror = function() { cb(false); };
    document.head.appendChild(s);
}

window._captureMap = function(mode) {
    var capMenu = document.getElementById('btb-cap-menu');
    if (capMenu) capMenu.style.display = 'none';

    if (mode === 'clipboard' && navigator.clipboard && window.ClipboardItem) {
        var _blobResolve = null;
        var blobPromise = new Promise(function(resolve) { _blobResolve = resolve; });

        navigator.clipboard.write([new ClipboardItem({'image/png': blobPromise})])
            .then(function() { _captureToast(window._I18N.toolbar.cap_clipboard_done); })
            .catch(function(e) {
                console.warn('[GpsMap] clipboard 실패:', e);
                blobPromise.then(function(blob) {
                    var ts = new Date().toISOString().slice(0,19).replace(/[-T:]/g,'_');
                    _blobDownload(blob, ts + '_dodoRynx.png');
                });
            });

        // 비동기로 캡처 후 Promise resolve
        map.triggerRepaint();
        map.once('render', function() {
            _buildCaptureCanvas(function(out) {
                out.toBlob(function(blob) {
                    if (_blobResolve) _blobResolve(blob);
                }, 'image/png');
            });
        });
        return;
    }

    // download 모드 (ClipboardItem 미지원 포함)
    map.triggerRepaint();
    map.once('render', function() {
        _buildCaptureCanvas(function(out) {
            _captureFinish(out, mode);
        });
    });
};

// ── roundRect 폴리필 (Chrome 99 미만 대비) ──────────────────
function _ensureRoundRect(ctx) {
    if (ctx.roundRect) return;
    ctx.roundRect = function(x, y, w, h, r) {
        r = Math.min(r || 0, w / 2, h / 2);
        this.moveTo(x + r, y);
        this.lineTo(x + w - r, y);
        this.arcTo(x + w, y, x + w, y + r, r);
        this.lineTo(x + w, y + h - r);
        this.arcTo(x + w, y + h, x + w - r, y + h, r);
        this.lineTo(x + r, y + h);
        this.arcTo(x, y + h, x, y + h - r, r);
        this.lineTo(x, y + r);
        this.arcTo(x, y, x + r, y, r);
        this.closePath();
    };
}

// ── thumbMarkers 배열 기반 마커 합성 (기존 첫 번째 함수 교체) ──────
function _drawMarkersOnCanvas(ctx, dpr) {
    _ensureRoundRect(ctx);

    var toolbar = document.getElementById('btb');
    var tbH = toolbar ? toolbar.offsetHeight : 0;
    var markers = document.querySelectorAll('.maplibregl-marker');

    markers.forEach(function(markerEl) {
        var cs = window.getComputedStyle(markerEl);
        if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return;
        var r = markerEl.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) return;

        var inner  = markerEl.children[0];
        var img    = inner ? inner.querySelector('img') : null;

        if (img && img.complete && img.naturalWidth > 0) {
            var dotEl  = inner.children[0];
            var cardEl = inner.children[1];

            if (dotEl) {
                var dr = dotEl.getBoundingClientRect();
                if (dr.width > 0) {
                    var ds = dr.width * dpr;
                    var dx = dr.left * dpr;
                    var dy = (dr.top - tbH) * dpr;
                    ctx.save();
                    ctx.beginPath();
                    ctx.arc(dx + ds/2, dy + ds/2, ds/2, 0, Math.PI * 2);
                    ctx.fillStyle = dotEl.style.background || dotEl.style.backgroundColor || '#4a9eff';
                    ctx.fill();
                    ctx.strokeStyle = '#fff';
                    ctx.lineWidth = 2 * dpr;
                    ctx.stroke();
                    ctx.restore();
                }
            }

            // card + image (border-radius clip + object-fit:cover 시뮬레이션)
            var cardR = cardEl ? cardEl.getBoundingClientRect() : r;
            if (cardR && cardR.width > 0 && cardR.height > 0) {
                var side = Math.max(8, Math.min(cardR.width, cardR.height));
                var cx = (cardR.left + (cardR.width  - side) / 2) * dpr;
                var cy = (cardR.top  + (cardR.height - side) / 2 - tbH) * dpr;
                var cw = side * dpr;
                var ch = side * dpr;
                var rad = 6 * dpr;

                // 흰 테두리
                ctx.save();
                ctx.beginPath();
                ctx.roundRect(cx - dpr, cy - dpr, cw + 2*dpr, ch + 2*dpr, rad + dpr);
                ctx.strokeStyle = 'rgba(255,255,255,0.9)';
                ctx.lineWidth = 2 * dpr;
                ctx.stroke();
                ctx.restore();

                // 이미지 (rounded clip + cover)
                ctx.save();
                ctx.beginPath();
                ctx.roundRect(cx, cy, cw, ch, rad);
                ctx.clip();
                try {
                    var iw = img.naturalWidth, ih = img.naturalHeight;
                    var scale = Math.max(cw / iw, ch / ih);
                    var sw = iw * scale, sh = ih * scale;
                    ctx.drawImage(img, cx + (cw - sw)/2, cy + (ch - sh)/2, sw, sh);
                } catch(e) {
                    ctx.fillStyle = 'rgba(40,50,70,0.9)';
                    ctx.fill();
                }
                ctx.restore();

                // 배지 (숫자 카운트)
                var badgeEl = cardEl.querySelector('.th-pin-badge');
                if (badgeEl && badgeEl.textContent.trim()) {
                    var bt = badgeEl.textContent.trim();
                    ctx.save();
                    ctx.font = 'bold ' + Math.round(9 * dpr) + 'px -apple-system,sans-serif';
                    var bW = ctx.measureText(bt).width + 8 * dpr;
                    var bH = 15 * dpr;
                    var bX = cx + cw - bW - 2 * dpr;
                    var bY = cy + 2 * dpr;
                    ctx.beginPath();
                    ctx.roundRect(bX, bY, bW, bH, 3 * dpr);
                    ctx.fillStyle = 'rgba(26,90,154,0.95)';
                    ctx.fill();
                    ctx.fillStyle = '#fff';
                    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
                    ctx.fillText(bt, bX + bW/2, bY + bH/2);
                    ctx.restore();
                }
            }
            return;
        }

        // ── 클러스터 / GPX 마커 ──
        var x = r.left * dpr, y = (r.top - tbH) * dpr;
        var w = r.width * dpr, h = r.height * dpr;
        var cntEl = markerEl.querySelector('[class*="count"],[class*="cluster"]')
                 || (inner && inner.firstElementChild);
        var text = cntEl ? cntEl.textContent.trim() : (inner ? inner.textContent.trim() : '');
        if (text && /^\d/.test(text)) _drawClusterPin(ctx, x, y, w, h, text, dpr);
        else                          _drawFallbackPin(ctx, x, y, w, h, dpr);
    });
}

function _drawGpxFlagOnCanvas(ctx, markerEl, tbH, dpr) {
    _ensureRoundRect(ctx);
    var badgeEl = markerEl.querySelector('.gpx-flag-badge');
    var stemEl  = markerEl.querySelector('.gpx-flag-stem');
    var dotEl2  = markerEl.querySelector('.gpx-flag-dot');
    if (!badgeEl) return;

    var text    = (badgeEl.textContent || '').trim();
    var isStart = /^start$/i.test(text);
    var fill    = isStart ? 'rgba(38,191,103,.96)' : 'rgba(236,104,104,.96)';

    // badge
    var br = badgeEl.getBoundingClientRect();
    if (br.width > 0) {
        var bx = br.left * dpr, by = (br.top - tbH) * dpr;
        var bw = br.width * dpr, bh = br.height * dpr;
        ctx.save();
        ctx.beginPath(); ctx.roundRect(bx, by, bw, bh, 10 * dpr);
        ctx.fillStyle = fill; ctx.fill();
        ctx.strokeStyle = 'rgba(255,255,255,0.22)'; ctx.lineWidth = dpr; ctx.stroke();
        ctx.fillStyle = '#fff';
        ctx.font = '700 ' + Math.round(10 * dpr) + 'px -apple-system,sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(text, bx + bw / 2, by + bh / 2);
        ctx.restore();
    }

    // stem
    if (stemEl) {
        var sr = stemEl.getBoundingClientRect();
        if (sr.height > 0) {
            var scx = (sr.left + sr.width / 2) * dpr;
            ctx.save();
            ctx.beginPath();
            ctx.moveTo(scx, (sr.top  - tbH) * dpr);
            ctx.lineTo(scx, (sr.top + sr.height - tbH) * dpr);
            ctx.strokeStyle = 'rgba(255,255,255,0.75)';
            ctx.lineWidth = Math.max(1.5 * dpr, sr.width * dpr);
            ctx.stroke();
            ctx.restore();
        }
    }

    // dot
    if (dotEl2) {
        var ddr = dotEl2.getBoundingClientRect();
        if (ddr.width > 0) {
            var ddcx = (ddr.left + ddr.width  / 2) * dpr;
            var ddcy = (ddr.top  + ddr.height / 2 - tbH) * dpr;
            var ddr2  = Math.max(3 * dpr, Math.min(ddr.width, ddr.height) * dpr / 2);
            ctx.save();
            ctx.beginPath(); ctx.arc(ddcx, ddcy, ddr2, 0, Math.PI * 2);
            ctx.fillStyle = fill; ctx.fill();
            ctx.strokeStyle = '#fff'; ctx.lineWidth = 2 * dpr; ctx.stroke();
            ctx.restore();
        }
    }
}

// ── 캡처 전용: 프리로드된 이미지로 마커 합성 ─────────────────────
function _drawMarkersWithFreshImages(ctx, dpr, tbH, freshImgs) {
    _ensureRoundRect(ctx);

    document.querySelectorAll('.maplibregl-marker').forEach(function(markerEl) {
        var cs = window.getComputedStyle(markerEl);
        if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return;
        var r = markerEl.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) return;

        // ── ① GPX START/END 플래그 ─────────────────────────
        if (markerEl.querySelector('.gpx-flag-badge')) {
            _drawGpxFlagOnCanvas(ctx, markerEl, tbH, dpr);
            return;
        }

        // ── ② 사진 핀 (썸네일) ─────────────────────────────
        var thumbSrc = _getMarkerThumbSrc(markerEl);
        if (thumbSrc) {
            var freshImg = freshImgs[thumbSrc];

            // 카드 요소 탐색: img를 포함한 가장 안쪽 div
            var cardEl = null;
            var allDivs = Array.prototype.slice.call(markerEl.querySelectorAll('div'));
            for (var ci = 0; ci < allDivs.length; ci++) {
                if (allDivs[ci].querySelector('img')) { cardEl = allDivs[ci]; break; }
            }
            var rawCardR = (cardEl || markerEl).getBoundingClientRect();

            if (rawCardR.width > 4 && rawCardR.height > 4) {
                // ── 정사각형 crop ─────────────────────────────
                var side = Math.min(rawCardR.width, rawCardR.height);
                var cx   = (rawCardR.left + (rawCardR.width  - side) / 2) * dpr;
                var cy   = (rawCardR.top  + (rawCardR.height - side) / 2 - tbH) * dpr;
                var cw   = side * dpr;
                var ch   = side * dpr;
                var rad  = Math.min(6 * dpr, cw / 4);

                // 흰 테두리
                ctx.save();
                ctx.beginPath();
                ctx.roundRect(cx - dpr, cy - dpr, cw + 2*dpr, ch + 2*dpr, rad + dpr);
                ctx.strokeStyle = 'rgba(255,255,255,0.90)';
                ctx.lineWidth = 2 * dpr; ctx.stroke();
                ctx.restore();

                // 이미지 (object-fit: cover)
                ctx.save();
                ctx.beginPath(); ctx.roundRect(cx, cy, cw, ch, rad); ctx.clip();
                if (freshImg && freshImg.naturalWidth > 0) {
                    try {
                        var iw = freshImg.naturalWidth, ih = freshImg.naturalHeight;
                        var sc = Math.max(cw / iw, ch / ih);
                        ctx.drawImage(freshImg,
                            cx + (cw - iw*sc) / 2,
                            cy + (ch - ih*sc) / 2,
                            iw*sc, ih*sc);
                    } catch(e) {
                        ctx.fillStyle = 'rgba(40,50,70,0.9)';
                        ctx.fillRect(cx, cy, cw, ch);
                    }
                } else {
                    ctx.fillStyle = 'rgba(40,50,70,0.9)';
                    ctx.fillRect(cx, cy, cw, ch);
                }
                ctx.restore();

                // 클러스터 숫자 배지
                var badgeEl = markerEl.querySelector(
                    '[class*="badge"],[class*="cnt"],[class*="count"],[class*="num"]'
                );
                if (badgeEl) {
                    var bt = (badgeEl.textContent || '').trim();
                    if (bt && /^\d/.test(bt)) {
                        var bR = badgeEl.getBoundingClientRect();
                        if (bR.width > 0) {
                            var bx = bR.left*dpr, by = (bR.top-tbH)*dpr;
                            var bw = bR.width*dpr, bh = bR.height*dpr;
                            ctx.save();
                            ctx.beginPath(); ctx.roundRect(bx, by, bw, bh, 3*dpr);
                            ctx.fillStyle = 'rgba(26,90,154,0.95)'; ctx.fill();
                            ctx.fillStyle = '#fff';
                            ctx.font = 'bold ' + Math.round(9*dpr) + 'px -apple-system,sans-serif';
                            ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
                            ctx.fillText(bt, bx+bw/2, by+bh/2);
                            ctx.restore();
                        }
                    }
                }

                // ── stem + dot ──────────────────────
                var gpsPx    = _getAnchorPxForThumbMarker(markerEl, thumbSrc);
                var anchorX  = gpsPx
                    ? gpsPx.x * dpr
                    : (r.left + r.width / 2) * dpr; 
                var anchorY  = gpsPx
                    ? gpsPx.y * dpr
                    : (r.bottom - tbH) * dpr;  

                var cardBotX  = (rawCardR.left + rawCardR.width  / 2) * dpr;
                var cardBotY  = (rawCardR.top  + rawCardR.height - tbH) * dpr;

                // stem: 카드 하단 중앙 → GPS 점
                if (Math.abs(anchorY - cardBotY) > 3 * dpr ||
                    Math.abs(anchorX - cardBotX) > 3 * dpr) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.moveTo(cardBotX, cardBotY);
                    ctx.lineTo(anchorX, anchorY);
                    ctx.strokeStyle = 'rgba(255,255,255,0.82)';
                    ctx.lineWidth = 2 * dpr;
                    ctx.stroke();
                    ctx.restore();
                }

                // dot: GPS 위치에 원
                ctx.save();
                ctx.beginPath();
                ctx.arc(anchorX, anchorY, 5 * dpr, 0, Math.PI * 2);
                ctx.fillStyle = '#ff9a2f';
                ctx.fill();
                ctx.strokeStyle = '#fff';
                ctx.lineWidth = 2 * dpr;
                ctx.stroke();
                ctx.restore();

                // stem: card 하단 → GPS 점
                if (anchorY > cardBotY + 3 * dpr) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.moveTo(anchorX, cardBotY);
                    ctx.lineTo(anchorX, anchorY);
                    ctx.strokeStyle = 'rgba(255,255,255,0.82)';
                    ctx.lineWidth = 2 * dpr;
                    ctx.stroke();
                    ctx.restore();
                }

                // dot: GPS 위치에 원
                ctx.save();
                ctx.beginPath();
                ctx.arc(anchorX, anchorY, 5 * dpr, 0, Math.PI * 2);
                ctx.fillStyle = '#ff9a2f';
                ctx.fill();
                ctx.strokeStyle = '#fff';
                ctx.lineWidth = 2 * dpr;
                ctx.stroke();
                ctx.restore();
            }
            return;
        }

        // ── ③ 클러스터 / 기타 마커 ─────────────────────────
        var mx = r.left * dpr, my = (r.top - tbH) * dpr;
        var mw = r.width * dpr, mh = r.height * dpr;
        var inner = markerEl.firstElementChild;
        var cntEl = inner
            ? inner.querySelector('[class*="count"],[class*="cluster"]')
            : null;
        var text = (cntEl || inner || markerEl).textContent.trim();
        if (text && /^\d/.test(text))
            _drawClusterPin(ctx, mx, my, mw, mh, text, dpr);
        else
            _drawFallbackPin(ctx, mx, my, mw, mh, dpr);
    });
}

function _getAnchorPxForThumbMarker(markerEl, thumbSrc) {
    var fp = markerEl.getAttribute('data-fp')
          || (markerEl.firstElementChild
              && markerEl.firstElementChild.getAttribute('data-fp'));
    if (fp && typeof points !== 'undefined') {
        for (var i = 0; i < points.length; i++) {
            if (points[i] && points[i].filepath === fp)
                return map.project([points[i].lon, points[i].lat]);
        }
    }
    if (thumbSrc && typeof points !== 'undefined') {
        for (var j = 0; j < points.length; j++) {
            if (points[j] && points[j].thumb_url === thumbSrc)
                return map.project([points[j].lon, points[j].lat]);
        }
    }
    return null;
}

// ── Attribution(저작권) + Scale(축척) 수동 드로우 ────────────────
function _drawMapControlsOnCanvas(ctx, W, H, dpr, tbH) {
    tbH = tbH || 0;

    var attrEl = document.querySelector('.maplibregl-ctrl-attrib-inner');
    if (attrEl) {
        var attrR = attrEl.getBoundingClientRect();
        var text  = attrEl.textContent.replace(/\s+/g, ' ').trim();
        if (text && attrR.width > 0) {
            var ax = attrR.left * dpr, ay = (attrR.top - tbH) * dpr;
            var aw = attrR.width * dpr, ah = attrR.height * dpr;
            ctx.save();
            ctx.fillStyle = 'rgba(255,255,255,0.85)';
            ctx.fillRect(ax - 4*dpr, ay - 2*dpr, aw + 8*dpr, ah + 4*dpr);
            ctx.fillStyle = '#333';
            ctx.font = Math.round(10 * dpr) + 'px -apple-system,sans-serif';
            ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
            ctx.fillText(text, ax + 2*dpr, ay + ah / 2);
            ctx.restore();
        }
    }

    // Scale bar — 실제 DOM 위치 기준
    var scaleEl = document.querySelector('.maplibregl-ctrl-scale');
    if (scaleEl) {
        var scR   = scaleEl.getBoundingClientRect();
        var label = scaleEl.textContent.trim();
        if (scR.width > 0) {
            var sx = scR.left * dpr, sy = (scR.top - tbH) * dpr;
            var sw = scR.width * dpr, sh = scR.height * dpr;
            var lineY = sy + sh * 0.70;
            ctx.save();
            ctx.fillStyle = 'rgba(255,255,255,0.85)';
            ctx.fillRect(sx - 3*dpr, sy - 2*dpr, sw + 6*dpr, sh + 4*dpr);
            ctx.strokeStyle = '#444'; ctx.lineWidth = 1.5 * dpr;
            ctx.beginPath();
            ctx.moveTo(sx, lineY);      ctx.lineTo(sx + sw, lineY);
            ctx.moveTo(sx, lineY - 4*dpr); ctx.lineTo(sx, lineY + 4*dpr);
            ctx.moveTo(sx + sw, lineY - 4*dpr); ctx.lineTo(sx + sw, lineY + 4*dpr);
            ctx.stroke();
            ctx.fillStyle = '#333';
            ctx.font = Math.round(9 * dpr) + 'px -apple-system,sans-serif';
            ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
            ctx.fillText(label, sx + sw / 2, lineY - 2*dpr);
            ctx.restore();
        }
    }
}

function _drawElevationDockOnCapture(ctx, dock, elevCanvas, tbH, dpr) {
    _ensureRoundRect(ctx);

    var dockR = dock.getBoundingClientRect();
    var dx = Math.round(dockR.left * dpr);
    var dy = Math.round((dockR.top - tbH) * dpr);
    var dw = Math.round(dockR.width * dpr);
    var dh = Math.round(dockR.height * dpr);
    var rad = 12 * dpr;

    // 도크 외곽 배경 + 그림자
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,0.45)';
    ctx.shadowBlur  = 20 * dpr;
    ctx.shadowOffsetY = 4 * dpr;
    ctx.beginPath(); ctx.roundRect(dx, dy, dw, dh, rad);
    ctx.fillStyle = 'rgba(17,22,29,0.94)'; ctx.fill();
    ctx.restore();

    // 테두리
    ctx.save();
    ctx.beginPath(); ctx.roundRect(dx, dy, dw, dh, rad);
    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    ctx.lineWidth = dpr; ctx.stroke();
    ctx.restore();

    // 고도 차트 캔버스
    var elevR = elevCanvas.getBoundingClientRect();
    ctx.drawImage(
        elevCanvas,
        Math.round(elevR.left * dpr),
        Math.round((elevR.top - tbH) * dpr)
    );

    // 헤더 구역 — elevationSub 텍스트
    var subEl = document.getElementById('elevationSub');
    if (subEl) {
        var subR = subEl.getBoundingClientRect();
        if (subR.width > 0) {
            ctx.save();
            ctx.font = Math.round(11 * dpr) + 'px -apple-system,Segoe UI,sans-serif';
            ctx.fillStyle = '#c0cad4';
            ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
            ctx.fillText(
                subEl.textContent || '',
                subR.left * dpr,
                (subR.top + subR.height / 2 - tbH) * dpr
            );
            ctx.restore();
        }
    }

    // 헤더 구역 — chip 목록
    var headRight = document.getElementById('elevationHeadRight');
    if (headRight) {
        headRight.querySelectorAll('.elevation-chip').forEach(function(chip) {
            var cr = chip.getBoundingClientRect();
            if (cr.width < 2) return;
            var chipTxt = chip.textContent.trim();
            var cpx = cr.left * dpr, cpy = (cr.top - tbH) * dpr;
            var cpw = cr.width * dpr, cph = cr.height * dpr;
            ctx.save();
            ctx.beginPath(); ctx.roundRect(cpx, cpy, cpw, cph, 4 * dpr);
            ctx.fillStyle = 'rgba(255,255,255,0.07)'; ctx.fill();
            ctx.strokeStyle = 'rgba(255,255,255,0.14)'; ctx.lineWidth = dpr; ctx.stroke();
            ctx.fillStyle = '#9eb3c2';
            ctx.font = Math.round(10 * dpr) + 'px -apple-system,Segoe UI,sans-serif';
            ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.fillText(chipTxt, cpx + cpw / 2, cpy + cph / 2);
            ctx.restore();
        });
    }
}

// 캔버스 합성 공통 로직을 별도 함수로 분리
function _buildCaptureCanvas(cb) {
    var toolbar = document.getElementById('btb');
    var tbH = toolbar ? toolbar.offsetHeight : 0;

    var glCanvas = map.getCanvas();
    var dpr = Math.max(1, window.devicePixelRatio || 1);
    var W = glCanvas.width, H = glCanvas.height;

    var out = document.createElement('canvas');
    out.width = W; out.height = H;
    var ctx = out.getContext('2d');
    _ensureRoundRect(ctx);

    // 1. 지도 타일
    ctx.fillStyle = '#12161e';
    ctx.fillRect(0, 0, W, H);
    ctx.drawImage(glCanvas, 0, 0);

    // 2. 썸네일 수집
    var imgJobs = [];
    document.querySelectorAll('.maplibregl-marker').forEach(function(markerEl) {
        var cs = window.getComputedStyle(markerEl);
        if (cs.display === 'none' || cs.visibility === 'hidden') return;
        var thumbSrc = _getMarkerThumbSrc(markerEl);
        if (thumbSrc) imgJobs.push({ markerEl: markerEl, src: thumbSrc });
    });

    var freshImgs = {};
    var remaining = imgJobs.length;

    function _proceed() {
        // 3. 마커 렌더
        _drawMarkersWithFreshImages(ctx, dpr, tbH, freshImgs);

        // 4. Attribution + Scale
        _drawMapControlsOnCanvas(ctx, W, H, dpr, tbH);

        // 5. 고도 패널
        var elevCanvas = document.getElementById('elevationCanvas');
        var dock = document.getElementById('elevationDock');
        if (elevCanvas && dock && !dock.classList.contains('hidden')) {
            _drawElevationDockOnCapture(ctx, dock, elevCanvas, tbH, dpr);
        }

        // 6. 워터마크
        ctx.save();
        ctx.fillStyle = 'rgba(255,255,255,0.28)';
        ctx.font = 'bold ' + Math.round(11 * dpr) + 'px -apple-system,sans-serif';
        ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
        ctx.fillText('dodoRynx', W - 10 * dpr, H - 6 * dpr);
        ctx.restore();

        cb(out);
    }

    if (!remaining) { _proceed(); return; }

    imgJobs.forEach(function(job) {
        var img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload  = function() { freshImgs[job.src] = img; if (--remaining === 0) _proceed(); };
        img.onerror = function() {                           if (--remaining === 0) _proceed(); };
        var sep = job.src.indexOf('?') >= 0 ? '&' : '?';
        img.src = job.src + sep + '_nc=' + Date.now();
    });
}

function _drawElevationDockFrameOnCanvas(ctx, dockRect, tbH, dpr) {
    var x = Math.round(dockRect.left * dpr);
    var y = Math.round((dockRect.top - tbH) * dpr);
    var w = Math.round(dockRect.width * dpr);
    var h = Math.round(dockRect.height * dpr);
    var r = 12 * dpr;

    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,0.34)';
    ctx.shadowBlur = 18 * dpr;
    ctx.shadowOffsetY = 4 * dpr;

    ctx.beginPath();
    ctx.roundRect(x, y, w, h, r);
    ctx.fillStyle = 'rgba(17,22,29,0.94)';
    ctx.fill();

    ctx.shadowColor = 'transparent';
    ctx.lineWidth = 1 * dpr;
    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    ctx.stroke();
    ctx.restore();
}

function _drawClusterPin(ctx, x, y, w, h, text, dpr) {
    var r = Math.min(w, h) / 2;
    var cx = x + w / 2, cy = y + h / 2;
    ctx.save();
    // 배경 원
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = '#1a5a9a';
    ctx.fill();
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = Math.round(1.5 * dpr);
    ctx.stroke();
    // 숫자
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold ' + Math.round(11 * dpr) + 'px -apple-system,sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, cx, cy);
    ctx.restore();
}

// ── 마커 썸네일 URL 다중 전략 탐지 ─────────────────────────────
function _getMarkerThumbSrc(markerEl) {
    // ① <img src="..."> 직접 탐색 (src attribute 기준, not property)
    var imgEl = markerEl.querySelector('img');
    if (imgEl) {
        var attrSrc = imgEl.getAttribute('src') || '';
        if (attrSrc && attrSrc !== '' && /^https?:\/\//i.test(attrSrc)) return attrSrc;
        // 상대경로 → 절대 URL
        if (attrSrc && attrSrc !== '') {
            try { return new URL(attrSrc, location.href).href; } catch(e) {}
        }
    }

    // ② inline style background-image (http:// URL만)
    var allEls = [markerEl].concat(Array.prototype.slice.call(markerEl.querySelectorAll('*')));
    for (var i = 0; i < allEls.length; i++) {
        var bg = allEls[i].style.backgroundImage || '';
        if (bg && bg !== 'none') {
            var m = bg.match(/url\(["']?(https?:\/\/[^"')]+)["']?\)/i);
            if (m && m[1]) return m[1];
        }
    }

    // ③ data-fp → points[] lookup
    var fp = markerEl.getAttribute('data-fp') ||
             (markerEl.firstElementChild && markerEl.firstElementChild.getAttribute('data-fp'));
    if (fp && typeof points !== 'undefined' && Array.isArray(points)) {
        for (var j = 0; j < points.length; j++) {
            if (points[j].filepath === fp && points[j].thumb_url)
                return points[j].thumb_url;
        }
    }

    return null;
}

// ── 마커 내 카드 요소 탐색 ────────────────────────────────────────
function _getMarkerCardEl(markerEl) {
    var card = markerEl.querySelector(
        '[class*="card"],[class*="thumb"],[class*="photo"],[class*="image"],[class*="img"]'
    );
    if (card && card !== markerEl) {
        var cr = card.getBoundingClientRect();
        if (cr.width > 4 && cr.height > 4) return card;
    }
    // fallback: markerEl 전체 bbox
    return markerEl;
}

function _drawFallbackPin(ctx, x, y, w, h, dpr) {
    ctx.save();
    ctx.beginPath();
    ctx.arc(x + w/2, y + h/2, Math.min(w,h)/2, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255,154,47,0.85)';
    ctx.fill();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = dpr;
    ctx.stroke();
    ctx.restore();
}

// ── 고도 헤더 수동 드로우 ──────────────────────────────
function _drawElevHeaderOnCanvas(ctx, dockRect, tbH, dpr) {
    var headerEl = document.getElementById('elevationHeader');
    if (!headerEl) return;
    var r = headerEl.getBoundingClientRect();
    var x = Math.round(r.left * dpr);
    var y = Math.round((r.top - tbH) * dpr);
    var w = Math.round(r.width * dpr);
    var h = Math.round(r.height * dpr);

    // 패널 배경
    ctx.save();
    ctx.fillStyle = 'rgba(20,20,20,0.94)';
    ctx.beginPath();
    ctx.roundRect(x, y, w, h + Math.round(dockRect.height * dpr - h), 14 * dpr);
    ctx.fill();

    // 헤더 구분선
    ctx.strokeStyle = 'rgba(255,255,255,0.07)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, y + h);
    ctx.lineTo(x + w, y + h);
    ctx.stroke();
    ctx.restore();

    // 텍스트 칩 (elevationHeadRight 내 .elevation-chip 순회)
    var chips = document.querySelectorAll('#elevationHeadRight .elevation-chip');
    var cx = x + w - 10 * dpr;
    chips.forEach(function(chip) {
        var text = chip.textContent.trim();
        if (!text) return;
        ctx.save();
        ctx.font = Math.round(11 * dpr) + 'px -apple-system,sans-serif';
        var tw = ctx.measureText(text).width;
        var chW = tw + 18 * dpr;
        var chH = 22 * dpr;
        var chX = cx - chW;
        var chY = y + (h - chH) / 2;
        ctx.fillStyle = 'rgba(255,255,255,0.05)';
        ctx.strokeStyle = 'rgba(255,255,255,0.10)';
        ctx.beginPath();
        ctx.roundRect(chX, chY, chW, chH, chH / 2);
        ctx.fill(); ctx.stroke();
        ctx.fillStyle = '#c8d1d6';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(text, chX + chW / 2, chY + chH / 2);
        ctx.restore();
        cx -= chW + 5 * dpr;
    });

    // elevationSub (선택 지점)
    var sub = document.getElementById('elevationSub');
    if (sub && sub.textContent.trim()) {
        ctx.save();
        ctx.font = Math.round(11 * dpr) + 'px -apple-system,sans-serif';
        ctx.fillStyle = '#7ec4ff';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(sub.textContent.trim(), x + 80 * dpr, y + h / 2);
        ctx.restore();
    }
}

// ── 센서바 수동 드로우 ────────────────────────────────
function _drawSensorBarOnCanvas(ctx, dockRect, tbH, dpr) {
    var bar = document.getElementById('elevationSensorBar');
    if (!bar || bar.classList.contains('hidden')) return;
    var r = bar.getBoundingClientRect();
    var x = Math.round(r.left * dpr);
    var y = Math.round((r.top - tbH) * dpr);
    var w = Math.round(r.width * dpr);
    var h = Math.round(r.height * dpr);

    ctx.save();
    ctx.fillStyle = 'rgba(18,18,18,0.84)';
    ctx.strokeStyle = 'rgba(255,255,255,0.10)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, 10 * dpr);
    ctx.fill(); ctx.stroke();

    var chips = bar.querySelectorAll('.sensor-chip');
    var cx = x + 10 * dpr;
    chips.forEach(function(chip) {
        var label = chip.querySelector('input') ? chip.textContent.trim() : chip.textContent.trim();
        var isActive = chip.classList.contains('active');
        var colorMap = {speed:'#5cb8ff', heart_rate:'#ff668a', cadence:'#a98bff', temperature:'#ffb454'};
        var key = chip.dataset ? chip.dataset.sensor : '';
        var color = colorMap[key] || '#aaa';
        ctx.save();
        ctx.font = Math.round(11 * dpr) + 'px -apple-system,sans-serif';
        var tw = ctx.measureText(label).width;
        var cW = tw + 20 * dpr, cH = 26 * dpr;
        var cY = y + (h - cH) / 2;
        ctx.fillStyle = isActive ? color + '1a' : 'rgba(255,255,255,0.04)';
        ctx.strokeStyle = isActive ? color : 'rgba(255,255,255,0.12)';
        ctx.lineWidth = 1;
        ctx.globalAlpha = isActive ? 1.0 : 0.65;
        ctx.beginPath();
        ctx.roundRect(cx, cY, cW, cH, cH / 2);
        ctx.fill(); ctx.stroke();
        ctx.fillStyle = isActive ? color : '#aaa';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(label, cx + cW / 2, cY + cH / 2);
        ctx.restore();
        cx += cW + 6 * dpr;
    });
    ctx.restore();
}

// html2canvas 실패 시 폴백 (지도 캔버스만)
function _captureMapCanvasOnly(mode) {
    map.triggerRepaint();
    map.once('render', function() {
        var c = map.getCanvas();
        var out = document.createElement('canvas');
        out.width = c.width; out.height = c.height;
        var ctx = out.getContext('2d');
        ctx.drawImage(c, 0, 0);
        _captureFinish(out, mode);
    });
}

function _injectExifSoftware(jpegBlob, software, callback) {
    var reader = new FileReader();
    reader.onload = function(e) {
        var src = new Uint8Array(e.target.result);
        if (src[0] !== 0xFF || src[1] !== 0xD8) { callback(jpegBlob); return; }

        // TIFF (little-endian): header + IFD0(1 entry: Software) + value
        var sw     = software + '\0';
        var swLen  = sw.length;
        // IFD0 at offset 8, entry at 10, next-IFD at 22, data at 26
        var swOff  = 26;
        var tSize  = swOff + swLen;
        var t      = new Uint8Array(tSize);
        var dv     = new DataView(t.buffer);
        t[0]=0x49; t[1]=0x49;             // 'II' LE
        dv.setUint16(2, 42, true);         // TIFF magic
        dv.setUint32(4, 8,  true);         // IFD0 offset
        dv.setUint16(8, 1,  true);         // 1 entry
        dv.setUint16(10, 0x0131, true);    // Software tag
        dv.setUint16(12, 2,    true);      // ASCII type
        dv.setUint32(14, swLen, true);     // count
        dv.setUint32(18, (swLen > 4) ? swOff : 0, true); // offset or inline
        dv.setUint32(22, 0, true);         // next IFD = 0
        for (var i = 0; i < swLen; i++) t[swOff + i] = sw.charCodeAt(i) & 0xFF;

        // APP1 = FFE1 + length(BE) + 'Exif\0\0' + TIFF
        var exifId  = [0x45,0x78,0x69,0x66,0x00,0x00];
        var app1Len = 2 + 6 + tSize;      // includes length field itself
        var app1    = new Uint8Array(2 + app1Len);
        app1[0]=0xFF; app1[1]=0xE1;
        app1[2]=(app1Len>>8)&0xFF; app1[3]=app1Len&0xFF;
        for (var j=0;j<6;j++) app1[4+j]=exifId[j];
        app1.set(t, 10);

        // FFD8 + APP1 + 나머지
        var out = new Uint8Array(src.length + app1.length);
        out[0]=src[0]; out[1]=src[1];     // FFD8
        out.set(app1, 2);
        out.set(src.subarray(2), 2 + app1.length);

        callback(new Blob([out], {type:'image/jpeg'}));
    };
    reader.onerror = function() { callback(jpegBlob); };
    reader.readAsArrayBuffer(jpegBlob);
}

// EXIF Software 태그 주입 후 다운로드/클립보드
function _captureFinish(canvas, mode) {
    if (mode === 'clipboard') {
        canvas.toBlob(function(pngBlob) {
            if (!navigator.clipboard || !window.ClipboardItem) {
                // 폴백: PNG 파일 다운로드
                var ts = new Date().toISOString().slice(0,19).replace(/[-T:]/g,'_');
                _blobDownload(pngBlob, ts + '_dodoRynx.png');
                return;
            }
            var item = new ClipboardItem({'image/png': pngBlob});
            navigator.clipboard.write([item])
                .then(function() { _captureToast(window._I18N.toolbar.cap_clipboard_done); })
                .catch(function(e) {
                    console.warn('[GpsMap] clipboard 실패:', e);
                    // 폴백: 파일 다운로드
                    var ts = new Date().toISOString().slice(0,19).replace(/[-T:]/g,'_');
                    _blobDownload(pngBlob, ts + '_dodoRynx.png');
                });
        }, 'image/png');
        return;
    }
    // 기존 저장(download) 경로
    canvas.toBlob(function(blob) {
        _injectExifSoftware(blob, 'dodoRynx', function(finalBlob) {
            var ts = new Date().toISOString().slice(0,19).replace(/[-T:]/g,'_');
            _blobDownload(finalBlob, ts + '_dodoRynx.jpg');
            _captureToast(ts + '_dodoRynx.jpg');
        });
    }, 'image/jpeg', 0.93);
}

function _blobDownload(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click();
    setTimeout(function() { document.body.removeChild(a); URL.revokeObjectURL(url); }, 200);
}

function _captureDownload(dataUrl, filename) {
    var a = document.createElement('a');
    a.download = filename; a.href = dataUrl;
    document.body.appendChild(a); a.click();
    setTimeout(function() { document.body.removeChild(a); }, 100);
}

function _captureToast(msg) {
    var t = document.createElement('div');
    t.textContent = msg;
    t.style.cssText =
        'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);' +
        'background:rgba(30,38,54,0.94);color:#c8e0f4;padding:9px 18px;' +
        'border-radius:8px;font-size:12px;z-index:9999;pointer-events:none;' +
        'border:1px solid rgba(100,160,255,0.25);transition:opacity 0.4s;';
    document.body.appendChild(t);
    setTimeout(function() {
        t.style.opacity = '0';
        setTimeout(function() { document.body.removeChild(t); }, 400);
    }, 2000);
}

// ── Qt 모드 전용: 지도 이동 중 overlay 숨김 → 깜박임 시각 충격 감소 ──
if (typeof IS_QT_MODE !== 'undefined' && IS_QT_MODE) {
    var _qtMoveTimer = null;
    var _overlayEls = function() {
        return [
            document.getElementById('elevationDock'),
            document.getElementById('elevationSensorBar')
        ].filter(Boolean);
    };

    function _qtHideOverlays() {
        _overlayEls().forEach(function(el) {
            if (!el.classList.contains('hidden')) el.style.opacity = '0.01';
        });
    }
    function _qtShowOverlays() {
        _overlayEls().forEach(function(el) { el.style.opacity = ''; });
    }

    ['movestart','zoomstart','rotatestart','pitchstart'].forEach(function(ev) {
        map.on(ev, function() {
            if (_qtMoveTimer) clearTimeout(_qtMoveTimer);
            _qtHideOverlays();
        });
    });
    ['moveend','zoomend','rotateend','pitchend'].forEach(function(ev) {
        map.on(ev, function() {
            if (_qtMoveTimer) clearTimeout(_qtMoveTimer);
            _qtMoveTimer = setTimeout(_qtShowOverlays, 80);
        });
    });
}
