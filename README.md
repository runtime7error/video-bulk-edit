# ✂️ Auto Cutter

Espelha o vídeo na horizontal, corta em trechos de 5 s e junta tudo de novo com crossfade (vídeo + áudio).

Duas formas de usar:

| | Como | Para quê |
|---|---|---|
| **Windows (local)** | `PROCESSAR.bat` + `processar.ps1` | Processar no PC, sem internet |
| **Web (celular)** | pasta `app/` + `Dockerfile` | Deploy no Railway/Render e usar pelo navegador |

---

## Versão Windows

1. Coloque os vídeos em `entrada\`
2. Dê dois cliques em `PROCESSAR.bat` (ou arraste vídeos em cima dele)
3. Resultado em `saida\`

Se o PC não tiver FFmpeg, o script baixa uma versão portátil automaticamente na primeira vez.

---

## Versão web — deploy

### 1. Subir o código para o GitHub

**Sem instalar nada:** crie um repositório em <https://github.com/new>, clique em
*"uploading an existing file"* e arraste **estes itens**:

```
app/            (a pasta inteira)
Dockerfile
requirements.txt
railway.json
render.yaml
.dockerignore
.gitignore
```

> Não envie `entrada/`, `saida/`, `ffmpeg/` nem vídeos.

**Com Git** (`winget install Git.Git`):
```powershell
git init; git add .; git commit -m "auto cutter"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/auto-cutter.git
git push -u origin main
```

### 2a. Railway (recomendado)

1. <https://railway.com> → **New Project** → **Deploy from GitHub repo** → escolha o repositório
2. Ele detecta o `Dockerfile` sozinho
3. Aba **Variables** → adicione `APP_PASSWORD` = sua senha
4. Aba **Settings → Networking** → **Generate Domain**
5. Abra o link no celular (usuário: qualquer coisa, senha: a que você definiu)

### 2b. Render

1. <https://render.com> → **New +** → **Blueprint** → escolha o repositório (usa o `render.yaml`)
2. Informe o valor de `APP_PASSWORD` quando pedir
3. Abra a URL `https://auto-cutter-xxxx.onrender.com` no celular

> Dica: no celular, use "Adicionar à tela inicial" para virar um ícone de app.

### Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `APP_PASSWORD` | *(vazio = sem senha)* | Senha de acesso. **Defina sempre** em produção |
| `X264_PRESET` | `veryfast` | `ultrafast` = mais rápido e usa menos RAM; `medium` = arquivo menor |
| `X264_CRF` | `20` | Qualidade (menor = melhor/maior) |
| `FFMPEG_THREADS` | `0` (auto) | Use `1`–`2` em planos com pouca RAM |
| `MAX_UPLOAD_MB` | `500` | Tamanho máximo por vídeo |
| `JOB_TTL_MIN` | `60` | Minutos até apagar os arquivos processados |

### Rodar a versão web localmente

```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Abra <http://localhost:8000> (ou `http://IP-DO-PC:8000` no celular, na mesma rede Wi-Fi).
