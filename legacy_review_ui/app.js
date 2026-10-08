const requestedCategory = new URLSearchParams(location.search).get('category');
const dedicatedCategory = new URLSearchParams(location.search).get('dedicated');
const validCategories = ['bad_product', 'bad_movement', 'nudity'];
const state = { category: validCategories.includes(requestedCategory) ? requestedCategory : 'bad_product', status: 'all', items: [], index: 0, counts: {}, remoteTotal: 0 };
const $ = id => document.getElementById(id);
const filters = ['all', 'unreviewed', 'approved', 'rejected'];
const esc = value => String(value || '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

if (validCategories.includes(dedicatedCategory)) {
  document.title = `${dedicatedCategory.replaceAll('_', ' ')} review`;
  const tabs = document.querySelector('.tabs');
  if (tabs) tabs.hidden = true;
  const heading = document.querySelector('h1');
  if (heading) heading.textContent = dedicatedCategory.replaceAll('_', ' ').toUpperCase();
}

function renderFilters() {
  $('filters').innerHTML = filters.map(name => `<button class="${state.status === name ? 'active' : ''}" data-status="${name}">${name} · ${state.counts[name] || 0}</button>`).join('');
  document.querySelectorAll('[data-status]').forEach(button => button.onclick = () => { state.status = button.dataset.status; state.index = 0; load(); });
}

async function load() {
  const params = new URLSearchParams({ category: state.category, status: state.status, search: $('search').value });
  if (state.category !== 'nudity') params.set('index', state.index);
  const response = await fetch(`/api/items?${params}`);
  const data = await response.json();
  state.items = data.items; state.counts = data.counts;
  state.remoteTotal = data.total || 0;
  document.querySelectorAll('[data-category]').forEach(button => button.classList.toggle('active', button.dataset.category === state.category));
  $('decision-help').innerHTML = state.category === 'bad_movement'
    ? '<strong>Approve</strong> only for unnatural body motion, frozen movement, distorted limbs, or abrupt body motion. Normal camera cuts and close-ups must be rejected.'
    : '<strong>Approve</strong> = keep this item in the selected dataset. <strong>Reject</strong> = remove it from that dataset.';
  const pageTotal = state.category !== 'nudity' ? state.remoteTotal : state.items.length;
  state.index = Math.min(state.index, Math.max(0, pageTotal - 1));
  renderFilters(); render();
}

function card(item, absoluteIndex) {
  const nudity = item.category === 'nudity';
  const pageTotal = state.category !== 'nudity' ? state.remoteTotal : state.items.length;
  const referenceUrls = Array.isArray(item.image_urls) && item.image_urls.length
    ? [...new Set(item.image_urls)]
    : (item.image_url ? [item.image_url] : []);
  const image = referenceUrls.length
    ? `<div class="reference-gallery">${referenceUrls.map((url, index) => `<img src="${esc(url)}" alt="Product reference ${index + 1} of ${referenceUrls.length}" loading="lazy">`).join('')}</div>`
    : `<div class="missing">Reference image unavailable</div>`;
  const video = item.video_available
    ? `<div class="video-frame"><video src="${esc(item.video_url)}" controls loop playsinline muted autoplay preload="auto"></video></div>`
    : `<div class="missing">Video unavailable—the artifact service has not delivered this file yet.</div>`;
  return `<article class="review-card ${esc(item.decision)}" data-id="${esc(item.id)}">
    <div class="card-reviewbar">
      <strong>${absoluteIndex + 1} / ${pageTotal}</strong>
      <div>
        <button class="decision reject compact" data-decision="rejected">Reject <kbd>N</kbd></button>
        <button class="reset compact" data-decision="unreviewed">Clear</button>
        <button class="decision approve compact" data-decision="approved">Approve <kbd>Y</kbd></button>
      </div>
    </div>
    <div class="media-grid ${nudity ? 'video-only' : ''}">
      ${nudity ? '' : `<figure class="media-panel"><figcaption>Product references (${referenceUrls.length})</figcaption><div class="media-stage">${image}</div></figure>`}
      <figure class="media-panel"><figcaption>Generated video</figcaption><div class="media-stage">${video}</div></figure>
    </div>
    <div class="details">
      <div><span class="counter">${absoluteIndex + 1} / ${pageTotal}</span><span class="source-label">${esc(item.source_label)}</span></div>
      <h2 title="${esc(item.title)}">${esc(item.title)}</h2>
      <dl>
        <div><dt>Generation / artifact ID</dt><dd class="id-value">${esc(item.artifact_id)}</dd></div>
        <div><dt>Item ID</dt><dd>${esc(item.item_id)}</dd></div>
        <div><dt>FSN ID</dt><dd>${esc(item.fsn_id)}</dd></div>
        <div><dt>Product ID</dt><dd>${esc(item.product_id)}</dd></div>
        <div><dt>Category</dt><dd>${esc(item.product_category)}</dd></div>
        ${item.exposure_flags?.length ? `<div><dt>Safety flags</dt><dd class="exposure-value">${item.exposure_flags.map(esc).join(' · ')}</dd></div>` : ''}
        ${item.workflow_id ? `<div><dt>Workflow</dt><dd>${esc(item.workflow_id)}</dd></div>` : ''}
      </dl>
      <textarea data-note placeholder="Optional review note">${item.note ? esc(item.note) : ''}</textarea>
    </div>
  </article>`;
}

function render() {
  const reviewed = (state.counts.approved || 0) + (state.counts.rejected || 0);
  const total = state.category !== 'nudity' ? state.remoteTotal : (state.counts.all || 0);
  $('progress-label').textContent = `${reviewed} of ${total} reviewed`;
  $('progress-bar').style.width = `${total ? reviewed / total * 100 : 0}%`;
  const available = state.items.filter(item => item.video_available).length;
  $('available-label').textContent = state.category !== 'nudity' ? '· Videos stream from S3' : `· ${available} playable videos in this view`;
  if (!state.items.length) { $('review-grid').innerHTML = ''; $('empty').hidden = false; $('empty').textContent = 'No items match this filter.'; $('pager').hidden = true; return; }
  $('empty').hidden = true; $('pager').hidden = false;
  const item = state.category === 'nudity' ? state.items[state.index] : state.items[0];
  $('review-grid').innerHTML = card(item, state.index);
  const video = $('review-grid').querySelector('video');
  if (video) video.play().catch(() => {});
  $('page-label').textContent = `Video ${state.index + 1} of ${total}`;
  $('previous').disabled = state.index === 0;
  $('next').disabled = state.index >= total - 1;
  document.querySelectorAll('[data-decision]').forEach(button => button.onclick = () => decide(button.closest('.review-card'), button.dataset.decision));
}

async function decide(cardElement, decision) {
  const item = state.items.find(candidate => candidate.id === cardElement.dataset.id);
  const note = cardElement.querySelector('[data-note]').value;
  const response = await fetch('/api/review', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ item_id:item.id, decision, note }) });
  if (!response.ok) { toast('Could not save decision'); return; }
  const itemTotal = state.category !== 'nudity' ? state.remoteTotal : state.items.length;
  if (decision !== 'unreviewed' && state.status === 'all' && state.index < itemTotal - 1) state.index += 1;
  toast(decision === 'unreviewed' ? 'Decision cleared' : `Marked ${decision}`);
  await load();
}

