# PharmacyOS ↔ ERP integration API (v1)

Status: foundation implemented and tested against a local test site. **No production PharmacyOS
system is connected**; outbound events are disabled until an administrator configures them.

## Principles

* The PharmacyOS **backend** is the only caller. The storefront never talks to the ERP directly.
* Authenticate as a dedicated user that has the **PharmacyOS Integration** role (role profile of the
  same name: + Sales User, Stock User) using a per-user API key/secret
  (`Authorization: token <key>:<secret>`) or OAuth2 client credentials. Never use Administrator.
  Pharmacy staff roles are rejected by every endpoint (403).
* ERPNext is authoritative for prices, stock and order state. Documents are created through normal
  ERPNext APIs, so validation, permissions and the audit trail apply.
* All endpoints: `/api/method/pharmacyos_erp.api.v1.<module>.<function>`. Responses are wrapped by
  Frappe as `{"message": ...}`.

## Endpoints

| Method | Path (`pharmacyos_erp.api.v1.` …) | Purpose |
|---|---|---|
| GET | `branches.get_branches` | Branch ↔ storefront code ↔ warehouse mapping |
| GET | `catalog.get_catalog?cursor=&modified_since=&limit=` | Published items (`Item.pharmacyos_publish = 1`), keyset-paginated by `(modified, name)` |
| GET | `availability.get_availability?branch_code=&item_codes=` | Sellable quantity per branch and item |
| POST | `orders.create_order` | Idempotent online order → submitted Sales Order |
| GET | `orders.get_order_status?order_id=` | Order state, delivery/billing progress, documents |
| POST | `customers.upsert_customer` | Link a PharmacyOS customer ID to an ERPNext Customer |
| GET | `events.get_events?after=&limit=` | Pull alternative to webhooks |

### Catalog

Each item: `item_code, name, name_ar, generic_name, strength, dosage_form, pack_size, dispensing,
is_medicine, brand, category, uom, image, barcodes[], price, currency, disabled, modified`.
Continue with `next_cursor` while `has_more` is true. Disabled items are returned with `disabled: 1`
so the storefront can unpublish them.

### Availability

`qty` = non-expired batch quantity (ERPNext's batch availability, which excludes expired batches) —
or on-hand quantity for non-batch items — in the branch's warehouses, minus quantity reserved by
open Sales Orders; never negative. `nearest_expiry` is the earliest non-expired batch expiry.

### Create order

```json
POST orders.create_order
{
  "order_id": "PO-2026-000123",          // PharmacyOS order ID — idempotency key
  "branch_code": "BGD-MAN",               // Branch.pharmacyos_storefront_code
  "items": [{"item_code": "MED-00012", "qty": 2}],
  "customer": {"id": "cus_42", "name": "…", "phone": "…", "email": "…"},
  "delivery_date": "2026-10-03",          // optional
  "notes": "…"                            // optional, stored as a comment
}
```

* **Idempotency**: `Sales Order.pharmacyos_order_id` is database-unique; the canonical payload
  (items sorted) is hashed with SHA-256. Same ID + same payload → `created: false` with the existing
  order. Same ID + different payload → HTTP **409** (`OrderConflictError`). Concurrent duplicates are
  resolved by the unique constraint.
* Validation before anything is created: items must be published and enabled; quantity must not
  exceed sellable availability (otherwise HTTP 417 with the shortage list).
* Prices: rates in the request are ignored; ERPNext price lists and pricing rules apply. The response
  returns `currency, grand_total, items[{item_code, qty, rate, amount}]` for reconciliation.
* The Sales Order is **submitted**, which reserves stock (reflected immediately in availability).
  Fulfilment (Delivery Note / Sales Invoice) happens in the ERP.

## Outbound events (webhooks)

Enable in *PharmacyOS Settings → Integration* (HTTPS endpoint + signing secret). Events are recorded in
the same transaction as the change (transactional outbox, DocType **PharmacyOS Sync Event**),
deduplicated while pending, and delivered every 5 minutes by the scheduler.

| Event | Trigger | `data` |
|---|---|---|
| `availability.changed` | stock ledger entry for a published item | `item_code, branch_code, qty, nearest_expiry` (computed at send time) |
| `order.status_changed` | PharmacyOS Sales Order submitted/updated/cancelled, or a Delivery Note / Sales Invoice against it | `sales_order, pharmacyos_order_id, status, docstatus, per_delivered, per_billed` |
| `catalog.changed` | published item saved | `item_code, published` |

Request: `POST <endpoint>` with JSON `{event, event_id, occurred_at, sent_at, data}` and headers
`X-PharmacyOS-Event`, `X-PharmacyOS-Event-Id`,
`X-PharmacyOS-Signature: sha256=<hex HMAC-SHA256 of the raw body with the shared secret>`.
Receivers must verify the signature, treat `event_id` as idempotency key and respond 2xx. Failures
are retried up to 5 times, then the event stays `Failed` (visible under System → Sync Events).

## Not yet implemented (next phase)

Order cancellation from the storefront, partial fulfilment notifications per line, rate limiting per
integration user, OpenAPI schema generation, and a sandbox receiver for end-to-end tests.


## PharmacyOS Cloud connector (website orders) — `integration/cloud.py`

The pharmacy server initiates every exchange, so no inbound port is opened.

* Events go out through the outbox to `{cloud}/integrations/erp/events`: `catalog.changed` (public
  fields only, with the ERP price), `availability.changed` (sellable quantity per branch, excluding
  expired batches and reservations) and `order.status_changed`. Sales Order submit and cancel also
  publish availability, because reservations change sellable stock.
* Every minute `pull_orders` reads `{cloud}/integrations/erp/orders/feed` and applies each order
  version idempotently:
  * an open stage creates the Sales Order once, which reserves stock;
  * `completed` creates a stock-updating, paid Sales Invoice with FEFO batches, once;
  * `cancelled` cancels the Sales Order, and is refused after delivery.
  * It then acknowledges the version. Business errors are acknowledged with `error`, and the Cloud
    moves the order to pharmacist review. Network errors are retried.
* Signing: `X-PharmacyOS-Timestamp` +
  `X-PharmacyOS-Signature: sha256=HMAC(secret, "ts\nMETHOD\npath?query\nsha256(body)")`. Events keep the
  body signature.
* Settings: PharmacyOS Settings → Integration (Cloud address, shared secret, branch for website
  orders, website payment method). Use *Website → Test Connection / Sync Catalog Now*. Status
  appears on System Status and the dashboard.
* Cloud side: `44mmd/PharmacyOS` → `docs/ERP_INTEGRATION.md`.
* Tests: `tests/test_cloud.py` (fake Cloud). A live run of both systems is described in
  `docs/DEPLOYMENT.md`.
