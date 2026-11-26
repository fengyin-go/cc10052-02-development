<template>
  <section class="import-panel" data-testid="exposure-import-panel">
    <div class="import-head">
      <h3>CSV 补传、行预览与导入回执</h3>
      <p>
        选择文件后先按文件行预览；服务端逐行校验时间并生成回执。相同
        <code>(来源编号, 实验编码)</code>
        的原样记录幂等忽略，时间更正会移动有效曝光窗口并标记受影响计算作业。
      </p>
    </div>

    <div class="import-controls">
      <button class="btn primary" type="button" data-testid="open-csv-import" @click="triggerFilePicker">
        上传 / 补传 CSV
      </button>
      <label class="btn ghost file-button" data-testid="choose-csv-button">
        选择文件
        <input
          ref="fileInput"
          id="csv-file-input"
          name="csv_file"
          aria-label="选择曝光 CSV 文件"
          data-testid="exposure-csv-file"
          type="file"
          accept=".csv,text/csv"
          hidden
          @change="onFileChange"
        />
      </label>
      <span class="filename" data-testid="selected-filename">{{ fileName || '尚未选择文件' }}</span>
      <button
        class="btn primary"
        data-testid="submit-exposure-import"
        type="button"
        :disabled="submitting || !fileContent.trim()"
        @click="submitImport"
      >
        {{ submitting ? '提交解析中…' : '提交补传 / 更正' }}
      </button>
      <button class="btn" type="button" data-testid="refresh-receipts" @click="loadRecentReceipts">
        刷新回执列表
      </button>
    </div>

    <details class="csv-text-details">
      <summary>也可以直接粘贴 CSV 文本</summary>
      <textarea
        v-model="fileContent"
        class="csv-text"
        data-testid="csv-content"
        rows="6"
        placeholder="来源编号,日志批次,实验编码,用户标识,分组名称,曝光时间&#10;X7,B-001,EXP-001,u7,对照组,2026-11-25 10:00:00"
        @input="renderLocalPreview"
      ></textarea>
      <button class="btn" type="button" data-testid="preview-csv-text" @click="fetchServerPreview">
        服务端预览校验
      </button>
    </details>

    <div v-if="previewRows.length" class="preview-block" data-testid="file-preview">
      <div class="block-title">
        <h4>文件选择预览</h4>
        <span>共 {{ previewRows.length }} 个数据行；第 1 行为表头，数据行号从 2 开始</span>
      </div>
      <div class="table-scroll">
        <table class="data-table preview-table">
          <thead>
            <tr>
              <th>文件行号</th>
              <th>来源编号</th>
              <th>实验编码</th>
              <th>用户标识</th>
              <th>分组名称</th>
              <th>曝光时间</th>
              <th>预览校验</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in previewRows"
              :key="`preview-${row.file_row}`"
              :data-testid="`preview-row-${row.file_row}`"
              :data-file-row="row.file_row"
              :data-invalid="row.valid === false ? 'true' : 'false'"
              :class="{ 'row-rejected': row.valid === false }"
            >
              <td class="line-no">{{ row.file_row }}</td>
              <td>{{ cell(row, 'source_id', '来源编号') || '—' }}</td>
              <td>{{ cell(row, 'experiment_code', '实验编码') || '—' }}</td>
              <td>{{ cell(row, 'user_id', '用户标识') || '—' }}</td>
              <td>{{ cell(row, 'group_name', '分组名称') || '—' }}</td>
              <td>{{ cell(row, 'occurred_at', '曝光时间') || '—' }}</td>
              <td data-testid="preview-error">
                <span v-if="row.valid === false" class="error-text">{{ row.reason || (row.errors || []).join('；') }}</span>
                <span v-else-if="row.valid === true" class="ok-text">可提交</span>
                <span v-else class="muted-text">待服务端校验</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="reopen-bar">
      <label>
        导入编号
        <input
          v-model="reopenNo"
          data-testid="receipt-no-input"
          type="text"
          placeholder="例如 IMP-20261126-0001"
          @keyup.enter="reopenReceipt"
        />
      </label>
      <button class="btn" data-testid="reopen-receipt" type="button" @click="reopenReceipt">
        凭编号重开回执
      </button>
      <select
        v-if="recentReceipts.length"
        data-testid="recent-receipt-select"
        :value="receipt?.import_no ? String(receipt.import_no) : ''"
        @change="onSelectRecent"
      >
        <option value="">选择最近回执…</option>
        <option v-for="item in recentReceipts" :key="item.import_no" :value="item.import_no">
          {{ item.import_no }}｜{{ item.filename }}｜{{ item.status_label }}
        </option>
      </select>
    </div>

    <p v-if="errorMessage" class="error-text" data-testid="import-error">{{ errorMessage }}</p>
    <p v-if="actionMessage" class="ok-text" data-testid="import-action-message">{{ actionMessage }}</p>

    <section
      v-if="receipt"
      class="receipt-card"
      data-testid="import-receipt"
      :data-import-no="receipt.import_no"
      :data-rejected-count="receipt.rejected_count"
    >
      <header class="receipt-head">
        <div>
          <h4>导入回执 <span data-testid="receipt-import-no">{{ receipt.import_no }}</span></h4>
          <p>{{ receipt.filename }}｜{{ receipt.created_at }}</p>
          <p class="receipt-message" data-testid="receipt-message">{{ receipt.message }}</p>
        </div>
        <div class="receipt-actions">
          <button class="btn" type="button" data-testid="download-receipt" @click="downloadReceipt">
            下载回执 CSV
          </button>
          <button
            class="btn primary"
            type="button"
            data-testid="recalc-affected"
            :disabled="recalculating"
            @click="recalculateAll"
          >
            {{ recalculating ? '重算中…' : '重算全部受影响窗口' }}
          </button>
          <button class="btn ghost" type="button" data-testid="locate-rejected" @click="scrollToRejected">
            定位拒收行
          </button>
        </div>
      </header>

      <div class="count-grid">
        <span data-testid="receipt-total">总行数：{{ receipt.total_rows ?? receipt.rows.length }}</span>
        <span data-testid="receipt-accepted">新增：{{ receipt.accepted_count ?? 0 }}</span>
        <span data-testid="receipt-corrected">更正：{{ receipt.corrected_count ?? 0 }}</span>
        <span data-testid="receipt-duplicate">重复忽略：{{ receipt.duplicate_count ?? 0 }}</span>
        <span class="rejected-count" data-testid="receipt-rejected-count">
          拒收：{{ receipt.rejected_count ?? 0 }}
        </span>
      </div>

      <div v-if="rejectedRows.length" class="rejected-panel" data-testid="rejected-rows">
        <h5>拒收曝光明细（保留 CSV 文件行号）</h5>
        <table class="data-table">
          <thead>
            <tr><th>文件行号</th><th>来源编号</th><th>实验编码</th><th>原始时间</th><th>拒绝原因</th></tr>
          </thead>
          <tbody>
            <tr
              v-for="row in rejectedRows"
              :key="`reject-${row.row_id}`"
              :data-testid="`rejected-row-${row.file_row}`"
              class="row-rejected"
            >
              <td class="line-no">{{ row.file_row }}</td>
              <td>{{ row.source_id || '—' }}</td>
              <td>{{ row.experiment_code || '—' }}</td>
              <td>{{ row.occurred_at || '—' }}</td>
              <td class="error-text" data-testid="reject-reason">{{ row.reason || row.拒绝原因 }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="filter-tabs" role="tablist">
        <button
          type="button"
          :class="{ active: receiptFilter === 'all' }"
          data-testid="receipt-filter-all"
          @click="receiptFilter = 'all'"
        >
          全部逐行回执
        </button>
        <button
          type="button"
          :class="{ active: receiptFilter === 'rejected' }"
          data-testid="receipt-filter-rejected"
          @click="receiptFilter = 'rejected'"
        >
          只看拒收行（{{ rejectedRows.length }}）
        </button>
        <button
          type="button"
          :class="{ active: receiptFilter === 'corrected' }"
          data-testid="receipt-filter-corrected"
          @click="receiptFilter = 'corrected'"
        >
          只看更正/影响窗口
        </button>
      </div>

      <div class="table-scroll">
        <table class="data-table receipt-table" data-testid="receipt-table">
          <thead>
            <tr>
              <th>文件行号</th>
              <th>来源/实验</th>
              <th>解析后时间/窗口</th>
              <th>处理结果</th>
              <th>原因与原行关联</th>
              <th>受影响作业窗口</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in visibleReceiptRows"
              :key="row.row_id || `receipt-row-${row.file_row}`"
              :id="`receipt-row-${row.file_row}`"
              :data-testid="`receipt-row-${row.file_row}`"
              :data-file-row="row.file_row"
              :data-status="row.status"
              :data-rejected="row.rejected ? 'true' : 'false'"
              :data-corrected="row.corrected ? 'true' : 'false'"
              :class="{
                'row-rejected': row.rejected,
                'row-corrected': row.corrected,
                'row-duplicate': row.duplicate,
              }"
            >
              <td class="line-no">{{ row.file_row }}</td>
              <td>
                <div><strong>{{ row.source_id || '—' }}</strong></div>
                <div class="muted-text">{{ row.experiment_code || '—' }}</div>
                <div class="muted-text">{{ row.user_id }} / {{ row.group_name }}</div>
              </td>
              <td>
                <div>{{ row.normalized_time || row.occurred_at || '—' }}</div>
                <div class="muted-text">窗口：{{ row.window_key || '无法解析' }}</div>
              </td>
              <td>
                <span class="status-pill" :data-status-code="row.status">{{ row.status_label || row.status }}</span>
                <div v-if="row.counted" class="ok-text">计入当前有效曝光</div>
                <div v-else-if="row.duplicate" class="muted-text">不重复计数</div>
              </td>
              <td class="reason-cell">
                <div :class="{ 'error-text': row.rejected }">{{ row.reason || '—' }}</div>
                <div v-if="row.changed_fields?.length" class="correction-fields">
                  变更字段：{{ row.changed_fields.join('、') }}
                </div>
                <div v-if="row.original_import_no" class="origin-link">
                  原回执
                  <button type="button" class="link" data-testid="open-original-receipt" @click="openReceipt(row.original_import_no)">
                    {{ row.original_import_no }}
                  </button>
                  ｜原文件第 {{ row.original_file_row }} 行
                </div>
                <div v-if="row.follow_up_corrections?.length" class="follow-up-list" data-testid="follow-up-corrections">
                  <div v-for="item in row.follow_up_corrections" :key="item.receipt_row_id || item.import_no">
                    后续更正：
                    <button type="button" class="link" @click="openReceipt(item.import_no)">
                      {{ item.import_no }}
                    </button>
                    （第 {{ item.file_row }} 行，{{ item.old_window }} → {{ item.new_window }}）
                  </div>
                </div>
              </td>
              <td class="impact-cell">
                <template v-if="row.affected_jobs?.length">
                  <div
                    v-for="job in row.affected_jobs"
                    :key="`${row.row_id}-${job.job_id}-${job.effect}`"
                    class="impact-job"
                    :data-testid="`affected-job-${job.job_id}`"
                    :data-job-id="job.job_id"
                    :data-state="job.state"
                    :data-effective="job.effective ? 'true' : 'false'"
                  >
                    <RouterLink class="job-link" :to="job.href || metricHref(job.job_id)">
                      #{{ job.job_id }} {{ job.job_name }}｜{{ job.window_day || job.window }}｜{{ job.state_label }}
                    </RouterLink>
                    <button
                      type="button"
                      class="link"
                      :data-testid="`recalc-job-${job.job_id}`"
                      @click="recalculateJob(job.job_id)"
                    >
                      立即重算
                    </button>
                  </div>
                </template>
                <span v-else class="muted-text">无匹配的日期作业窗口</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  downloadExposureReceipt,
  getExposureReceipt,
  listExposureReceipts,
  previewExposureImport,
  recalculateExposureReceipt,
  recalculateMetricJob,
  submitExposureImport,
} from '@/api/backend-service'
import { csvRowsToObjects, parseCsv } from '@/utils/csv'

