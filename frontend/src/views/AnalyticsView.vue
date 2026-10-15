<template>
  <div class="page">
    <div class="page-lead"><span class="step-kicker">第 2 步</span><h2 class="page-title">看懂你的消费</h2><p class="muted">先看周度变化和类别占比即可。净消费＝实际消费－退款；转账、充值和提现不会算作消费。</p></div>
    <el-alert v-if="data.has_data" title="图表可以继续下钻：点击消费类别，查看组成该金额的具体交易并核对分类。" type="info" :closable="false" />
    <el-alert v-if="data.irregular_essential_minor" class="section" type="success" :closable="false" :title="`已识别低频必要支出 ${formatMoney(data.irregular_essential_minor)}：保留在真实总消费中，但不再抬高日常周消费基线；下一次到期周会单独预留。`" />
    <template v-if="data.has_data">
      <div class="insight-strip section">
        <div><span>最近完整周</span><b>{{ formatMoney(latestWeek?.net_consumption_minor) }}</b></div>
        <div><span>主要消费类别</span><b>{{ categoryName(categoryRows[0]?.category_code) }}</b></div>
        <div><span>待留意异常周</span><b>{{ data.anomaly_weeks?.length || 0 }} 个</b></div>
      </div>
      <el-tabs v-model="activeTab" class="content-tabs section">
        <el-tab-pane label="先看概览" name="overview">
          <div class="grid analysis-overview">
            <div class="card analysis-main"><div class="section-heading"><div><h3>每周净消费</h3><p class="muted">先看整体高低变化，不需要逐个研究每个点。</p></div></div><EChart :option="weekly" /></div>
            <div class="card category-rank"><h3>钱主要花在哪里</h3><p class="muted">点击类别可查看组成金额的账单。</p><button v-for="row in categoryRows.slice(0,6)" :key="row.category_code" class="rank-row" @click="openCategory(row.category_code)"><span><b>{{categoryName(row.category_code)}}</b><small>{{row.consumption_count}} 笔</small></span><span><b>{{formatMoney(row.net_consumption_minor)}}</b><small>{{(row.amount_share_bps/100).toFixed(1)}}%</small></span></button></div>
          </div>
        </el-tab-pane>
        <el-tab-pane label="分类详情" name="categories">
          <div class="grid"><div class="card wide"><h3>消费类别占比</h3><p class="muted">点击扇区查看具体交易。</p><EChart :option="category" @chart-click="openChartCategory" /></div><div class="card wide"><h3>主要分类周度趋势</h3><EChart :option="categoryTrend" /></div><div class="card full"><h3>分类金额明细</h3><el-table :data="categoryRows" @row-click="row=>openCategory(row.category_code)"><el-table-column label="类别"><template #default="s"><el-button link type="primary" @click.stop="openCategory(s.row.category_code)">{{categoryName(s.row.category_code)}}</el-button></template></el-table-column><el-table-column prop="consumption_count" label="交易笔数"/><el-table-column label="净消费"><template #default="s">{{formatMoney(s.row.net_consumption_minor)}}</template></el-table-column><el-table-column label="金额占比"><template #default="s">{{(s.row.amount_share_bps/100).toFixed(1)}}%</template></el-table-column><el-table-column label="操作" width="120"><template #default="s"><el-button size="small" @click.stop="openCategory(s.row.category_code)">查看账单</el-button></template></el-table-column></el-table></div></div>
        </el-tab-pane>
        <el-tab-pane label="月度与异常" name="patterns">
          <div class="grid"><div class="card wide"><h3>每月消费与退款</h3><EChart :option="monthly" /></div><div class="card wide"><h3>工作日与周末日均</h3><EChart :option="dayType" /></div><div class="card full"><h3>需要留意的异常周</h3><p class="muted">异常只表示与平时差异较大，不等于不合理消费。</p><el-table :data="data.anomaly_weeks"><el-table-column prop="week_start" label="周开始"/><el-table-column label="净消费"><template #default="s">{{formatMoney(s.row.net_consumption_minor)}}</template></el-table-column><el-table-column prop="large_transaction_count" label="较大金额交易数"/><el-table-column label="变化方向"><template #default="s">{{s.row.anomaly_direction==='high'?'高于平时':'低于平时'}}</template></el-table-column></el-table></div></div>
        </el-tab-pane>
      </el-tabs>
    </template>
    <div v-if="data.has_data" class="next-step"><div><b>下一步：设置储蓄目标</b><p class="muted">收入由你填写，系统不会把微信转入误当工资。</p></div><el-button type="primary" @click="$router.push('/goals')">设置储蓄目标</el-button></div>
    <EmptyGuide v-else description="暂无可分析交易" hint="请先导入账单，系统才有真实消费数据。" action="/bills" action-text="导入账单" />

    <el-drawer v-model="drawerVisible" :title="`${categoryName(selectedCategory)}账单明细`" size="min(760px, 92%)">
      <el-alert title="商户和商品短文本仅保存在本机私有目录，不进入Git；旧导入若没有短文本，仍可按日期、金额和微信原始类别核对。" type="info" :closable="false" />
      <div class="drawer-summary"><span>共 {{detailTotal}} 笔</span><b>本页金额 {{formatMoney(detailAmount)}}</b></div>
      <el-table v-loading="detailLoading" :data="details" empty-text="该类别没有可显示交易">
        <el-table-column label="交易时间" min-width="150"><template #default="s">{{localDate(s.row.occurred_at)}}</template></el-table-column>
        <el-table-column label="金额" width="120"><template #default="s">{{formatMoney(s.row.amount_minor)}}</template></el-table-column>
        <el-table-column label="交易对方" min-width="150"><template #default="s">{{s.row.counterparty_hint||'未保留'}}</template></el-table-column>
        <el-table-column label="商品/说明" min-width="190"><template #default="s">{{s.row.description_hint||'未保留'}}</template></el-table-column>
        <el-table-column label="微信交易类型" min-width="130"><template #default="s">{{s.row.source_transaction_type||'未提供'}}</template></el-table-column>
        <el-table-column label="微信原始类别" min-width="130"><template #default="s">{{s.row.original_category||'未提供'}}</template></el-table-column>
        <el-table-column label="资金性质" width="110"><template #default="s">{{flowName(s.row.flow_type)}}</template></el-table-column>
        <el-table-column label="分类依据" width="110"><template #default="s">{{sourceName(s.row.classification_source)}}</template></el-table-column>
        <el-table-column label="预算处理" min-width="160"><template #default="s"><el-select :model-value="s.row.planning_role" @change="value=>changePlanning(s.row,value)"><el-option label="日常消费" value="routine"/><el-option label="低频必要支出" value="irregular_essential"/><el-option label="一次性可选支出" value="irregular_discretionary"/></el-select></template></el-table-column>
        <el-table-column label="纠正分类" min-width="150"><template #default="s"><el-select :model-value="s.row.effective_category" @change="value=>changeCategory(s.row,value)"><el-option v-for="item in categories" :key="item.code" :label="item.name" :value="item.code"/></el-select></template></el-table-column>
      </el-table>
      <el-pagination v-if="detailTotal>detailPageSize" class="section" layout="prev, pager, next" :total="detailTotal" :page-size="detailPageSize" :current-page="detailPage" @current-change="loadDetails" />
    </el-drawer>
  </div>
