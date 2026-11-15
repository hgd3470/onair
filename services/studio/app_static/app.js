/* 온에어 듣기 웹앱.
 * 팀 API(/api/v1)만 부르는 화면이다. 무엇을 틀지는 서버가 정하고, 여기서는 받은 순서대로 틀고 청취 이벤트를 보낸다.
 * 이벤트 보내는 시점은 차량 앱(PlaybackEventTracker)과 같게 맞췄다.
 */
(function () {
  'use strict';

  var API = '/api/v1';
  var LOGIN_URL = '/admin/login?next=/app/';
  var APP_VERSION = 'web-0.2.0';
  var POSITION_EVERY_SEC = 10;
  var RESUME_WITHIN_MS = 3 * 60 * 60 * 1000;
  var REWIND_SEC = 10;
  var DEFAULT_MINUTES = 10;
  // 새 이야기 표시는 임시 기준이다. /contents에 등록 날짜가 실리면 날짜로 바꾼다
  var NEW_PREFIXES = ['s2-'];

  var CATEGORY = {
    C01: '일·직장', C02: '연애·결혼', C03: '가족·육아', C04: '친구·인간관계', C05: '마음·습관·성장',
    C06: '자취·살림·생활팁', C07: '음식·맛집·식문화', C08: '돈·소비·생활경제', C09: '건강·운동·생활리듬',
    C10: '반려동물·자연', C11: '여행·지역·문화', C12: '패션·뷰티·자기표현', C13: '과학·기술·AI',
    C14: '역사·인물', C15: '언어·독서·인문·가치관', C16: '뉴스·사회·시사', C17: '사건·미스터리·추리',
    C18: '음악·영화·방송·예술', C19: '스포츠·게임·취미', C20: '자동차·교통·이동', C21: '성경·말씀·신앙'
  };

  // 관심사 묶음은 차량 앱의 주제 다섯 개와 같다
  var GROUPS = [
    { id: 'A', name: '사람과 일상', keys: '직장, 연애, 가족, 인간관계', codes: ['C01', 'C02', 'C03', 'C04', 'C05'] },
    { id: 'B', name: '생활과 취향', keys: '살림, 음식, 소비, 여행, 건강', codes: ['C06', 'C07', 'C08', 'C09', 'C10', 'C11', 'C12'] },
    { id: 'C', name: '지식과 세상', keys: '과학·AI, 역사, 인문, 사회', codes: ['C13', 'C14', 'C15', 'C16', 'C17'] },
    { id: 'D', name: '문화·취미·이동', keys: '영화, 음악, 스포츠, 교통', codes: ['C18', 'C19', 'C20'] },
    { id: 'E', name: '성경과 신앙', keys: '성경 낭독, 묵상', codes: ['C21'] }
  ];

  function $(id) { return document.getElementById(id); }

  var audio = $('audio');
  var el = {
    home: $('view-home'), player: $('view-player'), homeNote: $('home-note'), homeHeading: $('home-heading'),
    resume: $('resume'), resumeTitle: $('resume-title'), homeInterests: $('home-interests'),
    homeInterestsValue: $('home-interests-value'),
    count: $('p-count'), title: $('p-title'), meta: $('p-meta'), note: $('p-note'),
    bar: $('p-bar'), fill: $('p-fill'), cur: $('p-cur'), dur: $('p-dur'),
    prev: $('btn-prev'), play: $('btn-play'), next: $('btn-next'), like: $('btn-like'), nextLine: $('p-next'),
    back10: $('btn-back10'), stop: $('p-stop'),
    source: $('p-source'), srcAttr: $('src-attr'), srcLicense: $('src-license'), srcLink: $('src-link'),
    topics: $('topics'), rebuild: $('rebuild'), rebuildGo: $('rebuild-go'),
    list: $('list'), listEmpty: $('list-empty'),
    mini: $('mini'), miniTitle: $('mini-title'), miniMeta: $('mini-meta'), miniPlay: $('mini-play'),
    sheet: $('sheet'), sheetOk: $('sheet-ok'), sheetCancel: $('sheet-cancel'), sheetScrim: $('sheet-scrim'),
    toast: $('toast'), views: $('views'), tabbar: document.querySelector('.tabbar')
  };
  var LIST_MODES = ['all', 'new', 'unheard', 'history'];

  var S = {
    tab: 'listen',
    queue: [], index: -1, minutes: null,
    cur: null,            // 지금 트는 항목 하나의 재생 상태
    catalog: {}, catalogList: [],
    interests: [],
    liked: {}, history: [], heard: {}, historySig: '',
    listMode: 'all',
    stale: false,         // 듣는 중에 관심사를 바꿔서 지금 목록이 예전 관심사로 만든 것일 때
    pendingQueue: null,   // 광고가 끝나면 바꿔 틀 새 목록
    loadingQueue: false,
    unlocked: false,
    errorStreak: 0,
    lockUntil: 0,
    redirecting: false
  };

  /* ---------- 저장소 (막혀 있어도 화면은 돌아가야 한다) ---------- */

  var store = {
    get: function (key) {
      try { var v = localStorage.getItem('teum.' + key); return v ? JSON.parse(v) : null; } catch (e) { return null; }
    },
    set: function (key, value) {
      try { localStorage.setItem('teum.' + key, JSON.stringify(value)); } catch (e) { /* 무시 */ }
    },
    del: function (key) {
      try { localStorage.removeItem('teum.' + key); } catch (e) { /* 무시 */ }
    }
  };

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    var b = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    var h = Array.prototype.map.call(b, function (x) { return (x + 256).toString(16).slice(1); }).join('');
    return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
  }

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function round1(x) { return Math.round(x * 10) / 10; }

  /* ---------- API ---------- */

  function ApiError(status, detail) { this.status = status; this.detail = detail; }

  function goLogin() {
    if (S.redirecting) return;
    S.redirecting = true;
    location.assign(LOGIN_URL);
  }

  var device = store.get('device');
  var devicePromise = null;

  function ensureDevice() {
    if (device && device.token && device.user_id) return Promise.resolve(device);
    if (!devicePromise) {
      devicePromise = registerDevice().then(
        function (d) { devicePromise = null; return d; },
        function (e) { devicePromise = null; throw e; });
    }
    return devicePromise;
  }

  // 기기 등록은 부를 때마다 새 사용자가 생기므로 토큰을 저장해 두고 계속 쓴다
  function registerDevice() {
    var installId = (device && device.install_id) || uuid();
    return api('/devices', { method: 'POST', body: { install_id: installId, app_version: APP_VERSION, platform: 'web' } })
      .then(function (r) {
        device = { install_id: installId, user_id: r.user_id, token: r.token };
        store.set('device', device);
        return device;
      });
  }

  function api(path, opts) {
    opts = opts || {};
    var headers = {};
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    var ready = opts.auth ? ensureDevice().then(function (d) { headers.Authorization = 'Bearer ' + d.token; }) : Promise.resolve();
    return ready.then(function () {
      return fetch(API + path, {
        method: opts.method || 'GET',
        headers: headers,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        credentials: 'same-origin',
        cache: 'no-store',
        keepalive: !!opts.keepalive
      });
    }).then(function (res) {
      if (res.ok) return res.status === 204 ? null : res.json();
      return res.json().catch(function () { return {}; }).then(function (data) {
        var detail = data && data.detail;
        if (res.status === 401) {
          // 기기 토큰이 서버에서 사라진 경우에만 한 번 새로 등록하고, 그 밖의 401은 로그인이 풀린 것이다
          if (opts.auth && detail === 'invalid token' && !opts.retried) {
            device = { install_id: device && device.install_id };
            return api(path, Object.assign({}, opts, { retried: true }));
          }
          goLogin();
        }
        throw new ApiError(res.status, detail);
      });
    });
  }

  /* ---------- 청취 이벤트: 만든 순서대로 하나씩 보낸다 ---------- */

  var evq = [];
  var evBusy = false;
  var evWaiters = [];

  function nextSeq() {
    var n = (store.get('seq') || 0) + 1;
    store.set('seq', n);
    return n;
  }

  function emit(type, fields) {
    var ev = { event_id: uuid(), seq: nextSeq(), type: type, client_ts: new Date().toISOString() };
    for (var k in fields) if (fields[k] !== undefined && fields[k] !== null) ev[k] = fields[k];
    evq.push(ev);
    pump();
  }

  function pump() {
    if (evBusy) return;
    evBusy = true;
    (function step() {
      if (!evq.length) {
        evBusy = false;
        evWaiters.splice(0).forEach(function (fn) { fn(); });
        return;
      }
      sendOne(evq[0], 0).then(function () { evq.shift(); step(); });
    })();
  }

  function sendOne(ev, attempt) {
    return api('/events', { method: 'POST', body: ev, auth: true, keepalive: true }).catch(function (e) {
      var s = e && e.status;
      if (s === 401 || s === 404 || s === 422 || attempt >= 2) return null;   // 다시 보내도 안 되는 것은 버린다
      return sleep(1000 * (attempt + 1)).then(function () { return sendOne(ev, attempt + 1); });
    });
  }

  // 다음 추천을 받기 전에 방금 일을 서버가 알도록 잠깐 기다린다
  function flushEvents(timeoutMs) {
    if (!evBusy && !evq.length) return Promise.resolve();
    return Promise.race([new Promise(function (r) { evWaiters.push(r); }), sleep(timeoutMs)]);
  }

  // 화면이 닫힐 때 남은 이벤트를 한 번에 보낸다 (같은 event_id는 서버가 한 번만 받는다)
  function flushOnHide() {
    if (!evq.length || !device || !device.token) return;
    try {
      fetch(API + '/events/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + device.token },
        body: JSON.stringify({ events: evq.slice(0, 500) }),
        credentials: 'same-origin',
        keepalive: true
      }).catch(function () { /* 무시 */ });
    } catch (e) { /* 무시 */ }
  }

  /* ---------- 재생 ---------- */

  function isAd(item) { return item && item.kind === 'ad'; }
  function durationOf(item) {
    return isFinite(audio.duration) && audio.duration > 0 ? audio.duration : (item.duration_sec || 0);
  }

  function storyFields(c, position) {
    return {
      content_id: c.item.content_id, content_version: 1, playback_id: c.playbackId,
      position_sec: round1(position), playback_rate: c.rate
    };
  }
  function adFields(c, position) {
    return { content_id: c.item.content_id, playback_id: c.playbackId, position_sec: round1(position) };
  }

  // 위치는 목록에 적힌 길이를 넘지 않게 보낸다 (넘으면 서버가 그 재생을 건너뛰기 판단에서 뺀다)
  function positionNow(c) {
    var p = audio.currentTime || 0;
    var max = c.item.duration_sec;
    return max ? Math.min(p, max) : p;
  }

  function emitPosition(c) {
    if (isAd(c.item) || !c.started) return;
    c.lastReportAt = audio.currentTime || 0;
    emit('position', storyFields(c, positionNow(c)));
  }

  // iOS는 손가락이 닿은 순간에 한 번 재생을 걸어야 그 뒤로 자동으로 이어 틀 수 있다
  var silentUrl = null;
  function unlockAudio() {
    if (S.unlocked || S.cur) return;
    try {
      if (!silentUrl) {
        var samples = 2205, buf = new ArrayBuffer(44 + samples * 2), v = new DataView(buf);
        var w = function (o, s) { for (var i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
        w(0, 'RIFF'); v.setUint32(4, 36 + samples * 2, true); w(8, 'WAVEfmt '); v.setUint32(16, 16, true);
        v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 22050, true); v.setUint32(28, 44100, true);
        v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, samples * 2, true);
        silentUrl = URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
      }
      audio.src = silentUrl;
      var p = audio.play();
      if (p && p.catch) p.catch(function () { /* 무시 */ });
    } catch (e) { /* 무시 */ }
  }

  function playIndex(i) {
    var item = S.queue[i];
    if (!item) { finishQueue(); return; }
    S.index = i;
    var rate = item.playback_rate > 0 ? item.playback_rate : 1;
    S.cur = {
      item: item, playbackId: uuid(), rate: rate,
      started: false, paused: false, stalled: false, done: false, failed: false, lastReportAt: 0
    };
    saveQueue();
    renderTrack();
    setState('loading');
    audio.src = item.audio_uri;
    audio.defaultPlaybackRate = rate;
    audio.playbackRate = rate;
    startAudio();
  }

  function startAudio() {
    var c = S.cur;
    var p = audio.play();
    if (p && p.catch) {
      p.catch(function (err) {
        // 자동 재생이 막히면 멈춘 채로 두고, 사용자가 재생을 누르면 이어진다
        if (S.cur === c && err && err.name === 'NotAllowedError') setState('paused');
      });
    }
  }

  function advance() {
    if (S.pendingQueue) {
      var p = S.pendingQueue;
      S.pendingQueue = null;
      S.queue = p.items;
      S.minutes = p.minutes;
      playIndex(0);
      return;
    }
    var n = S.index + 1;
    if (n >= S.queue.length) finishQueue(); else playIndex(n);
  }

  function prevStoryIndex() {
    for (var i = S.index - 1; i >= 0; i--) if (!isAd(S.queue[i])) return i;
    return -1;
  }

  function locked() {
    var now = Date.now();
    if (now < S.lockUntil) return true;
    S.lockUntil = now + 700;
    return false;
  }

  // 다음: 광고는 넘길 수 없다(차량 앱과 같음). 이야기는 위치를 남기고 skip(user_next)를 보낸다
  function userNext() {
    var c = S.cur;
    if (!c) return;
    if (isAd(c.item) && !c.failed) { toast('광고가 끝나면 넘어갑니다'); return; }
    if (locked()) return;
    if (!isAd(c.item) && c.started && !c.done) {
      emitPosition(c);
      emit('skip', Object.assign(storyFields(c, positionNow(c)), { skip_reason: 'user_next' }));
    }
    c.done = true;
    advance();
  }

  function userPrev() {
    var c = S.cur;
    if (!c || isAd(c.item)) return;
    var i = prevStoryIndex();
    if (i < 0 || locked()) return;
    if (c.started && !c.done) {
      emitPosition(c);
      emit('skip', Object.assign(storyFields(c, positionNow(c)), { skip_reason: 'user_prev' }));
    }
    c.done = true;
    playIndex(i);
  }

  // 10초 뒤로. 서버에는 되감기 이벤트가 따로 없어서 되감기 직전 자리와 되감은 자리를 position으로 남긴다.
  // 서버는 앞 구간까지만 들은 것으로 치고 되감은 자리부터 다시 재며, 겹친 구간은 한 번만 센다. skip은 보내지 않는다
  function rewind10() {
    var c = S.cur;
    if (!c || isAd(c.item) || !c.started || c.done) return;
    var from = audio.currentTime || 0;
    var to = Math.max(0, from - REWIND_SEC);
    if (from - to < 0.1) return;
    if (!c.paused && !c.stalled) emitPosition(c);
    try { audio.currentTime = to; } catch (e) { return; }
    emitPosition(c);
    renderProgress();
  }

  function togglePlay() {
    var c = S.cur;
    if (!c) return;
    if (audio.paused) startAudio(); else audio.pause();
  }

  function stopAudio() {
    try { audio.pause(); audio.removeAttribute('src'); audio.load(); } catch (e) { /* 무시 */ }
  }

  // 끝내기(묻는 창에서 한 번 더 누른 뒤): 듣던 자리를 남기고 처음 화면으로 돌아간다
  function stopListening() {
    var c = S.cur;
    if (c && !isAd(c.item) && c.started && !c.done && !c.paused) {
      emitPosition(c);
      emit('pause', storyFields(c, positionNow(c)));
    }
    if (c) c.done = true;
    clearPlayback();
    showHome('', '얼마나 들으실래요?');
  }

  function finishQueue() {
    if (S.cur) S.cur.done = true;
    clearPlayback();
    showHome('다 들었습니다', '더 들으실래요?');
  }

  function clearPlayback() {
    S.cur = null;
    S.queue = [];
    S.index = -1;
    S.pendingQueue = null;
    setStale(false);
    store.del('queue');
    stopAudio();
    setPlaying(false);
    if ('mediaSession' in navigator) {
      try { navigator.mediaSession.metadata = null; navigator.mediaSession.playbackState = 'none'; } catch (e) { /* 무시 */ }
    }
    renderMini();
    if (S.tab === 'list') renderList();
  }

  function saveQueue() {
    store.set('queue', { items: S.queue, index: S.index, minutes: S.minutes, savedAt: Date.now() });
  }

  function fromCatalog(it) {
    return {
      content_id: it.content_id, kind: 'story', title: it.title, category: it.category, audio_uri: it.audio_uri,
      duration_sec: it.duration_sec, playback_rate: 1, attribution: it.attribution, license: it.license
    };
  }

  // 목록에서 고른 이야기는 지금 듣던 것 바로 뒤에 끼워 넣고 그리로 넘어간다
  function playFromList(contentId) {
    var it = S.catalog[contentId];
    if (!it) return;
    var c = S.cur;
    if (c && isAd(c.item) && !c.failed) { toast('광고가 끝난 뒤에 고를 수 있습니다'); return; }
    if (c && c.item.content_id === contentId) { switchTab('listen'); return; }
    if (locked()) return;
    if (c) {
      if (c.started && !c.done) emitPosition(c);   // 목록을 바꿔 트는 것은 건너뛰기로 치지 않는다
      c.done = true;
      S.queue.splice(S.index + 1, 0, fromCatalog(it));
      playIndex(S.index + 1);
    } else {
      S.queue = [fromCatalog(it)];
      S.minutes = null;
      playIndex(0);
    }
    showPlayer();
    switchTab('listen');
  }

  // 차량 앱의 추천 모드와 같은 요청. 관심사는 비워 보내면 서버가 저장된 값을 쓴다
  function fetchPlaylist(minutes) {
    return flushEvents(2000).then(ensureDevice).then(function (d) {
      var q = 'user_id=' + encodeURIComponent(d.user_id) + '&minutes=' + minutes +
        '&interests=&speed=1.0&mode=recommend&hour=' + new Date().getHours();
      return api('/recsys/playlist?' + q);
    }).then(function (pl) { return (pl && pl.items) || []; });
  }

  function startQueue(minutes, button) {
    if (S.loadingQueue) return;
    S.loadingQueue = true;
    unlockAudio();
    setPresetsBusy(true, button);
    el.homeNote.hidden = true;
    fetchPlaylist(minutes).then(function (items) {
      if (!items.length) { showHome('지금은 새로 들을 이야기가 없습니다', '얼마나 들으실래요?'); return; }
      S.queue = items;
      S.minutes = minutes;
      store.set('minutes', minutes);
      S.errorStreak = 0;
      showPlayer();
      playIndex(0);
    }).catch(function () {
      if (!S.redirecting) toast('목록을 불러오지 못했습니다. 다시 눌러 주세요.');
    }).then(function () {
      S.loadingQueue = false;
      setPresetsBusy(false);
    });
  }

  function setStale(on) {
    S.stale = on;
    el.rebuild.hidden = !(on && S.cur);
  }

  // 새 목록에서 지금 듣는 이야기는 빼고, 그래서 맨 앞에 오거나 연달아 붙게 된 광고도 뺀다
  function withoutCurrent(items, contentId) {
    var out = [];
    items.forEach(function (it) {
      if (!isAd(it) && it.content_id === contentId) return;
      if (isAd(it) && (!out.length || isAd(out[out.length - 1]))) return;
      out.push(it);
    });
    return out;
  }

  // 관심사를 바꾼 뒤 '지금 목록 다시 만들기': 고른 시간은 그대로 두고 새 목록을 받아 바로 튼다.
  // 듣던 이야기는 목록에서 고를 때처럼 자리(position)만 남기고 skip은 보내지 않는다 (건너뛴 것으로 잡히면 그 분야 추천이 줄어든다).
  // 새 목록이 올 때까지는 듣던 것을 계속 튼다
  function rebuildQueue() {
    if (!S.cur || S.loadingQueue) return;
    var minutes = S.minutes || store.get('minutes') || DEFAULT_MINUTES;
    S.loadingQueue = true;
    el.rebuildGo.disabled = true;
    el.rebuildGo.setAttribute('aria-busy', 'true');
    fetchPlaylist(minutes).then(function (all) {
      var c = S.cur;
      if (!c) return;   // 그 사이에 끝냈다
      var items = withoutCurrent(all, c.item.content_id);
      if (!items.some(function (it) { return !isAd(it); })) { toast('지금은 새로 들을 이야기가 없습니다'); return; }
      setStale(false);
      if (isAd(c.item) && !c.done) {
        // 광고는 넘길 수 없으니 광고가 끝난 다음에 바꿔 튼다
        S.pendingQueue = { items: items, minutes: minutes };
        toast('광고가 끝나면 새 목록으로 넘어갑니다');
        return;
      }
      if (c.started && !c.done) emitPosition(c);
      c.done = true;
      S.queue = items;
      S.minutes = minutes;
      S.errorStreak = 0;
      playIndex(0);
      toast('새 목록으로 바꿨습니다');
    }).catch(function () {
      if (!S.redirecting) toast('목록을 다시 만들지 못했습니다. 다시 눌러 주세요.');
    }).then(function () {
      S.loadingQueue = false;
      el.rebuildGo.disabled = false;
      el.rebuildGo.removeAttribute('aria-busy');
    });
  }

  function resumeSaved() {
    var q = store.get('queue');
    if (!q || !q.items || !q.items[q.index]) { el.resume.hidden = true; return; }
    S.queue = q.items;
    S.minutes = q.minutes;
    S.errorStreak = 0;
    showPlayer();
    playIndex(q.index);
  }

  /* ---------- 오디오 요소에서 오는 신호 ---------- */

  function isCurrentSource(c) {
    return !!audio.currentSrc && audio.currentSrc.indexOf(c.item.audio_uri) !== -1;
  }

  audio.addEventListener('playing', function () {
    var c = S.cur;
    if (!c || c.done || !isCurrentSource(c)) return;
    S.unlocked = true;
    S.errorStreak = 0;
    if (!c.started) {
      c.started = true;
      c.lastReportAt = audio.currentTime || 0;
      if (isAd(c.item)) emit('ad_start', adFields(c, 0));
      else emit('play', storyFields(c, audio.currentTime < 0.5 ? 0 : positionNow(c)));
    } else if (c.paused) {
      if (!isAd(c.item)) emit('resume', storyFields(c, positionNow(c)));
    } else if (c.stalled) {
      emitPosition(c);
    }
    c.paused = false;
    c.stalled = false;
    setState('playing');
  });

  audio.addEventListener('pause', function () {
    var c = S.cur;
    if (!c || c.done || !c.started || c.paused) return;
    var d = durationOf(c.item);
    if (audio.ended || (d && audio.currentTime >= d - 0.3)) return;   // 끝에서 나는 pause는 complete가 맡는다
    c.paused = true;
    if (!isAd(c.item)) {
      emitPosition(c);
      emit('pause', storyFields(c, positionNow(c)));
    }
    setState('paused');
  });

  // 받아 오느라 소리가 끊기면 그 자리를 남겨 둔다 (끊긴 시간이 들은 시간으로 잡히지 않게)
  audio.addEventListener('waiting', function () {
    var c = S.cur;
    if (!c || c.done || !c.started || c.paused || c.stalled) return;
    c.stalled = true;
    emitPosition(c);
    setState('loading');
  });

  audio.addEventListener('timeupdate', function () {
    var c = S.cur;
    if (!c || c.done) return;
    renderProgress();
    if (c.started && !c.paused && !c.stalled && !isAd(c.item) &&
        audio.currentTime - c.lastReportAt >= POSITION_EVERY_SEC) {
      emitPosition(c);
    }
  });

  audio.addEventListener('durationchange', function () { if (S.cur) renderProgress(); });

  audio.addEventListener('ended', function () {
    var c = S.cur;
    if (!c || c.done || !c.started) return;
    c.done = true;
    var d = durationOf(c.item);
    if (isAd(c.item)) emit('ad_complete', adFields(c, d));
    else emit('complete', storyFields(c, d));
    advance();
  });

  audio.addEventListener('error', function () {
    var c = S.cur;
    if (!c || c.done) return;
    if (isAd(c.item)) {
      emit('ad_error', adFields(c, positionNow(c)));
    } else if (c.started) {
      emitPosition(c);
      emit('skip', Object.assign(storyFields(c, positionNow(c)), { skip_reason: 'error' }));
    }
    c.done = true;
    c.failed = true;
    S.errorStreak += 1;
    // 로그인이 풀려서 음원이 막힌 것이라면 이 호출이 로그인 화면으로 보낸다
    api('/recsys/status').catch(function () { /* 무시 */ });
    if (S.errorStreak >= 3) {
      var keep = { items: S.queue, index: S.index, minutes: S.minutes, savedAt: Date.now() };
      clearPlayback();
      store.set('queue', keep);
      showHome('지금은 재생이 되지 않습니다. 잠시 뒤에 다시 눌러 주세요.', '얼마나 들으실래요?');
      return;
    }
    toast('재생하지 못해 다음으로 넘어갑니다');
    advance();
  });

  /* ---------- 화면 ---------- */

  function fmt(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    var s = sec % 60;
    return Math.floor(sec / 60) + ':' + (s < 10 ? '0' : '') + s;
  }

  function categoryName(code) { return CATEGORY[code] || ''; }

  var toastTimer = null;
  function toast(msg) {
    el.toast.textContent = msg;
    el.toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.toast.classList.remove('show'); }, 2600);
  }

  function setPlaying(on) {
    [el.play, el.miniPlay].forEach(function (b) {
      b.dataset.playing = on ? 'true' : 'false';
      b.setAttribute('aria-label', on ? '일시정지' : '재생');
    });
    if ('mediaSession' in navigator) {
      try { navigator.mediaSession.playbackState = S.cur ? (on ? 'playing' : 'paused') : 'none'; } catch (e) { /* 무시 */ }
    }
  }

  function setState(state) {
    el.player.dataset.state = state;
    setPlaying(state === 'playing' || (state === 'loading' && S.cur && !S.cur.paused));
  }

  function setPresetsBusy(busy, button) {
    var all = document.querySelectorAll('.preset');
    for (var i = 0; i < all.length; i++) {
      all[i].disabled = busy && all[i] !== button;
      all[i].removeAttribute('aria-busy');
    }
    if (busy && button) button.setAttribute('aria-busy', 'true');
  }

  function showHome(note, heading) {
    closeSheet();
    el.player.hidden = true;
    el.home.hidden = false;
    el.homeNote.textContent = note || '';
    el.homeNote.hidden = !note;
    el.homeHeading.textContent = heading;
    renderHome();
  }

  function showPlayer() {
    el.home.hidden = true;
    el.player.hidden = false;
  }

  function renderHome() {
    var last = store.get('minutes');
    var all = document.querySelectorAll('.preset');
    for (var i = 0; i < all.length; i++) {
      all[i].dataset.last = String(Number(all[i].dataset.minutes) === last);
    }
    var q = store.get('queue');
    var fresh = q && q.items && q.items[q.index] && Date.now() - q.savedAt < RESUME_WITHIN_MS;
    if (fresh && !S.cur) {
      var t = q.items[q.index];
      for (var j = q.index; j < q.items.length; j++) if (!isAd(q.items[j])) { t = q.items[j]; break; }
      el.resumeTitle.textContent = t.title || '';
      el.resume.hidden = false;
    } else {
      el.resume.hidden = true;
    }
    var names = GROUPS.filter(groupOn).map(function (g) { return g.name; });
    el.homeInterestsValue.textContent = names.length && names.length < GROUPS.length ? names.join(', ') : '전체';
  }

  function renderTrack() {
    var c = S.cur;
    if (!c) return;
    var item = c.item, ad = isAd(item);
    el.player.dataset.kind = ad ? 'ad' : 'story';
    el.player.dataset.contentId = item.content_id;

    var total = 0, no = 0, remain = 0;
    S.queue.forEach(function (q, i) {
      if (!isAd(q)) { total += 1; if (i <= S.index) no += 1; }
      if (i >= S.index) remain += q.duration_sec || 0;
    });
    var mins = Math.max(1, Math.round(remain / 60));
    el.count.textContent = (total > 1 ? no + ' / ' + total + ' · ' : '') + '약 ' + mins + '분 남음';

    el.title.textContent = ad ? '광고' : (item.title || '');
    el.meta.textContent = ad ? (item.title || '') : categoryName(item.category);
    el.note.hidden = !ad;

    el.prev.disabled = ad || prevStoryIndex() < 0;
    el.next.disabled = ad;
    el.back10.disabled = ad;
    el.like.disabled = ad;
    el.like.setAttribute('aria-pressed', String(!!S.liked[item.content_id]));

    var nx = S.queue[S.index + 1];
    el.nextLine.textContent = nx ? '다음 · ' + (isAd(nx) ? '광고' : nx.title) : (S.queue.length > 1 ? '마지막 이야기입니다' : '');

    renderSource(item, ad);
    el.fill.style.transform = 'scaleX(0)';
    el.cur.textContent = '0:00';
    el.dur.textContent = fmt(item.duration_sec);
    el.bar.setAttribute('aria-valuenow', '0');

    el.player.classList.remove('swap');
    void el.player.offsetWidth;
    el.player.classList.add('swap');

    renderMini();
    setMediaSession(item, ad);
    if (S.tab === 'list') { renderList(); loadHistory(); }
  }

  // 출처가 하나도 없으면 칸을 통째로 숨긴다. 칸이 있든 없든 아래 조작부 자리는 같다
  function renderSource(item, ad) {
    var cat = S.catalog[item.content_id] || {};
    var attribution = ad ? '' : (item.attribution || cat.attribution || '');
    var license = ad ? '' : (item.license || cat.license || '');
    var url = ad ? '' : (cat.source_url || item.source_url || '');
    var host = '';
    if (/^https?:\/\//i.test(url)) {
      try { host = new URL(url).hostname.replace(/^www\./, ''); } catch (e) { host = ''; }
    }
    var has = !!(attribution || license || host);
    el.player.dataset.source = String(has);
    el.source.hidden = !has;
    if (!has) return;
    el.srcAttr.textContent = attribution;
    el.srcAttr.hidden = !attribution;
    el.srcLicense.textContent = license;
    el.srcLicense.hidden = !license;
    if (host) {
      el.srcLink.href = url;
      el.srcLink.textContent = host + ' 원문 보기';
      el.srcLink.hidden = false;
    } else {
      el.srcLink.removeAttribute('href');
      el.srcLink.hidden = true;
    }
    el.srcLink.parentNode.hidden = !license && !host;
  }

  function renderProgress() {
    var c = S.cur;
    if (!c) return;
    var d = durationOf(c.item), t = audio.currentTime || 0;
    var ratio = d ? Math.min(1, t / d) : 0;
    el.fill.style.transform = 'scaleX(' + ratio.toFixed(4) + ')';
    el.cur.textContent = fmt(t);
    el.dur.textContent = fmt(d);
    el.bar.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
    if ('mediaSession' in navigator && navigator.mediaSession.setPositionState && d && c.started) {
      try { navigator.mediaSession.setPositionState({ duration: d, playbackRate: c.rate, position: Math.min(t, d) }); } catch (e) { /* 무시 */ }
    }
  }

  function renderMini() {
    var c = S.cur;
    el.mini.hidden = !c || S.tab === 'listen';
    if (!c) return;
    var ad = isAd(c.item);
    el.miniTitle.textContent = ad ? '광고' : (c.item.title || '');
    el.miniMeta.textContent = ad ? (c.item.title || '') : categoryName(c.item.category);
  }

  // 잠금 화면과 차량 화면에 제목과 버튼이 보이게 한다
  function setMediaSession(item, ad) {
    if (!('mediaSession' in navigator) || !window.MediaMetadata) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: ad ? '광고' : (item.title || '온에어'),
        artist: ad ? (item.title || '온에어') : (categoryName(item.category) || '온에어'),
        album: '온에어',
        artwork: [
          { src: new URL('./icons/icon-192.png', location.href).href, sizes: '192x192', type: 'image/png' },
          { src: new URL('./icons/icon-512.png', location.href).href, sizes: '512x512', type: 'image/png' }
        ]
      });
    } catch (e) { /* 무시 */ }
  }

  function bindMediaSession() {
    if (!('mediaSession' in navigator)) return;
    var set = function (name, fn) { try { navigator.mediaSession.setActionHandler(name, fn); } catch (e) { /* 무시 */ } };
    set('play', function () { if (S.cur && audio.paused) startAudio(); });
    set('pause', function () { if (S.cur) audio.pause(); });
    set('nexttrack', userNext);
    set('previoustrack', userPrev);
    set('seekto', null);
    set('seekbackward', null);
    set('seekforward', null);
  }

  function switchTab(name) {
    S.tab = name;
    ['listen', 'talk', 'interests', 'list'].forEach(function (t) { $('tab-' + t).hidden = t !== name; });
    var btns = document.querySelectorAll('.tabbtn');
    for (var i = 0; i < btns.length; i++) {
      if (btns[i].dataset.tab === name) btns[i].setAttribute('aria-current', 'page');
      else btns[i].removeAttribute('aria-current');
    }
    el.views.scrollTop = 0;
    renderMini();
    if (name === 'listen' && !S.cur) renderHome();
    if (name === 'interests') renderTopics();
    if (name === 'list') { renderList(); loadHistory(); }
    if (name === 'talk' && window.TeumTalk) window.TeumTalk.show();
  }

  /* ---------- 끝내기 전에 묻는 창 ---------- */

  var sheetReturn = null;
  function setInert(on) {
    [el.views, el.mini, el.tabbar].forEach(function (n) {
      if (on) n.setAttribute('inert', ''); else n.removeAttribute('inert');
    });
  }
  function openSheet() {
    if (!S.cur || !el.sheet.hidden) return;
    sheetReturn = document.activeElement;
    el.sheet.hidden = false;
    setInert(true);
    el.sheetCancel.focus();
  }
  function closeSheet() {
    if (el.sheet.hidden) return;
    el.sheet.hidden = true;
    setInert(false);
    if (sheetReturn && sheetReturn.focus) sheetReturn.focus();
    sheetReturn = null;
  }
  function sheetKey(e) {
    if (el.sheet.hidden) return;
    if (e.key === 'Escape') { e.preventDefault(); closeSheet(); return; }
    if (e.key === 'Tab') {
      // 창 안의 두 버튼 사이에서만 돈다
      e.preventDefault();
      (document.activeElement === el.sheetOk ? el.sheetCancel : el.sheetOk).focus();
    }
  }

  /* ---------- 관심사 ---------- */

  function available(g) {
    var has = Object.keys(S.catalog).length > 0;
    return g.codes.filter(function (code) {
      return !has || S.catalogList.some(function (it) { return it.category === code; });
    });
  }
  function groupOn(g) {
    return g.codes.some(function (code) { return S.interests.indexOf(code) !== -1; });
  }

  function renderTopics() {
    el.topics.textContent = '';
    GROUPS.forEach(function (g) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'topic';
      b.setAttribute('role', 'switch');
      b.setAttribute('aria-checked', String(groupOn(g)));
      b.dataset.group = g.id;
      b.disabled = !available(g).length;
      var text = document.createElement('span');
      var name = document.createElement('span'); name.className = 'topic-name'; name.textContent = g.name;
      var keys = document.createElement('span'); keys.className = 'topic-keys'; keys.textContent = g.keys;
      text.appendChild(name); text.appendChild(keys);
      var check = document.createElement('span');
      check.className = 'topic-check';
      check.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5.5 12.5l4.2 4.2 8.8-9.2"/></svg>';
      b.appendChild(text); b.appendChild(check);
      b.addEventListener('click', function () { toggleGroup(g); });
      el.topics.appendChild(b);
    });
  }

  var saveChain = Promise.resolve();
  function toggleGroup(g) {
    var before = S.interests.slice();
    var on = groupOn(g);
    var next = S.interests.filter(function (code) { return g.codes.indexOf(code) === -1; });
    if (!on) next = next.concat(available(g));
    S.interests = next;
    renderTopics();
    // 누른 순서대로 저장한다
    saveChain = saveChain.then(function () {
      return api('/me/interests', { method: 'PUT', body: { interests: next }, auth: true });
    }).then(function (r) {
      if (r && r.interests && S.interests === next) S.interests = r.interests;
      // 듣는 중이면 알림 대신 '지금 목록 다시 만들기' 줄을 띄운다
      if (S.cur) setStale(true);
      else toast('저장했습니다. 다음 목록부터 반영됩니다.');
    }).catch(function () {
      if (S.interests === next) { S.interests = before; renderTopics(); }
      if (!S.redirecting) toast('저장하지 못했습니다. 다시 눌러 주세요.');
    });
  }

  /* ---------- 목록, 들은 기록 ---------- */

  function isNew(id) {
    return NEW_PREFIXES.some(function (p) { return String(id).indexOf(p) === 0; });
  }

  // 이 기기에서 들은 이야기인지와 그 표시 글. 1초도 듣지 않은 기록은 들은 것으로 치지 않는다
  function heardOf(h) {
    if (!h) return null;
    if (h.completed) return { kind: 'done', text: '끝까지 들음' };
    if (h.skipped) return { kind: 'part', text: '넘김' };
    if (h.played_sec >= 1) return { kind: 'part', text: fmt(h.played_sec) + ' 들음' };
    return null;
  }

  function listFor(mode) {
    if (mode === 'history') {
      return S.history.filter(function (h) { return S.catalog[h.content_id] && S.heard[h.content_id]; })
        .map(function (h) { return S.catalog[h.content_id]; });
    }
    return S.catalogList.filter(function (it) {
      if (mode === 'new') return isNew(it.content_id);
      if (mode === 'unheard') return !S.heard[it.content_id];
      return true;
    });
  }

  var CHECK_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5.5 12.5l4.2 4.2 8.8-9.2"/></svg>';

  function itemRow(it, mode) {
    var li = document.createElement('li');
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'item';
    b.dataset.contentId = it.content_id;
    var current = S.cur && S.cur.item.content_id === it.content_id;
    var heard = S.heard[it.content_id];
    if (current) b.dataset.current = 'true';
    if (heard) b.dataset.heard = heard.kind;
    var text = document.createElement('span');
    var title = document.createElement('span'); title.className = 'item-title';
    // '새' 표시는 아직 듣지 않은 새 이야기에만 붙인다 (새 이야기만 모아 볼 때는 뺀다)
    if (isNew(it.content_id) && !heard && mode !== 'new') {
      b.dataset.fresh = 'true';
      var badge = document.createElement('span'); badge.className = 'badge-new'; badge.textContent = '새';
      badge.setAttribute('aria-hidden', 'true');
      var say = document.createElement('span'); say.className = 'vh'; say.textContent = '새 이야기. ';
      title.appendChild(badge); title.appendChild(say);
    }
    title.appendChild(document.createTextNode(it.title));
    var meta = document.createElement('span'); meta.className = 'item-meta';
    meta.textContent = [mode === 'history' ? categoryName(it.category) : '', fmt(it.duration_sec)].filter(Boolean).join(' · ');
    text.appendChild(title); text.appendChild(meta);
    var state = document.createElement('span'); state.className = 'item-state';
    if (current) {
      state.textContent = '듣는 중';
    } else if (heard) {
      if (heard.kind === 'done') state.innerHTML = CHECK_SVG;
      state.appendChild(document.createTextNode(heard.text));
    }
    b.appendChild(text); b.appendChild(state);
    li.appendChild(b);
    return li;
  }

  function renderList() {
    var loaded = S.catalogList.length > 0;
    var tabs = document.querySelectorAll('.seg button');
    for (var i = 0; i < tabs.length; i++) {
      var m = tabs[i].dataset.mode;
      tabs[i].setAttribute('aria-selected', String(m === S.listMode));
      tabs[i].querySelector('.seg-n').textContent = loaded ? String(listFor(m).length) : '';
    }
    el.list.textContent = '';
    var frag = document.createDocumentFragment();
    var items = listFor(S.listMode);
    var lastCat = null;
    items.forEach(function (it) {
      // 들은 기록은 최근 순서, 나머지는 분류별로 묶어 보여 준다
      if (S.listMode !== 'history' && it.category !== lastCat) {
        lastCat = it.category;
        var h = document.createElement('li');
        h.className = 'list-h';
        h.textContent = categoryName(it.category) || '기타';
        frag.appendChild(h);
      }
      frag.appendChild(itemRow(it, S.listMode));
    });
    el.list.appendChild(frag);
    el.listEmpty.textContent = !loaded ? '목록을 불러오지 못했습니다' : ({
      all: '목록을 불러오지 못했습니다',
      'new': '새로 들어온 이야기가 없습니다',
      unheard: '모든 이야기를 들었습니다',
      history: '아직 들은 이야기가 없습니다'
    })[S.listMode];
    el.listEmpty.hidden = items.length > 0;
  }

  function setListMode(mode) {
    if (LIST_MODES.indexOf(mode) === -1) return;
    S.listMode = mode;
    renderList();
    loadHistory();
  }

  function loadHistory() {
    return flushEvents(1500).then(function () { return api('/history', { auth: true }); }).then(function (r) {
      var items = (r && r.items) || [];
      var sig = JSON.stringify(items);
      if (sig === S.historySig) return;   // 바뀐 것이 없으면 목록을 다시 그리지 않는다
      S.historySig = sig;
      S.history = items.slice().sort(function (a, b) {
        return String(b.last_ts || '').localeCompare(String(a.last_ts || ''));
      });
      S.heard = {};
      S.history.forEach(function (h) {
        var st = heardOf(h);
        if (st) S.heard[h.content_id] = st;
        if (h.liked) S.liked[h.content_id] = true; else delete S.liked[h.content_id];
      });
      if (S.tab === 'list') renderList();
      if (S.cur && !isAd(S.cur.item)) el.like.setAttribute('aria-pressed', String(!!S.liked[S.cur.item.content_id]));
    }).catch(function () { /* 기록이 없어도 듣는 데는 지장이 없다 */ });
  }

  function toggleLike() {
    var c = S.cur;
    if (!c || isAd(c.item)) return;
    var id = c.item.content_id;
    var on = !S.liked[id];
    if (on) S.liked[id] = true; else delete S.liked[id];
    el.like.setAttribute('aria-pressed', String(on));
    emit(on ? 'like' : 'unlike', { content_id: id, playback_id: c.playbackId });
  }

  /* ---------- 시작 ---------- */

  function bind() {
    var presets = document.querySelectorAll('.preset');
    for (var i = 0; i < presets.length; i++) {
      presets[i].addEventListener('click', function () { startQueue(Number(this.dataset.minutes), this); });
    }
    el.resume.addEventListener('click', resumeSaved);
    el.homeInterests.addEventListener('click', function () { switchTab('interests'); });
    el.play.addEventListener('click', togglePlay);
    el.miniPlay.addEventListener('click', togglePlay);
    el.next.addEventListener('click', userNext);
    el.prev.addEventListener('click', userPrev);
    el.like.addEventListener('click', toggleLike);
    el.back10.addEventListener('click', rewind10);
    el.stop.addEventListener('click', openSheet);
    el.sheetOk.addEventListener('click', function () { closeSheet(); stopListening(); });
    el.sheetCancel.addEventListener('click', closeSheet);
    el.sheetScrim.addEventListener('click', closeSheet);
    document.addEventListener('keydown', sheetKey);
    el.rebuildGo.addEventListener('click', rebuildQueue);
    $('mini-open').addEventListener('click', function () { switchTab('listen'); });
    var tabs = document.querySelectorAll('.tabbtn');
    for (var j = 0; j < tabs.length; j++) {
      tabs[j].addEventListener('click', function () { switchTab(this.dataset.tab); });
    }
    var segs = document.querySelectorAll('.seg button');
    for (var k = 0; k < segs.length; k++) {
      segs[k].addEventListener('click', function () { setListMode(this.dataset.mode); });
    }
    el.list.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('.item') : null;
      if (b) playFromList(b.dataset.contentId);
    });
    window.addEventListener('pagehide', flushOnHide);
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'hidden' && S.cur) saveQueue();
    });
    bindMediaSession();
  }

  function init() {
    bind();
    setPlaying(false);
    renderHome();
    renderTopics();
    api('/contents').then(function (r) {
      var items = (r && r.items) || [];
      items.forEach(function (it) { S.catalog[it.content_id] = it; });
      S.catalogList = items.slice().sort(function (a, b) {
        return String(a.category).localeCompare(String(b.category)) || String(a.title).localeCompare(String(b.title), 'ko');
      });
      if (S.cur) renderSource(S.cur.item, isAd(S.cur.item));
      if (S.tab === 'list') renderList();
      if (S.tab === 'interests') renderTopics();
    }).catch(function () { /* 목록 탭에서 다시 알린다 */ });
    api('/me/interests', { auth: true }).then(function (r) {
      S.interests = (r && r.interests) || [];
      renderHome();
      if (S.tab === 'interests') renderTopics();
    }).catch(function () {
      if (!S.redirecting) showHome('서버에 연결하지 못했습니다', '얼마나 들으실래요?');
    });
    loadHistory();
  }

  init();
})();
