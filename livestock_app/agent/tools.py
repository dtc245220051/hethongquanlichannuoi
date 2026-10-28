from dataclasses import dataclass
from datetime import timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from livestock_app.models import (
    Alert,
    Animal,
    Barn,
    Farm,
    FeedInventoryBalance,
    FeedItem,
    Herd,
    HealthRecord,
    SyntheticDataset,
    VaccinationOutcome,
    VaccinationSchedule,
)
from livestock_app.services import (
    AuthorizationError,
    record_audit,
    require_farm_member,
    utc_now,
)

MAX_TOOL_ROWS = 10
MAX_HEALTH_TARGET_LENGTH = 100


class AgentToolError(Exception):
    """A safe, user-presentable tool invocation error."""


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., dict[str, Any]]


def _schema(
    properties: dict[str, Any], required: tuple[str, ...] = ()
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_LIMIT_PROPERTY = {"type": "integer", "minimum": 1, "maximum": MAX_TOOL_ROWS}


class ToolRegistry:
    def __init__(self, session: Session):
        self.session = session
        self._tools = {
            "get_farm_overview": ToolDefinition(
                name="get_farm_overview",
                description="Tổng số vật nuôi, đàn/lô và cá thể đang theo dõi.",
                input_schema=_schema({}),
                handler=self._farm_overview,
            ),
            "list_barns": ToolDefinition(
                name="list_barns",
                description="Danh sách chuồng và sức chứa hiện tại.",
                input_schema=_schema({"limit": _LIMIT_PROPERTY}),
                handler=self._list_barns,
            ),
            "get_feed_inventory": ToolDefinition(
                name="get_feed_inventory",
                description="Tồn kho thức ăn theo đơn vị kg.",
                input_schema=_schema({"limit": _LIMIT_PROPERTY}),
                handler=self._feed_inventory,
            ),
            "get_vaccination_schedule": ToolDefinition(
                name="get_vaccination_schedule",
                description="Lịch tiêm sắp đến hạn và quá hạn, tách khỏi kết quả thực tế.",
                input_schema=_schema({"limit": _LIMIT_PROPERTY}),
                handler=self._vaccination_schedule,
            ),
            "list_open_alerts": ToolDefinition(
                name="list_open_alerts",
                description="Cảnh báo chưa đóng trong trang trại.",
                input_schema=_schema({"limit": _LIMIT_PROPERTY}),
                handler=self._open_alerts,
            ),
            "get_health_history": ToolDefinition(
                name="get_health_history",
                description="Lịch sử quan sát sức khỏe theo mã cá thể hoặc tên đàn.",
                input_schema=_schema(
                    {
                        "target": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_HEALTH_TARGET_LENGTH,
                        },
                        "limit": _LIMIT_PROPERTY,
                    },
                    required=("target",),
                ),
                handler=self._health_history,
            ),
        }

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools.values())

    @property
    def allowed_names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def allowed_names_for(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
    ) -> frozenset[str]:
        try:
            member = require_farm_member(self.session, farm_id, actor_user_id)
        except AuthorizationError:
            raise AgentToolError("Không có quyền truy cập dữ liệu trang trại này.") from None
        if member.role == "veterinarian":
            return frozenset(
                {"get_farm_overview", "get_vaccination_schedule", "get_health_history"}
            )
        return self.allowed_names

    def definition(self, name: str) -> ToolDefinition:
        definition = self._tools.get(name)
        if definition is None:
            raise AgentToolError("Công cụ được yêu cầu không được hỗ trợ.")
        return definition

    def invoke(
        self,
        name: str,
        arguments: Any,
        *,
        farm_id: str,
        actor_user_id: str,
    ) -> dict[str, Any]:
        definition = self._tools.get(name)
        if definition is None:
            raise AgentToolError("Công cụ được yêu cầu không được hỗ trợ.")
        validated = self._validate_arguments(definition, arguments)
        if name not in self.allowed_names_for(
            farm_id=farm_id,
            actor_user_id=actor_user_id,
        ):
            raise AgentToolError("Vai trò Thú y không được truy cập công cụ này.")

        farm = self.session.scalar(
            select(Farm).where(Farm.id == farm_id, Farm.status == "active")
        )
        if farm is None:
            raise AgentToolError("Không có quyền truy cập dữ liệu trang trại này.")
        result = definition.handler(
            farm_id=farm_id,
            farm_timezone=farm.timezone,
            **validated,
        )
        record_audit(
            self.session,
            farm_id=farm_id,
            actor_user_id=actor_user_id,
            action=f"agent.read.{name}",
            entity_type="agent_tool",
            entity_id=None,
        )
        return result

    def _validate_arguments(
        self, definition: ToolDefinition, arguments: Any
    ) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise AgentToolError("Đối số công cụ không hợp lệ.")
        properties = definition.input_schema["properties"]
        if any(not isinstance(key, str) or key not in properties for key in arguments):
            raise AgentToolError("Đối số công cụ chứa trường không được hỗ trợ.")
        validated: dict[str, Any] = {}
        for key, value in arguments.items():
            spec = properties[key]
            if spec["type"] == "integer":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise AgentToolError("Giới hạn bản ghi phải là số nguyên.")
                if not spec["minimum"] <= value <= spec["maximum"]:
                    raise AgentToolError("Giới hạn bản ghi nằm ngoài phạm vi cho phép.")
                validated[key] = value
            elif spec["type"] == "string":
                if not isinstance(value, str):
                    raise AgentToolError("Đối tượng tra cứu không hợp lệ.")
                normalized = value.strip()
                if not spec["minLength"] <= len(normalized) <= spec["maxLength"]:
                    raise AgentToolError("Đối tượng tra cứu quá ngắn hoặc quá dài.")
                validated[key] = normalized
        for key in definition.input_schema["required"]:
            if key not in validated:
                raise AgentToolError("Thiếu đối số bắt buộc cho công cụ.")
        validated.setdefault("limit", MAX_TOOL_ROWS)
        return validated

    def _synthetic_filter(self, model):
        return (
            model.synthetic_dataset_id.is_not(None),
            select(SyntheticDataset.id).where(
                SyntheticDataset.farm_id == model.farm_id,
                SyntheticDataset.id == model.synthetic_dataset_id,
            ).exists(),
        )

    def _farm_overview(self, *, farm_id: str, farm_timezone: str, limit: int):
        del farm_timezone, limit
        herd_synthetic, herd_registry = self._synthetic_filter(Herd)
        animal_synthetic, animal_registry = self._synthetic_filter(Animal)
        active_herds = select(Herd.id).where(
            Herd.farm_id == farm_id,
            Herd.status == "active",
            herd_synthetic,
            herd_registry,
        )
        herd_count = self.session.scalar(
            select(func.count(Herd.id)).where(
                Herd.farm_id == farm_id,
                Herd.status == "active",
                herd_synthetic,
                herd_registry,
            )
        ) or 0
        unidentified_heads = self.session.scalar(
            select(func.coalesce(func.sum(Herd.unidentified_count), 0)).where(
                Herd.farm_id == farm_id,
                Herd.status == "active",
                herd_synthetic,
                herd_registry,
            )
        ) or 0
        identified_in_herds = self.session.scalar(
            select(func.count(Animal.id)).where(
                Animal.farm_id == farm_id,
                Animal.status == "active",
                Animal.herd_id.in_(active_herds),
                animal_synthetic,
                animal_registry,
            )
        ) or 0
        standalone_synthetic, standalone_registry = self._synthetic_filter(Animal)
        standalone_animals = self.session.scalar(
            select(func.count(Animal.id)).where(
                Animal.farm_id == farm_id,
                Animal.herd_id.is_(None),
                Animal.status == "active",
                standalone_synthetic,
                standalone_registry,
            )
        ) or 0
        return {
            "herd_count": int(herd_count),
            "identified_animal_count": int(identified_in_herds + standalone_animals),
            "total_heads": int(
                unidentified_heads + identified_in_herds + standalone_animals
            ),
            "tracking_rule": (
                "Mỗi cá thể thuộc đàn được tính trong head_count của đàn, "
                "không cộng lần hai."
            ),
        }

    def _list_barns(self, *, farm_id: str, farm_timezone: str, limit: int):
        del farm_timezone
        barn_synthetic, barn_registry = self._synthetic_filter(Barn)
        barns = self.session.scalars(
            select(Barn)
            .where(
                Barn.farm_id == farm_id,
                Barn.status == "active",
                barn_synthetic,
                barn_registry,
            )
            .order_by(Barn.name)
            .limit(limit)
        ).all()
        results = []
        for barn in barns:
            herd_synthetic, herd_registry = self._synthetic_filter(Herd)
            active_herds = select(Herd.id).where(
                Herd.farm_id == farm_id,
                Herd.barn_id == barn.id,
                Herd.status == "active",
                herd_synthetic,
                herd_registry,
            )
            unidentified_heads = self.session.scalar(
                select(func.coalesce(func.sum(Herd.unidentified_count), 0)).where(
                    Herd.farm_id == farm_id,
                    Herd.barn_id == barn.id,
                    Herd.status == "active",
                    herd_synthetic,
                    herd_registry,
                )
            ) or 0
            herd_animal_count = self.session.scalar(
                select(func.count(Animal.id)).where(
                    Animal.farm_id == farm_id,
                    Animal.status == "active",
                    Animal.herd_id.in_(active_herds),
                    *self._synthetic_filter(Animal),
                )
            ) or 0
            standalone_synthetic, standalone_registry = self._synthetic_filter(Animal)
            standalone_count = self.session.scalar(
                select(func.count(Animal.id)).where(
                    Animal.farm_id == farm_id,
                    Animal.barn_id == barn.id,
                    Animal.herd_id.is_(None),
                    Animal.status == "active",
                    standalone_synthetic,
                    standalone_registry,
                )
            ) or 0
            results.append(
                {
                    "name": barn.name,
                    "capacity_heads": barn.capacity_heads,
                    "current_heads": int(
                        unidentified_heads + herd_animal_count + standalone_count
                    ),
                    "status": barn.status,
                }
            )
        return {"barns": results}

    def _feed_inventory(self, *, farm_id: str, farm_timezone: str, limit: int):
        del farm_timezone
        item_synthetic, item_registry = self._synthetic_filter(FeedItem)
        balance_synthetic, balance_registry = self._synthetic_filter(
            FeedInventoryBalance
        )
        rows = self.session.execute(
            select(FeedItem.name, FeedInventoryBalance.quantity_kg)
            .join(
                FeedInventoryBalance,
                (FeedInventoryBalance.feed_item_id == FeedItem.id)
                & (FeedInventoryBalance.farm_id == FeedItem.farm_id),
            )
            .where(
                FeedItem.farm_id == farm_id,
                FeedItem.status == "active",
                item_synthetic,
                item_registry,
                FeedInventoryBalance.farm_id == farm_id,
                balance_synthetic,
                balance_registry,
            )
            .order_by(FeedItem.name)
            .limit(limit)
        ).all()
        return {
            "items": [
                {"name": name, "quantity_kg": format(Decimal(quantity), "f")}
                for name, quantity in rows
            ]
        }

    def _vaccination_schedule(self, *, farm_id: str, farm_timezone: str, limit: int):
        del farm_timezone
        schedule_synthetic, schedule_registry = self._synthetic_filter(
            VaccinationSchedule
        )
        now = utc_now()
        rows = self.session.scalars(
            select(VaccinationSchedule)
            .where(
                VaccinationSchedule.farm_id == farm_id,
                VaccinationSchedule.status == "scheduled",
                VaccinationSchedule.due_at <= now + timedelta(days=30),
                schedule_synthetic,
                schedule_registry,
            )
            .order_by(VaccinationSchedule.due_at)
            .limit(limit)
        ).all()
        schedule_ids = [row.id for row in rows]
        latest_outcome = aliased(VaccinationOutcome)
        superseding_outcome = aliased(VaccinationOutcome)
        outcome_synthetic, outcome_registry = self._synthetic_filter(latest_outcome)
        superseding_synthetic, superseding_registry = self._synthetic_filter(
            superseding_outcome
        )
        superseded = select(superseding_outcome.id).where(
            superseding_outcome.farm_id == latest_outcome.farm_id,
            superseding_outcome.supersedes_id == latest_outcome.id,
            superseding_synthetic,
            superseding_registry,
        ).exists()
        schedule_dataset_id = (
            select(VaccinationSchedule.synthetic_dataset_id)
            .where(
                VaccinationSchedule.farm_id == latest_outcome.farm_id,
                VaccinationSchedule.id == latest_outcome.schedule_id,
            )
            .scalar_subquery()
        )
        outcomes_by_schedule = (
            dict(
                self.session.execute(
                    select(latest_outcome.schedule_id, latest_outcome.outcome).where(
                        latest_outcome.farm_id == farm_id,
                        latest_outcome.schedule_id.in_(schedule_ids),
                        latest_outcome.synthetic_dataset_id == schedule_dataset_id,
                        outcome_synthetic,
                        outcome_registry,
                        ~superseded,
                    )
                ).all()
            )
            if schedule_ids
            else {}
        )
        now = utc_now()
        return {
            "schedules": [
                {
                    "vaccine_name": row.vaccine_name,
                    "due_at_utc": (
                        row.due_at.replace(tzinfo=timezone.utc)
                        if row.due_at.tzinfo is None
                        else row.due_at.astimezone(timezone.utc)
                    ).isoformat(),
                    "schedule_status": row.status,
                    "due_status": (
                        "overdue"
                        if (
                            row.due_at.replace(tzinfo=timezone.utc)
                            if row.due_at.tzinfo is None
                            else row.due_at.astimezone(timezone.utc)
                        )
                        < now
                        else "upcoming"
                    ),
                    "outcome": outcomes_by_schedule.get(row.id),
                    "result_recorded": row.id in outcomes_by_schedule,
                }
                for row in rows
            ]
        }

    def _open_alerts(self, *, farm_id: str, farm_timezone: str, limit: int):
        del farm_timezone
        alert_synthetic, alert_registry = self._synthetic_filter(Alert)
        rows = self.session.scalars(
            select(Alert)
            .where(
                Alert.farm_id == farm_id,
                Alert.status != "closed",
                alert_synthetic,
                alert_registry,
            )
            .order_by(Alert.created_at.desc())
            .limit(limit)
        ).all()
        return {
            "alerts": [
                {
                    "title": row.title[:200],
                    "severity": row.severity,
                    "status": row.status,
                    "created_at_utc": row.created_at.isoformat(),
                    "description": row.description[:500],
                }
                for row in rows
            ]
        }

    def _health_history(
        self, *, farm_id: str, farm_timezone: str, limit: int, target: str
    ):
        del farm_timezone
        health_synthetic, health_registry = self._synthetic_filter(HealthRecord)
        animal_synthetic, animal_registry = self._synthetic_filter(Animal)
        herd_synthetic, herd_registry = self._synthetic_filter(Herd)
        animal_match = self.session.execute(
            select(Animal.id, Animal.synthetic_dataset_id).where(
                Animal.farm_id == farm_id,
                Animal.identifier == target,
                animal_synthetic,
                animal_registry,
            )
        ).first()
        escaped_target = (
            target.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        herd_matches = self.session.execute(
            select(Herd.id, Herd.synthetic_dataset_id).where(
                Herd.farm_id == farm_id,
                Herd.name.ilike(f"%{escaped_target}%", escape="\\"),
                herd_synthetic,
                herd_registry,
            )
            .order_by(Herd.name)
            .limit(2)
        ).all()
        if animal_match is None and len(herd_matches) != 1:
            if len(herd_matches) > 1:
                return {
                    "records": [],
                    "target_found": False,
                    "target_ambiguous": True,
                }
            return {"records": [], "target_found": False}
        target_type, target_id, dataset_id = (
            ("animal", animal_match.id, animal_match.synthetic_dataset_id)
            if animal_match is not None
            else (
                "herd",
                herd_matches[0].id,
                herd_matches[0].synthetic_dataset_id,
            )
        )
        subject_filter = (
            HealthRecord.animal_id == target_id
            if target_type == "animal"
            else HealthRecord.herd_id == target_id
        )
        rows = self.session.scalars(
            select(HealthRecord)
            .where(
                HealthRecord.farm_id == farm_id,
                subject_filter,
                HealthRecord.synthetic_dataset_id == dataset_id,
                health_synthetic,
                health_registry,
            )
            .order_by(HealthRecord.recorded_at.desc())
            .limit(limit)
        ).all()
        return {
            "records": [
                {
                    "recorded_at_utc": row.recorded_at.isoformat(),
                    "observations": row.observations[:1000],
                    "follow_up_at_utc": (
                        row.follow_up_at.isoformat() if row.follow_up_at else None
                    ),
                }
                for row in rows
            ],
            "target_found": True,
            "target_type": target_type,
        }
