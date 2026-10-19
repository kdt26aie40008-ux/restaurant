// 注文用画面（トップ → メニュー → 確認 → 完了 → 追加注文 / 会計、このテーブルの注文）。
// 全テーブルの注文履歴はスタッフ用の店舗管理画面（/admin）にあり、この画面では来店中のお客様の分だけを見せる。
// 1組のお客様の注文は「伝票」にまとめ、追加注文も同じ伝票に入る。会計は伝票単位。
// 画面の状態は sessionStorage に保存し、再読み込みしても入力中のカートや伝票を失わない。

import { api, onApiError } from './api.js';
import { storeLoginCard } from './auth-ui.js';
import { clear, confirmDialog, errorBox, formatDateTime, h, toast, withBusy } from './dom.js';
import {
  ALLERGENS, MAX_QUANTITY, allergenConflicts, allergenNames, cartCount, cartLines, cartTotal, changeQuantity, filterByCategory, formatYen, getQuantity,
  imageSrc, newRequestKey, removeLine, searchProducts, setQuantity, splitAmount, summaryCount, validatePartySize,
} from './logic.js';

const SESSION_KEY = 'restaurant.session.v2';
const RECOMMEND_TAB = 'recommend';
const QUICK_PARTY_SIZES = [1, 2, 3, 4, 5, 6];
const AGE_OPTIONS = [['', '選択しない'], ['child', 'お子さま'], ['youth', '10〜20代'], ['adult', '30〜50代'], ['senior', '60代以上']];
const TASTE_OPTIONS = [['likes_spicy', '辛いものが好き'], ['likes_sweet', '甘いものが好き'], ['prefers_light', '軽めがいい']];

const state = {
  settings: { store_name: 'メニューオーダー', max_party_size: 30 },
  loggedIn: false,
  noLogin: false,
  categories: [],
  products: [],
  productsById: new Map(),
  menuLoaded: false,
  menuError: null,
  activeTab: RECOMMEND_TAB,
  search: '',
  reco: { items: [], mode: 'popular', error: null, loading: false },
  cartOpen: false,
  submitting: false,
};

const viewRoot = document.getElementById('view');
const storeNameEl = document.getElementById('store-name');
const navEl = document.getElementById('header-nav');

// ---------- セッション保存 ----------
// visitId: いま注文を受けている伝票（最初の注文を確定するまでは null）
// orderId: 直前に確定した注文の注文番号（完了画面で使う）

function defaultSession() {
  return {
    view: 'top', partySize: null, tableLabel: '', visitId: null, visitTotal: 0, cart: [], prefs: {}, avoidAllergens: [],
    requestKey: null, orderId: null, billVisitId: null, returnView: 'top',
  };
}

function loadSession() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null');
    return saved && typeof saved === 'object' ? { ...defaultSession(), ...saved } : defaultSession();
  } catch (error) {
    console.error('保存されていた画面の状態を読み込めませんでした', error);
    return defaultSession();
  }
}

let session = loadSession();
let storageWarningShown = false;

function saveSession() {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  } catch (error) {
    console.error('画面の状態を保存できませんでした', error);
    if (!storageWarningShown) {
      storageWarningShown = true;
      toast('この端末では入力途中の内容を一時保存できません。再読み込みするとカートが空になります。', { type: 'error', duration: 8000 });
    }
  }
}

function go(view, extra = {}) {
  Object.assign(session, extra, { view });
  saveSession();
  render();
  window.scrollTo({ top: 0 });
}

/** 伝票（来店中のお客様）に追加注文する状態にしてメニューを開く。 */
function startAdditionalOrder(visit) {
  go('menu', {
    visitId: visit.id, partySize: visit.party_size, tableLabel: visit.table_label, visitTotal: visit.total,
    cart: [], requestKey: null, orderId: null,
  });
}

// ---------- データ読み込み ----------

function setProducts(products) {
  state.products = products;
  state.productsById = new Map(products.map((p) => [p.id, p]));
}

async function loadMenu() {
  try {
    const [categories, products] = await Promise.all([api('GET', '/api/categories'), api('GET', '/api/products')]);
    state.categories = categories.categories;
    setProducts(products.products);
    state.menuLoaded = true;
    state.menuError = null;
  } catch (error) {
    state.menuError = error;
  }
}

function preferencesPayload() {
  const prefs = session.prefs || {};
  return {
    age_group: prefs.age_group || null,
    likes_spicy: Boolean(prefs.likes_spicy),
    likes_sweet: Boolean(prefs.likes_sweet),
    prefers_light: Boolean(prefs.prefers_light),
    preferred_category_id: prefs.preferred_category_id || null,
    avoid_allergens: session.avoidAllergens || [],
  };
}

async function loadRecommendations() {
  state.reco.loading = true;
  renderMenuContent();
  try {
    const data = await api('POST', '/api/recommendations', { json: { preferences: preferencesPayload() } });
    state.reco = { items: data.items, mode: data.mode, error: null, loading: false };
  } catch (error) {
    // おすすめが使えなくても通常メニューは使えるようにする。
    console.error('おすすめの取得に失敗しました', error);
    state.reco = { items: [], mode: 'popular', error, loading: false };
  }
  renderMenuContent();
}

// ---------- 共通部品 ----------

function renderHeader() {
  storeNameEl.textContent = state.settings.store_name;
  document.title = `${state.settings.store_name} | メニューオーダー`;
  const buttons = [];
  if (session.visitId && session.view !== 'tableHistory') {
    buttons.push(h('button', { type: 'button', class: 'btn btn-light', onclick: () => go('tableHistory', { returnView: session.view }) }, 'このテーブルの注文'));
  }
  if (session.view !== 'top') {
    buttons.push(h('button', { type: 'button', class: 'btn btn-light', onclick: startNextCustomer }, '次のお客様'));
  }
  if (!state.noLogin) buttons.push(h('button', { type: 'button', class: 'btn btn-light', onclick: logout }, 'ログアウト'));
  clear(navEl).append(...buttons);
}

