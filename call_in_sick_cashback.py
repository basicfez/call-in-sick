# /// script
# requires-python = "==3.11.*"
# dependencies = [
#   "codewords-client==0.4.6",
#   "fastapi==0.116.1",
#   "openai==1.99.7",
#   "httpx==0.28.1"
# ]
# [tool.env-checker]
# env_vars = [
#   "PORT=8000",
#   "LOGLEVEL=INFO",
#   "CODEWORDS_API_KEY",
#   "CODEWORDS_RUNTIME_URI"
# ]
# ///

from typing import Literal
from datetime import datetime, timezone
from textwrap import dedent

from codewords_client import logger, redis_client, run_service
from fastapi import FastAPI, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator
import json

# -------------------------
# Constants & Business Rules
# -------------------------
CASHBACK_RATE = 0.1342  # 13.42% cashback rate
MONTHLY_CAP_ZAR = 1438.00  # R1,438 monthly cap
SUBSCRIPTION_AMOUNT_ZAR = 193.00  # R193 per subscription mark

def get_current_month_key() -> str:
    """Get the current month key for tracking."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def get_monthly_total(redis, ns: str) -> float:
    """Get the current month's running cashback total from Redis."""
    month_key = get_current_month_key()
    raw = await redis.get(f"{ns}:cashback:monthly:{month_key}")
    return float(raw) if raw else 0.0


async def add_to_monthly_total(redis, ns: str, amount: float) -> float:
    """Add cashback amount to the monthly tracker in Redis and return new total."""
    month_key = get_current_month_key()
    key = f"{ns}:cashback:monthly:{month_key}"
    current = await get_monthly_total(redis, ns)
    new_total = current + amount
    await redis.set(key, str(round(new_total, 2)))
    return new_total


async def calculate_pos_cashback(receipt_total: float, redis, ns: str) -> dict:
    """Calculate cashback from a POS slip total.

    Business rule: total × 0.1342, capped at monthly R1,438.
    """
    logger.info("Calculating POS cashback", receipt_total=receipt_total)

    cashback_amount = round(receipt_total * CASHBACK_RATE, 2)
    current_monthly = await get_monthly_total(redis, ns)
    remaining_cap = max(0, MONTHLY_CAP_ZAR - current_monthly)

    if cashback_amount > remaining_cap:
        cashback_amount = round(remaining_cap, 2)
        cap_reached = True
    else:
        cap_reached = False

    new_monthly_total = await add_to_monthly_total(redis, ns, cashback_amount)

    return {
        "receipt_total": receipt_total,
        "cashback_rate": CASHBACK_RATE,
        "calculated_cashback": round(receipt_total * CASHBACK_RATE, 2),
        "actual_cashback": cashback_amount,
        "monthly_total_before": round(current_monthly, 2),
        "monthly_total_after": round(new_monthly_total, 2),
        "monthly_cap": MONTHLY_CAP_ZAR,
        "remaining_cap": round(MONTHLY_CAP_ZAR - new_monthly_total, 2),
        "cap_reached": cap_reached,
    }


