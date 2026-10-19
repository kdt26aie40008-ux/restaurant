// 店舗アカウントのログイン・パスワード再確認の画面部品（注文画面と店舗管理画面で共通）。

import { api } from './api.js';
import { clear, errorBox, h, withBusy } from './dom.js';

export function storeLoginCard({ title, lead, admin = false, onSuccess }) {
  const email = h('input', { type: 'email', class: 'text-input', autocomplete: 'username', inputmode: 'email', maxlength: 254, required: true });
  const password = h('input', { type: 'password', class: 'text-input', autocomplete: 'current-password', maxlength: 128, required: true });
  const errorArea = h('div', { 'aria-live': 'assertive' });
  const button = h('button', { type: 'submit', class: 'btn btn-primary btn-large btn-block' }, 'ログイン');
  const form = h('form', { class: 'stack', novalidate: true },
    h('label', { class: 'field' }, h('span', {}, 'メールアドレス'), email),
    h('label', { class: 'field' }, h('span', {}, 'パスワード'), password),
    errorArea, button);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    clear(errorArea);
    if (!email.value.trim() || !password.value) {
      errorArea.append(errorBox({ message: 'メールアドレスとパスワードを入力してください。', hint: '' }));
      return;
    }
    withBusy(button, '確認中…', async () => {
      try {
        const result = await api('POST', '/api/auth/store/login', { json: { email: email.value, password: password.value, admin } });
        password.value = '';
        await onSuccess(result);
      } catch (error) {
        password.value = '';
        clear(errorArea).append(errorBox(error));
        password.focus();
      }
    });
  });
  const card = h('section', { class: 'card login-card stack' },
    h('h1', { class: 'page-title' }, title),
    lead ? h('p', { class: 'lead' }, lead) : null,
    form,
    h('p', { class: 'help' }, h('a', { href: '/account' }, '初めて設定する方・パスワードを忘れた方はこちら')));
  setTimeout(() => email.focus(), 0);
  return card;
}

export function reauthCard({ onSuccess, onLogout }) {
  const password = h('input', { type: 'password', class: 'text-input', autocomplete: 'current-password', maxlength: 128 });
  const errorArea = h('div', { 'aria-live': 'assertive' });
  const button = h('button', { type: 'submit', class: 'btn btn-primary btn-large btn-block' }, '確認する');
  const form = h('form', { class: 'stack', novalidate: true },
    h('label', { class: 'field' }, h('span', {}, 'パスワード'), password), errorArea, button);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    withBusy(button, '確認中…', async () => {
      try {
        await api('POST', '/api/auth/store/reauth', { json: { password: password.value } });
        password.value = '';
        await onSuccess();
      } catch (error) {
        password.value = '';
        if (error.code === 'STORE_LOGIN_REQUIRED') {
          onLogout();
          return;
        }
        clear(errorArea).append(errorBox(error));
      }
    });
  });
  setTimeout(() => password.focus(), 0);
  return h('section', { class: 'card login-card stack' },
    h('h1', { class: 'page-title' }, 'パスワードの再確認'),
    h('p', { class: 'lead' }, '管理機能を使うため、パスワードをもう一度入力してください（15分ごとに確認します）。'),
    form);
}