const route = useRoute()
const router = useRouter()

const fileInput = ref<HTMLInputElement | null>(null)
const fileName = ref('')
const fileContent = ref('')
const previewRows = ref<any[]>([])
const receipt = ref<any | null>(null)
const recentReceipts = ref<any[]>([])
const reopenNo = ref('')
const receiptFilter = ref<'all' | 'rejected' | 'corrected'>('all')
const submitting = ref(false)
const recalculating = ref(false)
const errorMessage = ref('')
const actionMessage = ref('')

const rejectedRows = computed<any[]>(() => receipt.value?.rows?.filter((row: any) => row.rejected) ?? [])
const visibleReceiptRows = computed<any[]>(() => {
  const rows = receipt.value?.rows ?? []
  if (receiptFilter.value === 'rejected') return rows.filter((row: any) => row.rejected)
  if (receiptFilter.value === 'corrected') {
    return rows.filter((row: any) => row.corrected || (row.affected_jobs?.length ?? 0) > 0)
  }
  return rows
})

function cell(row: any, ...keys: string[]): string {
  for (const key of keys) {
    if (row?.[key] !== undefined && row[key] !== null && String(row[key]) !== '') return String(row[key])
    if (row?.raw?.[key] !== undefined && String(row.raw[key]) !== '') return String(row.raw[key])
  }
  return ''
}

