# 微信群消息监控总结工具（WxSum）

监控微信 PC 指定群聊消息，按自适应间隔滚动调用 AI（DeepSeek / 智谱 GLM / Kimi / 通义 / 本地 Ollama 等）生成结构化 Word 总结，并自动归档群文件到 `输出\群名\日期\` 下。

面向需要长期跟踪班级/工作群、又不想错过消息和文件的人——挂机即用，关闭窗口仅隐藏到托盘，每天 23:30 自动总结当日全部群。

> 新增：**图片 OCR 识别**——群里发的截图/通知图片会自动提取文字（RapidOCR 离线识别）并入 AI 总结和 Word 原话记录，让 AI 能"看懂"图片内容。
>
> 新增：**自动更新**——启动后自动检查 GitHub Release，发现新版本一键下载、退出替换、自动重启（用户配置不丢失）；托盘菜单可手动检查更新。

## 特性

- **本地数据库解密读取**：直连微信 4.x SQLCipher 加密数据库（通过 wechatauto 库自动提取密钥+解密副本），不依赖 UI 自动化、不需要群窗口保持打开
- **自适应滚动总结**：15–60 分钟状态机（无新消息拉长、刷屏缩短），加每日强制总结点
- **多服务商 AI 摘要**：OpenAI 兼容协议统一接入，内置 7 个预设；智谱 GLM-4.7-Flash 永久免费可用
- **图片 OCR 文字识别**：群图片自动解密并用 RapidOCR 离线提取文字，喂给 AI 参与总结；原图未缓存时自动重试
- **Word 六模块排版**：统计概览表格、话题分节摘要、活跃人物+关键发言引用、重要事项待办、文件清单、群聊原话记录（含图片 OCR 文字）
- **文件智能归档**：解析 `totallen` 字节大小 + 下载时间双重判据，正确区分同名不同内容文件；微信没下完的会自动等下轮重试（默认关闭，可在设置中开启）
- **增量去重游标**：每群独立 sort_seq 游标，崩溃重启幂等续跑；只归档启用日及之后的消息，不回溯历史
- **简洁 PySide6 界面**：群卡片网格、状态开关、设置对话框（API Key 含小眼睛、自动归档开关、图片 OCR 开关、微信目录手动选择）、托盘常驻
- **自动更新**：启动时检查 GitHub Release（可跳过指定版本），一键下载更新包 → 外部脚本退出替换 → 自动重启；纯标准库实现、用户配置不受影响

## 截图

（待补充：主窗口 + 设置页 + 输出 docx）

## 环境要求

- Windows 10/11
- 微信 PC 4.0+ 版本并保持登录（密钥从运行中的 Weixin.exe 进程只读提取）
- Python 3.12+（开发/源码运行）；打包版无需 Python

## 快速开始

### 1. 使用打包版（推荐给普通用户）

从 [Releases](../../releases) 下载后二选一：

- **安装版** `WxSum-v<版本>-Setup.exe`：双击安装（per-user，不弹 UAC），自动创建开始菜单/桌面快捷方式，可从「设置→应用」一键卸载
- **绿色版** `WxSum-v<版本>-windows-x64.zip`：解压后双击 `WxSum.exe`

首次启动：

1. 登录微信 PC 版
2. 双击 `WxSum.exe`
3. 右上角「设置」→ 选择 AI 服务商（推荐智谱 GLM-4.7-Flash，免费）→ 填 API Key → 保存
4. 右上角「＋ 添加群」→ 勾选要监控的群
5. 关闭主窗口仅隐藏到托盘；右键托盘「退出程序」真正退出

### 2. 源码运行（开发者）

```powershell
git clone <repo-url>
cd 微信监控群消息总结项目
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python run.py
```

或直接用 `启动工具.bat`（已配置好 venv 调用）。

## 项目结构

```
微信监控群消息总结项目\
├── run.py                    # 入口
├── pyproject.toml            # 依赖清单
├── WxSum.spec                # PyInstaller 打包配置
├── app\
│   ├── config.py             # 路径、AI 服务商预设、间隔参数
│   ├── state_store.py        # state.db（监控群/游标/总结记录/归档登记）
│   ├── models.py             # Message / Group / FileInfo 数据类
│   ├── core\
│   │   ├── db_factory.py     # 各角色独立解密副本缓存目录
│   │   ├── message_poller.py # 增量拉取+发送者解析+XML清洗
│   │   ├── archiver.py       # totallen+mtime 双判据文件归档
│   │   ├── interval_ctl.py   # 自适应间隔状态机
│   │   ├── image_ocr.py      # 图片解密 + RapidOCR 文字提取 + 缓存
│   │   ├── updater.py         # GitHub Release 检查/下载/退出替换重启
│   │   └── summary_service.py# 端到端总结编排
│   ├── summary\
│   │   ├── deepseek_client.py# OpenAI 兼容客户端+429 限流退避
│   │   ├── prompts.py        # map-reduce 分块提示词
│   │   ├── pipeline.py       # 分块总结→合并→JSON 解析降级
│   │   └── docx_builder.py   # 六模块 Word 排版
│   ├── ui\                   # PySide6 主窗口/卡片/设置/托盘
│   └── workers\              # MonitorWorker / SummaryWorker (QThread)
├── scripts\                  # 阶段验证脚本（可复跑）+ release_build.py 一键发版
├── installer.iss             # Inno Setup per-user 安装包脚本
└── 输出\                     # 生成的 docx + 归档文件
```

## 打包成 exe

需要 venv 中已安装 PyInstaller：

```powershell
.\.venv\Scripts\Activate.ps1
pip install pyinstaller
pyinstaller --noconfirm WxSum.spec
```

产物在 `dist\WxSum\`，把整个文件夹打包成 zip 分发即可（启动文件是 `WxSum.exe`）。

### 发布新版本（自动更新）

推荐用一键发版脚本（升版本号 → PyInstaller 打包 → PYZ 关键模块校验 → 压 zip → 编译 Inno Setup 安装包）：

```powershell
.\.venv\Scripts\python.exe scripts\release_build.py 1.3.0
```

需要 venv 中已安装 PyInstaller（`pip install pyinstaller`）；本机装有 [Inno Setup 6](https://jrsoftware.org/isdl.php) 时会额外产出 `WxSum-v<版本>-Setup.exe`（未装则自动跳过，只发 zip）。

随后：

1. git 提交推送
2. GitHub 仓库 **Releases → Draft a new release**，tag 填 `v<版本号>`（如 `v1.3.0`），正文写更新说明（会原样显示在用户的更新弹窗里）
3. 把 zip（自动更新通道）和 Setup.exe（新用户安装）都拖到附件区 → Publish release

已安装旧版（v1.2.0+）的用户启动程序后会自动收到更新提示，一键即可完成更新；用户的配置和数据不受影响（保存在 `%LOCALAPPDATA%\WxSum\`，不在程序目录）。

## 数据与隐私

- 微信密钥、解密副本、AI Key、输出 Word 全部保存在本地，不上传
- 密钥缓存在 `%LOCALAPPDATA%\WxSum\`（打包后）或项目 `data\` 下（源码运行）；`data\`、`dbcache\`、`logs\`、`输出\` 均已列入 `.gitignore`，不会误提交
- 只有 AI 摘要请求会把当批消息文本（含发送者昵称/wxid）发送到所选 AI 服务商；选本地 Ollama 时可完全断网运行

**本地明文数据须知**（当前版本为明文存储，注意保管好本机账户）：

| 文件 | 内容 | 位置（打包版） |
|---|---|---|
| `data\config.json` | AI API Key（明文） | `%LOCALAPPDATA%\WxSum\data\` |
| `data\wechat_keys.json` | 微信数据库解密密钥（明文） | 同上 |
| `dbcache\<角色>\` | 解密后的聊天数据库副本 | `%LOCALAPPDATA%\WxSum\dbcache\` |
| `data\ocr_cache.json` | 图片 OCR 提取的文字 | `%LOCALAPPDATA%\WxSum\data\` |
| `logs\app.log` | 消息预览/群名等运行日志 | `%LOCALAPPDATA%\WxSum\logs\` |

> 多人共用电脑时请注意：以上文件对本机当前用户（及管理员）可读。后续版本计划改用 Windows DPAPI 加密 API Key 与密钥文件。

## 已知限制

- 微信 4.x 通过 SQLCipher 加密本地数据库，本工具读取密钥**要求微信进程正在运行**；微信退出后密钥失效，需要重新启动微信
- 免费档 AI（智谱 GLM-4.7-Flash）有并发限制，多群同批总结可能触发 429，工具会自动退避重试但会变慢
- 自动更新通过 HTTPS 从 GitHub Release 下载 zip，目前校验包内含 `WxSum.exe` 与 ZIP 完整性，未做哈希/签名校验（路线图：发布 SHA-256 清单并核验）
- Windows 任务栏/标题栏图标由 Qt DWM 渲染，已尽量设置为透明或简洁图标

## 开发路线

- [x] 支持图片消息识别（OCR 入 Word）
- [x] Inno Setup per-user 安装包 + 一键发版脚本
- [ ] 跨日总结（合并多日到一份周报）
- [ ] 总结模板自定义（学校 / 企业 / 项目场景）
- [ ] 敏感配置加密存储（API Key / 微信密钥改用 Windows DPAPI）
- [ ] 更新包 SHA-256 校验（Release 附件附带校验和文件）
- [ ] 总结历史面板（列表查看/打开历史 docx，打通 state.db 的 summary_run 表）
- [ ] 卡片快捷入口：打开输出文件夹 / 打开当日 docx
- [ ] 关键词告警（命中"作业/考试/会议"等词弹系统通知）
- [ ] 发送者匿名化选项（昵称→用户A/B 再喂给 AI，进一步降低隐私外发）

## License

MIT
