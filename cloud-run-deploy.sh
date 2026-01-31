#!/bin/bash

# Google Cloud Run Deployment Script
# This script automates the deployment of the Wordle Bot to Google Cloud Run

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
PROJECT_ID="${GCP_PROJECT_ID:-}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="wordle-bot"
IMAGE_NAME="wordle-bot"

# Functions
print_header() {
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}$1${NC}"
    echo -e "${GREEN}========================================${NC}"
}

print_error() {
    echo -e "${RED}❌ ERROR: $1${NC}"
    exit 1
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}ℹ️ $1${NC}"
}

# Check prerequisites
check_prerequisites() {
    print_header "Checking Prerequisites"
    
    # Check if gcloud is installed
    if ! command -v gcloud &> /dev/null; then
        print_error "gcloud CLI is not installed. Please install it first."
    fi
    print_success "gcloud CLI found"
    
    # Check if docker is installed
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed. Please install it first."
    fi
    print_success "Docker found"
    
    # Check if PROJECT_ID is set
    if [ -z "$PROJECT_ID" ]; then
        PROJECT_ID=$(gcloud config get-value project)
        if [ -z "$PROJECT_ID" ]; then
            print_error "GCP_PROJECT_ID not set and no default project configured."
        fi
    fi
    print_success "Project ID: $PROJECT_ID"
}

# Create secrets
create_secrets() {
    print_header "Setting Up Secrets"
    
    read -p "Enter Discord Bot Token: " DISCORD_TOKEN
    read -p "Enter Discord App ID: " APP_ID
    read -p "Enter Supabase URL: " SUPABASE_URL
    read -p "Enter Supabase Key: " SUPABASE_KEY
    
    # Check if secrets already exist
    if ! gcloud secrets describe discord-token --project=$PROJECT_ID &> /dev/null; then
        echo -n "$DISCORD_TOKEN" | gcloud secrets create discord-token --data-file=- --project=$PROJECT_ID
        print_success "Created discord-token secret"
    else
        print_info "discord-token secret already exists"
        read -p "Update it? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo -n "$DISCORD_TOKEN" | gcloud secrets versions add discord-token --data-file=- --project=$PROJECT_ID
            print_success "Updated discord-token secret"
        fi
    fi
    
    if ! gcloud secrets describe discord-app-id --project=$PROJECT_ID &> /dev/null; then
        echo -n "$APP_ID" | gcloud secrets create discord-app-id --data-file=- --project=$PROJECT_ID
        print_success "Created discord-app-id secret"
    else
        print_info "discord-app-id secret already exists"
    fi
    
    if ! gcloud secrets describe supabase-url --project=$PROJECT_ID &> /dev/null; then
        echo -n "$SUPABASE_URL" | gcloud secrets create supabase-url --data-file=- --project=$PROJECT_ID
        print_success "Created supabase-url secret"
    else
        print_info "supabase-url secret already exists"
    fi
    
    if ! gcloud secrets describe supabase-key --project=$PROJECT_ID &> /dev/null; then
        echo -n "$SUPABASE_KEY" | gcloud secrets create supabase-key --data-file=- --project=$PROJECT_ID
        print_success "Created supabase-key secret"
    else
        print_info "supabase-key secret already exists"
    fi
}

# Enable required APIs
enable_apis() {
    print_header "Enabling Required APIs"
    
    gcloud services enable run.googleapis.com --project=$PROJECT_ID
    gcloud services enable cloudbuild.googleapis.com --project=$PROJECT_ID
    gcloud services enable containerregistry.googleapis.com --project=$PROJECT_ID
    gcloud services enable secretmanager.googleapis.com --project=$PROJECT_ID
    
    print_success "APIs enabled"
}

# Configure service account
configure_service_account() {
    print_header "Configuring Service Account"
    
    PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
    CLOUD_RUN_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
    
    for secret in discord-token discord-app-id supabase-url supabase-key; do
        gcloud secrets add-iam-policy-binding $secret \
            --member=serviceAccount:$CLOUD_RUN_SA \
            --role=roles/secretmanager.secretAccessor \
            --project=$PROJECT_ID \
            --quiet 2>/dev/null || true
    done
    
    print_success "Service account configured"
}

# Build and push image
build_and_push() {
    print_header "Building and Pushing Docker Image"
    
    IMAGE_URL="gcr.io/$PROJECT_ID/$IMAGE_NAME:latest"
    
    print_info "Building Docker image..."
    gcloud builds submit \
        --tag $IMAGE_URL \
        --project=$PROJECT_ID \
        --quiet
    
    print_success "Image built and pushed: $IMAGE_URL"
    echo $IMAGE_URL
}

# Deploy to Cloud Run
deploy() {
    IMAGE_URL="$1"
    
    print_header "Deploying to Cloud Run"
    
    gcloud run deploy $SERVICE_NAME \
        --image $IMAGE_URL \
        --platform managed \
        --region $REGION \
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
        --allow-unauthenticated \
        --project=$PROJECT_ID \
        --quiet
    
    print_success "Service deployed"
}

# Verify deployment
verify_deployment() {
    print_header "Verifying Deployment"
    
    SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
        --platform managed \
        --region $REGION \
        --format='value(status.url)' \
        --project=$PROJECT_ID)
    
    print_success "Service URL: $SERVICE_URL"
    
    print_info "Testing health endpoint..."
    sleep 5  # Wait for service to be ready
    
    if curl -s "${SERVICE_URL}/health" | grep -q "healthy"; then
        print_success "Health check passed!"
    else
        print_error "Health check failed. Check logs with: gcloud run services logs read $SERVICE_NAME --region $REGION"
    fi
    
    echo ""
    print_header "Deployment Complete!"
    echo -e "Service URL: ${GREEN}$SERVICE_URL${NC}"
    echo -e "View logs: ${YELLOW}gcloud run services logs read $SERVICE_NAME --region $REGION --limit 50${NC}"
}

# Main execution
main() {
    print_header "Wordle Bot - Google Cloud Run Deployment"
    
    check_prerequisites
    enable_apis
    create_secrets
    configure_service_account
    
    IMAGE_URL=$(build_and_push)
    deploy "$IMAGE_URL"
    verify_deployment
}

# Run main function
main "$@"
