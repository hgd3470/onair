/* 온에어 듣기 웹앱 '대담' 탭: 두 사람 롱폼(ai/longform/pipeline.py 결과)을 듣는다.
   목록은 스튜디오 API(/admin/api/longform)를 같은 로그인 쿠키로 읽고, 음원은 /admin/longform/<ID>.mp3.
   듣는 위치에 맞춰 대본 줄이 따라가고, 줄마다 근거 카드를 펼쳐 볼 수 있다. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); };
  var PALETTE = ['#5B2A55', '#6E1A1F', '#1F4D45', '#23365F', '#6A4A12', '#3D2C6B'];
  var T = {items: null, cur: null, detail: null, audio: new Audio(), lineAt: -1};
  T.audio.preload = 'metadata';

  function fmt(sec) { sec = Math.max(0, Math.floor(sec || 0)); return Math.floor(sec / 60) + ':' + ('0' + (sec % 60)).slice(-2); }
  function mins(sec) { return sec ? Math.round(sec / 60) + '분' : ''; }
  function color(i) { return PALETTE[i % PALETTE.length]; }
  function cover(it, i, big) {
    var word = (it.title || it.id).replace(/[?!.,:]/g, '').split(/\s+/).slice(0, 2).join(' ');
    return '<div class="tk-cover' + (big ? ' big' : '') + '" style="--c:' + color(i) + '" aria-hidden="true"><span class="tk-cover-k">대담</span><b>' + esc(word) + '</b><span class="tk-cover-h">' + esc((it.show || [])[0] || '') + ' × ' + esc((it.show || [])[1] || '') + '</span></div>';
  }

  function api(path) {
    return fetch(path, {credentials: 'same-origin', cache: 'no-store'}).then(function (r) {
      if (r.status === 401) { location.href = '/admin/login?next=/app/'; throw new Error('login'); }
      return r.json();
    });
  }

  function show() {
    if (T.cur) return renderPlayer();
    $('talk-body').innerHTML = '<p class="tk-empty">불러오는 중…</p>';
    api('/admin/api/longform').then(function (d) {
      T.items = d.items.filter(function (x) { return x.audio; });
      renderList();
    }).catch(function () { $('talk-body').innerHTML = '<p class="tk-empty">대담 목록을 불러오지 못했습니다. 잠시 뒤 다시 열어 주세요.</p>'; });
  }

  function renderList() {
    if (!T.items.length) { $('talk-body').innerHTML = '<p class="tk-empty">아직 들을 수 있는 대담이 없습니다.</p>'; return; }
    $('talk-body').innerHTML = '<ul class="tk-list">' + T.items.map(function (it, i) {
      return '<li class="tk-card" style="--c:' + color(i) + '">' +
        '<div class="tk-top">' + cover(it, i) + '<div class="tk-meta"><h3>' + esc(it.title) + '</h3>' +
        '<p>' + esc((it.show || []).slice(0, 2).join(' × ')) + (it.duration_sec ? ' · ' + mins(it.duration_sec) : '') + '</p></div></div>' +
        (it.hook ? '<p class="tk-hook">“' + esc(it.hook) + '”</p>' : '') +
        '<button class="tk-play" type="button" data-talk="' + esc(it.id) + '"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5v14l11-7z"/></svg>듣기</button></li>';
    }).join('') + '</ul>';
    var bs = $('talk-body').querySelectorAll('[data-talk]');
    for (var i = 0; i < bs.length; i++) bs[i].addEventListener('click', function () { open(this.dataset.talk, true); });
  }

  function open(id, autoplay) {
    var idx = T.items.findIndex(function (x) { return x.id === id; });
    T.cur = T.items[idx]; T.cur._i = idx; T.lineAt = -1;
    api('/admin/api/longform?id=' + encodeURIComponent(id)).then(function (d) {
      T.detail = d;
      if (T.audio.dataset.id !== id) { T.audio.src = '/admin/longform/' + encodeURIComponent(id) + '.mp3'; T.audio.dataset.id = id; }
      renderPlayer();
      if (autoplay) play();
    });
  }

  function facts() {
    var m = {};
    (T.detail.facts || []).forEach(function (f) { var k = /^\**(F\d+)/.exec(f); if (k) m[k[1]] = f.replace(/^\**F\d+\**[.:)\s]*/, ''); });
    return m;
  }

  function renderPlayer() {
    var it = T.cur, d = T.detail, sc = d.script || {lines: []}, fm = facts();
    var who = function (sp) { return sp === 'A' ? (d.show || [])[0] : (d.show || [])[1]; };
    $('talk-body').innerHTML =
      '<button class="tk-back" type="button" id="tk-back"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>대담 목록</button>' +
      '<div class="tk-hero" style="--c:' + color(it._i) + '">' + cover(it, it._i, true) +
      '<h2>' + esc(sc.title) + '</h2><p>' + esc((d.show || []).slice(0, 2).join(' × ')) + '</p>' +
      '<div class="tk-bar" id="tk-bar" role="slider" tabindex="0" aria-label="재생 위치" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><i id="tk-fill"></i></div>' +
      '<div class="tk-times"><span id="tk-cur">0:00</span><span id="tk-dur">' + fmt(it.duration_sec) + '</span></div>' +
      '<div class="tk-ctl"><button type="button" id="tk-b15" aria-label="15초 뒤로">-15</button>' +
      '<button type="button" id="tk-pp" class="tk-pp" aria-label="재생"></button>' +
      '<button type="button" id="tk-f15" aria-label="15초 앞으로">+15</button></div></div>' +
      '<h3 class="tk-sub">대본 <span>듣는 곳을 따라갑니다 · 줄을 누르면 거기서부터</span></h3>' +
      '<ol class="tk-lines" id="tk-lines">' + sc.lines.map(function (l, n) {
        return '<li class="tk-ln ' + l.speaker + '" data-n="' + n + '"><span class="tk-sp">' + esc(who(l.speaker)) + '</span><div><p>' + esc(l.text) + '</p>' +
          ((l.fact_ids || []).length ? '<button type="button" class="tk-fx" data-fx="' + n + '">근거 ' + l.fact_ids.length + '</button><div class="tk-cards" hidden>' +
            l.fact_ids.map(function (f) { return '<p><b>' + esc(f) + '</b> ' + esc(fm[f] || '') + '</p>'; }).join('') + '</div>' : '') + '</div></li>';
      }).join('') + '</ol>' +
      ((d.sources || []).length ? '<details class="tk-src"><summary>출처 ' + d.sources.length + '곳</summary><ul>' + d.sources.map(function (s) { return '<li>' + esc(s) + '</li>'; }).join('') + '</ul></details>' : '');
    $('tk-back').addEventListener('click', function () { T.cur = null; show(); });
    $('tk-pp').addEventListener('click', function () { T.audio.paused ? play() : T.audio.pause(); });
    $('tk-b15').addEventListener('click', function () { T.audio.currentTime = Math.max(0, T.audio.currentTime - 15); });
    $('tk-f15').addEventListener('click', function () { T.audio.currentTime = Math.min(T.audio.duration || 1e9, T.audio.currentTime + 15); });
    var bar = $('tk-bar');
    bar.addEventListener('click', function (e) { var r = bar.getBoundingClientRect(); if (T.audio.duration) T.audio.currentTime = T.audio.duration * (e.clientX - r.left) / r.width; });
    bar.addEventListener('keydown', function (e) { if (e.key === 'ArrowLeft') T.audio.currentTime -= 5; if (e.key === 'ArrowRight') T.audio.currentTime += 5; });
    $('tk-lines').addEventListener('click', function (e) {
      var fx = e.target.closest('.tk-fx');
      if (fx) { var box = fx.nextElementSibling; box.hidden = !box.hidden; return; }
      var li = e.target.closest('.tk-ln'), tm = (T.detail.timing || [])[li ? +li.dataset.n : -1];
      if (tm) { T.audio.currentTime = tm.start + 0.01; play(); }
    });
    T.lineAt = -1; paint();
  }

  function play() {
    var main = $('audio');
    if (main && !main.paused) main.pause();  // 듣기 탭 재생과 겹치지 않게
    T.audio.play().catch(function () {});
    if ('mediaSession' in navigator && T.detail) {
      navigator.mediaSession.metadata = new MediaMetadata({title: (T.detail.script || {}).title || '', artist: (T.detail.show || []).slice(0, 2).join(' × '), album: '온에어 대담'});
    }
  }

  function paint() {
    if (!T.cur || !$('tk-pp')) return;
    var a = T.audio, dur = a.duration || T.cur.duration_sec || 0;
    $('tk-pp').innerHTML = a.paused ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5v14l11-7z"/></svg>' : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z"/></svg>';
    $('tk-pp').setAttribute('aria-label', a.paused ? '재생' : '일시정지');
    $('tk-cur').textContent = fmt(a.currentTime);
    $('tk-dur').textContent = fmt(dur);
    var pct = dur ? 100 * a.currentTime / dur : 0;
    $('tk-fill').style.width = pct + '%';
    $('tk-bar').setAttribute('aria-valuenow', Math.round(pct));
    var tm = T.detail.timing || [], n = -1;
    for (var i = 0; i < tm.length; i++) if (a.currentTime >= tm[i].start - 0.05) n = i;
    if (n !== T.lineAt) {
      var old = document.querySelector('.tk-ln.on'); if (old) old.classList.remove('on');
      var li = document.querySelector('.tk-ln[data-n="' + n + '"]');
      if (li) { li.classList.add('on'); if (!a.paused) li.scrollIntoView({block: 'center', behavior: 'smooth'}); }
      T.lineAt = n;
    }
  }
  ['timeupdate', 'play', 'pause', 'loadedmetadata', 'ended'].forEach(function (ev) { T.audio.addEventListener(ev, paint); });
  document.addEventListener('DOMContentLoaded', function () {
    var main = $('audio');
    if (main) main.addEventListener('play', function () { if (!T.audio.paused) T.audio.pause(); });
  });

  window.TeumTalk = {show: show};
})();
