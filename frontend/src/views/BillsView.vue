<template>
  <div class="page">
    <div class="page-lead">
      <span class="step-kicker">第 1 步</span>
      <h2 class="page-title">导入微信账单</h2>
      <p class="muted">先完成导入就可以看消费分析。分类审核不是必做项，可以以后再处理。</p>
    </div>

    <div class="card primary-task">
      <div class="section-heading">
        <div><h3>选择微信导出的 .xlsx 文件</h3><p class="muted">文件只在本机临时解析，导入完成后删除；重复导入不会产生重复交易。</p></div>
        <span class="privacy-pill">仅本机处理</span>
      </div>
      <el-upload drag :auto-upload="false" :limit="1" accept=".xlsx" :on-change="file => selected = file.raw">
        <div class="upload-copy"><b>点击选择或拖入账单</b><small>当前仅支持微信支付 Excel 账单</small></div>
      </el-upload>
      <div class="task-actions"><el-button type="primary" size="large" :loading="uploading" :disabled="!selected" @click="upload">导入并生成分析</el-button></div>
    </div>

    <template v-if="report">
      <div class="result-banner section">
        <div><span class="eyebrow">导入完成</span><h3>新增 {{ report.inserted_rows }} 笔有效记录</h3><p>跳过 {{ report.duplicate_rows }} 笔重复记录；{{ report.failed_rows }} 笔失败，{{ report.warning_rows }} 条提示。</p></div>
        <el-button type="primary" size="large" @click="$router.push('/analytics')">下一步：看消费分析</el-button>
      </div>
      <div class="grid section import-summary">
        <div class="card"><span class="muted">消费</span><div class="metric">{{ report.flow_type_counts?.consumption || 0 }} 笔</div></div>
        <div class="card"><span class="muted">退款</span><div class="metric">{{ report.refund_rows }} 笔</div></div>
        <div class="card"><span class="muted">不计入消费的转账</span><div class="metric">{{ report.transfer_rows }} 笔</div></div>
        <div class="card"><span class="muted">待确认分类金额</span><div class="metric">{{ formatRatio(report.uncategorized_consumption_amount_ratio) }}</div></div>
      </div>
    </template>

    <el-collapse class="section secondary-panels">
      <el-collapse-item name="quality" title="查看导入记录与数据质量">
        <el-descriptions v-if="report" border :column="3">
          <el-descriptions-item label="有效记录">{{ report.total_rows }}</el-descriptions-item>
          <el-descriptions-item label="其他业务类型">{{ report.unknown_flow_rows }}</el-descriptions-item>
          <el-descriptions-item label="待确认分类（笔数）">{{ formatRatio(report.uncategorized_consumption_ratio) }}</el-descriptions-item>
          <el-descriptions-item label="账单范围" :span="3">{{ localDate(report.earliest_transaction_time) }} 至 {{ localDate(report.latest_transaction_time) }}</el-descriptions-item>
        </el-descriptions>
        <h4>历史导入</h4>
        <el-table :data="imports" empty-text="尚未导入账单">
          <el-table-column label="导入时间"><template #default="scope">{{ localDate(scope.row.started_at) }}</template></el-table-column>
          <el-table-column label="结果"><template #default="scope">{{ statusName(scope.row.status) }}</template></el-table-column>
          <el-table-column prop="inserted_rows" label="新增"/><el-table-column prop="duplicate_rows" label="重复"/><el-table-column prop="failed_rows" label="失败"/>
        </el-table>
      </el-collapse-item>

      <el-collapse-item name="classification">
        <template #title><span>可选：提高分类准确度</span><el-tag v-if="reviewSummary" class="collapse-tag" type="info">剩余 {{ reviewSummary.current_other_count }} 笔</el-tag></template>
        <div class="optional-intro">
          <div><h3>不用把所有交易逐笔分类</h3><p class="muted">系统已经按相同商户分组。优先确认金额较大的组；看不懂或不确定的记录可以继续保留为“其他 / 待确认消费”。</p></div>
          <div class="actions" v-if="deepseek?.ready"><el-button :loading="aiLoading" @click="requestSuggestions('A')">AI 辅助分析高影响项</el-button><el-button text :loading="aiLoading" @click="requestSuggestions('B')">继续分析其余建议项</el-button></div>
        </div>
        <el-alert :type="deepseek?.ready ? 'success' : 'info'" :closable="false" :title="deepseek?.ready ? 'AI 只提供候选，确认后才会修改分类。' : '未配置 AI 也不影响总消费、预测和预算主流程。'"/>
        <el-table class="section" :data="reviews" empty-text="当前没有需要优先审核的商户组">
          <el-table-column prop="merchant_hint" label="商户线索" min-width="180"/>
          <el-table-column label="影响" min-width="130"><template #default="scope"><b>{{ formatMoney(scope.row.amount_minor) }}</b><small class="table-help">{{ scope.row.transaction_count }} 笔 · {{ tierName(scope.row.tier) }}</small></template></el-table-column>
          <el-table-column label="AI 候选" min-width="170"><template #default="scope"><template v-if="scope.row.model_suggestion"><el-tag>{{ categoryName(scope.row.model_suggestion.category_code) }} {{ scope.row.model_suggestion.confidence.toFixed(0) }}%</el-tag><div class="compact-actions" v-if="scope.row.model_suggestion.category_code !== 'other'"><el-button size="small" type="primary" @click="acceptSuggestion(scope.row.model_suggestion)">接受</el-button><el-button size="small" @click="rejectSuggestion(scope.row.model_suggestion)">忽略</el-button></div></template><span v-else class="muted">暂无建议</span></template></el-table-column>
          <el-table-column label="我知道它是什么" min-width="170"><template #default="scope"><el-select placeholder="选择后整组生效" @change="value => confirmReview(scope.row, value)"><el-option v-for="category in categories.filter(item => item.code !== 'other')" :key="category.code" :label="category.name" :value="category.code"/></el-select></template></el-table-column>
        </el-table>
      </el-collapse-item>

      <el-collapse-item name="transactions" title="高级：查看标准交易记录">
        <el-table :data="transactions">
          <el-table-column label="时间"><template #default="scope">{{ localDate(scope.row.occurred_at) }}</template></el-table-column>
          <el-table-column label="金额"><template #default="scope">{{ formatMoney(scope.row.amount_minor) }}</template></el-table-column>
          <el-table-column prop="flow_type" label="类型"/>
          <el-table-column label="分类"><template #default="scope"><el-select :model-value="scope.row.effective_category" @change="value => changeCategory(scope.row, value)"><el-option v-for="category in categories" :key="category.code" :label="category.name" :value="category.code"/></el-select></template></el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client.js'
