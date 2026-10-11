# 缠论线段自动划分 · 开发者说明

[用户首页](README.md) · [构建与打包](docs/build.md) · [接口协议](docs/interface.md) · [笔计算迁移](docs/pen-migration.md)

TDXSegment 是通达信使用的 C++17 计算库，提供笔识别、已确认线段事件、Windows DLL、Python 参考计算和回归工具。工程名称、`TDXSegment.dll`、`RegisterTdxFunc` 和原函数 `1/2/99` 保持兼容；`v0.1.0-rc.2` 新增函数 `10/11` 承接原公式中的笔计算。

## 计算流程

`C / DAY → 函数10上下文 → H / L / 上下文 → 函数11笔标记 → 函数1或2 → 线段事件 → 通达信公式绘图`。

三数组 ABI 无法在一次回调中同时接收 H、L、C、DAY。函数10把后续实际使用的均线比较、有效性和跨日判断编码为小整数；函数11完成分型、包含、跳空、距离筛选及笔端点计算。两个入口均只读取当前数组，不保存全局行情或跨调用缓存，因此不同股票、周期和调用顺序不会共享上次输入。

公式保留顶底两个布尔标记用于绘图；生成 `PENEVENT` 时仍采用顶优先。旧公式中 `CLD1` 的最后一个分支仍比较 `REF(H,...)`，本轮不修订该规则。详见[迁移语义与宿主对照](docs/pen-migration.md)。

## 接口

| 函数 | 三输入 | 输出 |
| :---: | :--- | :--- |
| 10 | C、DAY、C | 均线方向、跨日和预热标记 |
| 11 | H、L、函数10结果 | 顶底笔标记，0..3 |
| 1 | 笔端点、H、L | 默认线段策略 43/13/13 |
| 2 | 笔端点、H、L | 实验恢复策略 43/13/9 |
| 99 | 笔端点、H、L | 接线及输入诊断 |

既有 C++ 入口与结构体不变。新增声明在 [pen_formula.hpp](include/tdx_segment/pen_formula.hpp)。所有回调在完整计算后一次写回，异常不得跨越 C ABI；输入错误清空部分结果并在最后一根写 64，函数99的既有诊断位协议保持不变。

## 从源码构建

### 环境要求

| 工具 | 要求 |
| :--- | :--- |
| 系统 | Windows。 |
| C++ 工具链 | MSVC，支持 C++17，安装 C++ 桌面开发组件。 |
| CMake | 3.24 或更高；Visual Studio 2026 需要支持对应生成器的版本，可使用 VS 自带 CMake。 |
| Python | 运行测试时使用 Python 3.12，仅依赖标准库；仅构建 DLL 时不需要。 |

在 PowerShell 中获取源码：

```powershell
git clone https://github.com/foranarchocommunism-crypto/tdx-segment.git
cd tdx-segment
```

构建并测试 x86 / x64 Release：

```powershell
.\scripts\build.ps1
.\scripts\test.ps1
```

默认 DLL 输出位置：

```text
build/x86/Release/TDXSegment.dll
build/x64/Release/TDXSegment.dll
```

仅构建指定架构的 DLL：

```powershell
.\scripts\build.ps1 -Architecture x64 -DllOnly
```

脚本通过 `vswhere` 发现工具链，也支持显式指定工具与构建目录。手动 CMake、参数说明和打包入口见[构建文档](docs/build.md)。构建、测试与打包分开执行。

## 项目结构

```text
tdx-segment/
├─ src/                 C++ 内核、内部头文件与 DLL 适配层
├─ include/tdx_segment/ 对外 C++ 接口
├─ compat/tdx/          最小通达信 ABI 兼容声明
├─ reference/           Python 参考实现
├─ tests/               自测、对拍、合成用例与回归工具
├─ examples/tdx/        四份日常公式与一份迁移对照公式
├─ docs/                算法、架构、接口与验证文档
├─ scripts/             构建、测试、检查与打包入口
└─ .github/workflows/   Windows 双架构自动构建与测试
```

## 文档导航

| 想了解什么 | 从这里开始 |
| :--- | :--- |
| 如何加载 DLL、导入公式与排错 | [使用说明](docs/usage.md) |
| 如何构建、运行测试与打包 | [构建文档](docs/build.md) |
| 输入、函数号、事件位与错误码 | [接口协议](docs/interface.md) |
| 算法规则与实验恢复边界 | [算法核查](docs/algorithm.md) · [恢复策略](docs/recovery.md) |
| 模块划分与状态生命周期 | [架构说明](docs/architecture.md) |
| 从规则追溯到实现 | [规则与实现追溯](docs/traceability.md) |
| 哪些验证通过，哪些仍未完成 | [验证边界](docs/validation.md) |
| 公开源码的整理与来源 | [目录迁移](docs/migration.md) · [许可证与来源](docs/provenance.md) |
| 如何参与，版本有哪些变化 | [贡献指南](CONTRIBUTING.md) · [变更记录](CHANGELOG.md) |

