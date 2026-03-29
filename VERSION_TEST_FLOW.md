# Claude Code 多平台版本测试流程

## 目标

本文档用于定义一套可重复执行的测试流程，按官方“安装特定版本”说明，
测试 Claude Code `2.1.80` 到 `2.1.85` 在以下平台上的安装与补丁行为：

- Windows PowerShell
- Windows CMD
- macOS
- Linux
- WSL

本文档覆盖：

- 使用官方原生安装器安装指定版本
- 校验当前激活版本
- 立即归档该版本二进制
- 对归档样本执行 `patch.py` 的补丁前检查、补丁应用、补丁后检查
- 记录安装异常、版本异常和补丁结果

本文档不覆盖 Homebrew 和 WinGet。原因是官方“安装特定版本”章节给出的
是原生安装器流程，不是包管理器流程。

## 官方安装命令

将 `<VERSION>` 替换为要测试的版本号，例如 `2.1.80`。

### macOS / Linux / WSL

```bash
curl -fsSL https://claude.ai/install.sh | bash -s <VERSION>
```

### Windows PowerShell

```powershell
& ([scriptblock]::Create((irm https://claude.ai/install.ps1))) <VERSION>
```

### Windows CMD

```cmd
curl -fsSL https://claude.ai/install.cmd -o install.cmd && install.cmd <VERSION> && del install.cmd
```

## 测试前置条件

开始任一版本测试前，先确认：

1. 当前没有正在运行的 `claude` 进程。
2. 当前机器可以访问 `claude.ai`。
3. Windows 平台已安装 Git for Windows。
4. `claude --version` 当前可正常执行。
5. 已准备独立测试目录，用于保存样本、日志和汇总结果。

建议目录结构：

```text
version-tests/
  binaries/
    <platform>/
      claude-<version>[.exe]
      claude-<version>.bak
  logs/
    <platform>/
      <version>-install.txt
      <version>-before-check.txt
      <version>-after-check.txt
  reports/
    summary.md
```

## 版本矩阵

每个平台都按同一批版本执行：

- `2.1.80`
- `2.1.81`
- `2.1.82`
- `2.1.83`
- `2.1.84`
- `2.1.85`

## 标准测试步骤

以下步骤对每个平台、每个版本都执行一次。

### 1. 安装目标版本

使用该平台对应的官方版本安装命令。

示例：

```powershell
& ([scriptblock]::Create((irm https://claude.ai/install.ps1))) 2.1.80
```

### 2. 立即校验当前激活版本

安装完成后马上执行：

```bash
claude --version
```

通过条件：

- 返回的版本号与请求安装的版本完全一致

失败条件：

- 安装器显示成功，但 `claude --version` 不匹配
- 安装器返回拉取错误或 `404`

### 3. 立即归档当前安装出的二进制

不要依赖安装器替你保留所有历史版本。版本校验通过后，立即把当前安装出的
`claude` 可执行文件复制到测试目录。

建议归档路径：

- Windows: `version-tests/binaries/windows/claude-<version>.exe`
- macOS / Linux / WSL: `version-tests/binaries/<platform>/claude-<version>`

这是测试执行建议，不是官方文档原文要求。这个建议来自当前实测行为：
原生安装器可能覆盖当前安装，且不保证为所有历史版本保留独立副本。

### 4. 记录样本身份信息

每个归档样本至少记录：

- 文件大小
- 最后修改时间
- SHA256
- 归档样本自身执行 `--version` 的输出

示例：

```powershell
Get-FileHash .\claude-2.1.80.exe -Algorithm SHA256
```

### 5. 对归档样本执行补丁前检查

不要只测 live install。先测归档样本。

```bash
python3 patch.py --check --binary <ARCHIVED_BINARY>
```

记录内容：

- 检测到的策略
- 补丁前状态
- 关键锚点数量，例如 `bun bytecode fallback`、`permissions flag`
- 可选 UI 锚点是否存在

### 6. 对归档样本执行补丁

```bash
python3 patch.py --binary <ARCHIVED_BINARY>
```

通过条件：

- 补丁执行无致命错误
- 样本旁边生成了对应的 `.bak`

### 7. 对归档样本执行补丁后检查

```bash
python3 patch.py --check --binary <ARCHIVED_BINARY>
```

通过条件：

- 输出中出现 `Status    : patched`
- 输出中检测策略符合预期，目前应为 `decision`

### 8. 可选的交互式运行验证

如果当前环境有真实终端，可以进一步做交互式运行验证：

```bash
claude --channels plugin:telegram@claude-plugins-official
```

记录：

- 是否还出现 `--channels ignored`
- 是否还出现 no-auth 或 policy-blocked 提示
- 是否进入正常交互式会话

这一项必须在真实 TTY 中执行。非交互 shell 不足以代表最终运行结果。

## 结果记录模板

每个平台、每个版本记录一行：

| Platform | Version | Install | `claude --version` | Archive | Pre-check | Patch | Post-check | Runtime TTY | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Windows PowerShell | 2.1.80 | Pass | Match | Pass | Clean | Pass | Patched | Pending |  |

建议状态值：

- `Pass`
- `Fail`
- `Skipped`
- `Unavailable`

## 异常处理要求

出现以下任一情况时，必须显式记录，不能静默跳过：

- 安装器返回 `404`
- 安装器显示成功，但激活版本没有变化
- 归档样本的实际版本与文件名不一致
- 补丁结果显示 `mixed`
- 某些版本缺少可选锚点

## 针对本仓库的执行约束

如果测试目标是本仓库的补丁脚本，请遵守：

1. 每个版本都先归档，再补丁。
2. 先补归档样本，后补 live install。
3. 只有在归档样本通过后，才允许补当前系统中的真实安装。
4. 每个样本单独保留一份备份。

当前 `patch.py` 已处理：

- Bun `@bytecode` fallback 补丁
- 部分补丁状态的 `mixed` 检测
- `tengu_harbor_permissions` 功能开关补丁
- `noAuth` UI 状态改为使用 `+`（`0x2B`）而不是空格占位
- `2.1.80` 这类带点版本文件名的独立备份命名
- macOS 上的 ad-hoc `codesign`（不依赖运行脚本的 Python 进程架构）

当前限制：

- 仓库维护者目前没有 Mac M 系列机器，Apple Silicon 路径尚未完成维护者本地真机回归测试

## 范围说明

本文档只定义测试流程，不包含最终测试结果表。各平台完成整轮测试后，
再单独生成结果汇总文档。

## 参考来源

- Claude Code 官方文档，安装与设置，"安装特定版本"：
  `https://code.claude.com/docs/zh-CN/setup#安装特定版本`
