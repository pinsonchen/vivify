"""交互式配置问答。"""

from __future__ import annotations

from .configurator import ConfigQuestion


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
        for q in questions:
            if q.default is not None:
                if non_interactive:
                    # 非交互模式下直接采用 default
                    results[q.key] = q.default
                else:
                    # 交互模式下也允许用户确认/修改 default
                    results[q.key] = self._ask_with_default(q)
                continue

            if non_interactive:
                results[q.key] = ""
                continue

            results[q.key] = self._ask(q)
        return results

    # ---------- 内部 ----------

    def _ask(self, q: ConfigQuestion) -> str:
        print(f"\n  {q.label}")
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
        print(f"\n  {q.label}")
        if q.hint:
            print(f"  ({q.hint})")
        suffix = "auto" if q.source == "auto" else (q.source or "default")
        print(f"  默认: {q.default}  [{suffix}]  回车采用 / 输入新值覆盖")
        try:
            raw = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            return q.default or ""
        return raw or (q.default or "")
