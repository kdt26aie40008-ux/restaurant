// サーバー API の呼び出し。エラーは ApiError（日本語の message と hint を持つ）にそろえる。

const TIMEOUT_MS = 15000;

export class ApiError extends Error {
  constructor(status, code, message, hint = '', details = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.hint = hint;
    this.details = details;
  }
}

// ログイン切れなど、どの画面でも同じ対応をするエラーの処理を登録する。
const errorHooks = new Map();

export function onApiError(code, handler) {
  errorHooks.set(code, handler);
}

export async function api(method, path, { json, body, contentType } = {}) {
  try {
    return await request(method, path, { json, body, contentType });
  } catch (error) {
    const hook = errorHooks.get(error.code);
    if (hook) hook(error);
    throw error;
  }
}

async function request(method, path, { json, body, contentType }) {
  const headers = { 'X-Requested-With': 'fetch', Accept: 'application/json' };
  let payload = body;
  if (json !== undefined) {
    payload = JSON.stringify(json);
    headers['Content-Type'] = 'application/json';
  } else if (contentType) {
    headers['Content-Type'] = contentType;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let response;
  try {
    response = await fetch(path, { method, headers, body: payload, credentials: 'same-origin', signal: controller.signal });
  } catch (error) {
    const timedOut = error.name === 'AbortError';
    throw new ApiError(0, timedOut ? 'TIMEOUT' : 'NETWORK',
      timedOut ? 'サーバーからの応答がありませんでした。' : 'サーバーに接続できませんでした。',
      'サーバー（店内PC）が起動しているか、ネットワークにつながっているか確認してから、もう一度操作してください。');
  } finally {
    clearTimeout(timer);
  }

  let data = null;
  if ((response.headers.get('Content-Type') || '').includes('application/json')) {
    try {
      data = await response.json();
    } catch (error) {
      console.error('JSON の解析に失敗しました', error);
    }
  }
  if (!response.ok) {
    const e = data?.error;
    throw new ApiError(response.status, e?.code || `HTTP_${response.status}`,
      e?.message || 'サーバーでエラーが発生しました。',
      e?.hint || 'しばらく待ってから、もう一度操作してください。', e?.details ?? null);
  }
  return data;
}
