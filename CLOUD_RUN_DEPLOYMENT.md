# Google Cloud Run Deployment Guide

This guide explains how to deploy the Wordle Bot to Google Cloud Run.

## Prerequisites

- Google Cloud Project with billing enabled
- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) installed and configured
- Docker installed (for local testing)
- All required environment variables ready

## Environment Variables Required

Create a `.env.gcp` file with the following:

```env
DISCORD_TOKEN=your_discord_bot_token
APP_ID=your_discord_app_id
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key
```

## Step 1: Set Up Google Cloud Project

```bash
# Set your project ID
export PROJECT_ID=your-project-id
gcloud config set project $PROJECT_ID

# Enable required APIs
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable containerregistry.googleapis.com
```

## Step 2: Create a Secret Manager Secret (for sensitive environment variables)

```bash
# Store Discord Token
echo -n "your_discord_token" | gcloud secrets create discord-token --data-file=-

# Store Supabase URL
echo -n "your_supabase_url" | gcloud secrets create supabase-url --data-file=-

# Store Supabase Key
echo -n "your_supabase_key" | gcloud secrets create supabase-key --data-file=-

# Store Discord App ID
echo -n "your_discord_app_id" | gcloud secrets create discord-app-id --data-file=-
```

## Step 3: Build and Push Docker Image

```bash
# Set image name
export IMAGE_NAME=gcr.io/$PROJECT_ID/wordle-bot:latest

# Build the Docker image
gcloud builds submit --tag $IMAGE_NAME

# Or build locally and push
docker build -t $IMAGE_NAME .
docker push $IMAGE_NAME
```

## Step 4: Deploy to Cloud Run

```bash
gcloud run deploy wordle-bot \
  --image $IMAGE_NAME \
  --platform managed \
  --region us-central1 \
  --memory 1Gi \
  --cpu 1 \
  --timeout 3600 \
  --max-instances 100 \
  --min-instances 1 \
  --set-env-vars DISCORD_TOKEN=$(gcloud secrets versions access latest --secret="discord-token") \
  --set-env-vars SUPABASE_URL=$(gcloud secrets versions access latest --secret="supabase-url") \
  --set-env-vars SUPABASE_KEY=$(gcloud secrets versions access latest --secret="supabase-key") \
  --set-env-vars APP_ID=$(gcloud secrets versions access latest --secret="discord-app-id") \
  --port 8080 \
  --no-allow-unauthenticated
```

**Alternatively, use Secret Manager directly (recommended for production):**

```bash
gcloud run deploy wordle-bot \
  --image $IMAGE_NAME \
  --platform managed \
  --region us-central1 \
  --memory 1Gi \
  --cpu 1 \
  --timeout 3600 \
  --max-instances 100 \
  --min-instances 1 \
  --set-cloudsql-instances="" \
  --update-secrets DISCORD_TOKEN=discord-token:latest \
  --update-secrets SUPABASE_URL=supabase-url:latest \
  --update-secrets SUPABASE_KEY=supabase-key:latest \
  --update-secrets APP_ID=discord-app-id:latest \
  --port 8080 \
  --allow-unauthenticated
```

## Step 5: Configure Service Account Permissions

```bash
# Grant service account access to secrets
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
CLOUD_RUN_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

gcloud secrets add-iam-policy-binding discord-token \
  --member=serviceAccount:$CLOUD_RUN_SA \
  --role=roles/secretmanager.secretAccessor

gcloud secrets add-iam-policy-binding supabase-url \
  --member=serviceAccount:$CLOUD_RUN_SA \
  --role=roles/secretmanager.secretAccessor

gcloud secrets add-iam-policy-binding supabase-key \
  --member=serviceAccount:$CLOUD_RUN_SA \
  --role=roles/secretmanager.secretAccessor

gcloud secrets add-iam-policy-binding discord-app-id \
  --member=serviceAccount:$CLOUD_RUN_SA \
  --role=roles/secretmanager.secretAccessor
```

## Step 6: Verify Deployment

```bash
# Get the service URL
gcloud run services describe wordle-bot --region us-central1 --format='value(status.url)'

# Test health endpoint
curl https://YOUR_SERVICE_URL/health

# Check logs
gcloud run services logs read wordle-bot --region us-central1 --limit 50
```

## Cloud Run Configuration Details

### Memory & CPU Settings

