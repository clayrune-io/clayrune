// Image coordinates stay in natural pixels. One transform handles every zoom
// path, including rotation; the screen point under the cursor stays stationary.
export function imageGestures(stage, image, onChange, navigate) {
  let width = 0, height = 0, scale = 1, angle = 0, x = 0, y = 0, fitted = true;
  let touch = null, mouse = null, lastTap = null;
  const bounds = () => stage.getBoundingClientRect();
  const rotated = () => angle % 180 ? [height, width] : [width, height];
  const fitScale = () => {
    const [w, h] = rotated();
    return Math.min(1, (stage.clientWidth - 32) / (w || 1), (stage.clientHeight - 32) / (h || 1));
  };
  const paint = () => {
    image.style.transform = `translate(-50%, -50%) translate(${x}px, ${y}px) scale(${scale}) rotate(${angle}deg)`;
    stage.classList.toggle('is-zoomed', scale > fitScale() + .001);
    onChange({ scale, angle, fitted });
  };
  const fit = () => { scale = Math.max(.001, fitScale()); x = y = 0; fitted = true; paint(); };
  // `gesture` is a wheel or pinch. Nudging one of those below Fit stays in Fit:
  // a stray scroll over a fitted picture must not freeze the zoom and stop the
  // picture following its frame. Menu, slider and key zooms are deliberate.
  const zoom = (next, cx, cy, gesture = false) => {
    if (!width) return;
    next = Math.max(.01, Math.min(16, next));
    if (gesture && fitted && next <= fitScale() + .001) { fit(); return; }
    const r = bounds();
    const px = (cx ?? r.left + r.width / 2) - r.left - r.width / 2;
    const py = (cy ?? r.top + r.height / 2) - r.top - r.height / 2;
    const ratio = next / scale;
    x = px - (px - x) * ratio;
    y = py - (py - y) * ratio;
    scale = next; fitted = false; paint();
  };
  const toggle = (cx, cy) => fitted ? zoom(1, cx, cy) : fit();
  stage.addEventListener('wheel', e => {
    e.preventDefault();
    zoom(scale * Math.exp(-e.deltaY * (e.deltaMode === 1 ? .035 : .0022)), e.clientX, e.clientY, true);
  }, { passive: false });
  stage.addEventListener('dblclick', e => toggle(e.clientX, e.clientY));
  stage.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    e.preventDefault(); mouse = { cx: e.clientX, cy: e.clientY, x, y };
    stage.classList.add('is-panning');
  });
  const moveMouse = e => {
    if (!mouse) return;
    x = mouse.x + e.clientX - mouse.cx; y = mouse.y + e.clientY - mouse.cy;
    fitted = false; paint();
  };
  const upMouse = () => { mouse = null; stage.classList.remove('is-panning'); };
  document.addEventListener('mousemove', moveMouse);
  document.addEventListener('mouseup', upMouse);
  const midpoint = ts => ({ x: (ts[0].clientX + ts[1].clientX) / 2, y: (ts[0].clientY + ts[1].clientY) / 2 });
  const distance = ts => Math.hypot(ts[0].clientX - ts[1].clientX, ts[0].clientY - ts[1].clientY);
  stage.addEventListener('touchstart', e => {
    if (!e.touches.length || e.touches.length > 2) { touch = null; return; }
    e.preventDefault();
    const first = e.touches[0];
    touch = e.touches.length === 2
      ? { pinch: true, distance: distance(e.touches), center: midpoint(e.touches), moved: true }
      : { cx: first.clientX, cy: first.clientY, x, y, time: Date.now(), fitted, moved: false };
    if (touch.pinch) lastTap = null;
  }, { passive: false });
  stage.addEventListener('touchmove', e => {
    if (!touch) return;
    e.preventDefault();
    if (e.touches.length === 2 && touch.pinch) {
      const center = midpoint(e.touches), d = distance(e.touches);
      zoom(scale * d / (touch.distance || 1), touch.center.x, touch.center.y, true);
      if (!fitted) { x += center.x - touch.center.x; y += center.y - touch.center.y; paint(); }
      touch.distance = d; touch.center = center;
    } else if (e.touches.length === 1 && !touch.pinch) {
      const dx = e.touches[0].clientX - touch.cx, dy = e.touches[0].clientY - touch.cy;
      touch.moved ||= Math.hypot(dx, dy) > 10;
      // At fit, a horizontal swipe changes pictures on release. Zoomed views pan.
      if (!touch.fitted) { x = touch.x + dx; y = touch.y + dy; fitted = false; paint(); }
    }
  }, { passive: false });
  stage.addEventListener('touchend', e => {
    if (!touch) return;
    if (touch.pinch || e.touches.length) { touch = null; return; }
    const t = e.changedTouches[0], dx = t.clientX - touch.cx, dy = t.clientY - touch.cy;
    if (touch.fitted && Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5 && Date.now() - touch.time < 700) {
      navigate(dx < 0 ? 1 : -1); lastTap = null;
    } else if (!touch.moved) {
      if (lastTap && Date.now() - lastTap.time < 300 && Math.hypot(t.clientX - lastTap.x, t.clientY - lastTap.y) < 30) {
        toggle(t.clientX, t.clientY); lastTap = null;
      } else lastTap = { time: Date.now(), x: t.clientX, y: t.clientY };
    }
    touch = null;
  });
  stage.addEventListener('touchcancel', () => { touch = lastTap = null; });
  const observer = new ResizeObserver(() => { if (fitted) fit(); });
  observer.observe(stage);
  return {
    fit, zoom, zoomBy: factor => zoom(scale * factor), toggle,
    rotate: () => { angle = (angle + 90) % 360; fit(); },
    load: (w, h) => { width = w; height = h; angle = 0; fit(); },
    destroy: () => {
      observer.disconnect();
      document.removeEventListener('mousemove', moveMouse);
      document.removeEventListener('mouseup', upMouse);
    },
  };
}
