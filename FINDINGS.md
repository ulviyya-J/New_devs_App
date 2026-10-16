# Findings

Four bugs, one for each thing the clients reported. Each is a separate commit.

Note on environment: Docker was not available on my machine, so I could not
bring the stack up. I worked from the code and the seed data, which is built to
expose exactly these cases — the seed rows below are the proof for each bug.

---

## 1. One client could read another client's revenue

**Reported as:** *"Sometimes when we refresh, we see revenue numbers that look
like they belong to another company."*

**Where:** `backend/app/services/cache.py`

**Root cause.** The cache key was the property id alone:

```python
cache_key = f"revenue:{property_id}"
```

Property ids are only unique **inside** a tenant. The seed data makes this
explicit:

```sql
('prop-001', 'tenant-a', 'Beach House Alpha',  'Europe/Paris'),
('prop-001', 'tenant-b', 'Mountain Lodge Beta','America/New_York'),
```

Both companies own a `prop-001`. Whichever one asked first filled
`revenue:prop-001`, and for the next five minutes the other company read those
numbers back. The function already received `tenant_id` — it just never reached
the key.

The word *"sometimes"* in the report is the clue: a bug that only appears on
some requests is usually a cache, because only some requests are served from it.

**Fix.** The tenant is part of the key.

```python
cache_key = f"revenue:{tenant_id}:{property_id}"
```

---

## 2. March was counted in UTC, not in the property's timezone

**Reported as:** *"We're showing different totals for March."*

**Where:** `backend/app/services/reservations.py`

**Root cause.** The month window was built from naive datetimes:

```python
start_date = datetime(year, month, 1)
```

No timezone, so every property was measured against a UTC month. But the
properties are not in UTC:

```sql
('prop-001', 'tenant-a', 'Beach House Alpha', 'Europe/Paris'),
```

and the seed contains this reservation:

```sql
('res-tz-1', 'prop-001', 'tenant-a', '2024-02-29 23:30:00+00', ..., 1250.000);
```

23:30 UTC on 29 February is **00:30 on 1 March in Paris**. Locally it is a March
booking. The UTC window started at 1 March 00:00 UTC, so it fell outside and
1250.00 went missing from Sunset Properties' March.

**Fix.** The boundaries are built in the property's own zone and converted to
UTC for the query:

```python
tz = ZoneInfo(timezone)
start_local = datetime(year, month, 1, tzinfo=tz)
start_date  = start_local.astimezone(UTC)
```

`tenant_id` is now a real parameter as well — the SQL bound it as `$2` while the
function had no such argument.

---

## 3. Money travelled through floating point

**Reported as:** *"Revenue totals that seem slightly off by a few cents."*

**Where:** `backend/app/api/v1/dashboard.py`, `frontend/src/components/RevenueSummary.tsx`

**Root cause.** The total was exact in the database and exact as a `Decimal`,
and then converted twice:

```python
total_revenue_float = float(revenue_data['total'])     # backend
```
```tsx
const displayTotal = Math.round(data.total_revenue * 100) / 100;   // frontend
```

Binary floating point cannot hold most decimal fractions, so each conversion
can shift the value. Amounts are stored with three decimals, which puts a
fraction of a cent in play, and the seed shows it:

```sql
333.333 + 333.333 + 333.334 = 1000.000
```

Exact as decimals. Rounded per value, or passed through a float, it is not.

The component even carried a "Precision Mismatch Detected" badge that compared
the float against its own rounding — it was reporting the bug rather than
preventing it.

**Fix.** The sum is rounded **once**, on the exact `Decimal`, with
`ROUND_HALF_UP`, and leaves the API as that exact string. The component formats
the string and does no arithmetic on it. The mismatch badge has nothing left to
detect and is gone.

---

## 4. A failed query answered with invented revenue

**Reported as:** *"The revenue numbers don't match our internal records."*

**Where:** `backend/app/services/reservations.py`

**Root cause.** Every exception was caught and answered with a hardcoded table:

```python
mock_data = {'prop-001': {'total': '1000.00', 'count': 3}, ...}
mock_property_data = mock_data.get(property_id, ...)
```

Keyed by property id only, so both tenants sharing an id got the same invented
figure — and it was returned looking exactly like real revenue, with no flag and
no error. Any database hiccup silently replaced a client's financial data with a
number nobody had earned.

**Fix.** The failure is logged and raised. A finance dashboard may show an
error; it may not show a number somebody made up.

---

## Recommendation, not changed here

`total_amount` is `NUMERIC(10, 3)`. Money has two decimals; the third invites
sub-cent values that no payment can actually represent and that have to be
rounded away later. I did not change it, because altering the column would
rewrite stored amounts and that is a migration, not a bug fix. It is worth
raising separately.
