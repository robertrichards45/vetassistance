from flask import Blueprint, current_app, redirect, request, url_for, jsonify, render_template, flash
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models.billing import OrgBilling
from app.models import Client, ClientPayment, DIYAccount, DIYSubscription, User
from app.services.email_service import send_email_html
import os, stripe
from decimal import Decimal, InvalidOperation
from datetime import datetime

billing_bp = Blueprint("billing", __name__, url_prefix="/billing")

def _stripe():
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY","").strip()
    return stripe


def _send_payment_email(db, client: Client, org_id: int, amount_total: int, status: str, receipt_url: str, payment: ClientPayment | None = None) -> None:
    if not client or not client.email:
        return
    subject = "Payment receipt - Veteran Benefits Assistance"
    amount_str = f"${amount_total/100:.2f}"
    text = f"Thank you for your payment.\n\nAmount: {amount_str}\nStatus: {status or 'paid'}\nReceipt: {receipt_url or 'Available in your client portal'}\n\nIf you have any questions, reply in your Client Portal."
    receipt_html = f"<a href=\"{receipt_url}\">View receipt</a>" if receipt_url else "Available in your client portal"
    html = f"""<!doctype html><html><body style='font-family:Arial,sans-serif;'>
<h2>Payment receipt</h2>
<p>Thank you for your payment.</p>
<ul>
<li><strong>Amount:</strong> {amount_str}</li>
<li><strong>Status:</strong> {status or 'paid'}</li>
<li><strong>Receipt:</strong> {receipt_html}</li>
</ul>
<p style='font-size:12px;color:#555'>If you have any questions, reply in your Client Portal.</p>
</body></html>"""
    ok, _ = send_email_html(client.email, subject, text, html, context="PAYMENT_RECEIPT", org_id=org_id, actor_user_id=None)
    if ok and payment:
        payment.email_sent = True
        db.commit()

def _linked_client(db):
    return db.query(Client).filter(Client.portal_user_id == current_user.id, Client.org_id == current_user.org_id).first()

@billing_bp.get("")
@login_required
@require_roles(Role.DIRECTOR, Role.CLIENT)
def overview():
    db = SessionLocal()
    if current_user.role == Role.CLIENT:
        c = _linked_client(db)
        if not c:
            return "Client not found", 404
        return render_template("billing/client_checkout.html", client=c)
    row = db.query(OrgBilling).filter(OrgBilling.org_id == current_user.org_id).first()
    return render_template(
        "billing/overview.html",
        billing=row,
        publishable_key=os.environ.get("STRIPE_PUBLISHABLE_KEY",""),
        price_id=os.environ.get("STRIPE_PRICE_ID","").strip(),
    )

@billing_bp.post("/checkout")
@login_required
@require_roles(Role.DIRECTOR, Role.CLIENT)
def checkout():
    s = _stripe()
    if not s.api_key:
        flash("Stripe not configured.", "error")
        return redirect(url_for("billing.overview"))

    if current_user.role == Role.CLIENT:
        db = SessionLocal()
        c = _linked_client(db)
        if not c:
            return "Client not found", 404
        amount_raw = (request.form.get("amount") or "").strip().replace("$", "")
        try:
            amount = Decimal(amount_raw)
        except InvalidOperation:
            flash("Enter a valid amount.", "error")
            return redirect(url_for("billing.overview"))
        if amount <= 0:
            flash("Amount must be greater than $0.", "error")
            return redirect(url_for("billing.overview"))
        if amount > Decimal("100000"):
            flash("Amount is too large.", "error")
            return redirect(url_for("billing.overview"))
        amount_cents = int((amount * 100).quantize(Decimal("1")))

        success_url = request.url_root.rstrip("/") + url_for("billing.receipt") + "?session_id={CHECKOUT_SESSION_ID}"
        cancel_url = request.url_root.rstrip("/") + url_for("billing.overview") + "?canceled=1"
        session = s.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount_cents,
                    "product_data": {"name": "Veteran Benefits Assistance - Client Service"},
                },
                "quantity": 1,
            }],
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=current_user.email,
            metadata={"org_id": str(current_user.org_id), "client_id": str(c.id), "source": "client_portal"},
        )
        return redirect(session.url, code=303)

    price_id = os.environ.get("STRIPE_PRICE_ID","").strip()
    if not price_id:
        flash("Stripe not configured (missing STRIPE_PRICE_ID).", "error")
        return redirect(url_for("billing.overview"))
    success_url = request.url_root.rstrip("/") + url_for("billing.overview") + "?success=1"
    cancel_url = request.url_root.rstrip("/") + url_for("billing.overview") + "?canceled=1"
    session = s.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        customer_email=current_user.email,
        metadata={"org_id": str(current_user.org_id)},
    )
    return redirect(session.url, code=303)

