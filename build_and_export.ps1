param(
    [string]$Platform = "linux/arm64"
)

$ErrorActionPreference = "Stop"
$ImageName = "iam-service"
$ImageTag = "latest"
$TarFile = "$ImageName.tar"
$OutputFile = "$ImageName.tar.gz"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ">>> [1/3] 构建 Python 3.12 镜像 (架构: $Platform): ${ImageName}:${ImageTag} ..." -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 优先使用 buildx 进行跨架构编译并加载至本地镜像库
$buildSuccess = $false
try {
    docker buildx build --platform $Platform -t "${ImageName}:${ImageTag}" --load .
    $buildSuccess = $true
} catch {
    Write-Host "docker buildx 执行失败或未配置，尝试常规 docker build --platform ..." -ForegroundColor Yellow
}

if (-not $buildSuccess) {
    docker build --platform $Platform -t "${ImageName}:${ImageTag}" .
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ">>> [2/3] 导出并压缩镜像至 $OutputFile ..." -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
docker save -o $TarFile "${ImageName}:${ImageTag}"

# 利用 Windows 原生自带的 tar.exe 压缩为 .tar.gz
if (Get-Command tar -ErrorAction SilentlyContinue) {
    tar -czf $OutputFile $TarFile
    Remove-Item $TarFile -Force
} else {
    python -c "import gzip, shutil; f_in = open('$TarFile', 'rb'); f_out = gzip.open('$OutputFile', 'wb'); shutil.copyfileobj(f_in, f_out); f_in.close(); f_out.close()"
    Remove-Item $TarFile -Force
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host ">>> [3/3] 打包完成: $(Resolve-Path $OutputFile)" -ForegroundColor Green
Write-Host "目标架构: $Platform" -ForegroundColor Green
Write-Host "服务器 (ARM64) 导入与启动命令:" -ForegroundColor Green
Write-Host "  docker load -i $OutputFile" -ForegroundColor Yellow
Write-Host "  docker compose up -d iam-service" -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Green
