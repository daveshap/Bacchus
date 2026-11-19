// The browser/tool contract for canvas films: the scenes must match film.json's composition, in order.
(function () {
  const api=window.__film;
  if(!api) { if(location.hash.includes('render')) throw new Error('Player did not expose the shared film contract.'); return; }
  api.engine='canvas'; api.profile=FILM_META.profile || 'legacy-canvas';
  const ready=api.ready;
  api.ready=Promise.resolve(ready).then(()=>{
    const expected=FILM_META.composition?.scene_ids;
    if(expected && !FILM_META.partOnly) {
      const scenes=api.scenes(), actual=scenes.map(s=>s.id);
      if(JSON.stringify(actual)!==JSON.stringify(expected)) throw new Error('Scene IDs/order differ from the film plan.');
    }
    return true;
  }).catch(error=>{window.__film_error=String(error);throw error;});
})();
