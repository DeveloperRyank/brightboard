// Animated breakdown of a marked test. Exposed as window.renderBreakdown so the
// results page can call it the moment the background grader finishes.

(function () {
  const esc = (s) => {
    const div = document.createElement("div");
    div.textContent = s == null ? "" : String(s);
    return div.innerHTML;
  };
  const pct = (v) => (v === null || v === undefined ? "—" : Math.round(v * 100) + "%");
  const pretty = (slug) =>
    String(slug || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

  function renderBreakdown(s, wrap) {
    const results = s.results || [];
    const total = results.length;
    const right = results.filter((r) => r.correct).length;
    const ratio = total ? right / total : 0;
    // Mastery only exists once answers are tagged to a concept, so don't show a bare dash.
    const masteryText = s.mastery === null || s.mastery === undefined
      ? "" : ` · topic mastery now <strong>${pct(s.mastery)}</strong>`;
    const ringColor = ratio < 0.4 ? "var(--brick)" : ratio < 0.7 ? "var(--ochre)" : "var(--sage)";
    const C = 2 * Math.PI * 52;

    let html = `
    <div class="session-panel glass">
      <div class="sess-head">
        <div class="ring-wrap">
          <svg viewBox="0 0 120 120" aria-hidden="true">
            <circle class="ring-bg" cx="60" cy="60" r="52"></circle>
            <circle class="ring-fg" id="sess-ring" cx="60" cy="60" r="52"
              style="stroke: ${ringColor};" stroke-dasharray="${C}" stroke-dashoffset="${C}"></circle>
          </svg>
          <div class="ring-label"><span id="sess-count">0</span><small>/${total}</small></div>
        </div>
        <div class="sess-titles">
          <div class="eyebrow">Paper marked</div>
          <h2>${esc(s.topic_name || pretty(s.topic))}</h2>
          <p class="sub" style="margin: 6px 0 0;">
            ${right} of ${total} correct${masteryText}
          </p>
        </div>
      </div>`;

    if ((s.concepts || []).length) {
      html += `<h3 class="sess-h">Concept mastery</h3><div class="concept-rows">`;
      s.concepts.forEach((c, i) => {
        const w = Math.round((c.p || 0) * 100);
        const col = c.p < 0.4 ? "var(--brick)" : c.p < 0.7 ? "var(--ochre)" : "var(--sage)";
        html += `
        <div class="concept-row rise" style="animation-delay: ${200 + i * 90}ms">
          <div class="c-name">${esc(c.name || pretty(c.concept))}
            <small>${c.answers} answer${c.answers === 1 ? "" : "s"}${c.error_type ? " · " + esc(c.error_type_label || pretty(c.error_type)) : ""}</small>
          </div>
          <div class="bar c-bar"><div style="width:${w}%; background:${col};"></div></div>
          <div class="c-p mono">${w}%</div>
        </div>`;
      });
      html += `</div>`;
    }

    html += `<h3 class="sess-h">Question by question</h3><div class="sess-rows">`;
    results.forEach((r, i) => {
      const chip = r.correct
        ? '<span class="chip correct">✓ Correct</span>'
        : '<span class="chip incorrect">✗ Not correct</span>';
      const concept = r.concept_name ? `<span class="mini-chip">${esc(r.concept_name)}</span>` : "";
      const moved = r.p === null || r.p === undefined
        ? "" : `<span class="mini-chip ok">mastery ${pct(r.p)}</span>`;
      html += `
        <div class="result-row rise" style="animation-delay: ${350 + i * 110}ms">
          <div class="r-q">
            ${esc(r.question)}
            <div class="r-tags">${concept}${moved}</div>
            <div class="r-fb">${esc(r.teacher_note || "")}</div>
          </div>
          <div>${chip}</div>
        </div>`;
    });
    html += `</div>`;

    if (s.recommendation) {
      const rec = s.recommendation;
      const what = rec.concept
        ? `${esc(pretty(rec.concept))} in ${esc(rec.topic_name || pretty(rec.topic))}`
        : esc(rec.topic_name || pretty(rec.topic));
      const scoreText = rec.score === null || rec.score === undefined
        ? "you told us this one troubles you"
        : `mastery ${pct(rec.score)}`;
      html += `
      <div class="rec-card rise" style="animation-delay: ${480 + total * 110}ms">
        <div class="target">${iconSvg("target")}</div>
        <div class="rec-text">Focus next on <strong>${what}</strong> — ${scoreText}</div>
        <a class="btn gradient" href="/lesson/${esc(rec.subject)}/${esc(rec.topic)}">Study it now</a>
      </div>`;
    }

    html += `
      <div class="lesson-controls rise" style="animation-delay: ${580 + total * 110}ms">
        <a class="btn" href="/analytics">See full analytics</a>
        <a class="btn ghost" href="/subject/${esc(s.subject)}">More ${esc(pretty(s.subject))} topics</a>
        <a class="btn ghost" href="/">All subjects</a>
      </div>
    </div>`;

    wrap.innerHTML = html;

    // Draw the ring and count up only after first paint, so the CSS transition runs.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const ring = document.getElementById("sess-ring");
      if (ring) ring.style.strokeDashoffset = String(C * (1 - ratio));
      const countEl = document.getElementById("sess-count");
      if (!countEl) return;
      const t0 = performance.now();
      const dur = 1300;
      const step = (now) => {
        const p = Math.min(1, (now - t0) / dur);
        countEl.textContent = String(Math.round((1 - Math.pow(1 - p, 3)) * right));
        if (p < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }));
  }

  window.renderBreakdown = renderBreakdown;
})();
