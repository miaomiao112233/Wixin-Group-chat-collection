# 微信群消息监控总结工具（WxSum）

监控微信 PC 指定群聊消息，按自适应间隔滚动调用 AI（DeepSeek / 智谱 GLM / Kimi / 通义 / 本地 Ollama 等）生成结构化 Word 总结，并自动归档群文件到 `输出\群名\日期\` 下。

面向需要长期跟踪班级/工作群、又不想错过消息和文件的人——挂机即用，关闭窗口仅隐藏到托盘，每天 23:30 自动总结当日全部群。

> 新增：**记录与诊断中心**——一个窗口四个页签：总结历史（可打开文档、按原日期一键重新生成）、归档文件（浏览/打开/导出 CSV）、日志与错误（error_log 表 + app.log 实时尾巴）、自检与维护（微信进程/密钥/目录权限/磁盘/缓存/API 配置体检 + 一键导出诊断包 + 清理解密缓存）。
>
> 新增：**关键词提醒**——群消息命中自定义词表（默认 作业/考试/会议/通知/报名/截止/签到/家长会）时弹托盘通知，点通知直接打开当日 Word；支持免打扰时段（如 22:00-07:00），同群同词 10 分钟内只提醒一次。
>
> 新增：**卡片快捷入口 / 窗口记忆 / 托盘状态**——群卡片上一键打开该群输出文件夹与当日文档；窗口位置大小（含最大化）自动记忆；托盘图标按"监控中/正在总结/未连接/已暂停"变色，悬停显示下次总结时间与监控群数。
>
> 新增：**缓存保留策略 + 发版流水线**——微信库解密副本（dbcache）按"保留天数 + 总量上限"自动清理并可在界面手动清理；GitHub Actions 打 tag 即自动打包发版，更新包带 **SHA-256 校验**（客户端下载后强制核对）。
>
> 新增：**单元测试**——`python -m unittest discover -s tests -t .`（139 个用例，覆盖自适应间隔状态机、分块与 JSON 降级、state.db、safe_name/文件名变体、关键词告警、缓存清理与诊断包）。
>
> 新增：**图片 OCR 识别**——群里发的截图/通知图片会自动提取文字（RapidOCR 离线识别）并入 AI 总结和 Word 原话记录，让 AI 能"看懂"图片内容。
>
> 新增：**QQ / 微信 风格无边框窗口**——整个窗口（含设置、添加群、更新、消息框）改为自绘标题栏，系统标题栏和左上角小图标不再出现；同时把系统边框样式加回来（`WS_THICKFRAME/WS_CAPTION` 不可见 + DWM 圆角投影），**Windows 原生动画（开/关、最小化/还原、最大化缩放）、贴边吸附、四周原生缩放全部保留**。
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
- **QQ / 微信 风格无边框界面**：主窗口与全部对话框自绘标题栏（标题栏右侧直接放「＋ 添加群 / 设置」与窗口按钮），系统标题栏与左上角图标消失；窗口本身仍是"原生窗口"——加回不可见的系统边框 + DWM 圆角/投影，所以 **Windows 原生过渡动画、贴边吸附、Win+方向键、四周原生缩放、任务栏预览全都保留**；群卡片网格列数随窗口宽度自适应
- **设置与提示同样无边框**：设置项收成「AI 服务 / 微信与归档 / 消息提醒 / 缓存与维护」四个**可折叠分组**——标题行右侧直接显示当前值摘要（如 `glm-4.7-flash`、`已开启 · 8 个词`），点开才展开、带 150ms 高度动画，默认只展开尚未配好的 AI 组；确认/警告/信息改用自绘消息框；关窗仅隐藏到托盘
- **自动更新**：启动时检查 GitHub Release（可跳过指定版本），一键下载更新包 → **SHA-256 校验** → 外部脚本退出替换 → 自动重启；纯标准库实现、用户配置不受影响
- **记录与诊断**：总结历史与失败重试、归档文件浏览与 CSV 导出、错误表 + 实时日志、运行自检、一键导出脱敏诊断包（不含微信密钥与明文 API Key）
- **关键词提醒**：自定义词表命中即弹托盘通知，点通知打开当日总结；支持跨午夜免打扰时段与同群同词节流
- **省心细节**：群卡片一键打开输出文件夹/当日文档；窗口位置大小自动记忆；托盘图标按状态变色 + 悬停显示下次总结时间；解密缓存按保留天数/容量上限自动清理

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
│   │   ├── alerts.py         # 关键词提醒纯逻辑（词表/静默时段/节流）
│   │   ├── maintenance.py    # 运行自检 + 解密缓存清理 + 诊断包导出
│   │   ├── image_ocr.py      # 图片解密 + RapidOCR 文字提取 + 缓存
│   │   ├── updater.py         # GitHub Release 检查/下载/SHA-256 校验/替换重启
│   │   └── summary_service.py# 端到端总结编排
│   ├── summary\
│   │   ├── deepseek_client.py# OpenAI 兼容客户端+429 限流退避
│   │   ├── prompts.py        # map-reduce 分块提示词
│   │   ├── pipeline.py       # 分块总结→合并→JSON 解析降级
│   │   └── docx_builder.py   # 六模块 Word 排版
│   ├── ui\                   # PySide6 主窗口/卡片/设置/托盘
│   │   ├── frameless.py      # 无边框窗口框架（自绘标题栏/原生动画/拖动缩放）
│   │   ├── collapse.py       # 可折叠分组（设置页收纳用，带动画与摘要）
│   │   ├── history_dialog.py # 记录与诊断（历史/归档/日志/自检 四页签）
│   │   ├── msgbox.py         # 无边框消息框（替代 QMessageBox）
│   │   ├── shell.py          # 用系统程序打开文件/文件夹
│   │   └── ...
│   └── workers\              # MonitorWorker / SummaryWorker (QThread)
├── tests\                    # 标准库 unittest（139 例，CI 里跑）
├── .github\workflows\        # release.yml：打 tag 自动打包 + SHA-256 + 发 Release
├── scripts\                  # 阶段验证脚本（可复跑）+ ui_preview.py 界面截图 + release_build.py 一键发版
├── installer.iss             # Inno Setup per-user 安装包脚本
└── 输出\                     # 生成的 docx + 归档文件
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

139 个用例、无需联网/微信/真实数据（临时目录 + 打桩），覆盖：自适应间隔状态机、分块与 JSON 降级、state.db 读写与幂等、`safe_name`/文件名变体、关键词与静默时段、缓存清理策略、诊断包内容（含"不含微信密钥"断言）。CI 在发版前会自动跑一遍。

## 打包成 exe

需要 venv 中已安装 PyInstaller：

```powershell
.\.venv\Scripts\Activate.ps1
pip install pyinstaller
pyinstaller --noconfirm WxSum.spec
```

产物在 `dist\WxSum\`，把整个文件夹打包成 zip 分发即可（启动文件是 `WxSum.exe`）。

### 发布新版本（自动更新）

**方式一：GitHub Actions 自动发版（推荐）**——推一个 tag 即可，测试 → 打包 → 校验文件 → 建 Release 全自动：

```powershell
git tag v1.3.0
git push origin v1.3.0
```

工作流（`.github/workflows/release.yml`）会：跑单元测试 → `scripts/release_build.py <tag 版本>` → 生成 zip/安装包各自的 `<资产名>.sha256` → 上传到对应 Release。也可在 Actions 页面手动 `workflow_dispatch` 触发（可填版本号、可标 prerelease）。

- 客户端下载更新包后会**强制核对 SHA-256**；对不上就删包并报错。老 Release 没带校验文件时只提示"已跳过校验"，不影响更新。
- **可选代码签名**：在仓库 Secrets 配置 `CERT_PFX_BASE64`（.pfx 的 base64）与 `CERT_PFX_PASSWORD`，工作流会用 `signtool` 签名并重算校验值；没有这两个 secret 时签名步骤自动跳过（不影响发版，只是 SmartScreen 会提示"未知发布者"）。

**方式二：本机手动打包**（升版本号 → PyInstaller → PYZ 关键模块校验 → 压 zip → 编译 Inno Setup）：

```powershell
.\.venv\Scripts\python.exe scripts\release_build.py 1.3.0
```

需要 venv 中已安装 PyInstaller（`pip install pyinstaller`）；本机装有 [Inno Setup 6](https://jrsoftware.org/isdl.php) 时会额外产出 `WxSum-v<版本>-Setup.exe`（未装则自动跳过，只发 zip）。脚本会在 `dist\` 里同时生成 `.sha256` 校验文件，**记得把它和 zip/Setup 一起上传**，否则客户端会提示"已跳过校验"。

手动方式随后：git 提交推送 → GitHub **Releases → Draft a new release**，tag 填 `v<版本号>`，正文写更新说明（会原样显示在用户的更新弹窗里）→ 把 zip + `.sha256`（+ Setup.exe）拖进附件区 → Publish release。

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
| `dbcache\<角色>\` | 解密后的聊天数据库副本（按策略自动清理） | `%LOCALAPPDATA%\WxSum\dbcache\` |
| `data\ocr_cache.json` | 图片 OCR 提取的文字 | `%LOCALAPPDATA%\WxSum\data\` |
| `data\state.db` | 监控群/游标/总结记录/归档登记/错误记录 | `%LOCALAPPDATA%\WxSum\data\` |
| `logs\app.log` | 消息预览/群名等运行日志 | `%LOCALAPPDATA%\WxSum\logs\` |

> 多人共用电脑时请注意：以上文件对本机当前用户（及管理员）可读。后续版本计划改用 Windows DPAPI 加密 API Key 与密钥文件。
>
> 「记录与诊断 → 导出诊断包」生成的 zip **不含** `wechat_keys.json`，API Key 也会脱敏，可以直接发给作者排查问题。

## 已知限制

- 微信 4.x 通过 SQLCipher 加密本地数据库，本工具读取密钥**要求微信进程正在运行**；微信退出后密钥失效，需要重新启动微信
- 免费档 AI（智谱 GLM-4.7-Flash）有并发限制，多群同批总结可能触发 429，工具会自动退避重试但会变慢
- 自动更新已核验 SHA-256（Release 带 `.sha256` 时强制校验）；**尚未做代码签名**——配置签名证书前 SmartScreen 仍会提示"未知发布者"
- Windows 任务栏/Alt+Tab 图标仍由系统显示（右侧绿色"总"字图标）；无边框窗口只在程序内部生效
- 文件选择框、托盘气泡通知仍是系统原生样式（属于操作系统组件，未自绘）
- 窗口外壳在 Windows 上走"原生边框 + 自绘标题栏"（`app/ui/frameless.py` 的原生模式）：Win11 有系统圆角与投影、Win10 为直角；非 Windows 平台自动退回透明自绘模式（12px 留白 + 自绘投影），该模式下 Windows 的分层窗口会失去 DWM 过渡动画，因此 Windows 上不建议强制关掉原生模式
- 提示框的复选框指示器样式由 Qt 原生样式 + QSS 决定（现有外观为既有效果），如需统一可另行定制 `QCheckBox::indicator`
- 关键词提醒只匹配消息**文本**（含文件名），不匹配图片 OCR 结果——OCR 在总结阶段才做，放进轮询会拖慢采集

## 开发路线

- [x] 支持图片消息识别（OCR 入 Word）
- [x] Inno Setup per-user 安装包 + 一键发版脚本
- [x] 更新包 SHA-256 校验（Release 附件附带校验和文件）
- [x] 总结历史面板（列表查看/打开历史 docx，打通 state.db 的 summary_run 表）
- [x] 卡片快捷入口：打开输出文件夹 / 打开当日 docx
- [x] 关键词告警（命中"作业/考试/会议"等词弹系统通知 + 免打扰时段）
- [x] 运行自检 / 日志查看器 / 导出诊断包
- [x] 解密缓存保留策略（天数 + 容量上限 + 手动清理）
- [x] GitHub Actions 自动打包发版 + 单元测试
- [ ] 每群独立设置（间隔上下限 / 模板 / 归档与 OCR 开关 / 关键词）
- [ ] 跨日总结（合并多日到一份周报）+ 待办跨日追踪
- [ ] 总结模板自定义（学校 / 企业 / 项目场景）
- [ ] 敏感配置加密存储（API Key / 微信密钥改用 Windows DPAPI）
- [ ] 深色模式（跟随系统）
- [ ] 微信消息类型补齐（群接龙/投票名单、语音转写、链接与小程序卡片）
- [ ] 附件正文抽取（群里的 PDF/Word 通知读进总结）
- [ ] 发送者匿名化 + 敏感信息脱敏选项（昵称→用户A/B 再喂给 AI）

## License

MIT
