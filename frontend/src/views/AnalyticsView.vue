<template>
  <div class="page">
    <h2 class="page-title">看懂你的消费习惯</h2>
    <p class="muted">净消费＝实际消费－退款；转账、充值和提现不算日常消费。</p>
    <el-alert v-if="data.has_data" title="图表可以继续下钻：点击消费类别，查看组成该金额的具体交易并核对分类。" type="info" :closable="false" />
    <div v-if="data.has_data" class="grid section">
      <div class="card wide"><h3>每周净消费</h3><p class="muted">用于观察短期波动，也是后续预算的主要时间单位。</p><EChart :option="weekly" /></div>
      <div class="card wide"><h3>每月消费与退款</h3><EChart :option="monthly" /></div>
      <div class="card wide"><div class="section-heading"><div><h3>消费类别占比</h3><p class="muted">点击扇区查看具体交易。</p></div></div><EChart :option="category" @chart-click="openChartCategory" /></div>
      <div class="card wide"><h3>工作日与周末日均</h3><EChart :option="dayType" /></div>
      <div class="card full"><h3>分类金额明细</h3><p class="muted">金额异常时先查看原始类别和交易日期，再决定是否纠正分类。</p>
        <el-table :data="categoryRows" @row-click="row=>openCategory(row.category_code)"><el-table-column label="类别"><template #default="s"><el-button link type="primary" @click.stop="openCategory(s.row.category_code)">{{categoryName(s.row.category_code)}}</el-button></template></el-table-column><el-table-column prop="consumption_count" label="交易笔数"/><el-table-column label="净消费"><template #default="s">{{formatMoney(s.row.net_consumption_minor)}}</template></el-table-column><el-table-column label="金额占比"><template #default="s">{{(s.row.amount_share_bps/100).toFixed(1)}}%</template></el-table-column><el-table-column label="操作" width="120"><template #default="s"><el-button size="small" @click.stop="openCategory(s.row.category_code)">查看账单</el-button></template></el-table-column></el-table>
      </div>
      <div class="card full"><h3>主要分类周度趋势</h3><EChart :option="categoryTrend" /></div>
      <div class="card full"><h3>需要留意的异常周</h3><p class="muted">异常只表示与平时差异较大，不等于不合理消费。</p><el-table :data="data.anomaly_weeks"><el-table-column prop="week_start" label="周开始"/><el-table-column label="净消费"><template #default="s">{{formatMoney(s.row.net_consumption_minor)}}</template></el-table-column><el-table-column prop="large_transaction_count" label="较大金额交易数"/><el-table-column label="变化方向"><template #default="s">{{s.row.anomaly_direction==='high'?'高于平时':'低于平时'}}</template></el-table-column></el-table></div>
    </div>
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
        <el-table-column label="纠正分类" min-width="150"><template #default="s"><el-select :model-value="s.row.effective_category" @change="value=>changeCategory(s.row,value)"><el-option v-for="item in categories" :key="item.code" :label="item.name" :value="item.code"/></el-select></template></el-table-column>
      </el-table>
      <el-pagination v-if="detailTotal>detailPageSize" class="section" layout="prev, pager, next" :total="detailTotal" :page-size="detailPageSize" :current-page="detailPage" @current-change="loadDetails" />
    </el-drawer>
  </div>
</template>
<script setup>
import { computed,onMounted,ref } from 'vue';import { ElMessage } from 'element-plus';import { api } from '../api/client.js';import EChart from '../components/EChart.vue';import EmptyGuide from '../components/EmptyGuide.vue';import { formatMoney } from '../utils/money.js';import { categoryName,localDate } from '../utils/display.js'
const data=ref({}),categories=ref([]),drawerVisible=ref(false),selectedCategory=ref('other'),details=ref([]),detailTotal=ref(0),detailPage=ref(1),detailPageSize=50,detailLoading=ref(false)
onMounted(async()=>{try{const [overview,categoryList]=await Promise.all([api.get('/analytics/overview'),api.get('/categories')]);data.value=overview;categories.value=categoryList.items}catch(e){ElMessage.error(e.message)}})
const tip={trigger:'axis',valueFormatter:v=>formatMoney(v)};const categoryRows=computed(()=>(data.value.categories||[]).slice().sort((a,b)=>b.net_consumption_minor-a.net_consumption_minor));const detailAmount=computed(()=>details.value.reduce((sum,item)=>sum+(item.flow_type==='refund'?-item.amount_minor:item.amount_minor),0))
const weekly=computed(()=>({tooltip:tip,xAxis:{type:'category',data:(data.value.weekly||[]).map(x=>x.week_start)},yAxis:{type:'value'},series:[{type:'line',smooth:true,data:(data.value.weekly||[]).map(x=>x.net_consumption_minor),color:'#2b9a72'}]}));const monthly=computed(()=>({tooltip:tip,legend:{},xAxis:{type:'category',data:(data.value.monthly||[]).map(x=>x.year_month)},yAxis:{type:'value'},series:['gross_consumption_minor','refund_minor','net_consumption_minor'].map((k,i)=>({name:['实际消费','退款','净消费'][i],type:'bar',data:(data.value.monthly||[]).map(x=>x[k])}))}));const category=computed(()=>({tooltip:{trigger:'item',valueFormatter:v=>formatMoney(v)},series:[{type:'pie',radius:['45%','70%'],data:categoryRows.value.map(x=>({name:categoryName(x.category_code),value:x.net_consumption_minor,categoryCode:x.category_code}))}]}));const dayType=computed(()=>({tooltip:tip,xAxis:{type:'category',data:(data.value.weekday_weekend||[]).map(x=>x.is_weekend?'周末':'工作日')},yAxis:{type:'value'},series:[{type:'bar',data:(data.value.weekday_weekend||[]).map(x=>x.daily_average_minor),color:'#4d7c72'}]}));const categoryTrend=computed(()=>{const top=categoryRows.value.slice(0,5).map(x=>x.category_code),weeks=[...new Set((data.value.category_weekly||[]).map(x=>x.week_start))];return{tooltip:tip,legend:{},xAxis:{type:'category',data:weeks},yAxis:{type:'value'},series:top.map(code=>({name:categoryName(code),type:'line',showSymbol:false,data:weeks.map(w=>(data.value.category_weekly||[]).find(x=>x.week_start===w&&x.category_code===code)?.net_consumption_minor||0)}))}})
function openChartCategory(params){const code=params?.data?.categoryCode;if(code)openCategory(code)}
async function openCategory(code){selectedCategory.value=code;drawerVisible.value=true;await loadDetails(1)}
async function loadDetails(page=1){detailLoading.value=true;detailPage.value=page;try{const result=await api.get(`/transactions?category_code=${encodeURIComponent(selectedCategory.value)}&page=${page}&page_size=${detailPageSize}`);details.value=result.items;detailTotal.value=result.total}catch(e){ElMessage.error(e.message)}finally{detailLoading.value=false}}
async function changeCategory(row,code){try{const result=await api.patch(`/transactions/${row.id}/category`,{category_code:code});ElMessage.success(`已纠正并应用到同组 ${result.updated_transactions} 笔交易`);await loadDetails(detailPage.value);data.value=await api.get('/analytics/overview')}catch(e){ElMessage.error(e.message)}}
const flowName=value=>({consumption:'消费',refund:'退款'}[value]||value);const sourceName=value=>({user:'你已确认',system:'系统规则',unclassified:'待确认'}[value]||value)
</script>
