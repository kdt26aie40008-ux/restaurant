// 運営者サイト：登録店舗の一覧・招待・利用停止、DB全体のバックアップと復元、操作ログ。
// ログインはパスワード＋メールの認証コード（二段階）。サーバー側で既定ではサーバー本体からの接続だけを受け付ける。

import { api, onApiError } from './api.js';
import { clear, confirmDialog, errorBox, formatDateTime, h, toast, withBusy } from './dom.js';

const viewRoot = document.getElementById('view');
const navEl = document.getElementById('header-nav');
const SECTIONS = [['stores', '登録店舗'], ['backup', 'バックアップ'], ['logs', '操作ログ']];
let activeSection = 'stores';

async function boot() {
  clear(viewRoot).append(h('p', { class: 'loading' }, '読み込み中…'));
  try {
    const session = await api('GET', '/api/auth/operator/session');
    if (session.logged_in) renderDashboard(session.email, session.no_login);
    else renderLogin();
  } catch (error) {
    clear(viewRoot).append(errorBox(error), h('button', { type: 'button', class: 'btn btn-primary', onclick: boot }, 'もう一度読み込む'));
  }
}

onApiError('OPERATOR_LOGIN_REQUIRED', () => {
  toast('ログインの有効期限が切れました。もう一度ログインしてください。', { type: 'error' });
  renderLogin();
});

// ---------- ログイン（パスワード → メールの認証コード） ----------

function renderLogin() {
  clear(navEl);
  const email = h('input', { type: 'email', class: 'text-input', autocomplete: 'username', maxlength: 254 });
  const password = h('input', { type: 'password', class: 'text-input', autocomplete: 'current-password', maxlength: 128 });
  const errorArea = h('div', { 'aria-live': 'assertive' });
  const button = h('button', { type: 'submit', class: 'btn btn-primary btn-large btn-block' }, '次へ');
  const form = h('form', { class: 'stack', novalidate: true },
    h('label', { class: 'field' }, h('span', {}, 'メールアドレス'), email),
    h('label', { class: 'field' }, h('span', {}, 'パスワード'), password), errorArea, button);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    withBusy(button, '確認中…', async () => {
      clear(errorArea);
      try {
        const result = await api('POST', '/api/auth/operator/login', { json: { email: email.value, password: password.value } });
        password.value = '';
        renderCode(result.challenge);
      } catch (error) {
        password.value = '';
        errorArea.append(errorBox(error));
      }
    });
  });
  clear(viewRoot).append(h('section', { class: 'card login-card stack' },
    h('h1', { class: 'page-title' }, '運営者ログイン'),
    h('p', { class: 'help' }, 'パスワードが正しければ、登録メールアドレスに8桁の認証コードを送ります。'),
    form));
  email.focus();
}

function renderCode(challenge) {
  const code = h('input', { type: 'text', class: 'text-input code-input', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 8 });
  const errorArea = h('div', { 'aria-live': 'assertive' });
  const button = h('button', { type: 'submit', class: 'btn btn-primary btn-large btn-block' }, 'ログイン');
  const form = h('form', { class: 'stack', novalidate: true },
    h('label', { class: 'field' }, h('span', {}, '認証コード（8桁）'), code), errorArea, button);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    withBusy(button, '確認中…', async () => {
      clear(errorArea);
      try {
        const result = await api('POST', '/api/auth/operator/verify', { json: { challenge, code: code.value.trim() } });
        renderDashboard(result.email);
      } catch (error) {
        code.value = '';
        errorArea.append(errorBox(error));
      }
    });
  });
  clear(viewRoot).append(h('section', { class: 'card login-card stack' },
    h('h1', { class: 'page-title' }, '認証コードの入力'),
    h('p', { class: 'lead' }, 'メールに届いた8桁の認証コードを入力してください（有効期限10分・5回まで）。'),
    form,
    h('button', { type: 'button', class: 'btn btn-light', onclick: renderLogin }, '最初からやり直す')));
  code.focus();
}

async function logout() {
  try {
    await api('POST', '/api/auth/operator/logout');
  } catch (error) {
    toast(`${error.message} ${error.hint}`, { type: 'error' });
    return;
  }
  renderLogin();
}

