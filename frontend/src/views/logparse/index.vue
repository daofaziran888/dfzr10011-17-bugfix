<template>
  <section class="page" data-module="logparse">
    <header class="page-head">
      <div>
        <h2>采集日志解析管理</h2>
        <p class="page-desc">维护采集日志的解析规则、字段提取与异常匹配，为采集日志解析提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记日志模板</button>
        <button class="btn" type="button" @click="exportRows">导出采集日志解析清单</button>
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
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
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
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无采集日志解析数据，可先登记日志模板</td>
        </tr>
      </tbody>
    </table>

    <section class="checklist">
      <h3>修复模板生命周期复核清单</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in checklistColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in checklist" :key="String(item.id)">
            <td v-for="column in checklistColumns" :key="column">{{ formatChecklistCell(item[column]) }}</td>
          </tr>
          <tr v-if="!checklist.length">
            <td :colspan="checklistColumns.length" class="empty-state">暂无模板引用复核数据</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集日志解析记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type ChecklistRow = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/logparse'
const columns = ["模板编号", "日志类型", "解析规则", "字段提取", "格式化输出", "异常匹配", "应用范围", "模板状态", "引用模板编号", "引用模板版本", "解析批次"]
const actions = ["启用模板", "暂停模板", "关闭模板"]
const statuses = ["草稿", "已启用", "已暂停", "已关闭"]
const stats = [{"label": "日志模板总数", "value": 0}, {"label": "已启用模板", "value": 0}, {"label": "已暂停模板", "value": 0}]
const checklistColumns = ["模板编号", "解析状态", "解析批次", "引用模板编号", "引用模板版本", "模板当前版本", "模板当前状态", "版本一致"]

const rows = ref<Row[]>([])
const checklist = ref<ChecklistRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function formatChecklistCell(value: string | number | boolean | null | undefined) {
  if (value === true) return '一致'
  if (value === false) return '不一致'
  if (value === null || value === undefined || value === '') return '—'
  return value
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '日志模板登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || (payload && payload.ok === false)) {
      throw new Error(payload?.message || payload?.detail || '采集日志解析动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集日志解析操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('日志模板列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集日志解析列表读取失败'
  }
  try {
    const response = await request(`${ENDPOINT}/lifecycle`)
    if (!response.ok) {
      throw new Error('修复模板生命周期复核清单读取失败')
    }
    const payload = await response.json()
    checklist.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '修复模板生命周期复核清单读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.checklist {
  margin-top: 16px;
}

.checklist h3 {
  font-size: 14px;
  margin: 0 0 8px;
}
</style>
