# ✂️ Auto Cutter

Espelha o vídeo na horizontal, corta em trechos de 5 s e junta tudo de novo com crossfade (vídeo + áudio).

Duas formas de usar:

| | Como | Para quê |
|---|---|---|
| **Windows (local)** | `PROCESSAR.bat` + `processar.ps1` | Processar no PC, sem internet |

---

## Versão Windows

1. Coloque os vídeos em `entrada\`
2. Dê dois cliques em `PROCESSAR.bat` (ou arraste vídeos em cima dele)
3. Resultado em `saida\`

Se o PC não tiver FFmpeg, o script baixa uma versão portátil automaticamente na primeira vez.

---
### Rodar a versão web localmente

```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Abra <http://localhost:8000> (ou `http://IP-DO-PC:8000` no celular, na mesma rede Wi-Fi).
