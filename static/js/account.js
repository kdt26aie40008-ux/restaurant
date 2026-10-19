// 店舗アカウントのパスワード設定（初回・忘れたとき）。
// 1. メールアドレス → 2. メールに届いた認証コード → 3. 新しいパスワード
// 認証コードの確認後に受け取るワンタイムトークンはメモリ上だけに持ち、保存しない。

import { api } from './api.js';
import { clear, errorBox, h, toast, withBusy } from './dom.js';

const MIN_LENGTH = 10;
const viewRoot = document.getElementById('view');
const flow = { email: '', token: null };

function card(title, ...children) {
  return h('section', { class: 'card login-card stack' }, h('p', { class: 'eyebrow' }, 'パスワードの設定'), h('h1', { class: 'page-title' }, title), children);
}

function steps(current) {
  const labels = ['メールアドレス', '認証コード', '新しいパスワード'];
  return h('ol', { class: 'steps', 'aria-label': '手順' }, labels.map((label, index) =>
    h('li', { class: index + 1 === current ? 'is-current' : index + 1 < current ? 'is-done' : '', 'aria-current': index + 1 === current ? 'step' : undefined },
      `${index + 1}. ${label}`)));
}

function formWith(button, fields, onSubmit) {
  const form = h('form', { class: 'stack', novalidate: true }, fields, button);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    withBusy(button, '処理中…', onSubmit);
  });
  return form;
}

// ---------- 1. メールアドレス ----------

function renderEmailStep() {
  const email = h('input', { type: 'email', class: 'text-input', autocomplete: 'username', inputmode: 'email', maxlength: 254, value: flow.email });
  const errorArea = h('div', { 'aria-live': 'assertive' });
  const button = h('button', { type: 'submit', class: 'btn btn-primary btn-large btn-block' }, '認証コードを送る');
  clear(viewRoot).append(card('メールアドレスの確認',
    steps(1),
    h('p', { class: 'lead' }, '店舗アカウントに登録したメールアドレスを入力してください。8桁の認証コードをお送りします。'),
    formWith(button, [h('label', { class: 'field' }, h('span', {}, 'メールアドレス'), email), errorArea], async () => {
      clear(errorArea);
      try {
        await api('POST', '/api/auth/store/request-code', { json: { email: email.value } });
        flow.email = email.value.trim();
        renderCodeStep();
      } catch (error) {
        errorArea.append(errorBox(error));
      }
    })));
  email.focus();
}

// ---------- 2. 認証コード ----------

function renderCodeStep() {
  const code = h('input', { type: 'text', class: 'text-input code-input', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 8, pattern: '[0-9]{8}' });
  const errorArea = h('div', { 'aria-live': 'assertive' });
  const button = h('button', { type: 'submit', class: 'btn btn-primary btn-large btn-block' }, '確認する');
  const resend = h('button', { type: 'button', class: 'btn btn-light' }, '認証コードを再送信');
  resend.addEventListener('click', () => withBusy(resend, '送信中…', async () => {
    try {
      await api('POST', '/api/auth/store/request-code', { json: { email: flow.email } });
      toast('登録されているメールアドレスであれば、新しい認証コードを送信しました（前のコードは使えなくなります）。', { type: 'success', duration: 7000 });
    } catch (error) {
      clear(errorArea).append(errorBox(error));
    }
  }));
  clear(viewRoot).append(card('認証コードの入力',
    steps(2),
    h('p', { class: 'lead' }, `${flow.email} が登録されている場合、8桁の認証コードを送信しました。メールに書かれた数字を入力してください（有効期限15分）。`),
    h('p', { class: 'help' }, 'メールが届かない場合は、迷惑メールフォルダを確認するか、1分ほど待ってから再送信してください。招待メールのコードは24時間有効です。'),
    formWith(button, [h('label', { class: 'field' }, h('span', {}, '認証コード（8桁）'), code), errorArea], async () => {
      clear(errorArea);
      const value = code.value.replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0)).replace(/\s/g, '');
      if (!/^\d{8}$/.test(value)) {
        errorArea.append(errorBox({ message: '認証コードは8桁の数字です。', hint: 'メールに書かれた数字を入力してください。' }));
        return;
      }
      try {
        const result = await api('POST', '/api/auth/store/verify-code', { json: { email: flow.email, code: value } });
        flow.token = result.token;
        renderPasswordStep();
      } catch (error) {
        code.value = '';
        errorArea.append(errorBox(error));
      }
    }),
    h('div', { class: 'actions-inline' }, resend,
      h('button', { type: 'button', class: 'btn btn-light', onclick: renderEmailStep }, 'メールアドレスを入力し直す'))));
  code.focus();
}

// ---------- 3. 新しいパスワード ----------

function renderPasswordStep() {
  const password = h('input', { type: 'password', class: 'text-input', autocomplete: 'new-password', minlength: MIN_LENGTH, maxlength: 128 });
  const confirm = h('input', { type: 'password', class: 'text-input', autocomplete: 'new-password', minlength: MIN_LENGTH, maxlength: 128 });
  const errorArea = h('div', { 'aria-live': 'assertive' });
  const button = h('button', { type: 'submit', class: 'btn btn-primary btn-large btn-block' }, 'パスワードを設定する');
  clear(viewRoot).append(card('新しいパスワード',
    steps(3),
    h('ul', { class: 'help rules' },
      h('li', {}, `${MIN_LENGTH}文字以上（長いほど安全です。単語をいくつか組み合わせた文がおすすめ）`),
      h('li', {}, 'メールアドレスや、よく使われるパスワードは使えません'),
      h('li', {}, 'ほかのサービスと同じパスワードは使わないでください')),
    h('p', { class: 'help' }, 'この画面の有効期限は10分です。'),
    formWith(button, [
      h('label', { class: 'field' }, h('span', {}, '新しいパスワード'), password),
      h('label', { class: 'field' }, h('span', {}, '確認のためもう一度'), confirm),
      errorArea,
    ], async () => {
      clear(errorArea);
      if (password.value.length < MIN_LENGTH) {
        errorArea.append(errorBox({ message: `パスワードは${MIN_LENGTH}文字以上にしてください。`, hint: '' }));
        return;
      }
      if (password.value !== confirm.value) {
        errorArea.append(errorBox({ message: '2つのパスワードが一致しません。', hint: 'もう一度入力してください。' }));
        confirm.value = '';
        return;
      }
      try {
        await api('POST', '/api/auth/store/set-password', { json: { token: flow.token, password: password.value } });
        password.value = '';
        confirm.value = '';
        flow.token = null;
        renderDone();
      } catch (error) {
        errorArea.append(errorBox(error));
        if (error.code === 'TOKEN_INVALID') {
          errorArea.append(h('button', { type: 'button', class: 'btn btn-primary', onclick: renderEmailStep }, '最初からやり直す'));
        }
      }
    })));
  password.focus();
}

function renderDone() {
  clear(viewRoot).append(card('パスワードを設定しました',
    h('p', { class: 'notice success' }, 'この端末ではログインした状態になりました。次回からはメールアドレスとこのパスワードでログインできます。ほかの端末でログイン中だった場合は、安全のためログアウトしました。登録メールアドレスに変更のお知らせを送りました。'),
    h('div', { class: 'actions-inline' },
      h('a', { class: 'btn btn-primary btn-large', href: '/' }, '注文画面へ'),
      h('a', { class: 'btn btn-light btn-large', href: '/admin' }, '店舗管理画面へ'))));
}

renderEmailStep();
