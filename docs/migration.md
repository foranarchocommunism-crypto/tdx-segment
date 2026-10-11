# 首版目录迁移

| 原开发项目位置 | 公开项目位置 |
|---|---|
| native_dll/src | src |
| native_dll/include | include |
| native_dll/vendor_compat | compat/tdx |
| 01_向上线段完整判断流程_伪代码.py | reference/segment_reference.py |
| native_dll/tests | tests |
| native_dll/formula | examples/tdx |
| native_dll/validation/tdx_dll_runner.cpp | tests/tdx_dll_runner.cpp |

内核及公共头文件、参考 Python、既有测试和四份公式按原文件内容搬迁；最小兼容头只更新来源注释。新旧生产行为由发布对照检查绑定。

根目录 CMake 由原配置迁移：修改参考实现与兼容头路径，增加项目版本、BUILD_TESTING 开关及真实 DLL runner，保持原生产编译与链接语义。构建脚本改为发现工具链，并将编译、测试、打包分开。

算法核查文档保留规则内容、修正链接；架构文档保留当前模块和所有权描述。历史迁移蓝图、旧性能结论、部署日志和私人验证数据保存在原开发项目，不构成新仓库的执行待办。

源码提交及具体工具链由随包构建清单记录。为了避免公开个人身份，本地初始提交使用项目贡献者署名及保留的 .invalid 邮箱域。
