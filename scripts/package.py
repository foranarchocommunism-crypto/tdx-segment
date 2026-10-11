"""Package a clean, tagged, validated source commit without deploying it."""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    args = parser.parse_args()
    version = (ROOT / "VERSION").read_text().strip()
    tag = f"v{version}"
    commit = git("rev-parse", "HEAD")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("Refuse to package a dirty checkout")
    if git("rev-parse", f"{tag}^{{commit}}") != commit:
        raise RuntimeError("Release tag and HEAD must identify the same source commit")
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
    subprocess.run(["git", "archive", "--format=zip", f"--prefix=tdx-segment-{tag}/", f"--output={source_zip}", tag], cwd=ROOT, check=True)
    if sha(source_zip) != report["source_archive_smoke"]["source_archive_sha256"]:
        raise RuntimeError("Source archive differs from independently tested archive")
    report_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    (output / "validation-report.json").write_text(report_text, encoding="utf-8")
    notes = f"""# 缠论线段自动划分 {tag}

源码提交：`{commit}`。预发布，MIT，TDXSegment contributors。

笔计算移入DLL，新增无状态函数10/11，四份日常公式同步更新，另提供05迁移对照公式。正式函数1、实验函数2、接线函数99及ABI保持既有行为。按通达信位数选包，先运行01自检，公式默认第1槽。用户指南与开发者说明分开，附合成案例和安装示意图。

两架构各15项CTest通过；既有{report['random_groups']}组随机输入及派生共{report['snapshot_count']}个快照，三策略事件/完整审计新旧零差异。笔迁移另用{pen['random_groups']}组及派生共{pen['snapshots']}个快照，对比冻结原公式参考解释器与真实DLL 10/11，零差异；同一参考笔流经旧新DLL 1/2/99流水线零差异。独立源码包验证见 validation-report.json。

通达信内的笔迁移对照仍待执行：维护者没有旧公式导出结果，自动对拍仅验证明确的参考语义。05对照公式已准备，实际宿主差异数未记录。第一、第二类待定保持现行规则；历史最终组合全量验收未完成，三项99%算法目标未达成。本次不开展新的速度优化。

远程GitHub CI状态：{report.get('remote_ci', 'not_run')}。本次客户端人工验证：{report.get('client_manual_validation', 'not_run')}。

SHA256SUMS.txt校验发布文件；release-manifest.json记录源码、工具链、架构及哈希。普通使用见README.md，开发与构建见源码包README.DEV.md。
"""
    (output / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8")
    for arch, metadata in architectures.items():
        package = output / f"TDXSegment-{tag}-windows-{arch}.zip"
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.write(args.build_root.resolve() / arch / "Release" / "TDXSegment.dll", "TDXSegment.dll")
            for rel in ("LICENSE", "VERSION", "docs/usage.md", "docs/interface.md", "docs/pen-migration.md", "include/tdx_segment/tdx_segment.hpp", "include/tdx_segment/pen_formula.hpp"):
                archive.write(ROOT / rel, rel)
            for asset in sorted((ROOT / "docs/assets").glob("*.svg")):
                archive.write(asset, "docs/assets/" + asset.name)
            archive.write(ROOT / "tests/fixtures/legacy_pen_formula.txt", "tests/fixtures/legacy_pen_formula.txt")
            archive.write(ROOT / "reference/pen_formula_reference.py", "reference/pen_formula_reference.py")
            for formula in sorted((ROOT / "examples" / "tdx").glob("*.txt")):
                archive.write(formula, "examples/tdx/" + formula.name)
            user_readme = (ROOT / "README.md").read_text(encoding="utf-8").replace(
                "](README.DEV.md)", f"](https://github.com/foranarchocommunism-crypto/tdx-segment/blob/{tag}/README.DEV.md)")
            archive.writestr("README.md", user_readme + f"\n包内版本：{tag} / {arch}。源码提交：`{commit}`。\n")
            archive.writestr("build-info.json", json.dumps({"version": version, "source_commit": commit, "architecture": arch, **metadata}, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("RELEASE_NOTES.md", notes)
            archive.writestr("validation-report.json", report_text)
        # Interface documentation must remain navigable in an extracted binary ZIP.
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            for name in names:
                if not name.endswith(".md"):
                    continue
                for target in re.findall(r"\]\(([^\s)]+)\)", archive.read(name).decode("utf-8")):
                    if target.startswith(("http://", "https://", "#", "mailto:")):
                        continue
                    destination = posixpath.normpath(posixpath.join(posixpath.dirname(name), target.split("#", 1)[0]))
                    if destination not in names:
                        raise RuntimeError(f"Broken package documentation link: {name} -> {target}")
        metadata["package"] = package.name
        metadata["package_sha256"] = sha(package)
    manifest = {"schema": "tdx-segment-release-manifest-v1", "version": version, "tag": tag, "source_commit": commit, "license": "MIT", "source_archive": {"file": source_zip.name, "sha256": sha(source_zip)}, "architectures": architectures, "validation_report_sha256": sha(output / "validation-report.json"), "source_files": {}}
    for item in subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0"):
        if item:
            rel = item.decode("utf-8")
            manifest["source_files"][rel] = sha(ROOT / rel)
    (output / "release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = sorted(p for p in output.iterdir() if p.is_file())
    (output / "SHA256SUMS.txt").write_text("".join(f"{sha(p)}  {p.name}\n" for p in files), encoding="utf-8")
    print(json.dumps({"release_directory": str(output), "source_commit": commit, "files": [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(output.iterdir())]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