- **Memory**: 1Gi (1024MB) - sufficient for bot operations
- **CPU**: 1 vCPU - handles Discord events efficiently
- **Timeout**: 3600s (1 hour) - allows long-running operations

### Auto-scaling

- **min-instances**: 1 - keeps bot responsive
- **max-instances**: 100 - prevents runaway costs

### Deployment Mode

- **Managed**: Serverless, fully managed by Google Cloud
- **Platform**: Ensures compatibility

## Key Improvements for Cloud Run

### 1. Health Check Endpoints

- `/health` - General health status
- `/ready` - Readiness check
- Automatically monitored by Cloud Run

### 2. Graceful Shutdown

- Handles SIGTERM signals properly
- Closes bot connections cleanly
- Prevents data corruption

### 3. Environment Variables

- Supports `PORT` environment variable (Cloud Run sets it)
- Uses Secret Manager for sensitive data
- Properly configured for containerized environment

### 4. Request Handling

- Flask server listens on `0.0.0.0:8080`
- Threads properly configured for concurrency
- Daemon threads prevent blocking shutdown

## Troubleshooting

### Timeout Errors

**Problem**: 500 errors with timeout messages

**Solution**:
- Increase `--timeout` value in deployment command
- Optimize database queries in `src/database.py`
- Check for blocking operations in game logic

### Memory Issues

**Problem**: Out of memory errors

**Solution**:
- Increase `--memory` to 2Gi
- Reduce cache sizes in `src/config.py`
- Implement pagination for large queries

### Connection Refused

**Problem**: "Connection refused" errors

**Solution**:
- Verify DISCORD_TOKEN is valid
- Check SUPABASE_URL and SUPABASE_KEY
- Ensure service account has Secret Manager access

### Bot Offline

**Problem**: Bot shows as offline in Discord

**Solution**:
- Check service logs: `gcloud run services logs read wordle-bot`
- Verify bot status: `/ready` endpoint should return 200
- Check Discord API status
- Ensure bot has required intents enabled

## Monitoring

### Logs

```bash
# Stream logs in real-time
gcloud run services logs read wordle-bot --region us-central1 --limit 50 -f

# Filter by severity
gcloud run services logs read wordle-bot --region us-central1 --limit 50 --severity=ERROR
```

### Metrics

Monitor from Cloud Console:
- Request count
- Error rate
- Latency
- Memory usage
- CPU usage

## Cost Optimization

1. **Set min-instances to 0** for non-critical bots (cold starts ~15s)
2. **Use regional deployment** (cheaper than multi-region)
3. **Monitor memory usage** - don't over-allocate
4. **Use Cloud Scheduler** for periodic tasks instead of background loops

## Local Testing with Docker

```bash
# Build locally
docker build -t wordle-bot-local .

# Run with environment variables
docker run -p 8080:8080 \
  -e DISCORD_TOKEN=your_token \
  -e APP_ID=your_app_id \
  -e SUPABASE_URL=your_url \
  -e SUPABASE_KEY=your_key \
  wordle-bot-local

# Test health endpoint
curl http://localhost:8080/health
```

## Automated Deployment

Create a `.github/workflows/deploy-gcp.yml` for CI/CD:

```yaml
name: Deploy to Cloud Run

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Cloud SDK
        uses: google-github-actions/setup-gcloud@v1
        with:
          service_account_key: ${{ secrets.GCP_SA_KEY }}
          project_id: ${{ secrets.GCP_PROJECT_ID }}
          export_default_credentials: true
      - name: Build and push
        run: |
          gcloud builds submit --tag gcr.io/${{ secrets.GCP_PROJECT_ID }}/wordle-bot:latest
      - name: Deploy
        run: |
          gcloud run deploy wordle-bot \
            --image gcr.io/${{ secrets.GCP_PROJECT_ID }}/wordle-bot:latest \
            --region us-central1 \
            --update-secrets DISCORD_TOKEN=discord-token:latest \
            --update-secrets SUPABASE_URL=supabase-url:latest \
            --update-secrets SUPABASE_KEY=supabase-key:latest \
            --update-secrets APP_ID=discord-app-id:latest
```

## Additional Resources

- [Google Cloud Run Documentation](https://cloud.google.com/run/docs)
- [Cloud Run Best Practices](https://cloud.google.com/run/docs/quickstarts/build-and-deploy)
- [Secret Manager Documentation](https://cloud.google.com/secret-manager/docs)
- [Discord.py Documentation](https://discordpy.readthedocs.io/)