@billing_bp.get("/receipt")
@login_required
@require_roles(Role.CLIENT)
def receipt():
    session_id = (request.args.get("session_id") or "").strip()
    if not session_id:
        flash("Missing receipt session.", "error")
        return redirect(url_for("billing.overview"))
    s = _stripe()
    if not s.api_key:
        flash("Stripe not configured.", "error")
        return redirect(url_for("billing.overview"))
    db = SessionLocal()
    c = _linked_client(db)
    if not c:
        return "Client not found", 404
    session = s.checkout.Session.retrieve(session_id)
    meta = session.get("metadata", {}) or {}
    if str(meta.get("client_id")) != str(c.id):
        return "Not authorized", 403

    payment_intent = session.get("payment_intent", "") or ""
    receipt_url = ""
    invoice_id = ""
    invoice_url = ""
    try:
        pi = s.PaymentIntent.retrieve(payment_intent) if payment_intent else None
        if pi and pi.get("charges", {}).get("data"):
            receipt_url = pi["charges"]["data"][0].get("receipt_url", "") or ""
    except Exception:
        receipt_url = ""

    invoice_id = session.get("invoice","") or ""
    if invoice_id:
        try:
            inv = s.Invoice.retrieve(invoice_id)
            invoice_url = inv.get("hosted_invoice_url","") or ""
        except Exception:
            invoice_url = ""

    existing = db.query(ClientPayment).filter(ClientPayment.stripe_session_id == session_id).first()
    if not existing:
        existing = ClientPayment(
            org_id=current_user.org_id,
            client_id=c.id,
            amount_cents=int(session.get("amount_total") or 0),
            currency=(session.get("currency") or "usd").lower(),
            status=session.get("payment_status", "") or "",
            stripe_session_id=session_id,
            stripe_payment_intent_id=payment_intent,
            receipt_url=receipt_url,
            invoice_id=invoice_id,
            invoice_url=invoice_url,
        )
        db.add(existing)
        db.commit()
    else:
        existing.status = session.get("payment_status", "") or existing.status
        existing.receipt_url = receipt_url or existing.receipt_url
        existing.invoice_id = invoice_id or existing.invoice_id
        existing.invoice_url = invoice_url or existing.invoice_url
        db.commit()

    if not existing.email_sent:
        _send_payment_email(db, c, current_user.org_id, int(session.get("amount_total") or 0), session.get("payment_status", "") or "", existing.receipt_url or "", existing)

    return render_template("billing/receipt.html", session=session, client=c, receipt_url=existing.receipt_url)

@billing_bp.post("/portal")
@login_required
@require_roles(Role.DIRECTOR)
def portal():
    s = _stripe()
    db = SessionLocal()
    row = db.query(OrgBilling).filter(OrgBilling.org_id == current_user.org_id).first()
    if not row or not row.stripe_customer_id:
        flash("No Stripe customer found for this org yet.", "error")
        return redirect(url_for("billing.overview"))
    portal = s.billing_portal.Session.create(
        customer=row.stripe_customer_id,
        return_url=request.url_root.rstrip("/") + url_for("billing.overview"),
    )
    return redirect(portal.url, code=303)

