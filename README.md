# Jimeng API to OpenAI 兼容接口

将即梦图片/视频生成 API 封装为 OpenAI Chat Completions 兼容格式，分别提供两个独立的 Docker 服务和各自的实时 Web Dashboard。

## 快速开始

```bash
# 启动服务
docker compose up -d --build

# 查看日志
docker compose logs -f
```

## 访问地址

- **图片服务 Dashboard / API**: http://localhost:18566
- **视频服务 Dashboard / API**: http://localhost:18567

## 部署结构

- `image-service` 独立运行在 `18566`
- `video-service` 独立运行在 `18567`
- 两个服务共用同一套应用代码，但分别使用各自的数据目录：
  `./data/image` 和 `./data/video`

## 管理界面功能

- 📊 实时调用统计和成功率
- 🔑 Session 账号池管理
- 📁 批量导入账号（支持拖拽上传）
- ⚙️ 即梦 API 地址配置
- 🔄 自动失败重试和账号禁用
- 📋 调用记录和图片预览
- 🌐 WebSocket 实时推送

## 连接状态说明

管理界面右上角的“已连接 / 未连接”表示的是 **Dashboard 与当前服务实例之间的 WebSocket 连接状态**，不是图片/视频生成接口本身的业务可用性状态。

- `http://localhost:18566` 对应图片服务的 Dashboard WebSocket
- `http://localhost:18567` 对应视频服务的 Dashboard WebSocket

如果图片页显示“已连接”，而视频页显示“未连接”，通常说明是视频服务管理页的 WebSocket 代理链路有问题，而不是视频生成接口一定不可用。

## 常见问题

### 视频管理页一直显示未连接

如果视频侧导入了大量 Session，Dashboard 建立连接时会先接收完整的统计和 Session 列表。旧版部署使用统一网关代理 WebSocket 时，视频侧首包过大可能导致连接被直接断开，页面右上角会一直显示“未连接”。

当前版本已经修复这个问题。更新后重新构建并启动服务：

```bash
docker compose up -d --build
```

然后刷新 `http://localhost:18567` 即可。

## API 使用

### 图片生成

```bash
curl -X POST "http://localhost:18566/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "jimeng-4.5",
    "messages": [{"role": "user", "content": "一只可爱的猫咪"}],
    "stream": true
  }'
```

### 视频生成

```bash
curl -X POST "http://localhost:18567/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "jimeng-video-3.5-pro",
    "messages": [{"role": "user", "content": "一只猫在奔跑"}],
    "stream": true
  }'
```

## 账号管理

在管理界面中点击"导入 Session"，上传格式如下的 txt 文件：

```
email=user1@example.com----password=pass123----sessionid=hk-xxxxx;
email=user2@example.com----password=pass456----sessionid=hk-yyyyy;
```

## 配置说明

可通过 `.env` 覆盖两个服务的后端地址和端口，默认示例见 `.env.example`：

```bash
IMAGE_JIMENG_BASE_URL=http://8.209.250.40:5100
IMAGE_SERVICE_PORT=18566
VIDEO_JIMENG_BASE_URL=http://8.209.250.40:5100
VIDEO_SERVICE_PORT=18567
```

也可以在各自管理界面的“设置”中修改即梦 API 地址。

## 管理命令

```bash
# 停止服务
docker compose down

# 重启服务
docker compose restart

# 查看状态
docker compose ps

# 重新构建
docker compose up -d --build
```
