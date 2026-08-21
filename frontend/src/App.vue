<template>
  <router-view v-if="isAuthPage" />

  <el-container v-else class="app-layout">
    <el-aside width="248px" class="sidebar">
      <div class="brand">
        <div class="brand-mark">政</div>
        <div class="brand-copy">
          <h1 class="brand-title">
            <span>考研政治</span>
            <span>复习系统</span>
          </h1>
          <p>抗遗忘复习工作台</p>
        </div>
      </div>

      <el-menu router :default-active="$route.path" class="nav-menu">
        <el-menu-item v-for="item in navItems" :key="item.path" :index="item.path">
          <img class="nav-icon-fixed" :src="item.icon" alt="" />
          <span>{{ item.label }}</span>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-note">
        <strong>今日政治重点</strong>
        <span>先整理错题与材料题，再进入背诵卡复习，最后用知识库补齐薄弱概念。</span>
      </div>
    </el-aside>

    <el-container>
      <el-header class="topbar">
        <div>
          <p class="eyebrow">Politics Review</p>
          <h2>考研政治抗遗忘复习系统</h2>
        </div>
        <div class="topbar-actions">
          <div class="status-pill">
            <span></span>
            后端 8002
          </div>
          <div class="user-pill">{{ username }}</div>
          <el-button @click="router.push('/settings')">个人设置</el-button>
          <button class="logout-soft-button" type="button" @click="confirmLogout">
            <span></span>
            退出
          </button>
        </div>
      </el-header>

      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>

  <el-dialog
    v-model="logoutDialogVisible"
    class="logout-dialog"
    width="430px"
    align-center
    :show-close="false"
  >
    <div class="logout-card-head">
      <div class="logout-mark" aria-hidden="true">
        <span class="logout-orbit"></span>
        <span class="logout-node"></span>
        <span class="logout-arrow"></span>
      </div>
      <div>
        <h3>确认退出当前账号？</h3>
        <p>退出后会回到登录页，本地登录状态会被清理。</p>
      </div>
    </div>

    <div class="logout-account">
      <span>当前账号</span>
      <strong>{{ username }}</strong>
    </div>

    <template #footer>
      <div class="logout-actions">
        <el-button :disabled="loggingOut" @click="logoutDialogVisible = false">
          继续学习
        </el-button>
        <el-button type="primary" :loading="loggingOut" @click="logout">
          确认退出
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import request from "./api/request";

const iconWrap = "%3Crect width='40' height='40' rx='12' fill='%23e8f0ff'/%3E";
const iconStroke = "fill='none' stroke='%232563eb' stroke-width='2.8' stroke-linecap='round' stroke-linejoin='round'";

function svgIcon(paths) {
  return `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40' viewBox='0 0 40 40'%3E${iconWrap}${paths}%3C/svg%3E`;
}

const navItems = [
  {
    path: "/",
    label: "学习总览",
    icon: svgIcon(
      `%3Cpath d='M12 20.5 20 13l8 7.5' ${iconStroke}/%3E%3Cpath d='M15 20v8h10v-8' ${iconStroke}/%3E`,
    ),
  },
  {
    path: "/chat",
    label: "智能问答",
    icon: svgIcon(
      `%3Cpath d='M13 15.5h14a4 4 0 0 1 4 4v3.5a4 4 0 0 1-4 4h-5.5L16 31v-4h-3a4 4 0 0 1-4-4v-3.5a4 4 0 0 1 4-4Z' ${iconStroke}/%3E`,
    ),
  },
  {
    path: "/ocr",
    label: "拍题判卷",
    icon: svgIcon(
      `%3Cpath d='M14 10h12a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H14a2 2 0 0 1-2-2V12a2 2 0 0 1 2-2Z' ${iconStroke}/%3E%3Cpath d='M16 17h8M16 22h8' ${iconStroke}/%3E`,
    ),
  },
  {
    path: "/mistakes",
    label: "错题本",
    icon: svgIcon(
      `%3Cpath d='M20 11v13' ${iconStroke}/%3E%3Cpath d='M20 29h.01' ${iconStroke}/%3E`,
    ),
  },
  {
    path: "/review",
    label: "今日复习",
    icon: svgIcon(
      `%3Cpath d='M28 18a8 8 0 1 0-2.4 5.7' ${iconStroke}/%3E%3Cpath d='M28 13v5h-5' ${iconStroke}/%3E`,
    ),
  },
  {
    path: "/agents",
    label: "练习与规划",
    icon: svgIcon(
      `%3Cpath d='M12 20h16' ${iconStroke}/%3E%3Cpath d='M20 12a8 8 0 1 1 0 16 8 8 0 0 1 0-16Z' ${iconStroke}/%3E`,
    ),
  },
  {
    path: "/records",
    label: "搜索记录",
    icon: svgIcon(
      `%3Cpath d='M18 12a6 6 0 1 1 0 12 6 6 0 0 1 0-12Z' ${iconStroke}/%3E%3Cpath d='m23 23 5 5' ${iconStroke}/%3E`,
    ),
  },
];

const route = useRoute();
const router = useRouter();
const logoutDialogVisible = ref(false);
const loggingOut = ref(false);
const currentUser = ref(readCurrentUser());
const isAuthPage = computed(() => route.meta.public === true);
const username = computed(() => {
  return currentUser.value?.username || "当前账号";
});

function readCurrentUser() {
  try {
    const user = JSON.parse(localStorage.getItem("auth_user") || "{}");
    return user && typeof user === "object" ? user : {};
  } catch {
    return {};
  }
}

function refreshCurrentUser() {
  currentUser.value = readCurrentUser();
}

function confirmLogout() {
  logoutDialogVisible.value = true;
}

async function logout() {
  loggingOut.value = true;

  try {
    await request.post("/api/auth/logout");
  } catch (error) {
    // 即使后端登出失败，也要清理本地登录状态，避免 token 过期后卡住。
  } finally {
    loggingOut.value = false;
    logoutDialogVisible.value = false;
  }

  localStorage.removeItem("auth_token");
  localStorage.removeItem("auth_user");
  refreshCurrentUser();
  router.push("/login");
}

watch(() => route.fullPath, refreshCurrentUser, { immediate: true });
onMounted(() => window.addEventListener("auth-user-updated", refreshCurrentUser));
onBeforeUnmount(() => window.removeEventListener("auth-user-updated", refreshCurrentUser));
</script>
