#!/bin/bash
set -e

# Get a new access token
echo "Fetching new access token via gcloud..."
ACCESS_TOKEN=$(gcloud auth print-access-token)

if [ -z "$ACCESS_TOKEN" ]; then
    echo "Error: Failed to retrieve access token. Please run 'gcloud auth login' first."
    exit 1
fi

NPMRC_FILE="$HOME/.npmrc"
echo "Updating $NPMRC_FILE with new token..."

REGISTRIES=(
    "//us-west1-npm.pkg.dev/gemini-code-dev/gemini-code/"
    "//us-npm.pkg.dev/artifact-foundry-prod/npm-3p-trusted/"
    "//us-npm.pkg.dev/artifact-foundry-prod/ah-3p-staging-npm/"
)

# Check if .npmrc exists
if [ ! -f "$NPMRC_FILE" ]; then
    echo "Creating $NPMRC_FILE..."
    touch "$NPMRC_FILE"
fi

# Function to escape slashes for sed
escape_sed() {
    echo "$1" | sed 's/[\/&]/\\&/g'
}

for REGISTRY in "${REGISTRIES[@]}"; do
    
    ESCAPED_REGISTRY=$(escape_sed "$REGISTRY")
    NEW_LINE="${REGISTRY}:_authToken=${ACCESS_TOKEN}"
    
    if grep -qF "$REGISTRY" "$NPMRC_FILE"; then
        sed -i "s|${REGISTRY}:_authToken=.*|${NEW_LINE}|" "$NPMRC_FILE"
    else
        echo "$NEW_LINE" >> "$NPMRC_FILE"
    fi
    
    if ! grep -qF "${REGISTRY}:always-auth=true" "$NPMRC_FILE"; then
        echo "${REGISTRY}:always-auth=true" >> "$NPMRC_FILE"
    fi
    
done

echo "NPM authentication updated successfully."
