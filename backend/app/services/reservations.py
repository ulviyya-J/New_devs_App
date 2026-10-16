from datetime import datetime
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, List
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")

# Amounts are stored with three decimals, so a sum can carry a fraction of a
# cent. It is rounded once, at the end, on the exact Decimal total — never per
# row and never through a float.
CENTS = Decimal("0.01")


async def calculate_monthly_revenue(
    property_id: str,
    month: int,
    year: int,
    tenant_id: str,
    timezone: str = "UTC",
    db_session=None,
) -> Decimal:
    """
    Calculates revenue for a specific month, in the property's own timezone.

    A month belongs to the property, not to UTC. The boundaries are built in
    the property's local zone and only then converted to UTC for the query:
    a Paris stay checking in on 1 March at 00:30 local is 29 February 23:30
    UTC, and a naive UTC window drops it out of March entirely.
    """

    tz = ZoneInfo(timezone)

    start_local = datetime(year, month, 1, tzinfo=tz)
    if month < 12:
        end_local = datetime(year, month + 1, 1, tzinfo=tz)
    else:
        end_local = datetime(year + 1, 1, 1, tzinfo=tz)

    start_date = start_local.astimezone(UTC)
    end_date = end_local.astimezone(UTC)

    print(f"DEBUG: Querying revenue for {property_id} from {start_date} to {end_date} ({timezone})")

    # SQL Simulation (This would be executed against the actual DB)
    query = """
        SELECT SUM(total_amount) as total
        FROM reservations
        WHERE property_id = $1
        AND tenant_id = $2
        AND check_in_date >= $3
        AND check_in_date < $4
    """
    
    # In production this query executes against a database session.
    # result = await db.fetch_val(query, property_id, tenant_id, start_date, end_date)
    # return result or Decimal('0')
    
    return Decimal('0') # Placeholder for now until DB connection is finalized

async def calculate_total_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Aggregates revenue from database.
    """
    try:
        # Import database pool
        from app.core.database_pool import DatabasePool
        
        # Initialize pool if needed
        db_pool = DatabasePool()
        await db_pool.initialize()
        
        if db_pool.session_factory:
            async with db_pool.get_session() as session:
                # Use SQLAlchemy text for raw SQL
                from sqlalchemy import text
                
                query = text("""
                    SELECT 
                        property_id,
                        SUM(total_amount) as total_revenue,
                        COUNT(*) as reservation_count
                    FROM reservations 
                    WHERE property_id = :property_id AND tenant_id = :tenant_id
                    GROUP BY property_id
                """)
                
                result = await session.execute(query, {
                    "property_id": property_id, 
                    "tenant_id": tenant_id
                })
                row = result.fetchone()
                
                if row:
                    total_revenue = Decimal(str(row.total_revenue)).quantize(
                        CENTS, rounding=ROUND_HALF_UP
                    )
                    return {
                        "property_id": property_id,
                        "tenant_id": tenant_id,
                        "total": str(total_revenue),
                        "currency": "USD", 
                        "count": row.reservation_count
                    }
                else:
                    # No reservations found for this property
                    return {
                        "property_id": property_id,
                        "tenant_id": tenant_id,
                        "total": "0.00",
                        "currency": "USD",
                        "count": 0
                    }
        else:
            raise Exception("Database pool not available")
            
    except Exception as e:
        # This used to swallow the failure and answer with hardcoded figures
        # keyed only by property_id — the same invented total for every tenant
        # sharing an id, presented as real revenue. A client reading its board
        # numbers off a dashboard must never be shown a fabricated figure, and
        # it explains totals that do not match anyone's internal records.
        #
        # A revenue read that cannot reach the database is a failure, and the
        # caller is told so.
        logger.error(
            "Revenue query failed for property=%s tenant=%s: %s",
            property_id,
            tenant_id,
            e,
        )
        raise