function changePage(delta) {
  const total = state.category !== 'nudity' ? state.remoteTotal : state.items.length;
  const nextIndex = Math.max(0, Math.min(total - 1, state.index + delta));
  if (nextIndex === state.index) return;
  state.index = nextIndex;
  if (state.category !== 'nudity') load(); else render();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
function toast(message) { $('toast').textContent = message; $('toast').classList.add('show'); clearTimeout(window.toastTimer); window.toastTimer = setTimeout(() => $('toast').classList.remove('show'), 1200); }

document.querySelectorAll('[data-category]').forEach(button => button.onclick = () => {
  document.querySelector('.tab.active').classList.remove('active'); button.classList.add('active');
  state.category = button.dataset.category; state.status = 'all'; state.index = 0; load();
  history.replaceState(null, '', `?category=${state.category}`);
});
$('previous').onclick = () => changePage(-1); $('next').onclick = () => changePage(1);
let searchTimer; $('search').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.index = 0; load(); }, 220); };
document.addEventListener('keydown', event => {
  if (event.target.matches('input:not(#reviewer),textarea,select') || event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === 'ArrowLeft') { event.preventDefault(); changePage(-1); }
  if (event.key === 'ArrowRight') { event.preventDefault(); changePage(1); }
  if (event.key.toLowerCase() === 'y' || event.key.toLowerCase() === 'n') {
    const current = document.querySelector('.review-card');
    if (current) {
      event.preventDefault();
      decide(current, event.key.toLowerCase() === 'y' ? 'approved' : 'rejected');
    }
  }
});
load();
