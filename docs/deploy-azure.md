# Deploying Turnkeeper to Azure App Service (Azure for Students)

You get one always-on Linux App Service (B1) running the `ghcr.io/arnabz-77/turnkeeper:demo-stable` container,
with a public `https://<app>.azurewebsites.net` URL for judges and PayPal webhooks, SQLite under `/home`
(persistent), and every env var set as an App Setting. Planned lifetime: now until **2026-12-16**.

Run everything in **PowerShell** from the repo root (`D:\Desktop\PayPal\turnkeeper`). No step here prints a
secret value. Do not paste the output of `az webapp config appsettings list` anywhere: it contains values.

Researched 2026-10-24. Sources are listed at the end.

---

## 0. How the image gets built

`.github/workflows/docker.yml` runs on every push to `demo-stable` (and from the Actions tab, "Run workflow").
It builds the repo `Dockerfile` and pushes:

- `ghcr.io/arnabz-77/turnkeeper:demo-stable` (moving tag, the one Azure runs)
- `ghcr.io/arnabz-77/turnkeeper:sha-<7-char sha>` (immutable, for rollback)

It authenticates with the built-in `GITHUB_TOKEN` (`packages: write`), so no repo secrets are needed.
The first build happens when you push `demo-stable` after this workflow is on that branch:

```powershell
git checkout demo-stable
git merge main          # or cherry-pick the .github/ commit
git push origin demo-stable
```

Wait for the green run in GitHub, Actions, "docker". The package then shows under your GitHub profile,
Packages, `turnkeeper`.

**Visibility.** A package published from a private repo starts **private**. While the repo is private (until
about Nov 9) Azure needs credentials to pull it (step 5). Making the repo public later does **not** make the
package public; do it separately (step 11).

---

## 1. Install and sign in to the Azure CLI

```powershell
if (-not (Get-Command az -ErrorAction SilentlyContinue)) { winget install -e --id Microsoft.AzureCLI }
# Open a NEW PowerShell window after installing so `az` is on PATH.
az login
az account show --query "{name:name, id:id, state:state}" -o table
```

The `name` column should read **Azure for Students**. If you have several subscriptions:

```powershell
az account list --query "[].{name:name, id:id}" -o table
az account set --subscription "Azure for Students"
```

Register the App Service provider once (new subscriptions sometimes need it):

```powershell
az provider register --namespace Microsoft.Web --wait
```

## 2. Find a region your subscription is allowed to use

Azure for Students subscriptions carry a Microsoft-assigned Azure Policy named **"Allowed resource deployment
regions"** (typically about five regions, and the set differs per student). Deployments anywhere else fail with
`RequestDisallowedByAzure` / `RequestDisallowedByPolicy`. List yours:

```powershell
az policy assignment list --disable-scope-strict-match `
  --query "[?contains(displayName, 'Allowed')].{name:displayName, params:parameters}" -o json
```

Look for the list of locations in `params` (for example `listOfAllowedLocations`). If nothing prints, check the
portal: **Policy, Authoring, Assignments, "Allowed resource deployment regions", Parameters**.

Pick the allowed region closest to India: `centralindia` > `southindia` > `westindia` > `southeastasia` >
`uaenorth` > any European region. Confirm B1 Linux exists there:

```powershell
az appservice list-locations --sku B1 --linux-workers-enabled -o table
```

## 3. Variables (edit the first three)

```powershell
$LOC  = "centralindia"                      # an allowed region from step 2
$RG   = "turnkeeper-rg"
$APP  = "turnkeeper-$((Get-Random -Maximum 99999))"   # must be globally unique; write it down
$PLAN = "turnkeeper-plan"
$IMAGE = "ghcr.io/arnabz-77/turnkeeper:demo-stable"
```

If you close the window, set these again before running later steps (`$APP` must be the same name you used).

## 4. Resource group, plan, web app

```powershell
az group create --name $RG --location $LOC -o table
az appservice plan create --name $PLAN --resource-group $RG --location $LOC --is-linux --sku B1 -o table
```

Create the app. The `-c` image is pulled anonymously at this point, so while the package is private the first
pull fails; step 5 adds the credentials and fixes that.

```powershell
az webapp create --name $APP --resource-group $RG --plan $PLAN `
  --container-image-name $IMAGE --https-only true -o none
$HOST_NAME = az webapp show -n $APP -g $RG --query defaultHostName -o tsv
$URL = "https://$HOST_NAME"
$URL
```

