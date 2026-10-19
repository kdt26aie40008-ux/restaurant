// 店舗管理画面：注文履歴（全テーブル）・店舗設定・カテゴリ・商品・CSV入出力・操作ログ。
// サーバー側で「店舗ログイン＋15分以内のパスワード再確認」を必須にしているため、この画面を開けても未ログインでは何も変更できない。
// DB全体のバックアップ・復元は全店舗に影響するため、運営者サイト（/operator）に置いている。

import { api, onApiError } from './api.js';
import { reauthCard, storeLoginCard } from './auth-ui.js';
import { clear, confirmDialog, errorBox, formatDateTime, h, toast, todayInJapan, withBusy } from './dom.js';
import { allergenNames, formatYen } from './logic.js';

const viewRoot = document.getElementById('view');
const navEl = document.getElementById('header-nav');
const brandEl = document.querySelector('.brand');
const SECTIONS = [
  ['history', '注文履歴'], ['settings', '店舗設定'], ['categories', 'カテゴリ'], ['products', '商品'], ['import', 'メニューCSV取込'],
  ['export', 'CSV出力'], ['logs', '操作ログ'],
];
let activeSection = 'history';
const historyFilter = { date: todayInJapan(), status: 'unpaid', query: '' };
let noLogin = false;

function baseNav() {
  clear(navEl).append(h('a', { class: 'btn btn-light', href: '/' }, '注文画面へ'));
}

async function boot() {
  clear(viewRoot).append(h('p', { class: 'loading' }, '読み込み中…'));
  try {
    const session = await api('GET', '/api/auth/store/session');
    noLogin = Boolean(session.no_login);
    if (!session.logged_in) showLogin();
    else if (!session.elevated) showReauth();
    else renderAdmin(session.store_name);
  } catch (error) {
    clear(viewRoot).append(errorBox(error), h('button', { type: 'button', class: 'btn btn-primary', onclick: boot }, 'もう一度読み込む'));
  }
}

function showLogin() {
  baseNav();
  brandEl.textContent = '店舗管理';
  clear(viewRoot).append(storeLoginCard({ title: '店舗管理ログイン', admin: true, onSuccess: boot }));
}

function showReauth() {
  baseNav();
  navEl.append(h('button', { type: 'button', class: 'btn btn-light', onclick: logout }, 'ログアウト'));
  clear(viewRoot).append(reauthCard({ onSuccess: boot, onLogout: showLogin }));
}

onApiError('STORE_LOGIN_REQUIRED', () => showLogin());
onApiError('REAUTH_REQUIRED', () => {
  toast('安全のため、もう一度パスワードを入力してください。', { type: 'error' });
  showReauth();
});

function renderAdmin(storeName) {
  if (storeName) brandEl.textContent = `店舗管理｜${storeName}`;
  baseNav();
  if (!noLogin) navEl.append(h('button', { type: 'button', class: 'btn btn-light', onclick: logout }, 'ログアウト'));
  const content = h('div', { id: 'admin-content' });
  clear(viewRoot).append(
    h('div', { class: 'admin-tabs', role: 'tablist' }, SECTIONS.map(([id, label]) => h('button', {
      type: 'button', role: 'tab', class: 'tab', 'aria-selected': String(id === activeSection),
      onclick: () => { activeSection = id; renderAdmin(); },
    }, label))),
    content);
  const renderers = { history: renderHistory, settings: renderSettings, categories: renderCategories, products: renderProducts, import: renderImport,
    export: renderExport, logs: renderLogs };
  renderers[activeSection](content).catch((error) => {
    if (['STORE_LOGIN_REQUIRED', 'REAUTH_REQUIRED'].includes(error.code)) return;
    clear(content).append(errorBox(error));
  });
}

async function logout() {
  try {
    await api('POST', '/api/auth/store/logout');
  } catch (error) {
    toast(`${error.message} ${error.hint}`, { type: 'error' });
    return;
  }
  showLogin();
}

function section(title, ...children) {
  return h('section', { class: 'card admin-section' }, h('h2', {}, title), children);
}

// ---------- 注文履歴（全テーブル・スタッフ用） ----------

const STATUS_ACTION_LABELS = { cooking: '調理中にする', served: '提供済みにする' };
const HISTORY_FILTERS = [['unpaid', '未会計'], ['paid', '会計済み'], ['all', 'すべて']];

