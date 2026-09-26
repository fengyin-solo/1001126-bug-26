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
      <article v-for="item in stats" :key="item.label" class="stat-card" :class="{ alert: item.alert }">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>作业编号</span>
        <input v-model="keyword" placeholder="按作业编号检索" />
      </label>
      <label class="filter-item">
        <span>作业状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-check">
        <input v-model="overdueOnly" type="checkbox" />
        <span>只看超期待指派</span>
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
            <template v-if="column === '作业状态'">
              <span>{{ row[column] ?? '—' }}</span>
              <em v-if="row.overdue" class="overdue-tag">超期待指派</em>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              :class="{ disabled: busyId === String(row.id) }"
              type="button"
              :disabled="busyId === String(row.id)"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <span v-if="!availableActions(row).length" class="muted-text">—</span>
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

    <!-- 登记 / 指派 共用一个弹窗：提交失败时保留已填内容，改完直接重试。 -->
    <div v-if="dialog.open" class="modal-mask" @click.self="closeDialog">
      <div class="modal">
        <h3 class="modal-title">{{ dialog.mode === 'create' ? '登记引航作业' : `指派作业（${dialog.form.作业编号}）` }}</h3>
        <form @submit.prevent="submitDialog">
          <div v-for="field in dialogFields" :key="field.key" class="form-row">
            <label class="form-label">
              <span>{{ field.label }}<i v-if="field.required" class="required-mark">*</i></span>
              <input
                v-model="dialog.form[field.key]"
                :type="field.type || 'text'"
                :placeholder="field.placeholder || ''"
                :disabled="submitting"
              />
            </label>
          </div>
          <p v-if="dialog.error" class="error-text form-error">{{ dialog.error }}</p>
          <div class="modal-actions">
            <button class="btn" type="button" :disabled="submitting" @click="closeDialog">取消</button>
            <button class="btn primary" type="submit" :disabled="submitting">
              {{ submitting ? '提交中…' : (dialog.mode === 'create' ? '提交登记' : '确认指派') }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
interface DialogField {
  key: string
  label: string
  required: boolean
  type?: string
  placeholder?: string
}

const ENDPOINT = '/api/pilot'
const columns = ["作业编号", "作业类型", "关联船舶", "关联航次", "拖轮名称", "引航员", "计划时间", "实际时间", "作业状态"]
const statuses = ["待指派", "已指派", "作业中", "已完成"]
// 已完成没有任何后续动作，从按钮层面也杜绝“改回作业中”。
const NEXT_ACTIONS: Record<string, string[]> = {
  '待指派': ['指派作业'],
  '已指派': ['开始作业'],
  '作业中': ['确认完成'],
  '已完成': [],
}

const CREATE_FIELDS: DialogField[] = [
  { key: '作业编号', label: '作业编号', required: true, placeholder: '如 PILO-0010' },
  { key: '作业类型', label: '作业类型', required: true, placeholder: '进港引航 / 出港引航 / 移泊引航' },
  { key: '关联船舶', label: '关联船舶', required: true, placeholder: '需与航次上的船舶名称一致' },
  { key: '关联航次', label: '关联航次', required: true, placeholder: '如 VOYA-0001' },
  { key: '计划时间', label: '计划时间', required: true, type: 'datetime-local' },
  { key: '拖轮名称', label: '拖轮名称', required: true, placeholder: '如 镇航拖12' },
  { key: '引航员', label: '引航员', required: true, placeholder: '指派引航员姓名' },
]
const ASSIGN_FIELDS: DialogField[] = [
  { key: '拖轮名称', label: '拖轮名称', required: true, placeholder: '如 镇航拖12' },
  { key: '引航员', label: '引航员', required: true, placeholder: '指派引航员姓名' },
  { key: '计划时间', label: '计划时间', required: true, type: 'datetime-local' },
]

function emptyForm(): Record<string, string> {
  return { 作业编号: '', 作业类型: '', 关联船舶: '', 关联航次: '', 拖轮名称: '', 引航员: '', 计划时间: '' }
}

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const overdueOnly = ref(false)
const busyId = ref<string | null>(null)
const stats = ref([
  { label: '待指派作业', value: 0, alert: false },
  { label: '超期待指派', value: 0, alert: true },
  { label: '作业中拖轮', value: 0, alert: false },
  { label: '已完成作业', value: 0, alert: false },
])

const dialog = reactive({
  open: false,
  mode: 'create' as 'create' | 'assign',
  targetId: 0,
  form: emptyForm(),
  error: '',
})
const submitting = ref(false)
const dialogFields = computed(() => (dialog.mode === 'create' ? CREATE_FIELDS : ASSIGN_FIELDS))

function availableActions(row: Row): string[] {
  return NEXT_ACTIONS[String(row['作业状态'] ?? '')] ?? []
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  overdueOnly.value = false
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (!response.ok) {
      return
    }
    const payload = (await response.json()) as Record<string, number>
    stats.value[0].value = payload['待指派'] ?? 0
    stats.value[1].value = payload['超期待指派'] ?? 0
    stats.value[2].value = payload['作业中'] ?? 0
    stats.value[3].value = payload['已完成'] ?? 0
  } catch {
    // 统计只是看板角标，失败不阻塞列表。
  }
}