async function startNextCustomer() {
  if (session.cart.length > 0) {
    const ok = await confirmDialog({
      title: '次のお客様の受付に進みますか？',
      body: h('p', {}, 'まだ注文を確定していない商品がカートにあります。進むとカートの内容は消えます。'),
      okLabel: 'カートを消して進む',
      cancelLabel: '注文を続ける',
      danger: true,
    });
    if (!ok) return;
  }
  session = defaultSession();
  state.activeTab = RECOMMEND_TAB;
  state.search = '';
  state.cartOpen = false;
  go('top');
}

function guestLabel(tableLabel, partySize) {
  return [tableLabel || null, partySize ? `${partySize}人` : null].filter(Boolean).join('・');
}

function orderStatusBadge(order) {
  return h('span', { class: `badge status-${order.order_status}` }, order.order_status_label);
}

function paymentBadge(visit) {
  return h('span', { class: `badge payment-${visit.payment_status}` }, visit.payment_status_label);
}

function itemsTable(items) {
  return h('div', { class: 'table-wrap' },
    h('table', { class: 'items-table' },
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, '商品'), h('th', { scope: 'col', class: 'num' }, '単価'),
        h('th', { scope: 'col', class: 'num' }, '数量'), h('th', { scope: 'col', class: 'num' }, '小計'))),
      h('tbody', {}, items.map((item) => h('tr', {},
        h('td', {}, item.product_name),
        h('td', { class: 'num' }, formatYen(item.unit_price)),
        h('td', { class: 'num' }, `${item.quantity}`),
        h('td', { class: 'num' }, formatYen(item.subtotal)))))));
}

/** 伝票の各注文（1回目・追加1回目…）を折りたたみで表示する。 */
function orderBreakdown(visit) {
  return h('div', { class: 'order-breakdown' }, visit.orders.map((order, index) => {
    const label = index === 0 ? '最初の注文' : `追加注文 ${index}`;
    return h('details', { class: `order-entry${order.order_status === 'cancelled' ? ' is-cancelled' : ''}` },
      h('summary', {},
        h('span', { class: 'order-entry-title' }, label),
        h('span', { class: 'order-entry-meta' }, `${formatDateTime(order.ordered_at).slice(11)}・注文番号 ${order.order_number}`),
        orderStatusBadge(order),
        h('strong', { class: 'order-entry-total' }, formatYen(order.total_amount))),
      h('ul', { class: 'plain-list' }, order.items.map((item) => h('li', {},
        h('span', {}, `${item.product_name} × ${item.quantity}`), h('span', {}, formatYen(item.subtotal))))),
      order.cancel_reason ? h('p', { class: 'help' }, `取消理由: ${order.cancel_reason}`) : null);
  }));
}

function loadingBox(text = '読み込み中…') {
  return h('p', { class: 'loading', role: 'status' }, text);
}

function retryBox(error, onRetry) {
  return h('div', { class: 'stack' }, errorBox(error),
    h('button', { type: 'button', class: 'btn btn-primary', onclick: onRetry }, 'もう一度読み込む'));
}

// ---------- トップ画面 ----------

function renderTop() {
  const max = state.settings.max_party_size;
  const input = h('input', {
    id: 'party-size', class: 'party-input', type: 'text', inputmode: 'numeric', autocomplete: 'off',
    value: session.partySize ?? '', 'aria-describedby': 'party-error party-help', maxlength: 6,
  });
  const tableInput = h('input', {
    id: 'table-label', class: 'text-input table-input', type: 'text', autocomplete: 'off', maxlength: 20,
    value: session.tableLabel || '', placeholder: '例: 5番、カウンター',
  });
  const errorArea = h('div', { id: 'party-error', 'aria-live': 'polite' });

  const showError = (result) => {
    input.classList.add('is-invalid');
    input.setAttribute('aria-invalid', 'true');
    clear(errorArea).append(errorBox(result));
  };
  const clearError = () => {
    input.classList.remove('is-invalid');
    input.removeAttribute('aria-invalid');
    clear(errorArea);
  };
  const setValue = (value) => {
    input.value = String(value);
    clearError();
    for (const chip of document.querySelectorAll('.quick-party .chip')) chip.setAttribute('aria-pressed', String(chip.dataset.value === input.value));
  };
  const step = (delta) => {
    const result = validatePartySize(input.value, max);
    setValue(Math.min(max, Math.max(1, (result.ok ? result.value : 0) + delta)));
  };
  const start = (event) => {
    event.preventDefault();
    const result = validatePartySize(input.value, max);
    if (!result.ok) {
      showError(result);
      input.focus();
      return;
    }
    clearError();
    go('menu', { partySize: result.value, tableLabel: tableInput.value.trim(), visitId: null, visitTotal: 0, orderId: null });
  };
  input.addEventListener('input', () => setValue(input.value));

  viewRoot.append(
    h('section', { class: 'top-screen card' },
      h('p', { class: 'eyebrow' }, 'いらっしゃいませ'),
      h('h1', { class: 'top-title' }, state.settings.store_name),
      h('form', { class: 'party-form', novalidate: true, onsubmit: start },
        h('label', { for: 'party-size', class: 'field-label' }, 'ご利用人数'),
        h('div', { class: 'quick-party', role: 'group', 'aria-label': '人数をすぐ選ぶ' },
          QUICK_PARTY_SIZES.filter((n) => n <= max).map((n) => h('button', {
            type: 'button', class: 'chip chip-large', dataset: { value: String(n) },
            'aria-pressed': String(String(session.partySize) === String(n)), onclick: () => setValue(n),
          }, `${n}人`))),
        h('div', { class: 'stepper' },
          h('button', { type: 'button', class: 'btn btn-step', 'aria-label': '1人減らす', onclick: () => step(-1) }, '−'),
          input,
          h('span', { class: 'unit' }, '人'),
          h('button', { type: 'button', class: 'btn btn-step', 'aria-label': '1人増やす', onclick: () => step(1) }, '＋')),
        h('p', { id: 'party-help', class: 'help' }, `1〜${max}人まで。7人以上は数字で入力してください。`),
        errorArea,
        h('label', { for: 'table-label', class: 'field-label' }, 'テーブル番号（任意）'),
        tableInput,
        h('button', { type: 'submit', class: 'btn btn-primary btn-large btn-block' }, '注文を開始'))),
  );
}