async function renderHistory(root) {
  const date = h('input', { type: 'date', class: 'text-input', value: historyFilter.date });
  const query = h('input', { type: 'search', class: 'text-input', value: historyFilter.query, maxlength: 20, placeholder: 'テーブル・伝票・注文番号' });
  const apply = () => { Object.assign(historyFilter, { date: date.value || todayInJapan(), query: query.value.trim() }); renderAdmin(); };
  date.addEventListener('change', apply);
  query.addEventListener('keydown', (event) => { if (event.key === 'Enter') apply(); });
  const params = new URLSearchParams({ date: historyFilter.date, status: historyFilter.status });
  if (historyFilter.query) params.set('q', historyFilter.query);
  const [{ visits }, summary] = await Promise.all([
    api('GET', `/api/visits?${params}`),
    api('GET', `/api/sales/summary?date=${encodeURIComponent(historyFilter.date)}`),
  ]);
  clear(root).append(section('注文履歴（全テーブル）',
    h('div', { class: 'form-grid' },
      h('label', { class: 'field' }, h('span', {}, '日付'), date),
      h('label', { class: 'field' }, h('span', {}, '検索'), query),
      h('div', { class: 'actions-inline' },
        h('button', { type: 'button', class: 'btn btn-primary', onclick: apply }, '表示'),
        h('button', { type: 'button', class: 'btn btn-light', onclick: () => renderAdmin() }, '最新に更新'))),
    h('div', { class: 'actions-inline' }, HISTORY_FILTERS.map(([value, label]) => h('button', {
      type: 'button', class: 'chip', 'aria-pressed': String(historyFilter.status === value),
      onclick: () => { historyFilter.status = value; renderAdmin(); },
    }, label))),
    h('div', { class: 'result-summary' },
      [['注文件数（取消除く）', `${summary.order_count}件`], ['売上合計（取消除く）', formatYen(summary.sales_total)],
        ['会計済み合計', formatYen(summary.paid_total)], ['取消', `${summary.cancelled_count}件`]]
        .map(([label, value]) => h('div', { class: 'summary-card' }, h('span', {}, label), h('strong', {}, value)))),
    visits.length ? h('div', { class: 'history-list' }, visits.map(visitCard))
      : h('p', { class: 'empty' }, { unpaid: '未会計のお客様はいません。', paid: '会計済みのお客様はいません。', all: 'この日の注文はありません。' }[historyFilter.status])));
}

function guestLabel(visit) {
  return [visit.table_label || null, `${visit.party_size}人`].filter(Boolean).join('・');
}

function visitCard(visit) {
  const errorArea = h('div', { 'aria-live': 'assertive' });
  const actions = [];
  if (visit.active_order_count && visit.payment_status === 'unpaid') {
    const check = h('button', { type: 'button', class: 'btn btn-light' }, '会計内容を確認済みにする');
    check.addEventListener('click', () => withBusy(check, '記録中…', async () => {
      try {
        await api('POST', `/api/visits/${visit.id}/check`);
        toast('会計確認済みにしました。', { type: 'success' });
        renderAdmin();
      } catch (error) {
        clear(errorArea).append(errorBox(error));
      }
    }));
    actions.push(check);
  }
  if (visit.payment_status === 'checked') {
    const pay = h('button', { type: 'button', class: 'btn btn-success' }, `代金 ${formatYen(visit.total)} の受け取りを記録`);
    pay.addEventListener('click', async () => {
      const ok = await confirmDialog({
        title: '代金を受け取りましたか？',
        body: h('p', {}, `${guestLabel(visit)}（伝票 ${visit.visit_number}）の ${formatYen(visit.total)} を実際に受け取った場合だけ押してください。`),
        okLabel: '受け取りました', cancelLabel: 'まだ受け取っていない',
      });
      if (!ok) return;
      await withBusy(pay, '記録中…', async () => {
        try {
          await api('POST', `/api/visits/${visit.id}/payment`, { json: { confirmed: true, expected_total: visit.total } });
          toast('会計済みにしました。', { type: 'success' });
          renderAdmin();
        } catch (error) {
          clear(errorArea).append(errorBox(error));
        }
      });
    });
    actions.push(pay);
  }
  return h('article', { class: `card visit-card payment-border-${visit.payment_status}` },
    h('div', { class: 'visit-head' },
      h('strong', { class: 'guest-label' }, guestLabel(visit)),
      h('span', { class: 'help' }, `伝票 ${visit.visit_number}`),
      h('span', { class: 'help' }, `${formatDateTime(visit.opened_at).slice(11)}〜${formatDateTime(visit.last_ordered_at).slice(11)}・注文${visit.order_count}回`),
      h('span', { class: `badge payment-${visit.payment_status}` }, visit.payment_status_label),
      visit.waiting_order_count ? h('span', { class: 'badge status-cooking' }, `未提供 ${visit.waiting_order_count}件`) : null),
    h('div', { class: 'visit-body' },
      h('ul', { class: 'plain-list visit-items' }, visit.items_summary.map((item) => h('li', {},
        h('span', {}, `${item.product_name} × ${item.quantity}`), h('span', {}, formatYen(item.subtotal))))),
      h('div', { class: 'visit-total' }, h('span', {}, '合計'), h('strong', {}, formatYen(visit.total)))),
    h('details', { class: 'breakdown-toggle' }, h('summary', {}, `注文ごとの内訳・調理状態（${visit.order_count}回）`),
      h('div', { class: 'order-breakdown' }, visit.orders.map((order, index) => orderEntry(order, index, visit, errorArea)))),
    errorArea,
    actions.length ? h('div', { class: 'actions-inline visit-actions' }, actions) : null);
}

