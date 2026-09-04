<template>
  <section class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">练习与规划</h1>
        <p class="page-subtitle">
          学习管家可自主调度知识库、错题数据、出题和制卡工具；也可单独使用出题、批改、规划智能体。
        </p>
      </div>
    </div>

    <el-card class="panel">
      <el-tabs v-model="activeAgent">
        <el-tab-pane label="学习管家" name="manager">
          <p class="metric-hint" style="margin-bottom: 10px">
            用自然语言下达任务，管家会自己决定调用哪些工具（检索知识库 / 查你的学习状态 / 出题 / 建复习卡）并执行。
          </p>
          <div v-if="conversationMessages.length" class="conv-transcript">
            <div v-for="m in conversationMessages" :key="m.id" :class="['conv-msg', m.role]">
              <span class="conv-role">{{ m.role === 'user' ? '你' : '管家' }}</span>
              <div class="conv-body" v-html="renderMarkdown(m.content)"></div>
            </div>
          </div>
          <el-input
            v-model="managerInstruction"
            type="textarea"
            :rows="4"
            :placeholder="conversationId ? '接着说，例如：再出一道类似的；把刚才那题的结论做成复习卡' : '例如：看看我最薄弱的考点，讲解一下，再出一道题考我；或：把「实践是检验真理的唯一标准」做成一张背诵卡'"
          />
          <div style="margin-top: 14px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap">
            <el-button type="primary" :loading="managerLoading" @click="runManager">
              {{ conversationId ? '继续对话' : '交给管家执行' }}
            </el-button>
            <el-button v-if="conversationId" :disabled="managerLoading" @click="newConversation">新对话</el-button>
            <el-select
              v-model="conversationPick"
              placeholder="回到之前的对话"
              size="default"
              style="min-width: 220px"
              clearable
              @change="openConversation"
            >
              <el-option v-for="c in conversationList" :key="c.id" :label="c.title" :value="c.id" />
            </el-select>
          </div>

          <div v-if="managerSteps.length || managerPhase" style="margin-top: 18px">
            <p class="metric-label">管家执行轨迹</p>
            <div class="keyword-tags">
              <el-tag
                v-for="(step, i) in managerSteps"
                :key="i"
                :type="step.status === 'failed' ? 'danger' : step.status === 'running' ? 'warning' : 'primary'"
                effect="plain"
              >
                {{ i + 1 }}. {{ step.label }}{{ step.status === 'running' ? '…' : step.status === 'failed' ? '（失败）' : '' }}
              </el-tag>
              <el-tag v-if="managerPhase" type="info" effect="plain">{{ managerPhase }}</el-tag>
            </div>
            <p v-if="managerModel" class="metric-hint" style="margin-top: 8px">
              模型：{{ managerModel }}<span v-if="managerDegraded">（主端点不可用，已降级）</span>
            </p>
          </div>
        </el-tab-pane>

        <el-tab-pane label="生成练习题" name="quiz">
          <el-input v-model="quizTopic" placeholder="例如：马克思主义基本原理、毛中特、史纲、思修法基" />
          <div class="quiz-type-card">
            <span>题型</span>
            <div class="quiz-type-options">
              <button
                v-for="item in quizTypeOptions"
                :key="item.value"
                type="button"
                :class="{ active: quizType === item.value }"
                @click="quizType = item.value"
              >
                {{ item.label }}
              </button>
            </div>
          </div>
          <div class="quiz-difficulty-card">
            <div class="quiz-difficulty-head">
              <span>难度 {{ difficulty }}/5</span>
            </div>
            <el-slider v-model="difficulty" :min="1" :max="5" show-stops />
          </div>
          <el-button type="primary" :loading="loading" @click="runQuiz">生成题目</el-button>
        </el-tab-pane>

        <el-tab-pane label="批改答案" name="grade">
          <el-input v-model="gradeQuestion" type="textarea" :rows="5" placeholder="粘贴题目" />
          <el-input
            v-model="studentAnswer"
            type="textarea"
            :rows="5"
            placeholder="粘贴学生答案"
            style="margin-top: 12px"
          />
          <div style="margin-top: 12px; display: flex; gap: 12px; align-items: center">
            <span class="metric-hint">题型</span>
            <el-radio-group v-model="gradeQuestionType" size="small">
              <el-radio-button value="single">单选</el-radio-button>
              <el-radio-button value="multi">多选</el-radio-button>
              <el-radio-button value="unknown">不确定</el-radio-button>
            </el-radio-group>
          </div>
          <el-button type="primary" :loading="loading" style="margin-top: 14px" @click="runGrade">
            批改答案
          </el-button>
        </el-tab-pane>

        <el-tab-pane label="复习规划" name="plan">
          <p class="metric-hint" style="margin-bottom: 10px">
            规划智能体会自动读取你错题本和复习卡里的真实数据；下面的补充说明可以不填。
          </p>
          <el-input
            v-model="mistakesSummary"
            type="textarea"
            :rows="4"
            placeholder="可选补充，例如：明天只有 2 小时、想主攻史纲时间线"
          />
          <el-button type="primary" :loading="loading" style="margin-top: 14px" @click="runPlan">
            生成复习计划
          </el-button>
          <p v-if="planNotice" class="metric-hint" style="margin-top: 10px">{{ planNotice }}</p>

          <div v-if="planSteps.length" style="margin-top: 18px">
            <p class="metric-label">管家取数轨迹</p>
            <div class="keyword-tags">
              <el-tag v-for="(step, i) in planSteps" :key="i" type="primary" effect="plain">
                {{ i + 1 }}. {{ step.label }}
              </el-tag>
            </div>
          </div>
          <p v-else-if="planMode === 'fallback'" class="metric-hint" style="margin-top: 10px">
            本次由基础规划模块生成（学习管家未成功取数）。
          </p>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-card v-if="answer" class="panel">
      <template #header>
        <div class="answer-header">
          <strong>学习助手输出</strong>
          <div class="save-mistake-row">
            <el-input v-model="mistakeTitle" placeholder="保存到错题本的标题" />
            <el-button @click="copyAnswer">复制输出</el-button>
            <el-button type="success" :loading="saving" @click="saveCurrentToMistakes">
              生成错题卡
            </el-button>
          </div>
        </div>
      </template>
      <div class="answer-body agent-output" :class="`agent-output-${activeAgent}`" v-html="renderContent(answer)"></div>
    </el-card>

    <div v-else class="empty-state">
      选择一个学习功能并输入任务，结果会显示在这里。
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
const activeAgent = ref("manager");
const quizTopic = ref("");
const planNotice = ref("");
const planSteps = ref([]);
const planMode = ref("");
const managerInstruction = ref("");
const managerLoading = ref(false);
const managerSteps = ref([]);
const conversationId = ref(null);
const conversationMessages = ref([]);
const conversationList = ref([]);
const conversationPick = ref(null);
const managerPhase = ref("");

