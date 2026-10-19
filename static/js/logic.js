// 画面に依存しない計算・検証ロジック（node --test でテストする）。
// 金額の最終的な計算はサーバーが行う。ここでの計算は画面表示用。

export const MAX_QUANTITY = 99;
export const PLACEHOLDER_IMAGE = '/images/placeholder.svg';

const FULLWIDTH = { '－': '-', 'ー': '-', '−': '-', '．': '.' };

export function normalizeDigits(text) {
  return String(text)
    .replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0))
    .replace(/[－ー−．]/g, (c) => FULLWIDTH[c]);
}

/** 利用人数の検証。サーバー側の validate_party_size と同じ規則。 */
export function validatePartySize(raw, maxPartySize) {
  const hint = `1〜${maxPartySize}の整数（例: 2）で入力してください。`;
  const text = normalizeDigits(raw ?? '').trim();
  if (text === '') return { ok: false, message: '利用人数が入力されていません。', hint };
  if (/^-\s*\d+(\.\d+)?$/.test(text)) return { ok: false, message: '利用人数にマイナスの値は入力できません。', hint };
  if (/^-?\d*\.\d+$|^-?\d+\.\d*$/.test(text)) return { ok: false, message: '利用人数に小数は入力できません。', hint };
  if (!/^\d+$/.test(text)) return { ok: false, message: '利用人数は数字で入力してください。', hint };
  const value = Number.parseInt(text, 10);
  if (value === 0) return { ok: false, message: '利用人数は1人以上にしてください。', hint };
  if (value > maxPartySize) {
    return {
      ok: false,
      message: `利用人数が多すぎます（最大${maxPartySize}人）。`,
      hint: '入力内容を確認してください。大人数の場合はお店の責任者に相談してください。',
    };
  }
  return { ok: true, value };
}

export function formatYen(amount) {
  return `${Number(amount).toLocaleString('ja-JP')}円`;
}

// ---------- カート ----------
// カートは [{ productId, quantity }] の配列（追加した順に並ぶ）。数量が0以下になった商品は取り除く。

export function getQuantity(cart, productId) {
  return cart.find((line) => line.productId === productId)?.quantity ?? 0;
}

export function setQuantity(cart, productId, quantity) {
  const next = Math.max(0, Math.min(MAX_QUANTITY, Math.trunc(Number(quantity) || 0)));
  if (next === 0) return cart.filter((line) => line.productId !== productId);
  if (cart.some((line) => line.productId === productId)) {
    return cart.map((line) => (line.productId === productId ? { ...line, quantity: next } : line));
  }
  return [...cart, { productId, quantity: next }];
}

export function changeQuantity(cart, productId, delta) {
  return setQuantity(cart, productId, getQuantity(cart, productId) + delta);
}

export function removeLine(cart, productId) {
  return cart.filter((line) => line.productId !== productId);
}

// ---------- アレルギー ----------
// サーバーの app/allergens.py と同じ定義（表示順）。

export const ALLERGENS = [
  ['egg', '卵'], ['milk', '乳'], ['wheat', '小麦'], ['shrimp', 'えび'], ['crab', 'かに'],
  ['buckwheat', 'そば'], ['peanut', '落花生'], ['walnut', 'くるみ'], ['soy', '大豆'], ['sesame', 'ごま'],
];
const ALLERGEN_NAMES = new Map(ALLERGENS);

export function allergenNames(codes) {
  return (codes || []).map((code) => ALLERGEN_NAMES.get(code)).filter(Boolean);
}

/** 商品に含まれる、お客様が選んだアレルゲンの表示名。 */
export function allergenConflicts(product, avoid) {
  if (!avoid?.length) return [];
  return allergenNames((product.allergens || []).filter((code) => avoid.includes(code)));
}

/** 表示用の明細。商品がメニューから消えた・販売停止・選んだアレルゲンを含む行は problem に理由を入れる。 */
export function cartLines(cart, productsById, avoid = []) {
  return cart.map(({ productId, quantity }) => {
    const product = productsById.get(productId);
    if (!product) {
      return { productId, quantity, name: '（メニューにない商品）', unitPrice: 0, subtotal: 0, problem: 'メニューから削除されました' };
    }
    const conflicts = allergenConflicts(product, avoid);
    let problem = null;
    if (!product.is_available) problem = '販売停止中';
    else if (conflicts.length) problem = `選んだアレルギー（${conflicts.join('・')}）を含む`;
    return {
      productId,
      quantity,
      name: product.name,
      unitPrice: product.price,
      subtotal: product.price * quantity,
      problem,
    };
  });
}

export function cartTotal(lines) {
  return lines.reduce((sum, line) => sum + line.subtotal, 0);
}

export function cartCount(cart) {
  return cart.reduce((sum, line) => sum + line.quantity, 0);
}

// ---------- 会計 ----------

/** 1人あたりの参考金額（1円未満切り捨て）。人数が正の整数でなければ null を返し、除算しない。 */
export function splitAmount(total, partySize) {
  if (!Number.isInteger(partySize) || partySize <= 0) return null;
  const perPerson = Math.floor(total / partySize);
  return { perPerson, remainder: total - perPerson * partySize };
}

// ---------- メニュー ----------

export function filterByCategory(products, categoryId) {
  return products.filter((product) => product.category_id === categoryId);
}

/** 検索用に表記をそろえる（全角半角・大文字小文字・ひらがな/カタカナの違いを無視）。 */
export function normalizeForSearch(text) {
  return String(text ?? '')
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[ぁ-ゖ]/g, (c) => String.fromCharCode(c.charCodeAt(0) + 0x60))
    .replace(/\s+/g, '');
}

/** 商品名・説明に検索語を含む商品（空なら空配列）。 */
export function searchProducts(products, query) {
  const needle = normalizeForSearch(query);
  if (!needle) return [];
  return products.filter((p) => normalizeForSearch(`${p.name}${p.description || ''}`).includes(needle));
}

/** 伝票の注文内容を商品ごとにまとめた合計点数。 */
export function summaryCount(items) {
  return items.reduce((sum, item) => sum + item.quantity, 0);
}

/** 画像パスが無い商品は仮画像を使う。読み込み失敗時の差し替えは画面側で行う。 */
export function imageSrc(product) {
  return product.image_path ? `/${product.image_path}` : PLACEHOLDER_IMAGE;
}

// ---------- 注文の識別キー ----------
// crypto.randomUUID は http://（LAN の IP アドレス）では使えないため getRandomValues で作る。

export function newRequestKey(cryptoImpl = globalThis.crypto) {
  const bytes = new Uint8Array(16);
  cryptoImpl.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}
