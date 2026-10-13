<p align="center">
  <img src="docs/assets/logo.png" alt="Copy-Right logo" width="160" />
</p>

<h1 align="center">Copy-Right</h1>

<p align="center">
  从已有视频反推出可编辑的分镜；替换人物、场景或修改剧情后，<b>只重新生成受影响的镜头</b>。
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="docs/PLAN.md">完整方案</a> ·
  <a href="LICENSE">MIT 许可证</a>
</p>

> **状态：早期开发中（M1 设置向导）。** 目前全程使用 mock 适配器即可跑通，设置向导可以保存并测试真实供应商的 key；真实生成适配器会在后续里程碑接入。

界面默认英文，可在顶部切换为简体中文。

<p align="center">
  <img src="docs/assets/screenshots/08-settings-zh.png" alt="设置 → 模型与密钥" width="800" />
</p>

## 工作流程

1. **上传**视频（3 分钟、30 个镜头以内）。
2. **解析**：PySceneDetect 切镜，多模态模型生成分层分镜（叙事层、视觉层、控制层、资产引用）。每个镜头保留原片段作为事实来源。
3. **编辑**：直接修改分镜卡片，或用自然语言描述剧情改动。
4. **重生成**：每个镜头按改动大小自动选最轻的处理方式：`keep → trim → swap → edit → motion → regenerate`（另有 `new`、`removed`）。没改的镜头逐帧沿用原片。
5. **合成**：FFmpeg 拼接成新视频。

## 供应商自选

所有 AI 能力都通过可插拔适配器调用，共 8 个能力。首次打开时引导选择套餐，也可以按能力自由混用：

| 套餐 | 供应商 |
| --- | --- |
| 国内 | 阿里云百炼、火山方舟、可灵、MiniMax |
| 海外 | Google Gemini、Runway、fal.ai、ElevenLabs、Sync Labs |
| 演示 | 全部 mock，无需 key |

key 只保存在本机，加密存储，保存后不再完整显示。主密钥存放在系统钥匙串（不可用时退回 `data/master.key`）。环境变量优先于已保存的 key。"测试连接"只调用免费的只读接口，不会产生生成费用。

## 快速开始

需要：Python 3.11+、[uv](https://docs.astral.sh/uv/)、[Bun](https://bun.sh)，以及 `PATH` 中的 [FFmpeg](https://ffmpeg.org)。

```bash
# 后端 http://localhost:8000
uv sync
uv run uvicorn app.main:app --reload --port 8000

# 前端 http://localhost:5173（另开一个终端）
cd web
bun install
bun run dev
```

## 合规提示

只上传你拥有使用权（包括肖像权）的素材。导出视频默认带可见的"AI 生成"标识和元数据。在中国大陆发布需遵守《人工智能生成合成内容标识办法》等规定。

## 许可证

[MIT](LICENSE)