async function refreshConversationList() {
  try {
    const res = await request.get("/api/agents/conversations");
    conversationList.value = res.data || [];
  } catch {
    conversationList.value = [];
  }
}

async function openConversation(id) {
  if (!id) return;
  try {
    const res = await request.get(`/api/agents/conversations/${id}`);
    conversationId.value = res.data.id;
    conversationMessages.value = res.data.messages || [];
    answer.value = "";
    managerSteps.value = [];
  } catch {
    ElMessage.error("读取对话失败。");
  }
}

function newConversation() {
  conversationId.value = null;
  conversationMessages.value = [];
  conversationPick.value = null;
  answer.value = "";
  managerSteps.value = [];
  managerModel.value = "";
}
const managerModel = ref("");
const managerDegraded = ref(false);

let pendingInstruction = "";

function applyManagerEvent(payload) {
  if (payload.type === "thinking") {
    managerPhase.value = payload.wrap_up ? "整理最终答复…" : `管家思考中（第 ${payload.round} 轮）…`;
  } else if (payload.type === "step") {
    if (payload.status === "running") {
      managerPhase.value = "";
      managerSteps.value.push({ tool: payload.tool, label: payload.label, args: payload.args, status: "running" });
    } else {
      const idx = managerSteps.value.findIndex((s) => s.status === "running" && s.tool === payload.tool);
      const done = { tool: payload.tool, label: payload.label, args: payload.args, status: payload.status };
      if (idx >= 0) managerSteps.value.splice(idx, 1, done);
      else managerSteps.value.push(done);
    }
  } else if (payload.type === "meta") {
    conversationId.value = payload.conversation_id || conversationId.value;
  } else if (payload.type === "done") {
    managerPhase.value = "";
    answer.value = payload.answer || "";
    if (payload.conversation_id) conversationId.value = payload.conversation_id;
    if (payload.answer) {
      conversationMessages.value.push(
        { id: `u-${Date.now()}`, role: "user", content: pendingInstruction },
        { id: `a-${Date.now()}`, role: "assistant", content: payload.answer },
      );
    }
    managerModel.value = payload.model || "";
    managerDegraded.value = Boolean(payload.degraded);
    if (Array.isArray(payload.steps) && payload.steps.length) {
      managerSteps.value = payload.steps.map((s) => ({ ...s, status: "done" }));
    }
  } else if (payload.type === "error") {
    throw new Error(payload.detail || "学习管家执行失败");
  }
}

