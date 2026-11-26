<template>
  <section class="impact-panel" data-testid="metric-impact-panel" :data-receipt="receiptNo || ''">
    <header class="impact-head">
      <div>
        <h3>导入更正影响的作业窗口</h3>
        <p>
          这里核对该次分母实际引用的导入回执与有效行；旧结果保留为可追踪修订，重算后显示“已生效”。
        </p>
      </div>
      <button class="btn primary" type="button" data-testid="recalc-all-impact" :disabled="working" @click="recalculateAll">
        {{ working ? '重算中…' : '一键重算列出的窗口' }}
      </button>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="impact-error">{{ errorMessage }}</p>

    <article
      v-for="detail in details"
      :key="detail.id"
      class="job-impact-card"
      :data-testid="`impact-job-card-${detail.id}`"
      :data-job-id="detail.id"
      :data-needs-recalc="detail.needs_recalculation ? 'true' : 'false'"
    >
      <div class="job-title-row">
        <div>
          <h4>#{{ detail.id }} {{ detail.作业名称 }}</h4>
          <p>{{ detail.指标编码 }}｜{{ detail.实验编码 || '全部实验' }}｜窗口：{{ detail.计算窗口 }}</p>
        </div>
        <div class="job-state">
          <span class="state-pill" data-testid="impact-job-state" :data-state="detail.recalc_status || detail.status">
            {{ detail.recalc_status || detail.status }}
          </span>
          <button class="btn" type="button" :data-testid="`impact-recalc-${detail.id}`" @click="recalculateOne(Number(detail.id))">
            重算此窗口
          </button>
        </div>
      </div>

      <div v-if="detail.current_revision_no" class="current-revision" data-testid="current-revision">
        当前修订：<strong>{{ detail.current_revision_no }}</strong>
        <span v-if="detail.结果修订状态">｜{{ detail.结果修订状态 }}</span>
      </div>

      <div v-if="denominators[Number(detail.id)]" class="denominator-box" data-testid="denominator-refs">
        <div class="denominator-count">
          当前有效曝光分母：
          <strong>{{ denominators[Number(detail.id)].valid_exposure_count }}</strong>
          <span class="muted-text">
            （在线 {{ denominators[Number(detail.id)].source_type_counts?.online ?? 0 }} /
            CSV {{ denominators[Number(detail.id)].source_type_counts?.csv ?? 0 }}）
          </span>
        </div>
        <div class="group-dist">
          分组：
          <span v-for="(count, group) in denominators[Number(detail.id)].分组分布" :key="String(group)" class="group-chip">
            {{ group }}={{ count }}
          </span>
        </div>

        <table class="data-table refs-table">
          <thead>
            <tr><th>引用导入回执</th><th>有效 CSV 行/来源</th><th>有效曝光行</th></tr>
          </thead>
          <tbody>
            <tr v-for="ref in denominators[Number(detail.id)].receipt_refs" :key="ref.import_no" :data-testid="`denom-receipt-${ref.import_no}`">
              <td>
                <RouterLink :to="`/exposure_log?receipt=${encodeURIComponent(ref.import_no)}`">
                  {{ ref.import_no }}
                </RouterLink>
                <div class="muted-text">{{ ref.filename }}</div>
              </td>
              <td data-testid="denom-valid-file-rows">
                文件行 {{ ref.valid_file_rows.join(', ') }}
                <div class="muted-text">来源：{{ ref.valid_sources.join(', ') }}</div>
              </td>
              <td>{{ ref.valid_row_count }} 行已冻结引用</td>
            </tr>
            <tr v-if="!denominators[Number(detail.id)].receipt_refs?.length">
              <td colspan="3" class="empty-state">该分母没有 CSV 回执行，可能全部来自在线接入或窗口内无曝光</td>
            </tr>
          </tbody>
        </table>
      </div>

      <details class="revision-details" open>
        <summary>结果修订链（{{ revisions[Number(detail.id)]?.length ?? 0 }}）</summary>
        <ol class="revision-list">
          <li
            v-for="revision in revisions[Number(detail.id)] ?? []"
            :key="revision.revision_no"
            :data-testid="`revision-${revision.revision_no}`"
            :data-status="revision.status"
          >
            <div class="revision-head">
              <strong>{{ revision.revision_no }}</strong>
              <span class="revision-status" :data-current="revision.status === 'current' ? 'true' : 'false'">
                {{ revision.status === 'current' ? '当前结果' : '已被修订' }}
              </span>
              <span class="muted-text">{{ revision.created_at }}</span>
            </div>
            <div>
              分母 <strong>{{ revision.valid_exposure_count }}</strong>；
              触发：{{ revision.trigger_type }}
              <template v-if="revision.trigger_import_no">｜回执 {{ revision.trigger_import_no }}</template>
              <template v-if="revision.revises">｜修订自 {{ revision.revises }}</template>
            </div>
            <div v-for="item in revision.denominator_refs ?? []" :key="item.denominator_no" class="muted-text">
              {{ item.experiment_code }}：{{ item.count }}，引用回执
              {{ (item.receipt_refs ?? []).map((ref: any) => ref.import_no).join('、') || '无' }}
            </div>
          </li>
        </ol>
      </details>
    </article>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import {
  getMetricDenominator,
  getMetricRevisions,
  recalculateMetricJob,
} from '@/api/backend-service'