async def calculate_subscription_cashback(marks: int, redis, ns: str) -> dict:
    """Calculate cashback for subscription marks.

    Each mark = R193. Check monthly cap.
    """
    logger.info("Calculating subscription cashback", marks=marks)

    total_subscription = round(marks * SUBSCRIPTION_AMOUNT_ZAR, 2)
    current_monthly = await get_monthly_total(redis, ns)
    remaining_cap = max(0, MONTHLY_CAP_ZAR - current_monthly)

    if total_subscription > remaining_cap:
        # How many full marks fit?
        affordable_marks = int(remaining_cap // SUBSCRIPTION_AMOUNT_ZAR)
        actual_amount = round(affordable_marks * SUBSCRIPTION_AMOUNT_ZAR, 2)
        cap_reached = True
    else:
        affordable_marks = marks
        actual_amount = total_subscription
        cap_reached = False

    new_monthly_total = await add_to_monthly_total(redis, ns, actual_amount)

    return {
        "requested_marks": marks,
        "approved_marks": affordable_marks,
        "amount_per_mark": SUBSCRIPTION_AMOUNT_ZAR,
        "total_subscription": total_subscription,
        "actual_amount": actual_amount,
        "monthly_total_before": round(current_monthly, 2),
        "monthly_total_after": round(new_monthly_total, 2),
        "monthly_cap": MONTHLY_CAP_ZAR,
        "remaining_cap": round(MONTHLY_CAP_ZAR - new_monthly_total, 2),
        "cap_reached": cap_reached,
    }


async def extract_receipt_total_from_image(image_url: str) -> float:
    """Use GPT-5 vision to extract the total amount from a POS slip image."""
    logger.info("Extracting receipt total from image", image_url=image_url[:80])

    client = AsyncOpenAI()

    prompt = dedent("""\
        You are a receipt/POS slip reader for South African stores.
        Analyze this image of a point-of-sale slip and extract the TOTAL amount.

        Rules:
        - Find the final TOTAL or GRAND TOTAL on the receipt
        - The currency is South African Rand (ZAR / R)
        - Return ONLY the numeric value (e.g., 345.99)
        - Do NOT include the R symbol or any text
        - If you see multiple totals, use the GRAND TOTAL (largest final amount)
        - If you cannot read the receipt clearly, return 0.00\
        """)

    response = await client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]
        }],
        reasoning_effort="minimal",
        max_completion_tokens=100
    )

    raw_text = response.choices[0].message.content.strip()
    logger.info("Raw OCR extraction", raw_text=raw_text)

    # Parse the numeric value
    cleaned = raw_text.replace("R", "").replace(",", "").replace(" ", "").strip()
    try:
        total = float(cleaned)
    except ValueError:
        logger.warning("Could not parse receipt total", raw_text=raw_text)
        raise HTTPException(
            status_code=400,
            detail=f"Could not extract total from receipt. AI returned: '{raw_text}'"
        )

    return total


async def extract_receipt_items_from_image(image_url: str) -> list[dict]:
    """Use GPT-5 vision to extract individual line items from a POS slip."""
    logger.info("Extracting receipt items from image")

    client = AsyncOpenAI()

    class ReceiptItem(BaseModel):
        product_name: str = Field(description="Product name as shown on receipt")
        description: str = Field(default="", description="Brief product description")
        price: float = Field(description="Item price in ZAR")
        quantity: int = Field(default=1, description="Quantity purchased")
        control_code: str = Field(default="", description="Barcode or product code if visible")
        category: str = Field(default="general", description="One of: medicine, diagnostics, groceries, general")

    class ReceiptExtraction(BaseModel):
        store_name: str = Field(default="Unknown", description="Store name from receipt")
        date: str = Field(default="", description="Date on receipt")
        items: list[ReceiptItem] = Field(description="List of items on the receipt")
        subtotal: float = Field(default=0.0, description="Subtotal before tax")
        total: float = Field(description="Grand total on receipt")

    response = await client.beta.chat.completions.parse(
        model="gpt-5-mini",
        messages=[
            {
                "role": "system",
                "content": dedent("""\
                    You are a South African POS receipt analyzer for the "Call in Sick" healthcare platform.
                    Extract ALL line items from the receipt image.

                    Categorize each item as:
                    - "medicine": OTC drugs, prescription medication, pain relief, cough/cold remedies, vitamins
                    - "diagnostics": COVID tests, pregnancy tests, blood pressure monitors, thermometers, virtual consultation fees
                    - "groceries": Food, beverages, household items, cleaning products, personal care
                    - "general": Anything that doesn't fit the above categories

                    Extract control codes/barcodes if visible on the receipt.
                    Currency is South African Rand (ZAR).\
                    """)
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract all items from this POS receipt:"},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]
            }
        ],
        response_format=ReceiptExtraction,
        reasoning_effort="minimal"
    )

    extraction = response.choices[0].message.parsed
    logger.info("Extracted items", count=len(extraction.items), total=extraction.total)

    return {
        "store_name": extraction.store_name,
        "date": extraction.date,
        "items": [item.model_dump() for item in extraction.items],
        "subtotal": extraction.subtotal,
        "total": extraction.total
    }