// ---------- メニュー画面 ----------

function renderMenu() {
  if (!session.partySize) {
    go('top');
    return;
  }
  if (!state.menuLoaded) {
    viewRoot.append(state.menuError
      ? retryBox(state.menuError, async () => { state.menuError = null; render(); await loadMenu(); render(); })
      : loadingBox('メニューを読み込み中…'));
    if (!state.menuError) loadMenu().then(render);
    return;
  }

  const tabs = [{ id: RECOMMEND_TAB, name: '★ おすすめ' }, ...state.categories];
  if (!tabs.some((tab) => tab.id === state.activeTab)) state.activeTab = RECOMMEND_TAB;
  const tabList = h('div', { class: 'category-tabs', role: 'tablist', 'aria-label': 'カテゴリ' },
    tabs.map((tab) => h('button', {
      type: 'button',
      role: 'tab',
      class: 'tab',
      id: `tab-${tab.id}`,
      'aria-selected': String(!state.search && tab.id === state.activeTab),
      'aria-controls': 'menu-content',
      onclick: () => selectTab(tab.id),
    }, tab.name)));

  const searchInput = h('input', {
    type: 'search', class: 'text-input search-input', value: state.search, placeholder: '商品名で探す（ひらがなでもOK）',
    'aria-label': '商品名で探す', maxlength: 30,
  });
  searchInput.addEventListener('input', () => {
    state.search = searchInput.value;
    for (const tab of document.querySelectorAll('.category-tabs .tab')) {
      tab.setAttribute('aria-selected', String(!state.search && tab.id === `tab-${state.activeTab}`));
    }
    renderMenuContent();
  });

  const guest = guestLabel(session.tableLabel, session.partySize);
  const allergyPanel = allergyChooser();
  viewRoot.append(
    h('div', { class: 'menu-layout' },
      h('div', { class: 'menu-main' },
        h('div', { class: `session-bar${session.visitId ? ' is-additional' : ''}` },
          h('div', { class: 'session-info' },
            session.visitId
              ? [h('span', { class: 'badge badge-additional' }, '追加注文'),
                h('span', {}, guest, `・これまでの注文 `, h('strong', {}, formatYen(session.visitTotal)))]
              : h('span', {}, 'お客様 ', h('strong', {}, guest))),
          session.visitId
            ? h('button', { type: 'button', class: 'btn btn-small btn-light',
              onclick: () => go('bill', { billVisitId: session.visitId, returnView: 'menu' }) }, '会計へ')
            : h('button', { type: 'button', class: 'btn btn-small btn-light', onclick: () => go('top') }, '人数・テーブルを変更')),
        allergyPanel,
        searchInput,
        tabList,
        h('div', { id: 'menu-content', role: 'tabpanel', 'aria-live': 'polite' })),
      h('aside', { id: 'cart-panel', class: 'cart-panel', 'aria-label': 'カート' })),
  );
  renderMenuContent();
  renderCart();
  if (state.activeTab === RECOMMEND_TAB && !state.reco.loading && !state.reco.items.length && !state.reco.error) {
    loadRecommendations();
  }
}

function allergyChooser() {
  const selected = session.avoidAllergens || [];
  const toggle = (code) => {
    session.avoidAllergens = selected.includes(code) ? selected.filter((c) => c !== code) : [...selected, code];
    saveSession();
    const open = document.querySelector('.allergy-panel')?.open;
    render();
    const panel = document.querySelector('.allergy-panel');
    if (panel && open) panel.open = true;
    loadRecommendations();
  };
  const summary = selected.length
    ? `アレルギー: ${allergenNames(selected).join('・')} を含む商品は注文できません`
    : 'アレルギーがある方はこちら（選ぶと、含む商品を注文できなくなります）';
  return h('details', { class: `allergy-panel${selected.length ? ' is-active' : ''}` },
    h('summary', {}, summary),
    h('div', { class: 'pref-body' },
      h('div', { class: 'pref-group', role: 'group', 'aria-label': '避けたいアレルギー' },
        ALLERGENS.map(([code, name]) => h('button', {
          type: 'button', class: 'chip', 'aria-pressed': String(selected.includes(code)), onclick: () => toggle(code),
        }, name))),
      selected.length ? h('button', { type: 'button', class: 'btn btn-light btn-small', onclick: () => { session.avoidAllergens = []; saveSession(); render(); loadRecommendations(); } }, '選択をすべて解除') : null,
      h('p', { class: 'help' }, 'アレルギー情報は店舗が登録した内容です（特定原材料など10品目）。調理中の混入まで完全には防げないため、重いアレルギーのある方は必ずスタッフにご確認ください。選んだ内容は保存しません。')));
}

function selectTab(tabId) {
  state.activeTab = tabId;
  state.search = '';
  const searchInput = document.querySelector('.search-input');
  if (searchInput) searchInput.value = '';
  for (const tab of document.querySelectorAll('.category-tabs .tab')) {
    const selected = tab.id === `tab-${tabId}`;
    tab.setAttribute('aria-selected', String(selected));
    // scrollIntoView はページ全体も縦に動かしてしまうため、タブ列だけを横にスクロールする。
    if (selected) tab.parentElement.scrollTo({ left: tab.offsetLeft - tab.parentElement.clientWidth / 2 + tab.offsetWidth / 2, behavior: 'smooth' });
  }
  renderMenuContent();
  if (tabId === RECOMMEND_TAB) loadRecommendations();
}

