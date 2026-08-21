import axios from "axios";

// VITE_API_BASE 为空字符串时表示同源（由 nginx 反向代理 /api），未设置时默认本地后端
const apiBase = import.meta.env.VITE_API_BASE;

const request = axios.create({
  baseURL: apiBase !== undefined ? apiBase : "http://127.0.0.1:8002",
  timeout: 180000,
});

request.interceptors.request.use((config) => {
  const token = localStorage.getItem("auth_token");

  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }

  return config;
});

export default request;
