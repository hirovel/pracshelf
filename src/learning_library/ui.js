/* Shared presentation behavior. No article data or workflow state lives here. */
const readingUI = (() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const key = 'workspace-motion';
  let preference = 'system';
  try { if (localStorage.getItem(key) === 'reduce') preference = 'reduce'; } catch {}
  const reduceMotion = () => preference === 'reduce' || reduced.matches;
  function sync() {
    document.documentElement.dataset.motion = reduceMotion() ? 'reduce' : 'standard';
    if (reduceMotion()) document.getAnimations().forEach(animation => animation.cancel());
    dispatchEvent(new Event('reading:motion'));
  }
  function setMotionPreference(value) {
    if (!['system', 'reduce'].includes(value)) throw new TypeError('未知的动效偏好。');
    preference = value;
    sync();
    try { localStorage.setItem(key, value); return true; } catch { return false; }
  }
  reduced.addEventListener('change', sync);
  addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = event.newValue === 'reduce' ? 'reduce' : 'system';
    sync();
  });
  sync();
  function change(element, update) {
    update();
    if (!reduceMotion() && element?.animate) {
      element.getAnimations().forEach(animation => animation.cancel());
      element.animate([{opacity:.35},{opacity:1}], {duration:200,easing:'ease-out'});
    }
  }

  // Use native summary activation (including keyboard), animating height only.
  document.addEventListener('click', event => {
    if (!document.body.classList.contains('terminal-app')) return;
    const summary = event.target.closest('summary');
    if (!summary || reduceMotion() || !summary.parentElement.animate) return;
    if (event.target.closest('a,button,input,select')) return;
    const details = summary.parentElement;
    // Width-changing capture and task panels use native disclosure to avoid layout jumps.
    if (details.classList.contains('quick-add') || details.closest('.task-sidebar')) return;
    event.preventDefault();
    if (details.dataset.animating) return;
    const start = details.getBoundingClientRect().height;
    const opening = !details.open;
    if (opening) details.open = true;
    const end = opening ? details.getBoundingClientRect().height : summary.getBoundingClientRect().height;
    details.dataset.animating = 'true';
    details.style.overflow = 'hidden';
    const animation = details.animate([{height:start+'px'},{height:end+'px'}], {duration:220,easing:'cubic-bezier(.2,.7,.2,1)'});
    animation.finished.catch(()=>{}).finally(() => {
      if (!opening) details.open = false;
      details.style.overflow = '';
      delete details.dataset.animating;
    });
  });
  return {change, reduceMotion, motionPreference: () => preference, setMotionPreference};
})();