function renderMenuContent() {
  const content = document.getElementById('menu-content');
  if (!content) return;
  clear(content);
  if (state.search.trim()) {
    const found = searchProducts(state.products, state.search);
    content.append(
      h('h2', { class: 'section-title' }, `「${state.search.trim()}」の検索結果（${found.length}件）`),
      found.length
        ? h('div', { class: 'product-grid' }, found.map((p) => productCard(p)))
        : h('p', { class: 'empty' }, '見つかりませんでした。言葉を短くするか、カテゴリから探してください。'));
    return;
  }
  if (state.activeTab === RECOMMEND_TAB) {
    content.append(preferencePanel(), recommendationList());
    return;
  }
  const products = filterByCategory(state.products, state.activeTab);
  content.append(products.length
    ? h('div', { class: 'product-grid' }, products.map((p) => productCard(p)))
    : h('p', { class: 'empty' }, 'このカテゴリには商品がありません。'));
}

function preferencePanel() {
  const prefs = session.prefs || {};
  const update = (changes) => {
    session.prefs = { ...prefs, ...changes };
    saveSession();
    loadRecommendations();
  };
  const chip = (label, pressed, onClick) => h('button', {
    type: 'button', class: 'chip', 'aria-pressed': String(pressed), onclick: onClick,
  }, label);

  return h('details', { class: 'pref-panel', open: Boolean(prefs.open) || undefined,
    ontoggle: (event) => { session.prefs = { ...session.prefs, open: event.target.open }; saveSession(); } },
  h('summary', {}, 'お好みを選んでおすすめを変える（任意）'),
  h('div', { class: 'pref-body' },
    h('p', { class: 'help' }, '選ばなくても注文できます。入力内容はおすすめの表示にだけ使い、保存しません。'),
    h('div', { class: 'pref-group', role: 'group', 'aria-label': '年齢層' },
      h('span', { class: 'pref-label' }, '年齢層'),
      AGE_OPTIONS.map(([value, label]) => chip(label, (prefs.age_group || '') === value, () => update({ age_group: value || null })))),
    h('div', { class: 'pref-group', role: 'group', 'aria-label': 'お好み' },
      h('span', { class: 'pref-label' }, 'お好み'),
      TASTE_OPTIONS.map(([key, label]) => chip(label, Boolean(prefs[key]), () => update({ [key]: !prefs[key] })))),
    h('div', { class: 'pref-group', role: 'group', 'aria-label': '好きなカテゴリ' },
      h('span', { class: 'pref-label' }, 'カテゴリ'),
      chip('指定しない', !prefs.preferred_category_id, () => update({ preferred_category_id: null })),
      state.categories.map((c) => chip(c.name, prefs.preferred_category_id === c.id, () => update({ preferred_category_id: c.id }))))));
}

function recommendationList() {
  const { items, mode, error, loading } = state.reco;
  if (loading && !items.length) return loadingBox('おすすめを選んでいます…');
  let entries = items.map((item) => ({ product: state.productsById.get(item.product_id), reasons: item.reasons }))
    .filter((entry) => entry.product);
  const notices = [];
  if (error) {
    notices.push(h('div', { class: 'notice' }, 'おすすめを計算できなかったため、人気の商品を表示しています。各カテゴリからも通常どおり注文できます。'));
    entries = state.products.filter((p) => p.is_available).sort((a, b) => b.popularity_score - a.popularity_score)
      .slice(0, 6).map((product) => ({ product, reasons: ['人気商品'] }));
  }
  const title = mode === 'personalized' ? 'お好みに合わせたおすすめ' : '人気のおすすめ';
  return h('section', { class: 'reco-section' },
    h('h2', { class: 'section-title' }, title),
    notices,
    entries.length
      ? h('div', { class: 'product-grid' }, entries.map((entry) => productCard(entry.product, entry.reasons)))
      : h('p', { class: 'empty' }, 'おすすめできる商品がありません。上のカテゴリから商品をお選びください。'));
}

function productImage(product) {
  const wrap = h('div', { class: 'product-image' });
  const showPlaceholder = () => clear(wrap).append(h('div', { class: 'image-placeholder' }, h('span', {}, '画像準備中')));
  if (!product.image_path) {
    showPlaceholder();
    return wrap;
  }
  const img = h('img', { src: imageSrc(product), alt: '', loading: 'lazy', decoding: 'async' });
  img.addEventListener('error', showPlaceholder, { once: true });
  wrap.append(img);
  return wrap;
}

function productCard(product, reasons = null) {
  const quantity = getQuantity(session.cart, product.id);
  const conflicts = allergenConflicts(product, session.avoidAllergens);
  const available = product.is_available && !conflicts.length;
  const allergyText = product.allergens?.length ? `アレルギー: ${allergenNames(product.allergens).join('・')}` : 'アレルギー表示対象: なし';
  return h('article', { class: `product-card${available ? '' : ' is-unavailable'}${conflicts.length ? ' is-allergen' : ''}${quantity ? ' is-selected' : ''}`, dataset: { productId: product.id } },
    productImage(product),
    h('div', { class: 'product-body' },
      reasons ? h('span', { class: 'badge badge-reco' }, 'おすすめ') : null,
      h('h3', { class: 'product-name' }, product.name),
      reasons ? h('p', { class: 'reco-reason' }, reasons.join('・')) : null,
      product.description ? h('p', { class: 'product-desc' }, product.description) : null,
      h('p', { class: `allergen-line${conflicts.length ? ' is-warning' : ''}` },
        conflicts.length ? `⚠ ${conflicts.join('・')}を含みます` : allergyText),
      h('div', { class: 'product-meta' },
        h('span', { class: 'price' }, formatYen(product.price)),
        product.is_available ? null : h('span', { class: 'badge badge-stop' }, '販売停止')),
      available
        ? quantityControl(product.id, quantity, product.name)
        : h('p', { class: 'help' }, conflicts.length ? '選んだアレルギーを含むため注文できません' : '現在ご注文いただけません')));
}

