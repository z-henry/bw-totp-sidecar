# bw-totp-sidecar

A lightweight sidecar service built on top of the Bitwarden CLI that exposes TOTP codes via a simple HTTP interface.

This project is designed for automation scenarios such as Node-RED, where interactive authentication (2FA / Passkey) is not feasible, but secure access to TOTP is still required.

## Features

- Uses official Bitwarden CLI (`bw`)
- Supports self-hosted Bitwarden and Vaultwarden
- Authenticates via Personal API Key (`bw login --apikey`)
- Strict item name matching
- Session caching to avoid repeated unlocks
- Simple HTTP endpoints:
  - `GET /health`
  - `GET /otp`
- Optional request authentication via HTTP header
- Container-friendly (Docker / docker-compose)

## Typical Use Case

Node-RED  
↓  
HTTP request (/otp)  
↓  
bw-totp-sidecar  
↓  
Bitwarden / Vaultwarden  
Used together with services that require TOTP-based login (e.g. MoviePilot, internal services, automation APIs).

## Security Model

- Secrets are never exposed by Bitwarden APIs
- TOTP is generated locally via Bitwarden CLI
- Master password is optional (for fully automated unlock)
- Designed to run in a trusted internal network

## Disclaimer

This project is intended for personal or internal automation use.
Do NOT expose the service directly to the public internet.
