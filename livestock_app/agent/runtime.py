import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from livestock_app.agent.gemini import (
    MAX_GEMINI_REQUEST_SECONDS,
    MAX_GEMINI_TOOL_CALLS as MAX_TOOL_CALLS,
    GeminiService,
    GeminiServiceError,
)
from livestock_app.agent.tools import AgentToolError, ToolRegistry

MAX_QUESTION_LENGTH = 2000
MAX_CONVERSATION_MESSAGES = 8
MAX_CONVERSATION_MESSAGE_LENGTH = 4000
MAX_CONVERSATION_CHARACTERS = 12000
MAX_RESPONSE_CHARACTERS = 8000
MAX_REQUEST_SECONDS = 5.0
_TOOL_LIMIT = 10


class AgentError(Exception):
    """Expected, safe agent failure."""


@dataclass(frozen=True)
class AgentToolCall:
    name: str
    description: str
    row_count: int
    data: dict[str, Any]


@dataclass(frozen=True)
class AgentResult:
    mode: str
    mode_label: str
    answer: str
    tool_calls: tuple[AgentToolCall, ...]
    observed_at: str
    data_scope: str


def validate_conversation_history(
    history: Any,
) -> list[dict[str, str]]:
    if not isinstance(history, list):
        raise AgentError("Lịch sử hội thoại không hợp lệ.")
    if len(history) > MAX_CONVERSATION_MESSAGES:
        raise AgentError(
            f"Hội thoại chỉ hỗ trợ tối đa {MAX_CONVERSATION_MESSAGES // 2} lượt trước."
        )
    validated: list[dict[str, str]] = []
    total_characters = 0
    expected_role = "user"
    for message in history:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message.get("role") != expected_role
            or not isinstance(message.get("content"), str)
        ):
            raise AgentError("Lịch sử hội thoại không đúng định dạng.")
        content = message["content"]
        if (
            not content.strip()
            or len(content) > MAX_CONVERSATION_MESSAGE_LENGTH
        ):
            raise AgentError("Một tin nhắn trong lịch sử hội thoại không hợp lệ.")
        total_characters += len(content)
        if total_characters > MAX_CONVERSATION_CHARACTERS:
            raise AgentError("Lịch sử hội thoại vượt giới hạn dung lượng.")
        validated.append({"role": expected_role, "content": content})
        expected_role = "assistant" if expected_role == "user" else "user"
    if validated and validated[-1]["role"] != "assistant":
        raise AgentError("Lịch sử phải kết thúc bằng một câu trả lời hoàn chỉnh.")
    return validated


def _normalize(value: str) -> str:
    value = value.strip().lower().replace("đ", "d")
    return "".join(
        character
        for character in unicodedata.normalize("NFD", value)
        if unicodedata.category(character) != "Mn"
    )


def _medical_response(question: str) -> str | None:
    normalized = _normalize(question)
    urgent_terms = (
        "kho tho",
        "chay mau",
        "co giat",
        "bat tinh",
        "cap cuu",
        "chet hang loat",
        "chet",
        "kho dung",
        "ngung tho",
        "bo liet",
    )
    if any(term in normalized for term in urgent_terms):
        return (
            "Đây có thể là tình huống khẩn cấp. Tôi không thể chẩn đoán hoặc hướng dẫn "
            "điều trị; hãy liên hệ bác sĩ thú y/chuyên gia phù hợp ngay và thực hiện "
            "quy trình an toàn tại trang trại. Trợ lý không đánh giá lâm sàng."
        )
    medical_terms = (
        "chan doan",
        "thuoc",
        "lieu",
        "dieu tri",
        "phac do",
        "tri benh",
    )
    if any(term in normalized for term in medical_terms):
        return (
            "Tôi không thể chẩn đoán bệnh, kê thuốc, chỉ định liều hoặc phác đồ. "
            "Hãy tham khảo bác sĩ thú y. Tôi chỉ có thể tóm tắt các ghi nhận đã lưu "
            "mà không đưa ra kết luận y khoa."
        )
    return None


