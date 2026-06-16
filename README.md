# Hospital Automation Engine
**External Automation Layer for Ayurvedic + Integrative Medicine ERP**

FastAPI · LiveKit Cloud · PostgreSQL · ERPNext Bridge

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  External Automation Engine              │
│                       (FastAPI)                          │
│                                                          │
│  ┌──────────┐  ┌─────────────┐  ┌──────────────────┐   │
│  │  Leads   │  │ Orientation │  │   Event Logger   │   │
│  │   CRM    │  │   Engine    │  │  (Audit Trail)   │   │
│  └──────────┘  └─────────────┘  └──────────────────┘   │
│         │             │                   │              │
│         └─────────────┴───────────────────┘              │
│                       │                                  │
│               ┌───────┴───────┐                          │
│               │  ERP Bridge   │                          │
│               └───────────────┘                          │
└────────────────────────┬────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
   ┌───────────┐  ┌───────────┐  ┌───────────────┐
   │ LiveKit   │  │PostgreSQL │  │   ERPNext     │
   │  Cloud    │  │(Events+DB)│  │(Frappe ERP)   │
   └───────────┘  └───────────┘  └───────────────┘
```

---

## Folder Structure

```
hospital-automation/
├── app/
│   ├── main.py                    # FastAPI app, lifespan, router mounting
│   ├── config/
│   │   ├── settings.py            # All env-driven config (Pydantic Settings)
│   │   └── database.py            # Async SQLAlchemy engine + session
│   ├── leads/
│   │   ├── models.py              # Lead ORM + Pydantic schemas
│   │   ├── service.py             # Lead business logic
│   │   └── router.py              # Lead HTTP endpoints
│   ├── orientation/
│   │   ├── models.py              # Session + Participant ORM
│   │   ├── service.py             # Attendance engine (70% rule)
│   │   └── router.py              # Session HTTP endpoints
│   ├── livekit/
│   │   ├── client.py              # LiveKit SDK wrapper
│   │   └── router.py              # Webhook handler
│   ├── events/
│   │   ├── logger.py              # Event types + structured logger
│   │   └── router.py              # Audit log query endpoints
│   └── erp_bridge/
│       ├── service.py             # ERPNext API integration
│       └── router.py              # Manual sync endpoints
├── frontend/
│   └── orientation_meet/
│       ├── index.html             # Patient meeting UI
│       ├── app.js                 # LiveKit JS SDK integration
│       └── styles.css             # Organic medical dark theme
├── alembic/
│   └── env.py                     # Async Alembic migration config
├── requirements.txt
├── .env.example
└── README.md
```

---

## Setup Instructions

### 1. Prerequisites

- Python 3.11+
- PostgreSQL 14+
- LiveKit Cloud account → https://cloud.livekit.io
- (Later) ERPNext instance

### 2. Clone & Install

```bash
git clone <repo>
cd hospital-automation
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your LiveKit keys, DB URL, etc.
```

### 4. Database Setup

```bash
# Create DB
createdb hospital_automation

# Run migrations (creates all tables)
alembic upgrade head
```

### 5. Start Server

```bash
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs  
Meeting UI: http://localhost:8000/meet

---

## API Quick Reference

### Leads

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/leads/` | Create lead |
| GET | `/api/v1/leads/` | List leads |
| GET | `/api/v1/leads/{id}` | Get lead |
| PATCH | `/api/v1/leads/{id}/status` | Update status |
| GET | `/api/v1/leads/{id}/eligibility` | Check appointment eligibility |

### Orientation Sessions

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/orientation/sessions` | Create session + LiveKit room |
| GET | `/api/v1/orientation/sessions` | List sessions |
| POST | `/api/v1/orientation/sessions/{id}/participants` | Add participant |
| POST | `/api/v1/orientation/sessions/{id}/token` | Generate join token |
| POST | `/api/v1/orientation/sessions/{id}/host-token` | Generate host token |
| POST | `/api/v1/orientation/sessions/{id}/start` | Start session |
| POST | `/api/v1/orientation/sessions/{id}/end` | End session |

### LiveKit Webhook

Configure this URL in LiveKit Cloud dashboard:
```
POST https://your-domain.com/api/v1/livekit/webhook
```

---

## Example API Requests

### Create a Lead

```bash
curl -X POST http://localhost:8000/api/v1/leads/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Arjun Sharma",
    "phone": "+91-9876543210",
    "email": "arjun@example.com",
    "lead_source": "WEBSITE",
    "interested_in": "CONSULTATION"
  }'
```

### Create an Orientation Session

```bash
curl -X POST http://localhost:8000/api/v1/orientation/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Morning Orientation — Week 23",
    "scheduled_at": "2025-06-10T09:00:00Z"
  }'
```

### Add a Lead as Participant

```bash
curl -X POST http://localhost:8000/api/v1/orientation/sessions/{SESSION_ID}/participants \
  -H "Content-Type: application/json" \
  -d '{
    "lead_id": "{LEAD_ID}",
    "lead_name": "Arjun Sharma"
  }'
```

### Generate Patient Token

```bash
curl -X POST http://localhost:8000/api/v1/orientation/sessions/{SESSION_ID}/token \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "{SESSION_ID}",
    "lead_id": "{LEAD_ID}",
    "lead_name": "Arjun Sharma"
  }'
```

---

## Testing the Full Flow

```
1. POST /api/v1/leads/              → Create lead, note lead_id
2. POST /api/v1/orientation/sessions → Create session, note session_id
3. POST /sessions/{id}/participants  → Register lead
4. POST /sessions/{id}/start         → Mark session LIVE
5. POST /sessions/{id}/token         → Get join token
6. Open http://localhost:8000/meet   → Enter token details and join
7. Wait for LiveKit webhooks to fire participant_joined / participant_left
8. POST /sessions/{id}/end           → End session, triggers attendance calc
9. GET /api/v1/leads/{lead_id}/eligibility → Should return appointment_eligible: true
```

---

## LiveKit Webhook Setup

1. Go to LiveKit Cloud Dashboard → Project Settings → Webhooks
2. Add URL: `https://your-domain.com/api/v1/livekit/webhook`
3. Select events: `participant_joined`, `participant_left`, `room_started`, `room_finished`
4. Copy the signing secret to `LIVEKIT_WEBHOOK_SECRET` in `.env`

For local development, use [ngrok](https://ngrok.com):
```bash
ngrok http 8000
# Use the https URL as your webhook endpoint
```

---

## Attendance Completion Rule

The 70% threshold is configurable via `ORIENTATION_COMPLETION_THRESHOLD` in `.env`.

When a patient's `watch_seconds / session_duration_seconds >= 0.70`:
- `OrientationParticipant.attendance_status` → `COMPLETED`
- `Lead.status` → `ORIENTATION_ATTENDED`
- ERP Bridge notified to create `SGP Orientation Attendance`
- `appointment_eligible` → `true`

---

## ERPNext Integration (Placeholder)

The ERP bridge is pre-wired but runs in placeholder mode until you configure `ERPNEXT_API_KEY`.

When ready:
1. Create the `SGP Orientation Attendance` DocType in ERPNext
2. Add fields: `lead_id`, `orientation_session`, `attendance_status`, `watch_time_seconds`
3. Set `ERPNEXT_BASE_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` in `.env`
4. The system will automatically POST to ERPNext on orientation completion

---

## Production Deployment

```bash
# With Gunicorn + Uvicorn workers
pip install gunicorn
gunicorn app.main:app \
  -w 4 \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --access-logfile - \
  --error-logfile -
```

Recommended: Deploy behind **Nginx** with **SSL** (required for camera/mic access in browsers).
