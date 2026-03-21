// ── Intersection Observer — fade-up animations ──────────────
(function () {
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });

  document.querySelectorAll('.fade-up').forEach(function (el) {
    observer.observe(el);
  });
})();

// ── Animated counters ────────────────────────────────────────
(function () {
  var counters = document.querySelectorAll('[data-count]');
  if (!counters.length) return;

  var started = false;

  function runCounters() {
    counters.forEach(function (el) {
      var target = parseInt(el.getAttribute('data-count'), 10);
      var suffix = el.getAttribute('data-suffix') || '';
      var duration = 1600;
      var start = performance.now();

      function step(now) {
        var elapsed = now - start;
        var progress = Math.min(elapsed / duration, 1);
        var ease = 1 - Math.pow(1 - progress, 3);
        el.textContent = Math.floor(ease * target).toLocaleString('ru-RU') + suffix;
        if (progress < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    });
  }

  var section = document.getElementById('stats');
  if (!section) return;

  var obs = new IntersectionObserver(function (entries) {
    if (entries[0].isIntersecting && !started) {
      started = true;
      runCounters();
      obs.disconnect();
    }
  }, { threshold: 0.3 });

  obs.observe(section);
})();

// ── Admin tab switcher ───────────────────────────────────────
(function () {
  var tabs = document.querySelectorAll('.admin-tab-btn');
  var panels = document.querySelectorAll('.admin-tab-panel');

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var target = tab.getAttribute('data-tab');

      tabs.forEach(function (t) { t.classList.remove('active'); });
      panels.forEach(function (p) { p.style.display = 'none'; });

      tab.classList.add('active');
      var panel = document.querySelector('.admin-tab-panel[data-panel="' + target + '"]');
      if (panel) panel.style.display = 'block';
    });
  });
})();

// ── Smooth scroll for nav links ──────────────────────────────
(function () {
  document.querySelectorAll('a[href^="#"]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      var id = a.getAttribute('href').slice(1);
      var target = document.getElementById(id);
      if (!target) return;
      e.preventDefault();
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
})();

// ── Nav active state on scroll ───────────────────────────────
(function () {
  var sections = document.querySelectorAll('section[id]');
  var navLinks = document.querySelectorAll('.nav-links a[href^="#"]');

  window.addEventListener('scroll', function () {
    var scrollY = window.scrollY + 100;
    sections.forEach(function (sec) {
      if (scrollY >= sec.offsetTop && scrollY < sec.offsetTop + sec.offsetHeight) {
        navLinks.forEach(function (a) { a.style.color = ''; });
        var active = document.querySelector('.nav-links a[href="#' + sec.id + '"]');
        if (active) active.style.color = 'var(--text)';
      }
    });
  }, { passive: true });
})();