def generate_report(mode: str, receipt_data: dict | None, cashback_data: dict) -> str:
    """Generate a markdown cashback report."""
    lines = [
        "# 🏥 Call in Sick — Cashback Report",
        "",
        f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Mode:** {mode.upper()}",
        "",
    ]

    if mode == "pos_slip" and receipt_data:
        lines.extend([
            f"## 🧾 Receipt: {receipt_data.get('store_name', 'Unknown Store')}",
            f"**Receipt Date:** {receipt_data.get('date', 'N/A')}",
            "",
        ])

        items = receipt_data.get("items", [])
        if items:
            lines.append("### Items Breakdown")
            lines.append("")
            lines.append("| # | Product | Category | Price (R) |")
            lines.append("|---|---------|----------|-----------|")
            for i, item in enumerate(items, 1):
                cat_emoji = {"medicine": "💊", "diagnostics": "🔬", "groceries": "🛒"}.get(item.get("category", ""), "📦")
                lines.append(
                    f"| {i} | {item['product_name']} | {cat_emoji} {item.get('category', 'general')} | R{item['price']:.2f} |"
                )
            lines.append("")

        lines.extend([
            f"**Receipt Total:** R{cashback_data['receipt_total']:.2f}",
            "",
        ])

    lines.extend([
        "## 💰 Cashback Calculation",
        "",
    ])

    if mode == "pos_slip":
        lines.extend([
            f"- Receipt Total: **R{cashback_data['receipt_total']:.2f}**",
            f"- Cashback Rate: **{cashback_data['cashback_rate'] * 100:.2f}%**",
            f"- Calculated Cashback: **R{cashback_data['calculated_cashback']:.2f}**",
            f"- Actual Cashback: **R{cashback_data['actual_cashback']:.2f}**",
        ])
    else:
        lines.extend([
            f"- Requested Marks: **{cashback_data['requested_marks']}**",
            f"- Approved Marks: **{cashback_data['approved_marks']}**",
            f"- Amount per Mark: **R{cashback_data['amount_per_mark']:.2f}**",
            f"- Total: **R{cashback_data['actual_amount']:.2f}**",
        ])

    lines.extend([
        "",
        "## 📊 Monthly Status",
        "",
        f"- Monthly Total (before): **R{cashback_data['monthly_total_before']:.2f}**",
        f"- Monthly Total (after): **R{cashback_data['monthly_total_after']:.2f}**",
        f"- Monthly Cap: **R{cashback_data['monthly_cap']:.2f}**",
        f"- Remaining Cap: **R{cashback_data['remaining_cap']:.2f}**",
    ])

    if cashback_data.get("cap_reached"):
        lines.extend([
            "",
            "⚠️ **MONTHLY CAP REACHED** — No further cashback available this month.",
        ])

    return "\n".join(lines)


# -------------------------
# FastAPI Application
# -------------------------
app = FastAPI(
    title="Call in Sick — Healthcare Cashback",
    description="Receipt OCR and cashback calculator for the Call in Sick healthcare cost containment platform. Upload POS slips or submit subscription marks.",
    version="1.0.0",
)


class CashbackRequest(BaseModel):
    mode: Literal["pos_slip", "subscription"] = Field(
        default="pos_slip",
        description="Processing mode: 'pos_slip' to scan a receipt, 'subscription' for monthly mark",
        json_schema_extra={"enum": ["pos_slip", "subscription"]}
    )
    receipt_image: str = Field(
        default="",
        description="Photo of the POS slip/receipt (required for pos_slip mode)",
        json_schema_extra={"contentMediaType": "image/*"}
    )
    manual_total: float = Field(
        default=0.0,
        description="Manual receipt total in ZAR (use if image is unclear or for testing)",
        ge=0
    )
    subscription_marks: int = Field(
        default=1,
        description="Number of subscription marks (for subscription mode)",
        ge=1, le=10
    )
    extract_items: bool = Field(
        default=True,
        description="Extract individual line items from receipt (slower but more detailed)"
    )


class CashbackResponse(BaseModel):
    report: str = Field(
        ...,
        description="Full cashback report in markdown format",
        json_schema_extra={"contentMediaType": "text/markdown"}
    )
    cashback_amount: float = Field(..., description="Cashback amount awarded (ZAR)")
    receipt_total: float = Field(default=0.0, description="Receipt total (ZAR)")
    monthly_remaining: float = Field(..., description="Remaining monthly cap (ZAR)")
    cap_reached: bool = Field(default=False, description="Whether monthly cap has been reached")
    mode: str = Field(..., description="Processing mode used")
    items_extracted: int = Field(default=0, description="Number of items extracted from receipt")