@billing_bp.post("/client/<int:client_id>/pay-link")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def client_pay_link(client_id: int):
    s = _stripe()
    if not s.api_key:
        flash("Stripe not configured.", "error")
        return redirect(url_for("clients.view_client", client_id=client_id))
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    amount_raw = (request.form.get("amount") or "").strip().replace("$", "")
    try:
        amount = Decimal(amount_raw)
    except InvalidOperation:
        flash("Enter a valid amount.", "error")
        return redirect(url_for("clients.view_client", client_id=client_id))
    if amount <= 0:
        flash("Amount must be greater than $0.", "error")
        return redirect(url_for("clients.view_client", client_id=client_id))
    if amount > Decimal("100000"):
        flash("Amount is too large.", "error")
        return redirect(url_for("clients.view_client", client_id=client_id))
    amount_cents = int((amount * 100).quantize(Decimal("1")))
    success_url = request.url_root.rstrip("/") + url_for("billing.receipt") + "?session_id={CHECKOUT_SESSION_ID}"
    cancel_url = request.url_root.rstrip("/") + url_for("clients.view_client", client_id=c.id) + "?pay_canceled=1"
    session = s.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": amount_cents,
                "product_data": {"name": "Veteran Benefits Assistance - Client Service"},
            },
            "quantity": 1,
        }],
        success_url=success_url,
        cancel_url=cancel_url,
        customer_email=c.email or current_user.email,
        metadata={"org_id": str(current_user.org_id), "client_id": str(c.id), "source": "staff_link"},
    )
    flash("Payment link created. Share it with the client.", "success")
    return redirect(session.url, code=303)

@billing_bp.post("/client/<int:client_id>/invoice")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def client_invoice(client_id: int):
    s = _stripe()
    if not s.api_key:
        flash("Stripe not configured.", "error")
        return redirect(url_for("clients.view_client", client_id=client_id))
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    amount_raw = (request.form.get("amount") or "").strip().replace("$", "")
    try:
        amount = Decimal(amount_raw)
    except InvalidOperation:
        flash("Enter a valid amount.", "error")
        return redirect(url_for("clients.view_client", client_id=client_id))
    if amount <= 0:
        flash("Amount must be greater than $0.", "error")
        return redirect(url_for("clients.view_client", client_id=client_id))
    if amount > Decimal("100000"):
        flash("Amount is too large.", "error")
        return redirect(url_for("clients.view_client", client_id=client_id))
    if not c.email:
        flash("Client email is required to send an invoice.", "error")
        return redirect(url_for("clients.view_client", client_id=client_id))
    amount_cents = int((amount * 100).quantize(Decimal("1")))

    customer = None
    if c.email:
        existing = s.Customer.search(query=f"email:'{c.email}'", limit=1)
        if existing and existing.get("data"):
            customer = existing["data"][0]
    if not customer:
        customer = s.Customer.create(email=c.email, name=c.display_name(), metadata={"org_id": str(current_user.org_id), "client_id": str(c.id)})

    s.InvoiceItem.create(
        customer=customer["id"],
        currency="usd",
        amount=amount_cents,
        description="Veteran Benefits Assistance - Client Service",
        metadata={"org_id": str(current_user.org_id), "client_id": str(c.id), "source": "staff_invoice"},
    )
    invoice = s.Invoice.create(
        customer=customer["id"],
        auto_advance=True,
        collection_method="send_invoice",
        days_until_due=7,
        metadata={"org_id": str(current_user.org_id), "client_id": str(c.id), "source": "staff_invoice"},
    )
    invoice = s.Invoice.finalize_invoice(invoice["id"])
    s.Invoice.send_invoice(invoice["id"])

    pay = ClientPayment(
        org_id=current_user.org_id,
        client_id=c.id,
        amount_cents=amount_cents,
        currency="usd",
        status="invoice_sent",
        invoice_id=invoice["id"],
        invoice_url=invoice.get("hosted_invoice_url","") or "",
    )
    db.add(pay)
    db.commit()

    flash("Invoice sent to client.", "success")
    return redirect(url_for("clients.view_client", client_id=c.id))

