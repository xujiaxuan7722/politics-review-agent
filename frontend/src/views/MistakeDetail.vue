<template>
  <section class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">{{ detail?.title || "错题详情" }}</h1>
        <p class="page-subtitle">查看题目、AI 分析、关键词和关联复习卡。</p>
      </div>
      <div class="button-row">
        <el-button @click="router.push('/mistakes')">返回错题本</el-button>
        <el-button type="success" :loading="generating" @click="generateCards">生成复习卡</el-button>
      </div>
    </div>

    <el-card v-if="detail" class="panel">
      <template #header>
        <div class="toolbar">
          <strong>错题信息</strong>
          <span class="metric-hint">编号 #{{ detail.id }}</span>
        </div>
      </template>

      <div v-if="keywords.length" class="keyword-tags">
        <el-tag v-for="tag in keywords" :key="tag" effect="plain">
          {{ tag }}
        </el-tag>
      </div>

      <el-tabs v-model="activeTab">
        <el-tab-pane label="AI 分析" name="analysis">
          <div class="answer-body" v-html="renderContent(detail.analysis || '')"></div>
        </el-tab-pane>
        <el-tab-pane label="题目文本" name="clean">
          <div class="answer-body" v-html="renderContent(detail.clean_text || '')"></div>
        </el-tab-pane>
        <el-tab-pane label="OCR 原文" name="raw">
          <div class="raw-text-box">
            <pre>{{ detail.raw_text || "暂无原文" }}</pre>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-card v-if="reviewCards.length" class="panel">
      <template #header>
        <strong>关联复习卡</strong>
      </template>

      <div class="card-list">
        <el-card v-for="card in reviewCards" :key="card.id" class="item-card">
          <el-tag v-if="card.knowledge_point" effect="plain">{{ card.knowledge_point }}</el-tag>
          <div class="answer-body compact" style="margin-top: 12px" v-html="renderContent(card.question)"></div>
          <div class="answer-body" style="margin-top: 12px" v-html="renderContent(card.answer)"></div>
          <p class="metric-hint" style="margin-top: 12px">
            已复习 {{ card.repetition || 0 }} 次，当前间隔 {{ card.interval_days || 1 }} 天
          </p>
        </el-card>
      </div>
    </el-card>

    <div v-if="!detail && !loading" class="empty-state">
      没有找到这条错题。
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import request from "../api/request";
import { renderMarkdown } from "../utils/markdown";

const route = useRoute();
const router = useRouter();
const loading = ref(false);
const generating = ref(false);
const detail = ref(null);
const reviewCards = ref([]);
const activeTab = ref("analysis");

const keywords = computed(() =>
  String(detail.value?.knowledge_points || "")
    .split(/[、,，\s]+/)
    .map((tag) => tag.trim())
    .filter(Boolean),
);

function renderContent(text) {
  return renderMarkdown(text || "");
}

async function loadDetail() {
  loading.value = true;

  try {
    const res = await request.get(`/api/mistakes/${route.params.id}`);
    detail.value = res.data.mistake || null;
    reviewCards.value = res.data.review_cards || [];
  } catch (error) {
    ElMessage.error("加载错题详情失败。");
  } finally {
    loading.value = false;
  }
}

async function generateCards() {
  generating.value = true;

  try {
    await request.post(`/api/review/generate-from-mistake/${route.params.id}`);
    ElMessage.success("复习卡已生成");
    await loadDetail();
  } catch (error) {
    ElMessage.error("生成失败，请检查模型接口或余额。");
  } finally {
    generating.value = false;
  }
}

onMounted(loadDetail);
</script>