@app.post("/", response_model=CashbackResponse)
async def process_cashback(request: CashbackRequest):
    """
    Process a POS slip or subscription for healthcare cashback.

    **POS Slip Mode:**
    - Upload a photo of your receipt OR enter the total manually
    - AI extracts the total and individual items
    - Cashback = Total × 13.42% (capped at R1,438/month)

    **Subscription Mode:**
    - Submit 1 or more subscription marks
    - Each mark = R193
    - Monthly cap applies
    """
    logger.info("STEPLOG START trigger")
    logger.info("Processing cashback request", mode=request.mode)

    async with redis_client() as (redis, ns):
      if request.mode == "pos_slip":
        # Determine the receipt total
        receipt_data = None
        items_count = 0

        if request.receipt_image:
            # Extract total from image using AI vision
            logger.info("STEPLOG START stepone")
            receipt_total = await extract_receipt_total_from_image(request.receipt_image)

            # Optionally extract individual items
            if request.extract_items:
                logger.info("STEPLOG START steptwo")
                receipt_data = await extract_receipt_items_from_image(request.receipt_image)
                items_count = len(receipt_data.get("items", []))
                # Use the AI-extracted total if available
                if receipt_data.get("total", 0) > 0:
                    receipt_total = receipt_data["total"]
        elif request.manual_total > 0:
            receipt_total = request.manual_total
        else:
            raise HTTPException(
                status_code=400,
                detail="Please provide a receipt image or a manual total amount."
            )

        # Calculate cashback
        logger.info("STEPLOG START stepthree")
        cashback_data = await calculate_pos_cashback(receipt_total, redis, ns)

        # Check if over the cap — stop processing
        if cashback_data["cap_reached"]:
            logger.warning("Monthly cap reached", monthly_total=cashback_data["monthly_total_after"])

        # Generate report
        logger.info("STEPLOG START stepfour")
        # Store receipt data in Redis for record-keeping
        receipt_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "pos_slip",
            "receipt_total": receipt_total,
            "cashback": cashback_data["actual_cashback"],
            "items_count": items_count
        }
        record_key = f"{ns}:cashback:receipts:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        await redis.set(record_key, json.dumps(receipt_record))

        logger.info("STEPLOG START stepfive")
        report = generate_report("pos_slip", receipt_data, cashback_data)

        return CashbackResponse(
            report=report,
            cashback_amount=cashback_data["actual_cashback"],
            receipt_total=cashback_data["receipt_total"],
            monthly_remaining=cashback_data["remaining_cap"],
            cap_reached=cashback_data["cap_reached"],
            mode="pos_slip",
            items_extracted=items_count
        )

      elif request.mode == "subscription":
        # Calculate subscription cashback
        logger.info("STEPLOG START stepthree")
        cashback_data = await calculate_subscription_cashback(request.subscription_marks, redis, ns)

        if cashback_data["cap_reached"]:
            logger.warning("Monthly cap reached during subscription", monthly_total=cashback_data["monthly_total_after"])

        logger.info("STEPLOG START stepfour")
        sub_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "subscription",
            "marks": request.subscription_marks,
            "amount": cashback_data["actual_amount"]
        }
        sub_key = f"{ns}:cashback:subscriptions:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        await redis.set(sub_key, json.dumps(sub_record))

        logger.info("STEPLOG START stepfive")
        report = generate_report("subscription", None, cashback_data)

        return CashbackResponse(
            report=report,
            cashback_amount=cashback_data["actual_amount"],
            receipt_total=0.0,
            monthly_remaining=cashback_data["remaining_cap"],
            cap_reached=cashback_data["cap_reached"],
            mode="subscription",
            items_extracted=0
        )

      else:
        raise HTTPException(status_code=400, detail=f"Unknown mode: {request.mode}")


@app.get("/status")
async def get_monthly_status():
    """Get the current monthly cashback status."""
    async with redis_client() as (redis, ns):
        current = await get_monthly_total(redis, ns)
    return {
        "month": get_current_month_key(),
        "monthly_total": round(current, 2),
        "monthly_cap": MONTHLY_CAP_ZAR,
        "remaining": round(MONTHLY_CAP_ZAR - current, 2),
        "cap_reached": current >= MONTHLY_CAP_ZAR,
        "cashback_rate": CASHBACK_RATE,
        "subscription_amount": SUBSCRIPTION_AMOUNT_ZAR
    }


if __name__ == "__main__":
    run_service(app)
