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

$env:DOCKER_BUILDKIT = "1"

# 优先使用本地 docker builder（支持 arm64 与 BuildKit 缓存且兼容中文目录）
$buildSuccess = $false
docker buildx build --builder desktop-linux --platform $Platform -t "${ImageName}:${ImageTag}" --load .
if ($LASTEXITCODE -eq 0) {
    $buildSuccess = $true
} else {
    Write-Host "desktop-linux builder 未就绪，尝试默认 buildx / docker build ..." -ForegroundColor Yellow
    docker buildx build --platform $Platform -t "${ImageName}:${ImageTag}" --load .
    if ($LASTEXITCODE -eq 0) {
        $buildSuccess = $true
    }
}

if (-not $buildSuccess) {
    docker build --platform $Platform -t "${ImageName}:${ImageTag}" .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
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
