# Cloud Run Deployment Guide

To ensure your Discord bot stays online and doesn't go "dead," you must configure Google Cloud Run with specific settings. 

## 1. Prerequisites
- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) installed.
- Docker installed (if building locally).
- Your `DISCORD_TOKEN`, `SUPABASE_URL`, and `SUPABASE_KEY` ready.

## 2. Deployment Command
Run the following command in your terminal. This command includes the critical flags to keep the bot alive:

```bash
gcloud run deploy wordle-bot \
  --source . \
  --platform managed \
  --region YOUR_REGION \
  --allow-unauthenticated \
  --set-env-vars DISCORD_TOKEN=your_token_here,SUPABASE_URL=your_url,SUPABASE_KEY=your_key \
  --min-instances 1 \
  --no-cpu-throttling \
  --cpu 1 \
  --memory 1Gi \
  --port 8080
```

### Why these flags?
*   `--min-instances 1`: Cloud Run usually scales to 0 when there is no web traffic. Discord bots require at least one instance to stay connected to Discord's Gateway.
*   `--no-cpu-throttling`: **CRITICAL**. By default, Cloud Run only gives CPU power when an HTTP request is being processed. Discord bots run on WebSockets in the background, so you must tell Google to give the bot CPU power even when no one is visiting the website.
*   `--cpu 1` & `--memory 1Gi`: Standard resources for a medium-sized bot.

## 3. Alternative: Deploy via Console
If you prefer using the Google Cloud Console UI:
1.  Go to **Cloud Run** -> **Create Service**.
2.  Choose "Deploy one revision from an existing container image" (or use the Cloud Build integration).
3.  Under **Container, Networking, Security**:
    *   **Capacity**: Set CPU to 1 and Memory to at least 512MB/1GB.
    *   **CPU allocation**: Select **"CPU is always allocated"**.
4.  Under **Scaling**:
    *   **Min number of instances**: Set to **1**.
5.  Under **Variables**:
    *   Add `DISCORD_TOKEN`, `SUPABASE_URL`, `SUPABASE_KEY`.