const props = defineProps<{
  jobIds: number[]
  receiptNo?: string
}>()
const emit = defineEmits<{ recalculated: [] }>()

const details = ref<any[]>([])
const revisions = ref<Record<number, any[]>>({})
const denominators = ref<Record<number, any>>({})
const working = ref(false)
const errorMessage = ref('')

async function loadJob(jobId: number) {
  try {
    const [detailResponse, revisionResponse, denominatorResponse] = await Promise.all([
      fetch(`/api/metric_compute/${jobId}`).then((r) => r.json()),
      getMetricRevisions(jobId),
      getMetricDenominator(jobId),
    ])
    const existingIndex = details.value.findIndex((item) => Number(item.id) === jobId)
    if (existingIndex >= 0) details.value[existingIndex] = detailResponse
    else details.value.push(detailResponse)
    revisions.value[jobId] = (revisionResponse.items ?? []).sort((a: any, b: any) => b.id - a.id)
    denominators.value[jobId] = denominatorResponse
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : `作业 ${jobId} 核对失败`
  }
}

async function loadAll() {
  errorMessage.value = ''
  details.value = []
  await Promise.all(props.jobIds.map((id) => loadJob(id)))
}

async function recalculateOne(jobId: number) {
  errorMessage.value = ''
  try {
    await recalculateMetricJob(jobId, { import_no: props.receiptNo || undefined })
    await loadJob(jobId)
    emit('recalculated')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重算失败'
  }
}

async function recalculateAll() {
  working.value = true
  try {
    for (const jobId of props.jobIds) await recalculateOne(jobId)
    emit('recalculated')
  } finally {
    working.value = false
  }
}

watch(() => props.jobIds, loadAll)
onMounted(loadAll)
</script>

<style scoped>
.impact-panel {
  background: #fff8eb;
  border: 1px solid #f79009;
  border-radius: 10px;
  padding: 14px 16px;
  margin: 12px 0;
}
.impact-head,
.job-title-row,
.job-state,
.revision-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.impact-head h3,
.job-impact-card h4 {
  margin: 0 0 4px;
}
.impact-head p,
.job-impact-card p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}
.job-impact-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-top: 12px;
}
.state-pill {
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  background: #f2f4f7;
}
.state-pill[data-state='待重算'] {
  background: #fffaeb;
  color: #b54708;
  border: 1px solid #fedf89;
  font-weight: 700;
}
.state-pill[data-state='已生效'],
.state-pill[data-state='已完成'] {
  background: #ecfdf3;
  color: #067647;
}
.current-revision {
  margin: 10px 0;
  font-size: 13px;
}
.denominator-box {
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
}
.denominator-count {
  font-size: 14px;
  margin-bottom: 6px;
}
.group-dist {
  font-size: 12px;
  margin-bottom: 8px;
}
.group-chip {
  display: inline-block;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 2px 8px;
  margin-right: 6px;
}
.refs-table {
  background: #fff;
}
.muted-text {
  color: var(--muted);
  font-size: 12px;
}
.revision-details {
  margin-top: 10px;
}
.revision-list {
  margin: 8px 0 0;
  padding-left: 20px;
  display: grid;
  gap: 8px;
}
.revision-list li {
  font-size: 13px;
}
.revision-status[data-current='true'] {
  color: #067647;
  font-weight: 700;
}
.revision-status[data-current='false'] {
  color: #98a2b3;
}
.error-text {
  color: #b42318;
  font-size: 13px;
}
</style>
