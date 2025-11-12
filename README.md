Mars Weather — Curiosity (REMS) via MAAS

A small FastAPI service that proxies and normalises Mars weather from the community MAAS API (Curiosity/REMS), plus a static frontend in /docs for GitHub Pages. The backend exposes simple JSON endpoints, and the frontend lets you view the latest or a specific sol with unit toggles and a copy-to-clipboard helper.

Features

Normalised JSON: temps (°C), pressure (Pa), sunrise/sunset, UV, season

Endpoints: /weather/latest and /weather/{sol}

CORS enabled for browser use; simple in-memory caching

GitHub Pages frontend (in /docs) with clean UI and copy button

Quick start

Deploy backend on Render (requirements.txt + Procfile at repo root)

Publish frontend from /docs via GitHub Pages

Open the Pages site, paste your Render URL, and fetch data



[URL](https://gsopas.github.io/mars-maas/)

![.gif](docs/mars.gif)
