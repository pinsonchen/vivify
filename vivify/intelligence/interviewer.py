"""交互式配置问答。"""

from __future__ import annotations

from .configurator import ConfigQuestion


# 问题分组标题：根据 question.key 前缀匹配
GROUP_HEADERS: dict[str, str] = {
    "project.": "── 项目信息 (Project) ──",
    "deploy.": "── 部署配置 (Deploy) ──",
    "commands.": "── 构建命令 (Commands) ──",
}


# 示例值映射：根据 question.key 精确匹配
EXAMPLES: dict[str, str] = {
    "deploy.url": "https://example.com",
    "deploy.health_endpoint": "/health 或 /api/status",
    "commands.test": "pytest / npm test / go test ./...",
    "commands.build": "npm run build / python -m build",
    "project.name": "my-awesome-project",
}


class Interviewer:
    """对未自动发现的配置项进行交互式询问。"""

    def conduct(
        self,
        questions: list[ConfigQuestion],
        non_interactive: bool = False,
    ) -> dict[str, str]:
        """对 ``default`` 为 None 的必要配置项向用户交互式提问。

        ``non_interactive`` 模式下：使用 default 值或空字符串（不弹提示）。
        返回 ``{key: value}`` 字典。
        """
        results: dict[str, str] = {}
        printed_groups: set[str] = set()

        for q in questions:
            # 非交互模式：直接采用 default 或空值，不打印任何提示
            if non_interactive:
                if q.default is not None:
                    results[q.key] = q.default
                else:
                    results[q.key] = ""
                continue

            # 输出分组标题（每组首次遇到时打印一次）
            self._maybe_print_group_header(q, printed_groups)

            if q.default is not None and q.source:
                # 智能建议：default 来自自动发现/AI 分析
                results[q.key] = self._ask_smart_suggestion(q)
            elif q.default is not None:
                results[q.key] = self._ask_with_default(q)
            else:
                results[q.key] = self._ask(q)

        return results

    # ---------- 内部 ----------

    @staticmethod
    def _maybe_print_group_header(
        q: ConfigQuestion, printed_groups: set[str]
    ) -> None:
        for prefix, header in GROUP_HEADERS.items():
            if q.key.startswith(prefix) and prefix not in printed_groups:
                print(f"\n{header}")
                printed_groups.add(prefix)
                break

    @staticmethod
    def _format_label(q: ConfigQuestion) -> str:
        """组装带示例值与必填/可选标注的 label 行。"""
        parts = [q.label]
        example = EXAMPLES.get(q.key)
        if example:
            parts.append(f"(如 {example})")
        if q.required:
            parts.append("[必填]")
        else:
            parts.append("[可选，回车跳过]")
        return " ".join(parts)

    def _ask(self, q: ConfigQuestion) -> str:
        print(f"\n  {self._format_label(q)}")
        if q.hint:
            print(f"  ({q.hint})")
        if q.options:
            for i, opt in enumerate(q.options, 1):
                print(f"    {i}. {opt}")
            try:
                raw = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                return ""
            if raw.isdigit() and 1 <= int(raw) <= len(q.options):
                return q.options[int(raw) - 1]
            return raw or ""

        try:
            raw = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""
        return raw

    def _ask_with_default(self, q: ConfigQuestion) -> str:
        print(f"\n  {self._format_label(q)}")
        if q.hint:
            print(f"  ({q.hint})")
        suffix = "auto" if q.source == "auto" else (q.source or "default")
        print(f"  默认: {q.default}  [{suffix}]  回车采用 / 输入新值覆盖")
        try:
            raw = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            return q.default or ""
        return raw or (q.default or "")

    def _ask_smart_suggestion(self, q: ConfigQuestion) -> str:
        """智能建议模式：default 来自自动发现/AI 分析时显示来源并询问是否采用。"""
        print(f"\n  {self._format_label(q)}")
        if q.hint:
            print(f"  ({q.hint})")
        print(f"  检测到 {q.source}: {q.default}")
        try:
            raw = input("  使用此值? [Y/n]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return q.default or ""
        # 直接回车 / y / yes  -> 接受
        if raw == "" or raw.lower() in ("y", "yes"):
            return q.default or ""
        # n / no -> 重新输入
        if raw.lower() in ("n", "no"):
            try:
                new_val = input("  请输入新值: ").strip()
            except (EOFError, KeyboardInterrupt):
                return q.default or ""
            return new_val or (q.default or "")
        # 其他输入直接作为新值
        return raw
