/** 支持引号、转义双引号和换行的轻量 CSV 解析，用于选择文件后的即时行预览。 */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false

  for (let i = 0; i < text.length; i += 1) {
    const char = text[i]
    const next = text[i + 1]

    if (quoted) {
      if (char === '"' && next === '"') {
        cell += '"'
        i += 1
      } else if (char === '"') {
        quoted = false
      } else {
        cell += char
      }
      continue
    }

    if (char === '"') {
      quoted = true
    } else if (char === ',') {
      row.push(cell)
      cell = ''
    } else if (char === '\n' || char === '\r') {
      if (char === '\r' && next === '\n') i += 1
      row.push(cell)
      rows.push(row)
      row = []
      cell = ''
    } else {
      cell += char
    }
  }

  if (cell.length > 0 || row.length > 0) {
    row.push(cell)
    rows.push(row)
  }
  return rows.filter((items) => items.some((item) => item.trim() !== ''))
}

export function normalizeHeader(value: string): string {
  return value.replace(/[\s_\-/\\.。:：（）()[\]]/g, '').toLowerCase()
}

const FIELD_BY_HEADER: Record<string, string> = {
  来源编号: 'source_id',
  源记录编号: 'source_id',
  sourceid: 'source_id',
  sourcerecordid: 'source_id',
  日志批次: 'batch_id',
  batchid: 'batch_id',
  实验编码: 'experiment_code',
  experimentcode: 'experiment_code',
  expcode: 'experiment_code',
  用户标识: 'user_id',
  userid: 'user_id',
  uid: 'user_id',
  分组名称: 'group_name',
  groupname: 'group_name',
  曝光时间: 'occurred_at',
  曝光日期: 'occurred_at',
  发生时间: 'occurred_at',
  事件时间: 'occurred_at',
  occurredat: 'occurred_at',
  eventtime: 'occurred_at',
  eventdate: 'occurred_at',
  timestamp: 'occurred_at',
}

export function csvRowsToObjects(matrix: string[][]): Array<Record<string, string>> {
  if (!matrix.length) return []
  const headers = matrix[0].map(normalizeHeader)
  return matrix.slice(1).map((cells, index) => {
    const item: Record<string, string> = { file_row: String(index + 2) }
    headers.forEach((header, columnIndex) => {
      const value = cells[columnIndex] ?? ''
      item[header] = value
      const canonical = FIELD_BY_HEADER[header]
      if (canonical) item[canonical] = value
    })
    return item
  })
}
