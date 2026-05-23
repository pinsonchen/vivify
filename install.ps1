# =============================================================================
#  复元·Vivify - Windows PowerShell 一键安装脚本
# -----------------------------------------------------------------------------
#  项目地址: https://github.com/pinsonchen/vivify
#  PyPI 包:  vivify-cli
#
#  用法:
#      irm https://raw.githubusercontent.com/pinsonchen/vivify/main/install.ps1 | iex
#
#  作用:
#      - 检测 Python >= 3.10 (尝试 python / py -3 / python3)
#      - 通过 pip 安装 vivify-cli (失败则回退到 GitHub 源码安装)
#      - 验证安装并检查外部依赖 (git / gh / qodercli / GH_TOKEN)
# =============================================================================

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# ---------- 彩色输出 ---------------------------------------------------------
function Write-Info  { param([string]$Msg) Write-Host "[复元·Vivify] $Msg" -ForegroundColor Cyan }
function Write-Ok    { param([string]$Msg) Write-Host "✓ $Msg"           -ForegroundColor Green }
function Write-Warn2 { param([string]$Msg) Write-Host "⚠ $Msg"           -ForegroundColor Yellow }
function Write-Err   { param([string]$Msg) Write-Host "✗ $Msg"           -ForegroundColor Red }
function Write-Hr    { Write-Host ('-' * 64) }

function Show-Banner {
    Write-Host ''
    Write-Host '   ╭──────────────────────────────────────────────╮' -ForegroundColor White
    Write-Host '   │           复元 · Vivify  Installer           │' -ForegroundColor White
    Write-Host '   │     让代码自我修复，让项目持续生长           │' -ForegroundColor White
    Write-Host '   ╰──────────────────────────────────────────────╯' -ForegroundColor White
    Write-Host ''
}

# ---------- Python 检测 ------------------------------------------------------
$script:PyCmd = $null

function Test-PythonCandidate {
    param([string]$Cmd, [string[]]$ExtraArgs = @())
    try {
        $argList = $ExtraArgs + @('-c', 'import sys; print("%d.%d" % sys.version_info[:2])')
        $ver = & $Cmd @argList 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $ver) { return $null }
        $parts = $ver.Trim().Split('.')
        if ($parts.Count -lt 2) { return $null }
        $major = [int]$parts[0]; $minor = [int]$parts[1]
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
            return [pscustomobject]@{ Version = $ver.Trim(); Major = $major; Minor = $minor }
        }
        return $null
    } catch {
        return $null
    }
}

function Find-Python {
    Write-Info '检测 Python 环境 (要求 >= 3.10)...'

    $candidates = @(
        @{ Cmd = 'python';  Args = @() },
        @{ Cmd = 'python3'; Args = @() },
        @{ Cmd = 'py';      Args = @('-3') }
    )

    foreach ($c in $candidates) {
        if (Get-Command $c.Cmd -ErrorAction SilentlyContinue) {
            $result = Test-PythonCandidate -Cmd $c.Cmd -ExtraArgs $c.Args
            if ($result) {
                $script:PyCmd  = $c.Cmd
                $script:PyArgs = $c.Args
                Write-Ok "找到 Python $($result.Version): $((Get-Command $c.Cmd).Source)"
                return
            }
        }
    }

    Write-Err '未检测到符合要求的 Python (>= 3.10)'
    Write-Host ''
    Write-Host '请按以下方式安装 Python:' -ForegroundColor Yellow
    Write-Host '  • 官方下载: https://www.python.org/downloads/'
    Write-Host '  • Winget:   winget install -e --id Python.Python.3.12'
    Write-Host '  • Choco:    choco install python --version=3.12.0'
    Write-Host ''
    Write-Host '安装完成后请重新打开 PowerShell 并执行本脚本。'
    exit 1
}

function Invoke-Py {
    param([Parameter(ValueFromRemainingArguments = $true)] [string[]]$RestArgs)
    $all = @()
    if ($script:PyArgs) { $all += $script:PyArgs }
    $all += $RestArgs
    & $script:PyCmd @all
}

