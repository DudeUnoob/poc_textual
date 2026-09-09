/* Each viewer owns its transform; shortcuts never interfere with review fields. */
(() => {
  document.querySelectorAll('[data-scan-viewer]').forEach(root => {
    const viewport = root.querySelector('.scan-viewport');
    const img = viewport.querySelector('img');
    const output = root.querySelector('output');
    let scale = 1, x = 0, y = 0, fitted = true;
    const pointers = new Map();
    const paint = () => {
      const w = img.naturalWidth * scale, h = img.naturalHeight * scale;
      x = w <= viewport.clientWidth ? (viewport.clientWidth - w) / 2 : Math.max(viewport.clientWidth - w, Math.min(0, x));
      y = h <= viewport.clientHeight ? (viewport.clientHeight - h) / 2 : Math.max(viewport.clientHeight - h, Math.min(0, y));
      img.style.transform = `translate(${x}px,${y}px) scale(${scale})`;
      output.value = `${Math.round(scale * 100)}%`;
    };
    const fit = () => {
      if (!img.naturalWidth || !viewport.clientWidth) return;
      scale = Math.min(viewport.clientWidth / img.naturalWidth, viewport.clientHeight / img.naturalHeight);
      x = y = 0; fitted = true; paint();
    };
    const zoom = (factor, cx = viewport.clientWidth / 2, cy = viewport.clientHeight / 2) => {
      const next = Math.max(0.01, Math.min(8, scale * factor));
      x = cx - (cx - x) * next / scale; y = cy - (cy - y) * next / scale;
      scale = next; fitted = false; paint();
    };
    const fullscreen = async () => {
      try {
        if (document.fullscreenElement === root) await document.exitFullscreen();
        else if (root.requestFullscreen) await root.requestFullscreen();
        else root.querySelector('a').click();
      } catch { output.value = 'Use Open image'; }
    };
    const action = name => {
      if (name === 'in') zoom(1.25);
      if (name === 'out') zoom(0.8);
      if (name === 'fit') fit();
      if (name === 'reset') { scale = 1; x = y = 0; fitted = false; paint(); }
      if (name === 'fullscreen') fullscreen();
    };
    root.querySelectorAll('[data-viewer-action]').forEach(button => button.addEventListener('click', () => action(button.dataset.viewerAction)));
    viewport.addEventListener('keydown', event => {
      const names = {'+':'in', '=':'in', '-':'out', '0':'fit', 'f':'fullscreen', 'F':'fullscreen'};
      if (names[event.key]) action(names[event.key]);
      else if (event.key.startsWith('Arrow')) {
        x += event.key === 'ArrowLeft' ? 50 : event.key === 'ArrowRight' ? -50 : 0;
        y += event.key === 'ArrowUp' ? 50 : event.key === 'ArrowDown' ? -50 : 0;
        fitted = false; paint();
      } else return;
      event.preventDefault(); event.stopPropagation();
    });
    viewport.addEventListener('wheel', event => {
      // Ordinary scrolling still scrolls the review form; modified wheel zooms.
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const box = viewport.getBoundingClientRect();
      zoom(Math.exp(-event.deltaY * 0.002), event.clientX - box.left, event.clientY - box.top);
    }, {passive:false});
    viewport.addEventListener('pointerdown', event => {
      viewport.focus(); viewport.setPointerCapture(event.pointerId);
      pointers.set(event.pointerId, {x:event.clientX,y:event.clientY});
      viewport.classList.add('dragging');
    });
    viewport.addEventListener('pointermove', event => {
      const previous = pointers.get(event.pointerId);
      if (!previous) return;
      if (pointers.size === 2) {
        const other = [...pointers.entries()].find(([id]) => id !== event.pointerId)[1];
        const before = Math.hypot(previous.x-other.x, previous.y-other.y);
        const after = Math.hypot(event.clientX-other.x, event.clientY-other.y);
        const box = viewport.getBoundingClientRect();
        if (before > 0) zoom(after/before, (event.clientX+other.x)/2-box.left, (event.clientY+other.y)/2-box.top);
      } else { x += event.clientX-previous.x; y += event.clientY-previous.y; fitted=false; paint(); }
      pointers.set(event.pointerId, {x:event.clientX,y:event.clientY});
    });
    ['pointerup','pointercancel','lostpointercapture'].forEach(name => viewport.addEventListener(name, event => {
      pointers.delete(event.pointerId); if (!pointers.size) viewport.classList.remove('dragging');
    }));
    img.addEventListener('load', fit);
    img.addEventListener('error', () => { output.value = 'Image unavailable'; });
    new ResizeObserver(() => fitted ? fit() : paint()).observe(viewport);
    if (img.complete) fit();
  });
})();