function quantityControl(productId, quantity, name) {
  return h('div', { class: 'qty-control', role: 'group', 'aria-label': `${name}の数量` },
    h('button', { type: 'button', class: 'btn btn-qty', 'aria-label': `${name}を1つ減らす`, disabled: quantity <= 0,
      dataset: { qtyMinus: productId }, onclick: () => updateCart(changeQuantity(session.cart, productId, -1)) }, '−'),
    h('output', { class: 'qty-value', dataset: { qtyFor: productId }, 'aria-live': 'polite' }, String(quantity)),
    h('button', { type: 'button', class: 'btn btn-qty btn-qty-plus', 'aria-label': `${name}を1つ増やす`, disabled: quantity >= MAX_QUANTITY,
      dataset: { qtyPlus: productId }, onclick: () => updateCart(changeQuantity(session.cart, productId, 1)) }, '＋'));
}

/** カートを変更したら注文識別キーを作り直す（内容が変わった注文は別の注文として扱う）。 */
function updateCart(nextCart) {
  session.cart = nextCart;
  session.requestKey = null;
  saveSession();
  for (const output of document.querySelectorAll('[data-qty-for]')) {
    const quantity = getQuantity(session.cart, output.dataset.qtyFor);
    output.textContent = String(quantity);
    const control = output.closest('.qty-control');
    control.querySelector('[data-qty-minus]').disabled = quantity <= 0;
    control.querySelector('[data-qty-plus]').disabled = quantity >= MAX_QUANTITY;
    output.closest('.product-card')?.classList.toggle('is-selected', quantity > 0);
  }
  renderCart();
}

function removeWithUndo(line) {
  const before = session.cart;
  updateCart(removeLine(session.cart, line.productId));
  toast(`「${line.name}」をカートから削除しました。`, {
    actionLabel: '元に戻す',
    onAction: () => updateCart(before),
    duration: 6000,
  });
}

function renderCart() {
  const panel = document.getElementById('cart-panel');
  if (!panel) return;
  const lines = cartLines(session.cart, state.productsById, session.avoidAllergens);
  const total = cartTotal(lines);
  const count = cartCount(session.cart);
  const hasProblem = lines.some((line) => line.problem);
  panel.classList.toggle('is-open', state.cartOpen);
  const title = session.visitId ? '追加のご注文' : 'ご注文内容';

  clear(panel).append(
    h('button', { type: 'button', class: 'cart-toggle', 'aria-expanded': String(state.cartOpen),
      onclick: () => { state.cartOpen = !state.cartOpen; renderCart(); } },
    h('span', {}, `カート ${count}点`),
    h('strong', {}, formatYen(total)),
    h('span', { class: 'cart-toggle-hint' }, state.cartOpen ? '閉じる ▼' : '開く ▲')),
    h('div', { class: 'cart-body' },
      h('h2', { class: 'cart-title' }, title, h('span', { class: 'cart-count' }, `${count}点`)),
      lines.length
        ? h('ul', { class: 'cart-lines' }, lines.map((line) => h('li', { class: `cart-line${line.problem ? ' has-problem' : ''}` },
          h('div', { class: 'cart-line-head' },
            h('span', { class: 'cart-line-name' }, line.name),
            h('button', { type: 'button', class: 'btn btn-small btn-text-danger', onclick: () => removeWithUndo(line) }, '削除')),
          line.problem ? h('p', { class: 'problem' }, `${line.problem}のため注文できません。削除してください。`) : null,
          h('div', { class: 'cart-line-foot' },
            line.problem ? h('span', {}, `数量 ${line.quantity}`) : h('div', { class: 'qty-control qty-compact' },
              h('button', { type: 'button', class: 'btn btn-qty', 'aria-label': `${line.name}を1つ減らす`,
                onclick: () => updateCart(changeQuantity(session.cart, line.productId, -1)) }, '−'),
              h('output', { class: 'qty-value' }, String(line.quantity)),
              h('button', { type: 'button', class: 'btn btn-qty btn-qty-plus', 'aria-label': `${line.name}を1つ増やす`, disabled: line.quantity >= MAX_QUANTITY,
                onclick: () => updateCart(setQuantity(session.cart, line.productId, line.quantity + 1)) }, '＋')),
            h('span', { class: 'cart-line-price' }, `${formatYen(line.unitPrice)} × ${line.quantity}`,
              h('strong', {}, formatYen(line.subtotal)))))))
        : h('p', { class: 'empty' }, 'まだ商品が選ばれていません。「＋」で商品を追加してください。'),
      h('div', { class: 'cart-total' }, h('span', {}, session.visitId ? '今回の追加分' : '合計（税込）'), h('strong', {}, formatYen(total))),
      session.visitId ? h('p', { class: 'help cart-visit-total' }, `確定するとお会計は ${formatYen(session.visitTotal + total)} になります`) : null,
      hasProblem ? h('p', { class: 'problem' }, '注文できない商品があります。削除してから確認に進んでください。') : null,
      h('button', { type: 'button', class: 'btn btn-primary btn-large btn-block', disabled: !lines.length || hasProblem,
        onclick: () => go('confirm') }, '注文内容を確認する')),
  );
}

// ---------- 注文確認画面 ----------

