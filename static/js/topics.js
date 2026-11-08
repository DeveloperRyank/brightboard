// Searching a subject. The groups and their open/closed state are plain <details> in the
// page, so folding works with no script at all; this only adds the two filters a student
// asks for when a syllabus has thirty topics in it — a word and a year.
//
// Nothing is fetched: every topic name is already in the page, so filtering cannot make a
// topic appear that the syllabus does not have.

(function () {
  const root = document.getElementById("tgroups");
  const tools = document.querySelector(".tp-tools");
  if (!root || !tools) return;

  const input = document.getElementById("tp-q");
  const pills = Array.from(document.querySelectorAll("#tp-years [data-year]"));
  const none = root.querySelector(".tp-none");

  // A year of a language syllabus is seventy topics, not nine, and the grouping that fixed
  // Chemistry would not have fixed that. A strand shows its first CAP topics and a button
  // for the rest — except under a search, where the answer has to be the whole answer.
  const CAP = 12;

  const groups = Array.from(root.querySelectorAll(".tgroup")).map((el) => ({
    el,
    // What the student left open is theirs; a search opens everything to answer it, and
    // the state comes back untouched when the search is cleared.
    open: el.open,
    lists: Array.from(el.querySelectorAll(".topic-list")).map((list) => {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "link-btn tp-more";
      more.hidden = true;
      list.after(more);
      return {
        el: list,
        rows: Array.from(list.querySelectorAll(".topic-row")),
        head: list.previousElementSibling,
        more,
        expanded: false,
      };
    }),
  }));
  let year = "";

  tools.hidden = false;

  function paint(item) {
    const word = input.value.trim().toLowerCase();
    const offYear = year && item.el.dataset.year !== year;
    let shown = 0;
    item.lists.forEach((list) => {
      let hits = 0;
      list.rows.forEach((row) => {
        const hit = !offYear && (!word || row.dataset.name.includes(word));
        row.hidden = !hit;
        if (hit) { hits += 1; shown += 1; }
      });
      let seen = 0;
      const capped = !word && !list.expanded && hits > CAP;
      if (capped) {
        list.rows.forEach((row) => {
          if (row.hidden) return;
          if (++seen > CAP) row.hidden = true;
        });
      }
      list.more.hidden = !capped;
      if (capped) list.more.textContent = `Show all ${hits} topics in this strand`;
      list.el.hidden = hits === 0;
      if (list.head && list.head.classList.contains("tg-strand")) list.head.hidden = list.el.hidden;
    });
    item.el.hidden = offYear || (!!word && shown === 0);
    // A topic found behind a closed heading is a topic not found.
    item.el.open = (word || year) ? !item.el.hidden : item.open;
    return shown;
  }

  function apply() {
    const total = groups.reduce((n, item) => n + paint(item), 0);
    none.hidden = total > 0;
  }

  groups.forEach((item) => item.lists.forEach((list) => {
    list.more.addEventListener("click", () => {
      list.expanded = true;
      apply();
    });
  }));

  input.addEventListener("input", apply);
  pills.forEach((pill) => pill.addEventListener("click", () => {
    year = pill.dataset.year;
    pills.forEach((p) => p.classList.toggle("sel", p === pill));
    apply();
  }));

  apply();
})();
