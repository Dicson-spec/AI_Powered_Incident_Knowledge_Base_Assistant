$ErrorActionPreference = "Stop"

function Invoke-LatencyCheck {
    param(
        [string]$Name,
        [string]$Url,
        [string]$Body,
        [int]$Iterations = 10,
        [int]$TimeoutSec = 120,
        [int]$DelayMs = 0
    )

    $durations = @()
    $failures = @()
    for ($i = 1; $i -le $Iterations; $i++) {
        try {
            $elapsed = (Measure-Command {
                Invoke-RestMethod $Url `
                    -Method Post `
                    -ContentType "application/json" `
                    -Body $Body `
                    -TimeoutSec $TimeoutSec | Out-Null
            }).TotalMilliseconds

            $durations += $elapsed
            Write-Host ("{0} run {1}/{2}: {3:N0} ms" -f $Name, $i, $Iterations, $elapsed)
        } catch {
            $failures += $_
            Write-Host ("{0} run {1}/{2}: FAILED ({3})" -f $Name, $i, $Iterations, $_.Exception.Message)
        }

        if ($DelayMs -gt 0) {
            Start-Sleep -Milliseconds $DelayMs
        }
    }

    if ($durations.Count -gt 0) {
        $sorted = $durations | Sort-Object
        $count = $sorted.Count
        $avg = ($durations | Measure-Object -Average).Average
        $p50Index = [Math]::Floor(0.50 * ($count - 1))
        $p95Index = [Math]::Floor(0.95 * ($count - 1))
        $p50 = $sorted[$p50Index]
        $p95 = $sorted[$p95Index]
        $min = $sorted[0]
        $max = $sorted[$count - 1]

        Write-Host ("{0} summary: avg={1:N0} ms | p50={2:N0} ms | p95={3:N0} ms | min={4:N0} ms | max={5:N0} ms | failures={6}" -f $Name, $avg, $p50, $p95, $min, $max, $failures.Count)
    } else {
        Write-Host ("{0} summary: no successful responses | failures={1}" -f $Name, $failures.Count)
    }
    Write-Host ""
}

function Get-Json {
    param(
        [string]$Url,
        [int]$TimeoutSec = 30
    )
    try {
        return Invoke-RestMethod $Url -Method Get -TimeoutSec $TimeoutSec
    } catch {
        return $null
    }
}

$iterations = if ($env:LATENCY_RUNS) { [int]$env:LATENCY_RUNS } else { 10 }
$timeoutSec = if ($env:LATENCY_TIMEOUT_SEC) { [int]$env:LATENCY_TIMEOUT_SEC } else { 120 }
$delayMs = if ($env:LATENCY_DELAY_MS) { [int]$env:LATENCY_DELAY_MS } else { 0 }

Invoke-LatencyCheck `
    -Name "gateway-resolution" `
    -Url "http://127.0.0.1:8000/api/resolution" `
    -Body '{"query":"App is slow and hangs over time","top_k":4}' `
    -Iterations $iterations `
    -TimeoutSec $timeoutSec `
    -DelayMs $delayMs

Invoke-LatencyCheck `
    -Name "resolution-service" `
    -Url "http://127.0.0.1:8001/resolution" `
    -Body '{"query":"App is slow and hangs over time","top_k":4}' `
    -Iterations $iterations `
    -TimeoutSec $timeoutSec `
    -DelayMs $delayMs

$triageFilters = Get-Json -Url "http://127.0.0.1:8002/filters" -TimeoutSec 10
$triageCategory = if ($triageFilters -and $triageFilters.category -and $triageFilters.category.Count -gt 0) { $triageFilters.category[0] } else { "incident" }
$triageCiCategory = if ($triageFilters -and $triageFilters.ci_category -and $triageFilters.ci_category.Count -gt 0) { $triageFilters.ci_category[0] } else { "application" }
$triageCiSubcategory = if ($triageFilters -and $triageFilters.ci_subcategory -and $triageFilters.ci_subcategory.Count -gt 0) { $triageFilters.ci_subcategory[0] } else { "Web Based Application" }
$triageBody = (@{
    ticket_summary = "Customer-facing app is intermittently unavailable."
    category = $triageCategory
    ci_category = $triageCiCategory
    ci_subcategory = $triageCiSubcategory
    top_k = 5
} | ConvertTo-Json -Compress)

Invoke-LatencyCheck `
    -Name "triage-service" `
    -Url "http://127.0.0.1:8002/triage" `
    -Body $triageBody `
    -Iterations $iterations `
    -TimeoutSec $timeoutSec `
    -DelayMs $delayMs

$routingFilters = Get-Json -Url "http://127.0.0.1:8003/filters" -TimeoutSec 10
$routingCategory = if ($routingFilters -and $routingFilters.category -and $routingFilters.category.Count -gt 0) { $routingFilters.category[0] } else { "Category 55" }
$routingSubcategory = if ($routingFilters -and $routingFilters.subcategory -and $routingFilters.subcategory.Count -gt 0) { $routingFilters.subcategory[0] } else { "Subcategory 170" }
$routingSymptom = if ($routingFilters -and $routingFilters.u_symptom -and $routingFilters.u_symptom.Count -gt 0) { $routingFilters.u_symptom[0] } else { "Symptom 72" }
$routingImpact = if ($routingFilters -and $routingFilters.impact -and $routingFilters.impact.Count -gt 0) { $routingFilters.impact[0] } else { "2" }
$routingUrgency = if ($routingFilters -and $routingFilters.urgency -and $routingFilters.urgency.Count -gt 0) { $routingFilters.urgency[0] } else { "2" }
$routingContact = if ($routingFilters -and $routingFilters.contact_type -and $routingFilters.contact_type.Count -gt 0) { $routingFilters.contact_type[0] } else { "Phone" }
$routingLocation = if ($routingFilters -and $routingFilters.location -and $routingFilters.location.Count -gt 0) { $routingFilters.location[0] } else { "Location 143" }
$routingBody = (@{
    description = "Optional routing context"
    category = $routingCategory
    subcategory = $routingSubcategory
    u_symptom = $routingSymptom
    impact = $routingImpact
    urgency = $routingUrgency
    contact_type = $routingContact
    location = $routingLocation
    top_k = 5
} | ConvertTo-Json -Compress)

Invoke-LatencyCheck `
    -Name "routing-service" `
    -Url "http://127.0.0.1:8003/routing" `
    -Body $routingBody `
    -Iterations $iterations `
    -TimeoutSec $timeoutSec `
    -DelayMs $delayMs
