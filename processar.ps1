# Auto Cutter - edicao de videos em lote com FFmpeg
# Para cada video:
#   1. espelha a imagem na horizontal
#   2. divide em trechos de N segundos (padrao 5)
#   3. junta os trechos de novo com crossfade (video + audio) entre eles
#
# Uso:
#   - Coloque os videos na pasta "entrada" e execute PROCESSAR.bat
#   - Ou arraste arquivos de video em cima do PROCESSAR.bat
#   - Ou: powershell -ExecutionPolicy Bypass -File processar.ps1 -Segmento 5 -Fade 0.5

param(
    [double]$Segmento = 5,          # duracao de cada trecho (segundos)
    [double]$Fade = 0.5,            # duracao do crossfade (segundos)
    [string]$Transicao = 'fade',    # qualquer transicao do xfade: fade, dissolve, fadeblack, wipeleft...
    [int]$Crf = 18,                 # qualidade x264 (menor = melhor/maior arquivo)
    [string]$Preset = 'medium',     # velocidade do x264: ultrafast ... veryslow
    [string]$Entrada = '',          # padrao: pasta "entrada" ao lado do script
    [string]$Saida = '',            # padrao: pasta "saida" ao lado do script
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arquivos
)

$ErrorActionPreference = 'Stop'

# $PSScriptRoot pode vir vazio (ISE, "Executar com PowerShell", codigo colado...)
$baseDir = $PSScriptRoot
if (-not $baseDir -and $MyInvocation.MyCommand.Path) { $baseDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $baseDir) { $baseDir = (Get-Location).Path }
if (-not $Entrada) { $Entrada = Join-Path $baseDir 'entrada' }
if (-not $Saida) { $Saida = Join-Path $baseDir 'saida' }
$inv = [Globalization.CultureInfo]::InvariantCulture
$extensoes = @('.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.wmv', '.flv')

function Num([double]$x) { $x.ToString('0.######', $inv) }

function Find-FFmpeg {
    # 1) pasta local "ffmpeg" ao lado do script (portatil)  2) ao lado do script  3) PATH
    $localDir = Join-Path $baseDir 'ffmpeg'
    foreach ($dir in @($localDir, $baseDir)) {
        $f = Join-Path $dir 'ffmpeg.exe'; $p = Join-Path $dir 'ffprobe.exe'
        if ((Test-Path -LiteralPath $f) -and (Test-Path -LiteralPath $p)) { return @($f, $p) }
    }
    $f = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $p = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($f -and $p) { return @($f.Source, $p.Source) }
    return $null
}

function Install-FFmpeg {
    $url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
    $localDir = Join-Path $baseDir 'ffmpeg'
    $zip = Join-Path $env:TEMP 'autocutter-ffmpeg.zip'
    $tmp = Join-Path $env:TEMP 'autocutter-ffmpeg'

    Write-Host "FFmpeg nao encontrado. Baixando versao portatil (~100 MB, so na primeira vez)..." -ForegroundColor Yellow
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source -L --fail --progress-bar -o $zip $url
        if ($LASTEXITCODE -ne 0) { throw "falha no download (curl codigo $LASTEXITCODE)" }
    } else {
        $old = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        $ProgressPreference = $old
    }

    Write-Host "Extraindo..." -ForegroundColor Yellow
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Force -Path $localDir | Out-Null
    foreach ($exe in 'ffmpeg.exe', 'ffprobe.exe') {
        $src = Get-ChildItem -LiteralPath $tmp -Recurse -Filter $exe | Select-Object -First 1
        if (-not $src) { throw "$exe nao encontrado no pacote baixado" }
        Copy-Item -LiteralPath $src.FullName -Destination $localDir -Force
    }
    Remove-Item -LiteralPath $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "FFmpeg instalado em: $localDir" -ForegroundColor Green
}