Use **exactly** the printed `$URL` for `PUBLIC_BASE_URL` and the PayPal webhook. Newer apps can get a hostname
with a random suffix and region, so don't type it from memory.

## 5. Registry credentials (only while the GHCR package is private)

Create a GitHub **personal access token (classic)** at GitHub, Settings, Developer settings, Personal access
tokens, Tokens (classic). Give it only the **`read:packages`** scope, with an expiry **after 2026-12-31**. GHCR
accepts only classic PATs, not fine-grained ones. App Service pulls the image on every restart, so an expired
token takes the site down at the next restart.

```powershell
$sec = Read-Host "GitHub PAT (read:packages)" -AsSecureString
$pat = [System.Net.NetworkCredential]::new("", $sec).Password
az webapp config container set --name $APP --resource-group $RG `
  --container-image-name $IMAGE `
  --container-registry-url "https://ghcr.io" `
  --container-registry-user "ArnabZ-77" `
  --container-registry-password $pat -o none
Remove-Variable pat, sec
```

The username is the GitHub account that owns the token. The credentials are stored as the reserved settings
`DOCKER_REGISTRY_SERVER_*`, which App Service does not expose to the container.

## 6. App Settings

### 6a. Platform settings plus the deployment overrides

```powershell
az webapp config appsettings set --name $APP --resource-group $RG -o none --settings `
  WEBSITES_PORT=8000 `
  WEBSITES_ENABLE_APP_SERVICE_STORAGE=true `
  WEBSITES_CONTAINER_START_TIME_LIMIT=600 `
  TK_DB_PATH=/home/data/turnkeeper.db `
  PAYPAL_ENV=sandbox `
  PUBLIC_BASE_URL=$URL
```

- `WEBSITES_PORT=8000`: the container's uvicorn listens on 8000. App Service assumes 80 otherwise.
- `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`: mounts persistent `/home` (it is **off** by default for Linux
  custom containers). The app creates `/home/data/` itself.
- `WEBSITES_CONTAINER_START_TIME_LIMIT=600`: allows for a slow first image pull on B1. The default is 230 s and
  the maximum is 1800.

### 6b. Everything else from your `.env`, without echoing values

The app reads these names (from `render.yaml` and `.env.example`):

| Required for a working demo | Optional (AI / tuning) |
| --- | --- |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | `TK_LLM_PROVIDER`, `TK_LLM_API_KEY`, `TK_LLM_MODEL`, `TK_LLM_BASE_URL` |
| `PAYPAL_WEBHOOK_ID` (after step 9) | `TK_LLM_MAX_TOKENS`, `TK_LLM_REASONING_EFFORT`, `TK_LLM_CACHE`, `TK_LIVE_LLM_PER_IP_HOUR` |
| `TK_SEED_VAULT_IDS` | `ANTHROPIC_API_KEY`, `TK_REASONING_MODEL`, `TK_TEXT_MODEL` |
| `MANDATE_HMAC_SECRET`, `SESSION_SECRET` (each 16+ random chars) | `TURNKEEPER_FREEZE`, `TK_PAYPAL_RATE_PER_MIN`, `TK_MAX_ACTIVE_SESSIONS` |
| `MCP_DEMO_TOKEN` | any other `TK_*` key present in your `.env` |

`PUBLIC_BASE_URL`, `TK_DB_PATH` and `PAYPAL_ENV` come from 6a and are skipped below. Empty keys are skipped.
The snippet below:

- Generates fresh `MANDATE_HMAC_SECRET` and `SESSION_SECRET` when the local ones are missing or shorter than
  32 characters, but only the first time. On re-runs it keeps the values already on Azure, so sessions and
  mandates stay valid. The app **refuses to start** on an https `PUBLIC_BASE_URL` with default or short secrets.
- Writes the values to a temporary JSON file so that quotes in values such as `TK_SEED_VAULT_IDS` survive
  PowerShell.
- Sends them with `-o none` (otherwise `az` prints every value back), deletes the temp file, and prints
  **names only**.

