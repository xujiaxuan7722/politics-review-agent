<template>
  <section class="page settings-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">个人设置</h1>
        <p class="page-subtitle">管理当前账号信息和登录密码。</p>
      </div>
    </div>

    <el-card class="panel settings-card">
      <template #header>
        <strong>修改密码</strong>
      </template>

      <el-form label-position="top" @submit.prevent>
        <el-form-item label="当前账号">
          <el-input :model-value="username" disabled />
        </el-form-item>
        <el-form-item label="原密码">
          <el-input v-model="form.old_password" type="password" show-password placeholder="请输入原密码" />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="form.new_password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input
            v-model="confirmPassword"
            type="password"
            show-password
            placeholder="再次输入新密码"
            @keyup.enter="submit"
          />
        </el-form-item>
        <div class="settings-action">
          <el-button type="primary" :loading="loading" @click="submit">
            保存新密码
          </el-button>
        </div>
      </el-form>
    </el-card>
  </section>
</template>

<script setup>
import { computed, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import request from "../api/request";

const loading = ref(false);
const confirmPassword = ref("");
const form = reactive({
  old_password: "",
  new_password: "",
});

const username = computed(() => {
  try {
    const user = JSON.parse(localStorage.getItem("auth_user") || "{}");
    return user.username || "当前账号";
  } catch {
    return "当前账号";
  }
});

async function submit() {
  if (form.old_password.length < 6 || form.new_password.length < 6) {
    ElMessage.warning("密码至少 6 位。");
    return;
  }

  if (form.new_password !== confirmPassword.value) {
    ElMessage.warning("两次新密码不一致。");
    return;
  }

  loading.value = true;

  try {
    const res = await request.post("/api/auth/change-password", form);
    localStorage.setItem("auth_token", res.data.token);
    localStorage.setItem("auth_user", JSON.stringify(res.data.user));
    form.old_password = "";
    form.new_password = "";
    confirmPassword.value = "";
    ElMessage.success("密码修改成功");
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "修改失败，请检查原密码。");
  } finally {
    loading.value = false;
  }
}
</script>
