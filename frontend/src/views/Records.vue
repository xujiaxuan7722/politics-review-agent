<template>
  <section class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">搜索记录</h1>
        <p class="page-subtitle">
          智能问答、拍题判卷、练习与规划的历史记录都保存在服务端，换设备也不会丢。
        </p>
      </div>
      <div class="button-row">
        <el-button type="danger" :disabled="!records.length" :loading="clearing" @click="confirmClearAll">
          清空全部
        </el-button>
        <el-button type="primary" :loading="loading" @click="loadRecords">刷新</el-button>
      </div>
    </div>

    <el-card class="panel">
      <div class="history-toolbar">
        <el-input
          v-model="keyword"
          clearable
          placeholder="搜索记录内容，例如：实践、新民主主义、矛盾"
          @keyup.enter="loadRecords"
          @clear="loadRecords"
        />
        <el-select v-model="source" @change="loadRecords">
          <el-option label="全部来源" value="all" />
          <el-option label="智能问答" value="chat" />
          <el-option label="拍题判卷" value="ocr" />
          <el-option label="练习与规划" value="agents" />
        </el-select>
        <el-button @click="loadRecords">搜索</el-button>
      </div>

      <p class="metric-hint" style="margin-top: 10px">共 {{ records.length }} 条记录</p>
    </el-card>

    <div v-if="records.length" class="card-list">
      <el-card v-for="item in records" :key="item.id" class="item-card">
        <div class="toolbar">
          <div>
            <h3>{{ item.title }}</h3>
            <p class="metric-hint">{{ formatTime(item.created_at) }}</p>
          </div>
          <el-tag effect="plain">{{ item.source_label }}</el-tag>
        </div>

        <p class="metric-hint" style="margin-top: 10px">{{ preview(item) }}</p>

        <div class="button-row" style="margin-top: 12px">
          <el-button @click="openDetail(item)">查看</el-button>
          <el-button v-if="item.source === 'chat'" type="primary" @click="reAsk(item)">
            重新提问
          </el-button>
          <el-button type="danger" :loading="deletingId === item.id" @click="deleteRecord(item)">
            删除
          </el-button>
        </div>
      </el-card>
    </div>

    <div v-else class="empty-state">
      还没有记录。去智能问答提一个问题，或到拍题判卷上传一道题试试。
    </div>

    <el-dialog v-model="detailVisible" width="860px" align-center destroy-on-close>
      <template #header>
        <div>
          <p class="metric-label">{{ selected?.source_label }} · {{ formatTime(selected?.created_at) }}</p>
          <h3>{{ selected?.title }}</h3>
        </div>
      </template>

      <div v-if="selected" class="history-detail">
        <template v-if="selected.question">
          <p class="metric-label">提问 / 题目</p>
          <div class="raw-text-box"><pre>{{ selected.question }}</pre></div>
        </template>

        <p class="metric-label" style="margin-top: 14px">回答</p>
        <div class="answer-body" v-html="renderMarkdown(selected.content || '（无内容）')"></div>
      </div>

      <template #footer>
        <div class="logout-actions">
          <el-button type="primary" @click="detailVisible = false">关闭</el-button>
        </div>
      </template>
    </el-dialog>
  </section>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRouter } from "vue-router";
import request from "../api/request";
import { renderMarkdown } from "../utils/markdown";

const router = useRouter();
const records = ref([]);
const keyword = ref("");
const source = ref("all");
const loading = ref(false);
const clearing = ref(false);
const deletingId = ref(null);
const detailVisible = ref(false);
const selected = ref(null);

function preview(item) {
  return String(item.content || item.question || "无内容")
    .replace(/[#*`]/g, "")
    .replace(/\s+/g, " ")
    .slice(0, 120);
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

async function loadRecords() {
  loading.value = true;
  try {
    const res = await request.get("/api/records", {
      params: { q: keyword.value, source: source.value },
    });
    records.value = res.data;
  } catch (error) {
    ElMessage.error("加载记录失败，请检查后端接口。");
  } finally {
    loading.value = false;
  }
}

function openDetail(item) {
  selected.value = item;
  detailVisible.value = true;
}

function reAsk(item) {
  const payload = encodeURIComponent(JSON.stringify({ question: item.question || item.title }));
  router.push(`/chat?payload=${payload}`);
}

async function deleteRecord(item) {
  deletingId.value = item.id;
  try {
    await request.delete(`/api/records/${item.id}`);
    records.value = records.value.filter((r) => r.id !== item.id);
    ElMessage.success("已删除");
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "删除失败");
  } finally {
    deletingId.value = null;
  }
}

async function confirmClearAll() {
  try {
    await ElMessageBox.confirm(`确定清空全部 ${records.value.length} 条记录吗？`, "清空搜索记录", {
      confirmButtonText: "清空",
      cancelButtonText: "取消",
      type: "warning",
    });
  } catch {
    return;
  }

  clearing.value = true;
  try {
    await request.delete("/api/records");
    records.value = [];
    ElMessage.success("已清空全部记录");
  } catch (error) {
    ElMessage.error("清空失败，请检查后端接口。");
  } finally {
    clearing.value = false;
  }
}

onMounted(loadRecords);
</script>
