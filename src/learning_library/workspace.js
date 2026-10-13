// Detail views only. Execution and retry decisions remain in Python.
const workbenchDetails = (() => {
  const el = id => document.getElementById(id);
  let run = null, view = 'source', locked = false;
  function fromHash() {
    if (['#translation-panel', '#processing-panel', '#execution-details'].includes(location.hash)) return 'translation';
    return location.hash === '#original-review' ? 'review' : 'source';
  }
  function apply() {
    if (!run) return;
    for (const button of document.querySelectorAll('[data-detail-view]')) {
      button.setAttribute('aria-pressed', String(button.dataset.detailView === view));
      button.disabled = locked;
    }
    el('capture-pane').hidden = view !== 'source';
    el('source-pane').hidden = view !== 'source';
    el('execution-details').hidden = view !== 'translation';
    el('processing-panel').hidden = view !== 'translation' || !run.review.available;
    originalReview.show(run, view === 'review', locked);
    el('open-reading').href = '/read/' + run.id;
    el('open-reading').hidden = !run.review.available;
    el('write-note').href = '/read/' + run.id + '#curation-panel';
    el('write-note').hidden = !run.review.available;
    el('translation-reading').hidden = !run.review.available;
    el('translation-reading').href = '/read/' + run.id + '?mode=parallel';
  }
  for (const button of document.querySelectorAll('[data-detail-view]')) {
    button.onclick = () => {
      view = button.dataset.detailView;
      const hash = {source: '#saved-original', translation: '#translation-panel', review: '#original-review'}[view];
      history.replaceState(null, '', '?run=' + run.id + hash);
      apply();
    };
  }
  addEventListener('hashchange', () => {view = fromHash(); apply();});
  document.addEventListener('processing-updated', event => {
    if (event.detail.state.task.pipeline_id === run?.id) apply();
  });
  return {
    render(value, busy) {
      if (run?.id !== value.id) view = fromHash();
      run = value; locked = busy; apply();
    }
  };
})();