function orderEntry(order, index, visit, errorArea) {
  const buttons = [];
  if (order.order_status !== 'cancelled' && order.next_order_status) {
    const button = h('button', { type: 'button', class: 'btn btn-light btn-small' }, STATUS_ACTION_LABELS[order.next_order_status]);
    button.addEventListener('click', () => withBusy(button, '更新中…', async () => {
      try {
        await api('POST', `/api/orders/${order.id}/status`, { json: { current_status: order.order_status, new_status: order.next_order_status } });
        renderAdmin();
      } catch (error) {
        clear(errorArea).append(errorBox(error));
      }
    }));
    buttons.push(button);
  }
  if (order.order_status !== 'cancelled' && visit.payment_status !== 'paid') {
    const cancel = h('button', { type: 'button', class: 'btn btn-text-danger btn-small' }, 'この注文を取消');
    cancel.addEventListener('click', async () => {
      const reason = h('textarea', { class: 'text-input', rows: 2, maxlength: 200, 'aria-label': '取消理由' });
      const ok = await confirmDialog({
        title: `注文 ${order.order_number} を取消しますか？`,
        body: h('div', { class: 'stack' }, h('p', {}, '注文は削除されず「取消」として記録され、会計・売上から外れます。'),
          h('label', { class: 'field' }, h('span', {}, '取消理由（必須）'), reason)),
        okLabel: '取消する', cancelLabel: 'やめる', danger: true,
      });
      if (!ok) return;
      try {
        await api('POST', `/api/orders/${order.id}/cancel`, { json: { reason: reason.value } });
        toast(`注文 ${order.order_number} を取消しました。`, { type: 'success' });
        renderAdmin();
      } catch (error) {
        clear(errorArea).append(errorBox(error));
      }
    });
    buttons.push(cancel);
  }
  return h('div', { class: `order-entry${order.order_status === 'cancelled' ? ' is-cancelled' : ''}` },
    h('div', { class: 'order-entry-head' },
      h('strong', {}, index === 0 ? '最初の注文' : `追加注文 ${index}`),
      h('span', { class: 'help' }, `${formatDateTime(order.ordered_at).slice(11)}・注文番号 ${order.order_number}`),
      h('span', { class: `badge status-${order.order_status}` }, order.order_status_label),
      h('strong', { class: 'order-entry-total' }, formatYen(order.total_amount))),
    h('ul', { class: 'plain-list' }, order.items.map((item) => h('li', {}, h('span', {}, `${item.product_name} × ${item.quantity}`), h('span', {}, formatYen(item.subtotal))))),
    order.cancel_reason ? h('p', { class: 'help' }, `取消理由: ${order.cancel_reason}`) : null,
    buttons.length ? h('div', { class: 'actions-inline' }, buttons) : null);
}

// ---------- 店舗設定 ----------

