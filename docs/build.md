# 构建、测试与打包

首次发布在 Visual Studio 2026 / MSVC 工具链和 Python 3.12 上验证，准确版本以发布构建清单为准。工程要求 Windows、MSVC C++17 和 CMake 3.24+；VS 2026 需使用支持该生成器的 CMake。GitHub Actions 配置固定使用 `windows-2025-vs2026`；[首次远程运行](https://github.com/foranarchocommunism-crypto/tdx-segment/actions/runs/38125105934)的 x86/x64 构建和全部 CTest 已通过。发布前本地记录仍以随包验证报告为准，当前分支状态见仓库 Actions 页面。

## 脚本

从仓库根目录运行：

```powershell
.\scripts\build.ps1 -Architecture all
.\scripts\test.ps1 -Architecture all
```

默认 Release，支持 `-Configuration Debug`。`build.ps1` 只配置、编译；`test.ps1` 执行 CTest 并在对应构建目录生成 `ctest-results.xml`。计时门禁串行执行，保持原阈值。首次发布的性能结论以发布报告中的机器和配置为准。

自定义路径（相对 BuildRoot 按仓库根目录解释）：

```powershell
.\scripts\build.ps1 -BuildRoot 'build/custom' -CMakePath 'C:/Tools/CMake/bin/cmake.exe' -Generator 'Visual Studio 18 2026' -PythonExecutable 'C:/Python312/python.exe'
.\scripts\test.ps1 -BuildRoot 'build/custom' -CMakePath 'C:/Tools/CMake/bin/cmake.exe'
```

`-VendorIncludeDir` 可指定外部 `PluginTCalcFunc.h` 目录，替换后必须重验 ABI 和真实加载。默认使用项目内最小兼容声明。

## 直接使用 CMake

```powershell
cmake -S . -B build/x64 -G 'Visual Studio 18 2026' -A x64 -DBUILD_TESTING=ON
cmake --build build/x64 --config Release --parallel
ctest --test-dir build/x64 -C Release --output-on-failure
```

x86 对应 `-A Win32`。仅构建 DLL 时设置 `-DBUILD_TESTING=OFF`，不查找 Python。保留 `/W4 /WX /permissive- /utf-8 /EHsc`、静态 MSVC CRT，未启用 IPO 或 fast-math。

## 发布检查

`tests/release_equivalence.py` 比较整理前后的两个构建根目录。默认 seed=20260828、5,000 组随机输入，加已有定向、前缀、镜像和非法输入派生。检查三种策略的事件与完整审计，以及真实 DLL 函数 1/2/99；同时执行 Python 正式语义事件和审计对拍。

```powershell
python -B tests/release_equivalence.py --baseline-build 'C:/baseline-build' --candidate-build 'build' --output 'build/release-validation.json'
.\scripts\check_formula_identifiers.ps1
python -B scripts/check_public_tree.py
```

对照构建需包含本仓库同名 runner；首版基线来自整理前冻结源码，额外添加的 `tdx_dll_runner` 只加载 DLL，不链接内核。没有对照基线的使用者可直接运行完整 CTest；新版本发布需选择明确的前一版本构建。

## 打包

在源码提交干净且验收通过后，给同一提交添加与 VERSION 对应的本地标签。运行：

```powershell
.\scripts\package.ps1 -ValidationReport 'build/release-validation.json'
```

打包器核对源码提交、标签、验收记录及 DLL 哈希，再生成源码 ZIP、两个二进制 ZIP、验证报告、发布说明、SHA256SUMS 和构建清单。默认输出 `dist/v0.1.0-rc.2`，拒绝覆盖已有发布目录。打包需要 Git 和 Python 3.12；普通 DLL 构建不需要 Python。

预发布 ZIP 为便携包，不执行安装、客户端配置修改或远程上传。

## 笔迁移扩展对拍

```powershell
python -B tests/crosscheck_pen_formula.py --build-root build --baseline-build 'C:/baseline-build' --random-cases 5000 --seed 20260828 --output build/pen-validation.json
```

正式发布记录需要合并这份报告。C++与冻结公式解释器的对拍不替代通达信宿主验收；运行05对照公式的方法见[笔迁移](pen-migration.md)。
