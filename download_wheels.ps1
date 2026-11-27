param(
    [ValidateSet("arm64", "amd64", "all")]
    [string]$Arch = "arm64",
    [string]$Mirror = "https://mirrors.aliyun.com/pypi/simple/",
    [string]$FallbackMirror = "https://pypi.tuna.tsinghua.edu.cn/simple"
)

$ErrorActionPreference = "Stop"

function Download-ArchWheels {
    param([string]$TargetArch)

    $OutDir = Join-Path $PSScriptRoot "wheels\$TargetArch"
    if (-not (Test-Path $OutDir)) {
        New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    }

    if ($TargetArch -eq "arm64") {
        $Platforms = @(
            "manylinux_2_34_aarch64",
            "manylinux_2_28_aarch64",
            "manylinux_2_17_aarch64",
            "manylinux2014_aarch64",
            "linux_aarch64"
        )
    } else {
        $Platforms = @(
            "manylinux_2_34_x86_64",
            "manylinux_2_28_x86_64",
            "manylinux_2_17_x86_64",
            "manylinux2014_x86_64",
            "linux_x86_64"
        )
    }

    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host ">>> 开始下载 Python 3.12 (linux/$TargetArch) 离线依赖包到: $OutDir" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan

    $ArgsList = @(
        "-m", "pip", "download",
        "-r", (Join-Path $PSScriptRoot "requirements.txt"),
        "uvloop>=0.15.1",
        "-d", $OutDir,
        "--python-version", "3.12",
        "--implementation", "cp",
        "--abi", "cp312",
        "--only-binary=:all:",
        "-i", $Mirror,
        "--extra-index-url", $FallbackMirror
    )
    foreach ($p in $Platforms) {
        $ArgsList += @("--platform", $p)
    }

    & python @ArgsList
    if ($LASTEXITCODE -ne 0) {
        throw "下载 linux/$TargetArch 依赖包失败 (退出码: $LASTEXITCODE)"
    }

    $Count = (Get-ChildItem -Path $OutDir -Filter "*.whl").Count
    Write-Host ">>> [完成] linux/$TargetArch 共就绪 $Count 个离线 .whl 包 ($OutDir)" -ForegroundColor Green
}

if ($Arch -eq "all") {
    Download-ArchWheels -TargetArch "arm64"
    Download-ArchWheels -TargetArch "amd64"
} else {
    Download-ArchWheels -TargetArch $Arch
}

Write-Host ""
Write-Host "现在可以直接运行 .\build_and_export.ps1，Docker 将自动使用本地离线包，无需联网下载！" -ForegroundColor Yellow