async function runManager() {
  if (!managerInstruction.value.trim()) {
    ElMessage.warning("请先描述要交给管家的任务。");
    return;
  }

  managerLoading.value = true;
  pendingInstruction = managerInstruction.value.trim();
  managerSteps.value = [];
  managerPhase.value = "连接管家…";
  managerModel.value = "";
  managerDegraded.value = false;
  answer.value = "";

  try {
    const response = await fetch(`${request.defaults.baseURL}/api/agents/manager-stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${localStorage.getItem("auth_token") || ""}`,
      },
      body: JSON.stringify({ instruction: managerInstruction.value, conversation_id: conversationId.value }),
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
        applyManagerEvent(payload);
      }
    }
    mistakeTitle.value = managerInstruction.value.trim().slice(0, 28);
    managerInstruction.value = "";
    refreshConversationList();
  } catch (error) {
    ElMessage.error(error.message || "学习管家执行失败，请稍后重试。");
  } finally {
    managerPhase.value = "";
    managerLoading.value = false;
  }
}
const quizType = ref("single");
const difficulty = ref(3);
const gradeQuestion = ref("");
const studentAnswer = ref("");
const gradeQuestionType = ref("unknown");
const mistakesSummary = ref("");
const answer = ref("");
const mistakeTitle = ref("");
const loading = ref(false);
const saving = ref(false);

const difficultyText = computed(() => {
  const labels = {
    1: "基础识记",
    2: "概念理解",
    3: "常规应用",
    4: "材料辨析",
    5: "综合分析",
  };
  return labels[difficulty.value] || "常规应用";
});

const agentLabels = {
  manager: "学习管家",
  quiz: "生成练习题",
  grade: "批改答案",
  plan: "复习规划",
};

const quizTypeOptions = [
  { label: "单选题", value: "single" },
  { label: "分析题", value: "analysis" },
];

const quizTypeLabel = computed(() => {
  return quizTypeOptions.find((item) => item.value === quizType.value)?.label || "单选题";
});

function renderContent(text) {
  const displayText = activeAgent.value === "quiz" ? normalizeQuizDisplay(text || "", quizType.value) : text || "";
  return renderMarkdown(displayText);
}

