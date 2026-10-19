// 画面ロジックのテスト: node --test tests/js
// （仕様のテスト項目 1〜9, 20 の画面側に対応）

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  MAX_QUANTITY, PLACEHOLDER_IMAGE, allergenConflicts, allergenNames, cartCount, cartLines, cartTotal, changeQuantity, filterByCategory, formatYen,
  getQuantity, imageSrc, newRequestKey, removeLine, searchProducts, setQuantity, splitAmount, summaryCount, validatePartySize,
} from '../../static/js/logic.js';

const products = [
  { id: 'P001', category_id: 1, name: 'ハンバーガー', price: 680, is_available: true, image_path: 'images/products/P001.jpg' },
  { id: 'P006', category_id: 2, name: 'ポテト', price: 320, is_available: true, image_path: '' },
  { id: 'P011', category_id: 3, name: 'コーラ', price: 250, is_available: true, image_path: '' },
  { id: 'P010', category_id: 2, name: '季節限定スープ', price: 380, is_available: false, image_path: '' },
];
const byId = new Map(products.map((p) => [p.id, p]));

test('正しい人数を受け付ける', () => {
  assert.deepEqual(validatePartySize('3', 30), { ok: true, value: 3 });
  assert.deepEqual(validatePartySize(' ３ ', 30), { ok: true, value: 3 });
  assert.deepEqual(validatePartySize('30', 30), { ok: true, value: 30 });
});

test('空欄・0・負数・小数・文字列・多すぎる人数を拒否する', () => {
  const cases = {
    '': '入力されていません',
    '0': '1人以上',
    '-1': 'マイナス',
    'ー２': 'マイナス',
    '1.5': '小数',
    'abc': '数字で',
    '2人': '数字で',
    '31': '多すぎます',
  };
  for (const [input, fragment] of Object.entries(cases)) {
    const result = validatePartySize(input, 30);
    assert.equal(result.ok, false, input);
    assert.match(result.message, new RegExp(fragment), input);
    assert.ok(result.hint);
  }
  assert.equal(validatePartySize(null, 30).ok, false);
});

test('カテゴリで商品を絞り込める', () => {
  assert.deepEqual(filterByCategory(products, 2).map((p) => p.id), ['P006', 'P010']);
  assert.deepEqual(filterByCategory(products, 99), []);
});

test('カートに追加・数量の増減ができ、0で削除される', () => {
  let cart = [];
  cart = changeQuantity(cart, 'P001', 1);
  cart = changeQuantity(cart, 'P001', 1);
  cart = changeQuantity(cart, 'P006', 1);
  assert.deepEqual(cart, [{ productId: 'P001', quantity: 2 }, { productId: 'P006', quantity: 1 }]);
  cart = changeQuantity(cart, 'P001', -1);
  assert.equal(getQuantity(cart, 'P001'), 1);
  cart = changeQuantity(cart, 'P006', -1);
  assert.equal(getQuantity(cart, 'P006'), 0);
  assert.equal(cart.length, 1);
});

test('数量は0未満にも上限超えにもならない', () => {
  let cart = changeQuantity([], 'P001', -1);
  assert.deepEqual(cart, []);
  cart = setQuantity(cart, 'P001', -5);
  assert.deepEqual(cart, []);
  cart = setQuantity(cart, 'P001', 1000);
  assert.equal(getQuantity(cart, 'P001'), MAX_QUANTITY);
  cart = setQuantity(cart, 'P001', 'abc');
  assert.deepEqual(cart, []);
});

test('小計と合計が正しい', () => {
  const cart = [{ productId: 'P001', quantity: 2 }, { productId: 'P006', quantity: 3 }, { productId: 'P011', quantity: 1 }];
  const lines = cartLines(cart, byId);
  assert.deepEqual(lines.map((l) => l.subtotal), [1360, 960, 250]);
  assert.equal(cartTotal(lines), 2570);
  assert.equal(cartCount(cart), 6);
  assert.equal(cartTotal(cartLines(removeLine(cart, 'P006'), byId)), 1610);
});

test('販売停止・削除済み商品はカートで問題として表示される', () => {
  const lines = cartLines([{ productId: 'P010', quantity: 1 }, { productId: 'GONE', quantity: 1 }], byId);
  assert.equal(lines[0].problem, '販売停止中');
  assert.ok(lines[1].problem);
});

test('1人あたりの金額が正しく、人数0以下では除算しない', () => {
  assert.deepEqual(splitAmount(3000, 3), { perPerson: 1000, remainder: 0 });
  assert.deepEqual(splitAmount(1000, 3), { perPerson: 333, remainder: 1 });
  for (const size of [0, -1, 1.5, NaN, undefined]) assert.equal(splitAmount(1000, size), null);
});

test('商品検索はひらがな・全角・大文字小文字の違いを無視する', () => {
  const list = [
    { id: 'A', name: 'ポテト', description: '' },
    { id: 'B', name: 'チーズバーガー', description: 'チェダー入り' },
    { id: 'C', name: 'Cola', description: '' },
  ];
  assert.deepEqual(searchProducts(list, 'ぽてと').map((p) => p.id), ['A']);
  assert.deepEqual(searchProducts(list, 'ﾁｰｽﾞ').map((p) => p.id), ['B']);
  assert.deepEqual(searchProducts(list, 'ちぇだー').map((p) => p.id), ['B']);
  assert.deepEqual(searchProducts(list, 'ＣＯＬＡ').map((p) => p.id), ['C']);
  assert.deepEqual(searchProducts(list, '  '), []);
  assert.deepEqual(searchProducts(list, 'ラーメン'), []);
  assert.equal(summaryCount([{ quantity: 2 }, { quantity: 3 }]), 5);
});

test('選んだアレルゲンを含む商品はカートで問題として表示される', () => {
  const map = new Map([
    ['A', { id: 'A', name: 'プリン', price: 380, is_available: true, allergens: ['egg', 'milk'] }],
    ['B', { id: 'B', name: 'ポテト', price: 320, is_available: true, allergens: [] }],
  ]);
  assert.deepEqual(allergenConflicts(map.get('A'), ['milk', 'wheat']), ['乳']);
  assert.deepEqual(allergenConflicts(map.get('B'), ['milk']), []);
  assert.deepEqual(allergenConflicts(map.get('A'), []), []);
  const lines = cartLines([{ productId: 'A', quantity: 1 }, { productId: 'B', quantity: 1 }], map, ['egg']);
  assert.match(lines[0].problem, /卵/);
  assert.equal(lines[1].problem, null);
  assert.deepEqual(allergenNames(['milk', 'egg', 'unknown']), ['乳', '卵']);
});

test('画像パスが無い商品は仮画像になる', () => {
  assert.equal(imageSrc(products[0]), '/images/products/P001.jpg');
  assert.equal(imageSrc(products[1]), PLACEHOLDER_IMAGE);
});

test('金額は円単位でカンマ区切り', () => {
  assert.equal(formatYen(1234567), '1,234,567円');
  assert.equal(formatYen(0), '0円');
});

test('注文識別キーは毎回異なる32桁の16進数', () => {
  const a = newRequestKey();
  const b = newRequestKey();
  assert.match(a, /^[0-9a-f]{32}$/);
  assert.notEqual(a, b);
});
