from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, scoped_session

from livestock_app.models import (
    FeedInventoryBalance,
    FeedItem,
    FeedTransaction,
    Herd,
)
from livestock_app.services import (
    InsufficientStockError,
    NotFoundError,
    ValidationError,
    atomic,
    record_audit,
    require_farm_manager,
    require_farm_operator,
    utc_now,
)

_MAX_QUANTITY_KG = Decimal("999999999.999")
_MILLI_KG = Decimal("0.001")


@dataclass(frozen=True)
class FeedTransactionResult:
    transaction_id: str
    balance_before_kg: Decimal
    balance_after_kg: Decimal


def _quantity(value: Decimal | int | str, *, allow_negative: bool) -> Decimal:
    if isinstance(value, (bool, float)):
        raise ValidationError("Feed quantity must be an exact decimal, not a float.")
    try:
        quantity = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError("Feed quantity is not a valid decimal.") from None
    if not quantity.is_finite() or quantity == 0:
        raise ValidationError("Feed quantity must be a finite nonzero decimal.")
    if not allow_negative and quantity < 0:
        raise ValidationError("Feed quantity must be positive for this transaction.")
    if abs(quantity) > _MAX_QUANTITY_KG or quantity.quantize(_MILLI_KG) != quantity:
        raise ValidationError("Feed quantity must fit DECIMAL(12,3) kilograms.")
    return quantity


class FeedInventoryService:
    def __init__(self, session: Session | scoped_session):
        self.session = session() if isinstance(session, scoped_session) else session

    def create_item(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        name: str,
        low_stock_threshold_kg: Decimal | int | str | None = None,
    ) -> str:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
            raise ValidationError("Feed name must contain 1 to 160 characters.")
        threshold = None
        if low_stock_threshold_kg is not None:
            if isinstance(low_stock_threshold_kg, (bool, float)):
                raise ValidationError("Threshold must be an exact decimal.")
            try:
                threshold = Decimal(str(low_stock_threshold_kg))
            except (InvalidOperation, ValueError):
                raise ValidationError("Threshold is not a valid decimal.") from None
            if (
                not threshold.is_finite()
                or threshold < 0
                or threshold > _MAX_QUANTITY_KG
                or threshold.quantize(_MILLI_KG) != threshold
            ):
                raise ValidationError("Threshold must fit DECIMAL(12,3) kilograms.")

        with atomic(self.session):
            require_farm_manager(self.session, farm_id, actor_user_id)
            item = FeedItem(
                farm_id=farm_id,
                name=name.strip(),
                base_unit_code="kg",
                low_stock_threshold_kg=threshold,
                status="active",
            )
            self.session.add(item)
            self.session.flush()
            self.session.add(
                FeedInventoryBalance(
                    farm_id=farm_id,
                    feed_item_id=item.id,
                    quantity_kg=Decimal("0.000"),
                    updated_at=utc_now(),
                )
            )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="feed.item.created",
                entity_type="feed_item",
                entity_id=item.id,
            )
            return item.id

    def record_transaction(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        feed_item_id: str,
        transaction_type: Literal["receipt", "consumption", "adjustment"],
        quantity_kg: Decimal | int | str,
        unit_code: str = "kg",
        reason: str | None = None,
        herd_id: str | None = None,
    ) -> FeedTransactionResult:
        if unit_code != "kg":
            raise ValidationError("Only kilograms (kg) are supported.")
        if transaction_type not in {"receipt", "consumption", "adjustment"}:
            raise ValidationError("Unsupported feed transaction type.")

        if transaction_type == "adjustment":
            quantity = _quantity(quantity_kg, allow_negative=True)
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                raise ValidationError("An adjustment reason of 1 to 500 characters is required.")
            permission_codes = ("feed_adjust",)
        else:
            quantity = _quantity(quantity_kg, allow_negative=False)
            if reason is not None and (
                not isinstance(reason, str) or len(reason) > 500
            ):
                raise ValidationError("Reason must be a string no longer than 500 characters.")
            permission_codes = (
                ("feed_stock_in",) if transaction_type == "receipt" else ()
            )
        delta = -quantity if transaction_type == "consumption" else quantity

        with atomic(self.session):
            require_farm_operator(
                self.session,
                farm_id,
                actor_user_id,
                permissions=permission_codes,
            )

            item = self.session.scalar(
                select(FeedItem)
                .where(
                    FeedItem.farm_id == farm_id,
                    FeedItem.id == feed_item_id,
                    FeedItem.status == "active",
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if item is None:
                raise NotFoundError("Active feed item was not found in this farm.")

            if herd_id is not None:
                herd = self.session.scalar(
                    select(Herd).where(
                        Herd.farm_id == farm_id,
                        Herd.id == herd_id,
                        Herd.status == "active",
                    )
                )
                if herd is None:
                    raise NotFoundError("Active herd was not found in this farm.")

            balance = self.session.scalar(
                select(FeedInventoryBalance)
                .where(
                    FeedInventoryBalance.farm_id == farm_id,
                    FeedInventoryBalance.feed_item_id == feed_item_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if balance is None:
                balance = FeedInventoryBalance(
                    farm_id=farm_id,
                    feed_item_id=feed_item_id,
                    quantity_kg=Decimal("0.000"),
                    updated_at=utc_now(),
                )
                self.session.add(balance)
                self.session.flush()

            before = Decimal(balance.quantity_kg)
            after = before + delta
            if after < 0:
                raise InsufficientStockError("Transaction would make feed stock negative.")

            now = utc_now()
            balance.quantity_kg = after
            balance.updated_at = now
            transaction = FeedTransaction(
                farm_id=farm_id,
                feed_item_id=feed_item_id,
                transaction_type=transaction_type,
                quantity_delta_kg=delta,
                unit_code="kg",
                balance_before_kg=before,
                balance_after_kg=after,
                herd_id=herd_id,
                reason=reason.strip() if reason and reason.strip() else None,
                occurred_at=now,
                recorded_by_user_id=actor_user_id,
            )
            self.session.add(transaction)
            self.session.flush()
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action=f"feed.{transaction_type}",
                entity_type="feed_transaction",
                entity_id=transaction.id,
                occurred_at=now,
            )
            result = FeedTransactionResult(
                transaction_id=transaction.id,
                balance_before_kg=before,
                balance_after_kg=after,
            )
        return result
