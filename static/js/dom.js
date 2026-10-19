// DOM 生成の小さな道具。利用者や DB 由来の文字列は必ず textContent で入れ、innerHTML は使わない（XSS 対策）。

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value === true) el.setAttribute(key, '');
    else el.setAttribute(key, String(value));
  }
  appendChildren(el, children);
  return el;
}

function appendChildren(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

// ---------- 通知 ----------

let toastTimer = null;

/** 画面下部に結果を表示する。actionLabel を渡すと「元に戻す」などの操作ボタンを付ける。 */
export function toast(message, { type = 'info', actionLabel, onAction, duration = 4000 } = {}) {
  const region = document.getElementById('toast-region');
  clear(region);
  const box = h('div', { class: `toast toast-${type}`, role: type === 'error' ? 'alert' : 'status' }, h('span', {}, message));
  if (actionLabel) {
    box.append(h('button', {
      type: 'button',
      class: 'btn btn-small btn-light',
      onclick: () => { clear(region); onAction(); },
    }, actionLabel));
  }
  region.append(box);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => clear(region), duration);
}

/** エラーを「何が起きたか」「どうすればよいか」の2段で表示する要素。 */
export function errorBox(error) {
  return h('div', { class: 'error-box', role: 'alert' },
    h('p', { class: 'error-message' }, error.message || 'エラーが発生しました。'),
    error.hint ? h('p', { class: 'error-hint' }, error.hint) : null);
}

// ---------- 確認ダイアログ ----------

/** window.confirm の代わり。Promise<boolean> を返す。 */
export function confirmDialog({ title, body, okLabel = 'OK', cancelLabel = 'キャンセル', danger = false, requireText = null }) {
  return new Promise((resolve) => {
    const root = document.getElementById('modal-root');
    const input = requireText
      ? h('input', { class: 'text-input', type: 'text', autocomplete: 'off', 'aria-label': `確認のため「${requireText}」と入力` })
      : null;
    const okButton = h('button', { type: 'button', class: `btn btn-large ${danger ? 'btn-danger' : 'btn-primary'}` }, okLabel);
    const cancelButton = h('button', { type: 'button', class: 'btn btn-large btn-light' }, cancelLabel);
    const close = (result) => {
      clear(root);
      document.removeEventListener('keydown', onKey);
      resolve(result);
    };
    const onKey = (event) => { if (event.key === 'Escape') close(false); };
    okButton.addEventListener('click', () => {
      if (input && input.value.trim() !== requireText) {
        input.focus();
        input.classList.add('is-invalid');
        return;
      }
      close(true);
    });
    cancelButton.addEventListener('click', () => close(false));
    document.addEventListener('keydown', onKey);
    clear(root).append(
      h('div', { class: 'modal-backdrop' },
        h('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'modal-title' },
          h('h2', { id: 'modal-title' }, title),
          h('div', { class: 'modal-body' }, body),
          input ? h('label', { class: 'field' }, h('span', {}, `確認のため「${requireText}」と入力してください`), input) : null,
          h('div', { class: 'modal-actions' }, cancelButton, okButton))),
    );
    (input || okButton).focus();
  });
}

/** 非同期処理中はボタンを無効化し「処理中」表示にする。連打による重複送信を防ぐ。 */
export async function withBusy(button, busyLabel, task) {
  if (button.disabled) return undefined;
  const label = button.textContent;
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  button.textContent = busyLabel;
  try {
    return await task();
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      button.textContent = label;
    }
  }
}

export function formatDateTime(iso) {
  // サーバーは日本時間（+09:00）で保存しているので、表示も文字列から組み立てる。
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso || '');
  return m ? `${m[1]}/${m[2]}/${m[3]} ${m[4]}:${m[5]}` : '';
}

export function todayInJapan() {
  const now = new Date(Date.now() + 9 * 3600 * 1000);
  return now.toISOString().slice(0, 10);
}
