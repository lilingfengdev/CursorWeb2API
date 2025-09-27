# Cursor Web to API Bridge

将 [Cursor](https://cursor.com/) 网页聊天转换为与 OpenAI 兼容的 API，让您可以在任何支持 OpenAI API 的第三方应用中使用 Cursor 的强大模型。

## ✨ 特性

- **OpenAI 兼容**: 无缝集成到任何支持 OpenAI API 的工具中 (如：NextChat, LobeChat 等)。
- **支持流式传输**: 实时获取响应，体验与原生网页版一致。
- **多模型支持**: 支持 Cursor 提供的所有最新模型。
- **易于部署**: 只需一个 Python 后端和一个浏览器脚本即可运行。

## 🚀 工作原理

本项目由两部分组成：

1.  **Python FastAPI 后端 (`main.py`)**:
    - 启动一个本地 HTTP 服务器，提供与 OpenAI 对齐的 `/v1/chat/completions` 和 `/v1/models` 接口。
    - 维护一个 WebSocket 服务 (`/ws`)，用于与浏览器脚本通信。
    - 接收到 API 请求后，通过 WebSocket 将其转发给浏览器脚本。

2.  **浏览器用户脚本 (`cursor-ws-bridge.user.js`)**:
    - 需要通过 [Tampermonkey](https://www.tampermonkey.net/) 等扩展安装在浏览器中。
    - 当您打开 Cursor 网站时，此脚本会自动运行并连接到本地的 Python WebSocket 服务。
    - 脚本会拦截 Cursor 网站的前端 `fetch` 请求。当从后端收到聊天请求时，它会模拟一次真实的网站聊天请求，并将 Cursor 返回的数据流通过 WebSocket 传回给后端。

整个流程如下：
```
[第三方应用] -> [FastAPI 后端] -> [WebSocket] -> [浏览器脚本] -> [Cursor 官网 API] -> [AI 模型]
```
响应数据再按原路返回。

## ⚙️ 如何使用

### 步骤 1: 准备环境

- 安装 [Python 3.8+](https://www.python.org/downloads/)。
- 安装一个用户脚本管理器，如 [Tampermonkey](https://www.tampermonkey.net/) (推荐)。

### 步骤 2: 启动后端服务

1.  克隆或下载本仓库。
2.  安装 Python 依赖：
    ```bash
    pip install -r requirements.txt
    ```
3.  启动 FastAPI 服务器：
    ```bash
    # 设置您的 API 密钥 (在 Linux/macOS)
    export CURSOR_API_KEY="your-secret-key"

    # (在 Windows PowerShell)
    # $env:CURSOR_API_KEY="your-secret-key"

    python main.py
    ```
    服务默认运行在 `http://localhost:8765`。

### 步骤 3: 安装用户脚本

1.  打开浏览器的 Tampermonkey 扩展仪表盘。
2.  选择 "新建脚本"。
3.  将 `cursor-ws-bridge.user.js` 的全部内容复制并粘贴到编辑器中。
4.  保存脚本。

### 步骤 4: 运行

1.  确保后端服务 (`main.py`) 正在运行。
2.  在安装了用户脚本的浏览器中，打开 [Cursor](https://cursor.com/) 官网并保持该页面打开。
3.  脚本会自动连接到后端。您可以在浏览器开发者工具的控制台中看到 `[cursor-ws-bridge]` 相关的日志，或者访问 `http://localhost:8765` 查看服务状态。
    ```json
    {
      "status": "running",
      "browser_connected": true
    }
    ```
    当 `browser_connected` 为 `true` 时，代表一切就绪。

### 步骤 5: 调用 API

现在您可以在任何支持 OpenAI API 的应用中配置使用此接口了。

- **API 地址**: `http://localhost:8765/v1`
- **API 密钥**: `your-secret-key` (或者您在环境变量中设置的值)
- **模型名称**: 从下面的模型列表中选择

这是一个使用 `curl` 的示例：

```bash
curl http://localhost:8765/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-secret-key" \
  -d '{
    "model": "gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": "你好，请介绍一下你自己"
      }
    ],
    "stream": true
  }'
```

## 🧠 可用模型

您可以通过访问 `http://localhost:8765/v1/models` 端点获取实时模型列表。当前支持的模型包括：

| 模型 ID               | 所属机构   |
|-----------------------|------------|
| `gpt-5`               | openai     |
| `gpt-5-codex`         | openai     |
| `gpt-5-mini`          | openai     |
| `gpt-5-nano`          | openai     |
| `gpt-4.1`             | openai     |
| `gpt-4o`              | openai     |
| `claude-3.5-sonnet`   | anthropic  |
| `claude-3.5-haiku`    | anthropic  |
| `claude-3.7-sonnet`   | anthropic  |
| `claude-4-sonnet`     | anthropic  |
| `claude-4-opus`       | anthropic  |
| `claude-4.1-opus`     | anthropic  |
| `gemini-2.5-pro`      | google     |
| `gemini-2.5-flash`    | google     |
| `o3`                  | openai     |
| `o4-mini`             | openai     |
| `deepseek-r1`         | deepseek   |
| `deepseek-v3.1`       | deepseek   |
| `kimi-k2-instruct`    | moonshot-ai|
| `grok-3`              | xai        |
| `grok-3-mini`         | xai        |
| `grok-4`              | xai        |

## ⚠️ 注意事项

- 本项目依赖于 Cursor 网站的前端实现，如果官网进行大的改版，用户脚本可能需要适配更新。
- 请确保浏览器页面始终处于打开状态，否则 API 将无法工作。
- 这是一个社区项目，与 Cursor 官方无关。请合理使用。