async function renderSettings(root) {
  const { settings } = await api('GET', '/api/admin/settings');
  const name = h('input', { class: 'text-input', value: settings.store_name, maxlength: 40 });
  const maxParty = h('input', { class: 'text-input', inputmode: 'numeric', value: settings.max_party_size });
  const bom = h('input', { type: 'checkbox', checked: settings.csv_bom_default === '1' });
  const errorArea = h('div');
  const button = h('button', { type: 'button', class: 'btn btn-primary' }, '保存する');
  button.addEventListener('click', () => withBusy(button, '保存中…', async () => {
    clear(errorArea);
    try {
      const result = await api('PUT', '/api/admin/settings', { json: {
        store_name: name.value, max_party_size: maxParty.value, csv_bom_default: bom.checked ? '1' : '0',
      } });
      brandEl.textContent = `店舗管理｜${result.settings.store_name}`;
      toast('設定を保存しました。注文画面は再読み込みすると反映されます。', { type: 'success' });
    } catch (error) {
      clear(errorArea).append(errorBox(error));
    }
  }));
  clear(root).append(section('店舗設定',
    h('div', { class: 'form-grid' },
      h('label', { class: 'field' }, h('span', {}, '店舗名'), name),
      h('label', { class: 'field' }, h('span', {}, '最大利用人数（1〜1000）'), maxParty),
      h('label', { class: 'check' }, bom, 'CSV出力を既定で BOM 付きにする（Excel向け）')),
    errorArea, h('div', {}, button),
    h('p', { class: 'help' }, 'パスワードを変更するときは、ログアウトして「パスワードを忘れた方」からメールの認証コードで設定し直してください。')));
}

// ---------- カテゴリ ----------

async function renderCategories(root) {
  const { categories } = await api('GET', '/api/admin/categories');
  const errorArea = h('div');
  const rows = categories.map((category) => {
    const name = h('input', { class: 'text-input', value: category.name, maxlength: 30 });
    const order = h('input', { class: 'text-input num-input', inputmode: 'numeric', value: category.sort_order });
    const active = h('input', { type: 'checkbox', checked: category.is_active });
    const button = h('button', { type: 'button', class: 'btn btn-light btn-small' }, '保存');
    button.addEventListener('click', () => withBusy(button, '保存中…', async () => {
      clear(errorArea);
      try {
        await api('PUT', `/api/admin/categories/${category.id}`, { json: { name: name.value, sort_order: order.value, is_active: active.checked } });
        toast(`カテゴリ「${name.value}」を保存しました。`, { type: 'success' });
      } catch (error) {
        clear(errorArea).append(errorBox(error));
      }
    }));
    return h('tr', {}, h('td', {}, String(category.id)), h('td', {}, name), h('td', {}, order),
      h('td', {}, h('label', { class: 'check' }, active, '表示')), h('td', {}, button));
  });
  const newName = h('input', { class: 'text-input', maxlength: 30, placeholder: '例: 季節限定' });
  const newOrder = h('input', { class: 'text-input num-input', inputmode: 'numeric', value: (categories.length + 1) * 10 });
  const addButton = h('button', { type: 'button', class: 'btn btn-primary' }, '追加する');
  addButton.addEventListener('click', () => withBusy(addButton, '追加中…', async () => {
    clear(errorArea);
    try {
      await api('POST', '/api/admin/categories', { json: { name: newName.value, sort_order: newOrder.value } });
      toast('カテゴリを追加しました。', { type: 'success' });
      renderAdmin();
    } catch (error) {
      clear(errorArea).append(errorBox(error));
    }
  }));
  clear(root).append(section('カテゴリ',
    h('p', { class: 'help' }, '並び順の数字が小さいほど左に表示されます。「表示」を外すと注文画面に出なくなります（データは残ります）。'),
    errorArea,
    h('div', { class: 'table-wrap' }, h('table', { class: 'admin-table' },
      h('thead', {}, h('tr', {}, ['ID', 'カテゴリ名', '並び順', '表示', ''].map((t) => h('th', {}, t)))),
      h('tbody', {}, rows))),
    h('h3', {}, 'カテゴリを追加'),
    h('div', { class: 'form-grid' }, h('label', { class: 'field' }, h('span', {}, 'カテゴリ名'), newName),
      h('label', { class: 'field' }, h('span', {}, '並び順'), newOrder), h('div', {}, addButton))));
}

// ---------- 商品 ----------

