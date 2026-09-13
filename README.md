<div align="center">
  <img src="web/assets/icons/1_.png" width="112" alt="Panel Chat" />
  <h1>Panel Chat</h1>
  <p>系统托盘常驻 · 全局热键显示/隐藏 · 接口 / 工具 / 技能插件化</p>
</div>

<p align="center">
  <img alt="version" src="https://img.shields.io/badge/version-v2.1.2-7c4dff?style=flat-square">
  <img alt="python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="pywebview" src="https://img.shields.io/badge/pywebview-6.x%20%2B%20WebView2-2E7D5B?style=flat-square">
  <img alt="platform" src="https://img.shields.io/badge/Windows-10%20%2F%2011-0078D6?style=flat-square&logo=windows&logoColor=white">
  <img alt="no electron" src="https://img.shields.io/badge/runtime-%E6%97%A0%20Electron-9c6ade?style=flat-square">
</p>

---

**Panel Chat** 是一个 Windows 桌面 AI 对话工具，通过系统托盘和全局热键使用。

## 目录

- [特性](#特性)
- [界面与交互](#界面与交互)
- [快速开始](#快速开始)
- [目录结构](#目录结构)
- [自定义 AI 接口](#自定义-ai-接口)
- [内置接口 DeepSeek 与 ModelScope](#内置接口-deepseek-与-modelscope)
- [给接口添加工具](#给接口添加工具)
- [给接口添加技能](#给接口添加技能)
- [配置项](#配置项)
- [主题与外观](#主题与外观)
- [开发与测试](#开发与测试)
- [已知限制与 FAQ](#已知限制与-faq)

---

## 特性

| | |
|---|---|
| **托盘常驻** | 左键单击托盘图标切换窗口显示；右键菜单可切换主题、切换 AI 接口、退出 |
| **静默启动** | `start_hidden` 默认为 `true`：启动时不显示窗口，只留托盘图标；首次运行时无条件显示一次 |
| **全局热键** | 默认 `Ctrl+Alt+W`，用于切换窗口显示（在 `settings.json` 的 `hotkeys` 中修改，keyboard 库语法） |
| **不进任务栏** | 窗口使用 `WS_EX_TOOLWINDOW`，不出现在任务栏与 Alt+Tab。仅在托盘与全局热键都注册成功时隐藏任务栏按钮，否则保留按钮作为备用入口 |
| **始终置顶** | 窗口始终置顶，没有「取消置顶」开关 |
| **Agent 工具调用** | 内置接口运行 `chat ↔ tools` 的 LangGraph 循环：模型决定是否调用工具，拿到工具返回值后生成回答 |
| **技能（Skills）** | 技能是 Markdown 指令集，模型通过 `list_skills` / `load_skill` 两个工具发现并加载；加载后只绑定该技能声明的工具 |
| **接口插件化** | 在 `plugins/<名称>/__init__.py` 中用 `@register_ai_provider` 装饰一个 `chat()` 函数即为一个接口，重启后生效 |
| **会话管理** | 每个会话一个 JSON 文件，自动或手动命名、可删除；修改最新一条提问会重新生成回答；删除某条消息会截断其后的所有消息 |
| **Markdown** | 渲染 Markdown 与代码块，代码块做关键字高亮，不引入高亮库 |
| **图片输入** | 接口声明 `supports_images` 后可在输入框粘贴、拖入或上传图片（最多 6 张、单张 ≤8MB），点击可放大；发送前只保留最新一条用户消息的图片，历史图片降级为文本 |
| **系统提示词** | 按分组管理，可启用一个或多个分组，按顺序拼接为 system prompt |
| **窗口记忆** | 窗口尺寸与位置写入 `settings.json`；Windows 11 下使用 DWM 系统圆角，Win10 忽略此项 |
| **主题** | 所有样式通过 CSS 变量取值，复制一个主题目录即可新增主题（见 [主题与外观](#主题与外观)） |

## 界面与交互

```
├─ 侧边栏：品牌 · ＋ 新对话 · 历史会话列表 ·（底部）设置 / 隐藏
│     展开时 216px；收起后为 52px 窄边栏，展开按钮、新对话、设置、隐藏仍在栏内
│
├─ 头部：拖动此区域移动窗口，右侧为设置 / 隐藏按钮
│
├─ 消息区：渲染 Markdown；用户与 AI 消息分别配色
│
└─ 输入卡片：图片托盘（有图片时出现，最多 6 张）
      ├ 输入框：随内容自动增高，最高 160px
      └ 工具条：＋ 加图 ······ [接口 ▾] · ( ↑ ) 发送
```

窗口右边缘、下边缘、右下角各有自绘把手，可拖拽调整尺寸。

| 操作 | 方式 |
|---|---|
| 显示 / 隐藏窗口 | 全局热键（默认 `Ctrl+Alt+W`），或左键单击托盘图标 |
| 移动窗口 | 拖动顶部标题栏 |
| 调整尺寸 | 拖右边缘 / 下边缘 / 右下角把手 |
| 发送消息 | `Enter` 发送，`Shift+Enter` 换行 |
| 发送图片 | 粘贴、拖入，或点击输入卡片左下角的 `＋` |
| 重新生成 | 修改**最新一条**提问并发送 |
| 截断后续消息 | 删除某条消息，其后所有消息一并删除 |
| 打开设置 | 侧边栏底部「设置」，或头部右侧按钮 |

窗口的拖动、缩放、圆角、置顶由 `core/window_controller.py` 通过 Win32 / DWM API 完成；
前后端通过 `core/api.py` 上的一个 `js_api` 实例通信。

## 快速开始

**环境要求**

- Windows 10 / 11（依赖 Win32 / DWM API）
- Python 3.10 及以上（开发与打包使用 3.13）
- Microsoft Edge WebView2 Runtime —— Windows 11 自带；Win10 若缺失需安装
  [Evergreen Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)

```powershell
git clone https://github.com/Asuior/panel-chat.git
cd panel-chat

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
python main.py
```

**首次运行**

1. 程序在托盘出现（图标由代码绘制，不依赖外部资源）；
2. `data/settings.json` 不存在时显示一次窗口，之后启动不再显示；
3. 打开 **设置 → AI 接口**，选择接口并填写 `API Key` 后即可对话。

将 `settings.json` 中的 `start_hidden` 改为 `false`，或在 **设置 → 通用** 中关闭「静默启动」，
可让后续启动直接显示窗口（下次启动生效）。

调试模式（打开 WebView2 开发者工具）：

```powershell
$env:ALICE_DEBUG = "1"; python main.py
```

**API Key 的存放位置**：按优先级从高到低依次为 —— ① 设置界面填写的自定义参数、
② 环境变量、③ 插件目录下的 `config.json`。密钥保存在本机 `data/settings.json` 中。

## 目录结构

```
panel-chat/
├── main.py                     # 入口：DPI 感知、依赖检查、建窗、托盘、热键、事件循环
├── core/
│   ├── api.py                  # pywebview js_api：暴露给前端的全部函数
│   ├── window_controller.py    # 窗口控制 + 位置记忆 + 任务栏策略 + 事件推送
│   ├── plugin_manager.py       # 插件扫描 / 注册 / 路由（@register_ai_provider）
│   ├── conversation.py         # 会话 CRUD + 索引 + 分支截断
│   ├── image_store.py          # 对话图片：data URL ↔ 本地文件（内容哈希命名）
│   ├── chat_payload.py         # 发送前的消息过滤（历史图片丢弃、字段净化）
│   ├── prompt_manager.py       # 系统提示词分组与按序拼接
│   ├── settings_manager.py     # settings.json 读写（深合并默认值）
│   └── paths.py / logger.py    # 路径常量 / 日志
├── plugins/                    # AI 接口插件，一个子目录一个接口
│   ├── deepseek/               # LangGraph Agent：DeepSeek 官方 API
│   │   ├── __init__.py         #   声明参数、图片能力、欢迎页
│   │   ├── main.py             #   LangGraph 图：chat ↔ tools
│   │   ├── skill_loader.py     #   技能解析器
│   │   ├── skills/*.md         #   技能文件
│   │   └── tools/
│   │       ├── loader.py       #   工具注册器 @tool_resigter
│   │       └── tools_xxx/*.py  #   工具本体（自动扫描）
│   └── ModelScope/             # 同一套 Agent，使用 ModelScope 推理服务
├── web/                        # 前端（原生 JS，无构建）
│   ├── index.html
│   ├── css/  js/
│   └── assets/                 # 背景图 / 图标 / 对话图片
├── themes/
│   └── glassmorphism/          # 默认主题（CSS 变量声明）
├── data/                       # 运行时生成：会话、索引、设置、提示词
└── logs/                       # app.log（2MB 轮转）
```

### 数据文件

```
data/
├── conversations/{uuid}.json    每个对话一个文件（含全部消息）
├── history_index.json           索引 {id: {title, preview, updated_at}}
├── settings.json                全局配置
└── prompts.json                 提示词分组 {groups: [...]}

web/assets/
├── backgrounds/                 上传的背景图
├── icons/                       图标
└── conversations/<对话id>/<内容哈希>.<ext>
                                 多模态消息的图片本体。对话 JSON 只记录相对路径，
                                 图片与页面同源，前端直接以 <img src> 渲染
```

发送请求时由 `core/chat_payload.py` 过滤消息，只有「最后一条用户消息」的图片会随本次请求发出。
消息被删除或分支截断后，不再被引用的图片会被回收。

## 自定义 AI 接口

一个接口对应 `plugins/` 下的一个目录。程序启动时扫描 `plugins/`，目录中存在 `__init__.py` 即加载。

```
plugins/my_provider/
├── __init__.py     # 必须：用 @register_ai_provider 注册 chat()
└── config.json     # 可选：插件自己的配置（API Key 等，不要提交到 git）
```

```python
from core.plugin_manager import register_ai_provider, load_plugin_config

CFG = load_plugin_config(__file__)      # 读取同目录 config.json，不存在时返回 {}

PARAMS_SCHEMA = {
    "api_key": {
        "type": "string",
        "name": "API Key",
        "description": "仅保存在本机",
        "sensitive": True,              # 前端用密码框渲染
        "default": "",
    },
    "base_url": {"type": "string", "name": "Base URL", "default": "https://api.openai.com/v1"},
    "model":    {"type": "string", "name": "模型名称", "default": "gpt-4o-mini"},
}

@register_ai_provider(
    provider_id="my_provider",
    name="我的接口",
    description="显示在设置页与托盘菜单中的说明",
    params=PARAMS_SCHEMA,        # 可选：前端据此渲染参数表单
    supports_images=False,       # 可选：声明支持图片输入
    welcome_html="<h2>你好</h2>", # 可选：空会话时的专属欢迎页
)
def chat(messages: list, temperature: float = 0.7, extra_body: dict | None = None) -> str:
    """messages: [{"role": "system"|"user"|"assistant", "content": "..."}]

    多模态时 content 是 parts 列表：
        [{"type": "text", "text": "..."},
         {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]
    """
    extra = extra_body or {}
    api_key = extra.get("api_key") or CFG.get("api_key") or ""
    # ... 调用服务 ...
    return "AI 回复文本"
```

重启程序后，该接口会出现在 **设置 → AI 接口** 与托盘的「切换 AI 接口」菜单中。

**参数说明**

| 参数 | 说明 |
|---|---|
| `params` | 前端据此渲染参数表单；用户填写的值以 `extra_body` 作为第三个参数传入 `chat()`。字段类型支持 `string`（默认）、`number`（数字输入框）、`boolean`（开关）；设置 `"sensitive": true` 或键名包含 key / secret / token / password 的字段渲染为密码框。用户未修改的字段会补上 `default`，用户显式清空的字段保持空串，使插件内部的「环境变量 / config.json」回退链继续生效 |
| `supports_images` | 声明该接口是否支持图片输入。用户在设置中可单独关闭，实际是否携带图片由 `settings.multimodal` 的覆盖值决定 |
| `welcome_html` | 仅在**当前会话没有消息**时替换内置欢迎页的内容区。外层 `.welcome` 容器保留，因此沿用其居中布局与主题变量，同时额外带 `.custom` 类；顶部 `.big` 头像由程序渲染、跟随「设置 → 外观」中的 AI 头像，不要自行编写。片段中带 `data-tab="ai"` 的元素会自动绑定为「跳转对应设置页签」的按钮。该 HTML 以 `innerHTML` 原样注入，其中的 `<script>` 不执行，但内联事件属性会生效 |
| 旧式签名 | 只写 `chat(messages, temperature)` 也可以，主程序会检测参数个数并回退调用 |
| 返回值 | 返回 `str`；非字符串会被 `str()` 转换 |

## 内置接口 DeepSeek 与 ModelScope

仓库自带两个接口：`plugins/deepseek/` 与 `plugins/ModelScope/`。
**两者的代码结构相同**，都是同一个 LangGraph Agent，区别只在服务商与默认值：

| | `deepseek` | `ModelScope` |
|---|---|---|
| 默认 Base URL | `https://api.deepseek.com/v1` | `https://api-inference.modelscope.cn/v1` |
| 默认模型 | `deepseek-v4-flash` | `Qwen/Qwen3.8-27B` |
| 图片输入 | 未声明 | 声明支持（`supports_images=True`） |
| 专属欢迎页 | 有 | 无（使用内置默认欢迎页） |

两者都使用 `langchain_deepseek.ChatDeepSeek` 作为客户端，通过 `DEEPSEEK_API_BASE`
环境变量指定各自的 `base_url`，因此任何 OpenAI 兼容的服务都可以用同样方式接入。

### Agent 的执行流程

`main.py` 中是一张 LangGraph 状态图，`chat` 与 `tools` 两个节点循环执行：

```mermaid
graph LR
    START([用户消息]) --> chat["chat 节点<br/>绑定工具 + 注入技能提示词"]
    chat -->|"模型返回 tool_calls"| tools["ToolNode<br/>执行 Python 工具"]
    tools --> chat
    chat -->|"模型返回最终回答"| FIN([返回文本])
```

- **`chat` 节点**：组装要绑定给模型的工具列表，调用一次 LLM。
- **`tools_condition`**：模型返回 tool_calls 时转到 `tools` 节点，否则结束。
- **`tools` 节点**：`ToolNode` 依次执行工具，把返回值作为 `ToolMessage` 追加到消息列表，再回到 `chat`。
- 图状态中额外带 `active_skill` 字段，记录当前激活的技能（见「给接口添加技能」）。

新增服务商的做法：复制 `plugins/ModelScope/` 并改名，修改 `main.py` 中的默认 `base_url` / `model`
以及 `__init__.py` 中的 `provider_id` / `params` 默认值，`tools/` 与 `skills/` 目录会一并复制过去。

## 给接口添加工具

工具是普通 Python 函数，放在 `tools/` 目录下，由扫描器自动导入，不需要额外的清单文件。

```
plugins/deepseek/tools/
├── loader.py               # 注册器与扫描器
└── tools_98es7d5/          # 一个工具分组；目录名以 tools 开头即可被扫描
    ├── main.py             # 工具本体
    └── skill_tools.py      # 技能管理工具（见下一节）
```

### 编写工具

```python
# plugins/deepseek/tools/tools_98es7d5/main.py
import requests
from plugins.deepseek.tools.loader import tool_resigter


@tool_resigter("get_weather", "demand")
def get_weather() -> str:
    """自动根据用户当前位置获取天气信息。

    :return: 天气信息（JSON 格式）；失败时返回错误说明
    """
    ...
    return response.text
```

| 要点 | 说明 |
|---|---|
| 装饰器 | `@tool_resigter(name, load_type)`。`name` 是注册表中的键，技能在 `tools:` 中引用的是它；模型看到的工具名来自**函数名**，两者保持一致可避免混淆 |
| `load_type="permanent"` | 每次对话都绑定给模型 |
| `load_type="demand"` | 按需绑定：只有当某个技能在 `tools:` 中列出该工具、且该技能被激活时才会绑定。默认值为 `"permanent"` |
| 文档字符串 | 会被 LangChain `bind_tools` 转换为工具的 JSON Schema 交给模型，用于说明工具的用途、参数与返回值 |
| 参数 | 使用普通 Python 类型标注（`str` / `int` / `Literal` 等） |
| 返回值 | 返回字符串即可（JSON 文本也可以）；LangGraph 用返回值构造 `ToolMessage` |
| 依赖 | 新引入的第三方包需要加入 `requirements.txt` |

### 加载机制

```python
from plugins.deepseek.tools.loader import import_tools

import_tools("permanent")   # 只返回常驻工具
import_tools("demand")      # 只返回按需工具
import_tools("all")         # 返回两者合并
```

`import_tools()` 每次都会 glob 整个 `tools*/**/*.py` 并逐个 import，导入时执行装饰器即完成注册。因此：

- 新建 `.py` 文件并放入 `tools*/` 下的任意层级，重启后生效，不需要注册或改动配置；
- 只有以 `tools` 开头的目录会被扫描（`tools_98es7d5` 是原有的随机后缀，可改为任意 `tools_xxx`）；
- `plugins/<接口>/tools/loader.py` 中的注册表是模块级字典，**每个接口各有一份**。
  两个内置接口各自带有一套 `tools/` 与 `skills/`；若两个接口要共用同一个工具，
  可在各自目录下各放一份，或把 `loader.py` 提取为共用模块。

### 技能管理工具

`skill_tools.py` 中的 `list_skills` / `load_skill` 是标准 LangChain `@tool`，
**不通过 `tool_resigter` 注册**（避免与普通工具表重复），而是由 `main.py` 显式 import 并按路径绑定：

```python
from plugins.deepseek.tools.tools_98es7d5.skill_tools import list_skills, load_skill
SKILL_TOOLS = [list_skills, load_skill]
```

修改工具目录名时，需要同步修改 `main.py` 中的这一行 import。

## 给接口添加技能

技能是放在 `skills/` 目录下的 Markdown 文件，用来向模型提供可复用的任务指令：

```
plugins/deepseek/skills/weather.md
```

```markdown
---
name: weather_query
description: 查询某个城市的天气，并按标准格式播报
tools: get_weather
flow:
  - 调用 get_weather 工具获取天气
  - 从 Open-Meteo 返回的数据中提取用户需要的信息
---

# 天气查询技能

## 目标
当用户的请求涉及「查天气 / 天气怎么样 / 今天热不热」等内容时，启用本技能。

## 执行规范
1. 调用 `get_weather` 工具获取天气数据。
2. 从 Open-Meteo 格式的返回数据中提取用户需要的信息进行回答。
3. 如用户需要的信息在返回结果中不存在，则如实回答不知道。

## 注意事项
- 不要编造工具返回值之外的数据。
- 语气友好、简洁。
```

### front-matter 字段

| 字段 | 说明 |
|---|---|
| `name` | 技能唯一标识，模型调用 `load_skill` 时使用；缺省取文件名 |
| `description` | 技能说明，模型根据它判断何时加载该技能，应写明触发场景 |
| `tools` | 本技能允许使用的工具名，逗号分隔的字符串或 YAML 列表均可。列出的工具必须已用 `"demand"` 注册 |
| `flow` | 业务流程步骤（YAML 列表），渲染为 `【业务流程】1. … 2. …` 拼接在正文之前 |
| 正文 | 面向模型的执行规范，作为 system 消息注入 |

### 生效流程

1. 模型通过系统自带的 `list_skills` 工具获得所有技能及其 `description`；
2. 判断需要哪个技能后调用 `load_skill("weather_query")`；
3. 该工具返回技能概要，并把技能名写入图状态的 `active_skill`；
4. **下一轮** `chat` 节点检测到 `active_skill` 非空，于是：
   - 把 `Skill.build_prompt()`（`flow` 与正文）作为 `SystemMessage` 插入消息列表最前面；
   - 工具列表替换为「全部 `permanent` 工具 + 本技能在 `tools:` 中列出的 `demand` 工具 + 技能管理工具」。

因此技能的指令只在被加载后才占用上下文，`demand` 工具也不会在未加载技能时出现在工具表中。

新增技能不需要修改代码：往 `plugins/<接口>/skills/` 放入一个 `.md` 文件并重启即可。
单个技能文件解析失败会被跳过并记录日志，不影响其余技能加载。

## 配置项

全部配置位于 `data/settings.json`（首次运行自动生成，与默认值深合并）。界面中可修改的项目都在 **设置** 中，
以下为完整清单：

| 键 | 默认值 | 说明 |
|---|---|---|
| `hotkeys.toggle_chat` | `"ctrl+alt+w"` | 显示/隐藏窗口的全局热键（keyboard 库语法），修改后需重启 |
| `start_hidden` | `true` | 静默启动；首次运行时会显示一次窗口 |
| `round_corners` | `true` | 使用 Windows 11 DWM 系统圆角（Win10 无效，自动忽略） |
| `chat` | `500×700`（最小 `500×600`） | 窗口尺寸与位置（`x`/`y` 为 `null` 时居中） |
| `provider` | 第一个可用接口 | 当前使用的 AI 接口 id |
| `temperature` | `0.7` | 采样温度 |
| `provider_params` | `{}` | 各接口的自定义参数 `{provider_id: {参数: 值}}`，包括界面中填写的 API Key |
| `multimodal` | `{}` | 图片输入开关的覆盖值 `{provider_id: bool}`，缺省时跟随插件声明 |
| `selected_prompt_groups` | `[]` | 已启用（有序）的提示词分组 id |
| `theme` | `"glassmorphism"` | 当前主题目录名 |
| `background_image` | `null` | 背景图，形如 `assets/backgrounds/xxx.png` |
| `user` / `assistant` | `{name, avatar}` | 用户与 AI 的名称和头像（avatar 为 data URL 或 `default`） |
| `sidebar_visible` | `true` | 侧边栏展开 / 收起状态 |
| `auto_launch` · `use_background_blur` | `false` / `true` | 预留项，当前版本未使用 |

旧版本升级后，`ball` / `ball_light_effect` / `file_processing` / `hotkeys.toggle_ball`
等遗留键可能仍存在于文件中。这些键没有任何代码读取，可以手动删除。

## 主题与外观

外观与程序行为相互独立：所有颜色、圆角、模糊强度都通过 CSS 变量取值，
默认主题只是 `themes/glassmorphism/` 中的一份变量声明。

### 新增主题

复制默认主题目录并修改其中两个文件：

```powershell
Copy-Item -Recurse themes\glassmorphism themes\my-theme
```

```
themes/my-theme/
├── theme.css        # 覆盖 :root 下的 CSS 变量
└── manifest.json    # {"name": "...", "author": "...", "description": "..."}
```

新增主题目录需要重新扫描，重启程序后可在 **设置 → 外观** 中看到并应用；
已在列表中的主题可热切换。

### 可覆盖的变量

主样式表（`web/css/base.css` / `chat.css` / `settings.css`）全部通过变量取值，
因此在 `theme.css` 中重新声明 `:root` 即可：

<details>
<summary>展开变量清单</summary>

| 分组 | 变量 |
|---|---|
| 品牌色 | `--accent-1` `--accent-2` `--accent-gradient` |
| 浮层表面 | `--glass-bg` `--glass-bg-strong` `--glass-border` `--panel-bg` `--surface` |
| 模糊强度 | `--glass-blur`（浮层面板 / 弹层）`--bubble-blur`（AI 气泡）`--bg-blur`（背景层，默认 `0px`） |
| 文本 | `--text-primary` `--text-secondary` `--text-on-accent` |
| 消息气泡 | `--bubble-user` `--bubble-user-text` `--bubble-ai` `--bubble-ai-text` |
| 控件 | `--hover` `--active` `--danger` |
| 代码块 | `--code-bg` `--code-text` |
| 阴影 | `--shadow-window` `--shadow-soft` |
| 字体 / 圆角 | `--font-ui` `--font-mono` `--radius-lg` `--radius-md` `--radius-sm` |
| 背景层 | `--backdrop-image`（内置渐变）`--backdrop-glow` `--window-base` |

</details>

默认主题中另有一组深色变量（`:root[data-contrast="dark"]`）。界面目前没有切换它的入口，
为 `document.documentElement` 添加 `data-contrast="dark"` 后即可生效。

### 背景图 / 头像 / 名称

| 修改项 | 位置 |
|---|---|
| 聊天背景图 | **设置 → 外观 → 上传背景图**（保存到 `web/assets/backgrounds/`，路径写入 `settings.background_image`） |
| 用户头像 / 名称 | **设置 → 外观 → 我的资料**（点击头像上传，名称最长 20 字） |
| AI 头像 / 名称 | **设置 → 外观 → AI 资料** |

设置弹层打开时背景图大部分被遮挡：弹层表面使用 `--surface`（默认 90% 不透明）以保证文字可读，
可调小该变量的 alpha。弹层高度固定为 `min(560px, 78vh)`，切换页签不会改变窗口尺寸。

### 修改样式时的注意事项

- 不要给窗口根容器添加 `backdrop-filter`：它会成为 `position: fixed` 后代的包含块，
  影响尺寸把手与图片灯箱的定位；
- `theme.css` 中的相对 `url()` 相对样式表解析（该文件由 `<link>` 动态加载），不是相对 `index.html`；
- `background_image` 在前端会被转换为绝对 URL 后写入 CSS 变量，因此相对路径与绝对路径均可。

## 开发与测试

纯 Python 与原生前端，修改 `web/` 下的 CSS/JS 后重启程序即可，没有编译步骤。

```powershell
python scripts/smoke_core.py         # 无头冒烟测试：插件/会话/提示词/设置/图片/Api/窗口控制器
node   scripts/js_smoke.cjs          # Markdown 渲染器
node   scripts/js_welcome_smoke.cjs  # 接口自带欢迎页及切换行为
```

`scripts/` 为本地开发脚本，已被 `.gitignore` 忽略，克隆仓库后不存在。

**相关文件**

- `core/plugin_manager.py` —— 插件注册与路由的约定；
- `plugins/deepseek/main.py` —— Agent 的完整实现；
- `core/api.py` —— 前后端接口清单，各方法的 docstring 说明其行为。

## 已知限制与 FAQ

**窗口隐藏后如何找回？**
按全局热键，或左键单击托盘图标。若托盘与热键都不可用，程序会保留任务栏按钮作为入口 ——
窗口不进任务栏也不进 Alt+Tab，隐藏后没有其他找回方式。

**窗口圆角半径可以指定吗？**
不能。使用的是 DWM 系统圆角，只有约 8px 与 4px 两档，带抗锯齿。
需要更大半径只能改用 `SetWindowRgn`，但边缘没有抗锯齿。

**发送给 AI 的请求包含哪些内容？**
发送前会统一过滤：只保留 `role` 与 `content`（`timestamp`、`images` 等本地字段不外传），
历史消息中的图片一律丢弃并降级为文本，只有最新一条用户消息的图片会随本次请求发出。

**代码块是完整的语法高亮吗？**
不是，只做关键字高亮，不引入高亮库。
