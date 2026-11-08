// Sign-up asks for one thing at a time. All three fieldsets are already in the page, so the
// form still works with JavaScript switched off — every field is simply visible at once.
// With script on, the panels become steps: the script only hides the ones the student has
// not reached and moves between them.
//
// Which school years exist is the qualification's decision, so each qualification's year
// list is rendered and CSS reveals the chosen one — see the .qnote/.yg rules in style.css.

(function () {
  const form = document.getElementById("signup");
  if (!form) return;

  const panels = Array.from(form.querySelectorAll(".su-step"));
  const dots = Array.from(form.querySelectorAll(".su-dot"));
  const back = document.getElementById("su-back");
  const next = document.getElementById("su-next");
  const done = document.getElementById("su-done");
  const stepField = document.getElementById("su-step");
  const remembers = !form.hasAttribute("data-rejected");
  const LAST = panels.length;
  let current = 1;

  form.classList.add("wiz-live");

  function markPills() {
    form.querySelectorAll(".opt-pill").forEach((pill) => {
      const input = pill.querySelector("input");
      if (input) pill.classList.toggle("sel", input.checked);
    });
  }

  function show(n) {
    current = Math.max(1, Math.min(LAST, n));
    panels.forEach((p) => p.classList.toggle("on", +p.dataset.panel === current));
    dots.forEach((d) => {
      const i = +d.dataset.dot;
      d.classList.toggle("now", i === current);
      d.classList.toggle("past", i < current);
    });
    stepField.value = String(current);
    back.hidden = current === 1;
    next.hidden = current === LAST;
    done.hidden = current !== LAST;
    if (remembers) {
      try { sessionStorage.setItem("bb.signup.step", String(current)); } catch (e) { /* blocked */ }
    }
  }

  // A year belongs to the qualification that names it, so picking a different
  // qualification must clear whatever was chosen under the old one — otherwise the account
  // is saved with a year that does not exist in the system the student just switched to.
  form.querySelectorAll("#quals input").forEach((radio) => {
    radio.addEventListener("change", () => {
      const year = form.querySelector('input[name="grade"]:checked');
      if (year) year.checked = false;
      markPills();
    });
  });
  form.addEventListener("change", markPills);

  // Continue only passes over fields the browser itself considers unfilled, so the step
  // gate and the submit gate are the same rules, written once.
  function blockedIn(panel) {
    if (panel.checkValidity()) return null;
    return panel.querySelector(":invalid") || panel;
  }

  function advance() {
    const bad = blockedIn(panels[current - 1]);
    if (!bad) { show(current + 1); return; }
    bad.reportValidity();
    if (bad.focus) bad.focus();
  }

  next.addEventListener("click", advance);
  back.addEventListener("click", () => show(current - 1));

  dots.forEach((d) => d.addEventListener("click", () => {
    const n = +d.dataset.dot;
    if (n <= current) { show(n); return; }
    for (let i = current; i < n; i += 1) {
      const bad = blockedIn(panels[i - 1]);
      if (bad) { show(i); bad.reportValidity(); return; }
    }
    show(n);
  }));

  // A browser that cannot focus a hidden field would swallow a native validation message,
  // so the step holding the first problem is unhidden before the report is shown.
  form.addEventListener("submit", (e) => {
    if (form.checkValidity()) return;
    for (let i = 0; i < LAST; i += 1) {
      if (blockedIn(panels[i])) { show(i + 1); return; }
    }
  });

  let start = +stepField.value || 1;
  if (remembers && start === 1) {
    try { start = Math.min(+sessionStorage.getItem("bb.signup.step") || 1, LAST) || 1; }
    catch (e) { /* private mode */ }
  }
  show(start);
  if (start > 1) {
    const first = panels[start - 1].querySelector("input:not([type=hidden])");
    if (first) first.focus();
  }
})();
