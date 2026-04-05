# 🏥 Call in Sick — Healthcare Cashback Framework

> Open-source healthcare cost containment platform for South African employers and employees.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.116-green.svg)](https://fastapi.tiangolo.com)

## 📖 Overview

**Call in Sick** is a healthcare benefits platform that helps employers contain healthcare costs while empowering employees to earn cashback on qualifying health-related purchases.

### How It Works

1. **Employers** subscribe and commit employees to the platform
2. **Employees** submit POS (Point of Sale) slips or subscribe for monthly care packages
3. **AI-powered OCR** extracts items from receipts automatically
4. **Smart categorization** classifies items into Medicine, Diagnostics, or Groceries
5. **Cashback** is calculated at 13.42% of the receipt total (capped at R1,438/month)

### Two Modes of Operation

| Mode | How It Works | Amount |
|------|-------------|--------|
| **POS Slip** | Upload receipt → AI reads total → 13.42% cashback | Up to R1,438/month |
| **Subscription** | Monthly care package marks → R193 per mark | R193 × marks (capped) |

---

## 🏗️ Project Structure

```
call-in-sick/
├── README.md                          # This file
├── LICENSE                            # MIT License
├── call_in_sick_cashback.py          # Main service (FastAPI + AI OCR)
├── docs/
│   ├── api-reference.md              # API endpoint documentation
│   ├── business-rules.md             # Cashback rules & calculations
│   └── data-models.md               # Pydantic models reference
├── tests/
│   ├── test_cashback_calculation.py  # Unit tests for cashback logic
│   ├── test_subscription.py         # Subscription mode tests
│   └── sample_receipts/             # Sample receipt images for testing
└── .github/
    └── workflows/
        └── ci.yml                    # GitHub Actions CI pipeline
```

---

## 🔧 Tech Stack

- **Runtime**: Python 3.11 on CodeWords serverless platform
- **API Framework**: FastAPI 0.116
- **AI/OCR**: OpenAI GPT-5-mini (vision) for receipt reading
- **State**: Redis (persistent monthly tracking across sessions)
- **Data Models**: Pydantic v2

---

## 📡 API Reference

### `POST /` — Process Cashback

**Request Body:**

```json
{
  "mode": "pos_slip",           // "pos_slip" or "subscription"
  "receipt_image": "<image>",    // Upload receipt photo (POS mode)
  "manual_total": 0.0,          // Manual total in ZAR (testing/fallback)
  "subscription_marks": 1,      // Number of marks (subscription mode)
  "extract_items": true          // Extract individual line items
}
```

**Response:**

```json
{
  "report": "# Cashback Report (markdown)",
  "cashback_amount": 114.14,
  "receipt_total": 850.50,
  "monthly_remaining": 937.86,
  "cap_reached": false,
  "mode": "pos_slip",
  "items_extracted": 5
}
```

### `GET /status` — Monthly Status

Returns current monthly cashback tracking:

```json
{
  "month": "2026-04",
  "monthly_total": 500.14,
  "monthly_cap": 1438.0,
  "remaining": 937.86,
  "cap_reached": false,
  "cashback_rate": 0.1342,
  "subscription_amount": 193.0
}
```

---

## 💼 Business Rules

### Cashback Rate
- **Rate**: 13.42% of receipt total
- **Formula**: `cashback = receipt_total × 0.1342`

### Monthly Cap
- **Maximum**: R1,438 per month
- Processing stops when cap is reached
- Resets at the start of each calendar month

### Subscription Marks
- Each mark = R193 (fixed)
- Maximum marks per month: ~7 (R1,438 ÷ R193)
- Partial marks not supported

### Product Categories

| Category | Examples | Emoji |
|----------|----------|-------|
| 💊 Medicine | Pain relief, vitamins, prescription drugs, cough remedies | 💊 |
| 🔬 Diagnostics | COVID tests, pregnancy tests, BP monitors, virtual consults | 🔬 |
| 🛒 Groceries | Food, beverages, household items, personal care | 🛒 |
| 📦 General | Anything not in above categories | 📦 |

---

## 📊 Data Models

### ReceiptItem
```python
class ReceiptItem(BaseModel):
    product_name: str       # Product name from receipt
    description: str        # Brief description
    price: float           # Price in ZAR
    quantity: int           # Quantity (default: 1)
    control_code: str       # Barcode/product code
    category: str           # medicine | diagnostics | groceries | general
```

### CashbackRequest
```python
class CashbackRequest(BaseModel):
    mode: Literal["pos_slip", "subscription"]
    receipt_image: str      # Image file (for POS mode)
    manual_total: float     # Manual ZAR amount (optional)
    subscription_marks: int # 1-10 marks (for subscription mode)
    extract_items: bool     # Extract line items (default: True)
```

### CashbackResponse
```python
class CashbackResponse(BaseModel):
    report: str             # Markdown cashback report
    cashback_amount: float  # Cashback awarded (ZAR)
    receipt_total: float    # Receipt total (ZAR)
    monthly_remaining: float # Remaining monthly cap
    cap_reached: bool       # Whether cap was hit
    mode: str               # Mode used
    items_extracted: int    # Number of items found
```

---

## 🚀 Deployment

### On CodeWords Platform

The service is deployed and live at:
- **Service ID**: `call_in_sick_cashback_3f4ca43e`
- **UI**: [Run on CodeWords](https://codewords.agemo.ai/run/call_in_sick_cashback_3f4ca43e)

### Self-Hosted

1. Clone this repository
2. Install dependencies: `pip install fastapi openai httpx codewords-client`
3. Set up Redis for state management
4. Run: `python call_in_sick_cashback.py`

---

## 🧪 Testing Examples

### Test Subscription Mode
```bash
curl -X POST https://api.codewords.agemo.ai/run/call_in_sick_cashback_3f4ca43e \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{"mode": "subscription", "subscription_marks": 2}'
```

### Test POS Slip (Manual Total)
```bash
curl -X POST https://api.codewords.agemo.ai/run/call_in_sick_cashback_3f4ca43e \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{"mode": "pos_slip", "manual_total": 850.50, "extract_items": false}'
```

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🏢 Built With

- [CodeWords](https://codewords.agemo.ai) — Serverless automation platform
- [OpenAI GPT-5](https://openai.com) — AI vision for receipt OCR
- [FastAPI](https://fastapi.tiangolo.com) — Modern Python web framework
- [Redis](https://redis.io) — Persistent state management

---

*Built for the South African healthcare market 🇿🇦*
