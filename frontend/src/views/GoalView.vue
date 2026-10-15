<template>
  <div class="page narrow-page">
    <div class="page-lead"><span class="step-kicker">第 3 步</span><h2 class="page-title">设置储蓄目标</h2><p class="muted">先填目标和可靠收入，再告诉系统哪些类别每周至少要保留多少钱。</p></div>
    <EmptyGuide v-if="ready && !hasTransactions" description="还没有可用的消费数据" hint="请先导入账单并查看消费分析，再设置目标。" action="/bills" action-text="导入账单"/>
    <template v-else>
      <div class="card primary-task">
        <div class="form-step-title"><span>1</span><div><h3>你想存到多少钱？</h3><p class="muted">金额都由你填写；微信转入不会被自动当作工资收入。</p></div></div>
        <el-form label-position="top" :model="form">
          <div class="grid form-grid">
            <el-form-item label="目前可用于目标的存款"><el-input v-model="form.current" placeholder="例如 5000" inputmode="decimal"><template #append>元</template></el-input></el-form-item>
            <el-form-item label="目标存款"><el-input v-model="form.target" placeholder="例如 12000" inputmode="decimal"><template #append>元</template></el-input></el-form-item>
            <el-form-item label="开始日期"><el-date-picker v-model="form.start" value-format="YYYY-MM-DD"/></el-form-item>
            <el-form-item label="希望完成日期"><el-date-picker v-model="form.end" value-format="YYYY-MM-DD"/></el-form-item>
            <el-form-item label="预计每月可支配收入"><el-input v-model="form.income" placeholder="工资等可靠收入" inputmode="decimal"><template #append>元</template></el-input><small class="field-help">没有稳定收入可填 0，之后仍可修改。</small></el-form-item>
          </div>
          <el-button type="primary" size="large" :loading="saving" @click="create">保存目标，继续下一步</el-button>
        </el-form>
      </div>

      <div v-if="goalId" class="card section primary-task">
        <div class="form-step-title"><span>2</span><div><h3>哪些消费不能再低？</h3><p class="muted">通常只需填写房租、基础交通等确实不能压缩的类别；其余保持 0 即可。</p></div></div>
        <div class="constraint-picker">
          <label>选择确实需要设置底线的类别</label>
          <el-select v-model="protectedCodes" multiple filterable placeholder="例如：居住、交通、餐饮" class="protected-select"><el-option v-for="item in constraints" :key="item.category_code" :label="item.name" :value="item.category_code"/></el-select>
          <small class="field-help">没有必须保护的类别可以不选，系统会按你的历史习惯生成候选预算。</small>
        </div>
        <div v-if="protectedConstraints.length" class="protected-list">
          <div v-for="item in protectedConstraints" :key="item.category_code" class="protected-row"><div><b>{{item.name}}</b><small>预算不会低于这个金额</small></div><el-input v-model="item.minimum" inputmode="decimal"><template #append>元/周</template></el-input></div>
        </div>
        <div v-else class="soft-empty"><b>暂未设置消费底线</b><span>这不是错误，你可以直接继续生成方案。</span></div>
        <el-collapse class="section secondary-panels">
          <el-collapse-item name="advanced" title="高级设置：最高预算、固定支出和调整程度">
            <p class="muted">不确定时不用修改，系统默认采用“可适度调整”。</p>
            <el-table :data="constraints">
              <el-table-column prop="name" label="类别"/>
              <el-table-column label="每周最多（可选）"><template #default="scope"><el-input v-model="scope.row.maximum" inputmode="decimal"><template #append>元</template></el-input></template></el-table-column>
              <el-table-column label="金额固定"><template #default="scope"><el-switch v-model="scope.row.fixed" @change="scope.row.maximum = scope.row.fixed ? scope.row.minimum : scope.row.maximum"/></template></el-table-column>
              <el-table-column label="可接受的调整"><template #default="scope"><el-select v-model="scope.row.adjustability" :disabled="scope.row.fixed"><el-option v-for="option in adjustabilityOptions" :key="option.value" v-bind="option"/></el-select></template></el-table-column>
            </el-table>
          </el-collapse-item>
        </el-collapse>
        <div class="next-step"><div><b>完成后不会立刻启用预算</b><p class="muted">下一页先展示候选方案，你确认后才开始执行。</p></div><el-button type="primary" size="large" :loading="savingConstraints" @click="saveConstraints">保存并查看预算方案</el-button></div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'
import { yuanToMinor } from '../utils/money.js'
import { adjustabilityOptions, validateGoal } from '../utils/forms.js'
import EmptyGuide from '../components/EmptyGuide.vue'

const router = useRouter()
const today = new Date().toISOString().slice(0, 10)
const next = new Date(Date.now() + 180 * 86400000).toISOString().slice(0, 10)
const form = reactive({ current: '', target: '', start: today, end: next, income: '' })
const constraints = ref([])
const protectedCodes = ref([])
const protectedConstraints = computed(() => constraints.value.filter(item => protectedCodes.value.includes(item.category_code)))
const goalId = ref()
const saving = ref(false)
const savingConstraints = ref(false)
const ready = ref(false)
const hasTransactions = ref(false)

onMounted(async () => {
  try {
    const [categories, dashboard] = await Promise.all([api.get('/categories'), api.get('/dashboard')])
    hasTransactions.value = dashboard.workflow?.has_transactions
    constraints.value = categories.items.map(category => ({ category_code: category.code, name: category.name, minimum: '0', maximum: '', fixed: false, adjustability: 'medium' }))
  } finally { ready.value = true }
})

async function create() {
  let current, target, income
  try { current = yuanToMinor(form.current); target = yuanToMinor(form.target); income = yuanToMinor(form.income || '0') }
  catch (error) { return ElMessage.error(error.message) }
  const validationError = validateGoal({ start_date: form.start, target_date: form.end, current_savings_minor: current, target_savings_minor: target })
  if (validationError) return ElMessage.error(validationError)
  saving.value = true
  try {
    const result = await api.post('/goals', { current_savings_minor: current, start_date: form.start, target_date: form.end, target_savings_minor: target, income_assumptions: [{ frequency: 'monthly', amount_minor: income, start_date: form.start, end_date: form.end, expected_day: 10 }] })
    goalId.value = result.id
    ElMessage.success('目标已保存，请填写必要消费底线')
  } catch (error) { ElMessage.error(error.message) }
  finally { saving.value = false }
}

async function saveConstraints() {
  savingConstraints.value = true
  try {
    await api.put(`/goals/${goalId.value}/constraints`, { constraints: constraints.value.map(item => ({ category_code: item.category_code, minimum_budget_minor: yuanToMinor(item.minimum || '0'), maximum_budget_minor: item.fixed ? yuanToMinor(item.minimum || '0') : item.maximum === '' ? null : yuanToMinor(item.maximum), is_fixed: item.fixed, adjustability: item.fixed ? 'none' : item.adjustability })) })
    ElMessage.success('目标和预算底线已保存')
    router.push('/budgets')
  } catch (error) { ElMessage.error(error.message) }
  finally { savingConstraints.value = false }
}
</script>
