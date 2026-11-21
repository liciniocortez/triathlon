# Painel de triathlon · Licinio Cortez

Site estático no GitHub Pages. Todo dia às 06:00 (Imperatriz) o GitHub busca Strava, Whoop, Garmin e TrainingPeaks, gera `data/data.json` e publica o site.

---

## Configuração (uma vez só)

### 1. Ativar o GitHub Pages
**Settings › Pages › Build and deployment › Source:** escolha **GitHub Actions**.

Endereço do site: `https://SEU-USUARIO.github.io/triathlon/`

### 2. Criar os apps
**Strava** · strava.com/settings/api
- Application Name: `Painel`
- Website: `https://SEU-USUARIO.github.io/triathlon/`
- Authorization Callback Domain: `SEU-USUARIO.github.io`
- Anote o **Client ID** e o **Client Secret**

**Whoop** · developer-dashboard.whoop.com
- Crie um app com os escopos: `read:recovery`, `read:cycles`, `read:sleep`, `read:workout`, `read:profile`, `offline`
- Redirect URL: `https://SEU-USUARIO.github.io/triathlon/conectar.html`
- Anote o **Client ID** e o **Client Secret**

### 3. Cadastrar os segredos
**Settings › Secrets and variables › Actions › New repository secret.** Um por vez:

| Nome | Valor |
|---|---|
| `TOKEN_KEY` | Uma frase longa e aleatória (40+ caracteres). Protege as autorizações salvas. Não perca: se mudar, é preciso reconectar as contas. |
| `STRAVA_CLIENT_ID` | do passo 2 |
| `STRAVA_CLIENT_SECRET` | do passo 2 |
| `WHOOP_CLIENT_ID` | do passo 2 |
| `WHOOP_CLIENT_SECRET` | do passo 2 |
| `GARMIN_EMAIL` | e-mail do Garmin Connect |
| `GARMIN_PASSWORD` | senha do Garmin Connect |
| `TP_ICAL_URL` | link do calendário do TrainingPeaks (Configurações › Conta › Calendário) |

### 4. Publicar a primeira vez
**Actions › Atualizar dados e publicar site › Run workflow.** Em ~2 min o site está no ar (ainda com dados de exemplo).

### 5. Conectar as contas
1. Abra `https://SEU-USUARIO.github.io/triathlon/conectar.html`
2. **Strava:** cole o Client ID › Autorizar › aceite › copie o código que aparece
3. **Actions › Conectar conta › Run workflow** › serviço `strava` › cole o código › Run workflow
4. Repita para o **Whoop** (serviço `whoop`)
5. **Garmin:** Actions › Conectar conta › serviço `garmin`, código vazio
   - Se a conta Garmin tiver verificação em duas etapas, desative durante a conexão e reative depois.

### 6. Rodar a atualização
**Actions › Atualizar dados e publicar site › Run workflow.** A partir daqui, roda sozinho todo dia.

---

## Ajustes no dia a dia

**`config.json`** (editar direto no GitHub, ícone de lápis):
- `limiares`: FTP, FC de limiar por modalidade e CSS da natação. Usados no cálculo de TSS e da carga de treino.
- `provas`: resultados oficiais. Modelo:

```json
{
  "nome": "IRONMAN 70.3 Maceió",
  "data": "2026-08-16",
  "categoria": "tri",
  "local": "Maceió, AL",
  "destaque": "12º M40-44",
  "distancias": { "natacao": 1.9, "bike": 90, "corrida": 21.1 },
  "oficial": { "natacao": "38:12", "t1": "4:05", "bike": "2:41:30", "t2": "2:48", "corrida": "1:52:40", "total": "5:19:15" }
}
```
Para provas de corrida: `"categoria": "run"` e só `"corrida"` nas distâncias e `"total"` no oficial.
Corridas marcadas como **Prova** no Strava entram sozinhas.

**`index.html`**: textos do site (prefácio, ficha lateral).

## Se algo falhar
Actions › última execução › a etapa vermelha mostra o motivo. Se uma fonte falhar num dia, o site mantém o último dado bom dela.
