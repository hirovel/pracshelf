/* Shared presentation behavior. No article data or workflow state lives here. */
const readingUI = (() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  function change(element, update) {
    update();
    if (!reduced.matches && element?.animate) {
      element.getAnimations().forEach(animation => animation.cancel());
      element.animate([{opacity:.35},{opacity:1}], {duration:200,easing:'ease-out'});
    }
  }

  // Use native summary activation (including keyboard), animating height only.
  document.addEventListener('click', event => {
    const summary = event.target.closest('summary');
    if (!summary || reduced.matches || !summary.parentElement.animate) return;
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
  return {change};
})();
