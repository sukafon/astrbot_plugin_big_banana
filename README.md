<div align="center">

# 🍌 AstrBot Nano Banana Pro 图片生成插件 🍌

![:访问量](https://count.getloli.com/@astrbot_plugin_big_banana?name=astrbot_plugin_big_banana&theme=rule34&padding=5&offset=0&scale=1&pixelated=1&darkmode=auto)

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.10.0%2B-75B9D8.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Nano Banana Pro](https://img.shields.io/badge/Nano%20Banana-2-FFD700.svg)](https://gemini.google/tw/overview/image-generation)

</div>

## 兼容性变更：

- v0.1.2: `--append_mode` 参数更名为 `--gather_mode` 以增强语义，`--append_mode` 默认作为新参数的别名，可在配置文件中修改。
- v0.1.0：代码重构，务必在插件配置中重新检查配置项。配置文件 `key` 字段变更为 `keys`，需要重新配置 `Key`。部分字段位置变更。
- v0.0.5：`anything` 占位符已于 v0.1.0 移除，请改用替代占位符 `{{user_text}}`。

## 主要特性

- 支持 OpenAI、Gemini 接口规范和流式传输模式。
- 整合 Vertex AI Anonymous 逆向提供商，免费无限的 4K 18MB PNG（无损压缩） 图片生成，开箱即用（需能访问 Google）。
- 支持 LLM 函数调用工具，支持通过大语言模型模型阅读预设、整合修改以及图片生成的功能。
- 灵活的参数配置，支持提示词级别的粒度控制，和方便拓展工具。
- 支持预设提示词和用户文本占位符以及 AI 读改用，高度灵活的提示词控制。
- 支持备用提供商降级调用，增强资源冗余，提高出图成功率。
- 智能补充头像参考以及跳过部分@头像。
- 支持群聊和用户白名单配置，以及提示词前缀配置和混合模式，多场景下适用。无操作权限将静默返回，避免频繁打扰。
- 支持为每个提示词指定不同的提供商。

## 常用命令

- `<触发词>` 使用预设提示词生成图片
- `/lm添加 <触发词> <提示词内容>` 快捷添加预设提示词
- `/lm删除 <触发词>` 快捷删除预设提示词
- `/lm列表` 查看所有预设提示词名称列表
- `/lm提示词 <触发词>` 查看预设的完整提示词
- `/lm白名单添加 <用户/群组> <ID/SID>` 可通过命令增加用户和群组白名单
- `/lm白名单删除 <用户/群组> <ID/SID>` 可通过命令删除用户和群组白名单
- `/lm白名单列表` 查看白名单启用状态和名单列表

## 提示词参数列表

提示词参数可以置于触发词之后的任意地方，需要以空格进行分割。

格式: <触发词> <提示词内容> --参数 1 参数值 1 --参数 2 参数值 2

示例：bnn 提示词 --image_size 4K --google_search

| 参数名 | 参数值 | 描述 |
| :--- | :--- | :--- |
| `--min_images` | INT | 最小输入图片数量。*[1] |
| `--max_images` | INT | 最大输入图片数量 |
| `--aspect_ratio` | 1:1 ... 16:9, 21:9 *[2] | 图片生成比例 *[3]|
| `--image_size` | 1K, 2K, 4K | 生成图片的分辨率，图片越大耗时越长 *[3] |
| `--google_search` | true, false | 启用谷歌搜索获取实时信息 *[3] |
| `--refer_images` | 文件名 | 为提示词注入固定的图片参考 *[4] |
| `--gather_mode` | true, false | 启用消息收集模式 *[5] |
| `--providers` | 提供商名称 | 使用此参数可以指定提供商 *[6] |

\*[1] 仅 aiocqhttp：如果消息携带的图片数量小于 --min_images 参数值，将自动添加 At 对象头像（At 多个用户则可能添加多张，直到达到上限），如果仍然不够，继续添加发送者头像，数量仍然不够将返回错误信息。

\*[2] 比例可以任意填写，插件会原样传递给请求上下文，但是需模型支持。常用值有 1:1, 2:3, 3:2, 3:4, 4:3 4:5, 5:4, 9:16, 16:9, 21:9。模型并不总是会遵循这个比例参数。

\*[3] 部分参数只在特定场景下被传递：

- `--aspect_ratio` 仅 Gemini 规范生效
- `--image_size`、`--google_search` 仅 Gemini 规范，gemini-3 前缀的模型生效

\*[4] 支持添加预设图片参考，使用英文 `,` 分割多张图片。需要将文件放在插件数据目录 `plugin_data/astrbot_plugin_big_banana/refer_images/` 文件夹，使用示例 `--refer_images 文件名1,文件名2`。参数值中不能有空格。

\*[5] 消息收集模式旨在于通过发送多条消息，实现多提示词拼接和图片收集，解决单条消息的局限性问题（例如图片和文本不能同时发送，或者消息平台只接受第一张图片等）。发送「开始」将使用收集到的提示词和图片进行图片生成；发送「取消」可以取消操作。对于 aiocqhttp，收集模式结束后，如果图片数量未满足最低要求，插件仍然会自动添加QQ头像作为图片参考。

\*[6] 使用此参数指定提供商以后，无论该提供商是否启用，这个提供商都会被用于图片生成。多个提供商可以使用英文 `,` 分割。使用示例：`--providers 主提供商,备用提供商1,备用提供商2`

## 默认预设提示词

- `bnn` 图生图，至少 1 张图片，若消息中不包含图片且消息平台是 QQ 个人号，将自动取用户头像作为参考图。
- `bnt` 文生图，无最少图片需求。
- `bna` 收集模式，触发后会进入文本和图片收集模式，不会立即生成图片。用户可以发送多条消息，补充提示词和图片参考。发送「开始」将使用收集的提示词和图片进行图片生成；发送「取消」可以取消操作。
- `手办化` 手办化预设提示词。

\* 部分预设为新版本添加，可能不存在旧版本配置中，请手动添加（参考 `_conf_schema.json` 文件）。

## 故障排查

- Telegram 不支持 10MB 以上的图片发送（报错会导致控制台打印完整的图片 Base64 数据，可能会造成较大的日志缓存占用）。V0.1.0 强制对超过 10MB 的 Telegram 图片消息改用文件发送。

- 工具循环调用问题本插件没有特别好的处理方法，这是模型理解能力造成的。（其实把工具响应直接加入聊天历史就挺好的）

## 致谢

感谢 Copilot 和 Google One PRO 提供代码补全与参考支持！