def _request_refusal(question: str) -> str | None:
    normalized = _normalize(question)
    if any(
        marker in normalized
        for marker in ("farm khac", "trang trai khac", "farm b", "farm 2")
    ):
        return (
            "Tôi không thể truy cập trang trại khác. Phạm vi dữ liệu chỉ do phiên "
            "đăng nhập và quyền backend hiện tại quyết định."
        )
    write_requests = (
        "hay nhap them",
        "nhap kho",
        "ghi nhan",
        "cap nhat",
        "sua ",
        "xoa ",
        "dong canh bao",
        "dieu chinh",
        "tao lich",
        "thay doi",
        "chuyen chuong",
        "tang dan",
        "giam dan",
        "danh dau da",
        "thuc hien tiem",
    )
    if any(marker in normalized for marker in write_requests):
        return (
            "Trợ lý chỉ đọc, không thể ghi hoặc thay đổi dữ liệu. Hãy dùng workflow "
            "nghiệp vụ phù hợp nếu bạn có quyền thực hiện."
        )
    return None


class FarmAgent:
    """Bounded farm-scoped tool agent with offline mock and Gemini modes."""

    def __init__(
        self,
        session: Session,
        *,
        app_env: str,
        mode: str,
        gemini_api_key: str = "",
        gemini_model: str = "",
    ):
        self.session = session
        self.app_env = app_env
        self.mode = mode
        self.gemini_api_key = gemini_api_key
        self.gemini_model = gemini_model
        self.registry = ToolRegistry(session)

    @staticmethod
    def status(
        *,
        app_env: str,
        mode: str,
        gemini_api_key: str = "",
        gemini_model: str = "",
    ) -> dict[str, str | bool]:
        if mode == "mock" and app_env in {"test", "demo"}:
            return {
                "available": True,
                "mode": "mock",
                "label": "MÔ PHỎNG",
                "message": (
                    "Điều phối quy tắc có giới hạn, không gọi LLM. Chỉ đọc dữ liệu "
                    "được đánh dấu tổng hợp trong test/demo."
                ),
            }
        if mode == "gemini":
            if not gemini_api_key.strip() or not gemini_model.strip():
                return {
                    "available": False,
                    "mode": "unavailable",
                    "label": "CHƯA CẤU HÌNH",
                    "message": (
                        "Cần cấu hình GEMINI_API_KEY và GEMINI_MODEL ở phía máy chủ. "
                        "Không có yêu cầu nào được gửi tới Gemini."
                    ),
                }
            return {
                "available": True,
                "mode": "gemini",
                "label": "GEMINI API",
                "message": (
                    "Khi bạn gửi câu hỏi, nội dung câu hỏi và kết quả tối thiểu từ "
                    "các công cụ chỉ đọc sẽ được gửi tới Google Gemini API; hiện "
                    "các công cụ chỉ truy vấn bản ghi tổng hợp có SyntheticDataset. "
                    "Lịch sử chat chỉ lưu trong sessionStorage của tab trình duyệt, "
                    "không ghi vào database; có thể xóa bằng nút Xóa hội thoại."
                ),
            }
        if mode == "provider":
            return {
                "available": False,
                "mode": "unavailable",
                "label": "CHƯA CẤU HÌNH",
                "message": (
                    "Chưa có nhà cung cấp LLM và phê duyệt xử lý dữ liệu. "
                    "Không gửi dữ liệu ra dịch vụ bên ngoài."
                ),
            }
        if mode == "mock":
            return {
                "available": False,
                "mode": "unavailable",
                "label": "CHƯA KHẢ DỤNG",
                "message": "Chế độ mô phỏng chỉ được bật trong môi trường test/demo.",
            }
        return {
            "available": False,
            "mode": "unavailable",
            "label": "ĐÃ TẮT",
            "message": "Trợ lý chưa được cấu hình; không có dữ liệu nào được gửi ra ngoài.",
        }

    def ask(
        self,
        question: str,
        *,
        farm_id: str,
        actor_user_id: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> AgentResult:
        if not isinstance(question, str) or not question.strip():
            raise AgentError("Vui lòng nhập câu hỏi.")
        if len(question) > MAX_QUESTION_LENGTH:
            raise AgentError(
                f"Câu hỏi vượt giới hạn {MAX_QUESTION_LENGTH} ký tự."
            )
        history = validate_conversation_history(
            [] if conversation_history is None else conversation_history
        )
        status = self.status(
            app_env=self.app_env,
            mode=self.mode,
            gemini_api_key=self.gemini_api_key,
            gemini_model=self.gemini_model,
        )
        if not status["available"]:
            raise AgentError(str(status["message"]))

        refused_request = _request_refusal(question)
        if refused_request is not None:
            return AgentResult(
                mode=self.mode,
                mode_label=self._local_response_label(),
                answer=refused_request,
                tool_calls=(),
                observed_at=datetime.now(timezone.utc).isoformat(),
                data_scope="Không truy vấn dữ liệu.",
            )

        urgent_or_medical = _medical_response(question)
        if urgent_or_medical is not None:
            return AgentResult(
                mode=self.mode,
                mode_label=self._local_response_label(),
                answer=urgent_or_medical,
                tool_calls=(),
                observed_at=datetime.now(timezone.utc).isoformat(),
                data_scope="Không truy vấn dữ liệu; hướng dẫn an toàn chung.",
            )

        if self.mode == "gemini":
            return self._ask_gemini(
                question,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                conversation_history=history,
            )

        planned = self._plan(question)
        if not planned:
            return AgentResult(
                mode="mock",
                mode_label="MÔ PHỎNG",
                answer=(
                    "Tôi chưa xác định được dữ liệu cần tra cứu. Bạn có thể hỏi về "
                    "tổng quan đàn, chuồng/sức chứa, tồn kho thức ăn, lịch tiêm, "
                    "cảnh báo hoặc lịch sử sức khỏe kèm mã cá thể/tên đàn."
                ),
                tool_calls=(),
                observed_at=datetime.now(timezone.utc).isoformat(),
                data_scope="Chưa truy vấn dữ liệu.",
            )

        allowed_tool_names = self.registry.allowed_names_for(
            farm_id=farm_id,
            actor_user_id=actor_user_id,
        )
        if any(name not in allowed_tool_names for name, _ in planned):
            raise AgentToolError("Vai trò Thú y không được truy cập công cụ này.")

        started = time.monotonic()
        calls: list[AgentToolCall] = []
        for tool_name, arguments in planned[:MAX_TOOL_CALLS]:
            if time.monotonic() - started > MAX_REQUEST_SECONDS:
                raise AgentError(
                    "Yêu cầu vượt thời gian xử lý cho phép; chưa hoàn tất truy vấn."
                )
            try:
                data = self.registry.invoke(
                    tool_name,
                    arguments,
                    farm_id=farm_id,
                    actor_user_id=actor_user_id,
                )
            except AgentToolError:
                raise
            calls.append(
                AgentToolCall(
                    name=tool_name,
                    description=self.registry.definition(tool_name).description,
                    row_count=self._row_count(tool_name, data),
                    data=data,
                )
            )
            if len(json.dumps(data, ensure_ascii=False)) > MAX_RESPONSE_CHARACTERS:
                raise AgentError("Kết quả tra cứu vượt giới hạn hiển thị an toàn.")
            if time.monotonic() - started > MAX_REQUEST_SECONDS:
                raise AgentError(
                    "Yêu cầu vượt thời gian xử lý cho phép; chưa hoàn tất truy vấn."
                )

        answer = self._compose(calls)
        if len(planned) > MAX_TOOL_CALLS:
            answer += (
                f"\nChỉ thực hiện tối đa {MAX_TOOL_CALLS} công cụ trong một yêu cầu; "
                "một số nội dung có thể chưa được tra cứu."
            )
        if len(answer) > MAX_RESPONSE_CHARACTERS:
            raise AgentError("Câu trả lời vượt giới hạn hiển thị an toàn.")
        return AgentResult(
            mode="mock",
            mode_label="MÔ PHỎNG",
            answer=answer,
            tool_calls=tuple(calls),
            observed_at=datetime.now(timezone.utc).isoformat(),
            data_scope="Chỉ dữ liệu có marker SyntheticDataset; phạm vi trang trại hiện tại.",
        )

    def _local_response_label(self) -> str:
        return "MÔ PHỎNG" if self.mode == "mock" else "KIỂM SOÁT AN TOÀN"

    def _ask_gemini(
        self,
        question: str,
        *,
        farm_id: str,
        actor_user_id: str,
        conversation_history: list[dict[str, str]],
    ) -> AgentResult:
        started = time.monotonic()
        try:
            answer, executions = GeminiService(
                api_key=self.gemini_api_key,
                model=self.gemini_model,
            ).answer(
                question,
                registry=self.registry,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                conversation_history=conversation_history,
                started_at=started,
                time_budget_seconds=MAX_GEMINI_REQUEST_SECONDS,
                allowed_tool_names=self.registry.allowed_names_for(
                    farm_id=farm_id,
                    actor_user_id=actor_user_id,
                ),
            )
        except GeminiServiceError as exc:
            raise AgentError(str(exc)) from None
        calls = tuple(
            AgentToolCall(
                name=execution.name,
                description=self.registry.definition(execution.name).description,
                row_count=self._row_count(execution.name, execution.data),
                data=execution.data,
            )
            for execution in executions
        )
        if len(answer) > MAX_RESPONSE_CHARACTERS:
            raise AgentError("Câu trả lời vượt giới hạn hiển thị an toàn.")
        return AgentResult(
            mode="gemini",
            mode_label="GEMINI API",
            answer=answer,
            tool_calls=calls,
            observed_at=datetime.now(timezone.utc).isoformat(),
            data_scope=(
                "Chỉ dữ liệu được các công cụ backend trả về trong phạm vi trang "
                "trại hiện hành; các công cụ hiện chỉ đọc dữ liệu có SyntheticDataset "
                "đã đăng ký. Không có dữ liệu ngoài kết quả công cụ được xác minh."
            ),
        )

    @staticmethod
    def _plan(question: str) -> list[tuple[str, dict[str, Any]]]:
        normalized = _normalize(question)
        planned: list[tuple[str, dict[str, Any]]] = []

        def add(name: str, arguments: dict[str, Any] | None = None) -> None:
            if not any(existing_name == name for existing_name, _ in planned):
                planned.append((name, arguments or {}))

        if any(
            term in normalized
            for term in (
                "tong quan",
                "so luong",
                "vat nuoi",
                "bao nhieu con",
                "quy mo dan",
                "tong dan",
            )
        ):
            add("get_farm_overview")
        if any(term in normalized for term in ("chuong", "suc chua")):
            add("list_barns", {"limit": _TOOL_LIMIT})
        if any(
            term in normalized
            for term in ("thuc an", "ton kho", "ton cam", "kho cam")
        ):
            add("get_feed_inventory", {"limit": _TOOL_LIMIT})
        if any(term in normalized for term in ("tiem", "vaccine", "vac xin", "qua han")):
            add("get_vaccination_schedule", {"limit": _TOOL_LIMIT})
        if any(term in normalized for term in ("canh bao", "canhbao")):
            add("list_open_alerts", {"limit": _TOOL_LIMIT})
        if any(term in normalized for term in ("suc khoe", "lich su")):
            target = FarmAgent._extract_health_target(question)
            if target:
                add(
                    "get_health_history",
                    {"target": target, "limit": _TOOL_LIMIT},
                )
        return planned

    @staticmethod
    def _extract_health_target(question: str) -> str | None:
        identifier = re.search(r"\b[A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b", question)
        if identifier:
            return identifier.group(0)
        quoted = re.search(r"[\"“](.{1,100}?)[\"”]", question)
        if quoted:
            return quoted.group(1).strip()
        herd_name = re.search(
            r"\bđàn\s+(.+?)(?:[?.!,;]|$)",
            question,
            flags=re.IGNORECASE,
        )
        return herd_name.group(1).strip() if herd_name else None

    @staticmethod
    def _row_count(tool_name: str, data: dict[str, Any]) -> int:
        if tool_name == "get_farm_overview":
            return 1 if data.get("herd_count") or data.get("total_heads") else 0
        collection_key = {
            "list_barns": "barns",
            "get_feed_inventory": "items",
            "get_vaccination_schedule": "schedules",
            "list_open_alerts": "alerts",
            "get_health_history": "records",
        }.get(tool_name)
        return len(data.get(collection_key, [])) if collection_key else 0

    @staticmethod
    def _compose(calls: list[AgentToolCall]) -> str:
        sections: list[str] = [
            "Kết quả mô phỏng được truy vấn qua công cụ nghiệp vụ chỉ đọc:"
        ]
        for call in calls:
            data = call.data
            if call.name == "get_farm_overview":
                if call.row_count:
                    sections.append(
                        f"- Tổng quan: {data['total_heads']} con; "
                        f"{data['herd_count']} đàn/lô; "
                        f"{data['identified_animal_count']} cá thể có bản ghi."
                    )
                else:
                    sections.append("- Tổng quan: chưa có dữ liệu tổng hợp.")
            elif call.name == "list_barns":
                barns = data["barns"]
                sections.append(
                    "- Chuồng: "
                    + (
                        "; ".join(
                            f"{row['name']} — {row['current_heads']}/{row['capacity_heads']} con"
                            for row in barns
                        )
                        if barns
                        else "không có dữ liệu chuồng tổng hợp."
                    )
                )
            elif call.name == "get_feed_inventory":
                items = data["items"]
                sections.append(
                    "- Tồn kho: "
                    + (
                        "; ".join(
                            f"{row['name']} — {row['quantity_kg']} kg"
                            for row in items
                        )
                        if items
                        else "không có dữ liệu tồn kho tổng hợp."
                    )
                )
            elif call.name == "get_vaccination_schedule":
                schedules = data["schedules"]
                sections.append(
                    "- Lịch tiêm (không tự xác nhận kết quả): "
                    + (
                        "; ".join(
                            f"{row['vaccine_name']} — hạn {row['due_status']}, "
                            f"kết quả thực tế: {row['outcome'] or 'chưa ghi nhận'} "
                            f"(hạn UTC {row['due_at_utc']})"
                            for row in schedules
                        )
                        if schedules
                        else "không có lịch tiêm tổng hợp trong 30 ngày tới/quá hạn."
                    )
                )
            elif call.name == "list_open_alerts":
                alerts = data["alerts"]
                sections.append(
                    "- Cảnh báo chưa đóng: "
                    + (
                        "; ".join(
                            f"{row['title']} ({row['severity']}, {row['status']})"
                            for row in alerts
                        )
                        if alerts
                        else "không có cảnh báo tổng hợp đang mở."
                    )
                )
            elif call.name == "get_health_history":
                records = data["records"]
                if not data["target_found"]:
                    if data.get("target_ambiguous"):
                        sections.append(
                            "- Tên đối tượng khớp nhiều bản ghi; hãy cung cấp mã cá thể "
                            "hoặc tên đàn đầy đủ hơn."
                        )
                    else:
                        sections.append(
                            "- Không tìm thấy đối tượng tổng hợp trong trang trại này."
                        )
                elif not records:
                    sections.append("- Đối tượng có trong dữ liệu tổng hợp nhưng chưa có ghi nhận sức khỏe.")
                else:
                    sections.append(
                        "- Ghi nhận sức khỏe (nguyên văn là dữ liệu do người dùng lưu, "
                        "không phải chẩn đoán): "
                        + "; ".join(
                            f"{row['recorded_at_utc']}: “{row['observations']}”"
                            for row in records
                        )
                    )
        sections.append(
            "Nguồn: "
            + ", ".join(call.name for call in calls)
            + ". Dữ liệu là tổng hợp/mô phỏng; không dùng để chẩn đoán hay kê đơn."
        )
        if not any(call.row_count for call in calls):
            sections.append(
                "Không đủ dữ liệu tổng hợp được đánh dấu để đưa ra số liệu; không "
                "có dữ liệu thật nào được thay bằng giá trị suy đoán."
            )
        return "\n".join(sections)
