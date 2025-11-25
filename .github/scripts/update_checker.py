#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用应用更新检查器
只需要提供应用名称、版本检查URL和下载地址模板
"""

import re
import sys
import os
import yaml
import hashlib
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
import json


# Custom YAML representer for literal block style
class literal_str(str):
    pass


def literal_str_presenter(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.add_representer(literal_str, literal_str_presenter)


class UpdateChecker:
    def __init__(self, config_file):
        """初始化更新检查器"""
        self.config = self.load_config(config_file)
        self.app_name = self.config.get("app_name", "Unknown App")
        self.version_url = self.config.get("version_url", "")
        self.version_pattern = self.config.get("version_pattern", "")
        self.download_url_template = self.config.get("download_url_template", "")

    def load_config(self, config_file):
        """加载配置文件"""
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"加载配置文件失败: {e}")
            sys.exit(1)

    def fetch_latest_version(self):
        """获取最新版本号"""
        try:
            if not self.version_url:
                print("未配置版本检查URL")
                return None

            with urllib.request.urlopen(self.version_url) as response:
                html = response.read().decode("utf-8")

            version_match = re.search(self.version_pattern, html)
            if version_match:
                return version_match.group(1)
            else:
                print(f"无法从页面解析版本号")
                return None
        except urllib.error.URLError as e:
            print(f"获取版本页面失败: {e}")
            return None

    def calculate_sha256(self, url):
        """计算远程文件的SHA256哈希值"""
        try:
            print(f"正在计算文件哈希值: {url}")
            with urllib.request.urlopen(url) as response:
                sha256_hash = hashlib.sha256()
                for chunk in iter(lambda: response.read(4096), b""):
                    sha256_hash.update(chunk)
                return sha256_hash.hexdigest()
        except urllib.error.URLError as e:
            print(f"下载文件失败: {e}")
            return None

    def update_package_version(self, yaml_data, new_build_version):
        """更新package字段中的version"""
        try:
            version_parts = str(new_build_version).split(".")
            current_date = datetime.now().strftime("%m%d")

            # 确保版本号格式为：前三位使用传进来的build_version，最后一位使用当前日期
            if len(version_parts) >= 3:
                # 如果传入的版本已经有3位或更多，取前三位，最后一位用日期
                new_version = f"{version_parts[0]}.{version_parts[1]}.{version_parts[2]}.{current_date}"
            elif len(version_parts) == 2:
                # 如果传入的版本有2位，补一位0，最后一位用日期
                new_version = f"{version_parts[0]}.{version_parts[1]}.0.{current_date}"
            elif len(version_parts) == 1:
                # 如果传入的版本只有1位，补两位0，最后一位用日期
                new_version = f"{version_parts[0]}.0.0.{current_date}"
            else:
                # 默认情况
                new_version = f"{new_build_version}.0.0.{current_date}"

            if "package" in yaml_data and isinstance(yaml_data["package"], dict):
                yaml_data["package"]["version"] = new_version
                print(f"  包版本已更新: {new_version}")
                return True
            else:
                print("  警告：未找到package字段或格式不正确")
                return False
        except Exception as e:
            print(f"  更新包版本失败: {e}")
            return False

    def extract_version_from_filename(self, filename):
        """从文件名提取版本号"""
        # Vivaldi特定版本号模式 - 提取完整的4位版本号如 7.7.3851.52
        vivaldi_pattern = r"(\d+\.\d+\.\d+\.\d+)"
        match = re.search(vivaldi_pattern, filename)
        if match:
            return match.group(1)

        # 如果Vivaldi模式不匹配，使用常见版本号模式（备用）
        patterns = [
            r"(\d{4})",  # 4位数字，如 4200
            r"(\d+\.\d+\.\d+)",  # 标准版本号，如 1.2.3
            r"(\d+\.\d+)",  # 两位版本号，如 1.2
            r"(\d+(?:\.\d+)+)",  # 任意位版本号
        ]

        for pattern in patterns:
            match = re.search(pattern, filename)
            if match:
                return match.group(1)

        return None

    def get_current_version_from_yaml(self, yaml_file):
        """从YAML文件中获取当前版本号"""
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                content = f.read()

            yaml_data = yaml.safe_load(content)
            sources = yaml_data.get("sources", [])

            if not sources:
                return None

            # 从第一个source的URL中提取版本号
            for source in sources:
                url = source.get("url", "")
                if url:
                    current_version = self.extract_version_from_filename(url)
                    if current_version:
                        return current_version

            return None
        except Exception as e:
            print(f"从{yaml_file}读取当前版本失败: {e}")
            return None

    def update_yaml_file_with_github_url(self, yaml_file, new_version, github_url):
        """使用GitHub URL更新YAML文件"""
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                content = f.read()

            yaml_data = yaml.safe_load(content)
            sources = yaml_data.get("sources", [])

            if not sources:
                print(f"{yaml_file}中没有找到sources，跳过")
                return False

            # 查找并更新匹配的source
            updated = False
            for i, source in enumerate(sources):
                name = source.get("name", "")
                url = source.get("url", "")

                print(f"  检查source {i}: name='{name}', url='{url}'")

                if not name or not url:
                    print(f"  跳过source {i}: name或url为空")
                    continue

                # 提取当前版本号
                current_version = self.extract_version_from_filename(url)
                if not current_version:
                    print(f"  无法从URL提取版本号: {url}")
                    continue

                # 检查架构
                arch = "amd64"
                if "arm64" in url or "aarch64" in url:
                    arch = "arm64"

                # 构建新的文件名
                new_name = name.replace(current_version, new_version)

                # 直接使用提供的GitHub URL，但需要替换架构
                if "{arch}" in github_url:
                    new_url = github_url.replace("{arch}", arch)
                else:
                    # 如果GitHub URL不包含{arch}占位符，根据架构调整URL
                    if arch == "arm64" and "amd64" in github_url:
                        new_url = github_url.replace("amd64", "arm64")
                    else:
                        new_url = github_url

                # 添加代理前缀
                proxy_prefix = "https://edgeone.gh-proxy.com/"
                if not new_url.startswith(proxy_prefix):
                    new_url = proxy_prefix + new_url

                # 计算哈希值（从GitHub下载的文件）
                print(f"正在计算GitHub release文件的哈希值...")
                new_digest = self.calculate_sha256(new_url)
                if not new_digest:
                    print(f"  无法计算GitHub文件的哈希值")
                    continue

                # 更新source
                source["url"] = new_url
                source["digest"] = new_digest
                source["name"] = new_name

                updated = True
                print(f"  已使用GitHub URL更新 {arch} 架构的source条目")
                break

            if not updated:
                print("  没有找到可更新的source条目")
                return False

            # 更新package版本
            self.update_package_version(yaml_data, new_version)

            # 同步更新build字段中的文件名
            if updated and "build" in yaml_data and isinstance(yaml_data["build"], str):
                build_content = yaml_data["build"]
                # 直接使用新的name替换build字段中的文件名
                # 查找包含旧版本号的文件名模式
                old_filename_pattern = rf"{re.escape(name)}"
                updated_build = re.sub(old_filename_pattern, new_name, build_content)

                if updated_build != build_content:
                    yaml_data["build"] = updated_build
                    print(f"  build字段文件名已同步更新: {new_name}")

            # 保持YAML格式
            if "build" in yaml_data and isinstance(yaml_data["build"], str):
                yaml_data["build"] = literal_str(yaml_data["build"])

            # 写回文件
            updated_content = yaml.dump(
                yaml_data, allow_unicode=True, default_flow_style=False, sort_keys=False
            )
            with open(yaml_file, "w", encoding="utf-8") as f:
                f.write(updated_content)

            print(f"已使用GitHub URL更新{yaml_file}")
            return True

        except Exception as e:
            print(f"使用GitHub URL更新{yaml_file}失败: {e}")
            return False

    def update_yaml_file(self, yaml_file, new_version):
        """更新YAML文件"""
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                content = f.read()

            yaml_data = yaml.safe_load(content)
            sources = yaml_data.get("sources", [])

            if not sources:
                print(f"{yaml_file}中没有找到sources，跳过")
                return False

            # 查找并更新匹配的source
            updated = False
            for i, source in enumerate(sources):
                name = source.get("name", "")
                url = source.get("url", "")

                print(f"  检查source {i}: name='{name}', url='{url}'")

                if not name or not url:
                    print(f"  跳过source {i}: name或url为空")
                    continue

                # 提取当前版本号
                current_version = self.extract_version_from_filename(url)
                if not current_version:
                    print(f"  无法从URL提取版本号: {url}")
                    continue

                # 检查架构
                arch = "amd64"
                if "arm64" in url or "aarch64" in url:
                    arch = "arm64"

                # 构建新的下载URL和文件名
                new_url = self.download_url_template.format(
                    version=new_version, arch=arch
                )
                new_name = name.replace(current_version, new_version)

                # 计算哈希值
                print(f"正在计算 {arch} 新版本的哈希值...")
                new_digest = self.calculate_sha256(new_url)
                if not new_digest:
                    continue

                # 更新source
                source["url"] = new_url
                source["digest"] = new_digest
                source["name"] = new_name

                updated = True
                print(f"  已更新 {arch} 架构的source条目")
                break

            if not updated:
                print("  没有找到可更新的source条目")
                return False

            # 更新package版本
            self.update_package_version(yaml_data, new_version)

            # 同步更新build字段中的文件名
            if updated and "build" in yaml_data and isinstance(yaml_data["build"], str):
                build_content = yaml_data["build"]
                # 直接使用新的name替换build字段中的文件名
                # 查找包含旧版本号的文件名模式
                old_filename_pattern = rf"{re.escape(name)}"
                updated_build = re.sub(old_filename_pattern, new_name, build_content)

                if updated_build != build_content:
                    yaml_data["build"] = updated_build
                    print(f"  build字段文件名已同步更新: {new_name}")

            # 保持YAML格式
            if "build" in yaml_data and isinstance(yaml_data["build"], str):
                yaml_data["build"] = literal_str(yaml_data["build"])

            # 写回文件
            updated_content = yaml.dump(
                yaml_data, allow_unicode=True, default_flow_style=False, sort_keys=False
            )
            with open(yaml_file, "w", encoding="utf-8") as f:
                f.write(updated_content)

            print(f"已更新{yaml_file}")
            return True

        except Exception as e:
            print(f"更新{yaml_file}失败: {e}")
            return False

    def find_yaml_files(self):
        """自动查找YAML文件"""
        yaml_files = []

        # 查找主要的linglong.yaml
        if Path("linglong.yaml").exists():
            yaml_files.append("linglong.yaml")

        # 查找架构子目录中的YAML文件
        for arch_dir in ["amd64", "arm64", "sw64", "riscv64", "loong64", "mips64"]:
            arch_yaml = Path(arch_dir) / "linglong.yaml"
            if arch_yaml.exists():
                yaml_files.append(str(arch_yaml))

        return yaml_files

    def run(self):
        """运行更新检查"""
        print(f"开始检查 {self.app_name} 更新...")

        # 获取最新版本
        latest_version = self.fetch_latest_version()
        if not latest_version:
            print("无法获取最新版本，退出")
            return 1

        print(f"最新版本: {latest_version}")

        # 查找所有YAML文件
        yaml_files = self.find_yaml_files()
        if not yaml_files:
            print("未找到任何YAML文件，退出")
            return 1

        print(f"找到 {len(yaml_files)} 个YAML文件")

        # 检查当前版本是否与最新版本一致
        need_update = False
        force_update = os.environ.get("FORCE_UPDATE") == "true"

        for yaml_file in yaml_files:
            current_version = self.get_current_version_from_yaml(yaml_file)
            if current_version:
                print(f"{yaml_file} 当前版本: {current_version}")
                if current_version != latest_version or force_update:
                    need_update = True
            else:
                need_update = True

        # 如果不需要更新，直接返回（除非强制更新）
        if not need_update and not force_update:
            print(f"所有文件当前版本与最新版本({latest_version})一致，无需更新")
            return 0

        if force_update:
            print(f"强制更新模式：将更新到版本 {latest_version}")

        # 检查是否使用GitHub URL模式
        if os.environ.get("USE_GITHUB_URL") == "true":
            # 直接使用提供的GitHub URL更新
            github_url = os.environ.get("GITHUB_RELEASE_URL")
            if github_url:
                print(f"使用GitHub release URL更新: {github_url}")
                # 更新每个YAML文件
                success_count = 0
                for yaml_file in yaml_files:
                    print(f"\n处理 {yaml_file}...")
                    if self.update_yaml_file_with_github_url(
                        yaml_file, latest_version, github_url
                    ):
                        success_count += 1

                if success_count > 0:
                    print(
                        f"\n使用GitHub URL更新完成！成功更新了 {success_count} 个文件。"
                    )
                    if "GITHUB_OUTPUT" in os.environ or os.environ.get("GITHUB_OUTPUT"):
                        output_file = os.environ.get("GITHUB_OUTPUT", "/tmp/output.txt")
                        with open(output_file, "a") as f:
                            f.write("has_changes=true\n")
                    return 0
                else:
                    print("\n使用GitHub URL更新失败！")
                    return 1
            else:
                print("未提供GitHub release URL")
                return 1

        # 普通模式：获取下载URL并输出信息给workflow
        # 构建下载URL
        download_url = self.download_url_template.format(
            version=latest_version, arch="amd64"
        )
        print(f"下载URL: {download_url}")

        # 输出信息给GitHub Actions
        if "GITHUB_OUTPUT" in os.environ or os.environ.get("GITHUB_OUTPUT"):
            output_file = os.environ.get("GITHUB_OUTPUT", "/tmp/output.txt")
            with open(output_file, "a") as f:
                f.write(f"has_changes=true\n")
                f.write(f"new_version={latest_version}\n")
                f.write(f"download_url={download_url}\n")

        return 0


def main():
    """主函数"""
    if len(sys.argv) != 2:
        print("用法: python3 update_checker.py <config_file.json>")
        print("\n配置示例:")
        print("{")
        print('  "app_name": "Sublime Text",')
        print('  "version_url": "https://www.sublimetext.com/download",')
        print('  "version_pattern": "Build\\\\s+(\\\\d{4})",')
        print(
            '  "download_url_template": "https://download.sublimetext.com/sublime-text_build-{version}_{arch}.deb"'
        )
        print("}")
        sys.exit(1)

    config_file = sys.argv[1]
    checker = UpdateChecker(config_file)
    sys.exit(checker.run())


if __name__ == "__main__":
    main()
