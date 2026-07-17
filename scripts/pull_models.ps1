# PowerShell script to seed/pull Ollama models inside docker containers with a distributed mapping.
# Usage: .\pull_models.ps1

$ModelMapping = @{
    "ollama-1" = "gemma2:2b"
    "ollama-2" = "gemma2:2b"
    "ollama-3" = "tinyllama:latest"
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Ollama Model Downloader/Seeder" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

foreach ($container in $ModelMapping.Keys) {
    $model = $ModelMapping[$container]
    # Check if the container is running
    $state = docker inspect -f '{{.State.Running}}' $container 2>$null
    if ($state -ne "true") {
        Write-Host "[WARNING] Container $container is not running. Skipping." -ForegroundColor Yellow
        continue
    }

    Write-Host "Pulling '$model' on '$container' (this may take a few minutes)..." -ForegroundColor White
    docker exec $container ollama pull $model
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[SUCCESS] Successfully pulled '$model' on '$container'." -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Failed to pull '$model' on '$container'." -ForegroundColor Red
    }
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Model pull process completed." -ForegroundColor Green

