# Findings

I found four bugs. One for each thing the clients reported. Each one is a
separate commit.

A note on the environment: I did not have Docker on my machine, so I could not
run the stack. I worked from the code and the seed data. The seed data is built
to show exactly these cases, so I used it as the proof for each bug.

---

## 1. One client could see another client's revenue

**The report:** "Sometimes when we refresh, we see numbers that belong to
another company."

**File:** `backend/app/services/cache.py`

The cache key was only the property id:

```python
cache_key = f"revenue:{property_id}"
```

Property ids are only unique inside one tenant. The seed data shows it:

```sql
('prop-001', 'tenant-a', 'Beach House Alpha',  'Europe/Paris'),
('prop-001', 'tenant-b', 'Mountain Lodge Beta','America/New_York'),
```

Both companies have a `prop-001`. The first one to ask filled the cache. For the
next five minutes the other company read those numbers.

The word "sometimes" was the clue. A bug that only happens on some requests is
usually a cache, because only some requests come from it.

**The fix:**

```python
cache_key = f"revenue:{tenant_id}:{property_id}"
```

The function already had `tenant_id`. It just never reached the key.

---

## 2. March was counted in UTC, not in the property's own time

**The report:** "We're showing different totals for March."

**File:** `backend/app/services/reservations.py`

The month was built without a timezone:

```python
start_date = datetime(year, month, 1)
```

So every property was measured against a UTC month. But the properties are not
in UTC. Beach House Alpha is in Paris, and the seed has this booking:

```sql
('res-tz-1', 'prop-001', 'tenant-a', '2024-02-29 23:30:00+00', ..., 1250.000);
```

23:30 UTC on 29 February is 00:30 on 1 March in Paris. In local time it is a
March booking. The UTC window started later, so it was left out and 1250.00
disappeared from March.

**The fix:** build the month in the property's own timezone, then convert to UTC
for the query.

```python
tz = ZoneInfo(timezone)
start_local = datetime(year, month, 1, tzinfo=tz)
start_date  = start_local.astimezone(UTC)
```

I also added `tenant_id` as a real parameter. The SQL used `$2` for it, but the
function did not have that argument.

---

## 3. Money went through floating point

**The report:** "Totals are off by a few cents."

**Files:** `backend/app/api/v1/dashboard.py`,
`frontend/src/components/RevenueSummary.tsx`

The total was exact in the database and exact as a `Decimal`. Then it was
converted twice:

```python
total_revenue_float = float(revenue_data['total'])
```
```tsx
const displayTotal = Math.round(data.total_revenue * 100) / 100;
```

A computer stores floats in binary, and most decimal fractions do not fit. So
the value moves a little, and rounding afterwards rounds the wrong value:

```
"1.005"  ->  float  ->  1.0049999999999998
Math.round(1.0049999999999998 * 100) / 100  ->  1.00
correct answer                              ->  1.01
```

The amounts are stored with three decimals, so half a cent is in play. The seed
shows it:

```sql
333.333 + 333.333 + 333.334 = 1000.000
```

Exact as decimals. Not exact through a float.

The component even had a "Precision Mismatch Detected" badge that compared the
float with its own rounding. It was showing the bug, not stopping it.

**The fix:** round once, on the exact `Decimal`, with `ROUND_HALF_UP`. Send it
to the client as that exact string. The component formats the string and does no
maths on it. The badge has nothing left to find, so it is gone.

---

## 4. A failed query answered with invented numbers

**The report:** "The numbers don't match our internal records."

**File:** `backend/app/services/reservations.py`

Every error was caught and answered with a fixed table:

```python
mock_data = {'prop-001': {'total': '1000.00', 'count': 3}, ...}
```

It was keyed by property id only, so both tenants with the same id got the same
made-up number. And it looked exactly like real revenue. No error, no warning.
So any database problem quietly replaced a client's financial data.

**The fix:** log the error and raise it. A finance dashboard can show an error.
It cannot show a number nobody earned.

---

## One thing I did not change

`total_amount` is `NUMERIC(10, 3)`. Money has two decimals. A third one allows
half a cent, which no payment can really be, and it has to be rounded away
later anyway.

I left it alone because changing the column would rewrite stored amounts. That
is a migration, not a bug fix. I think it is worth raising separately.