async function renderProducts(root) {
  const { products } = await api('GET', '/api/admin/products');
  const errorArea = h('div');
  const rows = products.map((product) => {
    const price = h('input', { class: 'text-input num-input', inputmode: 'numeric', value: product.price, 'aria-label': `${product.name}の価格` });
    const available = h('input', { type: 'checkbox', checked: product.is_available });
    const button = h('button', { type: 'button', class: 'btn btn-light btn-small' }, '保存');
    button.addEventListener('click', () => withBusy(button, '保存中…', async () => {
      clear(errorArea);
      try {
        const result = await api('PUT', `/api/admin/products/${encodeURIComponent(product.id)}`,
          { json: { price: price.value, is_available: available.checked } });
        toast(`「${result.product.name}」を ${formatYen(result.product.price)}・${result.product.is_available ? '販売中' : '販売停止'} にしました。`, { type: 'success' });
      } catch (error) {
        clear(errorArea).append(errorBox(error));
      }
    }));
    return h('tr', {}, h('td', { class: 'mono' }, product.id), h('td', {}, product.name), h('td', {}, product.category_name),
      h('td', {}, allergenNames(product.allergens).join('・') || '—'),
      h('td', {}, price), h('td', {}, h('label', { class: 'check' }, available, '販売中')), h('td', {}, button));
  });
  clear(root).append(section('商品',
    h('p', { class: 'help' }, '価格を変更しても、確定済みの注文の金額は変わりません（注文時の価格を保存しています）。商品名・タグ・画像などの一括変更は「メニューCSV取込」を使ってください。'),
    errorArea,
    h('div', { class: 'table-wrap' }, h('table', { class: 'admin-table' },
      h('thead', {}, h('tr', {}, ['商品ID', '商品名', 'カテゴリ', 'アレルギー', '価格（円）', '販売状態', ''].map((t) => h('th', {}, t)))),
      h('tbody', {}, rows)))));
}

// ---------- メニューCSV取込 ----------

async function renderImport(root) {
  const fileInput = h('input', { type: 'file', accept: '.csv,text/csv', class: 'text-input' });
  const resultArea = h('div', { 'aria-live': 'polite' });
  const previewButton = h('button', { type: 'button', class: 'btn btn-primary' }, '内容を確認する');
  let checkedBytes = null;

  previewButton.addEventListener('click', () => withBusy(previewButton, '確認中…', async () => {
    clear(resultArea);
    const file = fileInput.files[0];
    if (!file) {
      resultArea.append(errorBox({ message: 'ファイルが選ばれていません。', hint: 'メニューCSVファイルを選んでください。' }));
      return;
    }
    try {
      checkedBytes = await file.arrayBuffer();
    } catch (error) {
      resultArea.append(errorBox({ message: 'ファイルを読み込めませんでした。', hint: 'ファイルが開けるか確認して、選び直してください。' }));
      console.error(error);
      return;
    }
    try {
      const result = await api('POST', '/api/admin/menu-import?mode=preview', { body: checkedBytes, contentType: 'text/csv' });
      showImportResult(resultArea, result, () => applyImport(resultArea, checkedBytes));
    } catch (error) {
      resultArea.append(errorBox(error));
    }
  }));

  clear(root).append(section('メニューCSV取込',
    h('p', { class: 'help' }, '1. CSVを選んで「内容を確認する」 → 2. 追加・更新の件数とエラーを確認 → 3.「取り込む」。エラーが1件でもあると何も反映しません。'),
    h('p', { class: 'help' }, '列: product_id, category_name, product_name, price, image_path, is_available, popularity_score, recommendation_tags（description は任意）。見本は sample/menu_sample.csv です。'),
    h('div', { class: 'form-grid' }, h('label', { class: 'field' }, h('span', {}, 'CSVファイル（UTF-8、1MBまで）'), fileInput), h('div', {}, previewButton)),
    resultArea));
}

