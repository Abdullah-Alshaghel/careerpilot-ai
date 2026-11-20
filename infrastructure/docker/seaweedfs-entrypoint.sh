#!/bin/sh
# S3-compatible object storage for local development (SeaweedFS).
# Credentials are rendered from environment variables at start-up so they
# never live in the repository.
set -eu
: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"

cat > /tmp/s3.json <<EOF
{
  "identities": [
    {
      "name": "careerpilot-services",
      "credentials": [{ "accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}" }],
      "actions": ["Read", "Write", "List", "Tagging", "Admin"]
    }
  ]
}
EOF

# Volume slots: with -volume.max=0 SeaweedFS derives the count from free disk
# divided by the volume size (30 GB by default), which gave only 2 slots on a
# CI runner. Each bucket needs its own, so the second bucket got "No writable
# volume". Small volumes and a fixed count avoid that; volumes grow on demand,
# so nothing is preallocated.
exec weed server -dir=/data -ip.bind=0.0.0.0 -s3 -s3.port=8333 -s3.config=/tmp/s3.json \n  -master.volumeSizeLimitMB=1024 -volume.max=64
