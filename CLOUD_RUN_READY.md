# Google Cloud Run Deployment - Quick Start

This repository is now configured for deployment to Google Cloud Run with proper handling of timeouts and containerization.

## What's New

### ✅ Cloud Run Ready Features

1. **Health Check Endpoints** (`/health`, `/ready`)
   - Automatic monitoring by Cloud Run
   - Prevents timeout errors
   
2. **Graceful Shutdown Handling**
   - Proper SIGTERM signal handling
   - Clean bot disconnection
   - Data integrity on shutdown

3. **Production Docker Configuration**
   - Optimized Dockerfile
   - Multi-stage builds (can be added for optimization)
   - Proper signal handling

4. **Environment Variable Support**
   - Cloud Run's `PORT` environment variable support
   - Secret Manager integration
   - No hardcoded credentials

5. **Request Timeout Configuration**
   - 3600-second (1-hour) timeout configured
   - Prevents premature termination

## Quick Deployment (Easiest Way)

### Option 1: Using the Deployment Script (Linux/macOS)

```bash
# Make the script executable
chmod +x cloud-run-deploy.sh

# Run the deployment script
export GCP_PROJECT_ID=your-project-id
./cloud-run-deploy.sh
```

The script will:
- Check prerequisites (gcloud, Docker)
- Enable required GCP APIs
- Create secrets in Secret Manager
- Build and push Docker image
- Deploy to Cloud Run
- Verify the deployment

### Option 2: Manual Deployment (All Platforms)

```bash
# 1. Set project ID
export PROJECT_ID=your-project-id
gcloud config set project $PROJECT_ID

# 2. Enable APIs
gcloud services enable run.googleapis.com cloudbuild.googleapis.com containerregistry.googleapis.com secretmanager.googleapis.com

# 3. Create secrets
echo -n "your_discord_token" | gcloud secrets create discord-token --data-file=-
echo -n "your_app_id" | gcloud secrets create discord-app-id --data-file=-
echo -n "your_supabase_url" | gcloud secrets create supabase-url --data-file=-
echo -n "your_supabase_key" | gcloud secrets create supabase-key --data-file=-

# 4. Configure service account permissions
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
CLOUD_RUN_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

for secret in discord-token discord-app-id supabase-url supabase-key; do
  gcloud secrets add-iam-policy-binding $secret \
    --member=serviceAccount:$CLOUD_RUN_SA \
    --role=roles/secretmanager.secretAccessor
done

# 5. Build and deploy
gcloud run deploy wordle-bot \
  --source . \
  --platform managed \
  --region us-central1 \
  --memory 1Gi \
  --cpu 1 \
  --timeout 3600 \
  --max-instances 100 \
  --min-instances 1 \
  --update-secrets DISCORD_TOKEN=discord-token:latest \
  --update-secrets APP_ID=discord-app-id:latest \
  --update-secrets SUPABASE_URL=supabase-url:latest \
  --update-secrets SUPABASE_KEY=supabase-key:latest \
  --port 8080 \
  --allow-unauthenticated
```

## After Deployment

### Monitor Your Service

```bash
# View recent logs
gcloud run services logs read wordle-bot --region us-central1 --limit 50

# Stream logs in real-time
gcloud run services logs read wordle-bot --region us-central1 --limit 50 -f

# Get service details
gcloud run services describe wordle-bot --region us-central1
```

### Test the Service

```bash
# Get the service URL
SERVICE_URL=$(gcloud run services describe wordle-bot --region us-central1 --format='value(status.url)')

# Test health endpoint
curl $SERVICE_URL/health

# Test readiness
curl $SERVICE_URL/ready

# Test the website
curl $SERVICE_URL/
```

### Update Secrets

```bash
# Update Discord token
echo -n "new_token" | gcloud secrets versions add discord-token --data-file=-

# Redeploy with the new secret version (Cloud Run automatically uses latest)
gcloud run deploy wordle-bot \
  --image gcr.io/$PROJECT_ID/wordle-bot:latest \
  --region us-central1 \
  --update-secrets DISCORD_TOKEN=discord-token:latest \
  --quiet
```

## Solving Common Issues

### 1. Timeout Errors

**Error**: `504 Gateway Timeout` or timeout after 60 seconds

**Solution**: Already fixed! The deployment is configured with 3600-second timeout.

