<template>
  <div class="page">
    <div class="page-lead"><span class="step-kicker">第 4 步</span><h2 class="page-title">生成预算方案</h2><p class="muted">这里只生成候选方案。你看完并确认后，它才会成为正在执行的预算。</p></div>
    <EmptyGuide v-if="ready && !goals.length" description="还没有储蓄目标" hint="请先填写目标、收入和每周预算底线。" action="/goals" action-text="设置储蓄目标" />
    <template v-else>
      <div class="card primary-task">
        <div class="form-step-title"><span>1</span><div><h3>确认本次规划目标</h3><p class="muted">如果只有一个目标，系统已经替你选好。</p></div></div>
        <el-select v-model="goalId" class="goal-picker" placeholder="选择目标"><el-option v-for="g in goals" :key="g.id" :label="`${formatMoney(g.target_savings_minor)}，截至 ${g.target_date}`" :value="g.id" /></el-select>
        <div class="form-step-title section"><span>2</span><div><h3>选择调整偏好</h3><p class="muted">不确定就选“平衡”，之后仍可重新生成。</p></div></div>
        <el-radio-group v-model="mode" class="mode-cards">
          <el-radio value="goal_priority" border><b>目标优先</b><small>允许较明显地调整非必要消费</small></el-radio>
          <el-radio value="balanced" border><b>平衡（推荐）</b><small>兼顾储蓄目标和原有习惯</small></el-radio>
          <el-radio value="life_stability" border><b>生活稳定优先</b><small>尽量减少预算变化</small></el-radio>
        </el-radio-group>
        <div class="task-actions"><el-button type="primary" size="large" :loading="loading" :disabled="!goalId" @click="generate">生成候选方案</el-button></div>
      </div>
      <template v-if="result">
        <el-result v-if="!result.feasible" icon="warning" title="按当前条件，目标暂时无法实现">
          <template #sub-title><p>在目标日期前最高预计可达到 {{formatMoney(result.maximum_achievable_target_minor)}}；若保持原目标，还需增加约 {{formatMoney(result.required_additional_income_minor)}} 收入。</p></template>
          <template #extra><el-button @click="$router.push('/goals')">调整目标或预算底线</el-button></template>
        </el-result>
        <template v-else>
          <el-alert class="section" type="success" :closable="false" title="目标具备可行方案。以下是候选预算，确认前不会影响当前执行计划。" />
          <div class="grid section summary-grid">
            <div class="card"><span class="muted">保持原习惯预计消费</span><div class="metric">{{formatMoney(result.natural_spending_minor)}}</div></div>
            <div class="card focus-card"><span class="muted">建议消费预算</span><div class="metric">{{formatMoney(result.recommended_spending_minor)}}</div></div>
            <div class="card"><span class="muted">预计期末存款</span><div class="metric">{{formatMoney(result.expected_final_savings_minor)}}</div></div>
            <div class="card"><span class="muted">变化风险</span><div class="metric">{{riskName(result.risk_level)}}</div><small>表示历史波动情形，不是成功概率</small></div>
          </div>
          <el-alert v-if="largeAdjustment" class="section" type="warning" :closable="false" title="至少一个类别需要调整 30% 以上。请重点核对生活是否可接受，再决定是否启用。" />
          <div class="card section decision-card">
            <div><span class="eyebrow">你只需要做一个决定</span><h3>这个预算调整可以接受吗？</h3><p class="muted">系统建议总消费调整 {{ formatMoney(result.recommended_spending_minor-result.natural_spending_minor) }}。详细分类和每月计划可在下方展开核对。</p></div>
            <el-button type="primary" size="large" @click="activate">接受方案并开始执行</el-button>
          </div>
          <el-collapse class="section secondary-panels budget-details">
            <el-collapse-item title="查看各类别如何调整" name="categories"><EChart :option="chart" /><el-table :data="categoryRows"><el-table-column prop="category" label="类别"/><el-table-column label="正常消费参考"><template #default="s">{{formatMoney(s.row.natural)}}</template></el-table-column><el-table-column label="建议预算"><template #default="s">{{formatMoney(s.row.budget)}}</template></el-table-column><el-table-column label="变化"><template #default="s"><span :class="deltaClass(s.row.delta)">{{formatMoney(s.row.delta)}}</span></template></el-table-column></el-table></el-collapse-item>
            <el-collapse-item title="查看每月储蓄进度" name="monthly"><el-table :data="result.monthly_plan"><el-table-column prop="year_month" label="月份"/><el-table-column label="预计收入"><template #default="s">{{formatMoney(s.row.expected_income_minor)}}</template></el-table-column><el-table-column label="建议消费"><template #default="s">{{formatMoney(s.row.recommended_consumption_minor)}}</template></el-table-column><el-table-column label="预计储蓄"><template #default="s">{{formatMoney(s.row.expected_saving_minor)}}</template></el-table-column><el-table-column label="累计存款"><template #default="s">{{formatMoney(s.row.cumulative_savings_minor)}}</template></el-table-column></el-table></el-collapse-item>
          </el-collapse>
        </template>
      </template>
    </template>
  </div>
</template>
<script setup>
import { computed,onMounted,ref } from 'vue';import { useRouter } from 'vue-router';import { ElMessage,ElMessageBox } from 'element-plus';import { api } from '../api/client.js';import { formatMoney } from '../utils/money.js';import { categoryName,riskLabels } from '../utils/display.js';import EChart from '../components/EChart.vue';import EmptyGuide from '../components/EmptyGuide.vue'
const router=useRouter(),goals=ref([]),goalId=ref(),mode=ref('balanced'),result=ref(),loading=ref(false),ready=ref(false)
onMounted(async()=>{try{goals.value=(await api.get('/goals')).items;goalId.value=goals.value[0]?.id}finally{ready.value=true}})
async function generate(){loading.value=true;try{result.value=await api.post('/budgets/generate',{goal_id:goalId.value,adjustment_mode:mode.value})}catch(e){ElMessage.error(e.message)}finally{loading.value=false}}
const categoryRows=computed(()=>{const m={};for(const x of result.value?.items||[]){m[x.category_code]??={category:categoryName(x.category_code),natural:0,budget:0,delta:0};m[x.category_code].natural+=x.natural_spend_minor;m[x.category_code].budget+=x.recommended_budget_minor;m[x.category_code].delta+=x.adjustment_minor}return Object.values(m)})
const largeAdjustment=computed(()=>categoryRows.value.some(x=>x.natural>0&&Math.abs(x.delta)/x.natural>.3));const riskName=v=>riskLabels[v]||v||'暂无'
const deltaClass=value=>value<0?'cut':'raise'
const chart=computed(()=>({tooltip:{trigger:'axis',valueFormatter:v=>formatMoney(v)},legend:{},xAxis:{type:'category',data:categoryRows.value.map(x=>x.category)},yAxis:{type:'value'},series:[{name:'正常消费参考',type:'bar',data:categoryRows.value.map(x=>x.natural)},{name:'建议预算',type:'bar',data:categoryRows.value.map(x=>x.budget),color:'#2b9a72'}]}))
async function activate(){try{await ElMessageBox.confirm('启用后，这份方案将成为当前执行预算。旧方案不会被删除。','确认开始执行',{confirmButtonText:'确认启用',cancelButtonText:'再看看',type:'warning'});await api.post(`/budgets/${result.value.plan_id}/activate`);ElMessage.success('预算方案已启用');router.push('/replan')}catch(e){if(e!=='cancel'&&e!=='close')ElMessage.error(e.message)}}
</script>