async function renderConfirm() {
  if (!session.partySize) return go('top');
  if (!session.cart.length) return go('menu');
  // 確認画面に入るたびに最新の価格・販売状態を取得する（確定時はサーバーでも再検証する）。
  viewRoot.append(loadingBox('最新の価格を確認しています…'));
  await loadMenu();
  if (session.view !== 'confirm') return undefined;
  clear(viewRoot);
  if (state.menuError) {
    viewRoot.append(retryBox(state.menuError, () => render()));
    return undefined;
  }

  const lines = cartLines(session.cart, state.productsById, session.avoidAllergens);
  const total = cartTotal(lines);
  const problems = lines.filter((line) => line.problem);
  if (!session.requestKey) {
    session.requestKey = newRequestKey();
    saveSession();
  }
  const errorArea = h('div', { 'aria-live': 'assertive' });
  const submitButton = h('button', { type: 'button', class: 'btn btn-primary btn-large', disabled: problems.length > 0 },
    session.visitId ? '追加注文を確定する' : 'この内容で注文を確定する');
  submitButton.addEventListener('click', () => submitOrder(submitButton, errorArea, total));

  viewRoot.append(h('section', { class: 'card confirm-screen' },
    h('h1', { class: 'page-title' }, session.visitId ? '追加注文の確認' : 'ご注文内容の確認'),
    h('p', { class: 'lead' }, '内容をご確認のうえ、下のボタンで注文を確定してください。'),
    itemsTable(lines.map((l) => ({ ...l, product_name: l.problem ? `${l.name}（${l.problem}）` : l.name, unit_price: l.unitPrice }))),
    h('dl', { class: 'summary-list' },
      h('div', {}, h('dt', {}, 'お客様'), h('dd', {}, guestLabel(session.tableLabel, session.partySize))),
      session.avoidAllergens?.length ? h('div', {}, h('dt', {}, '避けるアレルギー'), h('dd', {}, allergenNames(session.avoidAllergens).join('・'))) : null,
      h('div', { class: 'summary-total' }, h('dt', {}, session.visitId ? '今回の追加分（税込）' : '合計金額（税込）'), h('dd', {}, formatYen(total))),
      session.visitId ? h('div', {}, h('dt', {}, '追加後のお会計（予定）'), h('dd', {}, formatYen(session.visitTotal + total))) : null),
    problems.length ? errorBox({ message: '注文できない商品が含まれています（販売停止・メニューにない・選んだアレルギーを含む）。', hint: 'メニューに戻り、該当の商品を削除してください。' }) : null,
    errorArea,
    h('div', { class: 'actions' },
      h('button', { type: 'button', class: 'btn btn-light btn-large', onclick: () => go('menu') }, 'メニューに戻って修正'),
      submitButton)));
  return undefined;
}

async function submitOrder(button, errorArea, displayedTotal) {
  if (state.submitting) return;
  state.submitting = true;
  clear(errorArea);
  try {
    await withBusy(button, '注文を送信中…', async () => {
      const result = await api('POST', '/api/orders', {
        json: {
          request_key: session.requestKey,
          visit_id: session.visitId,
          party_size: session.partySize,
          table_label: session.tableLabel,
          items: session.cart.map((line) => ({ product_id: line.productId, quantity: line.quantity })),
          expected_total: displayedTotal,
        },
      });
      session.cart = [];
      session.requestKey = null;
      go('done', { orderId: result.order.id, visitId: result.visit.id, visitTotal: result.visit.total });
      toast(result.duplicate ? 'この注文はすでに確定済みです（重複して登録はしていません）。' : '注文を確定しました。', { type: 'success' });
    });
  } catch (error) {
    if (['PRICE_CHANGED', 'PRODUCT_UNAVAILABLE', 'PRODUCT_NOT_FOUND'].includes(error.code)) {
      session.requestKey = null;
      saveSession();
      await loadMenu();
    }
    if (error.code === 'NETWORK' || error.code === 'TIMEOUT') {
      error.hint = '注文が保存されたか確認できませんでした。もう一度確定ボタンを押してください（同じ注文が二重に登録されることはありません）。';
    }
    clear(errorArea).append(errorBox(error));
    if (['PRICE_CHANGED', 'PRODUCT_UNAVAILABLE', 'PRODUCT_NOT_FOUND'].includes(error.code)) {
      errorArea.append(h('button', { type: 'button', class: 'btn btn-primary', onclick: () => render() }, '最新の内容で確認し直す'));
    }
    if (error.code === 'VISIT_CLOSED' || error.code === 'VISIT_NOT_FOUND') {
      errorArea.append(h('button', { type: 'button', class: 'btn btn-primary', onclick: startNextCustomer }, '新しいお客様として受付する'));
    }
  } finally {
    state.submitting = false;
  }
}

// ---------- 注文完了画面 ----------

async function renderDone() {
  if (!session.orderId || !session.visitId) return go('top');
  viewRoot.append(loadingBox());
  let order;
  let visit;
  try {
    [{ order }, { visit }] = await Promise.all([
      api('GET', `/api/orders/${session.orderId}`), api('GET', `/api/visits/${session.visitId}`),
    ]);
  } catch (error) {
    clear(viewRoot).append(retryBox(error, () => render()));
    return undefined;
  }
  if (session.view !== 'done') return undefined;
  session.visitTotal = visit.total;
  saveSession();
  const isAdditional = visit.orders.length > 1;
  clear(viewRoot).append(h('section', { class: 'card done-screen' },
    h('p', { class: 'eyebrow success' }, isAdditional ? '追加注文を確定しました' : '注文を確定しました'),
    h('h1', { class: 'page-title' }, 'ご注文を承りました'),
    h('p', { class: 'order-number' }, '注文番号 ', h('strong', {}, order.order_number)),
    itemsTable(order.items),
    h('div', { class: 'visit-summary' },
      h('h2', { class: 'section-title' }, `このお客様のご注文（${guestLabel(visit.table_label, visit.party_size)}・${visit.order_count}回）`),
      itemsTable(visit.items_summary),
      h('div', { class: 'cart-total' }, h('span', {}, '現在のお会計（税込）'), h('strong', {}, formatYen(visit.total)))),
    h('div', { class: 'actions' },
      h('button', { type: 'button', class: 'btn btn-light btn-large', onclick: startNextCustomer }, '次のお客様の受付へ'),
      h('button', { type: 'button', class: 'btn btn-light btn-large', onclick: () => go('bill', { billVisitId: visit.id, returnView: 'done' }) }, '会計へ'),
      h('button', { type: 'button', class: 'btn btn-primary btn-large', onclick: () => startAdditionalOrder(visit) }, '追加注文する'))));
  return undefined;
}

