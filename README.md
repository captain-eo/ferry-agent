# Fauntleroy to Vashon School Commute Ferry Tracker 🚢🎒

A serverless, real-time commute intelligence system for families and students traveling between **Fauntleroy (West Seattle)** and **Vashon Island Public Schools** (Vashon High School, McMurray Middle School, and Chautauqua Elementary).

Powered by **AWS Lambda**, **Google Gemini Flash**, and hosted as a static 1-page responsive application on **AWS S3**.

---

## Key Features

- **Automated WSDOT Telemetry Ingestion**:
  - Live vessel GPS tracking, speed, heading, and dock status from the unauthenticated WSDOT REST API (`/vessellocations`).
  - Real-time delay flags and ETA basis from VesselWatch (`Vessels.ashx`).
  - Daily scheduled timetables for Route 14 (Fauntleroy / Vashon).
- **Intelligent Bulletin & Disruption Scraping**:
  - Automatically parses `wsdot.com/ferries/schedule/Bulletin.aspx`.
  - Dynamically detects whether the route is running a **Three-Boat Schedule (Normal)** or a **Two-Boat Contingency Schedule** (e.g., due to maintenance or vessel breakdowns).
- **VISD School Commuter Rules Engine**:
  - **Friday Late Start (PDD)**: Automatically recognizes Friday Professional Development Days and shifts target departures (8:25 AM for VHS/McM, 9:30 AM for CES).
  - **Sports & Activity Buses**: Recommends the later sports bus sailings (4:40 PM and 5:40 PM) for students participating in after-school athletics or clubs.
  - **Elementary (CES) Chaperone Coordination**: Flags chaperoned sailings for K-5 students.
  - **Bus Connection Catchability**: Evaluates whether delayed ferries will connect safely with the VISD shuttle buses waiting at the north-end Vashon terminal.
- **Gemini Flash AI Synthesis**:
  - Uses `gemini-2.5-flash` to generate plain-English, reassuring advisories for parents and students.
  - Includes a built-in deterministic heuristic fallback engine, ensuring 100% uptime even if API quotas or keys are absent.
- **Fast 1-Page Static Dashboard (AWS S3)**:
  - Mobile-first, responsive interface (Tailwind CSS, Lucide Icons, Leaflet interactive map).
  - Automatic client-side background polling (`data.json`) every 45 seconds with zero full-page reloads.

---

## Project Structure

```
ferry-agent/
├── README.md
├── requirements.txt            # Python dependencies (beautifulsoup4, boto3)
├── .env.example                # Example environment variables
├── src/
│   ├── config.py               # VISD bell schedules, Friday PDD rules, endpoints
│   ├── collectors/
│   │   ├── wsdot_api.py        # WSDOT REST APIs (vessellocations, schedule, VesselWatch)
│   │   └── bulletin_scraper.py # Scrapes Bulletin.aspx for 2-boat vs 3-boat status
│   ├── analyzer/
│   │   ├── gemini_analyzer.py  # Google Gemini Flash prompt & JSON client
│   │   └── heuristic_analyzer.py # Deterministic delay math & bus catchability logic
│   ├── generator/
│   │   ├── template.html       # Mobile-first responsive UI template
│   │   └── site_generator.py   # Injects data into index.html & data.json
│   ├── lambda_handler.py       # AWS Lambda entrypoint for scheduled invocations
│   └── local_runner.py         # Local development runner and HTTP server
├── infra/
│   ├── template.yaml           # AWS SAM / CloudFormation template (S3 + Lambda + Cron)
│   └── deploy.sh               # One-click deployment script
├── dist/                       # Generated static assets (index.html, data.json)
└── tests/
    └── test_collectors.py      # Unit & integration tests
```

---

## Quickstart (Local Testing & Preview)

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment (Optional)
Copy `.env.example` to `.env` and provide your Google Gemini API key:
```bash
cp .env.example .env
export GEMINI_API_KEY="your_api_key_here"
```
*(Note: If no API key is provided, the tracker automatically uses the deterministic heuristic engine).*

### 3. Run and Preview Dashboard Locally
```bash
python3 src/local_runner.py --port 8000
```
Open your browser to: **`http://localhost:8000`**

To generate the static bundle once without starting a server:
```bash
python3 src/local_runner.py --once
```

### 4. Run Unit Tests
```bash
python3 -m unittest discover tests
```

---

## AWS Deployment (S3 + Lambda + EventBridge)

The infrastructure is defined in `infra/template.yaml` using AWS SAM / CloudFormation:
- **AWS S3 Bucket**: Configured for public static website hosting (`index.html`, `data.json`).
- **AWS Lambda Function**: Python 3.12 runtime with IAM permissions to upload static files to the S3 bucket.
- **Amazon EventBridge Rule**: Triggers the Lambda every 4 minutes to refresh live data and AI predictions.

### Automated Deployment:
```bash
export GEMINI_API_KEY="your_api_key"
export AWS_REGION="us-west-2"
./infra/deploy.sh
```

---

## VISD Commuter Reference Schedule

| Commute Window | Target School Group | 3-Boat (Normal) Sailing | 2-Boat Contingency Sailing | Bus Connection |
|---|---|---|---|---|
| **AM Regular (M–Th)** | **VHS / McMurray Middle** | **7:05 AM** (Dep Fauntleroy) | **7:20 AM** (M-W) / **6:45 AM** (Thu) | Meets bus at Vashon dock (~7:35 AM) |
| **AM Regular (M–Th)** | **Chautauqua Elementary (CES)** | **8:05 AM** (Dep Fauntleroy) | **8:15 AM** (Dep Fauntleroy) | Chaperoned; meets bus at dock (~8:35 AM) |
| **AM Friday Late Start (PDD)** | **VHS / McMurray Middle** | **8:25 AM** (Dep Fauntleroy) | **8:15 AM** (Dep Fauntleroy) | Meets bus at dock (~8:55 AM) |
| **AM Friday Late Start (PDD)** | **Chautauqua Elementary (CES)** | **9:30 AM** (Dep Fauntleroy) | **9:30 AM** (Dep Fauntleroy) | Chaperoned; meets bus at dock (~10:00 AM) |
| **PM Regular Dismissal** | **VHS / McMurray Middle** | **3:25 PM** (Dep Vashon) | **3:25 PM** (Dep Vashon) | Bus drops off at Vashon dock |
| **PM Regular Dismissal** | **Chautauqua Elementary (CES)** | **4:40 PM** (Dep Vashon) | **4:40 PM** (Dep Vashon) | Chaperoned return ferry |
| **PM Sports & Activities** | **Athletics / Late Bus** | **4:40 PM** or **5:40 PM** | **4:40 PM** or **5:40 PM** | Late activity bus drops off at dock |