function metricHref(jobId: number | string): string {
  const receiptQuery = receipt.value?.import_no ? `&receipt=${encodeURIComponent(receipt.value.import_no)}` : ''
  return `/metric_compute?jobs=${jobId}${receiptQuery}`
}

function triggerFilePicker() {
  fileInput.value?.click()
}

function renderLocalPreview() {
  if (!fileContent.value.trim()) {
    previewRows.value = []
    return
  }
  try {
    previewRows.value = csvRowsToObjects(parseCsv(fileContent.value))
  } catch {
    // 本地预览失败时仍允许提交给服务端给出权威解析错误。
    previewRows.value = []
  }
}

async function onFileChange(event: Event) {
  errorMessage.value = ''
  actionMessage.value = ''
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  fileName.value = file.name
  fileContent.value = await file.text()
  renderLocalPreview()
  await fetchServerPreview()
}

async function fetchServerPreview() {
  if (!fileContent.value.trim()) return
  errorMessage.value = ''
  try {
    const result = await previewExposureImport({ filename: fileName.value, content: fileContent.value })
    if (!result.ok) {
      errorMessage.value = result.message || '预览失败'
      return
    }
    previewRows.value = result.rows ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '服务端预览失败'
  }
}

async function submitImport() {
  if (!fileContent.value.trim()) {
    errorMessage.value = '请先选择 CSV 文件或粘贴 CSV 内容'
    return
  }
  submitting.value = true
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    const result = await submitExposureImport({ filename: fileName.value || 'pasted.csv', content: fileContent.value })
    if (!result?.import_no) throw new Error(result?.message || '服务端未返回导入编号')
    receipt.value = result
    reopenNo.value = String(result.import_no)
    receiptFilter.value = 'all'
    actionMessage.value = result.message
    await loadRecentReceipts()
    if (result.rejected_count > 0) await nextTick(scrollToRejected)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交失败'
  } finally {
    submitting.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

async function loadRecentReceipts() {
  try {
    const result = await listExposureReceipts()
    recentReceipts.value = result.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回执列表读取失败'
  }
}

async function openReceipt(importNo: string) {
  const number = String(importNo || '').trim()
  if (!number) {
    errorMessage.value = '请输入导入编号'
    return
  }
  errorMessage.value = ''
  try {
    receipt.value = await getExposureReceipt(number)
    reopenNo.value = String(receipt.value.import_no ?? number)
    receiptFilter.value = 'all'
    actionMessage.value = `已重开回执 ${reopenNo.value}`
  } catch (error) {
    receipt.value = null
    errorMessage.value = error instanceof Error ? error.message : '回执读取失败'
  }
}

function reopenReceipt() {
  void openReceipt(reopenNo.value)
}

function onSelectRecent(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (value) void openReceipt(value)
}

async function downloadReceipt() {
  if (!receipt.value?.import_no) return
  errorMessage.value = ''
  try {
    await downloadExposureReceipt(String(receipt.value.import_no))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回执下载失败'
  }
}

async function recalculateAll() {
  if (!receipt.value?.import_no) return
  recalculating.value = true
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    const result = await recalculateExposureReceipt(String(receipt.value.import_no))
    receipt.value = result.receipt ?? await getExposureReceipt(String(receipt.value.import_no))
    actionMessage.value = result.message || '受影响窗口已重算'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重算失败'
  } finally {
    recalculating.value = false
  }
}

