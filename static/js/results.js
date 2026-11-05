// Polls the background grading job and renders the breakdown when it lands.

(function () {
  const PHASES = [
    "Marking your paper…",
    "Reading each answer like an examiner…",
    "Checking keywords, units and reasoning…",
    "Updating your concept mastery…",
    "Working out what to teach you next…",
  ];

  const marking = document.getElementById("marking");
  const titleEl = document.getElementById("marking-title");
  const mount = document.getElementById("session-breakdown");
  let phase = 0;

  const rotate = setInterval(() => {
    phase = (phase + 1) % PHASES.length;
    titleEl.textContent = PHASES[phase];
  }, 3200);

  function finish(result) {
    clearInterval(rotate);
    marking.style.display = "none";
    if (result && result.error) {
      mount.innerHTML =
        '<div class="error-box">Marking failed: ' + result.error +
        ' <a href="/analytics">Back to analytics</a></div>';
      return;
    }
    window.renderBreakdown(result || {}, mount);
  }

  async function poll() {
    try {
      const resp = await fetch(`/api/results/${JOB_ID}`);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "Couldn't load your results");
      if (data.done) { finish(data.result); return; }
    } catch (err) {
      clearInterval(rotate);
      marking.style.display = "none";
      mount.innerHTML = '<div class="error-box">' + err.message + '</div>';
      return;
    }
    setTimeout(poll, 2000);
  }

  poll();
})();