function showImportResult(area, result, onApply) {
  const s = result.summary;
  clear(area).append(
    h('div', { class: 'result-summary' },
      [['データ行', s.rows], ['追加', s.add], ['更新', s.update], ['変更なし', s.unchanged], ['エラー', s.error_count]
    ].map(([label, value]) => h('div', { class: 'summary-card' }, h('span', {}, label), h('strong', {}, `${value}件`)))),
  );
  if (!result.valid) {
    area.append(errorBox({ message: 'CSVに問題があるため取り込めません。', hint: '下の一覧を見てCSVを修正し、もう一度確認してください。' }),
      h('div', { class: 'table-wrap' }, h('table', { class: 'admin-table' },
        h('thead', {}, h('tr', {}, ['行', '列', '内容', '対処'].map((t) => h('th', {}, t)))),
        h('tbody', {}, result.errors.map((e) => h('tr', {}, h('td', {}, e.line ?? ''), h('td', { class: 'mono' }, e.column ?? ''),
          h('td', {}, e.message), h('td', {}, e.hint || '')))))));
    return;
  }
  const applyButton = h('button', { type: 'button', class: 'btn btn-success btn-large' }, `取り込む（追加 ${s.add}件・更新 ${s.update}件）`);
  applyButton.addEventListener('click', () => withBusy(applyButton, '取り込み中…', onApply));
  area.append(
    h('div', { class: 'table-wrap' }, h('table', { class: 'admin-table' },
      h('thead', {}, h('tr', {}, ['行', '処理', '商品ID', '商品名', 'カテゴリ', '価格', '販売'].map((t) => h('th', {}, t)))),
      h('tbody', {}, result.preview.map((p) => h('tr', {}, h('td', {}, p.line), h('td', {}, { add: '追加', update: '更新', unchanged: '変更なし' }[p.action]),
        h('td', { class: 'mono' }, p.product_id), h('td', {}, p.product_name), h('td', {}, p.category_name), h('td', {}, formatYen(p.price)),
        h('td', {}, p.is_available ? '販売中' : '停止')))))),
    h('div', {}, applyButton));
}

async function applyImport(area, bytes) {
  try {
    const result = await api('POST', '/api/admin/menu-import?mode=apply', { body: bytes, contentType: 'text/csv' });
    clear(area).append(h('p', { class: 'notice success' }, `取り込みました（追加 ${result.summary.add}件・更新 ${result.summary.update}件）。`));
    toast('メニューを更新しました。', { type: 'success' });
  } catch (error) {
    clear(area).append(errorBox(error));
    if (error.details?.errors) showImportResult(area, error.details, () => applyImport(area, bytes));
  }
}

// ---------- CSV出力 ----------

async function renderExport(root) {
  const { settings } = await api('GET', '/api/admin/settings');
  const from = h('input', { type: 'date', class: 'text-input', value: todayInJapan() });
  const to = h('input', { type: 'date', class: 'text-input', value: todayInJapan() });
  const bom = h('input', { type: 'checkbox', checked: settings.csv_bom_default === '1' });
  const links = h('div', { class: 'download-links' });
  const update = () => {
    const query = new URLSearchParams({ from: from.value, to: to.value, bom: bom.checked ? '1' : '0' });
    clear(links).append(
      h('a', { class: 'btn btn-primary', href: `/api/admin/export/sales.csv?${query}` }, '売上CSV（取消を除く）'),
      h('a', { class: 'btn btn-light', href: `/api/admin/export/orders.csv?${query}` }, '注文履歴CSV（取消を含む）'),
      h('a', { class: 'btn btn-light', href: `/api/admin/export/menu.csv?bom=${bom.checked ? '1' : '0'}` }, 'メニューCSV'));
  };
  for (const input of [from, to, bom]) input.addEventListener('change', update);
  update();
  clear(root).append(section('CSV出力',
    h('div', { class: 'form-grid' },
      h('label', { class: 'field' }, h('span', {}, '開始日'), from),
      h('label', { class: 'field' }, h('span', {}, '終了日'), to),
      h('label', { class: 'check' }, bom, 'BOM付き（Excelで開く場合はオン）')),
    links,
    h('p', { class: 'help' }, '= + - @ で始まる文字列には先頭に \' を付けて、Excelで数式として実行されないようにしています。CSVはバックアップの代わりにはなりません。')));
}

// ---------- 操作ログ ----------

async function renderLogs(root) {
  const { logs } = await api('GET', '/api/admin/audit-logs?limit=200');
  clear(root).append(section('操作ログ（新しい順・200件）',
    h('div', { class: 'table-wrap' }, h('table', { class: 'admin-table' },
      h('thead', {}, h('tr', {}, ['日時', '操作者', '操作', '対象', '内容'].map((t) => h('th', {}, t)))),
      h('tbody', {}, logs.map((log) => h('tr', {}, h('td', {}, formatDateTime(log.occurred_at)), h('td', {}, log.actor),
        h('td', { class: 'mono' }, log.action), h('td', {}, [log.target_type, log.target_id].filter(Boolean).join(' #')),
        h('td', { class: 'mono' }, log.detail || ''))))))));
}

boot();
