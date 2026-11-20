<template>
  <section class="page" data-module="data_extract">
    <header class="page-head">
      <div>
        <h2>数据提取管理</h2>
        <p class="page-desc">维护按条件提取数据子集的任务定义、字段选择和分页导出配置。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记提取任务</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出数据提取清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" :data-testid="`delivery-${row.id}`" @click="openDelivery(row)">
              分段交付
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据提取数据，可先登记提取任务</td>
        </tr>
      </tbody>
    </table>

    <section v-if="activeTask" class="delivery-panel" data-testid="delivery-panel">
      <header class="delivery-head">
        <div>
          <h3>分段交付 · {{ activeTask["任务编号"] }}</h3>
          <p class="page-desc">
            来源 {{ activeTask["来源数据集"] }}，按筛选口径冻结快照后分段确认；只有已确认分段进入下载与报表。
          </p>
        </div>
        <div class="page-actions">
          <input
            v-model="segmentSize"
            class="delivery-input"
            data-testid="segment-size"
            type="number"
            min="1"
            max="1000"
            title="每段成员数"
          />
          <button class="btn primary" data-testid="start-delivery" type="button" :disabled="deliveryBusy" @click="startDelivery">
            启动分段交付
          </button>
          <button class="btn ghost" type="button" @click="closeDelivery">收起</button>
        </div>
      </header>

      <p v-if="deliveryMessage" class="delivery-message" data-testid="delivery-message">{{ deliveryMessage }}</p>
      <p v-if="deliveryError" class="error-text" data-testid="delivery-error">{{ deliveryError }}</p>

      <div v-if="deliveries.length" class="delivery-tabs">
        <button
          v-for="item in deliveries"
          :key="item.id"
          type="button"
          class="delivery-tab"
          :class="{ active: item.id === activeDelivery?.id }"
          :data-testid="`delivery-version-${item.id}`"
          @click="selectDelivery(item.id)"
        >
          {{ item.version }} · {{ item.status }} · {{ item.confirmed_member_count }} 人
        </button>
      </div>

      <div v-if="activeDelivery" class="delivery-body">
        <div class="analysis-summary">
          <article class="analysis-card">
            <span class="stat-label">业务截止时间</span>
            <strong data-testid="business-cutoff">{{ activeDelivery.business_cutoff_at }}</strong>
          </article>
          <article class="analysis-card">
            <span class="stat-label">交付版本 / 状态</span>
            <strong data-testid="delivery-version">{{ activeDelivery.version }} · {{ activeDelivery.status }}</strong>
          </article>
          <article class="analysis-card">
            <span class="stat-label">已确认人数</span>
            <strong data-testid="confirmed-count">{{ activeReport?.member_count ?? activeDelivery.confirmed_member_count }}</strong>
          </article>
          <article class="analysis-card">
            <span class="stat-label">已确认 LTV</span>
            <strong data-testid="confirmed-ltv">{{ activeReport?.total_ltv ?? activeDelivery.confirmed_ltv_total }}</strong>
          </article>
          <article class="analysis-card">
            <span class="stat-label">未送达分段</span>
            <strong data-testid="undelivered-count">{{ activeDelivery.undelivered_segment_count }}</strong>
          </article>
        </div>

        <p class="delivery-scope" data-testid="delivery-scope">
          口径：{{ activeDelivery.filter }}｜快照 {{ activeDelivery.total_members }} 人 / {{ activeDelivery.segment_count }} 段｜
          报表范围：{{ activeReport?.scope ?? '已确认分段' }}{{ activeReport?.complete ? '（全部送达）' : '（仅部分送达）' }}
        </p>

        <table class="data-table" data-testid="segment-table">
          <thead>
            <tr>
              <th>分段</th><th>状态</th><th>断点</th><th>人数</th><th>LTV</th><th>校验码</th><th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="segment in activeDelivery.segments"
              :key="segment.seq"
              :data-testid="`segment-${segment.seq}`"
              :data-status="segment.status"
            >
              <td>第 {{ segment.seq }} 段</td>
              <td>{{ segment.status }}</td>
              <td>{{ segment.breakpoint }}</td>
              <td>{{ segment.member_count }}</td>
              <td>{{ segment.ltv_total }}</td>
              <td>{{ segment.checksum }}</td>
              <td class="row-actions">
                <button
                  class="link"
                  type="button"
                  :data-testid="`confirm-${segment.seq}`"
                  @click="deliveryAction('确认分段', segment.seq)"
                >
                  确认分段
                </button>
                <button
                  class="link"
                  type="button"
                  :data-testid="`download-${segment.seq}`"
                  @click="downloadSegment(segment.seq)"
                >
                  下载本段
                </button>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="page-actions delivery-actions">
          <button class="btn" data-testid="interrupt-delivery" type="button" @click="deliveryAction('中断交付')">中断交付</button>
          <button class="btn" data-testid="resume-delivery" type="button" @click="deliveryAction('恢复交付')">恢复交付</button>
          <button class="btn primary" data-testid="download-delivery" type="button" @click="downloadDelivery">下载已确认交付文件</button>
          <button class="btn" data-testid="publish-report" type="button" @click="publishReport">复用到人群报表</button>
        </div>

        <div v-if="activeReport" class="delivery-report" data-testid="delivery-report">
          <h4>活动交付报表（{{ activeReport.version }}）</h4>
          <table class="data-table">
            <tbody>
              <tr><th>业务截止时间</th><td>{{ activeReport.business_cutoff_at }}</td></tr>
              <tr><th>交付版本</th><td>{{ activeReport.version }} · {{ activeReport.delivery_status }}</td></tr>
              <tr><th>实际采用门槛</th><td>{{ activeReport.filter }}</td></tr>
              <tr><th>报表人数（唯一用户）</th><td>{{ activeReport.member_count }} / 去重 {{ activeReport.duplicate_count }} 条</td></tr>
              <tr><th>LTV 合计</th><td>{{ activeReport.total_ltv }}</td></tr>
              <tr>
                <th>文件复核</th>
                <td data-testid="file-check">
                  应含 {{ activeReport.file_check.expected_row_count }} 行 · 校验码 {{ activeReport.file_check.expected_checksum }}
                  <template v-if="activeReport.file_check.last_confirmed_download">
                    ｜最近下载 {{ activeReport.file_check.last_confirmed_download.row_count }} 行 /
                    {{ activeReport.file_check.last_confirmed_download.unique_user_count }} 个用户 /
                    {{ activeReport.file_check.last_confirmed_download.checksum }}
                  </template>
                </td>
              </tr>
              <tr><th>纳入分段</th><td>{{ activeReport.confirmed_segments.join('、') || '—' }}</td></tr>
              <tr><th>未送达分段</th><td>{{ activeReport.undelivered_segments.join('、') || '无' }}</td></tr>
              <tr><th>当前来源对照</th><td>
                {{ activeReport.live_reference.matched_count }} 人 · {{ activeReport.live_reference.total_ltv }}（不并入报表）
              </td></tr>
              <tr><th>截止后新增成员</th><td>{{ activeReport.drift.added_user_ids.join('、') || '无' }}</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记提取任务</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据提取记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Segment = {
  seq: number
  status: string
  breakpoint: string
  member_count: number
  ltv_total: number
  checksum: string
}
type DeliveryReport = {
  version: string
  version_number: number
  delivery_status: string
  business_cutoff_at: string
  filter: string
  member_count: number
  unique_user_count: number
  total_ltv: number
  duplicate_count: number
  confirmed_segments: number[]
  undelivered_segments: number[]
  complete: boolean
  scope: string
  live_reference: { matched_count: number; total_ltv: number }
  drift: { added_user_ids: string[] }
  file_check: {
    expected_row_count: number
    expected_checksum: string
    last_confirmed_download: { row_count: number; unique_user_count: number; checksum: string; at: string } | null
  }
}
type Delivery = {
  id: number
  version: string
  status: string
  business_cutoff_at: string
  filter: string
  total_members: number
  segment_count: number
  confirmed_segment_count: number
  undelivered_segment_count: number
  confirmed_member_count: number
  confirmed_ltv_total: number
  segments?: Segment[]
}
type DeliverySummary = Omit<Delivery, 'segments'>

