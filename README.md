# Price Drop Notifier

A serverless AWS application that monitors product prices and emails subscribers when prices drop. Built as a portfolio project demonstrating **fan-out notification patterns** with SNS, SES, Lambda, DynamoDB, and API Gateway.

---

## Architecture

```
User submits URL + email
        │
        ▼
  API Gateway (POST /subscribe)
        │
        ▼
  Lambda: subscribe
    ├─ Scrapes product URL for name + price
    ├─ Stores product in DynamoDB
    ├─ Creates subscription record
    └─ Sends welcome email via SES
        │
        ▼ (every hour)
  EventBridge Schedule
        │
        ▼
  Lambda: scraper
    ├─ Queries all active subscriptions
    ├─ Scrapes fresh price for each product
    └─ If price dropped → publishes to SNS topic
              │
              ▼  (fan-out)
        SNS Topic: price-drop-events
              │
              ▼
        Lambda: notifier
          ├─ Queries DynamoDB for all subscribers of that product
          └─ Sends personalised price-drop email to each subscriber via SES

User clicks "Unsubscribe" link in any email
        │
        ▼
  API Gateway (GET /unsubscribe?token=<token>)
        │
        ▼
  Lambda: unsubscribe
    ├─ Looks up subscription by token
    ├─ Marks subscription inactive
    └─ Returns HTML confirmation page
```

### AWS Services Used

| Service | Role |
|---|---|
| **Lambda** | Subscribe, scrape, notify, unsubscribe functions |
| **API Gateway** | REST API for subscribe and unsubscribe endpoints |
| **DynamoDB** | Stores product prices + subscriber records |
| **SNS** | Fan-out hub — one price-drop event → N email notifications |
| **SES** | Sends HTML emails (welcome + price drop alerts) |
| **EventBridge** | Triggers the scraper Lambda on a schedule (default: every hour) |

---

## Project Structure

```
price-drop-notifier/
├── backend/
│   ├── template.yaml                    # AWS SAM infrastructure template
│   ├── layers/
│   │   └── utils/
│   │       ├── requirements.txt         # Shared Python dependencies
│   │       └── python/
│   │           ├── scraper_utils.py     # Multi-strategy price scraper
│   │           └── email_utils.py       # HTML email template builder
│   └── functions/
│       ├── subscribe/handler.py         # POST /subscribe
│       ├── scraper/handler.py           # Scheduled price checker
│       ├── notifier/handler.py          # SNS → SES fan-out
│       └── unsubscribe/handler.py       # GET /unsubscribe
├── frontend/
│   ├── index.html                       # Single-page UI
│   ├── styles.css
│   └── app.js                           # Fetch calls to API Gateway
├── scripts/
│   ├── setup.sh                         # One-time SES verification
│   └── deploy.sh                        # SAM build + deploy
└── env.example                          # Environment variable reference
```

---

## Prerequisites

| Tool | Install |
|---|---|
| AWS CLI | [docs.aws.amazon.com/cli](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |
| AWS SAM CLI | `pip install aws-sam-cli` or `brew install aws-sam-cli` |
| Docker | Required by `sam build --use-container` |
| Python 3.11 | Only needed for local testing |

---

## Deployment

### 1. Configure AWS credentials

```bash
aws configure
# Enter your Access Key ID, Secret Access Key, region (e.g. us-east-1)
```

### 2. One-time setup (SES email verification)

```bash
chmod +x scripts/setup.sh scripts/deploy.sh

SENDER_EMAIL=you@example.com ./scripts/setup.sh
```

Check your inbox and **click the verification link** AWS sends you. This is required before SES will send any emails.

> **SES Sandbox**: New accounts can only send to verified addresses. To send to anyone, request production access in the AWS Console under **SES → Account dashboard → Request production access**.

### 3. Deploy

```bash
SENDER_EMAIL=you@example.com ./scripts/deploy.sh
```

The script will output your **API URL**. Copy it.

### 4. Wire up the frontend

Open `frontend/app.js` and replace the placeholder:

```js
const API_BASE_URL = 'https://REPLACE_ME.execute-api.us-east-1.amazonaws.com/Prod';
//                              ^^^^^^^^^^
//  Paste your actual API URL here
```

### 5. Host the frontend

The `frontend/` folder is a plain static site — no build step needed.

| Option | Command / Notes |
|---|---|
| **S3 Static Website** | `aws s3 sync frontend/ s3://your-bucket --acl public-read` |
| **GitHub Pages** | Push to `gh-pages` branch |
| **Netlify / Vercel** | Drop-and-deploy the `frontend/` folder |
| **Local dev** | `npx serve frontend` or `python -m http.server -d frontend 8080` |

---

## Configuration

Copy `env.example` to `.env` (see `.gitignore`) and customise:

| Variable | Default | Description |
|---|---|---|
| `AWS_REGION` | `us-east-1` | AWS region for all resources |
| `SENDER_EMAIL` | _(required)_ | SES-verified sender address |
| `STACK_NAME` | `price-drop-notifier` | CloudFormation stack name |
| `ENVIRONMENT` | `prod` | `dev` or `prod` (affects resource names) |
| `SCRAPER_API_KEY` | _(empty)_ | Optional ScraperAPI key (see below) |

---

## Scraping & Limitations

The scraper uses a layered strategy:

1. **JSON-LD Schema.org** — most e-commerce sites embed structured product data
2. **OpenGraph meta tags** — `og:price:amount`, `product:price:amount`
3. **CSS selector heuristics** — common class/id patterns (`[itemprop="price"]`, `.a-price`, etc.)
4. **Regex sweep** — last resort scan of price-like text

### Known Limitations

| Site type | Status | Reason |
|---|---|---|
| Static HTML shops | ✅ Works | Full page delivered on first request |
| WooCommerce / Shopify | ✅ Usually works | Schema.org data present |
| Amazon | ⚠️ Inconsistent | Aggressive bot detection + CloudFront |
| Best Buy / Walmart | ⚠️ Inconsistent | JS-rendered prices |
| React / Next.js SPAs | ❌ Won't work | Prices injected by client-side JS |

**For production use**, sign up for [ScraperAPI](https://www.scraperapi.com/) (free tier: 1,000 requests/month) and set `SCRAPER_API_KEY`. The scraper will automatically route requests through their proxy when the key is present.

---

## Teardown

To delete all AWS resources:

```bash
sam delete --stack-name price-drop-notifier
```

Note: DynamoDB tables are retained by default. To remove them, delete the stack with `--no-retain-resources` or remove them manually in the AWS Console.

---

## Local Development

You can invoke Lambda functions locally with SAM:

```bash
cd backend

# Build first
sam build --use-container

# Test the subscribe function
sam local invoke SubscribeFunction --event events/subscribe.json \
  --env-vars env-local.json

# Start the full API locally (requires Docker)
sam local start-api --env-vars env-local.json
```

Create `backend/env-local.json`:

```json
{
  "SubscribeFunction": {
    "PRODUCTS_TABLE": "price-drop-products-dev",
    "SUBSCRIPTIONS_TABLE": "price-drop-subscriptions-dev",
    "SENDER_EMAIL": "you@example.com",
    "SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:000000000000:price-drop-events-dev",
    "API_BASE_URL": "http://localhost:3000",
    "ENVIRONMENT": "dev"
  }
}
```

---

## License

MIT
