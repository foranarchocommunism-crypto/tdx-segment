"""Package a clean, tagged, validated source commit without deploying it."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import posixpath
import re
import subprocess
import zipfile
from pathlib import Path

from user_manual import render_manual

ROOT = Path(__file__).resolve().parents[1]
AUTHOR_NAME = "Sixun Huang"
USER_ASSETS = ("hero.svg", "case-overview.svg", "case-first-pending.svg", "case-second-pending.svg", "install-01.svg", "install-02.svg", "install-03.svg")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(commit: str) -> dict[str, bytes]:
    paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "-z", commit], cwd=ROOT)
    return {path.decode("utf-8"): subprocess.check_output(["git", "show", f"{commit}:{path.decode('utf-8')}"], cwd=ROOT) for path in paths.split(b"\0") if path}


def cmake_metadata(build: Path) -> dict:
    cache = (build / "CMakeCache.txt").read_text(encoding="utf-8")
    compiler_files = list((build / "CMakeFiles").glob("*/CMakeCXXCompiler.cmake"))
    if len(compiler_files) != 1:
        raise RuntimeError("Expected exactly one configured compiler version")
    compiler = compiler_files[0].read_text(encoding="utf-8")
    version = re.search(r'set\(CMAKE_CXX_COMPILER_VERSION "([^"]+)"\)', compiler)
    source = re.search(r"^CMAKE_HOME_DIRECTORY:INTERNAL=(.*)$", cache, re.M)
    if not source or Path(source[1]).resolve() != ROOT:
        raise RuntimeError("Build cache does not belong to this source checkout")
    def value(name: str) -> str:
        match = re.search(rf"^{name}:[^=]+=(.*)$", cache, re.M)
        return match[1] if match else ""
    project = (build / "TDXSegment.vcxproj").read_text(encoding="utf-8-sig")
    config = re.search(r"<ItemDefinitionGroup Condition=\"[^\"]*'Release\|[^\"]*\">(.*?)</ItemDefinitionGroup>", project, re.S)
    if not config or "MultiThreaded</RuntimeLibrary>" not in config[1] or "TreatWarningAsError>true" not in config[1]:
        raise RuntimeError("Release project is missing static CRT or strict warnings")
    return {"compiler": "MSVC", "compiler_version": version[1] if version else "unknown", "generator": value("CMAKE_GENERATOR"), "platform": value("CMAKE_GENERATOR_PLATFORM"), "cxx_standard": 17, "configuration": "Release", "runtime": "/MT", "strict_options": ["/W4", "/WX", "/permissive-", "/utf-8", "/EHsc"], "release_flags": value("CMAKE_CXX_FLAGS_RELEASE"), "ipo": False, "fast_math": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", type=Path, default=ROOT / "build")
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-ref", default="HEAD", help="Accepted source ref; a different HEAD may change packaging/docs only")
    args = parser.parse_args()
    version = (ROOT / "VERSION").read_text().strip()
    tag = f"v{version}"
    packaging_commit = git("rev-parse", "HEAD")
    commit = git("rev-parse", f"{args.source_ref}^{{commit}}")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("Refuse to package a dirty checkout")
    if git("rev-parse", f"{tag}^{{commit}}") != commit:
        raise RuntimeError("Release tag and accepted source ref must identify the same commit")
    accepted_files = source_files(commit)
    current_files = source_files(packaging_commit)
    # A packaging refresh can reuse accepted DLLs only while every production
    # source, ABI declaration and build setting remains byte-identical.
    build_paths = {name for name in set(accepted_files) | set(current_files) if name.startswith(("src/", "include/", "compat/")) or name in {"CMakeLists.txt", "VERSION"}}
    if any(accepted_files.get(name) != current_files.get(name) for name in build_paths):
        raise RuntimeError("Packaging refresh changed computation sources or build settings; build and validate a new release")
    report = json.loads(args.validation_report.read_text(encoding="utf-8"))
    if not report.get("passed") or report.get("source_commit") != commit or report.get("version") != version:
        raise RuntimeError("Validation receipt does not bind this version and commit")
    if not report.get("source_archive_smoke", {}).get("passed") or not report.get("public_tree_checks", {}).get("passed"):
        raise RuntimeError("Independent archive smoke/public file checks are required")
    pen = report.get("pen_migration", {})
    if not pen.get("passed") or pen.get("random_groups", 0) < 5000 or pen.get("seed") != 20260828:
        raise RuntimeError("Pen migration needs the extended frozen-formula validation receipt")
    if any(pen.get("architectures", {}).get(a, {}).get("mismatches") != 0 for a in ("x86", "x64")):
        raise RuntimeError("Both pen migration architectures must pass with zero differences")
    architectures = {}
    for arch in ("x86", "x64"):
        dll = args.build_root.resolve() / arch / "Release" / "TDXSegment.dll"
        actual = sha(dll)
        if actual != report["architectures"][arch]["dll_sha256"]:
            raise RuntimeError(f"Validated DLL was replaced: {arch}")
        architectures[arch] = {"dll_sha256": actual, **cmake_metadata(dll.parent.parent)}
    output = args.output.resolve() if args.output else ROOT / "dist" / tag
    if output.exists():
        raise RuntimeError(f"Refuse to overwrite release directory: {output}")
    output.mkdir(parents=True)
    source_zip = output / f"tdx-segment-{tag}-source.zip"
    subprocess.run(["git", "archive", "--format=zip", f"--prefix=tdx-segment-{tag}/", f"--output={source_zip}", packaging_commit], cwd=ROOT, check=True)
    if sha(source_zip) != report["source_archive_smoke"]["source_archive_sha256"]:
        raise RuntimeError("Source archive differs from independently tested archive")
    report_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    (output / "validation-report.json").write_text(report_text, encoding="utf-8")
    notes = f"""# 缠论线段自动划分 {tag}

