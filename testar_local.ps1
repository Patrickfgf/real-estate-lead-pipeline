<#
.SYNOPSIS
  Teste manual ponta a ponta do Projeto Jaré.

.DESCRIPTION
  Atalho do passo a passo do README. O script:
    1. Sobe o FastAPI (uvicorn, sem reload) em background.
    2. Espera o /health responder.
    3. Faz POST do lead de exemplo no webhook do Wimoveis  -> testa ingestao + carga no Trello.
    4. Reenvia o mesmo lead                                -> testa a deduplicacao.
    5. Derruba o servidor e consulta o DuckDB              -> confirma gravacao e vinculo do card.

  ATENCAO: com as credenciais do Trello preenchidas no .env, o lead de teste cria
  um card REAL no quadro. A carga e idempotente (reenviar nao duplica), mas o
  primeiro envio aparece la de verdade. Para testar sem tocar no Trello, esvazie
  temporariamente as variaveis TRELLO_* no .env.

.EXAMPLE
  .\testar_local.ps1
#>
[CmdletBinding()]
param(
    [string]$ServerHost = "127.0.0.1",
    [int]$Port          = 8000,
    [string]$Sample     = "samples\wimoveis_lead.json",
    [int]$TimeoutSec    = 30
)

$ErrorActionPreference = "Stop"
$base = "http://${ServerHost}:${Port}"

# Usa o python da venv do projeto; cai para o python do PATH se a venv nao existir.
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$samplePath = Join-Path $PSScriptRoot $Sample
if (-not (Test-Path $samplePath)) { throw "Lead de exemplo nao encontrado: $samplePath" }

# Contador de falhas + helper de verificacao (PASS/FAIL colorido).
$script:failures = 0
function Check([string]$label, [bool]$cond) {
    if ($cond) { Write-Host "  [OK]  $label" -ForegroundColor Green }
    else       { Write-Host "  [XX]  $label" -ForegroundColor Red; $script:failures++ }
}

Write-Host ""
Write-Host "=== Teste local do Projeto Jare ===" -ForegroundColor Cyan
Write-Host "Cria um card de teste (idEvento unico) no Trello e o APAGA no fim (passo 4)." -ForegroundColor Yellow
Write-Host ""

$server   = $null   # processo que ESTE script subiu (so derrubamos esse)
$outLog   = Join-Path $env:TEMP "jare_uvicorn_out.log"
$errLog   = Join-Path $env:TEMP "jare_uvicorn_err.log"