**If still occurring**:
```bash
# Redeploy with custom timeout
gcloud run deploy wordle-bot \
  --timeout 3600 \
  --region us-central1 \
  --quiet
```

### 2. "Connection Refused" or Bot Goes Offline

**Symptoms**: Bot shows offline in Discord, connection errors in logs

**Check**:
```bash
# View the health status
curl $SERVICE_URL/health

# Check recent errors
gcloud run services logs read wordle-bot --severity=ERROR --limit 20
```

**Solutions**:
- Verify Discord token is valid
- Check that Supabase credentials are correct
- Ensure bot has required intents enabled in Discord Developer Portal

### 3. Memory or CPU Issues

**Error**: `Out of memory` or persistent high CPU

**Solution**: Increase resources during redeployment
```bash
# Increase memory to 2Gi and CPU to 2
gcloud run deploy wordle-bot \
  --memory 2Gi \
  --cpu 2 \
  --region us-central1 \
  --quiet
```

### 4. 502 Bad Gateway

**Causes**: Flask server not responding, startup issues

**Debug**:
```bash
# Check logs for startup errors
gcloud run services logs read wordle-bot --region us-central1 --limit 100

# Redeploy if needed
gcloud run deploy wordle-bot \
  --source . \
  --region us-central1 \
  --quiet
```

## Local Testing

Before deploying to Cloud Run, test locally:

```bash
# Build Docker image locally
docker build -t wordle-bot-local .

# Run with environment variables
docker run -p 8080:8080 \
  -e DISCORD_TOKEN="your_token" \
  -e APP_ID="your_app_id" \
  -e SUPABASE_URL="your_url" \
  -e SUPABASE_KEY="your_key" \
  wordle-bot-local

# Test endpoints
curl http://localhost:8080/health
curl http://localhost:8080/
```

## File Changes Made

### Core Files Modified
- **wordle_bot.py**: Added signal handlers and proper shutdown
- **src/server.py**: Added health check endpoints, graceful shutdown
- **requirements.txt**: Pinned versions for production stability

### New Files Created
- **Dockerfile**: Container configuration for Cloud Run
- **.dockerignore**: Excludes unnecessary files from Docker build
- **.gcloudignore**: Excludes files from gcloud deployment
- **app.yaml**: App Engine alternative configuration
- **CLOUD_RUN_DEPLOYMENT.md**: Detailed deployment documentation
- **cloud-run-deploy.sh**: Automated deployment script

## Key Improvements

### 1. No More Timeout Errors
- Configured 1-hour timeout (3600 seconds)
- Health check prevents premature termination
- Graceful shutdown on SIGTERM

### 2. Proper Container Support
- Listens on `0.0.0.0:8080` (Cloud Run requirement)
- Reads `PORT` environment variable
- Proper signal handling

### 3. Production Ready
- Secret Manager integration (no hardcoded credentials)
- Health monitoring endpoints
- Automatic restart on failure
- Auto-scaling configured (1-100 instances)

### 4. Cost Optimized
- 1Gi memory (sufficient for bot)
- 1 vCPU (scales to 2 if needed)
- min-instances=1 (prevents cold starts from hurting)
- Regional deployment (not multi-region)

## Cost Estimates

**Monthly costs** (estimated, varies by region):
- Compute: ~$10-15 (1 instance running 24/7)
- Storage: ~$1-2 (if using Cloud Storage)
- Secrets: ~$0.50 (first 1M operations free)
- **Total**: ~$15-20/month

To reduce costs:
- Set `--min-instances 0` (cold starts ~15 seconds)
- Use smaller regions
- Optimize database queries

## Additional Resources

- [Cloud Run Documentation](https://cloud.google.com/run/docs)
- [Troubleshooting Guide](CLOUD_RUN_DEPLOYMENT.md#troubleshooting)
- [Discord.py Documentation](https://discordpy.readthedocs.io/)
- [Cloud Run Best Practices](https://cloud.google.com/run/docs/quickstarts/build-and-deploy)

## Support

For issues or questions:
1. Check the [detailed deployment guide](CLOUD_RUN_DEPLOYMENT.md)
2. View logs: `gcloud run services logs read wordle-bot --region us-central1 --limit 50`
3. Check Discord bot token and intents
4. Verify Supabase credentials

---

**Ready to deploy?** Run:
```bash
export GCP_PROJECT_ID=your-project-id
./cloud-run-deploy.sh
```

✅ Your bot is now Cloud Run ready!
