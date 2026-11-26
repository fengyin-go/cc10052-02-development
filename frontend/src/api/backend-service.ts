import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export type CsvImportPayload = {
  filename?: string
  content?: string
  rows?: Record<string, unknown>[]
  import_no?: string
}

export async function previewExposureImport(payload: CsvImportPayload): Promise<any> {
  const response = await request('/api/exposure_log/imports/preview', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error('CSV 预览解析失败')
  return await response.json()
}

export async function submitExposureImport(payload: CsvImportPayload): Promise<any> {
  const response = await request('/api/exposure_log/imports', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    let detail = '曝光补传提交失败'
    try {
      const body = await response.json()
      detail = body?.message ?? detail
    } catch {
      // 保留默认错误文案。
    }
    throw new Error(detail)
  }
  return await response.json()
}

export async function listExposureReceipts(): Promise<any> {
  const response = await request('/api/exposure_log/imports')
  if (!response.ok) throw new Error('导入回执列表读取失败')
  return await response.json()
}

export async function getExposureReceipt(importNo: string): Promise<any> {
  const response = await request(`/api/exposure_log/imports/${encodeURIComponent(importNo)}`)
  if (!response.ok) throw new Error(`导入编号 ${importNo} 的回执不存在或无法读取`)
  return await response.json()
}

export async function downloadExposureReceipt(importNo: string): Promise<void> {
  const response = await request(
    `/api/exposure_log/imports/${encodeURIComponent(importNo)}/download?format=csv`,
  )
  if (!response.ok) throw new Error('导入回执下载失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `receipt-${importNo}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function recalculateExposureReceipt(importNo: string): Promise<any> {
  const response = await request(
    `/api/exposure_log/imports/${encodeURIComponent(importNo)}/recalculate`,
    { method: 'POST', body: JSON.stringify({}) },
  )
  if (!response.ok) throw new Error('受影响窗口重算失败')
  return await response.json()
}

export async function recalculateMetricJob(jobId: number, payload: Record<string, unknown> = {}): Promise<any> {
  const response = await request(`/api/metric_compute/${jobId}/recalculate`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error('指标窗口重算失败')
  return await response.json()
}

export async function getMetricRevisions(jobId: number): Promise<any> {
  const response = await request(`/api/metric_compute/${jobId}/revisions`)
  if (!response.ok) throw new Error('结果修订记录读取失败')
  return await response.json()
}

export async function getMetricDenominator(jobId: number): Promise<any> {
  const response = await request(`/api/metric_compute/${jobId}/denominator`)
  if (!response.ok) throw new Error('分母回执引用读取失败')
  return await response.json()
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}
