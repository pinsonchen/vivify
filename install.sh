#!/usr/bin/env sh
# shellcheck shell=sh
# =============================================================================
#  复元·Vivify - 一键安装脚本 (POSIX 兼容)
# -----------------------------------------------------------------------------
#  项目地址: https://github.com/pinsonchen/vivify
#  PyPI 包:  vivify-cli
#
#  用法:
#      curl -fsSL https://raw.githubusercontent.com/pinsonchen/vivify/main/install.sh | sh
#
#  作用:
#      - 自动检测操作系统 (macOS / Linux / WSL)
#      - 检测 Python >= 3.10
#      - 通过 pip 安装 vivify-cli (失败则回退到 GitHub 源码安装)
#      - 验证安装并检查外部依赖 (git / gh / qodercli / GH_TOKEN)
#
#  设计参考: rustup / nvm 的安装脚本，简洁可靠。
# =============================================================================

set -eu

# ---------- 彩色输出 ---------------------------------------------------------
if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    C_RESET="$(tput sgr0)"
    C_RED="$(tput setaf 1)"
    C_GREEN="$(tput setaf 2)"
    C_YELLOW="$(tput setaf 3)"
    C_BLUE="$(tput setaf 4)"
    C_BOLD="$(tput bold)"
else
    C_RESET=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""
fi

info()    { printf '%s[复元·Vivify]%s %s\n' "$C_BLUE" "$C_RESET" "$1"; }
ok()      { printf '%s✓%s %s\n' "$C_GREEN" "$C_RESET" "$1"; }
warn()    { printf '%s⚠%s %s\n' "$C_YELLOW" "$C_RESET" "$1"; }
err()     { printf '%s✗%s %s\n' "$C_RED" "$C_RESET" "$1" >&2; }
hr()      { printf '%s\n' "----------------------------------------------------------------"; }

banner() {
    printf '\n'
    printf '%s' "$C_BOLD"
    printf '   ╭──────────────────────────────────────────────╮\n'
    printf '   │           复元 · Vivify  Installer           │\n'
    printf '   │     让代码自我修复，让项目持续生长           │\n'
    printf '   ╰──────────────────────────────────────────────╯\n'
    printf '%s\n' "$C_RESET"
}

# ---------- 操作系统检测 -----------------------------------------------------
detect_os() {
    OS_NAME="$(uname -s 2>/dev/null || echo unknown)"
    case "$OS_NAME" in
        Darwin)
            OS_KIND="macOS"
            ;;
        Linux)
            if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
                OS_KIND="WSL"
            else
                OS_KIND="Linux"
            fi
            ;;
        MINGW*|MSYS*|CYGWIN*)
            OS_KIND="Windows (Git Bash)"
            ;;
        *)
            OS_KIND="$OS_NAME"
            ;;
    esac
    info "检测到操作系统: ${C_BOLD}${OS_KIND}${C_RESET}"
}

# ---------- Python 检测 ------------------------------------------------------
PY_CMD=""
PIP_CMD=""

check_python() {
    info "检测 Python 环境 (要求 >= 3.10)..."
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            ver="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "")"
            if [ -n "$ver" ]; then
                major="${ver%%.*}"
                minor="${ver##*.}"
                if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ] 2>/dev/null; then
                    PY_CMD="$candidate"
                    ok "找到 Python ${ver}: $(command -v "$candidate")"
                    return 0
                fi
            fi
        fi
    done

    err "未检测到符合要求的 Python (>= 3.10)"
    cat <<EOF

${C_YELLOW}请按以下方式安装 Python:${C_RESET}
  • macOS:  brew install python@3.12
  • Ubuntu: sudo apt update && sudo apt install -y python3.12 python3-pip
  • Fedora: sudo dnf install -y python3.12
  • WSL:    与 Linux 相同
  • 通用:   https://www.python.org/downloads/

安装完成后重新执行本脚本。
EOF
    exit 1
}

check_pip() {
    info "检测 pip..."
    if "$PY_CMD" -m pip --version >/dev/null 2>&1; then
        PIP_CMD="$PY_CMD -m pip"
        ok "pip 可用"
    else
        err "未找到 pip，请先执行: $PY_CMD -m ensurepip --upgrade"
        exit 1
    fi
}

# ---------- 安装 vivify-cli --------------------------------------------------
PIP_FLAGS="--upgrade --user"

install_from_pypi() {
    info "尝试从 PyPI 安装 vivify-cli..."
    # shellcheck disable=SC2086
    if $PIP_CMD install $PIP_FLAGS vivify-cli; then
        ok "已从 PyPI 安装 vivify-cli"
        return 0
    fi
    return 1
}

install_from_github() {
    warn "PyPI 安装失败，回退到 GitHub 源码安装..."
    # shellcheck disable=SC2086
    if $PIP_CMD install $PIP_FLAGS "git+https://github.com/pinsonchen/vivify.git"; then
        ok "已从 GitHub 源码安装 vivify-cli"
        return 0
    fi
    err "GitHub 源码安装也失败了，请检查网络或手动安装"
    exit 1
}

# ---------- 验证安装 --------------------------------------------------------
verify_install() {
    info "验证安装..."
    if command -v vivify >/dev/null 2>&1; then
        ver_out="$(vivify --version 2>&1 || true)"
        ok "vivify 已就绪: ${ver_out}"
    else
        warn "已安装 vivify-cli，但 ${C_BOLD}vivify${C_RESET} 命令不在 PATH 中"
        user_base="$("$PY_CMD" -m site --user-base 2>/dev/null || echo "")"
        if [ -n "$user_base" ]; then
            warn "请将以下路径加入 PATH 后重启终端:"
            printf '    %s/bin\n' "$user_base"
        fi
    fi
}

# ---------- 外部依赖检测 ----------------------------------------------------
check_dep() {
    name="$1"
    label="$2"
    required="$3"  # required | recommended
    if command -v "$name" >/dev/null 2>&1; then
        ok "${label} 已安装: $(command -v "$name")"
    else
        if [ "$required" = "required" ]; then
            err "${label} 未安装 (必需，但不阻塞本次安装)"
        else
            warn "${label} 未安装 (推荐)"
        fi
    fi
}

check_external_deps() {
    hr
    info "检查外部依赖 (不会阻塞安装)..."
    check_dep git      "git"                    required
    check_dep gh       "gh (GitHub CLI)"        required
    check_dep qodercli "qodercli"               required

    if [ -n "${GH_TOKEN:-}" ]; then
        ok "GH_TOKEN 环境变量已设置"
    else
        warn "GH_TOKEN 环境变量未设置 (推荐设置以启用 PR 自动化)"
        printf '    示例: export GH_TOKEN=ghp_xxxxxxxxxxxxxxxx\n'
    fi
}

# ---------- 收尾 ------------------------------------------------------------
finish() {
    hr
    printf '%s🎉 复元·Vivify 安装完成！%s\n' "$C_GREEN$C_BOLD" "$C_RESET"
    printf '\n下一步:\n'
    printf '  • 查看帮助:    %svivify --help%s\n'   "$C_BOLD" "$C_RESET"
    printf '  • 初始化项目: %svivify init%s\n'      "$C_BOLD" "$C_RESET"
    printf '  • 项目主页:    https://github.com/pinsonchen/vivify\n\n'
}

# ---------- 主流程 ----------------------------------------------------------
main() {
    banner
    detect_os
    check_python
    check_pip

    if ! install_from_pypi; then
        install_from_github
    fi

    verify_install
    check_external_deps
    finish
}

main "$@"
