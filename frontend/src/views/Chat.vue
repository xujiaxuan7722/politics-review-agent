<template>
  <section class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">智能问答</h1>
        <p class="page-subtitle">
          概念讲解与知识问答统一入口：先检索内置政治知识库，再由大模型深度作答。
        </p>
      </div>
    </div>

    <el-card class="panel">
      <el-input
        v-model="question"
        type="textarea"
        :rows="5"
        resize="none"
        placeholder="例如：用通俗语言解释实践是认识的来源，或分析新民主主义革命总路线。"
      />

      <div class="chat-action-row">
        <el-button type="primary" :loading="loading" @click="ask">提问</el-button>
        <el-button @click="clearAnswer">清空回答</el-button>
      </div>
    </el-card>

    <el-card v-if="answer" class="panel">
      <template #header>
        <div class="answer-header">
          <strong>回答结果</strong>
          <div class="save-mistake-row">
            <el-input v-model="mistakeTitle" placeholder="保存到错题本的标题" />
            <el-button @click="copyAnswer">复制回答</el-button>
            <el-button type="success" :loading="saving" @click="saveToMistakes">
              写入错题本
            </el-button>
            <el-button type="primary" :loading="cardSaving" @click="createReviewCard">
              生成背诵卡
            </el-button>
          </div>
        </div>
      </template>
      <p v-if="mode" class="metric-hint" style="margin-bottom: 10px">
        {{ mode === "rag" ? "已命中知识库资料，回答基于教材内容生成" : notice || "由大模型直接回答" }}
      </p>
      <div class="answer-body" v-html="renderedAnswer"></div>

      <div v-if="references.length" style="margin-top: 16px">
        <p class="metric-label">知识库引用</p>
        <div class="keyword-tags">
          <el-tag v-for="(ref, i) in references" :key="i" effect="plain">
            {{ ref.heading || "教材片段" }}（相似度 {{ ref.score }}）
          </el-tag>
        </div>
      </div>
    </el-card>

    <div v-else class="empty-state">
      输入一个考研政治概念、材料题思路或错题疑问，回答会在这里整理成可读的复习笔记。
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRoute } from "vue-router";
import request from "../api/request";
import { renderMarkdown } from "../utils/markdown";

const route = useRoute();
const question = ref("");
const answer = ref("");
const mode = ref("");
const notice = ref("");
const references = ref([]);
const mistakeTitle = ref("");
const loading = ref(false);
const saving = ref(false);
const cardSaving = ref(false);

const renderedAnswer = computed(() => renderMarkdown(answer.value));

function clearAnswer() {
  answer.value = "";
  mode.value = "";
  notice.value = "";
  references.value = [];
  mistakeTitle.value = "";
}

function restoreFromRoute() {
  const payload = route.query.payload;
  if (!payload) return;

  try {
    const data = JSON.parse(decodeURIComponent(String(payload)));
    question.value = data.question || "";
  } catch {
    // Ignore malformed route payloads.
  }
}

async function ask() {
  if (!question.value.trim()) {
    ElMessage.warning("请先输入问题。");
    return;
  }

  loading.value = true;
  clearAnswer();

  try {
    const response = await fetch(`${request.defaults.baseURL}/api/chat/ask-stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${localStorage.getItem("auth_token") || ""}`,
      },
      body: JSON.stringify({ question: question.value }),
    });

    if (!response.ok || !response.body) {
      throw new Error(`后端返回 ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const events = buffer.split("\n\n");
      buffer = events.pop() || "";

      for (const event of events) {
        const line = event.trim();
        if (!line.startsWith("data:")) continue;

        let payload;
        try {
          payload = JSON.parse(line.slice(5));
        } catch {
          continue;
        }

        if (payload.type === "meta") {
          mode.value = payload.mode || "";
          notice.value = payload.notice || "";
          references.value = payload.reference || [];
        } else if (payload.type === "delta") {
          answer.value += payload.text;
        } else if (payload.type === "error") {
          throw new Error(payload.detail || "模型服务异常");
        }
      }
    }

    mistakeTitle.value = question.value.slice(0, 28);
  } catch (error) {
    ElMessage.error(error.message || "请求失败，请检查后端、API Key 或模型余额。");
  } finally {
    loading.value = false;
  }
}

async function copyAnswer() {
  try {
    await navigator.clipboard.writeText(answer.value);
    ElMessage.success("已复制回答");
  } catch {
    ElMessage.error("复制失败，请手动选择文本复制。");
  }
}

async function saveToMistakes() {
  if (!answer.value || !question.value.trim()) {
    ElMessage.warning("请先完成一次提问。");
    return;
  }

  saving.value = true;

  try {
    await request.post("/api/mistakes", {
      title: mistakeTitle.value || question.value.slice(0, 28),
      raw_text: question.value,
      clean_text: `## 问题\n${question.value}\n\n## 回答\n${answer.value}`,
      source: "chat",
    });

    ElMessage.success("已写入错题本");
  } catch (error) {
    ElMessage.error("写入失败，请检查错题本接口或模型余额。");
  } finally {
    saving.value = false;
  }
}

async function createReviewCard() {
  if (!answer.value || !question.value.trim()) {
    ElMessage.warning("请先完成一次提问。");
    return;
  }

  cardSaving.value = true;

  try {
    const mistakeRes = await request.post("/api/mistakes", {
      title: mistakeTitle.value || question.value.slice(0, 28),
      raw_text: question.value,
      clean_text: `## 问题\n${question.value}\n\n## 回答\n${answer.value}`,
      source: "chat",
    });

    await request.post(`/api/review/generate-from-mistake/${mistakeRes.data.id}`);
    ElMessage.success("已生成背诵卡，可到今日复习查看。");
  } catch (error) {
    ElMessage.error("生成背诵卡失败，请检查错题本接口、复习接口或模型余额。");
  } finally {
    cardSaving.value = false;
  }
}

onMounted(restoreFromRoute);
</script>