@billing_bp.post("/webhook")
def webhook():
    payload = request.data
    sig = request.headers.get("Stripe-Signature","")
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET","").strip()
    if not secret:
        return ("Stripe webhook secret not configured.", 400)

    s = _stripe()
    try:
        event = s.Webhook.construct_event(payload, sig, secret)
    except Exception as e:
        return (str(e), 400)

    etype = event.get("type","")
    data = event.get("data",{}).get("object",{})

    db = SessionLocal()
    # Handle subscription lifecycle
    if etype in ("checkout.session.completed",):
        meta = data.get("metadata", {}) or {}
        org_id = int(meta.get("org_id") or 0)
        cust = data.get("customer","") or ""
        sub = data.get("subscription","") or ""
        mode = data.get("mode","") or ""
        source = meta.get("source") or ""
        if source == "diy":
            user_id = int(meta.get("user_id") or 0)
            plan = (meta.get("plan") or "basic").strip().lower()
            if user_id and sub:
                user = db.get(User, user_id)
                acct = db.query(DIYAccount).filter_by(user_id=user_id).first()
                if user and not acct:
                    acct = DIYAccount(org_id=user.org_id, user_id=user.id, full_name=user.full_name or "", status="active")
                    db.add(acct)
                    db.commit()
                if acct:
                    row = db.query(DIYSubscription).filter_by(diy_id=acct.id).order_by(DIYSubscription.created_at.desc()).first()
                    if not row:
                        row = DIYSubscription(org_id=acct.org_id, diy_id=acct.id)
                        db.add(row)
                        db.commit()
                    row.plan = plan
                    row.status = "active"
                    row.stripe_customer_id = cust
                    row.stripe_subscription_id = sub
                    try:
                        sub_obj = s.Subscription.retrieve(sub)
                        cps = int(sub_obj.get("current_period_start") or 0)
                        cpe = int(sub_obj.get("current_period_end") or 0)
                        row.started_at = datetime.utcfromtimestamp(cps) if cps else datetime.utcnow()
                        row.current_period_end = datetime.utcfromtimestamp(cpe) if cpe else None
                    except Exception:
                        row.started_at = datetime.utcnow()
                    db.commit()
        if org_id and sub:
            row = db.query(OrgBilling).filter(OrgBilling.org_id == org_id).first()
            if not row:
                row = OrgBilling(org_id=org_id)
                db.add(row); db.commit()
            row.stripe_customer_id = cust
            row.stripe_subscription_id = sub
            row.status = "active"
            db.commit()

        if org_id and mode == "payment":
            client_id = int(meta.get("client_id") or 0)
            amount_total = int(data.get("amount_total") or 0)
            currency = (data.get("currency") or "usd").lower()
            session_id = data.get("id","")
            payment_intent = data.get("payment_intent","") or ""
            receipt_url = ""
            try:
                pi = s.PaymentIntent.retrieve(payment_intent) if payment_intent else None
                if pi and pi.get("charges", {}).get("data"):
                    receipt_url = pi["charges"]["data"][0].get("receipt_url","") or ""
            except Exception:
                receipt_url = ""
            
            if client_id:
                existing = db.query(ClientPayment).filter(ClientPayment.stripe_session_id == session_id).first()
                if not existing:
                    existing = ClientPayment(
                        org_id=org_id,
                        client_id=client_id,
                        amount_cents=amount_total,
                        currency=currency,
                        status=data.get("payment_status", "") or "",
                        stripe_session_id=session_id,
                        stripe_payment_intent_id=payment_intent,
                        receipt_url=receipt_url,
                    )
                    db.add(existing)
                    db.commit()
                else:
                    existing.status = data.get("payment_status", "") or existing.status
                    existing.receipt_url = receipt_url or existing.receipt_url
                    db.commit()

                try:
                    c = db.get(Client, client_id)
                    if c and not existing.email_sent:
                        _send_payment_email(db, c, org_id, amount_total, data.get("payment_status", "") or "", receipt_url, existing)
                except Exception:
                    pass

    if etype.startswith("customer.subscription."):
        sub = data.get("id","")
        status = data.get("status","")
        cust = data.get("customer","")
        row = db.query(OrgBilling).filter(OrgBilling.stripe_subscription_id == sub).first()
        if row:
            row.status = status
            row.stripe_customer_id = cust or row.stripe_customer_id
            db.commit()
        diy_row = db.query(DIYSubscription).filter(DIYSubscription.stripe_subscription_id == sub).first()
        if diy_row:
            diy_row.status = status
            diy_row.stripe_customer_id = cust or diy_row.stripe_customer_id
            try:
                if data.get("current_period_end"):
                    diy_row.current_period_end = datetime.utcfromtimestamp(int(data.get("current_period_end")))
            except Exception:
                pass
            db.commit()

    return jsonify({"received": True})