```powershell
$skip = @("PUBLIC_BASE_URL", "TK_DB_PATH", "PAYPAL_ENV")
$vals = [ordered]@{}
foreach ($line in Get-Content .env -Encoding UTF8) {
  if ($line -match '^\s*(#|$)') { continue }
  if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { continue }
  $k = $Matches[1]; $v = $Matches[2].Trim()
  if ($v -match '^"(.*)"$' -or $v -match "^'(.*)'$") { $v = $Matches[1] }
  else { $v = ($v -replace '\s+#.*$', '').Trim() }
  if ($skip -contains $k -or $v -eq "") { continue }
  $vals[$k] = $v
}
function New-Secret { $b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b).TrimEnd('=').Replace('+','-').Replace('/','_') }
$existing = @(az webapp config appsettings list -n $APP -g $RG --query "[].name" -o tsv)
foreach ($s in "MANDATE_HMAC_SECRET", "SESSION_SECRET") {
  if ($vals.Contains($s) -and $vals[$s].Length -ge 32) { continue }          # strong local value: use it
  if ($existing -contains $s) { $vals.Remove($s); continue }                 # keep the one already on Azure (re-runs)
  $vals[$s] = New-Secret; Write-Host "generated $s"
}
$list = @(foreach ($k in $vals.Keys) { [pscustomobject]@{ name = $k; value = $vals[$k]; slotSetting = $false } })
$tmp = Join-Path $env:TEMP "tk-appsettings-$([guid]::NewGuid()).json"
[IO.File]::WriteAllText($tmp, (ConvertTo-Json -InputObject $list -Depth 3), (New-Object Text.UTF8Encoding $false))
try { az webapp config appsettings set --name $APP --resource-group $RG --settings "@$tmp" -o none }
finally { Remove-Item $tmp -Force }
Remove-Variable vals, list
Write-Host "set:" (az webapp config appsettings list -n $APP -g $RG --query "[].name" -o tsv) -Separator "`n  "
```

Check the printed names against the table. Re-run the snippet any time you change `.env`; changing settings
restarts the app. Note that `PAYPAL_WEBHOOK_ID` in your local `.env` belongs to your ngrok/local URL and gets
replaced in step 9.

## 7. Always On, health check, logs

```powershell
az webapp config set --name $APP --resource-group $RG --always-on true --http20-enabled true -o none
az webapp config set --name $APP --resource-group $RG --generic-configurations healthCheckPath=/healthz -o none
az webapp config show --name $APP --resource-group $RG --query "{alwaysOn:alwaysOn, healthCheckPath:healthCheckPath}" -o table
az webapp log config --name $APP --resource-group $RG --docker-container-logging filesystem -o none
az webapp restart --name $APP --resource-group $RG
```

- **Always On** (available on Basic and above): without it the app unloads after 20 idle minutes. With it, the
  front end pings `/` every 5 minutes.
- **Health check** pings `/healthz` every minute. With a single B1 instance it can't reroute traffic; it only
  replaces an instance that stays unhealthy for an hour. Its main use here is the metric and alerts.

## 8. Verify

```powershell
az webapp log tail --name $APP --resource-group $RG     # Ctrl+C once you see "Application startup complete"
Invoke-RestMethod "$URL/healthz" | ConvertTo-Json
Start-Process $URL
```

The first start pulls the image and can take 2 to 5 minutes. In `/healthz`, expect `"ok": true`,
`"paypal_configured": true`, `"vault_seeded": true` and `"warnings": []`. Any warning names exactly what's
missing.

**SQLite on `/home` check (do this once).** `/home` is a network (SMB) share. Microsoft's FAQ warns that file
databases can hit locking problems there, and SQLite's WAL mode (which `app/db.py` enables) is documented as
unsupported on network filesystems. With one container and one uvicorn worker it usually works, but verify:
start a demo session, run a round, restart the app (`az webapp restart ...`) and confirm the history survived.
Also watch the log tail for `database is locked` or `disk I/O error`. If either appears, fall back to local
container disk for the rest of the event (the demo re-clones its fixture per session, so it still works; only
webhook/call history resets on restart):

```powershell
az webapp config appsettings set -n $APP -g $RG -o none --settings TK_DB_PATH=/tmp/turnkeeper.db
```

Ask the app owners for a setting that switches `journal_mode` to `DELETE` when the DB lives under `/home`.

## 9. Register the PayPal webhook and set `PAYPAL_WEBHOOK_ID`

Run locally with your `.env` (sandbox credentials). The script registers `<URL>/webhooks/paypal`, or reuses the
registration if one already exists, and prints the id:

```powershell
.venv\Scripts\python -I scripts\register_webhook.py --url "$URL/webhooks/paypal"
```

Copy the `WH-...`/id from the `PAYPAL_WEBHOOK_ID=...` line, then:

```powershell
$WHID = Read-Host "webhook id"
az webapp config appsettings set -n $APP -g $RG -o none --settings PAYPAL_WEBHOOK_ID=$WHID
```

(Setting it restarts the app; wait for `/healthz` to answer again.) PayPal allows at most 10 webhooks per REST
app. Use `register_webhook.py --list` and `--delete <id>` to clean up old ngrok ones.

**Smoke test:** a real delivery, verified by signature:

```powershell
.venv\Scripts\python -I scripts\smoke\f_webhook.py --public-url $URL --vault-id <a sandbox vault id>
```

PASS means a new event reached `$URL/api/debug/webhooks` with `verified: true`. Without `--vault-id`, nothing
triggers an event; trigger one from the demo UI instead, or pass `--allow-unverified` only to debug signature
setup. Record the result in `docs/d1-smoke.md`.

## 10. Redeploy

1. Merge to `demo-stable` and push. The **docker** workflow pushes a new `:demo-stable` and `:sha-<short>`.
2. When the run is green:

```powershell
az webapp restart --name $APP --resource-group $RG
```

App Service re-pulls the tag on every restart (only changed layers) and keeps serving the old container until
the new one answers. Check `"$URL/healthz"`.

**Pin or roll back** to an exact build:

```powershell
az webapp config container set -n $APP -g $RG --container-image-name ghcr.io/arnabz-77/turnkeeper:sha-abc1234 -o none
```

Switch back to `:demo-stable` the same way.

## 11. When the repo goes public (around Nov 9)

On GitHub, open Packages, `turnkeeper`, Package settings, **Change visibility, Public**. Then remove the stored
PAT so nothing expires under you:

```powershell
az webapp config appsettings delete -n $APP -g $RG --setting-names DOCKER_REGISTRY_SERVER_USERNAME DOCKER_REGISTRY_SERVER_PASSWORD -o none
az webapp config container set -n $APP -g $RG --container-image-name $IMAGE --container-registry-url "https://ghcr.io" -o none
az webapp restart -n $APP -g $RG
```

If you leave the package private, keep the PAT valid until after 2026-12-16.

## 12. Cost

Azure retail price API, 2026-10-24, pay-as-you-go list price (charged against the $100 credit):

| Item | Rate | Per month (730 h) | Oct 24 to Dec 16 2026 (54 days, about 1,300 h) |
| --- | --- | --- | --- |
| App Service **B1 Linux**, Central India | $0.018/h | **about $13.1** | **about $23** |
| B1 Linux, South India / East US / West Europe | $0.019 / $0.017 / $0.018 per h | $12.4 to $13.9 | $22 to $25 |
| GHCR image storage + pulls | free for this use | $0 | $0 |
| Outbound data | first 100 GB/month free | about $0 | about $0 |

That leaves about $75 of credit. The plan bills **per hour while it exists, whether the app is running or
stopped**. Check the balance at https://www.microsoftazuresponsorships.com/. If the credit runs out, the
subscription is disabled and the site goes down; there is no surprise card charge, because there is no card on
file.

## 13. After judging: stop paying

Optionally download the DB first: in the portal, open the app, **Advanced Tools (Kudu), Go, Debug console**,
browse to `/home/data/` and download `turnkeeper.db`.

```powershell
az group delete --name $RG --yes --no-wait     # deletes plan + app; billing stops
.venv\Scripts\python -I scripts\register_webhook.py --list
.venv\Scripts\python -I scripts\register_webhook.py --delete <the azure webhook id>
```

`az webapp stop` alone does **not** stop B1 charges; delete the plan or the resource group. If judging runs
late, keep it until then: B1 costs about $0.43/day.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `RequestDisallowedByAzure` / `RequestDisallowedByPolicy` on create | Region not in your allowed list (step 2). Delete the RG and recreate it in an allowed region. |
| Quota error creating the B1 plan | Try another allowed region, or open a portal support request (Service and subscription limits). |
| Log shows `unauthorized` / `manifest unknown` pulling the image | Package private and PAT missing/expired/wrong scope (step 5), or the docker workflow hasn't pushed `:demo-stable` yet. |
| `Container didn't respond to HTTP pings on port: 80` | `WEBSITES_PORT=8000` missing (step 6a). |
| Container exits with `refusing to start: MANDATE_HMAC_SECRET ...` | Re-run 6b (it generates strong secrets). |
| `/healthz` warns "PUBLIC_BASE_URL is not the public https URL" | Set `PUBLIC_BASE_URL=$URL` exactly (step 6a). |
| Data gone after restart | `WEBSITES_ENABLE_APP_SERVICE_STORAGE` isn't `true` or `TK_DB_PATH` isn't under `/home` (unless you chose the `/tmp` fallback). |
| Webhooks stored `verified: false` | App's `PAYPAL_WEBHOOK_ID` isn't the id registered for `$URL/webhooks/paypal` (step 9). |