// ---------- ダッシュボード ----------

function renderDashboard(email, noLogin = false) {
  clear(navEl).append(h('span', { class: 'help' }, email || ''),
    noLogin ? null : h('button', { type: 'button', class: 'btn btn-light', onclick: logout }, 'ログアウト'));
  const content = h('div', { id: 'operator-content' });
  clear(viewRoot).append(
    h('div', { class: 'admin-tabs', role: 'tablist' }, SECTIONS.map(([id, label]) => h('button', {
      type: 'button', role: 'tab', class: 'tab', 'aria-selected': String(id === activeSection),
      onclick: () => { activeSection = id; renderDashboard(email, noLogin); },
    }, label))),
    content);
  const renderers = { stores: renderStores, backup: renderBackup, logs: renderLogs };
  renderers[activeSection](content).catch((error) => {
    if (error.code === 'OPERATOR_LOGIN_REQUIRED') return;
    clear(content).append(errorBox(error));
  });
}

function section(title, ...children) {
  return h('section', { class: 'card admin-section' }, h('h2', {}, title), children);
}

// ---------- 登録店舗 ----------

async function renderStores(root) {
  const { stores } = await api('GET', '/api/operator/stores');
  const errorArea = h('div', { 'aria-live': 'assertive' });
  const counts = stores.reduce((acc, s) => ({ ...acc, [s.status]: (acc[s.status] || 0) + 1 }), {});

  const name = h('input', { class: 'text-input', maxlength: 40, placeholder: '例: さくら食堂 駅前店' });
  const email = h('input', { type: 'email', class: 'text-input', maxlength: 254, placeholder: 'shop@example.com' });
  const inviteButton = h('button', { type: 'button', class: 'btn btn-primary' }, '招待メールを送る');
  inviteButton.addEventListener('click', () => withBusy(inviteButton, '送信中…', async () => {
    clear(errorArea);
    try {
      const result = await api('POST', '/api/operator/stores', { json: { name: name.value, email: email.value } });
      toast(`「${result.store.name}」を登録し、招待メールを送りました。`, { type: 'success', duration: 6000 });
      renderStores(root);
    } catch (error) {
      errorArea.append(errorBox(error));
    }
  }));

  const rows = stores.map((store) => h('tr', {},
    h('td', {}, String(store.id)),
    h('td', {}, store.name),
    h('td', { class: 'mono' }, store.email || '（未設定）'),
    h('td', {}, h('span', { class: `badge status-${store.status}` }, store.status_label)),
    h('td', {}, formatDateTime(store.created_at)),
    h('td', {}, store.last_login_at ? formatDateTime(store.last_login_at) : '—'),
    h('td', { class: 'num' }, `${store.order_count.toLocaleString('ja-JP')}件`),
    h('td', {}, store.last_order_at ? formatDateTime(store.last_order_at) : '—'),
    h('td', {}, storeActions(store, root, errorArea))));

  clear(root).append(
    section('登録店舗の一覧',
      h('div', { class: 'result-summary' },
        [['登録店舗', stores.length], ['利用中', counts.active || 0], ['招待中', (counts.invited || 0) + (counts.unclaimed || 0)], ['利用停止', counts.suspended || 0]]
          .map(([label, value]) => h('div', { class: 'summary-card' }, h('span', {}, label), h('strong', {}, `${value}店`)))),
      errorArea,
      stores.length
        ? h('div', { class: 'table-wrap' }, h('table', { class: 'admin-table' },
          h('thead', {}, h('tr', {}, ['ID', '店舗名', 'メールアドレス', '状態', '登録日時', '最終ログイン', '注文数', '最終注文', '操作'].map((t) => h('th', {}, t)))),
          h('tbody', {}, rows)))
        : h('p', { class: 'empty' }, 'まだ登録された店舗はありません。下のフォームから招待してください。')),
    section('店舗を招待する',
      h('p', { class: 'help' }, '店舗名とメールアドレスを登録すると、認証コード付きの招待メールが届きます。店舗の担当者は /account でコードを入力し、パスワードを設定します（コードの有効期限は24時間）。'),
      h('div', { class: 'form-grid' },
        h('label', { class: 'field' }, h('span', {}, '店舗名'), name),
        h('label', { class: 'field' }, h('span', {}, '担当者のメールアドレス'), email),
        h('div', {}, inviteButton))));
}

