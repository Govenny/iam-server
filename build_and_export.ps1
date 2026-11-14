$ErrorActionPreference = "Stop"
$ImageName = "iam-service"
$ImageTag = "latest"
$TarFile = "$ImageName.tar"
$OutputFile = "$ImageName.tar.gz"

# 解决包含中文路径时 BuildKit gRPC 凭证编码问题
$env:DOCKER_BUILDKIT = "0"
$env:COMPOSE_DOCKER_CLI_BUILD = "0"

Write-Host ">>> [1/3] 构建 Python 3.12 镜像: ${ImageName}:${ImageTag} ..." -ForegroundColor Cyan
docker build -t "${ImageName}:${ImageTag}" .

Write-Host ">>> [2/3] 导出并压缩镜像至 $OutputFile ..." -ForegroundColor Cyan
docker save -o $TarFile "${ImageName}:${ImageTag}"

# 利用 Windows 原生自带的 tar.exe 压缩为 .tar.gz
if (Get-Command tar -ErrorAction SilentlyContinue) {
    tar -czf $OutputFile $TarFile
    Remove-Item $TarFile -Force
} else {
    python -c "import gzip, shutil; f_in = open('$TarFile', 'rb'); f_out = gzip.open('$OutputFile', 'wb'); shutil.copyfileobj(f_in, f_out); f_in.close(); f_out.close()"
    Remove-Item $TarFile -Force
}

Write-Host ">>> [3/3] 打包完成: $(Resolve-Path $OutputFile)" -ForegroundColor Green
