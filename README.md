<img width="1280" height="200" alt="Github-Banner_spot" src="https://github.com/user-attachments/assets/bec5a984-2f1f-44e7-b50d-cc6354d823cd" />

# 🗄️ SPOT OSM Query API

This repository contains the **geospatial query engine** of the SPOT system.  
It receives structured YAML queries and executes them against a **PostGIS database** containing OpenStreetMap (OSM) data.

---

## 🚀 Quickstart


Build the docker container:

```bash
cp .env.example .env
docker build . -t osmapi
```

Run the API using Docker:

```bash
docker run -p 5000:5000 \
  -e DATABASE_NAME='osmgermany' \
  -e DATABASE_USER='postgres' \
  -e DATABASE_PASSWORD='postgres' \
  -e DATABASE_HOST='host.docker.internal' \
  -e DATABASE_PORT='5432' \
  -e TABLE_VIEW='germany' \
  -e JWT_SECRET='your_jwt_secret' \
  osmapi
```

---

## ⚙️ Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_NAME` | Name of the PostGIS database containing OSM data. |
| `TABLE_VIEW` | View or table within the DB to query from. |
| `DATABASE_USER` | Username for the DB connection. |
| `DATABASE_PASSWORD` | **Secret**: Password for the DB. |
| `DATABASE_HOST` | Host address of the DB server. |
| `DATABASE_PORT` | Port for the DB connection. |
| `JWT_SECRET` | **Secret**: Secret used for API auth via JWT. |
| `PORT` | Port to expose the API on (default: `5000`). |

> **🔒 Security Note:** Store sensitive information in a `.env` file. Never commit secrets to version control.

---

## 🔑 Features

- Parses structured SPOT YAML and executes geospatial queries
- Uses spatial indexing via **PostGIS**
- Returns precise geographic object matches (with metadata & geometry)
- Supports query filtering based on entities, properties, and spatial relationships

---

## 🔌 API Endpoints

### POST `/run-osm-query`

Input: Accepts POST requests with the intermediate representation (minimized version).

Output: Returns the result in GeoJSON format along with statistics.

### Authentication

All endpoints require a valid JWT token for authentication. The token should be included in the `Authorization` header of each request in the following format:

`Authorization: Bearer <your_jwt_token>`

---

## 🛠️ Architecture & Workflow

For a visual representation of how the service functions, please refer to the architectural diagram:

![workflow OT++ query service](https://github.com/dw-innovation/kid2-ot-osm-api/assets/6747121/ad5fef02-6e6c-4a0d-97c4-03dfde833122)

---

## 🧩 Part of the SPOT System

This module is typically called by:
- [`central-nlp-api`](https://github.com/dw-innovation/kid2-spot-central-nlp-api) — which translates natural language into SPOT YAML and handles orchestration
- [`frontend`](https://github.com/dw-innovation/kid2-spot-frontend) — to display query results on a map

---

## 🔗 Related Docs

- [Main SPOT Repo](https://github.com/dw-innovation/kid2-spot)
- [Central NLP API](https://github.com/dw-innovation/kid2-spot-central-nlp-api)
- [Frontend UI](https://github.com/dw-innovation/kid2-spot-frontend)

---

## 🙌 Contributing

We welcome contributions of all kinds — from developers, journalists, mappers, and more!  
See [CONTRIBUTING.md](https://github.com/dw-innovation/kid2-spot/blob/main/CONTRIBUTING.md) for how to get started.
Also see our [Code of Conduct](https://github.com/dw-innovation/kid2-spot/blob/main/CODE_OF_CONDUCT.md).

---

## 📜 License

Licensed under [AGPLv3](../LICENSE).
