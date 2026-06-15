<#  DAM Platform — developer helper (Windows / PowerShell)

    .\scripts\dev.ps1 infra-up       # start docker infra
    .\scripts\dev.ps1 infra-down     # stop infra (keep data)
    .\scripts\dev.ps1 infra-reset    # stop + wipe all volumes
    .\scripts\dev.ps1 api            # run the API (port 8000)
    .\scripts\dev.ps1 worker         # run the ingestion consumer (arq)
    .\scripts\dev.ps1 inference      # run the model-serving endpoints (port 8100)
    .\scripts\dev.ps1 web            # run the Next.js dev server (port 3000)
    .\scripts\dev.ps1 ps             # infra status
    .\scripts\dev.ps1 schema-reset   # drop + recreate the Postgres schema
#>
param([Parameter(Mandatory=$true)][string]$cmd)

$root = Split-Path $PSScriptRoot -Parent
$compose = Join-Path $root "docker-compose.yml"

switch ($cmd) {
  "infra-up"    { docker compose -f $compose --project-directory $root up -d }
  "infra-down"  { docker compose -f $compose --project-directory $root down }
  "infra-reset" { docker compose -f $compose --project-directory $root down -v }
  "ps"          { docker compose -f $compose --project-directory $root ps }
  "api"         { Set-Location (Join-Path $root "apps\api");           & ".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8000 }
  "inference"   { Set-Location (Join-Path $root "services\ai-worker"); & ".venv\Scripts\python.exe" -m uvicorn worker.server:app --port 8100 }
  "worker"      { Set-Location (Join-Path $root "services\ai-worker"); & ".venv\Scripts\arq.exe" worker.main.WorkerSettings }
  "web"         { Set-Location (Join-Path $root "apps\web");           npm run dev }
  "schema-reset" {
      docker exec dam-platform-postgres-1 psql -U dam -d dam -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
      docker exec dam-platform-postgres-1 psql -U dam -d dam -f /docker-entrypoint-initdb.d/00-init.sql
  }
  default { Write-Output "unknown command: $cmd" }
}