function storeActions(store, root, errorArea) {
  const buttons = [];
  if (store.status === 'invited' || store.status === 'unclaimed') {
    const button = h('button', { type: 'button', class: 'btn btn-light btn-small' }, store.status === 'unclaimed' ? 'メールを設定して招待' : '招待を再送');
    button.addEventListener('click', async () => {
      const emailInput = h('input', { type: 'email', class: 'text-input', maxlength: 254, value: store.email || '' });
      const ok = await confirmDialog({
        title: `「${store.name}」に招待メールを送りますか？`,
        body: h('div', { class: 'stack' }, h('p', {}, '新しい認証コードを送ります。以前のコードは使えなくなります。'),
          h('label', { class: 'field' }, h('span', {}, 'メールアドレス'), emailInput)),
        okLabel: '送信する',
      });
      if (!ok) return;
      await withBusy(button, '送信中…', async () => {
        try {
          await api('POST', `/api/operator/stores/${store.id}/invite`, { json: { email: emailInput.value } });
          toast('招待メールを送りました。', { type: 'success' });
          renderStores(root);
        } catch (error) {
          clear(errorArea).append(errorBox(error));
        }
      });
    });
    buttons.push(button);
  }
  if (store.status === 'active') {
    const reset = h('button', { type: 'button', class: 'btn btn-light btn-small' }, '再設定メールを送る');
    reset.addEventListener('click', async () => {
      const ok = await confirmDialog({
        title: `「${store.name}」に再設定メールを送りますか？`,
        body: h('p', {}, `登録済みのアドレス（${store.email}）に、パスワード再設定用の認証コード（24時間有効）を送ります。ログインのロックと認証コードの失敗回数も解除します。店舗から依頼があった場合だけ使ってください。`),
        okLabel: '送信する',
      });
      if (!ok) return;
      await withBusy(reset, '送信中…', async () => {
        try {
          await api('POST', `/api/operator/stores/${store.id}/invite`, { json: {} });
          toast('再設定メールを送りました。', { type: 'success' });
        } catch (error) {
          clear(errorArea).append(errorBox(error));
        }
      });
    });
    buttons.push(reset);
  }
  const suspend = store.status !== 'suspended';
  const toggle = h('button', { type: 'button', class: `btn btn-small ${suspend ? 'btn-text-danger' : 'btn-light'}` }, suspend ? '利用停止' : '利用再開');
  toggle.addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: suspend ? `「${store.name}」を利用停止にしますか？` : `「${store.name}」の利用を再開しますか？`,
      body: h('p', {}, suspend
        ? 'この店舗はログインできなくなり、ログイン中の端末もすぐにログアウトされます。注文データは削除されません。'
        : '店舗が再びログインできるようになります。'),
      okLabel: suspend ? '利用停止にする' : '再開する',
      danger: suspend,
    });
    if (!ok) return;
    await withBusy(toggle, '更新中…', async () => {
      try {
        await api('POST', `/api/operator/stores/${store.id}/status`, { json: { status: suspend ? 'suspended' : 'active' } });
        toast(suspend ? '利用停止にしました。' : '利用を再開しました。', { type: 'success' });
        renderStores(root);
      } catch (error) {
        clear(errorArea).append(errorBox(error));
      }
    });
  });
  buttons.push(toggle);
  return h('div', { class: 'actions-inline' }, buttons);
}

// ---------- バックアップ ----------

