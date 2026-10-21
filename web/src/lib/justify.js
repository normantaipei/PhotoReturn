// Justified rows：每列等高、剛好填滿寬度（最後一列不放大）
export function justify(items, width, target, gap) {
  const rows = [];
  let row = [];
  let arSum = 0;
  const flush = (last) => {
    if (!row.length) return;
    const fill = (width - gap * (row.length - 1)) / arSum;
    const h = last ? Math.min(target, fill) : fill;
    rows.push(row.map(({ item, ar }) => ({ item, w: Math.floor(ar * h), h: Math.round(h) })));
    row = [];
    arSum = 0;
  };
  for (const item of items) {
    const ar = item.photo.w && item.photo.h ? item.photo.w / item.photo.h : 1.5;
    row.push({ item, ar });
    arSum += ar;
    if (arSum * target + gap * (row.length - 1) >= width) flush(false);
  }
  flush(true);
  return rows;
}
