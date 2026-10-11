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
    notes = f"""# TDXSegment {tag}

源码提交：`{commit}`。本地预发布，MIT，TDXSegment contributors。

包含 Windows x86/x64 Release DLL、四份通达信接入公式与使用说明。正式函数1、实验函数2、接线函数99及 ABI 保持既有行为。按进程位数选包；先运行01自检公式。公式默认第1槽。

两架构各13项CTest通过；{report['random_groups']}组随机输入及派生共{report['snapshot_count']}个快照，三策略事件/完整审计新旧零差异，真实DLL 1/2/99零差异，Python正式语义对拍通过。独立源码包验证见 validation-report.json。

本次为项目整理与发布验证，不是新的速度优化。历史最终组合全量验收未完成，历史三项99%算法目标未达成。远程GitHub CI和本次客户端人工验证未运行。

SHA256SUMS.txt 校验发布文件；release-manifest.json 记录源码、工具链、架构及哈希。完整源码和构建说明在源码包 README.md / docs/build.md。
"""
    (output / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8")
    for arch, metadata in architectures.items():
        package = output / f"TDXSegment-{tag}-windows-{arch}.zip"
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.write(args.build_root.resolve() / arch / "Release" / "TDXSegment.dll", "TDXSegment.dll")
            for rel in ("LICENSE", "VERSION", "docs/usage.md", "docs/interface.md", "include/tdx_segment/tdx_segment.hpp"):
                archive.write(ROOT / rel, rel)
            for formula in sorted((ROOT / "examples" / "tdx").glob("*.txt")):
                archive.write(formula, "examples/tdx/" + formula.name)
            archive.writestr("README.md", f"# TDXSegment {tag} / {arch}\n\n对应客户端进程位数选择DLL。先看 docs/usage.md，运行01自检，再使用02正式公式。默认槽位TDXDLL1。\n\n源码提交：{commit}\nDLL SHA-256：{metadata['dll_sha256']}\n\n完整源码见同版源码包。许可见 LICENSE，验证及限制见 RELEASE_NOTES.md 和 validation-report.json。\n")
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
