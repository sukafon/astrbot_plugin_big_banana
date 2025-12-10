### Beta: 在项目版本更新到 1.0.0 之前，不保证向后兼容。

<div align="center">

# 🍌 AstrBot Nano Banana Pro 图片生成插件 🍌

![:访问量](https://count.getloli.com/@astrbot_plugin_big_banana?name=astrbot_plugin_big_banana&theme=rule34&padding=7&offset=0&scale=1&pixelated=1&darkmode=auto)

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.5.7%2B-75B9D8.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Nano Banana Pro](https://img.shields.io/badge/Nano%20Banana-2-FFD700.svg)](https://gemini.google/tw/overview/image-generation)

</div>

## 兼容性变更：

- v0.0.5: 即将弃用 `anything` 占位符，请及时修改为 `{{user_text}}`

## 主要特性

- 支持 OpenAI、Gemini 接口规范和流式传输模式。
- 整合 Vertex AI Anonymous 逆向提供商，支持 4K 18MB 图片生成，开箱即用（需能访问 Google）。
- 支持 LLM 函数调用工具，支持通过大语言模型模型阅读预设、整合修改以及图片生成的功能。
- 灵活的参数配置，支持提示词级别的粒度控制。模块化的工具参数传递，方便拓展工具。
- 支持预设提示词和用户文本占位符以及 AI 读改用，高度灵活的提示词控制。
- 支持备用提供商降级调用，增强资源冗余，确保出图成功率。
- 智能补充头像参考和跳过部分@头像。
- 支持群聊和用户白名单配置，以及提示词前缀配置和混合模式，多场景下适用。无操作权限将静默返回，避免频繁打扰。

## 常用命令

- `<触发词>` 使用预设提示词生成图片
- `/lm添加 <触发词> <提示词内容>` 快捷添加预设提示词
- `/lm删除 <触发词>` 快捷删除预设提示词
- `/lm列表` 查看所有预设提示词名称列表
- `/lm提示词 <触发词>` 查看预设的完整提示词
- `/lm白名单添加 <用户/群组> <ID/SID>` 可通过命令增加用户和群组白名单
- `/lm白名单删除 <用户/群组> <ID/SID>` 可通过命令删除用户和群组白名单
- `/lm白名单列表` 查看白名单启用状态和名单列表