作者：[{AUTHOR_NAME}](https://github.com/foranarchocommunism-crypto)。版权：Copyright (c) 2026 {AUTHOR_NAME}，MIT。

DLL验收源码提交：`{commit}`。源码包、文档与打包提交：`{packaging_commit}`。预发布。

笔计算移入DLL，新增无状态函数10/11，四份日常公式同步更新，另提供05迁移对照公式。正式函数1、实验函数2、接线函数99及ABI保持既有行为。按通达信位数选包，先运行01自检，公式默认第1槽。用户指南与开发者说明分开，附合成案例和安装示意图。

两架构各15项CTest通过；既有{report['random_groups']}组随机输入及派生共{report['snapshot_count']}个快照，三策略事件/完整审计新旧零差异。笔迁移另用{pen['random_groups']}组及派生共{pen['snapshots']}个快照，对比冻结原公式参考解释器与真实DLL 10/11，零差异；同一参考笔流经旧新DLL 1/2/99流水线零差异。独立源码包验证见 validation-report.json。

通达信内的笔迁移对照仍待执行：维护者没有旧公式导出结果，自动对拍仅验证明确的参考语义。05对照公式已准备，实际宿主差异数未记录。第一、第二类待定保持现行规则；历史最终组合全量验收未完成，三项99%算法目标未达成。本次不开展新的速度优化。

远程GitHub CI状态：{report.get('remote_ci', 'not_run')}。本次客户端人工验证：{report.get('client_manual_validation', 'not_run')}。

用户包只含DLL、五份公式、图文说明和MIT许可证，不含Python、源码、开发头文件或测试报告，也不需要安装Python。解压后双击“使用说明.html”查看图文步骤。

源码包更新了文档、许可证和打包工具，生产源码及构建设置与已验收版本逐字一致；源码标签和DLL保留上述已验收提交。SHA256SUMS.txt校验发布文件，release-manifest.json分别记录DLL源码、源码包、打包提交、工具链、架构及文件哈希。验证报告单独提供在发布页面。
"""
    (output / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8")
    for arch, metadata in architectures.items():
        package = output / f"TDXSegment-{tag}-windows-{arch}.zip"
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.write(args.build_root.resolve() / arch / "Release" / "TDXSegment.dll", "TDXSegment.dll")
            license_text = current_files["LICENSE"].decode("utf-8")
            if f"Copyright (c) 2026 {AUTHOR_NAME}" not in license_text:
                raise RuntimeError("Current MIT copyright and project author disagree")
            archive.writestr("LICENSE", current_files["LICENSE"])
            for name in USER_ASSETS:
                archive.writestr("docs/assets/" + name, current_files["docs/assets/" + name])
            formulas = {name: blob for name, blob in accepted_files.items() if name.startswith("examples/tdx/") and name.endswith(".txt")}
            if len(formulas) != 5:
                raise RuntimeError("Expected four daily formulas and one host comparison formula")
            for name, blob in sorted(formulas.items()):
                archive.writestr("公式/" + posixpath.basename(name), blob)
            user_readme = (ROOT / "README.md").read_text(encoding="utf-8").replace(
                "](README.DEV.md)", f"](https://github.com/foranarchocommunism-crypto/tdx-segment/blob/{packaging_commit}/README.DEV.md)")
            user_readme += f"\n版本：{tag}。此包适用于{'32' if arch == 'x86' else '64'}位通达信。\n"
            archive.writestr("README.md", user_readme)
            archive.writestr("使用说明.html", render_manual(user_readme))
        # Keep user documentation navigable after extraction; developer files
        # stay in the source archive and release-page attachments.
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            expected = {"TDXSegment.dll", "LICENSE", "README.md", "使用说明.html"} | {"docs/assets/" + name for name in USER_ASSETS} | {"公式/" + posixpath.basename(name) for name in formulas}
            if names != expected:
                raise RuntimeError("User ZIP contains files outside the DLL/formulas/manual/license allowlist")
            for name in names:
                if not name.endswith((".md", ".html")):
                    continue
                text = archive.read(name).decode("utf-8")
                targets = re.findall(r"\]\(([^\s)]+)\)", text) if name.endswith(".md") else re.findall(r'(?:href|src)="([^"]+)"', text)
                for target in targets:
                    target = html.unescape(target)
                    if target.startswith(("http://", "https://", "#", "mailto:")):
                        continue
                    destination = posixpath.normpath(posixpath.join(posixpath.dirname(name), target.split("#", 1)[0]))
                    if destination not in names:
                        raise RuntimeError(f"Broken package documentation link: {name} -> {target}")
        metadata["package"] = package.name
        metadata["package_sha256"] = sha(package)
        with zipfile.ZipFile(package) as archive:
            metadata["package_files"] = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in sorted(archive.namelist())}
    manifest = {"schema": "tdx-segment-release-manifest-v1", "version": version, "tag": tag, "source_commit": commit, "packaging_commit": packaging_commit, "author": {"name": AUTHOR_NAME, "github": "foranarchocommunism-crypto"}, "copyright": f"Copyright (c) 2026 {AUTHOR_NAME}", "package_contents": "user-dll-formulas-manual-license", "license": "MIT", "source_archive": {"file": source_zip.name, "source_commit": packaging_commit, "sha256": sha(source_zip)}, "architectures": architectures, "validation_report_sha256": sha(output / "validation-report.json"), "source_files": {name: hashlib.sha256(blob).hexdigest() for name, blob in current_files.items()}}
    (output / "release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = sorted(p for p in output.iterdir() if p.is_file())
    (output / "SHA256SUMS.txt").write_text("".join(f"{sha(p)}  {p.name}\n" for p in files), encoding="utf-8")
    print(json.dumps({"release_directory": str(output), "source_commit": commit, "files": [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(output.iterdir())]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
