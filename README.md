# claude-channels-patch

English version: [README.en.md](./README.en.md)

> 免责声明：本项目仅供学习和研究使用。
> 请勿将其用于你不拥有或未获授权分析的软件或服务。

这是一个针对 **Claude Code** 的二进制补丁，可在无需 `claude.ai` OAuth 认证的情况下启用 `--channels` 功能。

## 它做了什么

Claude Code 的 `--channels` 参数允许 MCP 服务器向当前会话推送实时消息，例如 Telegram 机器人插件可以把聊天消息直接转发给 Claude。不过，这个功能被三层检查所限制：

| 限制项 | 检查内容 | 为什么会阻止 |
|------|---------------|---------------|
| **功能开关** | 通过 GrowthBook 检查 `tengu_harbor` | 默认值为 `false`；当 `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` 时无法访问 |
| **OAuth** | `accessToken` 检查 | 需要登录 claude.ai，会阻止 API key 或第三方代理用户 |
| **Allowlist** | Channel allowlist ledger | 没有 GrowthBook 时，服务端批准列表为空 |

默认情况下，脚本会优先尝试整体改写 channel decision function：保留 MCP capability check，并直接让后续流程返回 `register`。如果无法安全定位该函数，再回退到原来的等长字节替换方案。

## 需求

- **Python 3.10+**

## 兼容性

- 该脚本适用于 **Claude Code v2.1.80 及以上版本**；已在 **2.1.81** 和 **2.1.83** 上于 Windows、Linux、macOS 测试
- 使用 **稳定字符串锚点**（属性名、返回值、字符串字面量），不依赖混淆后的变量名
- 自动检测官方安装方式对应的二进制（**Native Install**、**Homebrew**、**WinGet**）并全部打补丁
- 如果你的安装路径是自定义的，使用 `python patch.py --binary /path/to/claude`
- 如果未找到预期代码模式，补丁会拒绝执行

## 用法

```bash
# 应用补丁（自动检测所有已安装的二进制）
python patch.py

# 只检查检测结果和将采用的补丁策略，不修改文件
python patch.py --check

# 对指定二进制路径应用补丁
python patch.py --binary /path/to/claude

# 强制使用整体 decision-function 补丁策略
python patch.py --strategy decision

# 强制使用 legacy 等长字节替换策略
python patch.py --strategy legacy

# 恢复原始状态
python patch.py revert
```

打完补丁后，使用 channels 启动 Claude Code：

```bash
claude --channels plugin:telegram@claude-plugins-official
```

## 工作原理

Claude Code 的二进制是一个 Node.js SEA（Single Executable Application），其中包含两份打包后的 JS 文件。脚本支持两种补丁策略，在 `auto` 模式下会优先选择语义更完整的方案：

### 1. 整体 decision-function 补丁（默认）

脚本会定位 channel decision function，保留最前面的 `claude/channel` capability check，然后把函数体后半段改写成直接返回 `register`。这个策略更接近“保留协议层检查，去掉业务限制”的语义。

如果你只想看脚本会采用哪种策略，而不实际写入文件，可以执行：

```bash
python patch.py --check
```

### 2. Legacy 回退字节补丁

如果无法安全定位 decision function，脚本会回退到原来的 5 处稳定代码模式替换，并在两份 bundle 副本上分别应用（总计 10 处修改）：

### 2.1 功能开关默认值：`!1` -> `!0`

```javascript
// 修改前
function waH() { return l$("tengu_harbor", !1) }  // default = false

// 修改后
function waH() { return l$("tengu_harbor", !0) }  // default = true
```

`l$` 函数会从 GrowthBook（Anthropic 的远程配置）读取功能开关。当设置 `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` 时，GrowthBook 无法访问，`l$` 会返回默认值。把默认值从 `false` 改为 `true` 后，该功能就会被启用。

### 2.2 OAuth 限制：`if(!` -> `if( `

```javascript
// 修改前：位于 B6f() 的 channel 注册逻辑中
if (!SL()?.accessToken)
  return { action: "skip", kind: "auth", ... };

// 修改后
if ( SL()?.accessToken)   // 条件取反，不再进入 skip
  return { action: "skip", kind: "auth", ... };
```

`SL()?.accessToken` 读取 claude.ai 的 OAuth token。API key 用户没有这个 token，因此它会返回 `undefined`。移除 `!` 后，条件变为 `if(undefined)`，结果为假，因此 skip 分支不会执行。

### 2.3 Allowlist 检查（插件）：`&&!` -> `&& `

```javascript
// 修改前
if (!D.dev && !KaH().some((L) => L.plugin === D.name && L.marketplace === D.marketplace))
  return { action: "skip", kind: "allowlist", ... };

// 修改后
if (!D.dev &&  KaH().some(...))  // 取反：只有在 allowlist 上时才会 skip
  return { action: "skip", kind: "allowlist", ... };
```

移除 allowlist 查询前的 `!` 后，判断条件被反转：现在只有插件已经在 allowlist 中时才会跳过，从而允许不在 allowlist 中的插件通过。

### 2.4 Allowlist 检查（服务端）：`if(!` -> `if( `

```javascript
// 修改前
else if (!D.dev) return { action: "skip", kind: "allowlist", ... };

// 修改后
else if ( D.dev) return { action: "skip", kind: "allowlist", ... };
```

对于生产插件，`D.dev` 为 `undefined`，因此 `if( D.dev)` 始终为假，skip 不会执行。

### 2.5 noAuth 状态：`noAuth:!` -> `noAuth: `

```javascript
// 修改前
noAuth: !SL()?.accessToken   // 没有 OAuth token 时为 true

// 修改后
noAuth:  SL()?.accessToken   // undefined（假值），会被视为 not-noAuth
```

### 为什么是两份副本？

二进制内部嵌入了两份 JS bundle（主线程和 worker）。每个补丁都会应用到两个偏移位置，以保持一致性。

## Legacy 补丁摘要

| # | 锚点 | 字节修改 | 目的 |
|---|--------|-------------|---------|
| 1 | `tengu_harbor",!1)}` | `1` -> `0` | 功能开关默认值 |
| 2 | `?.accessToken)return{action:"skip",kind:"auth"` | `!` -> ` ` | 绕过 OAuth |
| 3 | `.marketplace))return{action:"skip",kind:"allowlist"` | `!` -> ` ` | 绕过插件 allowlist |
| 4 | `)return{action:"skip",kind:"allowlist",reason:\`server` | `!` -> ` ` | 绕过服务端 allowlist |
| 5 | `noAuth:!` | `!` -> ` ` | UI 的 noAuth 状态 |

## 安全性

- **备份**：修改前会将原始二进制保存为 `*.bak`
- **模式匹配**：动态定位目标，不依赖硬编码偏移，也不会静默损坏文件
- **原子写入**：使用临时文件和 `os.replace()`，避免破坏正在运行的二进制
- **可恢复**：`python patch.py revert` 可以随时恢复所有二进制

## 友情链接

- [linux.do](https://linux.do/)

## 许可证

MIT
