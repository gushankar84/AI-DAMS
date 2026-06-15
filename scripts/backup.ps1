<#  DAM Platform — backup (P6 resilience).

    .\scripts\backup.ps1            # back up to .data\backups\<timestamp>\

Backs up the SOURCE OF TRUTH (Postgres metadata + MinIO binaries) plus optional
Qdrant snapshots. OpenSearch + Qdrant are DERIVED indexes: if lost, rebuild them
by re-ingesting assets (POST /api/assets/{id}/reprocess) — ingestion is idempotent
(clear_derived), so a full rebuild is safe. See docs/SCALE_AND_DR.md.
#>
param([string]$OutRoot = "E:\dam-platform\.data\backups")

$ErrorActionPreference = "Stop"
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $OutRoot $ts
New-Item -ItemType Directory -Force -Path $out | Out-Null
Write-Output "backup -> $out"

# 1. Postgres (system of record) — custom-format dump
docker exec dam-platform-postgres-1 pg_dump -U dam -d dam -Fc -f /tmp/dam.dump
docker cp dam-platform-postgres-1:/tmp/dam.dump "$out\postgres.dump"
docker exec dam-platform-postgres-1 rm -f /tmp/dam.dump
Write-Output "  [ok] postgres -> postgres.dump"

# 2. Qdrant snapshots (faster recovery than rebuild) — best-effort
foreach ($col in @("dam_text", "dam_image", "dam_face")) {
  try {
    Invoke-RestMethod -Method Post "http://localhost:6333/collections/$col/snapshots" -TimeoutSec 120 | Out-Null
    Write-Output "  [ok] qdrant snapshot: $col (in qdrant volume /qdrant/snapshots)"
  } catch { Write-Output "  [skip] qdrant snapshot $col : $($_.Exception.Message)" }
}

# 3. MinIO binaries -> host dir, via a one-shot mc container on the compose network.
# The mc image's entrypoint is `mc`, so override it with /bin/sh to run a script.
docker run --rm --network dam-platform_default --entrypoint /bin/sh `
  -v "${out}:/backup" minio/mc:RELEASE.2024-10-08T09-37-26Z `
  -c "mc alias set s3 http://minio:9000 minioadmin minioadmin && mc mirror --overwrite s3/dam-assets /backup/minio" | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Output "  [ok] minio bucket mirrored" }
else { Write-Output "  [warn] minio mirror failed (exit $LASTEXITCODE); binaries can be re-uploaded" }

Write-Output "backup complete: $out"
