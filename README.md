# TDXSegment

将通达信的笔端点事件与高低价序列转换为已确认线段事件的 Windows C++17 DLL，包含可执行的 Python 参考实现、上下镜像算法、结构审计和双架构回归测试。

当前版本：**v0.1.0-rc.1（预发布）**。工程验证结果随发布包提供。算法仍有待定情况与已知参考分歧，历史最终组合全量验收没有完成；本版不宣称达到历史三项 99% 一致率目标。详见[验证边界](docs/validation.md)。

## 快速构建

需要 Windows、带 C++ 桌面开发组件的 MSVC、CMake 3.24 或更高版本。Visual Studio 2026 需要支持对应生成器的新版本 CMake，可使用 VS 自带版本。测试使用 Python 3.12，仅依赖标准库。

在仓库根目录运行 PowerShell：

```powershell
.\scripts\build.ps1
.\scripts\test.ps1
```

默认构建 x86 和 x64 Release，DLL 分别位于 `build/x86/Release/TDXSegment.dll` 和 `build/x64/Release/TDXSegment.dll`。构建、测试和打包是独立操作。只构建 DLL、不查找 Python：

```powershell
.\scripts\build.ps1 -Architecture x64 -DllOnly
```

脚本通过 `vswhere` 发现工具链，也支持 `-CMakePath`、`-Generator`、`-BuildRoot`、`-VendorIncludeDir` 和 `-PythonExecutable`。手动 CMake 命令与打包方法见[构建说明](docs/build.md)。

## 接入通达信

1. 根据**通达信进程位数**选择 x86 或 x64 DLL。
2. 按客户端 DLL 管理方式放置文件并绑定到第 1 槽。更新文件前退出客户端，并备份原 DLL。
3. 先运行 [01 接口自检公式](examples/tdx/01_DLL接口自检公式.txt)。函数 `99` 用于确认加载、接线和输入，不能代替线段计算。
4. 自检通过后使用 [02 正式线段公式](examples/tdx/02_通达信笔识别与正式线段公式.txt)。[03 主图版本](examples/tdx/03_主图替换_待定区恢复K线版.txt)和 [04 精准价格测试模式](examples/tdx/04_笔端点精准价格测试模式.txt)为其他接入方式。

公式默认调用 `TDXDLL1`；绑定到其他槽位时同步更改调用名称。完整步骤见[使用说明](docs/usage.md)。发布脚本不会修改客户端。

## 接口与规则

| 函数号 | 用途 |
|---|---|
| 1 | 默认线段策略，恢复门槛 43/13/13 |
| 2 | 实验恢复策略，门槛 43/13/9 |
| 99 | 接线、输入与初始锚点探测 |

三条输入为等长的 `float` 数组：笔端点标记、高价、低价。输出是逐 K 线整数事件位掩码。公共 C++ 接口见 [tdx_segment.hpp](include/tdx_segment/tdx_segment.hpp)，协议见[接口说明](docs/interface.md)。

- [算法核查说明](docs/algorithm.md)与[恢复策略补充](docs/recovery.md)
- [架构与状态所有权](docs/architecture.md)
- [规则与实现追溯](docs/traceability.md)
- [目录迁移说明](docs/migration.md)
- [贡献指南](CONTRIBUTING.md)、[变更记录](CHANGELOG.md)、[许可证与来源](docs/provenance.md)

## English overview

TDXSegment is a Windows C++17 formula DLL that converts endpoint markers and high/low price arrays into confirmed segment event masks. It includes a runnable Python reference, synthetic regression tests, structural audit tools, and x86/x64 builds. This release is a prerelease with documented algorithm limitations. See [build instructions](docs/build.md) and [validation boundaries](docs/validation.md). Licensed under the [MIT License](LICENSE).