// ---------- 会計画面 ----------

async function renderBill() {
  if (!session.billVisitId) return go('top');
  viewRoot.append(loadingBox('保存済みの注文から金額を計算しています…'));
  let visit;
  try {
    ({ visit } = await api('GET', `/api/visits/${session.billVisitId}/bill`));
  } catch (error) {
    clear(viewRoot).append(...[retryBox(error, () => render()), backButton()].filter(Boolean));
    return undefined;
  }
  if (session.view !== 'bill') return undefined;
  const { bill } = visit;
  const split = splitAmount(bill.total, visit.party_size);
  const errorArea = h('div', { 'aria-live': 'assertive' });

  const actions = [];
  if (visit.active_order_count === 0) {
    actions.push(h('p', { class: 'notice' }, 'この伝票の注文はすべて取消されているため、会計はありません。'));
  } else if (visit.payment_status === 'unpaid') {
    const button = h('button', { type: 'button', class: 'btn btn-primary btn-large' }, '会計内容を確認しました');
    button.addEventListener('click', () => withBusy(button, '記録中…', async () => {
      try {
        await api('POST', `/api/visits/${visit.id}/check`);
        toast('会計確認済みにしました。代金を受け取ったら「代金の受け取りを記録する」を押してください。', { type: 'success', duration: 6000 });
        render();
      } catch (error) {
        clear(errorArea).append(errorBox(error));
      }
    }));
    actions.push(h('p', { class: 'help' }, '金額をお客様と確認したら押してください。この操作では「会計済み」にはなりません。'), button);
  } else if (visit.payment_status === 'checked') {
    const button = h('button', { type: 'button', class: 'btn btn-success btn-large' }, '代金の受け取りを記録する');
    button.addEventListener('click', async () => {
      const ok = await confirmDialog({
        title: '代金を受け取りましたか？',
        body: h('div', {}, h('p', { class: 'confirm-amount' }, formatYen(bill.total)),
          h('p', {}, `伝票 ${visit.visit_number}（${guestLabel(visit.table_label, visit.party_size)}）の代金を実際に受け取った場合だけ「受け取りました」を押してください。`)),
        okLabel: '受け取りました',
        cancelLabel: 'まだ受け取っていない',
      });
      if (!ok) return;
      await withBusy(button, '記録中…', async () => {
        try {
          await api('POST', `/api/visits/${visit.id}/payment`, { json: { confirmed: true, expected_total: bill.total } });
          finishVisit(visit);
        } catch (error) {
          clear(errorArea).append(errorBox(error));
          if (error.code === 'NOT_CHECKED') render();
        }
      });
    });
    actions.push(button);
  } else if (visit.payment_status === 'paid') {
    actions.push(h('p', { class: 'notice success' }, `会計済み（${formatDateTime(visit.paid_at)} に記録）`),
      h('button', { type: 'button', class: 'btn btn-primary btn-large', onclick: startNextCustomer }, '次のお客様の受付へ'));
  }

  clear(viewRoot).append(h('section', { class: 'card bill-screen' },
    h('h1', { class: 'page-title' }, 'お会計'),
    h('div', { class: 'bill-meta' },
      h('span', { class: 'guest-label' }, guestLabel(visit.table_label, visit.party_size)),
      h('span', {}, `伝票 ${visit.visit_number}`),
      h('span', {}, `ご来店 ${formatDateTime(visit.opened_at).slice(11)}・注文 ${visit.order_count}回`),
      paymentBadge(visit)),
    h('h2', { class: 'section-title' }, `ご注文内容（${summaryCount(visit.items_summary)}点）`),
    visit.items_summary.length ? itemsTable(visit.items_summary) : h('p', { class: 'empty' }, '会計対象の注文はありません。'),
    h('details', { class: 'breakdown-toggle' }, h('summary', {}, '注文ごとの内訳を見る'), orderBreakdown(visit)),
    h('div', { class: 'bill-totals' },
      h('div', { class: 'bill-total' }, h('span', {}, '合計金額（税込）'), h('strong', {}, formatYen(bill.total))),
      h('div', { class: 'bill-split' },
        h('span', {}, `1人あたりの参考金額（${visit.party_size}人で割った場合）`),
        h('strong', {}, split ? formatYen(split.perPerson) : '計算できません'),
        h('p', { class: 'help' }, bill.split.rule))),
    errorArea,
    h('div', { class: 'actions' },
      backButton(),
      visit.can_add_order ? h('button', { type: 'button', class: 'btn btn-light btn-large', onclick: () => startAdditionalOrder(visit) }, '追加注文する') : null,
      h('button', { type: 'button', class: 'btn btn-light btn-large', onclick: () => render() }, '最新の金額に更新'),
      actions)));
  return undefined;
}

/** 会計が終わったお客様の情報をこの端末から消し、最初の画面（履歴から来た場合は履歴）に戻す。注文データ自体は売上のため残る。 */
function finishVisit(visit) {
  session = defaultSession();
  state.activeTab = RECOMMEND_TAB;
  state.search = '';
  state.cartOpen = false;
  clear(viewRoot).append(h('section', { class: 'card done-screen finish-screen' },
    h('p', { class: 'eyebrow success' }, '会計が完了しました'),
    h('h1', { class: 'page-title' }, 'ありがとうございました'),
    h('p', { class: 'order-number' }, `${guestLabel(visit.table_label, visit.party_size)}・${formatYen(visit.bill.total)}`),
    h('p', { class: 'help' }, 'まもなく次のお客様の受付画面に戻ります。')));
  saveSession();
  setTimeout(() => go('top'), 2500);
}

function backButton() {
  const labels = { tableHistory: 'このテーブルの注文に戻る', done: '注文完了画面に戻る', menu: 'メニューに戻る' };
  const target = labels[session.returnView] ? session.returnView : 'menu';
  if (target === 'done' && !session.orderId) return null;
  return h('button', { type: 'button', class: 'btn btn-light btn-large', onclick: () => go(target) }, labels[target]);
}

