// Ambient particle field + 3D tilt for cards. Pure canvas/CSS — no dependencies.

(function () {
  const canvas = document.getElementById("particles");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let w, h, dots;
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    w = window.innerWidth;
    h = window.innerHeight;
    // Without the pixel-ratio scale the dots render at 1x and blur on phones and
    // retina screens; the drawing code below still works in CSS pixels.
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.min(90, Math.floor((w * h) / 16000));
    dots = Array.from({ length: n }, () => ({
      x: Math.random() * w,
      y: Math.random() * h,
      r: Math.random() * 1.8 + 0.4,
      vx: (Math.random() - 0.5) * 0.25,
      vy: (Math.random() - 0.5) * 0.25,
      a: Math.random() * 0.5 + 0.15,
    }));
  }

  function frame() {
    ctx.clearRect(0, 0, w, h);
    for (const d of dots) {
      d.x += d.vx; d.y += d.vy;
      if (d.x < 0) d.x = w; if (d.x > w) d.x = 0;
      if (d.y < 0) d.y = h; if (d.y > h) d.y = 0;
      ctx.beginPath();
      ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(125,211,252," + d.a + ")";
      ctx.fill();
    }
    // faint connecting lines for a constellation feel
    for (let i = 0; i < dots.length; i++) {
      for (let j = i + 1; j < dots.length; j++) {
        const dx = dots[i].x - dots[j].x, dy = dots[i].y - dots[j].y;
        const dist = dx * dx + dy * dy;
        if (dist < 11000) {
          ctx.beginPath();
          ctx.moveTo(dots[i].x, dots[i].y);
          ctx.lineTo(dots[j].x, dots[j].y);
          ctx.strokeStyle = "rgba(125,211,252," + (0.07 * (1 - dist / 11000)) + ")";
          ctx.lineWidth = 0.6;
          ctx.stroke();
        }
      }
    }
    requestAnimationFrame(frame);
  }

  resize();
  window.addEventListener("resize", resize);
  if (!REDUCED) frame();
})();

// Pointer-driven 3D tilt on any .subject-card
(function () {
  const cards = document.querySelectorAll(".subject-card");
  cards.forEach((card) => {
    card.addEventListener("pointermove", (e) => {
      const r = card.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width - 0.5;
      const py = (e.clientY - r.top) / r.height - 0.5;
      card.style.transform =
        "rotateY(" + (px * 12).toFixed(2) + "deg) rotateX(" + (-py * 12).toFixed(2) +
        "deg) translateZ(6px)";
    });
    card.addEventListener("pointerleave", () => { card.style.transform = ""; });
  });
})();