const ENDPOINT = '/api/data_extract'
const columns = ["任务编号", "来源数据集", "筛选条件", "字段列表", "排序规则", "分页方式", "输出格式", "提取上限"]
const actions = ["启动提取", "取消任务", "归档结果"]
const statuses = ["草稿", "提取中", "已完成", "已取消"]
const metricNames = ["任务总数", "提取中任务", "已完成任务"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const activeTask = ref<Row | null>(null)
const deliveries = ref<DeliverySummary[]>([])
const activeDelivery = ref<Delivery | null>(null)
const activeReport = ref<DeliveryReport | null>(null)
const segmentSize = ref(2)
const deliveryBusy = ref(false)
const deliveryMessage = ref('')
const deliveryError = ref('')
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function openDelivery(row: Row) {
  activeTask.value = row
  deliveryMessage.value = ''
  deliveryError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/deliveries`)
    if (!response.ok) throw new Error('交付版本读取失败')
    const payload = await response.json()
    deliveries.value = payload.items ?? []
    if (deliveries.value.length) {
      await selectDelivery(deliveries.value[deliveries.value.length - 1].id)
    } else {
      activeDelivery.value = null
      activeReport.value = null
    }
  } catch (error) {
    deliveryError.value = error instanceof Error ? error.message : '交付版本读取失败'
  }
}

function closeDelivery() {
  activeTask.value = null
  activeDelivery.value = null
  activeReport.value = null
  deliveries.value = []
}

async function refreshDeliveries() {
  if (!activeTask.value) return
  const response = await request(`${ENDPOINT}/${activeTask.value.id}/deliveries`)
  const payload = await response.json()
  deliveries.value = payload.items ?? []
}

async function selectDelivery(deliveryId: number) {
  if (!activeTask.value) return
  deliveryError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${activeTask.value.id}/deliveries/${deliveryId}`)
    if (!response.ok) throw new Error('交付明细读取失败')
    activeDelivery.value = await response.json()
    await loadReport(deliveryId)
  } catch (error) {
    deliveryError.value = error instanceof Error ? error.message : '交付明细读取失败'
  }
}