import { formatMoney } from '../utils/money.js'
import { localDate } from '../utils/display.js'

const selected = ref()
const uploading = ref(false)
const aiLoading = ref(false)
const report = ref()
const transactions = ref([])
const categories = ref([])
const imports = ref([])
const reviews = ref([])
const reviewSummary = ref()
const deepseek = ref()
const formatRatio = value => `${((value || 0) * 100).toFixed(1)}%`
const tierName = value => ({ A: '优先核对', B: '建议核对', C: '可暂时保留' }[value] || value)
const statusName = value => ({ completed: '导入完成', completed_with_warnings: '已导入，有提示', duplicate_file: '已导入过' }[value] || value)

async function load() {
  try {
    const [transactionResult, categoryResult, importResult, reviewResult, providerResult] = await Promise.all([
      api.get('/transactions?page_size=100'), api.get('/categories'), api.get('/imports'),
      api.get('/categories/review'), api.get('/classification/providers/deepseek'),
    ])
    transactions.value = transactionResult.items
    categories.value = categoryResult.items
    imports.value = importResult.items
    reviews.value = reviewResult.items
    reviewSummary.value = reviewResult.summary
    deepseek.value = providerResult
  } catch (error) { ElMessage.error(error.message) }
}

async function upload() {
  const body = new FormData(); body.append('file', selected.value); uploading.value = true
  try {
    report.value = await api.post('/imports/wechat', body, { timeout: 120000 })
    ElMessage.success('账单导入完成')
    await load()
  } catch (error) {
    ElMessage.error(error.message)
    if (error.code === 'REQUEST_TIMEOUT') await load()
  } finally { uploading.value = false }
}

async function changeCategory(row, code) {
  try { await api.patch(`/transactions/${row.id}/category`, { category_code: code }); row.effective_category = code; ElMessage.success('分类已确认') }
  catch (error) { ElMessage.error(error.message) }
}
async function confirmReview(row, code) {
  try { const result = await api.patch(`/transactions/${row.representative_transaction_id}/category`, { category_code: code }); ElMessage.success(`已应用到同组 ${result.updated_transactions} 笔交易`); await load() }
  catch (error) { ElMessage.error(error.message) }
}
const categoryName = code => categories.value.find(item => item.code === code)?.name || code
async function requestSuggestions(tier) {
  try {
    await ElMessageBox.confirm(`将发送最多 ${tier === 'A' ? 10 : 20} 个商户组的脱敏短文本、金额区间、时段和频次区间；个人二维码会跳过。`, '确认使用 AI 辅助', { type: 'warning', confirmButtonText: '同意并分析', cancelButtonText: '取消' })
    aiLoading.value = true
    const result = await api.post('/categories/review/deepseek-suggestions', { tier, limit: tier === 'A' ? 10 : 20 }, { timeout: 120000 })
    ElMessage.success(`生成 ${result.created} 个候选，跳过 ${result.skipped_private} 个隐私或证据不足组`)
    await load()
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '已取消') }
  finally { aiLoading.value = false }
}
async function acceptSuggestion(item) { try { const result = await api.post(`/categories/suggestions/${item.id}/accept`); ElMessage.success(`已应用到同组 ${result.updated_transactions} 笔交易`); await load() } catch (error) { ElMessage.error(error.message) } }
async function rejectSuggestion(item) { try { await api.post(`/categories/suggestions/${item.id}/reject`); ElMessage.success('已忽略该建议'); await load() } catch (error) { ElMessage.error(error.message) } }
onMounted(load)
</script>