## Optional: Azure OpenAI as the live model

Treat this as unconfirmed for Azure for Students (see the research notes). If you can create a Foundry/Azure
OpenAI resource in an allowed region and deploy, for example, `gpt-4.1-mini` (Global Standard), the app's
`openai_compat` provider can use Azure's OpenAI-compatible v1 endpoint:

```
TK_LLM_PROVIDER=openai_compat
TK_LLM_BASE_URL=https://<resource-name>.openai.azure.com/openai/v1/
TK_LLM_MODEL=<your deployment name>
TK_LLM_API_KEY=<key from the resource's Keys and Endpoint>
```

Add them to `.env` and re-run 6b. List prices per 1M tokens (Global Standard): gpt-4.1-mini $0.40 in / $0.10
cached / $1.60 out; gpt-5-mini $0.25 in / $0.025 cached / $2.00 out. These are paid from the same $100 credit.

## Research notes and sources

- **Azure for Students:** $100 credit for 12 months, no card; when the credit runs out the subscription is
  canceled unless you upgrade; the credit doesn't cover Marketplace offers.
  https://learn.microsoft.com/en-us/azure/education-hub/azure-dev-tools-teaching/program-faq
- **Region policy "Allowed resource deployment regions"** (about five regions per student, set varies; check
  Policy, Assignments). These are community and Microsoft Q&A answers, not product docs:
  https://learn.microsoft.com/answers/a/12297582 and https://learn.microsoft.com/answers/a/12822623