</template>
<script setup>
import { computed,onMounted,ref } from 'vue';import { ElMessage,ElMessageBox } from 'element-plus';import { api } from '../api/client.js';import EChart from '../components/EChart.vue';import EmptyGuide from '../components/EmptyGuide.vue';import { formatMoney,yuanToMinor } from '../utils/money.js';import { categoryName,localDate } from '../utils/display.js'
const data=ref({}),categories=ref([]),activeTab=ref('overview'),drawerVisible=ref(false),selectedCategory=ref('other'),details=ref([]),detailTotal=ref(0),detailPage=ref(1),detailPageSize=50,detailLoading=ref(false)
onMounted(async()=>{try{const [overview,categoryList]=await Promise.all([api.get('/analytics/overview'),api.get('/categories')]);data.value=overview;categories.value=categoryList.items}catch(e){ElMessage.error(e.message)}})
const tip={trigger:'axis',valueFormatter:v=>formatMoney(v)};const categoryRows=computed(()=>(data.value.categories||[]).slice().sort((a,b)=>b.net_consumption_minor-a.net_consumption_minor));const detailAmount=computed(()=>details.value.reduce((sum,item)=>sum+(item.flow_type==='refund'?-item.amount_minor:item.amount_minor),0))
const latestWeek=computed(()=>data.value.weekly?.at(-1))
const weekly=computed(()=>({tooltip:tip,xAxis:{type:'category',data:(data.value.weekly||[]).map(x=>x.week_start)},yAxis:{type:'value'},series:[{type:'line',smooth:true,data:(data.value.weekly||[]).map(x=>x.net_consumption_minor),color:'#2b9a72'}]}));const monthly=computed(()=>({tooltip:tip,legend:{},xAxis:{type:'category',data:(data.value.monthly||[]).map(x=>x.year_month)},yAxis:{type:'value'},series:['gross_consumption_minor','refund_minor','net_consumption_minor'].map((k,i)=>({name:['实际消费','退款','净消费'][i],type:'bar',data:(data.value.monthly||[]).map(x=>x[k])}))}));const category=computed(()=>({tooltip:{trigger:'item',valueFormatter:v=>formatMoney(v)},series:[{type:'pie',radius:['45%','70%'],data:categoryRows.value.map(x=>({name:categoryName(x.category_code),value:x.net_consumption_minor,categoryCode:x.category_code}))}]}));const dayType=computed(()=>({tooltip:tip,xAxis:{type:'category',data:(data.value.weekday_weekend||[]).map(x=>x.is_weekend?'周末':'工作日')},yAxis:{type:'value'},series:[{type:'bar',data:(data.value.weekday_weekend||[]).map(x=>x.daily_average_minor),color:'#4d7c72'}]}));const categoryTrend=computed(()=>{const top=categoryRows.value.slice(0,5).map(x=>x.category_code),weeks=[...new Set((data.value.category_weekly||[]).map(x=>x.week_start))];return{tooltip:tip,legend:{},xAxis:{type:'category',data:weeks},yAxis:{type:'value'},series:top.map(code=>({name:categoryName(code),type:'line',showSymbol:false,data:weeks.map(w=>(data.value.category_weekly||[]).find(x=>x.week_start===w&&x.category_code===code)?.net_consumption_minor||0)}))}})
function openChartCategory(params){const code=params?.data?.categoryCode;if(code)openCategory(code)}
async function openCategory(code){selectedCategory.value=code;drawerVisible.value=true;await loadDetails(1)}
async function loadDetails(page=1){detailLoading.value=true;detailPage.value=page;try{const result=await api.get(`/transactions?category_code=${encodeURIComponent(selectedCategory.value)}&page=${page}&page_size=${detailPageSize}`);details.value=result.items;detailTotal.value=result.total}catch(e){ElMessage.error(e.message)}finally{detailLoading.value=false}}
async function changeCategory(row,code){try{const result=await api.patch(`/transactions/${row.id}/category`,{category_code:code});ElMessage.success(`已纠正并应用到同组 ${result.updated_transactions} 笔交易`);await loadDetails(detailPage.value);data.value=await api.get('/analytics/overview')}catch(e){ElMessage.error(e.message)}}
async function changePlanning(row,role){try{let payload={planning_role:role};if(role==='irregular_essential'){const occurred=new Date(row.occurred_at);occurred.setUTCFullYear(occurred.getUTCFullYear()+1);const suggested=occurred.toISOString().slice(0,10);const dateResult=await ElMessageBox.prompt('请输入下一次预计发生日期。系统会在到期周完整预留，不会平均摊入日常消费。','低频必要支出',{inputValue:row.next_expected_date||suggested,inputPattern:/^\d{4}-\d{2}-\d{2}$/,inputErrorMessage:'请输入 YYYY-MM-DD'});const amountResult=await ElMessageBox.prompt('请输入预计金额（元）。','低频必要支出',{inputValue:String((row.expected_amount_minor||row.amount_minor)/100),inputPattern:/^\d+(\.\d{1,2})?$/,inputErrorMessage:'请输入有效金额'});payload={...payload,recurrence_frequency:'annual',next_expected_date:dateResult.value,expected_amount_minor:yuanToMinor(amountResult.value)}}await api.patch(`/transactions/${row.id}/planning`,payload);ElMessage.success(role==='irregular_essential'?'已从日常基线分离，并加入年度刚性支出计划':'预算处理方式已更新');await loadDetails(detailPage.value);data.value=await api.get('/analytics/overview')}catch(e){if(e!=='cancel'&&e!=='close')ElMessage.error(e.message)}}
const flowName=value=>({consumption:'消费',refund:'退款'}[value]||value);const sourceName=value=>({user:'你已确认',system:'系统规则',unclassified:'待确认'}[value]||value)
</script>
