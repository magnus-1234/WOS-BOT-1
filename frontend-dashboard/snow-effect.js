(() => {
  // Lightweight + slow snow for homepage only.
  // (Even if this script is accidentally included elsewhere, bail out.)
  const path = (location.pathname || "").toLowerCase();
  if (!path.endsWith("/index.html") && path !== "/" && !path.endsWith("/frontend-dashboard/") && !path.endsWith("/frontend-dashboard/index.html")) {
    return;
  }

  const FLAKE_COUNT = 95;
  const MIN_SIZE = 1;
  const MAX_SIZE = 3.2;
  const MIN_SPEED = 0.06;
  const MAX_SPEED = 0.35;
  const WIND_STRENGTH = 0.14;

  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  const flakes = [];

  if (!ctx) return;

  canvas.setAttribute("aria-hidden", "true");
  canvas.style.position = "fixed";
  canvas.style.top = "0";
  canvas.style.left = "0";
  canvas.style.width = "100vw";
  canvas.style.height = "100vh";
  canvas.style.pointerEvents = "none";
  canvas.style.zIndex = "2";
  canvas.style.opacity = "0.55";

  let width = window.innerWidth;
  let height = window.innerHeight;

  function rand(min, max) {
    return Math.random() * (max - min) + min;
  }

  function createFlake(initialY) {
    const size = rand(MIN_SIZE, MAX_SIZE);
    return {
      x: rand(0, width),
      y: initialY ?? rand(-height, 0),
      size,
      speedY: rand(MIN_SPEED, MAX_SPEED) * (size / MAX_SIZE + 0.5),
      drift: rand(-WIND_STRENGTH, WIND_STRENGTH),
      wobble: rand(0.002, 0.01),
      phase: rand(0, Math.PI * 2)
    };
  }

  function resize() {
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = width;
    canvas.height = height;
  }

  function populate() {
    flakes.length = 0;
    for (let i = 0; i < FLAKE_COUNT; i += 1) {
      flakes.push(createFlake());
    }
  }

  function update() {
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "rgba(255, 255, 255, 0.9)";

    for (let i = 0; i < flakes.length; i += 1) {
      const flake = flakes[i];
      flake.y += flake.speedY;
      flake.x += flake.drift + Math.sin((flake.y + flake.phase) * flake.wobble);

      if (flake.y > height + 10 || flake.x < -20 || flake.x > width + 20) {
        flakes[i] = createFlake(-10);
        continue;
      }

      ctx.beginPath();
      ctx.arc(flake.x, flake.y, flake.size, 0, Math.PI * 2);
      ctx.fill();
    }

    requestAnimationFrame(update);
  }

  function mount() {
    if (!document.body) return;
    document.body.appendChild(canvas);
    resize();
    populate();
    update();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount, { once: true });
  } else {
    mount();
  }

  window.addEventListener("resize", resize);
})();