- **SKUs:** no official doc restricts App Service SKUs on Azure for Students. B1 Linux is a normal paid SKU
  billed against the credit. Student subscriptions do sometimes see per-region SKU or capacity limits (reported
  for VMs):
  https://techcommunity.microsoft.com/discussions/microsoft-learn-for-educators/sku-quota-and-policy-restrictions-on-azure-for-students-and-free-subscriptions/4525160/replies/4526776
- **Custom containers** (private registry flags, `WEBSITES_PORT`, `/home` persistence off by default, re-pull on
  restart): https://learn.microsoft.com/en-us/azure/app-service/configure-custom-container
- **Linux FAQ** (`WEBSITES_CONTAINER_START_TIME_LIMIT` 230 to 1800 s, registry URL needs `https://`, SQLite
  warning on `/home`): https://learn.microsoft.com/en-us/troubleshoot/azure/app-service/faqs-app-service-linux
- **SQLite WAL** doesn't work over a network filesystem: https://www.sqlite.org/wal.html
- **Always On** (20-minute idle unload; ping every 5 min): https://learn.microsoft.com/en-us/azure/app-service/configure-common
- **Health check** (1-min pings, single-instance behavior): https://learn.microsoft.com/en-us/azure/app-service/monitor-instances-health-check
- **CLI flags:** https://learn.microsoft.com/en-us/cli/azure/webapp?view=azure-cli-latest and
  https://learn.microsoft.com/en-us/cli/azure/webapp/config/container?view=azure-cli-latest
- **GHCR** (classic PAT with `read:packages`; new packages private; anonymous pull for public):
  https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
- **Prices:** Azure Retail Prices API, https://prices.azure.com/api/retail/prices (App Service B1 Linux;
  Foundry Models "gpt 4.1 mini" / "GPT 5 Mini" Global meters).
- **Azure OpenAI quota tiers:** "Free Tier" and Tiers 1 to 6, eligibility partly by Azure offer; batch quota is
  "N/A" for "Azure for Students, free trials":
  https://learn.microsoft.com/en-us/azure/ai-foundry/openai/quotas-limits. A student reported a Free Tier to
  Tier 1 upgrade email in Apr 2026: https://learn.microsoft.com/en-us/answers/a/12716535
- **Azure OpenAI v1 API with the plain OpenAI client:** https://learn.microsoft.com/en-us/azure/ai-foundry/openai/api-version-lifecycle