try {
    Push-Location $PSScriptRoot   # garante CWD = raiz (import de src.* e paths relativos)

    # Se ja houver um servidor respondendo na porta, reaproveita (nao derruba o do usuario).
    $alreadyUp = $false
    try { (Invoke-RestMethod "$base/health" -TimeoutSec 2).status | Out-Null; $alreadyUp = $true } catch { }

    if ($alreadyUp) {
        Write-Host "Servidor ja esta no ar em $base — reaproveitando." -ForegroundColor DarkGray
    }
    else {
        Write-Host "Subindo servidor em $base ..."
        $server = Start-Process -FilePath $py `
            -ArgumentList @('-m','uvicorn','src.main:app','--host',$ServerHost,'--port',"$Port") `
            -WorkingDirectory $PSScriptRoot -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog

        # Espera o /health ficar verde (poll ate o timeout).
        $deadline = (Get-Date).AddSeconds($TimeoutSec)
        $healthy  = $false
        while ((Get-Date) -lt $deadline) {
            try {
                if ((Invoke-RestMethod "$base/health" -TimeoutSec 2).status -eq "ok") { $healthy = $true; break }
            } catch { }
            Start-Sleep -Milliseconds 500
        }
        if (-not $healthy) {
            Write-Host "Servidor nao respondeu em $TimeoutSec s. Ultimas linhas do log:" -ForegroundColor Red
            if (Test-Path $errLog) { Get-Content $errLog -Tail 20 }
            throw "Falha ao subir o servidor."
        }
        Write-Host "Servidor no ar." -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "1) Enviando lead de exemplo (ingestao + Trello)..."
    # idEvento unico por execucao: torna o teste repetivel (com id fixo, o dedup
    # da 1a rodada faria a 2a falhar) e isola este lead para a limpeza no passo 4.
    $stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
    $payload = Get-Content $samplePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $payload.idEvento = "evt-teste-$stamp"
    $body = $payload | ConvertTo-Json -Depth 20
    $r1 = Invoke-RestMethod -Method Post -Uri "$base/webhook/wimoveis" `
        -ContentType "application/json" -Body $body
    Check "webhook respondeu 'received'"        ($r1.status -eq "received")
    Check "primeiro envio NAO e duplicado"       (-not $r1.duplicate)
    $ext = $r1.external_id
    Write-Host "     external_id: $ext" -ForegroundColor DarkGray

    Write-Host ""
    Write-Host "2) Reenviando o mesmo lead (dedup)..."
    $r2 = Invoke-RestMethod -Method Post -Uri "$base/webhook/wimoveis" `
        -ContentType "application/json" -Body $body
    Check "reenvio e marcado como duplicado"     ([bool]$r2.duplicate)
    Check "mesmo external_id nos dois envios"     ($r2.external_id -eq $ext)

    # Derruba o NOSSO servidor antes de ler o DuckDB (o arquivo fica travado enquanto o server roda).
    if ($server) {
        Write-Host ""
        Write-Host "Derrubando servidor..."
        Stop-Process -Id $server.Id -Force
        try { Wait-Process -Id $server.Id -Timeout 10 } catch { }
        $server = $null
        Start-Sleep -Milliseconds 800   # folga para o lock do DuckDB ser liberado
    }

    Write-Host ""
    Write-Host "3) Conferindo no DuckDB..."
    $pyCode = @'
import os, duckdb
from src.config import settings
ext = os.environ.get("JARE_EXT", "")
con = duckdb.connect(settings.duckdb_path, read_only=True)
row = con.execute(
    "SELECT external_id, source, name, trello_card_id FROM leads_raw WHERE external_id = ?",
    [ext],
).fetchone()
con.close()
print("NOROW" if row is None else "|".join("" if c is None else str(c) for c in row))
'@
    $env:JARE_EXT = $ext
    $env:PYTHONPATH = $PSScriptRoot   # o .py temporario roda fora do projeto; isto faz ele achar o pacote `src`
    if ($alreadyUp) {
        Write-Host "     (servidor externo em uso — DuckDB pode estar travado; leitura best-effort)" -ForegroundColor DarkGray
    }
    $dbLine = $null
    # Repassar o codigo via `python -c` corrompe as aspas no PowerShell 5.1;
    # gravamos num .py temporario e rodamos o arquivo (sem problema de quoting).
    $checkPy = Join-Path $env:TEMP "jare_check.py"
    Set-Content -Path $checkPy -Value $pyCode -Encoding UTF8
    try { $dbLine = & $py $checkPy } catch { $dbLine = $null }
    Remove-Item $checkPy -ErrorAction SilentlyContinue

    $cardId = $null
    if ($dbLine -and $dbLine -ne "NOROW") {
        $parts   = $dbLine -split '\|'
        $name    = $parts[2]
        $cardId  = $parts[3]
        Check "lead gravado no DuckDB"            ($parts[0] -eq $ext)
        Check "card do Trello vinculado ao lead"  ([bool]$cardId)
        Write-Host "     nome: $name | trello_card_id: $cardId" -ForegroundColor DarkGray
    }
    else {
        Check "lead encontrado no DuckDB"         $false
        Write-Host "     (sem linha retornada — banco vazio ou arquivo travado)" -ForegroundColor DarkGray
    }

    Write-Host ""
    Write-Host "4) Limpando o lead de teste (apaga card no Trello + linha no banco)..."
    $cleanPy = Join-Path $env:TEMP "jare_clean.py"
    @'
import os, duckdb
from src.config import settings
ext  = os.environ.get("JARE_EXT", "")
card = os.environ.get("JARE_CARD", "")
removido = False
if card and settings.trello_api_key and settings.trello_api_token:
    import requests
    try:
        resp = requests.delete(
            f"https://api.trello.com/1/cards/{card}",
            params={"key": settings.trello_api_key, "token": settings.trello_api_token},
            timeout=15,
        )
        removido = resp.ok
    except Exception:
        pass
con = duckdb.connect(settings.duckdb_path)
con.execute("DELETE FROM leads_raw WHERE source = 'wimoveis' AND external_id = ?", [ext])
con.close()
print(f"card={'apagado' if removido else 'nao'} | linha=apagada")
'@ | Set-Content -Path $cleanPy -Encoding UTF8
    if ($cardId) { $env:JARE_CARD = $cardId } else { $env:JARE_CARD = "" }
    $cleanOut = $null
    try { $cleanOut = & $py $cleanPy } catch { $cleanOut = $null }
    Remove-Item $cleanPy -ErrorAction SilentlyContinue
    if ($cleanOut) { Write-Host "     limpeza: $cleanOut" -ForegroundColor DarkGray }
    else { Write-Host "     (limpeza nao confirmada — apague o card de teste manualmente se necessario)" -ForegroundColor Yellow }
}
finally {
    # Rede de seguranca: se algo estourou no meio, ainda derruba o servidor que subimos.
    if ($server) {
        try { Stop-Process -Id $server.Id -Force; Wait-Process -Id $server.Id -Timeout 10 } catch { }
    }
    Remove-Item env:JARE_EXT, env:JARE_CARD, env:PYTHONPATH -ErrorAction SilentlyContinue
    Pop-Location
}

Write-Host ""
if ($script:failures -eq 0) {
    Write-Host "=== TUDO OK — pipeline lead -> DuckDB -> Trello funcionando. ===" -ForegroundColor Green
    exit 0
}
else {
    Write-Host "=== $($script:failures) verificacao(oes) FALHOU(ARAM). ===" -ForegroundColor Red
    exit 1
}