// ---------- このテーブルの注文（会計まで） ----------

async function renderTableHistory() {
  if (!session.visitId) return go('top');
  viewRoot.append(loadingBox());
  let visit;
  try {
    ({ visit } = await api('GET', `/api/visits/${session.visitId}`));
  } catch (error) {
    clear(viewRoot).append(retryBox(error, () => render()));
    return undefined;
  }
  if (session.view !== 'tableHistory') return undefined;
  if (visit.payment_status === 'paid') {
    // 会計済みならこの端末の記録は消して最初の画面へ。
    session = defaultSession();
    go('top');
    return undefined;
  }
  session.visitTotal = visit.total;
  saveSession();
  const back = session.returnView && !['tableHistory', 'bill'].includes(session.returnView) ? session.returnView : 'menu';
  clear(viewRoot).append(h('section', { class: 'card bill-screen' },
    h('div', { class: 'page-head' },
      h('h1', { class: 'page-title' }, 'このテーブルのご注文'),
      h('button', { type: 'button', class: 'btn btn-light', onclick: () => go(back) }, '戻る')),
    h('div', { class: 'bill-meta' },
      h('span', { class: 'guest-label' }, guestLabel(visit.table_label, visit.party_size)),
      h('span', {}, `ご来店 ${formatDateTime(visit.opened_at).slice(11)}・注文 ${visit.order_count}回`),
      visit.waiting_order_count ? h('span', { class: 'badge status-cooking' }, `お料理準備中 ${visit.waiting_order_count}件`) : h('span', { class: 'badge status-served' }, 'すべてお出ししました')),
    h('h2', { class: 'section-title' }, `ご注文内容（${summaryCount(visit.items_summary)}点）`),
    visit.items_summary.length ? itemsTable(visit.items_summary) : h('p', { class: 'empty' }, 'まだご注文はありません。'),
    h('details', { class: 'breakdown-toggle', open: true }, h('summary', {}, '注文ごとの内訳'), orderBreakdown(visit)),
    h('div', { class: 'cart-total' }, h('span', {}, '現在のお会計（税込）'), h('strong', {}, formatYen(visit.total))),
    h('p', { class: 'help' }, 'この画面の内容は、会計が終わると表示されなくなります。'),
    h('div', { class: 'actions' },
      h('button', { type: 'button', class: 'btn btn-light btn-large', onclick: () => go('bill', { billVisitId: visit.id, returnView: 'tableHistory' }) }, '会計へ'),
      h('button', { type: 'button', class: 'btn btn-primary btn-large', onclick: () => startAdditionalOrder(visit) }, '追加注文する'))));
  return undefined;
}

// ---------- ログイン ----------

function resetLocalState() {
  session = defaultSession();
  try {
    sessionStorage.removeItem(SESSION_KEY);
  } catch (error) {
    console.error('画面の状態を消去できませんでした', error);
  }
  Object.assign(state, { categories: [], products: [], productsById: new Map(), menuLoaded: false, menuError: null,
    activeTab: RECOMMEND_TAB, search: '', reco: { items: [], mode: 'popular', error: null, loading: false }, cartOpen: false });
}

function showLogin() {
  state.loggedIn = false;
  storeNameEl.textContent = 'メニューオーダー';
  document.title = 'ログイン | メニューオーダー';
  clear(navEl);
  clear(viewRoot).append(storeLoginCard({
    title: '店舗ログイン',
    lead: 'お店のメールアドレスとパスワードでログインしてください。',
    onSuccess: async () => {
      resetLocalState();
      await boot();
    },
  }));
}

async function logout() {
  const unsentItems = session.cart.length > 0;
  const ok = await confirmDialog({
    title: 'ログアウトしますか？',
    body: h('p', {}, unsentItems
      ? 'まだ注文を確定していない商品がカートにあります。ログアウトするとカートの内容は消えます。'
      : 'この端末で注文を受けるには、もう一度ログインが必要になります。'),
    okLabel: 'ログアウト',
    cancelLabel: 'やめる',
    danger: unsentItems,
  });
  if (!ok) return;
  try {
    await api('POST', '/api/auth/store/logout');
  } catch (error) {
    toast(`${error.message} ${error.hint}`, { type: 'error' });
    return;
  }
  resetLocalState();
  showLogin();
}

// どの操作中でもログインが切れたらログイン画面に戻す（入力中のカートは sessionStorage に残る）。
onApiError('STORE_LOGIN_REQUIRED', () => {
  if (state.loggedIn) {
    toast('ログインの有効期限が切れました。もう一度ログインしてください。', { type: 'error', duration: 8000 });
  }
  showLogin();
});

// ---------- 起動 ----------

const VIEWS = { top: renderTop, menu: renderMenu, confirm: renderConfirm, done: renderDone, bill: renderBill, tableHistory: renderTableHistory };

function render() {
  if (!VIEWS[session.view]) session.view = 'top';
  renderHeader();
  clear(viewRoot);
  const result = VIEWS[session.view]();
  if (result instanceof Promise) {
    result.catch((error) => {
      console.error('画面の表示に失敗しました', error);
      clear(viewRoot).append(retryBox({ message: '画面の表示中にエラーが発生しました。', hint: 'もう一度読み込んでください。' }, () => render()));
    });
  }
}

async function boot() {
  try {
    const status = await api('GET', '/api/auth/store/session');
    if (!status.logged_in) {
      showLogin();
      return;
    }
    state.loggedIn = true;
    state.noLogin = Boolean(status.no_login);
    state.settings = await api('GET', '/api/settings/public');
  } catch (error) {
    if (error.code === 'STORE_LOGIN_REQUIRED') return;
    console.error('起動時の読み込みに失敗しました', error);
    clear(viewRoot).append(retryBox(error, boot));
    return;
  }
  render();
}

boot();
