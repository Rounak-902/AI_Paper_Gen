# PaperGen Backend

FastAPI API and Celery workers workspace for the AI Question Paper Generation Platform.

## Development

Local Compose Redis requires authentication. `infra/.env`'s `REDIS_PASSWORD` is
the authoritative local credential; synchronize the two authenticated backend URLs
before starting the API or a Celery worker:

```powershell
# From the repository root. This never prints the credential.
. .\scripts\local-infra-env.ps1
Sync-PaperGenBackendRedisEnv `
  -EnvFile .\infra\.env `
  -BackendEnvFile .\backend\.env

Set-Location backend
.\.venv\Scripts\Activate.ps1

# Run API dev server
uvicorn app.main:app --reload --port 8000
```
