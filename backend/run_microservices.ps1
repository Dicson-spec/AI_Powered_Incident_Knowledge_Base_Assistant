$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

$services = @(
  @{ Name = "gateway"; Module = "backend.app.main:app"; Port = 8000 },
  @{ Name = "resolution"; Module = "backend.app.resolution_service:app"; Port = 8001 },
  @{ Name = "triage"; Module = "backend.app.triage_service:app"; Port = 8002 },
  @{ Name = "routing"; Module = "backend.app.routing_service:app"; Port = 8003 }
)

foreach ($service in $services) {
  $command = "& '$python' -m uvicorn $($service.Module) --reload --port $($service.Port)"
  Start-Process powershell -ArgumentList "-NoExit", "-Command", $command -WorkingDirectory $root
}
