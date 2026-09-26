<template>
  <section class="page" data-module="pilot">
    <header class="page-head">
      <div>
        <h2>引航拖轮管理</h2>
        <p class="page-desc">维护引航作业，围绕作业编号、作业类型、关联船舶、拖轮名称做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记引航作业</button>
        <button class="btn" type="button" @click="exportRows">导出引航拖轮清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>作业编号</span>
        <input v-model="filters.keyword" placeholder="按作业编号检索" />
      </label>
      <label class="filter-item">
        <span>关联船舶</span>
        <input v-model="filters.vessel" placeholder="按关联船舶检索" />
      </label>
      <label class="filter-item">
        <span>作业状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-item filter-check">
        <input v-model="filters.overdueOnly" type="checkbox" />
        <span>只看超期未指派</span>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-overdue': row.overdue }">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '计划时间'">
              {{ row[column] ?? '—' }}
              <span v-if="row.overdue" class="tag-overdue">已超期</span>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <span v-if="!availableActions(row).length" class="muted-text">已办结</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无引航拖轮数据，可先登记引航作业</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条引航拖轮记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="createVisible" class="modal-mask" @click.self="closeCreate">
      <form class="modal-card" @submit.prevent="submitCreate">
        <h3>登记引航作业</h3>
        <label v-for="field in createFields" :key="field.key" class="form-item">
          <span>{{ field.label }}</span>
          <input
            v-model="createForm[field.key]"
            :type="field.type"
            :placeholder="`请填写${field.label}`"
          />
        </label>
        <p v-if="createError" class="error-text">{{ createError }}</p>
        <div class="modal-actions">
          <button class="btn primary" type="submit" :disabled="submitting">
            {{ submitting ? '提交中…' : createError ? '重试提交' : '提交' }}
          </button>
          <button class="btn ghost" type="button" @click="closeCreate">取消</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type ApiResult = { ok?: boolean; message?: string; detail?: string }

const ENDPOINT = '/api/pilot'
const columns = ["作业编号", "作业类型", "关联船舶", "关联航次", "拖轮名称", "引航员", "计划时间", "实际时间", "作业状态"]
const statuses = ["待指派", "已指派", "作业中", "已完成"]
const ACTION_BY_STATUS: Record<string, string[]> = {
  待指派: ['指派作业'],
  已指派: ['开始作业'],
  作业中: ['确认完成'],
  已完成: [],
}
const createFields = [
  { key: '作业编号', label: '作业编号', type: 'text' },
  { key: '作业类型', label: '作业类型', type: 'text' },
  { key: '关联船舶', label: '关联船舶', type: 'text' },
  { key: '拖轮名称', label: '拖轮名称', type: 'text' },
  { key: '引航员', label: '引航员', type: 'text' },
  { key: '计划时间', label: '计划时间', type: 'datetime-local' },
]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const stats = ref([
  { label: '待指派作业', value: 0 },
  { label: '作业中拖轮', value: 0 },
  { label: '超期未指派', value: 0 },
])
const filters = ref({ keyword: '', vessel: '', status: '', overdueOnly: false })

const createVisible = ref(false)
const submitting = ref(false)
const createError = ref('')
const createForm = ref<Record<string, string>>({})

function availableActions(row: Row): string[] {
  return ACTION_BY_STATUS[String(row.status ?? '')] ?? []
}

function resetFilters() {
  filters.value = { keyword: '', vessel: '', status: '', overdueOnly: false }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  createError.value = ''
  createForm.value = { 作业编号: '', 作业类型: '', 关联船舶: '', 拖轮名称: '', 引航员: '', 计划时间: '' }
  createVisible.value = true
}

function closeCreate() {
  if (submitting.value) return
  createVisible.value = false
}

async function submitCreate() {
  createError.value = ''
  if (!createForm.value['拖轮名称']?.trim()) {
    createError.value = '拖轮名称不能为空，请填写后再提交'
    return
  }
  if (!createForm.value['计划时间']) {
    createError.value = '计划时间不能为空，请选择计划时间后再提交'
    return
  }
  submitting.value = true
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm.value } }),
    })
    const payload = (await response.json().catch(() => null)) as ApiResult | null
    if (!response.ok) {
      throw new Error(payload?.detail ?? `接口返回 ${response.status}`)
    }
    if (payload && payload.ok === false) {
      throw new Error(payload.message ?? '引航作业登记被拒绝')
    }
    createVisible.value = false
    await reload()
  } catch (error) {
    // 已填内容保留在表单里，修正后可直接重试
    createError.value = error instanceof Error ? error.message : '引航作业登记失败，请重试'
  } finally {
    submitting.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = (await response.json().catch(() => null)) as ApiResult | null
    if (!response.ok) {
      throw new Error(payload?.detail ?? `接口返回 ${response.status}`)
    }
    if (payload && payload.ok === false) {
      throw new Error(payload.message ?? '引航拖轮动作未生效')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '引航拖轮操作失败'
  }
}

async function loadStats() {
  try {
    const [pending, working, overdue] = await Promise.all([
      fetchJson<{ total?: number }>(`${ENDPOINT}?status=${encodeURIComponent('待指派')}&size=1`),
      fetchJson<{ total?: number }>(`${ENDPOINT}?status=${encodeURIComponent('作业中')}&size=1`),
      fetchJson<{ total?: number }>(`${ENDPOINT}/overdue?size=1`),
    ])
    stats.value = [
      { label: '待指派作业', value: pending.total ?? 0 },
      { label: '作业中拖轮', value: working.total ?? 0 },
      { label: '超期未指派', value: overdue.total ?? 0 },
    ]
  } catch {
    // 统计卡片读取失败不阻塞列表展示
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.value.keyword) query.set('keyword', filters.value.keyword)
  if (filters.value.vessel) query.set('vessel', filters.value.vessel)
  if (filters.value.status) query.set('status', filters.value.status)
  if (filters.value.overdueOnly) query.set('overdue', 'true')
  try {
    const payload = await fetchJson<{ items?: Row[]; total?: number }>(`${ENDPOINT}?${query.toString()}`)
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await loadStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '引航拖轮列表读取失败'
  }
}

onMounted(reload)
</script>