$ff = Find-FFmpeg
if (-not $ff) {
    try { Install-FFmpeg; $ff = Find-FFmpeg } catch {
        Write-Host "ERRO ao baixar o FFmpeg: $($_.Exception.Message)" -ForegroundColor Red
    }
    if (-not $ff) {
        Write-Host "Baixe manualmente em https://www.gyan.dev/ffmpeg/builds/ e coloque ffmpeg.exe e ffprobe.exe em: $(Join-Path $baseDir 'ffmpeg')" -ForegroundColor Red
        exit 1
    }
}
$ffmpeg, $ffprobe = $ff
if ($Fade -ge $Segmento) {
    Write-Host "ERRO: o fade ($Fade s) precisa ser menor que o segmento ($Segmento s)." -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $Entrada, $Saida | Out-Null

# Lista de arquivos: os arrastados no .bat ou todos da pasta de entrada
if ($Arquivos -and $Arquivos.Count -gt 0) {
    $videos = $Arquivos | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | ForEach-Object { Get-Item -LiteralPath $_ }
} else {
    $videos = Get-ChildItem -LiteralPath $Entrada -File | Where-Object { $extensoes -contains $_.Extension.ToLower() }
}
$videos = @($videos)

if ($videos.Count -eq 0) {
    Write-Host "Nenhum video encontrado. Coloque os videos em: $Entrada" -ForegroundColor Yellow
    exit 0
}

Write-Host "Videos: $($videos.Count) | Segmento: $(Num $Segmento)s | Fade: $(Num $Fade)s ($Transicao)" -ForegroundColor Cyan

function Get-Probe([string]$file, [string[]]$probeArgs) {
    $out = & $ffprobe -v error @probeArgs -of 'default=nw=1:nk=1' -- $file 2>$null
    if ($out) { return ($out | Select-Object -First 1).ToString().Trim() }
    return ''
}

$ok = 0; $falhas = 0; $i = 0
foreach ($video in $videos) {
    $i++
    $nome = [IO.Path]::GetFileNameWithoutExtension($video.Name)
    $destino = Join-Path $Saida "$($nome)_editado.mp4"
    Write-Host ""
    Write-Host "[$i/$($videos.Count)] $($video.Name)" -ForegroundColor Green

    $graphFile = $null
    try {
        $durStr = Get-Probe $video.FullName @('-show_entries', 'format=duration')
        $dur = 0.0
        if (-not [double]::TryParse($durStr, [Globalization.NumberStyles]::Float, $inv, [ref]$dur) -or $dur -le 0) {
            throw "nao foi possivel ler a duracao do video"
        }
        $fps = Get-Probe $video.FullName @('-select_streams', 'v:0', '-show_entries', 'stream=avg_frame_rate')
        if (-not $fps -or $fps -match '^0') { $fps = '30' }
        $temAudio = [bool](Get-Probe $video.FullName @('-select_streams', 'a:0', '-show_entries', 'stream=index'))

        # Monta os trechos [inicio, fim]. Se o ultimo trecho ficar muito curto, junta com o anterior.
        $minUltimo = $Fade + 0.5
        $trechos = New-Object System.Collections.Generic.List[double[]]
        $t = 0.0
        while ($t -lt $dur - 0.001) {
            $fim = [Math]::Min($t + $Segmento, $dur)
            $trechos.Add(@($t, $fim))
            $t = $fim
        }
        if ($trechos.Count -gt 1 -and ($trechos[$trechos.Count - 1][1] - $trechos[$trechos.Count - 1][0]) -lt $minUltimo) {
            $ultimo = $trechos[$trechos.Count - 1]
            $trechos.RemoveAt($trechos.Count - 1)
            $trechos[$trechos.Count - 1][1] = $ultimo[1]
        }
        $n = $trechos.Count
        Write-Host ("  Duracao: {0}s | FPS: {1} | Audio: {2} | Trechos: {3}" -f (Num ([Math]::Round($dur, 2))), $fps, ($(if ($temAudio) { 'sim' } else { 'nao' })), $n)

        # ---- Monta o filter_complex ----
        $g = New-Object System.Text.StringBuilder
        $vBase = "fps=$fps,hflip,format=yuv420p,settb=AVTB"

        if ($n -eq 1) {
            [void]$g.AppendLine("[0:v]$vBase[vout];")
            if ($temAudio) { [void]$g.AppendLine("[0:a]anull[aout];") }
        } else {
            $vLabels = (0..($n - 1) | ForEach-Object { "[v$_]" }) -join ''
            [void]$g.AppendLine("[0:v]$vBase,split=$n$vLabels;")
            if ($temAudio) {
                $aLabels = (0..($n - 1) | ForEach-Object { "[a$_]" }) -join ''
                [void]$g.AppendLine("[0:a]aresample=async=1,asplit=$n$aLabels;")
            }
            for ($k = 0; $k -lt $n; $k++) {
                $ini = Num $trechos[$k][0]
                $endOpt = if ($k -lt $n - 1) { ":end=$(Num $trechos[$k][1])" } else { '' }
                [void]$g.AppendLine("[v$k]trim=start=$ini$endOpt,setpts=PTS-STARTPTS[s$k];")
                if ($temAudio) {
                    [void]$g.AppendLine("[a$k]atrim=start=$ini$endOpt,asetpts=PTS-STARTPTS[t$k];")
                }
            }
            # Encadeia os crossfades
            $comp = $trechos[0][1] - $trechos[0][0]   # duracao acumulada do resultado
            $vPrev = 's0'; $aPrev = 't0'
            for ($k = 1; $k -lt $n; $k++) {
                $offset = $comp - $Fade
                $vOut = if ($k -eq $n - 1) { 'vout' } else { "x$k" }
                $aOut = if ($k -eq $n - 1) { 'aout' } else { "y$k" }
                [void]$g.AppendLine("[$vPrev][s$k]xfade=transition=$($Transicao):duration=$(Num $Fade):offset=$(Num $offset)[$vOut];")
                if ($temAudio) {
                    [void]$g.AppendLine("[$aPrev][t$k]acrossfade=d=$(Num $Fade):c1=tri:c2=tri[$aOut];")
                }
                $comp += ($trechos[$k][1] - $trechos[$k][0]) - $Fade
                $vPrev = $vOut; $aPrev = $aOut
            }
        }

        $graph = $g.ToString().Trim().TrimEnd(';')
        $graphFile = [IO.Path]::GetTempFileName()
        [IO.File]::WriteAllText($graphFile, $graph, (New-Object System.Text.UTF8Encoding($false)))

        $ffArgs = @('-hide_banner', '-loglevel', 'error', '-stats', '-y',
                    '-i', $video.FullName,
                    '-/filter_complex', $graphFile,
                    '-map', '[vout]')
        if ($temAudio) { $ffArgs += @('-map', '[aout]', '-c:a', 'aac', '-b:a', '192k') }
        $ffArgs += @('-c:v', 'libx264', '-preset', $Preset, '-crf', "$Crf", '-pix_fmt', 'yuv420p',
                     '-movflags', '+faststart', $destino)

        $sw = [Diagnostics.Stopwatch]::StartNew()
        & $ffmpeg @ffArgs
        if ($LASTEXITCODE -ne 0) { throw "ffmpeg retornou codigo $LASTEXITCODE" }
        Write-Host ("  OK -> {0} ({1:N0}s)" -f $destino, $sw.Elapsed.TotalSeconds) -ForegroundColor Green
        $ok++
    } catch {
        Write-Host "  FALHA: $($_.Exception.Message)" -ForegroundColor Red
        $falhas++
    } finally {
        if ($graphFile -and (Test-Path -LiteralPath $graphFile)) { Remove-Item -LiteralPath $graphFile -Force }
    }
}

Write-Host ""
Write-Host "Concluido: $ok ok, $falhas falha(s). Saida em: $Saida" -ForegroundColor Cyan