async function renderBackup(root) {
  const [{ backups, backup_dir: backupDir }, { settings }] = await Promise.all([
    api('GET', '/api/operator/backups'), api('GET', '/api/operator/settings'),
  ]);
  const errorArea = h('div', { 'aria-live': 'assertive' });
  const dirInput = h('input', { class: 'text-input', value: settings.backup_dir, placeholder: '空欄なら data/backups（例: D:\\restaurant-backup）' });
  const saveDir = h('button', { type: 'button', class: 'btn btn-light' }, '保存先を変更');
  saveDir.addEventListener('click', () => withBusy(saveDir, '保存中…', async () => {
    clear(errorArea);
    try {
      await api('PUT', '/api/operator/settings', { json: { backup_dir: dirInput.value } });
      toast('バックアップの保存先を変更しました。', { type: 'success' });
      renderBackup(root);
    } catch (error) {
      errorArea.append(errorBox(error));
    }
  }));
  const createButton = h('button', { type: 'button', class: 'btn btn-primary' }, '今すぐバックアップ');
  createButton.addEventListener('click', () => withBusy(createButton, 'バックアップ中…', async () => {
    clear(errorArea);
    try {
      const result = await api('POST', '/api/operator/backups');
      toast(`バックアップを作成しました: ${result.filename}`, { type: 'success', duration: 6000 });
      renderBackup(root);
    } catch (error) {
      errorArea.append(errorBox(error));
    }
  }));
  const rows = backups.map((b) => {
    const button = h('button', { type: 'button', class: 'btn btn-text-danger btn-small' }, 'この時点に復元');
    button.addEventListener('click', async () => {
      const ok = await confirmDialog({
        title: 'バックアップから復元しますか？',
        body: h('div', { class: 'stack' }, h('p', { class: 'mono' }, b.filename),
          h('p', {}, 'すべての店舗のデータとアカウント情報が、このバックアップの時点に戻ります。復元前のデータは自動で保存します。復元後は全員がログアウトされます。')),
        okLabel: '復元する', cancelLabel: 'やめる', danger: true, requireText: '復元',
      });
      if (!ok) return;
      await withBusy(button, '復元中…', async () => {
        clear(errorArea);
        try {
          const result = await api('POST', '/api/operator/backups/restore', { json: { filename: b.filename, confirm_text: '復元' } });
          toast(`復元しました。復元前のデータは ${result.safety_copy} に保存しました。もう一度ログインしてください。`, { type: 'success', duration: 9000 });
          renderLogin();
        } catch (error) {
          errorArea.append(errorBox(error));
        }
      });
    });
    return h('tr', {}, h('td', { class: 'mono' }, b.filename), h('td', {}, `${Math.ceil(b.size_bytes / 1024).toLocaleString('ja-JP')} KB`), h('td', {}, button));
  });
  clear(root).append(section('バックアップと復元（全店舗）',
    h('p', { class: 'help' }, '保存先: ', h('span', { class: 'mono' }, backupDir)),
    h('div', { class: 'form-grid' }, h('label', { class: 'field' }, h('span', {}, '保存先フォルダ（絶対パス）'), dirInput), h('div', {}, saveDir)),
    h('div', {}, createButton),
    errorArea,
    h('p', { class: 'help' }, 'バックアップファイルにはアカウント情報（パスワードのハッシュ）も含まれます。USBメモリなどに保管する場合は、鍵のかかる場所で管理してください。'),
    rows.length
      ? h('div', { class: 'table-wrap' }, h('table', { class: 'admin-table' },
        h('thead', {}, h('tr', {}, ['ファイル名', 'サイズ', ''].map((t) => h('th', {}, t)))), h('tbody', {}, rows)))
      : h('p', { class: 'empty' }, 'まだバックアップはありません。')));
}

// ---------- 操作ログ ----------

async function renderLogs(root) {
  const { logs } = await api('GET', '/api/operator/audit-logs?limit=200');
  clear(root).append(section('操作ログ（全店舗・新しい順・200件）',
    h('div', { class: 'table-wrap' }, h('table', { class: 'admin-table' },
      h('thead', {}, h('tr', {}, ['日時', '店舗ID', '操作者', '操作', '対象', '内容'].map((t) => h('th', {}, t)))),
      h('tbody', {}, logs.map((log) => h('tr', {}, h('td', {}, formatDateTime(log.occurred_at)), h('td', {}, log.store_id ?? '—'),
        h('td', {}, log.actor), h('td', { class: 'mono' }, log.action),
        h('td', {}, [log.target_type, log.target_id].filter(Boolean).join(' #')), h('td', { class: 'mono' }, log.detail || ''))))))));
}

boot();
