<div align="center">

# 🍌 大香蕉 图片/视频生成插件 🍌

![:访问量](https://count.getloli.com/@astrbot_plugin_big_banana?name=astrbot_plugin_big_banana&theme=rule34&padding=5&offset=0&scale=1&pixelated=1&darkmode=auto)

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.26.0%2B-75B9D8.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Nano Banana Pro](https://img.shields.io/badge/Nano%20Banana-2-FFD700.svg)](https://gemini.google/tw/overview/image-generation)

</div>

## 兼容性变更：

V2（`v0.2.x`）以全新的配置结构和生成管线为基线，不保证兼容 V1 配置或运行数据。

版本更新请查看 [changelog.md](./changelog.md)。

## 主要特性

- 支持 Gemini、OpenAI Chat、OpenAI Images、OpenAI Responses、MiniMax、SiliconFlow、Agnes 等图片生成接口，并兼容流式响应。
- 集成 Vertex AI Anonymous 逆向提供商，免费无限*[1]的 4K 18MB PNG（无损压缩） 图片生成，开箱即用（需能访问 Google）。
- 支持智谱异步视频接口，可使用 `CogVideoX-Flash` 进行文生视频和单图生视频。
- 支持预设查询、图片生成和视频生成 LLM 函数调用工具，并可使用副脑模型优化提示词。
- 支持预设提示词、用户文本占位符、参数别名及预设级参数配置。
- 支持多个 API Key、默认提供商优先级和失败自动降级，也可通过预设或命令临时指定提供商。
- 支持消息图片、引用图片、固定参考图和 QQ 头像，并可自动补充或按需跳过头像。
- 支持多消息收集、后台生成、群组冷却、用户/群组白名单、命令前缀和混合触发模式。
- 支持仅返回图片 URL、本地保存及 R2 图床保存；参考图上传前可自动清理隐私元数据。

\*[1] 免费无限指生成次数不限，服务可用性视服务器实时资源占用情况而定。已知的 Vertex AI Anonymous 支持的图片生成模型有 `gemini-3.1-flash-lite-image`、 `gemini-3.1-flash-image-preview`、 `gemini-3.1-flash-image`、`gemini-3-pro-image-preview`、`gemini-3-pro-image`、`gemini-2.5-flash-image`。

## 常用命令

- `<触发词>` 使用预设提示词生成图片
- `bnv <提示词>` 使用 CogVideoX-Flash 生成视频；消息带图时自动作为首帧
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
| `--min_images` | INT | 最小输入图片数量，必须为非负整数。*[1] |
| `--max_images` | INT | 最大输入图片数量，必须为非负整数；具体上限由提供商决定。 |
| `--aspect_ratio` | 1:1 ... 16:9, 21:9 *[2] | 图片生成比例 *[3]|
| `--image_size` | 1K, 2K, 4K | 生成图片的分辨率，图片越大耗时越长 *[3] |
| `--google_search` | true, false | 启用谷歌搜索获取实时信息 *[3] |
| `--refer_images` | 文件名 | 为提示词注入固定的图片参考 *[4] |
| `--gather_mode` | true, false | 启用消息收集模式 *[5] |
| `--providers` | 提供商名称 | 使用此参数可以指定提供商 *[6] |
| `--n` | INT | 生成图片的数量，仅OpenAI Image API 支持 |
| `--size` | 1536x1024, 1024x1536, auto, ... | 生成图片的分辨率，仅OpenAI Image API 支持 |
| `--url` | true, false | 仅返回图片URL，不直接发送图片 *[7] |
| `--capability` | image_generation, video_generation | 选择预设使用的生成能力 |
| `--quality` | speed, quality | CogVideoX 输出模式 |
| `--fps` | 30, 60 | CogVideoX 视频帧率 |
| `--with_audio` | true, false | 是否生成 AI 音效 |
| `--watermark_enabled` | true, false | 是否添加 AI 水印 |

\*[1] 在支持头像获取的平台（目前包括 aiocqhttp、Telegram 和 Discord）上，如果消息携带的图片数量小于 --min_images 参数值，将自动添加 At 对象头像（At 多个用户则可能添加多张，直到达到上限）；如果仍然不够，再依次添加发送者头像和机器人头像。配置了头像替换时，其他平台也可以使用映射头像。最终数量仍然不够将返回错误信息。

\*[2] 比例可以任意填写，插件会原样传递给请求上下文，但是需模型支持。常用值有 1:1, 2:3, 3:2, 3:4, 4:3 4:5, 5:4, 9:16, 16:9, 21:9。模型并不总是会遵循这个比例参数。

\*[3] 部分参数只在特定场景下被传递：

- `--aspect_ratio` 仅 Gemini 规范生效
- `--image_size`、`--google_search` 仅 Gemini 规范，gemini-3 前缀的模型生效

\*[4] 支持添加预设图片参考，使用英文 `,` 分割多张图片。需要将文件放在插件数据目录 `plugin_data/astrbot_plugin_big_banana/refer_images/` 文件夹，使用示例 `--refer_images 文件名1,文件名2`。参数值中不能有空格。插件配置中的 `refer_images` 默认值对命令调用和 LLM 图片/视频工具调用均生效；预设或本次调用显式指定时会覆盖默认值。

\*[5] 消息收集模式旨在于通过发送多条消息，实现多提示词拼接和图片收集，解决单条消息的局限性问题（例如图片和文本不能同时发送，或者消息平台只接受第一张图片等）。发送「开始」将使用收集到的提示词和图片进行图片生成；发送「取消」可以取消操作。在支持头像获取的平台上，收集模式结束后如果图片数量未满足最低要求，插件仍会自动添加可获取的用户或机器人头像作为图片参考。

\*[6] 使用此参数可以覆盖默认提供商顺序；已停用或能力不匹配的提供商仍会被跳过。多个提供商可以使用英文 `,` 分割。使用示例：`--providers 主提供商,备用提供商1,备用提供商2,备用提供商3,备用提供商4`

\*[7] 仅在提供商实际返回图片 URL 时生效，例如部分 OpenAI_Chat 兼容接口或 Agnes_Images。若提供商只返回图片数据而不返回 URL，则会提示当前提供商不支持仅返回 URL。

## 默认预设提示词

- `bnn` 图生图，至少 1 张图片，若消息中不包含图片且消息平台是 QQ 个人号，将自动取用户头像作为参考图。
- `bnt` 文生图，无最少图片需求。
- `bna` 收集模式，触发后会进入文本和图片收集模式，不会立即生成图片。用户可以发送多条消息，补充提示词和图片参考。发送「开始」将使用收集的提示词和图片进行图片生成；发送「取消」可以取消操作。
- `bnv` 视频生成预设。默认使用 `video_generation` 能力，最多读取 1 张参考图；不带图时文生视频，带图时图生视频。
- `llm_default` LLM 图片工具默认参数预设，允许 0～6 张参考图。
- `llm_video_default` LLM 视频工具默认参数预设，使用视频生成能力并允许最多 1 张参考图。
- `手办化` 手办化预设提示词。

\* `llm_default` 和 `llm_video_default` 同时存在于默认配置和插件内部预设中，两处参数保持一致；配置中的同名预设存在时会覆盖内部值，旧配置缺少条目时使用内部参数。LLM 工具预设名称留空时不会自动启用这两个预设。

## LLM 工具后台回调

后台回调用于把 LLM 图片或视频工具的异步生成结果交还给发起工具调用的上游插件。BigBanana 负责执行生成任务；任务结束后，它根据“后台回调插件”查找已注册的 AstrBot 插件实例，再按“后台回调方法”查找并调用对应方法。该功能仅在“工具调用使用后台任务”开启且插件名、方法名均已填写时生效。

回调始终使用以下五个关键字参数：

| 参数 | 类型 | 说明 |
| :--- | :--- | :--- |
| `event` | `AstrMessageEvent` | 发起本次 LLM 工具调用的原始事件。 |
| `result` | `MessageChain` | 供上游插件处理的统一结果消息链，可能包含文本、图片或视频组件。 |
| `params` | `dict` | 合并默认值、预设和本次工具参数后的最终生成参数。 |
| `unified_msg_origin` | `str` | 任务提交时快照的原会话统一来源标识，可用于恢复会话并把结果交还给对应 AI。 |
| `is_success` | `bool` | 生成成功时为 `True`；生成结果带有错误信息时为 `False`。应以此字段判断成功状态，不要解析提示文本。 |

第三方插件可以实现如下方法：

```python
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.message_event_result import MessageChain


async def on_media_generation_complete(
    self,
    *,
    event: AstrMessageEvent,
    result: MessageChain,
    params: dict,
    unified_msg_origin: str,
    is_success: bool,
) -> bool:
    # 在这里按 unified_msg_origin 找到原会话，并将 result 交给 AI 或自行发送。
    # is_success=False 时，result 中包含可供 AI 判断是否重试的失败原因。
    await self.handle_background_result(
        event=event,
        result=result,
        params=params,
        unified_msg_origin=unified_msg_origin,
        is_success=is_success,
    )
    return True
```

方法可以是同步函数、协程或异步生成器。建议显式返回布尔值：

- 返回 `True` 表示结果已由上游插件接管。
- 返回 `False` 表示未接管，BigBanana 将执行回退处理。
- 返回非布尔值（包括 `None`）会被视为已接管，以兼容没有返回值的旧回调。
- 方法不存在或执行时抛出异常，也会被视为调用失败并进入回退处理。
- 异步生成器以最后一次 `yield` 的值作为返回结果；未产生值时按非布尔值处理。

“LLM 工具直接发送图片结果”决定了回调拿到的内容和处理职责：

| 直接发送 | BigBanana 行为 | 回调中的 `result` |
| :--- | :--- | :--- |
| 关闭 | 生成后先调用回调，不预先发送媒体。回调返回 `False` 或调用失败时，BigBanana 回退发送结果。 | 成功时包含尚未发送的图片或视频；失败时包含失败原因。 |
| 开启 | BigBanana 先向聊天窗口发送生成结果，再调用回调。 | 仅包含文字完成状态，适合上游插件恢复 AI 工具调用流程。 |

第三方插件适配时还应注意：

- 回调参数采用关键字传递，方法应声明上述参数，或使用 `**kwargs` 保持前向兼容。
- 回调在后台任务中执行，不应依赖原消息处理协程仍处于运行状态。
- `params` 应按只读数据使用；其中图片与视频任务的字段会有所不同。
- 回调只有一次处理机会。若需要重试或再次调用模型，应由上游插件自行管理会话状态和幂等性。

### Giftia 配置

使用 [`astrbot_plugin_giftia`](https://github.com/sukafon/astrbot_plugin_giftia) 接收结果时，请在插件配置的“函数调用工具”中填写：

- 后台回调插件：`astrbot_plugin_giftia`
- 后台回调方法：`on_drawing_complete`

## 故障排查

- Telegram 不支持 10MB 以上的图片发送（报错会导致控制台打印完整的图片 Base64 数据，可能会造成较大的Docker日志缓存占用）。V0.1.0 强制对超过 10MB 的 Telegram 图片消息改用文件发送。

- 工具循环调用问题已在 AstrBot 核心中修复（v4.25+），如仍遇到该问题请更新 AstrBot 至最新版本。

## 致谢

感谢所有贡献者与测试用户，以及 Codex 和 Antigravity 的编程辅助！