function normalizeQuizDisplay(text, selectedType = "single") {
  let skippingMulti = false;
  const lines = [];
  const expectedHeading = selectedType === "analysis" ? "## 分析题" : "## 单选题";
  let currentSection = "";
  let keptExpectedSection = false;

  for (const line of String(text || "").split(/\r?\n/)) {
    const normalized = normalizeQuizLine(line);
    if (normalized === "__SKIP_MULTI_SECTION__") {
      skippingMulti = true;
      continue;
    }
    if (normalized === "## 单选题" || normalized === "## 分析题") {
      skippingMulti = false;
      if (normalized !== expectedHeading || keptExpectedSection) {
        currentSection = "";
        continue;
      }
      currentSection = normalized;
      keptExpectedSection = true;
    }
    if (skippingMulti || normalized === null || currentSection !== expectedHeading) continue;
    if (selectedType === "analysis" && /^[A-D]\.\s+/.test(normalized)) continue;
    if (selectedType === "analysis" && /^考点：/.test(normalized)) continue;
    if (selectedType === "analysis" && !["## 分析题"].includes(normalized) && !/^题干：/.test(normalized)) {
      continue;
    }
    if (selectedType === "single" && !["## 单选题"].includes(normalized) && !/^(题干|考点)：/.test(normalized) && !/^[A-D]\.\s+/.test(normalized)) {
      continue;
    }
    lines.push(normalized);
  }

  return lines
    .join("\n")
    .replace(/(## 单选题\n)(?!题干：)/, `$1题干：关于${quizTopic.value || "当前知识点"}，下列说法正确的是（ ）。\n`)
    .replace(/(## 分析题\n)题干：\s*(?=\n|$)/, `$1题干：请结合${quizTopic.value || "当前知识点"}进行分析。\n`)
    .replace(/(## 分析题\n)(?!题干：)/, `$1题干：请结合${quizTopic.value || "当前知识点"}进行分析。\n`)
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function normalizeQuizLine(line) {
  let value = String(line || "").trim();
  if (!value) return "";

  if (/请清理|请整理|重新整理|修复说明|原始代码|原始文本|下面是整理/.test(value)) {
    return null;
  }

  if (/^[|丨\s]+$/.test(value)) return null;
  if (/^E\s*[.．、:：]/i.test(value)) return null;

  const heading = value.replace(/^#{1,6}\s*/, "").trim();
  if (/单题题|单选/.test(heading)) return "## 单选题";
  if (/多选多多|多选/.test(heading)) return "__SKIP_MULTI_SECTION__";
  if (/分析/.test(heading)) return "## 分析题";

  value = value.replace(/^题\s*[DEＥ]\s*[.．、:：]?\s*/i, "题干：");
  value = value.replace(/^题干\s*[.．、:：]?\s*/, "题干：");
  value = value.replace(/^考点\s*[DEＥ]?\s*[.．、:：]?\s*/i, "考点：");
  value = value.replace(/^作答要求\s*[DEＥ]?\s*[.．、:：]?\s*/i, "作答要求：");

  const option = value.match(/^([A-D])\s*[DEＥ]?\s*[.．、:：]?\s*(.+)$/i);
  if (option) {
    return `${option[1].toUpperCase()}. ${stripQuizNoise(option[2])}`;
  }

  return stripQuizNoise(value);
}

function stripQuizNoise(text) {
  return String(text || "")
    .replace(/\b([A-Za-z])\b(?:\s+\1\b){2,}/g, "")
    .replace(/([A-Za-z])\1{2,}/g, "")
    .replace(/(?<=[\u4e00-\u9fa5，。；、])\s*[DEＥ]+\s*(?=[\u4e00-\u9fa5，。；、])/gi, "")
    .replace(/(?<=[\u4e00-\u9fa5，。；、])\s*[DEＥ]+$/gi, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function restoreFromRoute() {
  const payload = route.query.payload;
  if (!payload) return;

  try {
    const data = JSON.parse(decodeURIComponent(String(payload)));
    activeAgent.value = agentLabels[data.activeAgent] ? data.activeAgent : "quiz";
    quizTopic.value = data.quizTopic || "";
    quizType.value = data.quizType || "single";
    difficulty.value = data.difficulty || 3;
    gradeQuestion.value = data.gradeQuestion || "";
    studentAnswer.value = data.studentAnswer || "";
    mistakesSummary.value = data.mistakesSummary || "";
  } catch {
    // Ignore malformed route payloads.
  }
}

async function runAgent(url, payload) {
  loading.value = true;
  answer.value = "";

  try {
    const res = await request.post(url, payload);
    answer.value = res.data.answer || "";
    planNotice.value = res.data.notice || "";
    planSteps.value = res.data.steps || [];
    planMode.value = res.data.mode || "";
    if (answer.value) {
      mistakeTitle.value = buildDefaultMistakeTitle();
    }
  } catch (error) {
    ElMessage.error("AI 学习助手调用失败，请检查后端、API Key 或模型余额。");
  } finally {
    loading.value = false;
  }
}

function runQuiz() {
  if (!quizTopic.value.trim()) return ElMessage.warning("请输入出题主题。");
  return runAgent("/api/agents/quiz", {
    topic: quizTopic.value,
    difficulty: difficulty.value,
    quiz_type: quizType.value,
  });
}

function runGrade() {
  if (!gradeQuestion.value.trim() || !studentAnswer.value.trim()) {
    return ElMessage.warning("请输入题目和学生答案。");
  }
  return runAgent("/api/agents/grade", {
    question: gradeQuestion.value,
    student_answer: studentAnswer.value,
    question_type: gradeQuestionType.value,
  });
}

function runPlan() {
  return runAgent("/api/agents/plan", { mistakes_summary: mistakesSummary.value });
}

function buildDefaultMistakeTitle() {
  if (activeAgent.value === "manager") return managerInstruction.value.trim().slice(0, 28) || "学习管家卡";
  if (activeAgent.value === "quiz") return `${quizTypeLabel.value}：${quizTopic.value.trim().slice(0, 24) || "练习题卡"}`;
  if (activeAgent.value === "grade") return gradeQuestion.value.trim().slice(0, 28) || "批改错题";
  if (activeAgent.value === "plan") return mistakesSummary.value.trim().slice(0, 28) || "复习规划卡";
  return "政治复习卡";
}

function buildCurrentMistakePayload() {
  if (activeAgent.value === "quiz") {
    return {
      rawText: quizTopic.value,
      cleanText: normalizeQuizDisplay(answer.value, quizType.value),
    };
  }

  if (activeAgent.value === "grade") {
    return {
      rawText: gradeQuestion.value,
      cleanText: answer.value,
    };
  }

  if (activeAgent.value === "manager") {
    return {
      rawText: managerInstruction.value,
      cleanText: answer.value,
    };
  }

  return {
    rawText: mistakesSummary.value,
    cleanText: answer.value,
  };
}

async function copyAnswer() {
  try {
    await navigator.clipboard.writeText(answer.value);
    ElMessage.success("已复制输出");
  } catch {
    ElMessage.error("复制失败，请手动选择文本复制。");
  }
}

async function saveCurrentToMistakes() {
  if (!answer.value) {
    ElMessage.warning("请先生成学习助手输出。");
    return;
  }

  if (!mistakeTitle.value.trim()) {
    ElMessage.warning("请填写错题卡标题。");
    return;
  }

  const payload = buildCurrentMistakePayload();
  saving.value = true;

  try {
    await request.post("/api/mistakes", {
      title: mistakeTitle.value,
      raw_text: payload.rawText,
      clean_text: payload.cleanText,
      source: "agents",
    });

    ElMessage.success("已生成错题卡");
  } catch (error) {
    ElMessage.error("生成失败，请检查登录状态、错题本接口或模型余额。");
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  restoreFromRoute();
  refreshConversationList();
});
</script>