function openCreate() {
  dialog.open = true
  dialog.mode = 'create'
  dialog.targetId = 0
  dialog.form = emptyForm()
  dialog.error = ''
}

function openAssign(row: Row) {
  dialog.open = true
  dialog.mode = 'assign'
  dialog.targetId = Number(row.id)
  dialog.form = {
    ...emptyForm(),
    拖轮名称: String(row['拖轮名称'] ?? ''),
    引航员: String(row['引航员'] ?? ''),
    计划时间: toDatetimeLocal(String(row['计划时间'] ?? '')),
    作业编号: String(row['作业编号'] ?? ''),
  }
  dialog.error = ''
}

function closeDialog() {
  if (submitting.value) {
    return
  }
  dialog.open = false
  dialog.error = ''
}

// datetime-local 控件要 “YYYY-MM-DDTHH:MM”，后端收的是空格分隔，两边各自归一。
function toDatetimeLocal(value: string): string {
  return value ? value.replace(' ', 'T').slice(0, 16) : ''
}
function fromDatetimeLocal(value: string): string {
  return value ? value.replace('T', ' ') : value
}

async function submitDialog() {
  dialog.error = ''
  // 前端先挡一遍空值并指出具体字段，网络往返前就给出原因。
  const missing = dialogFields.value
    .filter((field) => !String(dialog.form[field.key] ?? '').trim())
    .map((field) => field.label)
  if (missing.length) {
    dialog.error = `提交被拒：请填写${missing.join('、')}`
    return
  }
  submitting.value = true
  try {
    const values: Record<string, string> = { ...dialog.form, 计划时间: fromDatetimeLocal(dialog.form['计划时间']) }
    const url = dialog.mode === 'create' ? ENDPOINT : `${ENDPOINT}/${dialog.targetId}/actions`
    if (dialog.mode === 'assign') {
      values.action = '指派作业'
    }
    const response = await request(url, {
      method: 'POST',
      body: JSON.stringify(dialog.mode === 'create' ? { values } : { values }),
    })
    const payload = (await response.json().catch(() => null)) as { ok?: boolean; message?: string } | null
    if (!response.ok || !payload || payload.ok === false) {
      // 关键：失败后不关弹窗、不清表单，只把后端原因展示出来，改完即可重试。
      dialog.error = payload?.message || `提交失败（${response.status}），已填内容已保留，可修改后重试`
      return
    }
    dialog.open = false
    errorMessage.value = ''
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    dialog.error = error instanceof Error ? `${error.message}，内容已保留，可重试` : '提交失败，内容已保留，可重试'
  } finally {
    submitting.value = false
  }
}

async function runAction(action: string, row: Row) {
  if (action === '指派作业') {
    openAssign(row)
    return
  }
  errorMessage.value = ''
  busyId.value = String(row.id)
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = (await response.json().catch(() => null)) as { ok?: boolean; message?: string } | null
    if (!response.ok || !payload || payload.ok === false) {
      // 不再笼统回一句“没反应”：把后端的拦截原因原样带出来。
      throw new Error(payload?.message || `引航拖轮动作未生效（${response.status}），请稍后重试`)
    }
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '引航拖轮操作失败'
  } finally {
    busyId.value = null
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (keyword.value.trim()) {
    params.set('keyword', keyword.value.trim())
  }
  if (statusFilter.value) {
    params.set('status', statusFilter.value)
  }
  if (overdueOnly.value) {
    params.set('overdue', 'true')
  }
  const query = params.toString()
  try {
    const response = await request(`${ENDPOINT}${query ? `?${query}` : ''}`)
    if (!response.ok) {
      throw new Error('引航作业列表读取失败')
    }
    const payload = await response.json()
    // 列表始终以后端返回为准，刷新后看到的记录与航次对账后的库存完全一致。
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '引航拖轮列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadStats()
})
</script>

<style scoped>
.filter-item select {
  width: 100%;
}
.filter-check {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #b42318;
  padding-bottom: 7px;
}
.stat-card.alert .stat-value {
  color: #b42318;
}
.row-overdue {
  background: #fef3f2;
}
.overdue-tag {
  font-style: normal;
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #fee4e2;
  color: #b42318;
  font-size: 12px;
}
.muted-text {
  color: var(--muted);
  font-size: 13px;
}
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  width: 520px;
  max-height: 86vh;
  overflow-y: auto;
  background: #fff;
  border-radius: 10px;
  padding: 20px 22px;
}
.modal-title {
  margin: 0 0 14px;
  font-size: 16px;
}
.form-row {
  margin-bottom: 10px;
}
.form-label span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 4px;
}
.form-label input {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
.required-mark {
  color: #b42318;
  font-style: normal;
  margin-left: 2px;
}
.form-error {
  margin: 4px 0 0;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
.link.disabled {
  opacity: 0.5;
  cursor: wait;
}
</style>