function Test-Pip {
    Write-Info '检测 pip...'
    Invoke-Py '-m' 'pip' '--version' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "pip 不可用，请先执行: $script:PyCmd -m ensurepip --upgrade"
        exit 1
    }
    Write-Ok 'pip 可用'
}

# ---------- 安装 vivify-cli --------------------------------------------------
function Install-FromPyPI {
    Write-Info '尝试从 PyPI 安装 vivify-cli...'
    Invoke-Py '-m' 'pip' 'install' '--upgrade' '--user' 'vivify-cli'
    if ($LASTEXITCODE -eq 0) {
        Write-Ok '已从 PyPI 安装 vivify-cli'
        return $true
    }
    return $false
}

function Install-FromGitHub {
    Write-Warn2 'PyPI 安装失败，回退到 GitHub 源码安装...'
    Invoke-Py '-m' 'pip' 'install' '--upgrade' '--user' 'git+https://github.com/pinsonchen/vivify.git'
    if ($LASTEXITCODE -eq 0) {
        Write-Ok '已从 GitHub 源码安装 vivify-cli'
        return $true
    }
    Write-Err 'GitHub 源码安装也失败了，请检查网络或手动安装'
    exit 1
}

# ---------- 验证安装 --------------------------------------------------------
function Test-Install {
    Write-Info '验证安装...'
    $cmd = Get-Command vivify -ErrorAction SilentlyContinue
    if ($cmd) {
        $verOut = & vivify --version 2>&1
        Write-Ok "vivify 已就绪: $verOut"
    } else {
        Write-Warn2 '已安装 vivify-cli，但 vivify 命令不在 PATH 中'
        $userBase = Invoke-Py '-m' 'site' '--user-base' 2>$null
        if ($userBase) {
            Write-Warn2 '请将以下目录加入 PATH 后重启 PowerShell:'
            Write-Host  ("    {0}\Scripts" -f $userBase.Trim())
        }
    }
}

# ---------- 外部依赖检测 ----------------------------------------------------
function Test-Dep {
    param(
        [string]$Name,
        [string]$Label,
        [ValidateSet('required', 'recommended')] [string]$Level
    )
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) {
        Write-Ok "$Label 已安装: $($cmd.Source)"
    } else {
        if ($Level -eq 'required') {
            Write-Err "$Label 未安装 (必需，但不阻塞本次安装)"
        } else {
            Write-Warn2 "$Label 未安装 (推荐)"
        }
    }
}

function Test-ExternalDeps {
    Write-Hr
    Write-Info '检查外部依赖 (不会阻塞安装)...'
    Test-Dep -Name 'git'      -Label 'git'             -Level required
    Test-Dep -Name 'gh'       -Label 'gh (GitHub CLI)' -Level required
    Test-Dep -Name 'qodercli' -Label 'qodercli'        -Level required

    if ($env:GH_TOKEN) {
        Write-Ok 'GH_TOKEN 环境变量已设置'
    } else {
        Write-Warn2 'GH_TOKEN 环境变量未设置 (推荐设置以启用 PR 自动化)'
        Write-Host '    示例: $env:GH_TOKEN = "ghp_xxxxxxxxxxxxxxxx"'
    }
}

# ---------- 收尾 ------------------------------------------------------------
function Show-Finish {
    Write-Hr
    Write-Host '🎉 复元·Vivify 安装完成！' -ForegroundColor Green
    Write-Host ''
    Write-Host '下一步:'
    Write-Host '  • 查看帮助:    vivify --help'
    Write-Host '  • 初始化项目: vivify init'
    Write-Host '  • 项目主页:    https://github.com/pinsonchen/vivify'
    Write-Host ''
}

# ---------- 主流程 ----------------------------------------------------------
function Invoke-Main {
    Show-Banner
    Write-Info "检测到操作系统: Windows ($([System.Environment]::OSVersion.VersionString))"
    Find-Python
    Test-Pip

    if (-not (Install-FromPyPI)) {
        Install-FromGitHub | Out-Null
    }

    Test-Install
    Test-ExternalDeps
    Show-Finish
}

Invoke-Main