async function recalculateJob(jobId: number | string) {
  errorMessage.value = ''
  try {
    await recalculateMetricJob(Number(jobId), { import_no: receipt.value?.import_no })
    if (receipt.value?.import_no) receipt.value = await getExposureReceipt(String(receipt.value.import_no))
    actionMessage.value = `作业 #${jobId} 已重算，回执影响状态已刷新`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '作业重算失败'
  }
}

function scrollToRejected() {
  const element = document.querySelector<HTMLElement>('[data-testid="rejected-rows"]')
    ?? document.querySelector<HTMLElement>('tr[data-rejected="true"]')
  element?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  element?.classList.add('flash-rejected')
}

onMounted(async () => {
  await loadRecentReceipts()
  const queryNo = route.query.receipt ?? route.query.import_no ?? route.query.importNo
  if (typeof queryNo === 'string' && queryNo) await openReceipt(queryNo)
})

// 保留 router 引用，便于后续扩展为编程式跳转；模板主要使用 RouterLink。
void router
</script>

<style scoped>
.import-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
  margin: 12px 0 16px;
}
.import-head h3,
.receipt-card h4,
.rejected-panel h5 {
  margin: 0 0 6px;
}
.import-head p {
  color: var(--muted);
  font-size: 13px;
  margin: 0;
}
.import-controls,
.reopen-bar,
.receipt-actions,
.filter-tabs,
.count-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}
.import-controls {
  margin: 14px 0 10px;
}
.file-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.filename {
  color: var(--muted);
  font-size: 13px;
  word-break: break-all;
}
.csv-text-details {
  margin-bottom: 12px;
}
.csv-text {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  margin: 8px 0;
}
.block-title {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: baseline;
}
.block-title span {
  color: var(--muted);
  font-size: 12px;
}
.table-scroll {
  overflow-x: auto;
  margin: 8px 0 12px;
}
.preview-table,
.receipt-table {
  min-width: 860px;
}
.line-no {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  white-space: nowrap;
}
.row-rejected td {
  background: #fff1f0;
}
.row-corrected td {
  background: #eef6ff;
}
.row-duplicate td {
  background: #f8fafc;
  color: #64748b;
}
.error-text {
  color: #b42318;
  font-size: 12px;
}
.ok-text {
  color: #067647;
  font-size: 12px;
}
.muted-text {
  color: var(--muted);
  font-size: 12px;
}
.reopen-bar {
  margin: 12px 0;
}
.reopen-bar label {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
}
.reopen-bar input,
.reopen-bar select {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 7px 10px;
  min-width: 260px;
}
.receipt-card {
  border-top: 2px solid var(--brand);
  margin-top: 14px;
  padding-top: 14px;
}
.receipt-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  flex-wrap: wrap;
}
.receipt-head p {
  margin: 3px 0;
  color: var(--muted);
  font-size: 12px;
}
.receipt-message {
  color: #1f2937 !important;
  font-size: 13px !important;
}
.count-grid {
  margin: 12px 0;
  font-size: 13px;
}
.count-grid span {
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 4px 10px;
}
.count-grid .rejected-count {
  background: #fff1f0;
  border-color: #fda29b;
  color: #b42318;
  font-weight: 600;
}
.rejected-panel {
  border: 1px solid #fda29b;
  border-radius: 8px;
  padding: 10px;
  margin: 10px 0;
  background: #fffafa;
}
.filter-tabs {
  margin: 12px 0 8px;
}
.filter-tabs button.active {
  border-color: var(--brand);
  color: var(--brand);
  font-weight: 600;
}
.status-pill {
  display: inline-block;
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 12px;
  background: #eef2f6;
}
.status-pill[data-status-code='rejected'] {
  background: #fee4e2;
  color: #b42318;
}
.status-pill[data-status-code='corrected'] {
  background: #dbeafe;
  color: #1d4ed8;
}
.status-pill[data-status-code='accepted'] {
  background: #dcfae6;
  color: #067647;
}
.status-pill[data-status-code='duplicate'] {
  background: #f2f4f7;
  color: #475467;
}
.reason-cell {
  min-width: 220px;
  font-size: 12px;
}
.correction-fields,
.origin-link,
.follow-up-list {
  margin-top: 4px;
  color: #1d4ed8;
}
.follow-up-list {
  display: grid;
  gap: 2px;
}
.impact-cell {
  min-width: 260px;
}
.impact-job {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 5px 0;
  border-bottom: 1px dashed var(--border);
  font-size: 12px;
}
.impact-job[data-state='effective'] {
  color: #067647;
}
.impact-job[data-state='pending_recalculation'] {
  color: #b54708;
  font-weight: 600;
}
.job-link {
  color: inherit;
}
.flash-rejected {
  outline: 3px solid #f79009;
  outline-offset: 2px;
  border-radius: 6px;
}
</style>