async function loadReport(deliveryId: number) {
  if (!activeTask.value) return
  const response = await request(`${ENDPOINT}/${activeTask.value.id}/deliveries/${deliveryId}/report`)
  activeReport.value = response.ok ? await response.json() : null
}

async function readMessage(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json()
    return payload.message || fallback
  } catch {
    return fallback
  }
}

async function startDelivery() {
  if (!activeTask.value) return
  deliveryBusy.value = true
  deliveryError.value = ''
  deliveryMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${activeTask.value.id}/deliveries`, {
      method: 'POST',
      body: JSON.stringify({ 分段大小: Number(segmentSize.value) }),
    })
    if (!response.ok) throw new Error(await readMessage(response, '分段交付启动失败'))
    const payload = await response.json()
    deliveryMessage.value = payload.message
    await refreshDeliveries()
    await selectDelivery(payload.entry.id)
    await reload()
  } catch (error) {
    deliveryError.value = error instanceof Error ? error.message : '分段交付启动失败'
  } finally {
    deliveryBusy.value = false
  }
}

async function deliveryAction(action: string, segmentSeq?: number) {
  if (!activeTask.value || !activeDelivery.value) return
  deliveryError.value = ''
  deliveryMessage.value = ''
  try {
    const response = await request(
      `${ENDPOINT}/${activeTask.value.id}/deliveries/${activeDelivery.value.id}/actions`,
      { method: 'POST', body: JSON.stringify({ action, segment_seq: segmentSeq ?? null }) },
    )
    if (!response.ok) throw new Error(await readMessage(response, `${action}未生效`))
    const payload = await response.json()
    deliveryMessage.value = payload.message
    activeDelivery.value = payload.entry
    await loadReport(activeDelivery.value!.id)
    await refreshDeliveries()
    await reload()
  } catch (error) {
    deliveryError.value = error instanceof Error ? error.message : `${action}失败`
  }
}

async function downloadFrom(path: string, fallbackName: string) {
  deliveryError.value = ''
  try {
    const response = await request(path)
    if (!response.ok) throw new Error(await readMessage(response, '下载失败'))
    const cutoff = response.headers.get('X-Business-Cutoff') ?? ''
    const version = response.headers.get('X-Delivery-Version') ?? ''
    deliveryMessage.value = `已下载 ${version}，业务截止 ${cutoff}`
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = fallbackName
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    deliveryError.value = error instanceof Error ? error.message : '下载失败'
  }
}

function downloadDelivery() {
  if (!activeDelivery.value) return
  const suffix = activeDelivery.value.status === '已完成' ? 'confirmed' : 'partial'
  void downloadFrom(
    `${ENDPOINT}/${activeTask.value?.id}/deliveries/${activeDelivery.value.id}/download`,
    `${String(activeTask.value?.["任务编号"] ?? "delivery")}-${activeDelivery.value.version}-${suffix}.csv`,
  )
}

function downloadSegment(seq: number) {
  if (!activeDelivery.value) return
  void downloadFrom(
    `${ENDPOINT}/${activeTask.value?.id}/deliveries/${activeDelivery.value.id}/download?segment=${seq}`,
    `${String(activeTask.value?.["任务编号"] ?? "delivery")}-${activeDelivery.value.version}-part${seq}.csv`,
  )
}

async function publishReport() {
  if (!activeTask.value || !activeDelivery.value) return
  deliveryError.value = ''
  deliveryMessage.value = ''
  try {
    const query = new URLSearchParams({
      entry_id: String(activeTask.value.id),
      delivery_id: String(activeDelivery.value.id),
    })
    const response = await request(`/api/pipeline/cohort-delivery-report?${query}`, { method: 'POST' })
    if (!response.ok) throw new Error('人群报表复用失败')
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '人群报表复用失败')
    deliveryMessage.value = payload.message
  } catch (error) {
    deliveryError.value = error instanceof Error ? error.message : '人群报表复用失败'
  }
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'data_extract.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('数据提取动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据提取操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('提取任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据提取列表读取失败'
  }
}

onMounted(reload)
</script>
