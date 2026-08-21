# 阶段一：构建静态资源
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# 空字符串 = 同源请求，由 nginx 把 /api 反代到后端
ENV VITE_API_BASE=""
RUN npm run build

# 阶段二：nginx 托管 + 反向代理
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
