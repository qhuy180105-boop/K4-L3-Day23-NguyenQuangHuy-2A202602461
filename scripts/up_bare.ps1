# Start 2 regions + edge proxy on Windows
New-Item -ItemType Directory -Force -Path run, reports | Out-Null

function Start-ServiceProcess ($name, $port, $envVars, $url) {
    # Check if already listening
    $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Where-Object { $_.State -eq 'Listen' }
    if ($conn) {
        $conn.OwningProcess | Set-Content -Path "run/$name.pid" -Encoding ascii
        Write-Host "$name already listening on port $port with PID $($conn.OwningProcess)"
        return
    }

    # Set env vars
    foreach ($k in $envVars.Keys) {
        [System.Environment]::SetEnvironmentVariable($k, $envVars[$k], "Process")
    }

    $mod = if ($name -like "region*") { "serving.app:app" } else { "edge.proxy:app" }
    $p = Start-Process -FilePath "python.exe" -ArgumentList "-m uvicorn $mod --host 127.0.0.1 --port $port --log-level warning" -PassThru -WindowStyle Hidden
    $p.Id | Set-Content -Path "run/$name.pid" -Encoding ascii
    Write-Host "$name started on port $port with PID $($p.Id)"
}

Start-ServiceProcess "region-a" 8001 @{REGION="a"; STATE_DIR="state/region-a"; WARMUP_SECONDS="6"} "http://127.0.0.1:8001/healthz"
Start-ServiceProcess "region-b" 8002 @{REGION="b"; STATE_DIR="state/region-b"; WARMUP_SECONDS="6"} "http://127.0.0.1:8002/healthz"
Start-ServiceProcess "edge" 8080 @{EDGE_TTL_SECONDS="5"} "http://127.0.0.1:8080/edge/state"

Write-Host "Cho service len (toi da 10s)..."
Start-Sleep -Seconds 2

$services = @(
    @{name="region-a"; url="http://127.0.0.1:8001/healthz"},
    @{name="region-b"; url="http://127.0.0.1:8002/healthz"},
    @{name="edge"; url="http://127.0.0.1:8080/edge/state"}
)

foreach ($s in $services) {
    $up = $false
    for ($i = 0; $i -lt 10; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri $s.url -TimeoutSec 1 -UseBasicParsing -ErrorAction SilentlyContinue
            if ($resp.StatusCode -eq 200) { $up = $true; break }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if ($up) {
        Write-Host "  $($s.name): UP"
    } else {
        Write-Host "  $($s.name): KHONG PHAN HOI"
    }
}