## 参与贡献

欢迎通过 [Issues](https://github.com/foranarchocommunism-crypto/tdx-segment/issues) 反馈问题，或提交 Pull Request 改进文档、接入体验与可复现用例。

反馈时请说明客户端与 DLL 位数、版本、所用公式、复现步骤，以及期望结果和实际结果。算法问题优先提供**可公开的最小合成输入**及对应规则编号；不要上传私人行情、客户端配置、商业 DLL 或个人信息。代码贡献要求见[贡献指南](CONTRIBUTING.md)。

## 上一版已验证基线

以下结果对应 **`v0.1.0-rc.1` 发布提交 [`5873eeb`](https://github.com/foranarchocommunism-crypto/tdx-segment/commit/5873eebc27ec781f8646080ba32e85e3eb53d296)**；`main` 分支可继续更新文档，已发布源码与 DLL 以版本附件及构建清单为准。

| 验证项 | 结果与范围 |
| :--- | :--- |
| **本地双架构测试** | x86、x64 Release 各通过全部 **13 项 CTest**，包含 ABI、真实 DLL 加载与回调、异常输出、审计、范围、续算状态及两个既有性能门禁。 |
| **整理前后行为比较** | 固定种子 `20260828`，5,000 组随机输入及派生共 **51,455 个快照**；两架构、三策略的事件和完整审计输出零差异。 |
| **真实 DLL 回调比较** | 函数 `1/2/99`，每种架构 **154,365 次调用**，新旧输出零差异。 |
| **Python 参考对拍** | 已有支持的正式语义范围内，事件和审计对拍通过。 |
| **源码包独立构建** | 在含中文与空格的新目录中验证通过，不依赖原研究目录或私人行情。 |
| **GitHub 远程验证** | [首次双架构运行](https://github.com/foranarchocommunism-crypto/tdx-segment/actions/runs/38125105934)全部通过，各通过 **13 项 CTest**，同时通过公开文件、链接与公式检查。 |

完整本地记录见[发布验证报告](https://github.com/foranarchocommunism-crypto/tdx-segment/releases/download/v0.1.0-rc.1/validation-report.json)，当前分支的自动构建状态见 [Actions](https://github.com/foranarchocommunism-crypto/tdx-segment/actions)。测试通过说明相应验证集满足检查要求，不能替代所有输入的算法正确性证明。

## 验证边界

- **算法验收仍有缺口。** 历史最终组合全量验收未完成，历史三项 99% 算法一致性目标未达成；预发布不等于算法定稿。
- **实验恢复策略存在适用边界。** 本版保留既有判定规则与参考分歧，具体见[恢复策略](docs/recovery.md)和[验证边界](docs/validation.md)。
- **真实客户端人工验收尚未完成。** DLL 自加载与回调测试已通过，但本次在具体通达信版本中的公式导入、绘图、刷新和交互检查尚未执行。
- **结果依赖上游笔端点和输入历史。** 随包笔识别公式包含回填逻辑，不能将整套接入宣称为“完全不重绘”；改变端点、价格或历史范围可能影响计算结果。

## 本轮验收

x86/x64 原有13项测试完整保留，新增笔计算边界自测与真实 DLL 对拍，共15项。发布验收分别记录旧线段三策略事件与完整审计、新增笔计算参考对拍、旧/新 DLL 流水线对照、源码包独立构建和 GitHub CI。

笔参考实现直接解释冻结的原公式文本，采用明确的起始窗口和无效值约定。它只能证明 C++ 与该参考语义一致，不能证明每个通达信版本的内置函数边界相同。维护者目前没有旧笔公式导出结果；`05_笔计算迁移对照公式.txt` 已准备，客户端实际对照仍待执行。

发布报告记录每项是否执行以及源码提交，不把既有算法争议或历史门槛写成已通过。用户首页集中说明两类待定；完整验证边界保留在[验证文档](docs/validation.md)。

## 许可证与来源

自有源码、文档及随包公式采用 [MIT](LICENSE)，`Copyright (c) 2026 TDXSegment contributors`。最小 ABI 兼容声明不是官方 SDK，外部头文件替换及第三方来源见[来源说明](docs/provenance.md)。

首页借鉴 [Best-README-Template](https://github.com/othneildrew/Best-README-Template) 的居中介绍、展示和上手导航组织方式。图表与操作示意图为本项目绘制；合成案例来源和截取范围见[案例说明](docs/examples.md)。

## English overview

TDXSegment is a Windows C++17 DLL for pen identification and Chan-theory segment calculation in TongDaXin. The project includes x86/x64 builds, integration formulas, Python references, audit tools and regression tests. The default README is written for end users; this document covers development and validation. The pen migration still requires comparison in the actual TongDaXin host.
