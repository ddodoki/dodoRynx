// tools\gps_map\gps_map_browser_toolbar-1.tpl

(function () {
    var TOOLBAR_MODE = '${toolbar_mode}';
    if (!TOOLBAR_MODE) return;

    var _T  = window._I18N.toolbar;
    var _TQ = window._I18N.toolbar_qt || {};

    function _lbl(qtKey, fallback) {
        return (_TQ && _TQ[qtKey]) ? _TQ[qtKey] : fallback;
    }

    var _routeOn        = ${route_visible};
    var _gpxOn          = ${gpx_visible};
    var _elevOn         = ${elevation_visible};
    var _hasElev        = ${gpx_has_elevation};
    var _hasGpx         = (${gpx_json} !== null);
    var _pinThumb       = ${pin_thumbs_on};
    var _singlesOn      = ${pin_singles_on};
    var _clustersOn     = ${pin_clusters_on};
    var _thumbbarOn     = true;
    var _speedHeatmap   = false;
    var _arrows         = false;
    var _stopMarkers    = false;
    var _playbackActive = false;

    // ── CSS ───────────────────────────────────────────────────
    var css = document.createElement('style');
    css.textContent =
        '#btb{position:fixed;top:0;left:0;right:0;height:44px;' +
        'background:rgba(22,22,22,0.97);' +
        'border-bottom:1px solid #383838;' +
        'display:flex;align-items:center;padding:0 8px;gap:4px;' +
        'z-index:2000;box-sizing:border-box;' +
        'font-family:-apple-system,"Segoe UI",sans-serif;' +
        'overflow-x:auto;overflow-y:hidden;scrollbar-width:none;}' +
        '#btb::-webkit-scrollbar{display:none;}' +
        '#btb>*{flex-shrink:0;}' +
        '#btb button{background:#2e2e2e;color:#bbb;border:1px solid #444;' +
        'border-radius:4px;padding:3px 9px;font-size:11px;cursor:pointer;' +
        'white-space:nowrap;height:28px;transition:none;}' +
        '#btb button:hover{background:#3a3a3a;color:#fff;}' +
        '#btb button.on{background:#1a5a9a;border-color:#2a6ab0;color:#fff;}' +
        '#btb button:disabled{opacity:0.35;cursor:default;}' +
        '#btb .sep{width:1px;height:24px;background:#444;flex-shrink:0;margin:0 2px;}' +
        '#btb select{background:#2e2e2e;color:#bbb;border:1px solid #444;' +
        'border-radius:4px;padding:2px 4px;font-size:11px;height:28px;}' +
        '#btb-gpx-file{display:none;}' +
        '#map{top:44px !important;height:calc(100% - 44px) !important;}' +
        '#elevationPanel{bottom:14px !important;}';
    document.head.appendChild(css);

    // ── 헬퍼 ──────────────────────────────────────────────────
    function _btn(id, label, on, disabled, tip) {
        var b = document.createElement('button');
        b.id = id; b.textContent = label;
        if (on)      b.classList.add('on');
        if (disabled) b.disabled = true;
        if (tip)      b.title = tip;
        return b;
    }
    function _sep() {
        var d = document.createElement('div'); d.className = 'sep'; return d;
    }

    // ── 툴바 빌드 ─────────────────────────────────────────────
    var bar = document.createElement('div');
    bar.id = 'btb';

    // ── 그룹 1: Qt 전용 — 브라우저 ─────────────────────────────
    var bBrowser, bThumb, bCur;
    if (TOOLBAR_MODE === 'qt') {
        bBrowser = _btn('btb-browser', _T.btn_browser, false, false, _TQ.browser_tip);
        bar.appendChild(bBrowser);
        bar.appendChild(_sep());
    }

    // ── 그룹 2: 내비게이션 — Thumb(Qt) | World | Fit | Cur(Qt) ──
    if (TOOLBAR_MODE === 'qt') {
        bThumb = _btn('btb-thumb',
            _thumbbarOn ? _TQ.thumbbar_on : _TQ.thumbbar_off,
            _thumbbarOn);
        bar.appendChild(bThumb);
    }
    var bWorld = _btn('btb-world', _T.btn_world, false, false, _TQ.world_tip);
    var bFit   = _btn('btb-fit',   _T.btn_fit,   false, false, _TQ.fit_tip);
    bar.appendChild(bWorld);
    bar.appendChild(bFit);
    if (TOOLBAR_MODE === 'qt') {
        bCur = _btn('btb-cur', _T.btn_cur, false, false, _TQ.cur_tip);
        bar.appendChild(bCur);
    }
    bar.appendChild(_sep());

    // ── 그룹 3: 레이어 — Route | GPX | Elev ───────────────────
    var bRoute = _btn('btb-route',
        _lbl(_routeOn ? 'route_on' : 'route_off', _T.btn_route),
        _routeOn, false, _TQ.route_tip);
    var bGpx   = _btn('btb-gpx',
        _lbl(_gpxOn ? 'gpx_on' : 'gpx_off', _T.btn_gpx),
        _gpxOn, !_hasGpx, _TQ.gpx_tip);
    var bElev  = _btn('btb-elev',
        _lbl(_elevOn ? 'elev_on' : 'elev_off', _T.btn_elev),
        _elevOn, !_hasElev, _TQ.elev_tip);
    bar.appendChild(bRoute);
    bar.appendChild(bGpx);
    bar.appendChild(bElev);
    bar.appendChild(_sep());

    // ── 그룹 4: 핀 — Pin | Singles | Clusters ─────────────────
    var bPin      = _btn('btb-pin',
        _lbl(_pinThumb ? 'pin_on' : 'pin_off', _T.btn_pin),
        _pinThumb, false);
    var bSingles  = _btn('btb-singles',
        _lbl('singles_label', _T.btn_singles),
        _singlesOn, false, _TQ.singles_tip);
    var bClusters = _btn('btb-clusters',
        _lbl('clusters_label', _T.btn_clusters),
        _clustersOn, false, _TQ.clusters_tip);
    bar.appendChild(bPin);
    bar.appendChild(bSingles);
    bar.appendChild(bClusters);
    bar.appendChild(_sep());

    // ── 그룹 5: GPX 분석 — Speed | Arrows | Stops | Play | Load ─
    var bSpeedHeat = _btn('btb-speed',
        _lbl('speed_btn', _T.btn_speed_heat),
        _speedHeatmap, !_hasGpx);
    var bArrows    = _btn('btb-arrows',
        _lbl('arrows_btn', _T.btn_arrows),
        _arrows, !_hasGpx);
    var bStops     = _btn('btb-stops',
        _lbl('stops_btn', _T.btn_stops),
        _stopMarkers, !_hasGpx);
    var bPlayback  = _btn('btb-play',
        _lbl('play_off', _T.btn_playback_start),
        _playbackActive);
    var bGpxLoad   = _btn('btb-gpx-load',
        _lbl('gpx_load_btn', _T.btn_gpx_load),
        false, false, _TQ.gpx_load_tip);

    var bGpxEditor = null;
    if (TOOLBAR_MODE === 'qt') {
        bGpxEditor = _btn(
            'btb-gpx-editor',
            _lbl('gpx_editor_btn', '✂ GPX'),
            false, false,
            _TQ.gpx_editor_tip || 'GPX 파일 합치기 / 쪼개기'
        );
    }

    bar.appendChild(bSpeedHeat);
    bar.appendChild(bArrows);
    bar.appendChild(bStops);
    bar.appendChild(bPlayback);
    bar.appendChild(_sep());
    bar.appendChild(bGpxLoad);

    if (bGpxEditor) {
        bar.appendChild(_sep());
        bar.appendChild(bGpxEditor);
    }

    bar.appendChild(_sep());

    // ── 그룹 6: 캡처 ──────────────────────────────────────────
    var bCapture = _btn('btb-capture', _T.btn_capture, false, false, _TQ.capture_tip);
    bar.appendChild(bCapture);

    var _capMenu = document.createElement('div');
    _capMenu.id = 'btb-cap-menu';
    _capMenu.style.cssText =
        'display:none;position:fixed;background:#1a1e28;border:1px solid #3a4050;' +
        'border-radius:7px;padding:4px 0;z-index:4000;' +
        'box-shadow:0 6px 20px rgba(0,0,0,.65);min-width:168px;';
    var _capItems = TOOLBAR_MODE === 'qt'
        ? [[_T.cap_save_current,'currentfolder'],[_T.cap_save_as,'saveas'],[_T.cap_clipboard_jpeg,'clipboard']]
        : [[_T.cap_download,'download'],[_T.cap_clipboard,'clipboard']];
    _capItems.forEach(function(pair) {
        var item = document.createElement('div');
        item.textContent = pair[0];
        item.dataset.capMode = pair[1];
        item.style.cssText =
            'padding:9px 16px;cursor:pointer;color:#c8d8e8;font-size:11px;white-space:nowrap;';
        item.addEventListener('mouseenter', function() { this.style.background = 'rgba(74,158,255,0.12)'; });
        item.addEventListener('mouseleave', function() { this.style.background = ''; });
        _capMenu.appendChild(item);
    });
    document.body.appendChild(_capMenu);

    // ── 그룹 7: UTC ───────────────────────────────────────────
    bar.appendChild(_sep());

    var _utcValue = 'AUTO';
    var utcWrap = document.createElement('div');
    utcWrap.style.cssText = 'position:relative;flex-shrink:0;display:flex;align-items:center;gap:4px;';
    var utcIcon = document.createElement('span');
    utcIcon.style.cssText = 'color:#888;font-size:12px;flex-shrink:0;';
    utcIcon.textContent = _T.utc_icon;
    var utcBtn = document.createElement('button');
    utcBtn.id = 'btb-utc-btn';
    utcBtn.textContent = TOOLBAR_MODE === 'qt' ? _TQ.utc_auto : _T.utc_auto;
    utcBtn.style.cssText = 'min-width:72px;text-align:left;padding:3px 8px;';
    if (_TQ.utc_tip) utcBtn.title = _TQ.utc_tip;

    var utcList = document.createElement('div');
    utcList.id = 'btb-utc-list';
    utcList.style.cssText =
        'display:none;position:fixed;background:#222;border:1px solid #444;border-radius:4px;' +
        'max-height:220px;overflow-y:auto;z-index:9999;min-width:110px;' +
        'box-shadow:0 4px 12px rgba(0,0,0,.7);';

    var _utcAutoLabel = TOOLBAR_MODE === 'qt' ? _TQ.utc_auto : _T.utc_auto;
    var _utcItems = [{ v: 'AUTO', t: _utcAutoLabel }];
    for (var _hq = -48; _hq <= 56; _hq++) {
        var _hVal = _hq / 4;
        var _sign = _hVal >= 0 ? '+' : '-';
        var _absH = Math.abs(_hVal);
        var _wh   = Math.floor(_absH);
        var _mins = Math.round((_absH - _wh) * 60);
        var _skipQuarter = (_mins === 15 || _mins === 45);
        if (_skipQuarter) {
            var _knownQuarters = [5.75, 9.75, -9.5];
            var _isKnown = false;
            for (var _ki = 0; _ki < _knownQuarters.length; _ki++) {
                if (Math.abs(_hVal - _knownQuarters[_ki]) < 0.001) { _isKnown = true; break; }
            }
            if (!_isKnown) continue;
        }
        _utcItems.push({ v: String(_hVal),
            t: 'UTC' + _sign + String(_wh).padStart(2,'0') + ':' + String(_mins).padStart(2,'0') });
    }
    _utcItems.forEach(function(opt) {
        var item = document.createElement('div');
        item.textContent = opt.t;
        item.style.cssText = 'padding:5px 12px;cursor:pointer;font-size:11px;color:#bbb;white-space:nowrap;';
        item.addEventListener('mouseover', function() { item.style.background = '#3a3a3a'; });
        item.addEventListener('mouseout',  function() { item.style.background = ''; });
        item.addEventListener('click', function(e) {
            e.stopPropagation();
            _utcValue = opt.v;
            utcBtn.textContent = opt.v === 'AUTO' ? _utcAutoLabel : opt.t;
            utcList.style.display = 'none';
            if (window.setTimeOffsetMode) {
                if (opt.v === 'AUTO') window.setTimeOffsetMode('AUTO', 0);
                else window.setTimeOffsetMode('MANUAL', parseFloat(opt.v));
            }
        });
        utcList.appendChild(item);
    });
    utcBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        var isOpen = utcList.style.display !== 'none';
        if (isOpen) { utcList.style.display = 'none'; return; }
        var r = utcBtn.getBoundingClientRect();
        utcList.style.left = r.left + 'px';
        utcList.style.top  = (r.bottom + 2) + 'px';
        utcList.style.display = 'block';
    });
    document.addEventListener('click', function() { utcList.style.display = 'none'; });
    document.body.appendChild(utcList);
    utcWrap.appendChild(utcIcon);
    utcWrap.appendChild(utcBtn);
    bar.appendChild(utcWrap);
    document.body.appendChild(bar);

    // ── GPX 로드 후 버튼 상태 복원 ────────────────────────────
    window._onGpxLoaded = function(data) {
        _hasGpx  = true;
        _hasElev = !!(data && data.has_elevation);
        bGpx.disabled       = false;
        bSpeedHeat.disabled = false;
        bArrows.disabled    = false;
        bStops.disabled     = false;
        bElev.disabled = !_hasElev;
        if (_hasElev) {
            _elevOn = true;
            bElev.classList.add('on');
            bElev.textContent = _lbl('elev_on', _T.btn_elev);
        }
        _gpxOn = true;
        bGpx.classList.add('on');
        bGpx.textContent = _lbl('gpx_on', _T.btn_gpx);
    };

    window._closeCaptureMenu = function() {
        var m = document.getElementById('btb-cap-menu');
        if (m) m.style.display = 'none';
        var ul = document.getElementById('btb-utc-list');
        if (ul) ul.style.display = 'none';
    };

    if (TOOLBAR_MODE === 'browser') {
        var titleEl = document.querySelector('title');
        if (!titleEl) { titleEl = document.createElement('title'); document.head.appendChild(titleEl); }
        document.title = 'dodoRynx';
        new MutationObserver(function() {
            var t = document.title;
            if (!t || t === 'dodoRynx') return;
            if (!/^[A-Z]/.test(t)) return;
            if (t === 'ACTION:PLAYBACK_STOP') {
                _playbackActive = false;
                bPlayback.classList.remove('on');
                bPlayback.textContent = _lbl('play_off', _T.btn_playback_start);
            }
            requestAnimationFrame(function() { document.title = 'dodoRynx'; });
        }).observe(titleEl, { childList:true, subtree:true, characterData:true });
    }

    // ── 이벤트 바인딩 ─────────────────────────────────────────
    function _bind() {
        if (TOOLBAR_MODE === 'qt') {
            bBrowser.addEventListener('click', function() {
                document.title = 'ACTION:OPEN_BROWSER';
            });
            bThumb.addEventListener('click', function() {
                _thumbbarOn = !_thumbbarOn;
                bThumb.textContent = _thumbbarOn ? _TQ.thumbbar_on : _TQ.thumbbar_off;
                bThumb.classList.toggle('on', _thumbbarOn);
                document.title = 'ACTION:TOGGLE_THUMBBAR:' + (_thumbbarOn ? '1' : '0');
            });
            bCur.addEventListener('click', function() {
                document.title = 'ACTION:GOTO_CURRENT';
            });
        }

        if (bGpxEditor) {
            bGpxEditor.addEventListener('click', function() {
                document.title = 'ACTION:OPEN_GPX_MERGER';
            });
        }
        
        bWorld.addEventListener('click', function() {
            map.flyTo({ zoom:2, center:[10,25], duration:600, essential:true });
        });
        bFit.addEventListener('click', function() {
            if (window.fitAll) window.fitAll();
        });

        bRoute.addEventListener('click', function() {
            _routeOn = !_routeOn;
            bRoute.classList.toggle('on', _routeOn);
            bRoute.textContent = _lbl(_routeOn ? 'route_on' : 'route_off', _T.btn_route);
            if (window.setRouteVisible) window.setRouteVisible(_routeOn);
        });
        bGpx.addEventListener('click', function() {
            _gpxOn = !_gpxOn;
            bGpx.classList.toggle('on', _gpxOn);
            bGpx.textContent = _lbl(_gpxOn ? 'gpx_on' : 'gpx_off', _T.btn_gpx);
            if (window.setGpxVisible) window.setGpxVisible(_gpxOn);
        });
        bElev.addEventListener('click', function() {
            _elevOn = !_elevOn;
            bElev.classList.toggle('on', _elevOn);
            bElev.textContent = _lbl(_elevOn ? 'elev_on' : 'elev_off', _T.btn_elev);
            if (window.setElevationVisible) window.setElevationVisible(_elevOn);
        });

        bPin.addEventListener('click', function() {
            _pinThumb = !_pinThumb;
            bPin.classList.toggle('on', _pinThumb);
            bPin.textContent = _lbl(_pinThumb ? 'pin_on' : 'pin_off', _T.btn_pin);
            if (window.setPinThumbsEnabled) window.setPinThumbsEnabled(_pinThumb);
        });
        bSingles.addEventListener('click', function() {
            _singlesOn = !_singlesOn;
            bSingles.classList.toggle('on', _singlesOn);
            if (window.setPinSinglesEnabled) window.setPinSinglesEnabled(_singlesOn);
        });
        bClusters.addEventListener('click', function() {
            _clustersOn = !_clustersOn;
            bClusters.classList.toggle('on', _clustersOn);
            if (window.setPinClustersEnabled) window.setPinClustersEnabled(_clustersOn);
        });

        bSpeedHeat.addEventListener('click', function() {
            _speedHeatmap = !_speedHeatmap;
            bSpeedHeat.classList.toggle('on', _speedHeatmap);
            if (window.setSpeedHeatmapVisible) window.setSpeedHeatmapVisible(_speedHeatmap);
        });
        bArrows.addEventListener('click', function() {
            _arrows = !_arrows;
            bArrows.classList.toggle('on', _arrows);
            if (window.setArrowsVisible) window.setArrowsVisible(_arrows);
        });
        bStops.addEventListener('click', function() {
            _stopMarkers = !_stopMarkers;
            bStops.classList.toggle('on', _stopMarkers);
            if (window.setStopMarkersVisible) window.setStopMarkersVisible(_stopMarkers);
        });
        bPlayback.addEventListener('click', function() {
            _playbackActive = !_playbackActive;
            bPlayback.classList.toggle('on', _playbackActive);
            bPlayback.textContent = _lbl(_playbackActive ? 'play_on' : 'play_off', _playbackActive ? _T.btn_playback_stop : _T.btn_playback_start);
            if (_playbackActive) { if (window.startPhotoPlayback) window.startPhotoPlayback(2500); }
            else                 { if (window.stopPhotoPlayback)  window.stopPhotoPlayback(); }
        });

        bCapture.addEventListener('click', function(e) {
            e.stopPropagation();
            var r = bCapture.getBoundingClientRect();
            var open = _capMenu.style.display !== 'none';
            _capMenu.style.display = 'none';
            if (!open) {
                _capMenu.style.left = r.left + 'px';
                _capMenu.style.top  = (r.bottom + 4) + 'px';
                _capMenu.style.display = 'block';
            }
        });
        _capMenu.querySelectorAll('[data-cap-mode]').forEach(function(item) {
            item.addEventListener('click', function() {
                var mode = this.dataset.capMode;
                _capMenu.style.display = 'none';
                if (TOOLBAR_MODE === 'qt') {
                    document.title = 'ACTION:CAPTURE:' + mode;
                } else {
                    if (window._captureMap) window._captureMap(mode === 'current_folder' ? 'download' : mode);
                }
            });
        });
        document.addEventListener('click', function() { _capMenu.style.display = 'none'; });

        if (TOOLBAR_MODE === 'browser') {
            bGpxLoad.addEventListener('click', function() {
                bGpxLoad.disabled = true;
                var orig = bGpxLoad.textContent;
                bGpxLoad.textContent = _T.btn_gpx_loading;
                fetch('/api/open-gpx')
                    .then(function(r) {
                        if (r.status === 204) return null;
                        if (!r.ok) throw new Error('HTTP ' + r.status);
                        return r.json();
                    })
                    .then(function(data) {
                        if (data && window._reloadGpx) window._reloadGpx(data);
                    })
                    .catch(function(e) { console.warn('[GpsMap] GPX 열기 실패:', e); })
                    .finally(function() {
                        bGpxLoad.disabled = false;
                        bGpxLoad.textContent = orig;
                    });
            });
        } else {
            bGpxLoad.addEventListener('click', function() {
                document.title = 'ACTION:GPX_LOAD';
            });
        }
    }

    var _tid = setInterval(function() {
        if (typeof map !== 'undefined') {
            clearInterval(_tid);
            if (map.loaded()) _bind(); else map.once('load', _bind);
        }
    }, 50);

})();
